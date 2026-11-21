import json
import re
from copy import deepcopy

import pytest

from app import accounts, approvals, auth, partners
from app.delivery import deliver_outbox
from app.domain import (approve_exception, audit, create_order, customer_view, flush_digests, follow_up,
                        initial_state, process_next, public_order, verify_audit)
from app.security import Actor
from scripts import Copy_Data as copy_data

PASSWORD = "Correct-Horse-42"
CONTACT = {"contactName": "Ayse Yilmaz", "email": "ayse@acme.example", "preferredChannel": "email",
           "updateFrequency": "every-step"}
STAFF = {"oid": "oid-0001", "upn": "staff@example.invalid", "name": "Test Staff", "role": "ServiceManager"}
# The two orders of the source that are not finished with: one is being worked on, one is still being chased.
IN_PROGRESS = ("partner-working-001", "staff-chased-0001")
ORPHAN = "BM-20260101"
_built = {}


@pytest.fixture(autouse=True)
def live_stacks(monkeypatch):
    """Both stacks run live: no demo orders, any company name, and e-mail delivery on, so messages wait to be sent."""
    monkeypatch.setenv("AMA_MODE", "live")
    monkeypatch.setenv("AMA_DELIVERY", "live")
    for name in ("AZURE_STORAGE_ACCOUNT_URL", "AMA_SQL_SERVER", "AMA_SQL_DATABASE", "LOCAL_STATE_PATH"):
        monkeypatch.delenv(name, raising=False)


def stored(value):
    """What a store keeps: plain JSON, so the per-order and the global message lists are separate copies."""
    return json.loads(json.dumps(value))


def tick(state, order_id, times=12):
    for _ in range(times):
        process_next(state, order_id)


def order_body(key, scenario="happy", **changes):
    return {"customer": "Acme Lojistik AS", "city": "Istanbul", "product": "sdwan", "scenario": scenario,
            "language": "en", "idempotencyKey": key, "contact": CONTACT, **changes}


def order_for(state, key):
    return next(order for order in state["orders"].values() if order["idempotencyKey"] == key)


def held_ids(state):
    return {order_for(state, key)["id"] for key in IN_PROGRESS}


def build_source():
    """A stack that has been used: two enterprises, a partner with two contacts, orders in every settled state, one
    order still being worked on and one still being chased, partner concerns and a staff member's decisions."""
    state = initial_state()
    for email, company in (("ayse@acme.example", "Acme Lojistik AS"), ("john@globex.example", "Globex Ltd")):
        _, code = auth.new_code("verify", email)
        accounts.register(state, {"email": email, "name": "Contact of " + company, "company": company,
                                  "phone": "+90 555 000 00 00", "language": "tr"},
                          auth.hash_password(PASSWORD), code)
    acme = accounts.find_by_email(state, "ayse@acme.example")
    acme.update(verified=True, verification=None)
    accounts.start_reset(state, acme["email"], auth.new_code("reset", acme["email"])[1])

    token, invite = auth.new_invite()
    partner, _ = partners.create_partner(
        state, {"name": "Balkan Wholesale", "paymentTermsDays": 30, "billingEmail": "billing@balkan.example",
                "apiClient": True, "contactName": "Mira Petrov", "email": "mira@balkan.example"}, invite)
    mira, _ = partners.accept_invite(state, token, auth.hash_password(PASSWORD))
    partners.add_contact(state, partner, {"name": "Kaan Demir", "email": "kaan@balkan.example", "language": "tr"},
                         auth.new_invite()[1])

    staff = Actor("service-manager", STAFF)
    for order in (
        create_order(state, order_body("staff-happy-0001"), "service-manager"),
        accounts.place_order(state, acme, order_body("portal-happy-0001",
                                                     siteAddress="Levent Mah. 12, Besiktas, Istanbul")),
        partners.create_for_partner(state, partner, order_body("partner-recover-001", "auto-recovery",
                                                               city="Ankara", product="cloud"), create_order),
        create_order(state, order_body("staff-decide-0001", "human-exception"), "service-manager"),
        create_order(state, order_body("staff-window-0001", "change-window"), "service-manager"),
        create_order(state, order_body("staff-rejected-001", "human-exception"), "service-manager"),
        accounts.place_order(state, acme, order_body("portal-address-001", "address-correction",
                                                     siteAddress="Somewhere 5, Ankara")),
    ):
        tick(state, order["id"])
    approve_exception(state, order_for(state, "staff-rejected-001")["id"], staff, "reject")
    for _ in range(4):  # three reminders, then the escalation that ends them
        follow_up(state, interval_seconds=0)

    chased = create_order(state, order_body("staff-chased-0001", "address-correction"), "service-manager")
    tick(state, chased["id"], 1)
    working = partners.create_for_partner(state, partner, order_body("partner-working-001"), create_order)
    tick(state, working["id"], 1)

    partners.raise_concern(state, partner, mira, {"orderId": working["id"], "category": "delivery",
                                                  "subject": "Delivery date", "text": "When will it be active?"})
    billing = partners.raise_concern(state, partner, mira, {"orderId": None, "category": "billing",
                                                            "subject": "Invoice question", "text": "Send it again."})
    partners.add_message(state, billing["id"], None, "We are looking into it.", actor=staff)
    audit(state, ORPHAN, "benchmark", "benchmark.completed", "A benchmark run")
    return state


