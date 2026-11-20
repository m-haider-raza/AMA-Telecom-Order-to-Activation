"""Azure SQL store: the whole order state as one JSON document, plus relational tables derived from it.

The document has the same shape the Blob store keeps. It is read and written under an UPDLOCK so two replicas can
never overwrite each other. In the same transaction the tables in ama.* are refreshed from it, so the data can be
reported on with plain SQL. A projection failure is rolled back to a savepoint and never blocks the document write.
"""

import copy
import json
import logging
import os
import threading
import time

from app import projection, sqlschema, sqlwrite
from app.domain import initial_state

log = logging.getLogger(__name__)

LOCKED_VERSION = ("SELECT Version, ProjectedVersion FROM ama.StateDocument WITH (UPDLOCK, HOLDLOCK) WHERE Id = 1")
DOCUMENT = "SELECT Version, Document FROM ama.StateDocument WHERE Id = 1"
LOCK_TIMEOUT_MS = 20000
QUERY_TIMEOUT_SECONDS = 90


class CommitUncertain(RuntimeError):
    """The commit failed after it was sent, so it is unknown whether the change was saved. Never retried."""


class TransactionLost(RuntimeError):
    """The server ended the transaction (for example a deadlock victim); the whole transaction can be retried."""


def dumps(state):
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def sql_connector(server, database):
    """Connect with the app's managed identity: a fresh Microsoft Entra token per connection, no passwords."""
    import mssql_python
    from azure.identity import DefaultAzureCredential

    mssql_python.pooling(enabled=False)
    credential = DefaultAzureCredential()

    def connect():
        connection = mssql_python.connect(f"Server={server};Database={database};Encrypt=yes;",
                                          token_provider=credential, timeout=30)
        connection.timeout = QUERY_TIMEOUT_SECONDS
        return connection

    return connect


