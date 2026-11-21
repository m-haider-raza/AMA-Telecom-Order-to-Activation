import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import approvals, auth, delivery
from app.domain import tracking_token
from app.main import app
from tests.test_accounts import ADMIN, PARTNER, new_client, order_body, register, sent_code, tick
from tests.test_accounts import live as accounts_live  # noqa: F401  (the shared live-mode fixture)
from tests.test_sso import (MAIL_FIELDS, PEOPLE, PUBLIC, approval_token, enroll, person_of, place_for, sign_in,
                            staff_order, tab)
from tests.test_sso import sso as sso_site  # noqa: F401  (the single sign-on fixture)

OPERATOR = PEOPLE["ServiceOperator"]
WAITING = "awaitingEnterpriseApproval"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


@pytest.fixture
def desk(sso_site):  # noqa: F811
    """The single sign-on app with one person at each level signed in (`desk.operator`, `desk.manager`,
    `desk.auditor` are their request headers) and one verified enterprise, Acme, in its own browser."""
    sso_site.operator = tab(sign_in(sso_site, "ServiceOperator"))
    sso_site.manager = tab(sign_in(sso_site, "ServiceManager"))
    sso_site.auditor = tab(sign_in(sso_site, "ServiceAuditor"))
    sso_site.browser, sso_site.acme = enroll(sso_site)
    return sso_site


def ask(desk, key, headers=None, enterprise=None, **changes):
    return place_for(desk, headers or desk.operator, enterprise or desk.acme["id"], key, **changes)


def asked(desk, key, **kwargs):
    reply = ask(desk, key, **kwargs)
    assert reply.status_code == 201, reply.text
    return reply.json()


def stored(order_id):
    return app.state.store.read()["orders"][order_id]


def events_of(order_id):
    return [event for event in app.state.store.read()["events"] if event["orderId"] == order_id]


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def age(order_id, **fields):
    """Make an approval request older than it is, so a test need not wait for a minute or a week."""
    app.state.store.transact(lambda state: state["orders"][order_id]["enterpriseApproval"].update(fields))


def check(desk, token, **kwargs):
    return desk.post("/api/approvals/check", json={"token": token}, **kwargs)


def decide(desk, token, decision="approve", **kwargs):
    return desk.post("/api/approvals/decide", json={"token": token, "decision": decision}, **kwargs)


def resend(desk, order_id, headers=None):
    return desk.post(f"/api/orders/{order_id}/approval/resend", headers=headers or desk.operator)


def withdraw(desk, order_id, headers=None):
    return desk.post(f"/api/orders/{order_id}/approval/withdraw", headers=headers or desk.operator)


def history(desk, order_id):
    """The order's audit trail, oldest first, once the hash chain over the whole trail is known to verify."""
    reply = desk.get("/api/audit", params={"order": order_id}, headers=desk.manager)
    assert reply.status_code == 200, reply.text
    assert reply.json()["verified"] is True
    return list(reversed(reply.json()["entries"]))