def source_state():
    if "state" not in _built:
        _built["state"] = build_source()
    return stored(_built["state"])


def point_store_at(monkeypatch, path):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(path))


def results_of(output):
    return [json.loads(line[len("RESULT "):]) for line in output.splitlines() if line.startswith("RESULT ")]


class Memory:
    """Just enough of a store for the delivery step."""

    def __init__(self, state):
        self.state = state

    def read(self):
        return deepcopy(self.state)

    def transact(self, change):
        return change(self.state)


def test_export_leaves_secrets_and_work_in_progress_behind():
    source = source_state()
    before = json.dumps(source, sort_keys=True)
    left = held_ids(source)

    batch = copy_data.export_state(source)

    assert json.dumps(source, sort_keys=True) == before
    assert {order["id"] for order in batch["orders"]} == set(source["orders"]) - left
    assert batch["heldBack"] == 2
    assert not left & {event["orderId"] for event in batch["events"]}
    assert not left & {item["orderId"] for item in batch["idempotency"].values()}
    assert not left & {entry["orderId"] for entry in batch["audit"]["entries"]}
    assert [item["subject"] for item in batch["concerns"]] == ["Invoice question"]
    assert all(set(item) <= set(copy_data.ACCOUNT_KEYS) for item in batch["customers"])
    assert all(item["invite"] is None and item["reset"] is None for item in batch["partnerUsers"])
    assert not {"hash", "previousHash"} & set().union(*batch["audit"]["entries"])
    assert batch["audit"]["intact"] is True and batch["audit"]["length"] == len(source["audit"])


