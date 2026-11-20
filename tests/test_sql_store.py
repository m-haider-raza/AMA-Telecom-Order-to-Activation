"""SqlStore against an in-memory stand-in for the database: row locking, transactions, retries, the projection."""

import json
import threading
from decimal import Decimal

import pytest

from app import projection, sqlschema, sqlstore, sqlwrite
from app.domain import initial_state
from app.sqlstore import CommitUncertain, SqlStore
from app.store import Store, legacy_state, open_store


class Lost(Exception):
    """Stands in for the driver's connection errors."""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeDatabase:
    """One document row. An UPDLOCK read holds the row until the connection commits, rolls back or closes."""

    def __init__(self, state=None, projected=None):
        self.document = None if state is None else json.dumps(state)
        self.version = 1
        self.projected = projected
        self.row_lock = threading.Lock()
        self.statements = []
        self.connections = []
        self.failures = []
        self.counts = {}
        self.kpi = []

    def connect(self):
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def fail_next(self, text, error):
        self.failures.append((text, error))

    def fire(self, text):
        for index, (needle, error) in enumerate(self.failures):
            if needle in text:
                del self.failures[index]
                raise error

    def count(self, text):
        return sum(text in statement for statement in self.statements)


class FakeConnection:
    def __init__(self, database):
        self.database = database
        self.pending = {}
        self.savepoint = None
        self.holds_lock = False
        self.closed = False

    def cursor(self):
        if self.closed:
            raise Lost("the connection was closed")
        return FakeCursor(self)

    def value(self, name):
        return self.pending[name] if name in self.pending else getattr(self.database, name)

    def lock(self):
        if not self.holds_lock:
            if not self.database.row_lock.acquire(timeout=10):
                raise AssertionError("waited 10 seconds for the row lock")
            self.holds_lock = True

    def unlock(self):
        if self.holds_lock:
            self.holds_lock = False
            self.database.row_lock.release()

    def commit(self):
        self.database.fire("COMMIT")
        for name, value in self.pending.items():
            setattr(self.database, name, value)
        self.pending, self.savepoint = {}, None
        self.unlock()

    def rollback(self):
        self.pending, self.savepoint = {}, None
        self.unlock()

    def close(self):
        self.closed = True
        self.rollback()


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, parameters=()):
        connection = self.connection
        database = connection.database
        database.statements.append(sql)
        database.fire(sql)
        self.rows = []
        has_document = connection.value("document") is not None
        if "UPDLOCK" in sql:
            connection.lock()
        if sql.startswith("SET LOCK_TIMEOUT"):
            return
        if sql.startswith("SELECT COUNT(*) FROM ama.StateDocument"):
            self.rows = [(1 if has_document else 0,)]
        elif sql.startswith("INSERT INTO ama.StateDocument"):
            connection.pending.update(document=parameters[0], version=1, projected=None)
        elif sql == sqlstore.LOCKED_VERSION:
            self.rows = [(connection.value("version"), connection.value("projected"))] if has_document else []
        elif sql == "SELECT Version FROM ama.StateDocument WHERE Id = 1":
            self.rows = [(connection.value("version"),)] if has_document else []
        elif sql == sqlstore.DOCUMENT:
            self.rows = [(connection.value("version"), connection.value("document"))]
        elif sql.startswith("UPDATE ama.StateDocument SET Document"):
            connection.pending.update(document=parameters[0], version=parameters[1])
        elif sql.startswith("UPDATE ama.StateDocument SET ProjectedVersion"):
            connection.pending["projected"] = parameters[0]
        elif sql == "SAVE TRANSACTION proj":
            connection.savepoint = dict(connection.pending)
        elif sql == "ROLLBACK TRANSACTION proj":
            connection.pending = dict(connection.savepoint)
        elif sql.startswith("SELECT Version, ProjectedVersion, UpdatedAt"):
            self.rows = [(connection.value("version"), connection.value("projected"), "2026-01-01 00:00:00",
                          len(connection.value("document")))]
        elif sql.startswith("SELECT COUNT(*) FROM ama."):
            self.rows = [(database.counts.get(sql.rsplit(".", 1)[1], 0),)]
        elif "FROM ama.vw_KpiSummary" in sql:
            self.rows = list(database.kpi)
        else:
            raise AssertionError(f"the fake database does not know this statement: {sql}")

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