def test_the_service_team_sees_every_enterprise_with_only_what_it_needs_to_pick_one(desk):
    enroll(desk, "bora@beta.example", company="Beta Telekom AS", name="Bora Beta")
    enroll(desk, "zeynep@zeta.example", verified=False, company="Zeta Lojistik AS", name="Zeynep Zeta")

    listing = desk.get("/api/enterprises", headers=desk.operator)
    assert listing.status_code == 200, listing.text
    rows = listing.json()["enterprises"]
    assert [row["company"] for row in rows] == ["Acme Lojistik AS", "Beta Telekom AS", "Zeta Lojistik AS"]
    assert [row["verified"] for row in rows] == [True, True, False]
    assert rows[0] == {"id": desk.acme["id"], "company": "Acme Lojistik AS", "contactName": "Ayse Yilmaz",
                       "email": "a***@acme.example", "language": "en", "verified": True, "waiting": 0}
    assert rows[1]["email"] == "b***@beta.example"
    assert all(set(row) == set(rows[0]) for row in rows)
    for private in ("ayse@acme.example", "bora@beta.example", "zeynep@zeta.example", "passwordHash"):
        assert private not in listing.text

    asked(desk, "directory-key-0001")
    rows = desk.get("/api/enterprises", headers=desk.manager).json()["enterprises"]
    assert [row["waiting"] for row in rows] == [1, 0, 0]

    assert desk.get("/api/enterprises", headers=desk.auditor).status_code == 403
    assert new_client().get("/api/enterprises").status_code == 401
    assert desk.browser.get("/api/enterprises").status_code == 403
    assert new_client().get("/api/enterprises", headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 403


def test_an_order_placed_for_an_enterprise_waits_for_its_contact_and_nothing_starts(desk):
    reply = ask(desk, "placing-key-0001")
    assert reply.status_code == 201, reply.text
    placed = reply.json()
    order_id = placed["id"]
    assert placed["state"] == WAITING and "ownerId" not in placed
    record = placed["enterpriseApproval"]
    assert (record["status"], record["version"], record["sends"]) == ("pending", 1, 1)
    assert record["requestedBy"] == {"name": "Ömer Operatör", "upn": OPERATOR["upn"], "role": "ServiceOperator"}
    assert record["delivery"]["status"] == "pending"  # the reply is built before the e-mail goes out
    assert stored(order_id)["enterpriseApproval"]["delivery"]["status"] == "sent"

    held = json.dumps(stored(order_id), sort_keys=True)
    for _ in range(5):
        tick(order_id)
    assert json.dumps(stored(order_id), sort_keys=True) == held and events_of(order_id) == []

    assert len(desk.approvals) == 1
    mail = desk.approvals[0]
    assert (mail["email"], mail["name"], mail["company"]) == ("ayse@acme.example", "Ayse Yilmaz", "Acme Lojistik AS")
    assert (mail["order_id"], mail["requester"]) == (order_id, "Ömer Operatör")
    assert (mail["product"], mail["city"], mail["language"]) == ("Managed SD-WAN (200 Mbps)", "Istanbul", "en")
    assert mail["link"].startswith(f"{PUBLIC}/approve#token=")
    expires = datetime.strptime(mail["expires"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    assert timedelta(days=6, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=7)

    mine = desk.browser.get("/api/me/orders")
    assert mine.status_code == 200, mine.text
    [seen] = mine.json()["orders"]
    assert seen["id"] == order_id and seen["state"] == WAITING and "enterpriseApproval" not in seen
    assert seen["approval"]["status"] == "pending" and seen["approval"]["requestedBy"] == "Ömer Operatör"
    assert seen["approval"]["expired"] is False
    tracked = desk.get(f"/api/portal/orders/{order_id}", headers={"X-Tracking-Token": tracking_token(order_id)})
    assert tracked.status_code == 200 and tracked.json()["approval"]["status"] == "pending"
    for text in (mine.text, tracked.text):
        assert OPERATOR["upn"] not in text and OPERATOR["oid"] not in text

    trail = history(desk, order_id)
    assert [entry["action"] for entry in trail] == ["order.accepted", "order.approval-requested"]
    assert all(entry["by"] == person_of("ServiceOperator") for entry in trail)
    assert "Ayse Yilmaz" in trail[1]["detail"]


def test_the_contact_approves_from_the_link_and_only_then_do_the_agents_start(desk):
    placed = asked(desk, "approve-key-0001")
    order_id = placed["id"]
    token = approval_token(desk, order_id)

    seen = check(desk, token)
    assert seen.status_code == 200, seen.text
    assert seen.json() | {"requestedAt": None, "expiresAt": None} == {
        "status": "pending", "orderId": order_id, "company": "Acme Lojistik AS", "contactName": "Ayse Yilmaz",
        "product": "Managed SD-WAN (200 Mbps)", "city": "Istanbul", "language": "en", "requestedBy": "Ömer Operatör",
        "requestedAt": None, "expiresAt": None}
    for private in ("ayse@acme.example", "ownerId", OPERATOR["upn"], OPERATOR["oid"]):
        assert private not in seen.text
    assert stored(order_id)["state"] == WAITING and events_of(order_id) == []

    app.state.store.transact(lambda state: state["orders"][order_id].update(createdAt=ago(hours=1)))
    approved = decide(desk, token)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    for private in ("ayse@acme.example", OPERATOR["upn"], OPERATOR["oid"]):
        assert private not in approved.text
    order = stored(order_id)
    assert (order["state"], order["stage"]) == ("acknowledged", "validation")
    assert order["approvedAt"] and order["enterpriseApproval"]["via"] == "link"
    assert [(event["type"], event["status"]) for event in events_of(order_id)] == [("order.received", "queued")]
    assert any(item["en"].startswith("Thank you") for item in order["notifications"])

    entry = next(item for item in history(desk, order_id) if item["action"] == "order.enterprise-approved")
    assert entry["actor"] == "enterprise-contact" and "by" not in entry
    assert "from the link in the e-mail" in entry["detail"]

    for _ in range(12):
        tick(order_id)
    done = stored(order_id)
    assert done["state"] == "completed", done["state"]
    assert done["elapsedSeconds"] < 600  # the clock starts at the approval, not while the contact was thinking

    assert decide(desk, token).status_code == 409
    assert decide(desk, token, "decline").status_code == 409
    assert check(desk, token).json()["status"] == "approved"
    assert stored(order_id)["state"] == "completed"


def test_a_declined_order_never_starts_and_stays_declined(desk):
    placed = asked(desk, "decline-key-0001")
    order_id = placed["id"]
    token = approval_token(desk, order_id)

    declined = decide(desk, token, "decline")
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == "declined"
    order = stored(order_id)
    assert (order["state"], order["stage"], order["enterpriseApproval"]["status"]) == ("rejected", "done", "declined")
    assert events_of(order_id) == []
    for _ in range(3):
        tick(order_id)
    assert stored(order_id)["state"] == "rejected"

    entry = history(desk, order_id)[-1]
    assert entry["action"] == "order.enterprise-declined" and entry["actor"] == "enterprise-contact"
    assert decide(desk, token).status_code == 409
    assert check(desk, token).json()["status"] == "declined"
    [seen] = desk.browser.get("/api/me/orders").json()["orders"]
    assert seen["state"] == "rejected" and seen["approval"]["status"] == "declined"


def test_a_link_that_is_not_genuine_is_refused_with_the_same_answer(desk):
    placed = asked(desk, "refusal-key-0001")
    real = approval_token(desk, placed["id"])
    soon = int(time.time()) + 3600
    forgeries = {
        "garbage": "x" * 64,
        "tampered": real[:-4] + ("AAAA" if not real.endswith("AAAA") else "BBBB"),
        "another purpose": auth.sign_blob("invite", {"o": placed["id"], "v": 1, "e": soon}),
        "unknown order": auth.sign_blob(approvals.PURPOSE, {"o": "AMA-0000000000", "v": 1, "e": soon}),
        "lapsed": auth.sign_blob(approvals.PURPOSE, {"o": placed["id"], "v": 1, "e": int(time.time()) - 5}),
    }
    for name, token in forgeries.items():
        for reply in (check(desk, token), decide(desk, token)):
            assert reply.status_code == 404, name
            assert reply.json()["detail"] == approvals.BAD_LINK, name
    assert check(desk, "short").status_code in (400, 422)
    assert desk.get("/api/approvals/check").status_code == 405
    assert desk.get("/api/approvals/decide").status_code == 405
    assert stored(placed["id"])["state"] == WAITING
    assert check(desk, real).json()["status"] == "pending"


def test_the_public_answer_routes_need_the_browser_header_like_every_other_write(desk):
    placed = asked(desk, "csrf-key-0001")
    token = approval_token(desk, placed["id"])
    bare = TestClient(app)
    assert bare.post("/api/approvals/check", json={"token": token}).status_code == 403
    assert bare.post("/api/approvals/decide", json={"token": token, "decision": "approve"}).status_code == 403
    assert stored(placed["id"])["state"] == WAITING


def test_guessing_links_is_throttled_per_address_and_real_links_never_are(desk):
    placed = asked(desk, "throttle-key-0001")
    token = approval_token(desk, placed["id"])
    guesser = {"X-Forwarded-For": "203.0.113.9, 198.51.100.7"}
    for _ in range(30):
        assert check(desk, "g" * 64, headers=guesser).status_code == 404
    blocked = check(desk, token, headers=guesser)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert decide(desk, token, headers=guesser).status_code == 429
    assert check(desk, token, headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.8"}).status_code == 200
    for _ in range(40):
        assert check(desk, token).status_code == 200
    assert stored(placed["id"])["state"] == WAITING


def test_an_expired_request_cannot_be_answered_until_the_service_team_sends_it_again(desk):
    order_id = asked(desk, "expired-key-0001")["id"]
    token = approval_token(desk, order_id)
    age(order_id, expiresAt=ago(hours=1))

    seen = check(desk, token)
    assert seen.status_code == 200 and seen.json()["status"] == "expired"
    refused = decide(desk, token)
    assert refused.status_code == 409 and "expired" in refused.json()["detail"]
    in_account = desk.browser.post(f"/api/me/orders/{order_id}/approval", json={"decision": "approve"})
    assert in_account.status_code == 409 and "expired" in in_account.json()["detail"]
    assert stored(order_id)["state"] == WAITING and events_of(order_id) == []
    approval = desk.browser.get("/api/me/orders").json()["orders"][0]["approval"]
    assert approval["status"] == "pending" and approval["expired"] is True

    age(order_id, lastRequestedAt=ago(minutes=5))
    assert resend(desk, order_id).status_code == 200
    renewed = approval_token(desk, order_id)
    assert check(desk, renewed).json()["status"] == "pending"
    assert decide(desk, renewed).status_code == 200
    assert stored(order_id)["state"] == "acknowledged"


def test_the_enterprise_can_answer_from_its_own_account_but_nobody_else_can(desk):
    order_id = asked(desk, "account-key-0001")["id"]
    other, _ = enroll(desk, "bora@beta.example", company="Beta Telekom AS", name="Bora Beta")
    path = f"/api/me/orders/{order_id}/approval"
    approve = {"json": {"decision": "approve"}}

    assert other.post(path, **approve).status_code == 404
    assert desk.post(path, headers=desk.manager, **approve).status_code == 403
    assert new_client().post(path, **approve).status_code == 401
    assert new_client().post(f"/api/orders/{order_id}/approval/resend").status_code == 401
    assert new_client().post(f"/api/orders/{order_id}/approval/withdraw").status_code == 401
    assert desk.browser.post(f"/api/orders/{order_id}/approval/resend").status_code == 403
    assert desk.browser.post(f"/api/orders/{order_id}/approval/withdraw").status_code == 403
    assert desk.browser.post(path, json={"decision": "maybe"}).status_code == 422
    assert stored(order_id)["state"] == WAITING

    reply = desk.browser.post(path, **approve)
    assert reply.status_code == 200, reply.text
    assert reply.json()["state"] == "acknowledged" and reply.json()["approval"]["status"] == "approved"
    for private in (OPERATOR["upn"], OPERATOR["oid"]):
        assert private not in reply.text
    assert stored(order_id)["enterpriseApproval"]["via"] == "account"
    entry = next(item for item in history(desk, order_id) if item["action"] == "order.enterprise-approved")
    assert "from their account" in entry["detail"] and "by" not in entry
    assert decide(desk, approval_token(desk, order_id)).status_code == 409
    assert desk.browser.post(path, **approve).status_code == 409


def test_sending_the_request_again_retires_the_earlier_link_and_is_limited(desk):
    order_id = asked(desk, "resend-key-0001")["id"]
    first = approval_token(desk, order_id)

    soon = resend(desk, order_id)
    assert soon.status_code == 409 and "Wait a minute" in soon.json()["detail"]
    assert len(desk.approvals) == 1
    assert resend(desk, order_id, headers=desk.auditor).status_code == 403

    age(order_id, lastRequestedAt=ago(minutes=5))
    again = resend(desk, order_id)
    assert again.status_code == 200, again.text
    record = stored(order_id)["enterpriseApproval"]
    assert (record["version"], record["sends"], record["status"]) == (2, 2, "pending")
    assert record["delivery"]["status"] == "sent" and len(desk.approvals) == 2
    second = approval_token(desk, order_id)
    assert second != first
    stale = check(desk, first)
    assert stale.status_code == 200 and stale.json()["status"] == "replaced"
    assert stale.json()["message"] == approvals.REPLACED
    refused = decide(desk, first)
    assert refused.status_code == 409 and refused.json()["detail"] == approvals.REPLACED
    assert check(desk, second).json()["status"] == "pending"

    for sends in (3, 4, 5):
        age(order_id, lastRequestedAt=ago(minutes=5))
        assert resend(desk, order_id).status_code == 200
        assert stored(order_id)["enterpriseApproval"]["sends"] == sends
    age(order_id, lastRequestedAt=ago(minutes=5))
    capped = resend(desk, order_id)
    assert capped.status_code == 409 and "5 times" in capped.json()["detail"]
    assert len(desk.approvals) == 5 and stored(order_id)["enterpriseApproval"]["version"] == 5

    resent = [item for item in history(desk, order_id) if item["action"] == "order.approval-resent"]
    assert len(resent) == 4 and all(item["by"] == person_of("ServiceOperator") for item in resent)


def test_withdrawing_an_order_closes_it_and_kills_the_link(desk):
    order_id = asked(desk, "withdraw-key-0001")["id"]
    token = approval_token(desk, order_id)

    assert withdraw(desk, order_id, headers=desk.auditor).status_code == 403
    assert withdraw(desk, "AMA-NOSUCHORDER").status_code == 404
    gone = withdraw(desk, order_id)
    assert gone.status_code == 200, gone.text
    order = stored(order_id)
    assert (order["state"], order["stage"], order["enterpriseApproval"]["status"]) == ("rejected", "done", "withdrawn")
    assert events_of(order_id) == []
    assert check(desk, token).json()["status"] == "withdrawn"
    assert decide(desk, token).status_code == 409
    entry = history(desk, order_id)[-1]
    assert entry["action"] == "order.approval-withdrawn" and entry["by"] == person_of("ServiceOperator")
    assert withdraw(desk, order_id).status_code == 409
    assert resend(desk, order_id).status_code == 409

    started = staff_order(desk, desk.operator, desk.acme["id"], "withdraw-key-0002")
    assert withdraw(desk, started["id"]).status_code == 409
    assert resend(desk, started["id"]).status_code == 409


def test_placing_the_same_request_twice_sends_one_email(desk):
    first = ask(desk, "replay-key-0001")
    assert first.status_code == 201, first.text
    replay = ask(desk, "replay-key-0001")
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    clash = ask(desk, "replay-key-0001", city="Ankara")
    assert clash.status_code == 409
    assert len(desk.approvals) == 1 and len(app.state.store.read()["orders"]) == 1


def test_an_enterprise_can_have_only_five_orders_waiting_for_its_contact(desk):
    waiting = [asked(desk, f"capacity-key-{number:04d}")["id"] for number in range(5)]
    sixth = ask(desk, "capacity-key-0005")
    assert sixth.status_code == 409 and "5 orders waiting" in sixth.json()["detail"]
    assert len(app.state.store.read()["orders"]) == 5 and len(desk.approvals) == 5

    assert decide(desk, approval_token(desk, waiting[0]), "decline").status_code == 200
    freed = ask(desk, "capacity-key-0005")
    assert freed.status_code == 201, freed.text
    assert len(desk.approvals) == 6


def test_only_a_verified_enterprise_can_be_asked_and_only_one_that_exists(desk):
    _, unverified = enroll(desk, "zeynep@zeta.example", verified=False, company="Zeta Lojistik AS", name="Zeynep Zeta")
    refused = ask(desk, "verify-key-0001", enterprise=unverified["id"])
    assert refused.status_code == 409 and "has not verified" in refused.json()["detail"]
    missing = ask(desk, "verify-key-0002", enterprise="CUS-0000000000")
    assert missing.status_code == 404
    assert desk.approvals == [] and app.state.store.read()["orders"] == {}
    assert ask(desk, "verify-key-0003", headers=desk.auditor).status_code == 403
    assert ask(desk, "short").status_code == 422
    assert ask(desk, "verify-key-0004", city="Bursa").status_code == 422
    assert ask(desk, "verify-key-0005", customer="Someone Else AS").status_code == 422


def test_no_approval_link_leaves_the_server_except_in_the_email(desk):
    pending = asked(desk, "leak-key-0001")
    started = staff_order(desk, desk.operator, desk.acme["id"], "leak-key-0002")
    tokens = [approval_token(desk, order["id"]) for order in (pending, started)]
    base = "/tmf-api/productOrderingManagement/v4/productOrder"
    paths = ["/api/state", "/api/audit", "/api/audit.csv", "/api/analytics", "/api/enterprises", base,
             f"{base}/{pending['id']}", "/tmf-api/serviceOrdering/v4/serviceOrder",
             "/tmf-api/resourceOrderManagement/v4/resourceOrder"]
    for path in paths:
        reply = desk.get(path, headers=desk.manager)
        assert reply.status_code == 200, path
        for secret in (*tokens, "#token=", "ayse@acme.example"):
            assert secret not in reply.text, (path, secret)

    mine = desk.browser.get("/api/me/orders")
    raw = Path(os.environ["LOCAL_STATE_PATH"]).read_text(encoding="utf-8")
    for text in (mine.text, raw):
        for secret in (*tokens, "#token="):
            assert secret not in text
    for order in (pending, started):
        kept = stored(order["id"])
        assert kept["outbox"] == [] and not any(item["deliveredExternally"] for item in kept["notifications"])
    assert not app.state.store.read().get("outbox")


def test_service_staff_cannot_skip_the_approval_through_the_generic_order_apis(desk):
    for path, key in (("/api/orders", "bypass-order-0001"),
                      ("/tmf-api/productOrderingManagement/v4/productOrder", "bypass-order-0002")):
        refused = desk.post(path, json=order_body(key), headers=desk.operator)
        assert refused.status_code == 409, path
        assert "/api/enterprises/{id}/orders" in refused.json()["detail"]
    assert app.state.store.read()["orders"] == {}


def test_orders_customers_and_partners_place_themselves_are_not_held_for_approval(desk):
    own = desk.browser.post("/api/me/orders", json=order_body("customer-order-0001"))
    assert own.status_code == 201, own.text
    assert own.json()["state"] == "acknowledged" and own.json().get("approval") is None
    channel = desk.post("/api/orders", json=order_body("channel-order-0001"),
                        headers={"Authorization": f"Bearer {PARTNER}"})
    assert channel.status_code == 201, channel.text
    assert channel.json()["state"] == "acknowledged" and "enterpriseApproval" not in channel.json()
    assert desk.get("/api/enterprises", headers=desk.operator).json()["enterprises"][0]["waiting"] == 0


def test_orders_the_enterprise_turned_away_are_not_counted_as_pipeline_fallout(desk):
    declined = asked(desk, "analytics-key-0001")
    withdrawn = asked(desk, "analytics-key-0002")
    assert decide(desk, approval_token(desk, declined["id"]), "decline").status_code == 200
    assert withdraw(desk, withdrawn["id"]).status_code == 200
    ran = staff_order(desk, desk.operator, desk.acme["id"], "analytics-key-0003")
    for _ in range(12):
        tick(ran["id"])
    assert stored(ran["id"])["state"] == "completed"

    report = desk.get("/api/analytics", params={"scope": "live"}, headers=desk.manager).json()
    assert report["sample"]["orders"] == 1 and report["sample"]["completed"] == 1
    assert report["fallout"]["falloutOrders"] == 0 and report["fallout"]["decided"] == 1


def test_in_token_mode_the_admin_token_places_orders_for_enterprises_the_same_way(accounts_live, monkeypatch):  # noqa: F811
    live, mails = accounts_live, []
    monkeypatch.delenv("AMA_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setattr("app.approvals.send_approval_email", lambda *args: mails.append(dict(zip(MAIL_FIELDS, args))))
    admin = {"Authorization": f"Bearer {ADMIN}"}
    browser = new_client()
    assert register(browser).status_code == 201
    assert browser.post("/api/auth/verify", json={"code": sent_code(live, "verify")}).status_code == 200

    [row] = live.get("/api/enterprises", headers=admin).json()["enterprises"]
    assert row["company"] == "Acme Lojistik AS" and row["verified"] is True
    reply = live.post(f"/api/enterprises/{row['id']}/orders", headers=admin, json={
        "city": "Ankara", "product": "connectivity", "scenario": "happy", "idempotencyKey": "token-mode-0001"})
    assert reply.status_code == 201, reply.text
    placed = reply.json()
    assert placed["state"] == WAITING
    assert placed["enterpriseApproval"]["requestedBy"] == {"name": None, "upn": None, "role": None}
    [mail] = mails
    assert (mail["email"], mail["requester"], mail["link"]) == ("ayse@acme.example", None, "")
    assert (mail["product"], mail["city"]) == ("Dedicated Internet Access (500 Mbps)", "Ankara")

    approved = browser.post(f"/api/me/orders/{placed['id']}/approval", json={"decision": "approve"})
    assert approved.status_code == 200 and approved.json()["state"] == "acknowledged"
    generic = live.post("/api/orders", json=order_body("token-mode-0002"), headers=admin)
    assert generic.status_code == 201 and generic.json()["state"] == "acknowledged"


APPROVAL = ("Ayse Yilmaz", "Acme Lojistik AS", "AMA-0123456789", "Ömer Operatör", "Managed SD-WAN (200 Mbps)",
            "Istanbul", "2026-12-01 10:30 UTC")
LINK = "https://tbt.example.invalid/approve#token=abc.def"


def halves(body):
    return body.split("\n\n---\n\n")


def test_the_approval_email_is_bilingual_names_who_asked_and_carries_the_link_once():
    subject, body = delivery.approval_message(*APPROVAL, LINK, "en")
    english, turkish = halves(body)
    assert subject == "Approval needed for your TürkBizTel order AMA-0123456789"
    assert english.startswith("Hello Ayse Yilmaz,")
    assert "Ömer Operatör from the TürkBizTel service team placed an order on behalf of Acme Lojistik AS" in english
    for line in ("Order: AMA-0123456789", "Service: Managed SD-WAN (200 Mbps)", "City: Istanbul"):
        assert line in english
    assert "until you approve" in english and "approve or decline it" in english
    assert "expires on 2026-12-01 10:30 UTC" in english
    assert turkish.startswith("Merhaba Ayse Yilmaz,") and "TürkBizTel hizmet ekibinden Ömer Operatör" in turkish
    assert body.count(LINK) == 1 and body.endswith(f"{LINK}\n")

    _, body = delivery.approval_message(*APPROVAL, LINK, "tr")
    first, second = halves(body)
    assert first.startswith("Merhaba") and second.startswith("Hello") and body.count(LINK) == 1


def test_without_a_named_requester_or_a_link_the_email_says_so_plainly():
    anonymous = (*APPROVAL[:3], None, *APPROVAL[4:])
    _, body = delivery.approval_message(*anonymous, "", "en")
    english, turkish = halves(body)
    assert "The TürkBizTel service team placed an order on behalf of Acme Lojistik AS" in english
    assert "TürkBizTel hizmet ekibi, Acme Lojistik AS adına" in turkish
    assert "Sign in to your TürkBizTel account to review it; the request expires on 2026-12-01 10:30 UTC" in english
    assert "hesabınızda oturum açın" in turkish
    assert "http" not in body and "link below" not in english


def test_the_approval_link_keeps_the_token_in_the_fragment_and_needs_a_public_origin(monkeypatch):
    monkeypatch.delenv("DEMO_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("AMA_PUBLIC_ORIGIN", "https://tbt.example.invalid/")
    assert delivery.approval_link("abc.def") == "https://tbt.example.invalid/approve#token=abc.def"
    monkeypatch.delenv("AMA_PUBLIC_ORIGIN")
    assert delivery.approval_link("abc.def") == ""


class Markup(HTMLParser):
    """The ids, scripts and inline code of a page, read without a browser."""

    def __init__(self):
        super().__init__()
        self.ids, self.scripts, self.inline = set(), [], []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if "id" in found:
            self.ids.add(found["id"])
        if tag == "script":
            (self.scripts if "src" in found else self.inline).append(found.get("src", "<script>"))
        self.inline += [f"<{tag} {name}>" for name in found if name == "style" or name.startswith("on")]


def markup(name):
    page = Markup()
    page.feed((STATIC / name).read_text(encoding="utf-8"))
    return page


def test_the_approval_page_opens_for_anyone_and_runs_no_inline_code(desk):
    page = markup("approve.html")
    assert page.inline == [] and page.scripts == ["/static/ui.js?v=tb1", "/static/approve.js?v=tb1"]
    for visitor in (TestClient(app), desk.browser):
        reply = visitor.get("/approve", follow_redirects=False)
        assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/html")
        assert reply.headers["cache-control"] == "no-cache"
        assert "script-src 'self'" in reply.headers["content-security-policy"]
        assert reply.content == (STATIC / "approve.html").read_bytes()
    assert "TürkBizTel" in reply.text


@pytest.mark.parametrize("script, page", [("app.js", "index.html"), ("approve.js", "approve.html")])
def test_every_element_a_script_looks_up_by_id_is_in_its_page(script, page):
    looked_up = set(re.findall(r'\$\("#([\w-]+)"\)', (STATIC / script).read_text(encoding="utf-8")))
    assert looked_up, "the script no longer looks elements up by id, so this check has nothing to compare"
    assert looked_up <= markup(page).ids, looked_up - markup(page).ids