def test_a_copy_brings_across_everything_that_has_settled_and_keeps_the_target_consistent():
    source = source_state()
    before = json.dumps(source, sort_keys=True)
    left = held_ids(source)
    target = initial_state()
    audit(target, "STAFF-1", "service-manager", "staff.signed-in", "A staff member signed in")
    own = deepcopy(target["audit"])

    report = copy_data.merge_state(target, stored(copy_data.export_state(source)))

    assert json.dumps(source, sort_keys=True) == before
    assert {key: report[key]["copied"] for key in copy_data.TABLES if key not in ("events", "audit")} == {
        "customers": 2, "partners": 1, "partnerUsers": 2, "orders": 7, "concerns": 1, "idempotency": 7}
    assert report["events"]["copied"] == sum(event["orderId"] not in left for event in source["events"])
    assert report["heldBack"] == 2 and report["needInvite"] == 1 and report["auditIntact"] is True

    for key in set(source["orders"]) - left:
        copied, original = target["orders"][key], source["orders"][key]
        assert {name: value for name, value in copied.items() if name != "outbox"} == {
            name: value for name, value in original.items() if name != "outbox"}
        assert [item["id"] for item in copied["outbox"]] == [item["id"] for item in original["outbox"]]
    assert not left & set(target["orders"])
    portal = order_for(target, "portal-happy-0001")
    assert portal["ownerId"] in target["customers"]
    assert order_for(target, "partner-recover-001")["partnerId"] in target["partners"]
    assert create_order(target, order_body("staff-happy-0001"), "service-manager")["id"] == order_for(
        target, "staff-happy-0001")["id"]
    assert len(target["orders"]) == 7

    for customer in target["customers"].values():
        assert auth.verify_password(PASSWORD, customer["passwordHash"])
        assert customer["verification"] is None and customer["reset"] is None
    assert [(row["company"], row["verified"]) for row in approvals.directory(target)] == [
        ("Acme Lojistik AS", True), ("Globex Ltd", False)]
    assert sorted(bool(item["passwordHash"]) for item in target["partnerUsers"].values()) == [False, True]
    assert all(item["invite"] is None and item["reset"] is None for item in target["partnerUsers"].values())
    assert next(iter(target["partners"].values()))["apiClient"] is True
    assert target["concerns"] == {key: value for key, value in source["concerns"].items()
                                  if value["subject"] == "Invoice question"}
    assert target["inventory"] == source["inventory"]

    assert verify_audit(target) and target["audit"][:len(own)] == own
    imported = [entry for entry in target["audit"] if "imported" in entry]
    assert len(imported) == report["audit"]["copied"] == sum(
        entry["orderId"] not in left | {ORPHAN} for entry in source["audit"])
    assert report["audit"]["orphan"] == 1 and report["audit"]["invalid"] == 0
    original = {entry["sequence"]: entry for entry in source["audit"]}
    for entry in imported:
        was = original[entry["imported"]["sequence"]]
        assert all(entry.get(name) == was.get(name) for name in copy_data.ENTRY_KEYS[1:])
    assert any(entry.get("by") == STAFF and entry.get("changes") for entry in imported)
    attestation = target["audit"][-1]
    assert (attestation["orderId"], attestation["actor"], attestation["action"]) == (
        copy_data.COPY_ID, copy_data.COPY_ACTOR, copy_data.COPY_ACTION)
    assert source["audit"][-1]["hash"][:16] in attestation["detail"]


def test_a_second_run_copies_nothing_and_changes_nothing():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    first = copy_data.merge_state(target, batch)
    settled = json.dumps(target, sort_keys=True)

    second = copy_data.merge_state(target, batch)

    assert json.dumps(target, sort_keys=True) == settled
    assert not any(second[key]["copied"] for key in copy_data.TABLES)
    assert all(second[key]["existing"] == first[key]["copied"] for key in copy_data.TABLES)


def test_a_later_run_adds_only_what_has_settled_since():
    source = source_state()
    left = held_ids(source)
    target = initial_state()
    copy_data.merge_state(target, stored(copy_data.export_state(source)))
    earlier = deepcopy(target["audit"])

    tick(source, order_for(source, "partner-working-001")["id"])
    for _ in range(4):
        follow_up(source, interval_seconds=0)
    report = copy_data.merge_state(target, stored(copy_data.export_state(source)))

    assert report["heldBack"] == 0 and left <= set(target["orders"])
    assert (report["orders"]["copied"], report["orders"]["existing"]) == (2, 7)
    assert report["concerns"]["copied"] == 1
    assert report["events"]["copied"] == sum(event["orderId"] in left for event in source["events"])
    assert target["audit"][:len(earlier)] == earlier and verify_audit(target)
    assert {entry["orderId"] for entry in target["audit"][len(earlier):] if "imported" in entry} == left


def test_enterprises_copied_earlier_are_recognised_and_still_get_their_history():
    source = source_state()
    target = initial_state()
    copy_data.merge_accounts(target, stored(copy_data.export_state(source))["customers"])

    report = copy_data.merge_state(target, stored(copy_data.export_state(source)))

    assert (report["customers"]["existing"], report["customers"]["copied"]) == (2, 0)
    registered = [entry for entry in target["audit"] if entry["action"] == "account.registered"]
    assert len(registered) == 2 and all("imported" in entry for entry in registered)
    assert verify_audit(target)


