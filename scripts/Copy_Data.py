"""Copy the data of one TurkBizTel stack into another, once.

The service-team single sign-on stack keeps its own database, so it starts without the enterprises, partners and orders
the access-token stack has gathered. This seeds it from that stack without linking the two. Each stack's database is
private, so every step runs inside that stack's own container; Copy-AppData.ps1 ships this file there and carries the
data between them. Nothing stays connected or synchronised afterwards, and the source stack is only read.

    summary           counts only; changes nothing
    export            what can be copied, as one batch printed between BEGIN and END markers
    import BATCH      add what the target lacks (--dry-run changes nothing); BATCH is the text itself or @FILE, a file
                      that is deleted once a real import has succeeded

Copied: enterprise accounts and partner contacts (with their password hash, so a password keeps working), partner
organisations, orders with their messages, partner concerns, idempotency keys, the event history, the audit trail (added
to the end of the target's own chain and signed again, each entry saying which entry it was) and the capacity units the
copied orders hold.

Left behind, because it belongs to the stack that issued it or is still in motion: sessions; one-time e-mail codes;
invitation, approval and tracking links; the benchmark simulation; and orders the agents are still working on, that wait
for an enterprise's approval link, or whose customer the source is still sending reminders to, all of which a later run
copies once they have settled. A message the source queued but has not sent yet is copied as "copied-not-sent": the
source sends it, the target must not send it a second time.
"""

import base64
import hashlib
import json
import os
import re
import sys
import zlib
from collections import Counter
from copy import deepcopy

ACCOUNT_KEYS = ("id", "email", "name", "company", "phone", "language", "passwordHash", "verified", "sessionVersion",
                "createdAt")
# Orders in these states wait for a person, or are finished: no agent acts on them until someone does.
SETTLED = {"completed", "rejected", "failed", "held", "pendingApproval", "awaitingCustomer"}
WAITING = {"held", "pendingApproval", "awaitingCustomer"}
UNSENT = ("scheduled-digest", "pending-delivery")
NOT_SENT = "copied-not-sent"
OUTCOMES = ("copied", "existing", "conflict", "invalid", "orphan", "full")
TABLES = ("customers", "partners", "partnerUsers", "orders", "concerns", "idempotency", "events", "audit")
ENTRY_KEYS = ("sequence", "at", "orderId", "actor", "action", "detail", "by", "changes")
ORDER_TEXT = ("id", "customer", "city", "product", "scenario", "actor", "state", "stage", "createdAt", "updatedAt",
              "deadline")
ORDER_LISTS = ("timeline", "serviceOrders", "resourceOrders", "notifications", "approvals")
# The keys the app reads directly. "contact" is not one: an order placed without one, or by an older version, has none.
ORDER_FIELDS = ("reserved", "recovered", "exception", "siteReference")
ID = re.compile(r"[A-Za-z0-9-]{3,40}")
ACCOUNT_ACTOR, ACCOUNT_ACTION = "account-copy", "account.copied"
COPY_ACTOR, COPY_ACTION, COPY_ID = "data-copy", "data.copied", "DATA-COPY"
LINE = 100


def text_of(value):
    return isinstance(value, str) and bool(value.strip())


def counter():
    return dict.fromkeys(OUTCOMES, 0)


def pack(batch):
    body = base64.urlsafe_b64encode(zlib.compress(json.dumps(batch, separators=(",", ":")).encode(), 9)).decode()
    return hashlib.sha256(body.encode()).hexdigest()[:16] + "." + body


def unpack(text):
    checksum, _, body = text.partition(".")
    if hashlib.sha256(body.encode()).hexdigest()[:16] != checksum:
        raise ValueError("The batch arrived damaged")
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(body)))


def read_batch(argument):
    """A batch given on the command line, or @FILE for one too long for it."""
    if not argument.startswith("@"):
        return argument
    with open(argument[1:], encoding="ascii") as handle:
        return handle.read().strip()


def chased(order):
    """Whether the source still sends reminders about an order that waits for someone. The reminders stop once the
    order is escalated, and only the source should send them."""
    exception = order.get("exception")
    return bool(exception) and order["state"] in WAITING and not exception.get("escalated")


