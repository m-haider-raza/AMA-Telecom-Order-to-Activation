"""Batched writes for the relational projection: multi-row INSERT and keyed DELETE statements."""

from app.projection import TABLES

MAX_PARAMETERS = 2000
MAX_ROWS = 1000


def insert_statements(name, rows):
    """(sql, parameters) pairs that insert rows. NULLs are written as literals so no parameter needs a type."""
    table = TABLES[name]
    columns = ", ".join(table.names)
    per_statement = max(1, min(MAX_ROWS, MAX_PARAMETERS // len(table.names)))
    for start in range(0, len(rows), per_statement):
        values, parameters = [], []
        for row in rows[start:start + per_statement]:
            marks = []
            for value in row:
                if value is None:
                    marks.append("NULL")
                else:
                    marks.append("?")
                    parameters.append(value)
            values.append("(" + ", ".join(marks) + ")")
        yield f"INSERT INTO ama.{name} ({columns}) VALUES " + ", ".join(values), tuple(parameters)


def delete_statements(name, keys):
    table = TABLES[name]
    per_statement = max(1, MAX_PARAMETERS // len(table.key))
    for start in range(0, len(keys), per_statement):
        chunk = keys[start:start + per_statement]
        if len(table.key) == 1:
            sql = f"DELETE FROM ama.{name} WHERE {table.key[0]} IN ({', '.join('?' * len(chunk))})"
        else:
            clause = "(" + " AND ".join(f"{column} = ?" for column in table.key) + ")"
            sql = f"DELETE FROM ama.{name} WHERE " + " OR ".join([clause] * len(chunk))
        yield sql, tuple(part for key in chunk for part in key)


def write_plan(cursor, plan):
    """Apply a projection.diff() plan: removals first, then inserts."""
    for name in TABLES:
        for sql, parameters in delete_statements(name, plan[name]["delete"]):
            cursor.execute(sql, parameters)
    for name in TABLES:
        for sql, parameters in insert_statements(name, plan[name]["insert"]):
            cursor.execute(sql, parameters)


def rebuild(cursor, rows):
    """Replace every projected table with rows ({table: {key: row}})."""
    for name in TABLES:
        cursor.execute(f"DELETE FROM ama.{name}")
    write_plan(cursor, {name: {"delete": [], "insert": list(rows[name].values())} for name in TABLES})