def test_messages_the_source_has_not_sent_are_not_sent_again():
    source = source_state()
    order = order_for(source, "staff-happy-0001")
    for index, changes in ((0, {"status": "scheduled-digest", "sendAfter": "2020-01-01T00:00:00+00:00"}),
                           (1, {"status": "sent", "sentAt": "2020-01-01T00:00:00+00:00", "deliveredExternally": True})):
        item = order["outbox"][index]
        for held in [item, *(other for other in source["outbox"] if other["id"] == item["id"])]:
            held.update(changes)
    target = initial_state()

    report = copy_data.merge_state(target, stored(copy_data.export_state(source)))

    everything = [item for held in target["orders"].values() for item in held["outbox"]] + target["outbox"]
    assert not any(item["status"] in copy_data.UNSENT for item in everything)
    assert all(item["copiedStatus"] in copy_data.UNSENT for item in everything
               if item["status"] == copy_data.NOT_SENT)
    assert report["unsent"] == sum(item["status"] == copy_data.NOT_SENT
                                   for held in target["orders"].values() for item in held["outbox"]) > 0
    digest, delivered = target["orders"][order["id"]]["outbox"][:2]
    assert (digest["status"], digest["copiedStatus"]) == (copy_data.NOT_SENT, "scheduled-digest")
    assert delivered["status"] == "sent" and "copiedStatus" not in delivered
    assert {item["status"] for item in target["outbox"] if item["id"] == digest["id"]} == {copy_data.NOT_SENT}

    flush_digests(target, at="2100-01-01T00:00:00+00:00")
    sent = []
    assert deliver_outbox(Memory(target), sender=lambda contact, held, item: sent.append(item["id"])) == 0
    assert sent == [] and not any(item["status"] in copy_data.UNSENT for item in target["outbox"])


def test_copied_orders_stay_still_until_someone_acts_and_then_carry_on():
    target = initial_state()
    copy_data.merge_state(target, stored(copy_data.export_state(source_state())))
    before = json.dumps(target, sort_keys=True)
    for order_id in target["orders"]:
        process_next(target, order_id)
    assert json.dumps(target, sort_keys=True) == before

    waiting = order_for(target, "staff-decide-0001")
    assert waiting["state"] == "pendingApproval"
    approve_exception(target, waiting["id"], Actor("service-manager", STAFF), "approve")
    tick(target, waiting["id"])

    finished = target["orders"][waiting["id"]]
    assert finished["state"] == "completed" and finished["reserved"] is True
    assert verify_audit(target)
    decision = next(entry for entry in target["audit"] if entry["action"] == "exception.approve")
    assert "imported" not in decision and decision["by"] == STAFF


def test_capacity_follows_the_copied_orders_and_never_goes_below_zero():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    for cell in target["inventory"]:
        cell["availableUnits"] = 1

    report = copy_data.merge_state(target, batch)

    cells = {(order["city"], order["product"]) for order in batch["orders"] if order["reserved"]}
    assert len(cells) == 2 and report["capacity"] == 2
    assert min(cell["availableUnits"] for cell in target["inventory"]) == 0


def test_an_enterprise_whose_address_another_account_holds_is_left_alone_with_its_orders():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    _, code = auth.new_code("verify", "ayse@acme.example")
    stranger = accounts.register(target, {"email": "ayse@acme.example", "name": "Someone", "company": "Other AS"},
                                 auth.hash_password(PASSWORD), code)

    report = copy_data.merge_state(target, batch)

    assert (report["customers"]["conflict"], report["customers"]["copied"]) == (1, 1)
    assert target["customers"][stranger["id"]]["company"] == "Other AS"
    assert not any(order.get("ownerId") for order in target["orders"].values())
    assert (report["orders"]["orphan"], report["idempotency"]["orphan"]) == (2, 2)
    assert all(event["orderId"] in target["orders"] for event in target["events"]) and verify_audit(target)