def export_state(state):
    """Everything of a state that can move to another stack, as one batch. Orders that agents are still working on,
    or that the source still sends reminders about, stay behind with everything that refers to them, so that both
    stacks never act on the same order at the same time."""
    from app.domain import verify_audit

    orders = state.get("orders", {})
    held = {key for key, order in orders.items() if order["state"] not in SETTLED or chased(order)}
    kept = {key: order for key, order in orders.items() if key not in held}
    trail = state.get("audit", [])
    intact = verify_audit(state)
    return {
        "customers": [{key: item[key] for key in ACCOUNT_KEYS if key in item}
                      for item in state.get("customers", {}).values()],
        "partners": list(state.get("partners", {}).values()),
        "partnerUsers": [{**item, "invite": None, "reset": None} for item in state.get("partnerUsers", {}).values()],
        "orders": list(kept.values()),
        "concerns": [item for item in state.get("concerns", {}).values() if item.get("orderId") not in held],
        "idempotency": {key: item for key, item in state.get("idempotency", {}).items() if item["orderId"] in kept},
        "events": [item for item in state.get("events", []) if item["orderId"] in kept],
        # Without its hashes, which the target computes again, and only when the source chain is intact: a chain
        # that does not verify is evidence of tampering, and signing it again would hide that.
        "audit": {"length": len(trail), "head": trail[-1]["hash"] if trail else None, "intact": intact,
                  "entries": [{key: entry[key] for key in ENTRY_KEYS if key in entry}
                              for entry in trail if entry["orderId"] not in held] if intact else []},
        "heldBack": len(held),
    }


def valid_account(record, email_pattern):
    try:
        return bool(ID.fullmatch(record["id"]) and email_pattern.match(record["email"])
                    and text_of(record["name"]) and text_of(record["company"])
                    and record["passwordHash"].startswith("scrypt$"))
    except (KeyError, TypeError, AttributeError):
        return False


def merge_accounts(state, records, limit=None):
    """Add every account the state does not hold yet and return counts only: copied; existing (this very account is
    already there); conflict (a different account holds its id or e-mail address); invalid; full (account limit)."""
    from app.accounts import EMAIL, find_account  # imported here: the source stack may run an older image
    from app.domain import audit, now

    limit = int(os.getenv("AMA_MAX_CUSTOMERS", "1000")) if limit is None else limit
    report = counter()
    customers = state.get("customers")
    if customers is None:
        customers = {}
    for record in records:
        if not valid_account(record, EMAIL):
            report["invalid"] += 1
            continue
        email = record["email"].strip().lower()
        held = customers.get(record["id"])
        if held is not None or find_account(state, email)[1] is not None:
            report["existing" if held is not None and held["email"] == email else "conflict"] += 1
        elif len(customers) >= limit:
            report["full"] += 1
        else:
            customers[record["id"]] = {
                "phone": None, "language": "en", "verified": False, "sessionVersion": 1, "createdAt": now(),
                **{key: record[key] for key in ACCOUNT_KEYS if key in record}, "email": email,
                "verification": None, "reset": None}
            state["customers"] = customers
            audit(state, record["id"], ACCOUNT_ACTOR, ACCOUNT_ACTION,
                  "Customer account copied from another stack (one-time snapshot)")
            report["copied"] += 1
    return report


def merge_partners(state, records):
    """Partner organisations. A name is unique whatever its case, and one organisation at most is the API client, so
    a copied one never takes that role from the one the target already has."""
    limit = int(os.getenv("AMA_MAX_PARTNERS", "50"))
    report = counter()
    partners = state.get("partners")
    if partners is None:
        partners = {}
    for record in records:
        if not (isinstance(record, dict) and isinstance(record.get("id"), str) and ID.fullmatch(record["id"])
                and text_of(record.get("name")) and text_of(record.get("createdAt"))):
            report["invalid"] += 1
            continue
        name = record["name"].strip().casefold()
        held = partners.get(record["id"])
        namesake = next((item for item in partners.values() if item["name"].strip().casefold() == name), None)
        if held is not None:
            report["existing" if held["name"].strip().casefold() == name else "conflict"] += 1
        elif namesake is not None:
            report["conflict"] += 1
        elif len(partners) >= limit:
            report["full"] += 1
        else:
            partner = deepcopy(record)
            partner.setdefault("payments", {})
            if partner.get("apiClient") and any(item.get("apiClient") for item in partners.values()):
                partner["apiClient"] = False
            partners[partner["id"]] = partner
            state["partners"] = partners
            report["copied"] += 1
    return report


