"""Prepare the Azure SQL database once, from inside the virtual network, as its Microsoft Entra administrator.

    python -m app.sqlbootstrap                 # create the schema and the app's database user, then self-test
    python -m app.sqlbootstrap --skip-selftest

Environment: AMA_SQL_SERVER, AMA_SQL_DATABASE, AMA_SQL_ADMIN_TOKEN (an access token for https://database.windows.net/
that belongs to the administrator), AZURE_CLIENT_ID (the app's managed identity) and, optionally, AMA_SQL_USER
(default ama30-identity).

The app's identity is given SELECT, INSERT, UPDATE and DELETE on the ama schema and nothing else. The self-test then
signs in as that identity and exercises the real store against this database, including a 100-order benchmark run. It
writes only while the database is still empty and removes everything it wrote, so the app's first start can import
the previous store. Exit status 0 means every check passed.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from types import SimpleNamespace

from app import analytics, benchmark, projection, sqlschema, sqlwrite
from app.sqlstore import SqlStore, sql_connector

USER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FALLOUT = ("needs-people", "failed")


def say(status, name, detail=""):
    print(f"{status:<5} {name}" + (f" - {detail}" if detail else ""), flush=True)


class AdminToken:
    """Presents a token acquired elsewhere (for example by az account get-access-token) as a driver credential."""

    def __init__(self, token):
        self.token = token

    def get_token(self, *scopes, **options):
        return SimpleNamespace(token=self.token, expires_on=int(time.time()) + 300)


def admin_connection(server, database, token):
    import mssql_python

    mssql_python.pooling(enabled=False)
    connection = mssql_python.connect(f"Server={server};Database={database};Encrypt=yes;",
                                      token_provider=AdminToken(token), timeout=30)
    connection.timeout = 120
    return connection


def create_user(connection, user, client_id):
    cursor = connection.cursor()
    cursor.execute("SELECT type_desc FROM sys.database_principals WHERE name = ?", (user,))
    existing = cursor.fetchone()
    if existing:
        return f"already exists ({existing[0]})"
    try:
        cursor.execute(f"CREATE USER [{user}] FROM EXTERNAL PROVIDER")
        connection.commit()
        return "created from the directory"
    except Exception as error:
        connection.rollback()
        if not client_id:
            raise
        say("note", "directory lookup", f"{type(error).__name__}; creating the user from its client id instead")
    sid = "0x" + uuid.UUID(client_id).bytes_le.hex().upper()
    cursor.execute(f"CREATE USER [{user}] WITH SID = {sid}, TYPE = E")
    connection.commit()
    return "created from its client id"


def prepare_database(connection, user, client_id):
    """As the administrator: create or upgrade ama.*, then give the app's contained user its data permissions."""
    cursor = connection.cursor()
    if sqlschema.ensure_schema(cursor):
        connection.commit()
        say("PASS", "schema", f"created or upgraded to version {sqlschema.SCHEMA_VERSION}")
    else:
        say("PASS", "schema", f"already at version {sqlschema.SCHEMA_VERSION}")
    say("PASS", "database user", f"{user} {create_user(connection, user, client_id)}")
    cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::ama TO [{user}]")
    connection.commit()
    say("PASS", "permissions", "SELECT, INSERT, UPDATE, DELETE on schema ama")


def advance_run(state):
    benchmark.advance(state)
    return state["benchmark"]["run"]


def expected_kpi(rows, scope):
    """What ama.vw_KpiSummary should report for a scope, worked out from the projected rows."""
    names = projection.TABLES["Orders"].names
    column = {name: position for position, name in enumerate(names)}
    orders = [row for row in rows["Orders"].values() if row[column["Scope"]] == scope]
    decided = [row for row in orders if row[column["Outcome"]] != "in-flight"]
    completed = [row for row in orders if row[column["State"]] == "completed"]
    elapsed = [row[column["ElapsedSeconds"]] for row in completed if row[column["ElapsedSeconds"]] is not None]
    fallout = [row for row in orders if row[column["Outcome"]] in FALLOUT]
    return {"orders": len(orders), "decided": len(decided), "completed": len(completed),
            "avgElapsedSeconds": sum(elapsed) / len(elapsed) if elapsed else None,
            "falloutPercent": 100 * len(fallout) / len(decided) if decided else None}