def test_a_partner_whose_name_is_taken_is_left_alone_with_its_contacts_and_orders():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    own, _ = partners.create_partner(target, {"name": "BALKAN wholesale", "paymentTermsDays": 14,
                                              "contactName": "Someone", "email": "someone@elsewhere.example"},
                                     auth.new_invite()[1])

    report = copy_data.merge_state(target, batch)

    assert (report["partners"]["conflict"], report["partners"]["copied"]) == (1, 0)
    assert (report["partnerUsers"]["orphan"], report["partnerUsers"]["copied"]) == (2, 0)
    assert (report["orders"]["orphan"], report["concerns"]["orphan"]) == (1, 1)
    assert set(target["partners"]) == {own["id"]}
    assert not any(order.get("partnerId") for order in target["orders"].values())


def test_a_partner_contact_whose_address_an_enterprise_holds_is_left_out():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    _, code = auth.new_code("verify", "kaan@balkan.example")
    accounts.register(target, {"email": "kaan@balkan.example", "name": "Kaan", "company": "Kaan AS"},
                      auth.hash_password(PASSWORD), code)

    report = copy_data.merge_state(target, batch)

    assert (report["partnerUsers"]["conflict"], report["partnerUsers"]["copied"]) == (1, 1)
    assert [item["email"] for item in target["partnerUsers"].values()] == ["mira@balkan.example"]


def test_the_copied_partner_never_takes_the_api_client_role_from_the_targets_own():
    batch = stored(copy_data.export_state(source_state()))
    target = initial_state()
    partners.create_partner(target, {"name": "Own Partner", "paymentTermsDays": 14, "apiClient": True,
                                     "contactName": "Own Contact", "email": "own@elsewhere.example"},
                            auth.new_invite()[1])

    copy_data.merge_state(target, batch)

    assert len(target["partners"]) == 2
    assert [item["name"] for item in target["partners"].values() if item["apiClient"]] == ["Own Partner"]


@pytest.mark.parametrize("table, spoil", [
    ("customers", lambda record: {**record, "passwordHash": ""}),
    ("partners", lambda record: {**record, "name": " "}),
    ("partnerUsers", lambda record: {**record, "email": "not-an-address"}),
    ("orders", lambda record: {**record, "state": "inProgress"}),
    ("orders", lambda record: {**record, "city": "Atlantis"}),
    ("orders", lambda record: {**record, "timeline": None}),
    ("concerns", lambda record: {**record, "createdByName": ""}),
    ("events", lambda record: {**record, "type": ""}),
])
def test_a_spoiled_record_is_skipped_and_counted(table, spoil):
    batch = stored(copy_data.export_state(source_state()))
    batch[table][0] = spoil(batch[table][0])

    report = copy_data.merge_state(initial_state(), batch)

    assert report[table]["invalid"] == 1


def test_an_order_with_no_contact_on_it_is_copied_as_having_none():
    batch = stored(copy_data.export_state(source_state()))
    older = batch["orders"][0]
    del older["contact"]
    target = initial_state()

    report = copy_data.merge_state(target, batch)

    assert (report["orders"]["invalid"], report["orders"]["copied"]) == (0, 7)
    copied = target["orders"][older["id"]]
    assert copied["contact"] is None
    assert customer_view(copied)["contact"] is None and public_order(copied)["contact"] is None


def test_malformed_audit_entries_and_idempotency_keys_are_skipped():
    batch = stored(copy_data.export_state(source_state()))
    batch["audit"]["entries"][0] = {"sequence": "1", "at": "2026-01-01T00:00:00+00:00"}
    batch["idempotency"][next(iter(batch["idempotency"]))] = {"orderId": "AMA-0000000000"}
    target = initial_state()

    report = copy_data.merge_state(target, batch)

    assert (report["audit"]["invalid"], report["idempotency"]["invalid"]) == (1, 1)
    assert verify_audit(target)


def test_a_full_target_takes_what_fits_and_stays_consistent(monkeypatch):
    batch = stored(copy_data.export_state(source_state()))
    monkeypatch.setenv("AMA_MAX_ORDERS", "3")
    monkeypatch.setenv("AMA_MAX_CUSTOMERS", "1")
    target = initial_state()

    report = copy_data.merge_state(target, batch)

    assert (report["customers"]["copied"], report["customers"]["full"]) == (1, 1)
    assert (report["orders"]["copied"], report["orders"]["full"]) == (3, 4)
    assert len(target["orders"]) == 3
    assert all(event["orderId"] in target["orders"] for event in target["events"])
    assert all(item["orderId"] in target["orders"] for item in target["idempotency"].values())
    assert verify_audit(target)