class SqlStore:
    def __init__(self, connect, *, retryable=(), legacy=None, auto_schema=True, clock=time.monotonic,
                 max_age=2700.0, backoff=60.0, server="", database=""):
        self.lock = threading.RLock()
        self.connect = connect
        self.retryable = tuple(retryable)
        self.legacy = legacy
        self.auto_schema = auto_schema
        self.clock = clock
        self.max_age = max_age
        self.backoff = backoff
        self.server, self.database = server, database
        self.connection = None
        self.connected_at = 0.0
        self.ready = False
        self.version = None
        self.text = None
        self.rows = None
        self.rows_version = None
        self.projection_error = None
        self.retry_projection_at = 0.0

    @classmethod
    def from_env(cls, legacy=None):
        server, database = os.environ["AMA_SQL_SERVER"], os.environ["AMA_SQL_DATABASE"]
        import mssql_python

        return cls(sql_connector(server, database), retryable=(mssql_python.OperationalError,
                                                               mssql_python.InterfaceError),
                   legacy=legacy, auto_schema=os.getenv("AMA_SQL_AUTO_SCHEMA", "true").lower() != "false",
                   server=server, database=database)

    def _reset(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                log.debug("Ignoring an error while closing the SQL connection", exc_info=True)

    def _rollback(self):
        try:
            self.connection.rollback()
        except Exception:
            self._reset()

    def _open(self):
        if self.connection is not None and self.clock() - self.connected_at > self.max_age:
            self._reset()
        if self.connection is None:
            connection = self.connect()
            try:
                cursor = connection.cursor()
                cursor.execute(f"SET LOCK_TIMEOUT {LOCK_TIMEOUT_MS}")
                if not self.ready:
                    self._prepare(connection, cursor)
            except BaseException:
                try:
                    connection.close()
                except Exception:
                    log.debug("Ignoring an error while closing the SQL connection", exc_info=True)
                raise
            self.connection, self.connected_at = connection, self.clock()
        return self.connection

    def _prepare(self, connection, cursor):
        """First connection only: check or create the schema, then seed the document from the previous store."""
        if sqlschema.current_version(cursor) < sqlschema.SCHEMA_VERSION:
            if not self.auto_schema:
                raise RuntimeError("The database schema is missing or out of date; run python -m app.sqlbootstrap "
                                   "as the database administrator")
            sqlschema.ensure_schema(cursor)
            connection.commit()
            log.info("SQL schema ready (version %s)", sqlschema.SCHEMA_VERSION)
        cursor.execute("SELECT COUNT(*) FROM ama.StateDocument WITH (UPDLOCK, HOLDLOCK)")
        if cursor.fetchone()[0] == 0:
            state = self.legacy() if self.legacy else initial_state()
            cursor.execute("INSERT INTO ama.StateDocument (Id, Version, ProjectedVersion, Document) "
                           "VALUES (1, 1, NULL, ?)", (dumps(state),))
            log.info("Seeded the SQL state document with %s existing orders", len(state.get("orders", {})))
        connection.commit()
        self.ready = True

    def _document(self, cursor, version):
        if self.text is not None and self.version == version:
            return self.text
        cursor.execute(DOCUMENT)
        stored_version, stored = cursor.fetchone()
        self.text = dumps(json.loads(stored))
        self.version = stored_version
        return self.text

    def _call(self, operation):
        """Run operation(); on a connection-level failure reconnect and run it once more."""
        with self.lock:
            try:
                return operation()
            except (*self.retryable, TransactionLost) as error:
                log.warning("SQL connection problem (%s); reconnecting and retrying once", type(error).__name__)
                self._reset()
            return operation()

    def read(self):
        return self._call(self._read_once)

    def _read_once(self):
        connection = self._open()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT Version FROM ama.StateDocument WHERE Id = 1")
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("The state document is missing from ama.StateDocument")
            if self.text is None or self.version != row[0]:
                self._document(cursor, row[0])
            connection.commit()
        except BaseException:
            self._rollback()
            raise
        return json.loads(self.text)

    def transact(self, fn):
        return self._call(lambda: self._transact_once(fn))

    def _transact_once(self, fn):
        connection = self._open()
        cursor = connection.cursor()
        try:
            cursor.execute(LOCKED_VERSION)
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("The state document is missing from ama.StateDocument")
            version, projected_version = row
            before = self._document(cursor, version)
            state = json.loads(before)
            result = fn(state)
            after = dumps(state)
            changed = after != before
            new_version = version + 1 if changed else version
            if changed:
                cursor.execute("UPDATE ama.StateDocument SET Document = ?, Version = ?, UpdatedAt = SYSUTCDATETIME() "
                               "WHERE Id = 1", (after, new_version))
            rows = None
            if changed or projected_version != version:
                rows = self._project(cursor, state, new_version, version, projected_version)
        except BaseException:
            self.text = self.version = None
            self._rollback()
            raise
        try:
            connection.commit()
        except BaseException as error:
            self.text = self.version = self.rows = self.rows_version = None
            self._reset()
            raise CommitUncertain("The database did not confirm the save; check the order before repeating") from error
        self.text, self.version = after, new_version
        if rows is not None:
            self.rows, self.rows_version = rows, new_version
            self.projection_error = None
        return copy.deepcopy(result)

    def _project(self, cursor, state, new_version, version, projected_version):
        """Refresh ama.* inside a savepoint. Returns the projected rows, or None when the tables stay as they were."""
        if self.clock() < self.retry_projection_at:
            return None
        cursor.execute("SAVE TRANSACTION proj")
        try:
            rows = projection.project(state)
            if self.rows is not None and self.rows_version == version == projected_version:
                sqlwrite.write_plan(cursor, projection.diff(self.rows, rows))
            else:
                sqlwrite.rebuild(cursor, rows)
            cursor.execute("UPDATE ama.StateDocument SET ProjectedVersion = ? WHERE Id = 1", (new_version,))
            return rows
        except Exception as error:
            log.exception("Relational projection failed; the document was still saved")
            self.projection_error = f"{type(error).__name__}: {error}"[:300]
            self.retry_projection_at = self.clock() + self.backoff
            self.rows = self.rows_version = None
            try:
                cursor.execute("ROLLBACK TRANSACTION proj")
            except Exception as rollback_error:
                raise TransactionLost("The server ended the transaction") from rollback_error
            return None

    def inspect(self):
        """Facts for the Analytics data-source panel: versions, document size, table row counts, KPI view."""
        return self._call(self._inspect_once)

    def _inspect_once(self):
        connection = self._open()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT Version, ProjectedVersion, UpdatedAt, DATALENGTH(Document) "
                           "FROM ama.StateDocument WHERE Id = 1")
            version, projected, updated, size = cursor.fetchone()
            expected = self.rows if self.rows is not None and self.rows_version == version else None
            tables = []
            for name in projection.TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM ama.{name}")
                rows = cursor.fetchone()[0]
                tables.append({"name": name, "rows": rows,
                               "match": None if expected is None else rows == len(expected[name])})
            cursor.execute("SELECT Scope, Orders, Decided, Completed, AvgElapsedSeconds, FalloutPercent "
                           "FROM ama.vw_KpiSummary ORDER BY Scope")
            kpi = [{"scope": scope, "orders": orders, "decided": decided, "completed": completed,
                    "avgElapsedSeconds": None if average is None else float(average),
                    "falloutPercent": None if fallout is None else float(fallout)}
                   for scope, orders, decided, completed, average, fallout in cursor.fetchall()]
            connection.commit()
        except BaseException:
            self._rollback()
            raise
        return {"server": self.server, "database": self.database, "version": version,
                "projectedVersion": projected, "inSync": projected == version, "updatedAt": str(updated),
                "documentBytes": size, "tables": tables, "views": {"kpiSummary": kpi},
                "projectionError": self.projection_error}

    def close(self):
        with self.lock:
            self._reset()
