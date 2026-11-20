"""Relational tables derived from the order state, for reporting and ad-hoc SQL.

The JSON document stays the source of truth. These tables are rebuilt from it on every change, hold no secrets (no
password hashes, e-mail addresses or phone numbers) and can always be recreated from the document.
"""

import re
from datetime import datetime, timezone

from app.analytics import facts_for

LIVE, BENCHMARK = "live", "benchmark"
TEXT_TYPE = re.compile(r"n?(?:var)?char\((\d+)\)")


def units(text):
    return len(text.encode("utf-16-le")) // 2


def text_limit(kind):
    match = TEXT_TYPE.match(kind)
    return int(match.group(1)) if match else None


class Table:
    def __init__(self, columns, key, indexes=()):
        self.columns = tuple(columns)
        self.names = tuple(name for name, _ in self.columns)
        self.key = tuple(key)
        self.key_positions = tuple(self.names.index(name) for name in self.key)
        self.indexes = tuple(indexes)
        self.limits = tuple(text_limit(kind) for _, kind in self.columns)

    def fit(self, row):
        """The row with any text longer than its column cut to size, so one odd value can never fail a batch."""
        fitted = list(row)
        for position, limit in enumerate(self.limits):
            value = fitted[position]
            if limit and isinstance(value, str) and len(value) > limit // 2:
                value = value[:limit]
                while units(value) > limit:
                    value = value[:-1]
                fitted[position] = value
        return tuple(fitted)


