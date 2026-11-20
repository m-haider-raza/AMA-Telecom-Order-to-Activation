"""Tables, indexes and views the SQL store needs. Every statement is idempotent and gated on ama.SchemaInfo."""

from app.analytics import SLA_SECONDS
from app.projection import TABLES

SCHEMA_VERSION = 1

DOCUMENT_TABLE = """
CREATE TABLE ama.StateDocument (
    Id int NOT NULL CONSTRAINT PK_StateDocument PRIMARY KEY CONSTRAINT CK_StateDocument_Id CHECK (Id = 1),
    Version bigint NOT NULL,
    ProjectedVersion bigint NULL,
    UpdatedAt datetime2(3) NOT NULL CONSTRAINT DF_StateDocument_UpdatedAt DEFAULT SYSUTCDATETIME(),
    Document nvarchar(max) NOT NULL CONSTRAINT CK_StateDocument_Json CHECK (ISJSON(Document) = 1)
)"""

INFO_TABLE = """
CREATE TABLE ama.SchemaInfo (
    Id int NOT NULL CONSTRAINT PK_SchemaInfo PRIMARY KEY CONSTRAINT CK_SchemaInfo_Id CHECK (Id = 1),
    Version int NOT NULL,
    AppliedAt datetime2(3) NOT NULL CONSTRAINT DF_SchemaInfo_AppliedAt DEFAULT SYSUTCDATETIME()
)"""

VIEWS = {
    "vw_OrderCycle": f"""
CREATE OR ALTER VIEW ama.vw_OrderCycle AS
SELECT Scope, OrderId, Customer, City, Product, Scenario, Outcome, CreatedAt, CompletedAt, ElapsedSeconds,
       ElapsedSeconds / 86400.0 AS ElapsedDays,
       CASE WHEN ElapsedSeconds <= {SLA_SECONDS} THEN 1 ELSE 0 END AS WithinSevenDays
FROM ama.Orders
WHERE State = N'completed'""",
    "vw_AgentPerformance": """
CREATE OR ALTER VIEW ama.vw_AgentPerformance AS
SELECT Scope, Agent, COUNT(*) AS Steps, AVG(DurationSeconds) AS AvgSeconds, MAX(DurationSeconds) AS MaxSeconds,
       AVG(QueueSeconds) AS AvgQueueSeconds, AVG(ProcessSeconds) AS AvgProcessSeconds
FROM ama.OrderSteps
WHERE DurationSeconds IS NOT NULL
GROUP BY Scope, Agent""",
    "vw_KpiSummary": """
CREATE OR ALTER VIEW ama.vw_KpiSummary AS
SELECT Scope, COUNT(*) AS Orders,
       SUM(CASE WHEN Outcome <> N'in-flight' THEN 1 ELSE 0 END) AS Decided,
       SUM(CASE WHEN State = N'completed' THEN 1 ELSE 0 END) AS Completed,
       AVG(CASE WHEN State = N'completed' THEN ElapsedSeconds END) AS AvgElapsedSeconds,
       CAST(100.0 * SUM(CASE WHEN Outcome IN (N'needs-people', N'failed') THEN 1 ELSE 0 END)
            / NULLIF(SUM(CASE WHEN Outcome <> N'in-flight' THEN 1 ELSE 0 END), 0) AS decimal(5, 1)) AS FalloutPercent
FROM ama.Orders
GROUP BY Scope""",
}


def table_statement(name, table):
    columns = ",\n    ".join(f"{column} {kind}" for column, kind in table.columns)
    key = ", ".join(table.key)
    return (f"IF OBJECT_ID(N'ama.{name}', N'U') IS NULL\nCREATE TABLE ama.{name} (\n    {columns},\n"
            f"    CONSTRAINT PK_{name} PRIMARY KEY ({key})\n)")


def index_statement(name, index, columns):
    return (f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'{index}' "
            f"AND object_id = OBJECT_ID(N'ama.{name}'))\nCREATE INDEX {index} ON ama.{name} ({', '.join(columns)})")


def statements():
    yield "IF SCHEMA_ID(N'ama') IS NULL EXEC(N'CREATE SCHEMA ama')"
    yield "IF OBJECT_ID(N'ama.StateDocument', N'U') IS NULL" + DOCUMENT_TABLE
    yield "IF OBJECT_ID(N'ama.SchemaInfo', N'U') IS NULL" + INFO_TABLE
    for name, table in TABLES.items():
        yield table_statement(name, table)
        for index, columns in table.indexes:
            yield index_statement(name, index, columns)
    yield from VIEWS.values()
    yield (f"UPDATE ama.SchemaInfo SET Version = {SCHEMA_VERSION}, AppliedAt = SYSUTCDATETIME() WHERE Id = 1;\n"
           f"IF @@ROWCOUNT = 0 INSERT INTO ama.SchemaInfo (Id, Version) VALUES (1, {SCHEMA_VERSION})")


def current_version(cursor):
    cursor.execute("SELECT OBJECT_ID(N'ama.SchemaInfo', N'U')")
    if cursor.fetchone()[0] is None:
        return 0
    cursor.execute("SELECT Version FROM ama.SchemaInfo WHERE Id = 1")
    row = cursor.fetchone()
    return row[0] if row else 0


def ensure_schema(cursor):
    """Create or upgrade everything in ama.*. Returns True when it changed anything; the caller commits."""
    if current_version(cursor) >= SCHEMA_VERSION:
        return False
    for statement in statements():
        cursor.execute(statement)
    return True