def test_a_chain_that_does_not_verify_is_not_copied():
    source = source_state()
    source["audit"][3]["detail"] = "edited afterwards"
    batch = stored(copy_data.export_state(source))
    target = initial_state()

    report = copy_data.merge_state(target, batch)

    assert batch["audit"]["intact"] is False and batch["audit"]["entries"] == []
    assert report["audit"]["copied"] == 0 and report["auditIntact"] is False
    assert report["orders"]["copied"] == 7
    assert verify_audit(target) and "did not verify" in target["audit"][-1]["detail"]


def test_a_damaged_batch_is_refused():
    text = copy_data.pack(copy_data.export_state(source_state()))

    assert len(copy_data.unpack(text)["orders"]) == 7
    with pytest.raises(ValueError, match="damaged"):
        copy_data.unpack(text[:-1] + ("A" if text[-1] != "A" else "B"))


def test_export_prints_a_batch_the_wrapper_can_reassemble(tmp_path, monkeypatch, capsys):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source_state()), encoding="utf-8")
    point_store_at(monkeypatch, path)

    copy_data.main(["export"])

    out = capsys.readouterr().out
    lines = out.splitlines()
    body = lines[lines.index("BEGIN 1") + 1:lines.index("END 1")]
    packed = "".join(body)
    summary = results_of(out)[0]
    assert all(len(line) <= copy_data.LINE and re.fullmatch(r"[A-Za-z0-9_=.-]+", line) for line in body)
    assert len(packed) == summary["size"]
    assert (summary["orders"], summary["heldBack"], summary["auditIntact"]) == (7, 2, True)
    assert len(copy_data.unpack(packed)["orders"]) == 7


def test_a_dry_run_predicts_the_import_and_the_output_shows_counts_only(tmp_path, monkeypatch, capsys):
    target = tmp_path / "target.json"
    point_store_at(monkeypatch, target)
    batch = copy_data.pack(copy_data.export_state(source_state()))

    copy_data.main(["import", batch, "--dry-run"])
    assert not target.exists()
    copy_data.main(["import", batch])
    copy_data.main(["import", batch])
    copy_data.main(["summary"])

    out = capsys.readouterr().out
    dry, real, again, summary = results_of(out)
    assert (dry.pop("dryRun"), real.pop("dryRun"), again.pop("dryRun")) == (True, False, False)
    assert dry == real and real["orders"]["copied"] == 7
    assert (again["orders"]["copied"], again["orders"]["existing"]) == (0, 7)
    assert (summary["orders"], summary["partners"], summary["partnerUsers"], summary["concerns"]) == (7, 1, 2, 1)
    assert summary["auditIntact"] is True and summary["notSent"] == real["unsent"]
    assert summary["picker"] == {"listed": 2, "selectable": 1}
    assert "@" not in out and "scrypt" not in out


def test_a_batch_file_is_kept_after_a_dry_run_or_a_failure_and_removed_after_an_import(tmp_path, monkeypatch, capsys):
    point_store_at(monkeypatch, tmp_path / "target.json")
    staged = tmp_path / "batch"
    text = copy_data.pack(copy_data.export_state(source_state()))
    staged.write_text(text, encoding="ascii")

    copy_data.main(["import", "@" + str(staged), "--dry-run"])
    assert staged.exists()

    staged.write_text(text[:-1] + ("A" if text[-1] != "A" else "B"), encoding="ascii")
    with pytest.raises(ValueError):
        copy_data.main(["import", "@" + str(staged)])
    assert staged.exists()

    staged.write_text(text, encoding="ascii")
    copy_data.main(["import", "@" + str(staged)])
    assert not staged.exists()
    assert results_of(capsys.readouterr().out)[-1]["orders"]["copied"] == 7