TABLES = {
    "Orders": Table((
        ("Scope", "nvarchar(12) NOT NULL"), ("OrderId", "nvarchar(32) NOT NULL"), ("RunId", "nvarchar(40) NULL"),
        ("Customer", "nvarchar(200) NOT NULL"), ("City", "nvarchar(40) NOT NULL"),
        ("Product", "nvarchar(40) NOT NULL"), ("Scenario", "nvarchar(32) NOT NULL"),
        ("Actor", "nvarchar(32) NOT NULL"), ("PartnerId", "nvarchar(32) NULL"), ("OwnerId", "nvarchar(32) NULL"),
        ("State", "nvarchar(32) NOT NULL"), ("Stage", "nvarchar(32) NOT NULL"), ("Language", "nvarchar(8) NOT NULL"),
        ("SiteReference", "nvarchar(40) NULL"), ("CreatedAt", "datetime2(6) NOT NULL"),
        ("UpdatedAt", "datetime2(6) NOT NULL"), ("DeadlineAt", "datetime2(6) NOT NULL"),
        ("CompletedAt", "datetime2(6) NULL"), ("ElapsedSeconds", "float NULL"), ("Outcome", "nvarchar(24) NOT NULL"),
        ("AgentSeconds", "float NOT NULL"), ("PeopleSeconds", "float NOT NULL"), ("Recovered", "bit NOT NULL"),
        ("RecoveryAttempts", "int NOT NULL"), ("SlaBreached", "bit NOT NULL"), ("StepCount", "int NOT NULL"),
    ), key=("Scope", "OrderId"), indexes=(("IX_Orders_State", ("Scope", "State")),
                                          ("IX_Orders_CreatedAt", ("Scope", "CreatedAt")))),
    "OrderSteps": Table((
        ("Scope", "nvarchar(12) NOT NULL"), ("OrderId", "nvarchar(32) NOT NULL"), ("Seq", "int NOT NULL"),
        ("At", "datetime2(6) NOT NULL"), ("Agent", "nvarchar(60) NOT NULL"), ("Tool", "nvarchar(80) NOT NULL"),
        ("Detail", "nvarchar(1000) NOT NULL"), ("Result", "nvarchar(24) NOT NULL"), ("Simulated", "bit NOT NULL"),
        ("QueueSeconds", "float NULL"), ("ProcessSeconds", "float NULL"), ("DurationSeconds", "float NULL"),
    ), key=("Scope", "OrderId", "Seq"), indexes=(("IX_OrderSteps_Agent", ("Scope", "Agent")),)),
    "Events": Table((
        ("Scope", "nvarchar(12) NOT NULL"), ("EventId", "nvarchar(40) NOT NULL"),
        ("OrderId", "nvarchar(32) NOT NULL"), ("Type", "nvarchar(60) NOT NULL"), ("Status", "nvarchar(24) NOT NULL"),
        ("CreatedAt", "datetime2(6) NOT NULL"), ("ProcessedAt", "datetime2(6) NULL"),
    ), key=("Scope", "EventId"), indexes=(("IX_Events_OrderId", ("Scope", "OrderId")),)),
    "AuditLog": Table((
        ("Scope", "nvarchar(12) NOT NULL"), ("Sequence", "int NOT NULL"), ("At", "datetime2(6) NOT NULL"),
        ("SubjectId", "nvarchar(40) NOT NULL"), ("Actor", "nvarchar(60) NOT NULL"),
        ("Action", "nvarchar(80) NOT NULL"), ("Detail", "nvarchar(1000) NOT NULL"), ("Hash", "char(64) NOT NULL"),
        ("PreviousHash", "char(64) NOT NULL"),
    ), key=("Scope", "Sequence"), indexes=(("IX_AuditLog_SubjectId", ("Scope", "SubjectId")),)),
    "Messages": Table((
        ("MessageId", "nvarchar(40) NOT NULL"), ("OrderId", "nvarchar(40) NOT NULL"), ("At", "datetime2(6) NOT NULL"),
        ("Source", "nvarchar(12) NOT NULL"), ("Kind", "nvarchar(16) NOT NULL"), ("Channel", "nvarchar(40) NOT NULL"),
        ("Status", "nvarchar(32) NOT NULL"), ("TextEn", "nvarchar(1000) NOT NULL"),
        ("TextTr", "nvarchar(1000) NOT NULL"), ("DeliveredExternally", "bit NOT NULL"),
    ), key=("MessageId",), indexes=(("IX_Messages_OrderId", ("OrderId",)),)),
    "Customers": Table((
        ("CustomerId", "nvarchar(32) NOT NULL"), ("Company", "nvarchar(200) NOT NULL"),
        ("Language", "nvarchar(8) NOT NULL"), ("Verified", "bit NOT NULL"), ("CreatedAt", "datetime2(6) NOT NULL"),
    ), key=("CustomerId",)),
    "Partners": Table((
        ("PartnerId", "nvarchar(32) NOT NULL"), ("Name", "nvarchar(200) NOT NULL"),
        ("Status", "nvarchar(16) NOT NULL"), ("Currency", "nvarchar(8) NOT NULL"),
        ("PaymentTermsDays", "int NOT NULL"), ("ApiClient", "bit NOT NULL"), ("CreatedAt", "datetime2(6) NOT NULL"),
    ), key=("PartnerId",)),
    "Concerns": Table((
        ("ConcernId", "nvarchar(32) NOT NULL"), ("PartnerId", "nvarchar(32) NOT NULL"),
        ("OrderId", "nvarchar(32) NULL"), ("Category", "nvarchar(32) NOT NULL"), ("Subject", "nvarchar(200) NOT NULL"),
        ("Status", "nvarchar(16) NOT NULL"), ("CreatedAt", "datetime2(6) NOT NULL"),
        ("UpdatedAt", "datetime2(6) NOT NULL"), ("MessageCount", "int NOT NULL"),
    ), key=("ConcernId",), indexes=(("IX_Concerns_PartnerId", ("PartnerId",)),)),
    "Inventory": Table((
        ("City", "nvarchar(40) NOT NULL"), ("Product", "nvarchar(40) NOT NULL"),
        ("AvailableUnits", "int NOT NULL"),
    ), key=("City", "Product")),
}


def stamp(value):
    """ISO text for a datetime2 column: naive UTC, language-neutral, microsecond precision."""
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    if moment.tzinfo:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.isoformat(sep="T", timespec="microseconds")


def flag(value):
    return bool(value)


def seconds_or_none(timing, key):
    return None if timing is None else timing[key]


def text(value):
    return "" if value is None else str(value)