@pytest.fixture
def writes(monkeypatch):
    """Record what the projection would write instead of running it, so only the store's logic is under test."""
    calls = []
    monkeypatch.setattr(sqlwrite, "rebuild", lambda cursor, rows: calls.append(("rebuild", rows)))
    monkeypatch.setattr(sqlwrite, "write_plan", lambda cursor, plan: calls.append(("write_plan", plan)))
    return calls


@pytest.fixture
def schema(monkeypatch):
    class Schema:
        version = sqlschema.SCHEMA_VERSION
        created = 0

    def ensure(cursor):
        Schema.created += 1
        Schema.version = sqlschema.SCHEMA_VERSION
        return True

    monkeypatch.setattr(sqlschema, "current_version", lambda cursor: Schema.version)
    monkeypatch.setattr(sqlschema, "ensure_schema", ensure)
    return Schema


def make_store(database, **options):
    options.setdefault("retryable", (Lost,))
    return SqlStore(database.connect, **options)


def mark(state):
    return state.setdefault("marker", 1)


def test_transact_saves_the_changed_document_under_the_row_lock(writes, schema):
    database = FakeDatabase(initial_state())
    result = make_store(database).transact(lambda state: state.setdefault("marker", {"n": 1}))
    assert result == {"n": 1}
    assert json.loads(database.document)["marker"] == {"n": 1}
    assert (database.version, database.projected) == (2, 2)
    locked = database.statements.index(sqlstore.LOCKED_VERSION)
    saved = next(index for index, text in enumerate(database.statements)
                 if text.startswith("UPDATE ama.StateDocument SET Document"))
    assert locked < saved
    assert not database.row_lock.locked()


def test_an_unchanged_state_is_not_written_or_reprojected(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    make_store(database).transact(lambda state: len(state["orders"]))
    assert database.count("UPDATE ama.StateDocument") == 0
    assert writes == []
    assert database.version == 1


def test_an_error_in_the_function_undoes_everything_and_frees_the_row(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)

    def explode(state):
        state["marker"] = True
        raise ValueError("stop here")

    with pytest.raises(ValueError, match="stop here"):
        store.transact(explode)
    assert "marker" not in json.loads(database.document)
    assert not database.row_lock.locked()
    assert store.transact(mark) == 1


def test_a_missing_document_row_is_reported_and_the_row_is_freed(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)
    store.read()
    database.document = None
    with pytest.raises(RuntimeError, match="missing from ama.StateDocument"):
        store.transact(mark)
    assert not database.row_lock.locked()


def test_a_broken_connection_is_replaced_and_the_work_retried_once(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)
    store.read()
    database.fail_next(sqlstore.LOCKED_VERSION, Lost("connection reset"))
    assert store.transact(mark) == 1
    assert len(database.connections) == 2 and database.connections[0].closed
    assert json.loads(database.document)["marker"] == 1

    database.fail_next(sqlstore.LOCKED_VERSION, Lost("again"))
    database.fail_next(sqlstore.LOCKED_VERSION, Lost("and again"))
    with pytest.raises(Lost, match="and again"):
        store.transact(lambda state: state.setdefault("second", 1))
    assert "second" not in json.loads(database.document)


def test_a_failed_commit_is_reported_as_uncertain_and_never_repeated(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)
    store.read()
    database.fail_next("COMMIT", Lost("the link dropped"))
    with pytest.raises(CommitUncertain):
        store.transact(mark)
    assert database.count("UPDATE ama.StateDocument SET Document") == 1
    assert store.connection is None and database.connections[0].closed
    assert not database.row_lock.locked()


def test_a_failing_projection_keeps_the_document_and_waits_before_trying_again(monkeypatch, schema):
    clock, attempts = Clock(), []

    def broken(cursor, rows):
        attempts.append("failed")
        raise RuntimeError("table is locked by a report")

    monkeypatch.setattr(sqlwrite, "rebuild", broken)
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database, clock=clock, backoff=60.0)

    store.transact(mark)
    assert json.loads(database.document)["marker"] == 1 and database.version == 2
    assert database.projected == 1
    assert database.count("SAVE TRANSACTION proj") == 1 and database.count("ROLLBACK TRANSACTION proj") == 1
    assert store.projection_error.startswith("RuntimeError: table is locked")

    store.transact(lambda state: state.setdefault("second", 1))
    assert attempts == ["failed"] and database.version == 3

    clock.now += 61
    monkeypatch.setattr(sqlwrite, "rebuild", lambda cursor, rows: attempts.append("rebuilt"))
    store.transact(lambda state: state.setdefault("third", 1))
    assert attempts == ["failed", "rebuilt"]
    assert database.projected == database.version == 4 and store.projection_error is None