def valid_contact(record, email_pattern):
    try:
        return bool(ID.fullmatch(record["id"]) and text_of(record["partnerId"]) and email_pattern.match(record["email"])
                    and text_of(record["name"]) and text_of(record["createdAt"])
                    and (record["passwordHash"] is None or record["passwordHash"].startswith("scrypt$")))
    except (KeyError, TypeError, AttributeError):
        return False


def merge_partner_users(state, records):
    """Partner contacts, with their password hash. An invitation or reset link is signed with the issuing stack's own
    secret, so it cannot move: a contact who had not accepted yet is invited again from the partner page."""
    from app.accounts import EMAIL, find_account
    from app.partners import MAX_CONTACTS

    report = counter()
    partners = state.get("partners") or {}
    users = state.get("partnerUsers")
    if users is None:
        users = {}
    for record in records:
        if not valid_contact(record, EMAIL):
            report["invalid"] += 1
            continue
        email = record["email"].strip().lower()
        held = users.get(record["id"])
        if held is not None:
            report["existing" if held["email"] == email else "conflict"] += 1
        elif find_account(state, email)[1] is not None:
            report["conflict"] += 1
        elif record["partnerId"] not in partners:
            report["orphan"] += 1
        elif sum(item["partnerId"] == record["partnerId"] for item in users.values()) >= MAX_CONTACTS:
            report["full"] += 1
        else:
            users[record["id"]] = {**deepcopy(record), "email": email, "invite": None, "reset": None}
            state["partnerUsers"] = users
            report["copied"] += 1
    return report


def valid_order(record, cities, products):
    try:
        return bool(
            all(text_of(record[key]) for key in ORDER_TEXT) and ID.fullmatch(record["id"])
            and record["state"] in SETTLED and record["city"] in cities and record["product"] in products
            and all(isinstance(record[key], list) for key in ORDER_LISTS) and all(key in record for key in ORDER_FIELDS)
            and (record["state"] != "completed" or "elapsedSeconds" in record)
            and all(isinstance(item, dict) and item.get("id") and item.get("at")
                    for item in record["notifications"] + record.get("outbox", [])))
    except (KeyError, TypeError):
        return False


def merge_orders(state, records):
    """Orders, with their own messages. Each copied order must find its enterprise or partner in the target. A message
    still waiting to go out (a digest, or a send in progress) is marked copied-not-sent, so that only the source sends
    it. Returns the counts, the orders added and how many messages were held back from sending."""
    from app.domain import CITIES

    limit = int(os.getenv("AMA_MAX_ORDERS", "200"))
    report = counter()
    orders = state["orders"]
    products = {item["id"] for item in state["catalogue"]}
    added, held_back = [], 0
    for record in records:
        if not valid_order(record, CITIES, products):
            report["invalid"] += 1
        elif record["id"] in orders:
            report["existing"] += 1
        elif ((record.get("ownerId") and record["ownerId"] not in (state.get("customers") or {}))
              or (record.get("partnerId") and record["partnerId"] not in (state.get("partners") or {}))):
            report["orphan"] += 1
        elif len(orders) >= limit:
            report["full"] += 1
        else:
            order = deepcopy(record)
            order.setdefault("outbox", [])
            order.setdefault("contact", None)
            for item in order["outbox"]:
                if item.get("status") in UNSENT:
                    item["copiedStatus"], item["status"] = item["status"], NOT_SENT
                    held_back += 1
            orders[order["id"]] = order
            added.append(order)
            report["copied"] += 1
    return report, added, held_back


def mirror_messages(state, added):
    """The state also keeps one list of every message; the new orders' messages join it, oldest first."""
    for key in ("notifications", "outbox"):
        merged = state.get(key)
        if merged is None:
            merged = state[key] = []
        known = {item["id"] for item in merged}
        merged.extend(deepcopy(item) for order in added for item in order.get(key, []) if item["id"] not in known)
        merged.sort(key=lambda item: item["at"])