def order_row(scope, run_id, order, facts):
    return (
        scope, order["id"], run_id, order["customer"], order["city"], order["product"], order["scenario"],
        order["actor"], order.get("partnerId"), order.get("ownerId"), order["state"], order["stage"],
        order.get("language") or "en", order.get("siteReference"), stamp(order["createdAt"]),
        stamp(order["updatedAt"]), stamp(order["deadline"]), stamp(order.get("completedAt")), facts["elapsed"],
        facts["outcome"], facts["agentSeconds"], facts["peopleSeconds"], flag(order.get("recovered")),
        int(order.get("recoveryAttempts", 0)), flag(order.get("slaBreached")), len(order["timeline"]),
    )


def step_row(scope, order, index, entry, timing):
    return (
        scope, order["id"], index, stamp(entry["at"]), entry["agent"], entry["tool"], text(entry["detail"]),
        entry["result"], flag(entry.get("simulated", True)), seconds_or_none(timing, "queue"),
        seconds_or_none(timing, "process"), seconds_or_none(timing, "total"),
    )


def add(rows, table, row):
    definition = TABLES[table]
    row = definition.fit(row)
    rows[table][tuple(row[position] for position in definition.key_positions)] = row


def project_source(rows, scope, source, run_id=None, with_audit=True):
    for order, facts in facts_for(source):
        add(rows, "Orders", order_row(scope, run_id, order, facts))
        for index, (entry, timing) in enumerate(zip(order["timeline"], facts["timings"]), 1):
            add(rows, "OrderSteps", step_row(scope, order, index, entry, timing))
    for event in source["events"]:
        add(rows, "Events", (scope, event["id"], event["orderId"], event["type"], event["status"],
                             stamp(event["createdAt"]), stamp(event.get("processedAt"))))
    for entry in source["audit"] if with_audit else ():
        add(rows, "AuditLog", (scope, entry["sequence"], stamp(entry["at"]), text(entry["orderId"]), entry["actor"],
                               entry["action"], text(entry["detail"]), entry["hash"], entry["previousHash"]))


def project_live_only(rows, state):
    for item in state.get("outbox", []):
        add(rows, "Messages", (item["id"], item["orderId"], stamp(item["at"]), "outbox", text(item.get("kind")),
                               text(item.get("channel")), text(item.get("status")), text(item.get("en")),
                               text(item.get("tr")), flag(item.get("deliveredExternally"))))
    for item in state.get("notifications", []):
        add(rows, "Messages", (item["id"], item["orderId"], stamp(item["at"]), "portal", "",
                               text(item.get("channel")), "portal", text(item.get("en")), text(item.get("tr")),
                               flag(item.get("deliveredExternally"))))
    for item in state.get("customers", {}).values():
        add(rows, "Customers", (item["id"], text(item.get("company")), item.get("language") or "en",
                                flag(item.get("verified")), stamp(item["createdAt"])))
    for item in state.get("partners", {}).values():
        add(rows, "Partners", (item["id"], item["name"], item["status"], item.get("currency") or "TRY",
                               int(item.get("paymentTermsDays", 30)), flag(item.get("apiClient")),
                               stamp(item["createdAt"])))
    for item in state.get("concerns", {}).values():
        add(rows, "Concerns", (item["id"], item["partnerId"], item.get("orderId"), item["category"], item["subject"],
                               item["status"], stamp(item["createdAt"]), stamp(item["updatedAt"]),
                               len(item.get("messages", []))))
    for item in state.get("inventory", []):
        add(rows, "Inventory", (item["city"], item["product"], int(item["availableUnits"])))


def project(state):
    """{table: {key: row}} for the live order book and, when present, the isolated benchmark run."""
    rows = {name: {} for name in TABLES}
    project_source(rows, LIVE, state)
    project_live_only(rows, state)
    benchmark = state.get("benchmark")
    if benchmark:
        project_source(rows, BENCHMARK, benchmark["sim"], benchmark["run"]["id"], with_audit=False)
    return rows


def diff(old, new):
    """Per table: keys to delete (removed or changed) and rows to insert (added or changed)."""
    plan = {}
    for name in TABLES:
        before, after = old.get(name, {}), new[name]
        changed = [key for key, row in after.items() if key in before and before[key] != row]
        gone = [key for key in before if key not in after]
        fresh = [after[key] for key in after if key not in before]
        plan[name] = {"delete": gone + changed, "insert": fresh + [after[key] for key in changed]}
    return plan