class Selftest:
    def __init__(self, connect, retryable, user, server, database):
        self.connect, self.retryable, self.user = connect, retryable, user
        self.server, self.database = server, database
        self.run_id = uuid.uuid4().hex[:10]
        self.failures = []
        self.store = self.new_store()
        self.state = None

    def new_store(self):
        return SqlStore(self.connect, retryable=self.retryable, auto_schema=False, server=self.server,
                        database=self.database)

    def step(self, name, action):
        started = time.perf_counter()
        try:
            detail = action()
        except Exception as error:
            self.failures.append(name)
            say("FAIL", name, f"{type(error).__name__}: {str(error)[:400]}")
            for line in traceback.format_exc().splitlines()[-12:]:
                print("        " + line, flush=True)
            return False
        say("PASS", name, f"{detail or 'ok'} ({time.perf_counter() - started:.1f}s)")
        return True

    def query(self, sql, parameters=()):
        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, parameters)
            rows = [tuple(row) for row in cursor.fetchall()]
            connection.commit()
            return rows
        finally:
            connection.close()

    def sign_in(self):
        for attempt in range(4):
            try:
                (who, database), = self.query("SELECT USER_NAME(), DB_NAME()")
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(5)
        if who != self.user:
            raise AssertionError(f"signed in as {who}, expected {self.user}")
        return f"signed in to {database} as {who}"

    def database_is_empty(self):
        return self.query("SELECT COUNT(*) FROM ama.StateDocument")[0][0] == 0

    def seed(self):
        self.store.transact(lambda state: state.__setitem__("selftest-run", self.run_id))
        document = self.query("SELECT Version, DATALENGTH(Document) FROM ama.StateDocument WHERE Id = 1")[0]
        return f"state document seeded, version {document[0]}, {document[1] // 1024} KB"

    def two_writers(self):
        errors, stores = [], [self.new_store(), self.new_store()]

        def work(store):
            try:
                for _ in range(8):
                    store.transact(lambda state: state.__setitem__("selftest-counter",
                                                                   state.get("selftest-counter", 0) + 1))
            except Exception as error:
                errors.append(error)
            finally:
                store.close()

        threads = [threading.Thread(target=work, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
        count = self.store.read()["selftest-counter"]
        if count != 16:
            raise AssertionError(f"16 increments from two connections ended at {count}")
        return "16 increments from two connections, none lost"

    def timed(self, timings, fn):
        started = time.perf_counter()
        result = self.store.transact(fn)
        timings.append(time.perf_counter() - started)
        return result

    def benchmark_run(self):
        timings = []
        run = self.timed(timings, lambda state: benchmark.start(state, 100, human_delay=0))
        for _ in range(40):
            if run["status"] != "running":
                break
            run = self.timed(timings, advance_run)
        if run["status"] != "complete":
            raise AssertionError(f"the benchmark run ended as {run['status']}: {run['error']}")
        size = self.query("SELECT DATALENGTH(Document) FROM ama.StateDocument WHERE Id = 1")[0][0]
        return (f"100 orders in {len(timings)} transactions, slowest {max(timings):.1f}s, "
                f"average {sum(timings) / len(timings):.1f}s, document {size // 1024} KB")

    def tables_match(self):
        self.state = self.store.read()
        expected = projection.project(self.state)
        detail = []
        for name, table in projection.TABLES.items():
            found = {tuple(row) for row in self.query(f"SELECT {', '.join(table.key)} FROM ama.{name}")}
            if found != set(expected[name]):
                raise AssertionError(f"ama.{name} holds {len(found)} rows, the document projects "
                                     f"{len(expected[name])}")
            detail.append(f"{name} {len(found)}")
        return ", ".join(detail)

    def views_match(self):
        rows = projection.project(self.state)
        report = analytics.compute(self.state, "benchmark")
        found = {row[0]: row for row in self.query(
            "SELECT Scope, Orders, Decided, Completed, AvgElapsedSeconds, FalloutPercent FROM ama.vw_KpiSummary")}
        for scope in sorted(found):
            want, got = expected_kpi(rows, scope), found[scope]
            same = (got[1:4] == (want["orders"], want["decided"], want["completed"])
                    and abs((got[4] or 0) - (want["avgElapsedSeconds"] or 0)) < 0.01
                    and abs(float(got[5] or 0) - (want["falloutPercent"] or 0)) <= 0.051)
            if not same:
                raise AssertionError(f"ama.vw_KpiSummary says {tuple(got)} for {scope}, expected {want}")
        want = expected_kpi(rows, "benchmark")
        if report["fallout"]["decided"] != want["decided"] or report["cycle"]["count"] != want["completed"]:
            raise AssertionError("the analytics report and the SQL view count different orders")
        cycle = self.query("SELECT COUNT(*) FROM ama.vw_OrderCycle WHERE Scope = N'benchmark'")[0][0]
        agents = self.query("SELECT COUNT(*) FROM ama.vw_AgentPerformance WHERE Scope = N'benchmark'")[0][0]
        if cycle != want["completed"] or agents < 1:
            raise AssertionError(f"views returned {cycle} completed orders and {agents} agents")
        return (f"{want['completed']} completed orders, fallout {want['falloutPercent']:.1f}%, "
                f"{agents} agents in the views")

    def projection_failure(self):
        """A failing projection must be undone to its savepoint and must not stop the document being saved."""
        before = self.query("SELECT COUNT(*) FROM ama.Inventory")[0][0]
        original = sqlwrite.rebuild, sqlwrite.write_plan

        def broken(cursor, rows):
            cursor.execute("DELETE FROM ama.Inventory")
            cursor.execute("SELECT 1 / 0")

        sqlwrite.rebuild = sqlwrite.write_plan = broken
        try:
            self.store.transact(lambda state: state.__setitem__("selftest-failure", True))
        finally:
            sqlwrite.rebuild, sqlwrite.write_plan = original
        info = self.store.inspect()
        after = self.query("SELECT COUNT(*) FROM ama.Inventory")[0][0]
        if not self.store.read().get("selftest-failure"):
            raise AssertionError("the document was not saved after a projection failure")
        if after != before or not self.store.projection_error or info["inSync"]:
            raise AssertionError(f"projection failure was not contained (inventory {before} -> {after})")
        self.store.retry_projection_at = 0.0
        self.store.transact(lambda state: state.pop("selftest-failure", None))
        info = self.store.inspect()
        if not info["inSync"] or info["projectionError"]:
            raise AssertionError("the tables did not catch up on the next change")
        return "rolled back to the savepoint, document kept, tables caught up on the next change"

    def reconnect(self):
        if self.store.connection is not None:
            self.store.connection.close()
        state = self.store.read()
        if state.get("selftest-run") != self.run_id:
            raise AssertionError("the document read after reconnecting is not the one written")
        return "a closed connection was replaced and the read succeeded"

    def wipe(self):
        """Remove everything the self-test wrote, but only while the document is still the self-test's own."""
        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT Document FROM ama.StateDocument WITH (UPDLOCK, HOLDLOCK) WHERE Id = 1")
            row = cursor.fetchone()
            if row is not None:
                if json.loads(row[0]).get("selftest-run") != self.run_id:
                    raise AssertionError("the state document is no longer the self-test's; left untouched")
                for name in projection.TABLES:
                    cursor.execute(f"DELETE FROM ama.{name}")
                cursor.execute("DELETE FROM ama.StateDocument WHERE Id = 1")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return "database left empty for the app's first start"

    def inspect_existing(self):
        info = self.store.inspect()
        if info["projectionError"] or not info["inSync"]:
            raise AssertionError(f"tables are behind the document: {info['projectionError'] or info['version']}")
        return f"version {info['version']}, {info['documentBytes'] // 1024} KB, tables in sync"

    def run(self):
        if not self.step("sign in as the app identity", self.sign_in):
            return self.failures
        if not self.database_is_empty():
            say("note", "existing data", "the database already holds data, so only read-only checks run")
            self.step("document and tables", self.inspect_existing)
            self.step("tables match the document", self.tables_match)
            return self.failures
        try:
            if self.step("seed the state document", self.seed):
                self.step("two writers, no lost update", self.two_writers)
                if self.step("100-order benchmark run", self.benchmark_run):
                    self.step("tables match the document", self.tables_match)
                    self.step("SQL views agree with the analytics", self.views_match)
                self.step("projection failure is contained", self.projection_failure)
                self.step("reconnect after a lost connection", self.reconnect)
        finally:
            self.store.close()
            self.step("remove the self-test data", self.wipe)
        return self.failures


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.sqlbootstrap", description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-selftest", action="store_true", help="only prepare the database")
    args = parser.parse_args(argv)
    try:
        server, database = os.environ["AMA_SQL_SERVER"], os.environ["AMA_SQL_DATABASE"]
        token = os.environ["AMA_SQL_ADMIN_TOKEN"]
    except KeyError as missing:
        print(f"Set {missing.args[0]} before running the bootstrap", file=sys.stderr)
        return 2
    user = os.getenv("AMA_SQL_USER", "ama30-identity")
    if not USER_NAME.match(user):
        print("AMA_SQL_USER must be letters, digits, dot, dash or underscore", file=sys.stderr)
        return 2
    say("info", "target", f"{server} / {database}, app identity {user}")
    try:
        connection = admin_connection(server, database, token)
        try:
            prepare_database(connection, user, os.getenv("AZURE_CLIENT_ID", ""))
        finally:
            connection.close()
    except Exception as error:
        say("FAIL", "prepare the database", f"{type(error).__name__}: {str(error)[:600]}")
        traceback.print_exc()
        print("RESULT: FAIL", flush=True)
        return 1
    failures = []
    if not args.skip_selftest:
        import mssql_python

        retryable = (mssql_python.OperationalError, mssql_python.InterfaceError)
        failures = Selftest(sql_connector(server, database), retryable, user, server, database).run()
    print("RESULT: PASS" if not failures else f"RESULT: FAIL ({', '.join(failures)})", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