def hold_capacity(state, added):
    """Each reserved order holds one unit of its city and product, which the target has not yet given up."""
    units = 0
    for order in added:
        cell = next((item for item in state["inventory"]
                     if item["city"] == order["city"] and item["product"] == order["product"]), None)
        if order.get("reserved") and cell and cell["availableUnits"] > 0:
            cell["availableUnits"] -= 1
            units += 1
    return units


def valid_concern(record):
    try:
        return bool(ID.fullmatch(record["id"])
                    and all(text_of(record[key]) for key in ("partnerId", "category", "subject", "status", "createdAt",
                                                             "updatedAt", "createdByName"))
                    and isinstance(record["messages"], list)
                    and all(isinstance(item, dict) and text_of(item.get("from")) and text_of(item.get("name"))
                            and text_of(item.get("at")) for item in record["messages"]))
    except (KeyError, TypeError):
        return False


def merge_concerns(state, records):
    report = counter()
    partners, orders = state.get("partners") or {}, state["orders"]
    concerns = state.get("concerns")
    if concerns is None:
        concerns = {}
    for record in records:
        if not valid_concern(record):
            report["invalid"] += 1
        elif record["id"] in concerns:
            report["existing"] += 1
        elif record["partnerId"] not in partners or (record.get("orderId") and record["orderId"] not in orders):
            report["orphan"] += 1
        else:
            concerns[record["id"]] = deepcopy(record)
            state["concerns"] = concerns
            report["copied"] += 1
    return report


def merge_idempotency(state, entries):
    """A key stays tied to the order it created; one whose order is not in the target is left out, since replaying
    that key there would find no order."""
    report = counter()
    for key, item in entries.items():
        if not (text_of(key) and isinstance(item, dict) and text_of(item.get("fingerprint"))
                and text_of(item.get("orderId"))):
            report["invalid"] += 1
        elif key in state["idempotency"]:
            report["existing" if state["idempotency"][key]["orderId"] == item["orderId"] else "conflict"] += 1
        elif item["orderId"] not in state["orders"]:
            report["orphan"] += 1
        else:
            state["idempotency"][key] = deepcopy(item)
            report["copied"] += 1
    return report


def merge_events(state, records):
    report = counter()
    known = {item["id"] for item in state["events"]}
    for record in records:
        if not (isinstance(record, dict) and all(text_of(record.get(key)) for key in ("id", "orderId", "type", "status",
                                                                                       "createdAt"))):
            report["invalid"] += 1
        elif record["id"] in known:
            report["existing"] += 1
        elif record["orderId"] not in state["orders"]:
            report["orphan"] += 1
        else:
            state["events"].append(deepcopy(record))
            known.add(record["id"])
            report["copied"] += 1
    return report