def test_the_first_projection_rebuilds_and_later_ones_write_only_the_difference(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)
    store.transact(mark)

    def use_a_unit(state):
        state["inventory"][0]["availableUnits"] -= 1

    store.transact(use_a_unit)
    assert [kind for kind, _ in writes] == ["rebuild", "write_plan"]
    plan = writes[1][1]
    assert len(plan["Inventory"]["delete"]) == 1 and len(plan["Inventory"]["insert"]) == 1
    assert not any(plan[name]["delete"] or plan[name]["insert"] for name in plan if name != "Inventory")


def test_two_connections_never_lose_an_update(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    stores, errors = [make_store(database), make_store(database)], []

    def work(store):
        try:
            for _ in range(8):
                store.transact(lambda state: state.__setitem__("counter", state.get("counter", 0) + 1))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=work, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert json.loads(database.document)["counter"] == 16 and database.version == 17


def test_an_empty_database_is_seeded_from_the_previous_store(writes, schema):
    previous = initial_state()
    previous["from-blob"] = True
    database = FakeDatabase(None)
    store = make_store(database, legacy=lambda: previous)
    assert store.read()["from-blob"] is True
    assert (database.version, database.projected) == (1, None)
    store.transact(lambda state: None)
    assert database.projected == 1 and [kind for kind, _ in writes] == ["rebuild"]


def test_a_brand_new_install_starts_from_the_demo_data(writes, schema):
    store = make_store(FakeDatabase(None))
    assert len(store.read()["orders"]) == len(initial_state()["orders"])


def test_the_schema_is_only_created_when_the_app_is_allowed_to(writes, schema):
    schema.version = 0
    database = FakeDatabase(initial_state(), projected=1)
    with pytest.raises(RuntimeError, match=r"python -m app\.sqlbootstrap"):
        make_store(database, auto_schema=False).read()
    assert database.connections[0].closed and schema.created == 0

    clock = Clock()
    store = make_store(database, auto_schema=True, clock=clock, max_age=10.0)
    store.read()
    assert schema.created == 1
    clock.now += 11
    store.read()
    assert schema.created == 1 and len(database.connections) == 3


def test_a_connection_older_than_its_token_allows_is_replaced(writes, schema):
    clock = Clock()
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database, clock=clock, max_age=100.0)
    store.read()
    store.read()
    assert len(database.connections) == 1
    clock.now += 101
    store.read()
    assert len(database.connections) == 2 and database.connections[0].closed


def test_inspect_compares_the_tables_with_what_was_projected(writes, schema):
    database = FakeDatabase(initial_state(), projected=1)
    store = make_store(database)
    store.transact(mark)
    database.counts = {name: len(rows) for name, rows in projection.project(store.read()).items()}
    database.kpi = [("benchmark", 100, 100, 100, Decimal("30.5"), Decimal("5.0")), ("live", 4, 4, 0, None, None)]

    info = store.inspect()
    assert info["inSync"] and info["version"] == 2 and info["projectedVersion"] == 2
    assert [table["name"] for table in info["tables"]] == list(projection.TABLES)
    assert all(table["match"] is True for table in info["tables"])
    assert info["views"]["kpiSummary"] == [
        {"scope": "benchmark", "orders": 100, "decided": 100, "completed": 100, "avgElapsedSeconds": 30.5,
         "falloutPercent": 5.0},
        {"scope": "live", "orders": 4, "decided": 4, "completed": 0, "avgElapsedSeconds": None,
         "falloutPercent": None}]

    database.counts["Orders"] += 1
    assert next(table for table in store.inspect()["tables"] if table["name"] == "Orders")["match"] is False
    cold = make_store(database).inspect()
    assert all(table["match"] is None for table in cold["tables"])


def test_open_store_needs_both_sql_settings_or_neither(monkeypatch, tmp_path):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("AMA_SQL_DATABASE", raising=False)
    monkeypatch.setenv("AMA_SQL_SERVER", "example.database.windows.net")
    with pytest.raises(RuntimeError, match="both"):
        open_store()
    monkeypatch.delenv("AMA_SQL_SERVER")
    store = open_store()
    try:
        assert isinstance(store, Store)
    finally:
        store.close()


def test_the_legacy_state_is_whatever_the_snapshot_store_holds(monkeypatch, tmp_path):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "state.json"))
    assert len(legacy_state()["orders"]) == len(initial_state()["orders"])
    store = Store()
    store.transact(lambda state: state.__setitem__("kept", True))
    store.close()
    assert legacy_state()["kept"] is True