def sign(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()


def merge_audit(state, trail):
    """Add the source's audit entries to the end of the target's chain, each signed again in its new place and saying
    which entry it was (imported.sequence). A record's history moves once, together with the record: a record whose
    history is already here keeps it as it is, so a later run never adds a history the record itself does not show.
    An entry about a record the target does not hold is left out."""
    report = counter()
    chain = state["audit"]
    done = {entry["orderId"] for entry in chain if entry.get("imported")}
    known = set(state["orders"])
    for name in ("customers", "partners", "partnerUsers", "concerns"):
        known.update(state.get(name) or {})
    for entry in trail:
        if not (isinstance(entry, dict) and isinstance(entry.get("sequence"), int)
                and all(text_of(entry.get(key)) for key in ("at", "orderId", "actor", "action"))):
            report["invalid"] += 1
        elif entry["orderId"] in done:
            report["existing"] += 1
        elif entry["orderId"] not in known:
            report["orphan"] += 1
        else:
            item = {"sequence": len(chain) + 1, **{key: deepcopy(entry[key]) for key in ENTRY_KEYS[1:] if key in entry},
                    "previousHash": chain[-1]["hash"] if chain else "0" * 64,
                    "imported": {"sequence": entry["sequence"]}}
            item["hash"] = sign(item)
            chain.append(item)
            report["copied"] += 1
    return report


def describe(report, source):
    names = (("orders", "orders"), ("customers", "enterprise accounts"), ("partners", "partners"),
             ("partnerUsers", "partner contacts"), ("concerns", "concerns"), ("events", "events"),
             ("audit", "audit entries"))
    counts = [f"{report[key]['copied']} {label}" for key, label in names if report[key]["copied"]]
    history = (f"its audit chain of {source.get('length')} entries was intact (head {str(source.get('head'))[:16]})"
               if source.get("intact") else "its audit chain did not verify, so none of its entries were copied")
    return "Copied from another stack (one-time snapshot): " + ", ".join(counts) + "; " + history


def merge_state(state, batch):
    """Add to the state whatever the batch holds that it lacks, never changing a record it already has, and return
    counts only. Referenced records come first (enterprises, partners, contacts), then orders and what hangs on them."""
    report = {}
    report["customers"] = merge_accounts(state, batch.get("customers", []))
    report["partners"] = merge_partners(state, batch.get("partners", []))
    report["partnerUsers"] = merge_partner_users(state, batch.get("partnerUsers", []))
    report["orders"], added, report["unsent"] = merge_orders(state, batch.get("orders", []))
    if added:
        mirror_messages(state, added)
    report["capacity"] = hold_capacity(state, added)
    report["concerns"] = merge_concerns(state, batch.get("concerns", []))
    report["idempotency"] = merge_idempotency(state, batch.get("idempotency", {}))
    report["events"] = merge_events(state, batch.get("events", []))
    source = batch.get("audit") or {}
    report["audit"] = merge_audit(state, source.get("entries", []))
    report["auditIntact"] = bool(source.get("intact"))
    report["heldBack"] = int(batch.get("heldBack", 0))
    report["needInvite"] = sum(item["passwordHash"] is None for item in (state.get("partnerUsers") or {}).values())
    if sum(report[key]["copied"] for key in TABLES):
        from app.domain import audit

        audit(state, COPY_ID, COPY_ACTOR, COPY_ACTION, describe(report, source))
    return report


def summarise(state):
    from app.domain import verify_audit

    customers = list(state.get("customers", {}).values())
    orders = list(state["orders"].values())
    messages = state.get("outbox", [])
    result = {
        "customers": len(customers), "verified": sum(bool(item.get("verified")) for item in customers),
        "partners": len(state.get("partners", {})), "partnerUsers": len(state.get("partnerUsers", {})),
        "orders": len(orders), "states": dict(Counter(order["state"] for order in orders)),
        "reserved": sum(bool(order.get("reserved")) for order in orders),
        "concerns": len(state.get("concerns", {})), "idempotency": len(state["idempotency"]),
        "events": len(state["events"]), "audit": len(state["audit"]),
        "imported": sum("imported" in entry for entry in state["audit"]), "auditIntact": verify_audit(state),
        "units": sum(int(cell["availableUnits"]) for cell in state["inventory"]),
        "messages": len(messages), "notSent": sum(item.get("status") == NOT_SENT for item in messages)}
    try:
        from app.approvals import directory
    except ImportError:
        return result
    rows = directory(state)
    return {**result, "picker": {"listed": len(rows), "selectable": sum(row["verified"] for row in rows)}}


def print_batch(batch):
    packed = pack(batch)
    counts = {key: len(batch[key]) for key in TABLES if key != "audit"}
    counts.update(audit=len(batch["audit"]["entries"]), heldBack=batch["heldBack"],
                  auditIntact=batch["audit"]["intact"], size=len(packed))
    print("RESULT " + json.dumps(counts))
    print("BEGIN 1")
    for start in range(0, len(packed), LINE):
        print(packed[start:start + LINE])
    print("END 1")


def main(args):
    mode = args[0] if args else ""
    if mode not in ("summary", "export", "import"):
        raise ValueError("Use summary, export or import")
    if mode == "import" and len(args) < 2:
        raise ValueError("Give the batch, or @FILE")
    from app.store import open_store

    store = open_store()
    try:
        if mode == "summary":
            print("RESULT " + json.dumps(summarise(store.read())))
        elif mode == "export":
            print_batch(export_state(store.read()))
        else:
            batch = unpack(read_batch(args[1]))
            dry = "--dry-run" in args
            report = (merge_state(store.read(), batch) if dry
                      else store.transact(lambda state: merge_state(state, batch)))
            if args[1].startswith("@") and not dry:
                os.remove(args[1][1:])
            print("RESULT " + json.dumps({**report, "dryRun": dry}))
    finally:
        store.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        print("ERROR %s: %s" % (type(error).__name__, str(error)[:200]))
        sys.exit(1)
