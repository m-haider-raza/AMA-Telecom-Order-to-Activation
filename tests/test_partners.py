from datetime import datetime, timedelta, timezone

import pytest

from app import auth, partners
from app.domain import SITES
from app.main import app
from tests.test_accounts import ADMIN, CONTACT, PARTNER, PASSWORD, new_client, order_body, register, sent_code, tick
from tests.test_accounts import live as accounts_live  # noqa: F401  (registers the shared live-mode fixture)

ADDRESS = "Levent Mah. 12, Besiktas, Istanbul"
WRONG_CITY = "Kizilay Mah. 5, Cankaya, Ankara"
TAB = {"X-Session-Mode": "tab"}


@pytest.fixture
def portal(accounts_live, monkeypatch):  # noqa: F811
    accounts_live.invites, accounts_live.replies = [], []
    monkeypatch.setattr("app.partners_api.send_invite_email", lambda *args: accounts_live.invites.append(args))
    monkeypatch.setattr("app.partners_api.send_concern_email", lambda *args: accounts_live.replies.append(args))
    return accounts_live


def staff():
    client = new_client()
    client.headers["Authorization"] = f"Bearer {ADMIN}"
    return client


def onboard(boss, name, email, **changes):
    """Staff create a partner; its first contact accepts the invitation. Returns the partner id and that contact."""
    created = boss.post("/api/partners", json={"name": name, "contactName": "Deniz Kaya", "email": email,
                                               "paymentTermsDays": 14, **changes})
    assert created.status_code == 201, created.text
    contact = new_client()
    accepted = contact.post("/api/auth/invite/accept", json={"token": created.json()["invite"]["token"],
                                                             "password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return created.json()["partner"]["id"], contact


def place(boss, partner_id, key, **changes):
    placed = boss.post(f"/api/partners/{partner_id}/orders", json=order_body(key, **changes))
    assert placed.status_code == 201, placed.text
    return placed.json()["id"]


def finish(order_id):
    for _ in range(12):
        tick(order_id)
    assert app.state.store.read()["orders"][order_id]["state"] == "completed"


def overview(contact):
    reply = contact.get("/api/partner/overview")
    assert reply.status_code == 200, reply.text
    return reply.json()


def delivery_of(contact, order_id):
    return next(item for item in overview(contact)["deliveries"] if item["id"] == order_id)


def edit_order(order_id, **changes):
    app.state.store.transact(lambda state: state["orders"][order_id].update(changes))


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def tab(token):
    """What a TürkBizTel page sends: its own tab's session token, with the shared cookie switched off."""
    return {**TAB, "X-Session": token}


def tab_login(client, email="ayse@acme.example", password=PASSWORD):
    reply = client.post("/api/auth/login", json={"email": email, "password": password}, headers=TAB)
    assert reply.status_code == 200, reply.text
    return reply.json()["session"]


def tab_staff(client, token=ADMIN):
    reply = client.post("/api/auth/staff", json={"token": token}, headers=TAB)
    assert reply.status_code == 200, reply.text
    return reply.json()["session"]


def test_an_invitation_is_single_use_and_opens_the_partner_portal(portal):
    boss = staff()
    created = boss.post("/api/partners", json={"name": "Aegean Wholesale", "contactName": "Deniz Kaya",
                                               "email": "Deniz@Aegean.example", "billingEmail": "",
                                               "paymentTermsDays": 14})
    assert created.status_code == 201
    invite = created.json()["invite"]
    assert invite["delivery"] == "manual" and invite["path"] == f"/login#invite={invite['token']}"
    assert "passwordHash" not in created.text and '"hash"' not in created.text
    assert boss.post("/api/partners", json={"name": "aegean wholesale", "contactName": "Other Person",
                                            "email": "other@aegean.example"}).status_code == 409

    guest = new_client()
    before = guest.post("/api/auth/login", json={"email": "deniz@aegean.example", "password": PASSWORD})
    assert before.status_code == 401
    assert guest.post("/api/auth/invite/check", json={"token": "x" * 43}).status_code == 422
    seen = guest.post("/api/auth/invite/check", json={"token": invite["token"]}).json()
    assert seen == {"name": "Deniz Kaya", "email": "deniz@aegean.example", "partner": "Aegean Wholesale"}
    assert guest.post("/api/auth/invite/accept", json={"token": invite["token"], "password": "short"}
                      ).status_code == 422
    accepted = guest.post("/api/auth/invite/accept", json={"token": invite["token"], "password": PASSWORD})
    assert accepted.status_code == 200 and accepted.json()["role"] == "partner"
    assert "httponly" in accepted.headers["set-cookie"].lower()
    me = guest.get("/api/auth/me").json()
    assert me["role"] == "partner" and me["account"]["company"] == "Aegean Wholesale"
    assert me["partner"]["paymentTermsDays"] == 14 and "payments" not in me["partner"]

    assert new_client().post("/api/auth/invite/accept",
                             json={"token": invite["token"], "password": PASSWORD}).status_code == 422
    again = new_client().post("/api/auth/login", json={"email": "deniz@aegean.example", "password": PASSWORD})
    assert again.status_code == 200 and again.json()["role"] == "partner"


def test_invitations_expire_are_reissued_and_can_be_emailed(portal, monkeypatch):
    monkeypatch.setenv("AMA_PUBLIC_ORIGIN", "https://turkbiztel.example")
    boss = staff()
    created = boss.post("/api/partners", json={"name": "Aegean Wholesale", "contactName": "Deniz Kaya",
                                               "email": "deniz@aegean.example", "language": "tr"}).json()
    assert created["invite"]["delivery"] == "email"
    email, name, organisation, link, language = portal.invites[-1]
    assert (email, name, organisation, language) == ("deniz@aegean.example", "Deniz Kaya", "Aegean Wholesale", "tr")
    assert link == f"https://turkbiztel.example/login#invite={created['invite']['token']}"

    users = app.state.store.read()["partnerUsers"]
    contact_id = next(iter(users))
    app.state.store.transact(lambda state: state["partnerUsers"][contact_id]["invite"].update(expires=1))
    stale = new_client().post("/api/auth/invite/accept", json={"token": created["invite"]["token"],
                                                               "password": PASSWORD})
    assert stale.status_code == 422
    fresh = boss.post(f"/api/partners/{created['partner']['id']}/users/{contact_id}/invite").json()["invite"]
    assert fresh["token"] != created["invite"]["token"] and len(portal.invites) == 2
    assert new_client().post("/api/auth/invite/accept", json={"token": created["invite"]["token"],
                                                              "password": PASSWORD}).status_code == 422
    assert new_client().post("/api/auth/invite/accept", json={"token": fresh["token"], "password": PASSWORD}
                             ).status_code == 200
    assert boss.post(f"/api/partners/{created['partner']['id']}/users/{contact_id}/invite").status_code == 409


def test_each_role_is_kept_out_of_the_others_apis(portal):
    boss = staff()
    partner_id, contact = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    customer = new_client()
    assert register(customer).status_code == 201
    anonymous = new_client()

    for path in ("/api/partners", "/api/state", "/api/audit.csv", "/api/me/orders"):
        assert contact.get(path).status_code == 403, path
    assert contact.post("/api/partners", json={"name": "Sneaky Wholesale", "contactName": "No One",
                                               "email": "no.one@sneaky.example"}).status_code == 403
    assert contact.post(f"/api/partners/{partner_id}/payments", json={"orderId": "AMA-0123456789"}).status_code == 403
    assert customer.get("/api/partners").status_code == 403
    assert customer.get("/api/partner/overview").status_code == 403
    assert customer.put("/api/partner/profile", json={"name": "Nobody", "language": "en"}).status_code == 403
    assert anonymous.get("/api/partner/overview").status_code == 401
    assert boss.get("/api/partner/overview").status_code == 401
    assert boss.get("/api/partners").status_code == 200 and "scrypt$" not in boss.get("/api/partners").text
    state = boss.get("/api/state")
    assert state.status_code == 200
    assert not {"partners", "partnerUsers", "concerns", "customers"} & set(state.json())
    assert "scrypt$" not in state.text and "deniz@aegean.example" not in state.text

    assert register(new_client(), email="DENIZ@aegean.example").status_code == 409
    taken = boss.post(f"/api/partners/{partner_id}/users", json={"name": "Ayse Yilmaz", "email": "ayse@acme.example"})
    assert taken.status_code == 409
    added = boss.post(f"/api/partners/{partner_id}/users", json={"name": "Ayse Yilmaz", "email": "ayse@aegean.example"})
    assert added.status_code == 201 and added.json()["invite"]["token"]
    assert register(new_client(), email="ayse@aegean.example").status_code == 409


def test_partners_see_only_their_own_deliveries_and_other_orders_look_missing(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    b_id, b = onboard(boss, "Bosphorus Carrier", "ece@bosphorus.example")
    mine = place(boss, a_id, "partner-a-order-1")
    theirs = place(boss, b_id, "partner-b-order-1", city="Izmir", siteAddress="Alsancak Mah. 3, Konak, Izmir")

    assert [item["id"] for item in overview(a)["deliveries"]] == [mine]
    assert [item["id"] for item in overview(b)["deliveries"]] == [theirs]
    assert a.get(f"/api/partner/deliveries/{theirs}").status_code == 404
    assert b.get(f"/api/partner/deliveries/{mine}").status_code == 404
    body = {"city": "Istanbul", "siteAddress": ADDRESS}
    assert b.put(f"/api/partner/deliveries/{mine}/delivery", json=body).status_code == 404
    assert b.post("/api/partner/concerns", json={"category": "delivery", "orderId": mine, "subject": "Where is it",
                                                 "text": "Nothing arrived"}).status_code == 404
    assert a.post("/api/partner/concerns", json={"category": "delivery", "subject": "Where is it", "text": "Hello",
                                                 "partnerId": b_id}).status_code == 422
    assert boss.post(f"/api/partners/{a_id}/orders", json=order_body(
        "partner-b-order-1", city="Izmir", siteAddress="Alsancak Mah. 3, Konak, Izmir")).status_code == 409

    token_order = boss.post("/api/orders", json=order_body("token-order-0001"),
                            headers={"Authorization": f"Bearer {PARTNER}"}).json()["id"]
    assert token_order not in [item["id"] for item in overview(a)["deliveries"] + overview(b)["deliveries"]]
    assert boss.put(f"/api/partners/{b_id}", json={"apiClient": True}).json()["apiClient"] is True
    assert token_order in [item["id"] for item in overview(b)["deliveries"]]
    assert boss.put(f"/api/partners/{a_id}", json={"apiClient": True}).status_code == 200
    assert token_order in [item["id"] for item in overview(a)["deliveries"]]
    assert token_order not in [item["id"] for item in overview(b)["deliveries"]]


def test_the_shared_partner_token_never_reaches_a_partner_organisations_orders_unless_it_is_the_api_client(portal):
    boss = staff()
    a_id, _ = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    b_id, _ = onboard(boss, "Bosphorus Carrier", "ece@bosphorus.example")
    channel = {"Authorization": f"Bearer {PARTNER}"}
    listing = "/tmf-api/productOrderingManagement/v4/productOrder"
    izmir = {"city": "Izmir", "siteAddress": "Alsancak Mah. 3, Konak, Izmir"}

    def visible():
        reply = boss.get(listing, headers=channel)
        assert reply.status_code == 200
        return {item["id"] for item in reply.json()}

    own = boss.post("/api/orders", json=order_body("token-order-0001"), headers=channel).json()["id"]
    blocked = place(boss, a_id, "partner-a-order-1", scenario="address-correction")
    other = place(boss, b_id, "partner-b-order-1", **izmir)
    tick(blocked)
    site = {"siteReference": SITES["Istanbul"]}

    assert visible() == {own}
    for order_id in (blocked, other):
        assert boss.get(f"{listing}/{order_id}", headers=channel).status_code == 403
    assert boss.post(f"/api/orders/{blocked}/address", json=site, headers=channel).status_code == 409
    replay = boss.post("/api/orders", json=order_body("partner-b-order-1", **izmir), headers=channel)
    assert replay.status_code == 409 and other not in replay.text
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "submit_partner_order", "arguments": order_body("partner-b-order-1", **izmir)}}
    assert boss.post("/mcp", json=call, headers=channel).json()["result"]["isError"] is True

    assert boss.put(f"/api/partners/{a_id}", json={"apiClient": True}).status_code == 200
    assert visible() == {own, blocked}
    assert boss.get(f"{listing}/{blocked}", headers=channel).status_code == 200
    assert boss.get(f"{listing}/{other}", headers=channel).status_code == 403
    fixed = boss.post(f"/api/orders/{blocked}/address", json=site, headers=channel)
    assert fixed.status_code == 200, fixed.text


def test_expected_delivery_dates_track_the_order_from_received_to_delivered(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    order_id = place(boss, a_id, "partner-a-order-1")
    deadline = app.state.store.read()["orders"][order_id]["deadline"]
    start = delivery_of(a, order_id)
    assert start["outlook"]["status"] == "onTrack" and start["outlook"]["date"] == deadline
    assert start["needsInput"] is False and start["invoice"]["status"] == "scheduled"

    edit_order(order_id, deadline=days_ago(1))
    late = delivery_of(a, order_id)["outlook"]
    assert late["status"] == "delayed" and "Raise a concern" in late["reason"]
    edit_order(order_id, deadline=deadline)

    finish(order_id)
    done = a.get(f"/api/partner/deliveries/{order_id}").json()
    assert done["outlook"]["status"] == "delivered"
    assert done["outlook"]["date"] == app.state.store.read()["orders"][order_id]["completedAt"]
    texts = [item["text"] for item in done["timeline"]]
    assert texts[0] == "Order received." and "Capacity reserved and the service activated." in texts
    assert "agent" not in done["timeline"][0] and "Order Validation Agent" not in str(done["timeline"])


def test_partners_fix_the_delivery_details_that_block_an_order(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    order_id = place(boss, a_id, "partner-a-order-1", scenario="address-correction")
    tick(order_id)
    stuck = delivery_of(a, order_id)
    assert stuck["needsInput"] is True and stuck["outlook"]["status"] == "atRisk"
    assert "address" in stuck["outlook"]["reason"] and overview(a)["counts"]["needsInput"] == 1

    url = f"/api/partner/deliveries/{order_id}/delivery"
    assert a.put(url, json={"city": "Istanbul", "siteAddress": WRONG_CITY}).status_code == 409
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert a.put(url, json={"city": "Istanbul", "siteAddress": ADDRESS, "preferredDate": yesterday}
                 ).status_code == 409
    soon = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    fixed = a.put(url, json={"city": "Istanbul", "siteAddress": ADDRESS, "preferredDate": soon,
                             "accessNotes": "Ask for the building manager", "poReference": "PO-7781",
                             "contact": CONTACT})
    assert fixed.status_code == 200, fixed.text
    body = fixed.json()
    assert body["needsInput"] is False and body["state"] == "inProgress"
    assert body["delivery"] | {"updatedAt": None} == {
        "preferredDate": soon, "accessNotes": "Ask for the building manager", "poReference": "PO-7781",
        "updatedAt": None, "updatedBy": "Deniz Kaya"}
    assert any("Delivery details updated by Deniz Kaya" in item["text"] for item in body["timeline"])

    audits = len(app.state.store.read()["audit"])
    assert a.put(url, json={"city": "Istanbul", "siteAddress": ADDRESS, "preferredDate": soon,
                            "accessNotes": "Ask for the building manager", "poReference": "PO-7781",
                            "contact": CONTACT}).status_code == 200
    assert len(app.state.store.read()["audit"]) == audits

    finish(order_id)
    closed = a.put(url, json={"city": "Istanbul", "siteAddress": ADDRESS})
    assert closed.status_code == 409 and "closed" in closed.json()["detail"]


def test_invoices_follow_the_payment_terms_and_staff_record_payments(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    b_id, b = onboard(boss, "Bosphorus Carrier", "ece@bosphorus.example")
    order_id = place(boss, a_id, "partner-a-order-1")
    other_id = place(boss, b_id, "partner-b-order-1")
    net = partners.PRICES["sdwan"]["monthlyTry"]
    total = net + net * partners.VAT_PERCENT // 100

    scheduled = overview(a)["invoices"][0]
    assert scheduled["status"] == "scheduled" and scheduled["total"] == total and scheduled["simulated"] is True
    assert boss.post(f"/api/partners/{a_id}/payments", json={"orderId": order_id}).status_code == 409

    finish(order_id)
    issued = overview(a)
    invoice = issued["invoices"][0]
    assert invoice["status"] == "issued" and invoice["number"] == "INV-" + order_id.split("-", 1)[1]
    due = datetime.fromisoformat(invoice["issuedAt"]).date() + timedelta(days=14)
    assert invoice["dueDate"] == due.isoformat()
    assert issued["payments"]["outstanding"] == total and issued["payments"]["overdue"] == 0
    assert issued["payments"]["nextDue"]["number"] == invoice["number"]

    edit_order(order_id, completedAt=days_ago(40))
    late = overview(a)["payments"]
    assert late["overdue"] == total and late["overdueCount"] == 1 and overview(a)["invoices"][0]["status"] == "overdue"

    assert boss.post(f"/api/partners/{b_id}/payments", json={"orderId": order_id}).status_code == 404
    paid = boss.post(f"/api/partners/{a_id}/payments", json={"orderId": order_id, "reference": "EFT-1042"})
    assert paid.status_code == 201 and paid.json()["status"] == "paid" and paid.json()["reference"] == "EFT-1042"
    settled = overview(a)["payments"]
    assert settled["paid"] == total and settled["outstanding"] == 0 and settled["nextDue"] is None
    assert boss.post(f"/api/partners/{a_id}/payments", json={"orderId": order_id}).status_code == 409
    assert overview(b)["payments"]["paid"] == 0 and delivery_of(b, other_id)["invoice"]["status"] == "scheduled"
    assert boss.delete(f"/api/partners/{a_id}/payments/{order_id}").json()["status"] == "overdue"
    assert boss.get(f"/api/partners/{a_id}/invoices").json()["payments"]["overdue"] == total


def test_concerns_are_a_conversation_between_the_partner_and_the_service_team(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    b_id, b = onboard(boss, "Bosphorus Carrier", "ece@bosphorus.example")
    order_id = place(boss, a_id, "partner-a-order-1")

    raised = a.post("/api/partner/concerns", json={"category": "billing", "orderId": order_id,
                                                   "subject": "Invoice amount looks wrong",
                                                   "text": "The VAT line seems off."})
    assert raised.status_code == 201
    concern = raised.json()
    assert concern["status"] == "open" and concern["messages"][0]["from"] == "partner"
    assert [item["id"] for item in overview(a)["concerns"]] == [concern["id"]] and overview(b)["concerns"] == []
    assert [item["id"] for item in a.get(f"/api/partner/deliveries/{order_id}").json()["concerns"]] == [concern["id"]]
    assert b.post(f"/api/partner/concerns/{concern['id']}/messages", json={"text": "me too"}).status_code == 404
    assert b.put(f"/api/partner/concerns/{concern['id']}", json={"status": "resolved"}).status_code == 404

    inbox = boss.get("/api/partners").json()
    assert inbox["openConcerns"] == 1 and inbox["concerns"][0]["partnerName"] == "Aegean Wholesale"
    answered = boss.post(f"/api/concerns/{concern['id']}/messages", json={"text": "We are checking the VAT line."})
    assert answered.status_code == 201 and answered.json()["status"] == "inProgress"
    assert answered.json()["messages"][-1]["from"] == "staff"
    assert portal.replies[-1][:2] == ("deniz@aegean.example", "Deniz Kaya")
    assert partners.get_concern(app.state.store.read(), concern["id"])["messages"][-1]["name"] == "TürkBizTel service team"

    assert a.put(f"/api/partner/concerns/{concern['id']}", json={"status": "inProgress"}).status_code == 409
    assert a.put(f"/api/partner/concerns/{concern['id']}", json={"status": "resolved"}).json()["status"] == "resolved"
    reopened = a.post(f"/api/partner/concerns/{concern['id']}/messages", json={"text": "Still wrong, sorry."})
    assert reopened.json()["status"] == "open" and len(reopened.json()["messages"]) == 3
    assert boss.put(f"/api/concerns/{concern['id']}/status", json={"status": "resolved"}).json()["status"] == "resolved"
    assert boss.get("/api/partners").json()["openConcerns"] == 0
    assert a.post("/api/partner/concerns", json={"category": "other", "subject": "x", "text": "hello"}
                  ).status_code == 422


def test_switching_a_partner_off_ends_its_sessions_and_new_orders(portal):
    boss = staff()
    a_id, a = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    assert a.get("/api/partner/overview").status_code == 200
    assert boss.put(f"/api/partners/{a_id}", json={"status": "disabled"}).json()["status"] == "disabled"
    assert a.get("/api/partner/overview").status_code == 401
    assert a.get("/api/auth/me").json()["authenticated"] is False
    blocked = new_client().post("/api/auth/login", json={"email": "deniz@aegean.example", "password": PASSWORD})
    assert blocked.status_code == 403 and "switched off" in blocked.json()["detail"]
    assert boss.post(f"/api/partners/{a_id}/orders", json=order_body("partner-a-order-1")).status_code == 409
    assert boss.put(f"/api/partners/{a_id}", json={}).status_code == 422
    assert boss.put(f"/api/partners/{a_id}", json={"status": "active"}).json()["status"] == "active"
    assert new_client().post("/api/auth/login", json={"email": "deniz@aegean.example", "password": PASSWORD}
                             ).status_code == 200


def test_partner_contacts_use_the_same_password_reset_and_can_change_their_password(portal):
    boss = staff()
    _, contact = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    assert contact.post("/api/partner/password", json={"current": "Wrong-Horse-42", "password": "Brand-New-Pass-7"}
                        ).status_code == 422
    other = new_client()
    other.post("/api/auth/login", json={"email": "deniz@aegean.example", "password": PASSWORD})
    assert contact.post("/api/partner/password", json={"current": PASSWORD, "password": "Brand-New-Pass-7"}
                        ).status_code == 200
    assert contact.get("/api/partner/overview").status_code == 200
    assert other.get("/api/partner/overview").status_code == 401
    profile = contact.put("/api/partner/profile", json={"name": "Deniz K. Kaya", "phone": "", "language": "tr"})
    assert profile.status_code == 200 and profile.json()["account"]["name"] == "Deniz K. Kaya"

    forgotten = new_client()
    assert forgotten.post("/api/auth/forgot", json={"email": "deniz@aegean.example"}).status_code == 202
    code = sent_code(portal, "reset")
    reset = forgotten.post("/api/auth/reset", json={"email": "deniz@aegean.example", "code": code,
                                                    "password": "Another-Pass-9"})
    assert reset.status_code == 200
    assert contact.get("/api/partner/overview").status_code == 401
    login = new_client().post("/api/auth/login", json={"email": "deniz@aegean.example", "password": "Another-Pass-9"})
    assert login.json()["role"] == "partner"

    pending = boss.post("/api/partners", json={"name": "Black Sea Link", "contactName": "Mert Aydin",
                                               "email": "mert@blacksea.example"})
    assert pending.status_code == 201
    mails = len(portal.mailbox)
    assert new_client().post("/api/auth/forgot", json={"email": "mert@blacksea.example"}).status_code == 202
    assert len(portal.mailbox) == mails


def test_the_pages_are_the_same_for_everybody_because_each_tab_finds_its_own_area(portal, monkeypatch):
    boss = staff()
    _, contact = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    customer = new_client()
    register(customer)
    admin = new_client()
    assert admin.post("/api/auth/staff", json={"token": ADMIN}).json() == {"role": "admin"}
    scripts = {"/": "/static/login.js", "/login": "/static/login.js", "/console": "/static/app.js",
               "/partner": "/static/partner.js", "/account": "/static/account.js"}

    for client in (new_client(), admin, contact, customer):
        for path, script in scripts.items():
            reply = client.get(path, follow_redirects=False)
            assert reply.status_code == 200 and script in reply.text, path

    monkeypatch.delenv("AMA_MODE")
    for client in (new_client(), contact):
        assert "/static/journey.html" in client.get("/", follow_redirects=False).text


def test_each_browser_tab_signs_in_on_its_own_and_the_shared_cookie_is_ignored(portal):
    boss = staff()
    created = boss.post("/api/partners", json={"name": "Aegean Wholesale", "contactName": "Deniz Kaya",
                                               "email": "deniz@aegean.example"})
    assert register(new_client()).status_code == 201
    browser = new_client()  # every tab of one browser profile shares this cookie jar

    accepted = browser.post("/api/auth/invite/accept", headers=TAB, json={
        "token": created.json()["invite"]["token"], "password": PASSWORD})
    assert accepted.status_code == 200 and accepted.json()["role"] == "partner"
    tokens = {"customer": tab_login(browser), "partner": accepted.json()["session"], "admin": tab_staff(browser)}
    assert auth.COOKIE not in browser.cookies and len(set(tokens.values())) == 3

    reach = {"customer": "/api/me/orders", "partner": "/api/partner/overview", "admin": "/api/state"}
    for role, token in tokens.items():
        assert browser.get("/api/auth/me", headers=tab(token)).json()["role"] == role
        for owner, path in reach.items():
            assert browser.get(path, headers=tab(token)).status_code == (200 if owner == role else 403), (role, path)
    for path in reach.values():
        assert browser.get(path, headers=TAB).status_code == 401
        assert browser.get(path, headers={"X-Session": tokens["admin"]}).status_code == 401
    assert browser.get("/api/auth/me", headers=TAB).json()["authenticated"] is False

    scripted = new_client()
    registered = register(scripted, email="bora@acme.example")
    assert registered.status_code == 201 and "session" not in registered.json()
    assert scripted.get("/api/me/orders").status_code == 200
    assert scripted.get("/api/me/orders", headers=TAB).status_code == 401
    me = scripted.get("/api/auth/me", headers=tab(tokens["customer"])).json()
    assert me["account"]["email"] == "ayse@acme.example"
    assert auth.COOKIE in scripted.cookies
    tab_login(scripted, "bora@acme.example")
    assert auth.COOKIE not in scripted.cookies and scripted.get("/api/me/orders").status_code == 401


def test_tabs_are_independent_even_for_two_accounts_of_the_same_kind(portal):
    register(new_client())
    register(new_client(), email="bora@acme.example")
    browser = new_client()
    ayse, bora = tab_login(browser), tab_login(browser, "bora@acme.example")

    def who(token):
        return browser.get("/api/auth/me", headers=tab(token)).json()["account"]["email"]

    assert (who(ayse), who(bora)) == ("ayse@acme.example", "bora@acme.example")
    signed_out = browser.post("/api/auth/logout", headers=tab(ayse))
    assert signed_out.status_code == 200 and auth.COOKIE not in browser.cookies
    assert who(bora) == "bora@acme.example"


def test_changing_a_password_in_one_tab_hands_that_tab_a_fresh_token_and_ends_the_others(portal):
    boss = staff()
    onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    register(new_client())
    browser = new_client()

    for path, email, probe in (("/api/me/password", "ayse@acme.example", "/api/me/orders"),
                               ("/api/partner/password", "deniz@aegean.example", "/api/partner/overview")):
        changing, elsewhere = tab_login(browser, email), tab_login(browser, email)
        changed = browser.post(path, json={"current": PASSWORD, "password": "Brand-New-Pass-7"},
                               headers=tab(changing))
        assert changed.status_code == 200 and changed.json()["ok"] is True
        fresh = changed.json()["session"]
        assert fresh != changing and auth.COOKIE not in browser.cookies
        assert browser.get(probe, headers=tab(fresh)).status_code == 200
        assert browser.get(probe, headers=tab(changing)).status_code == 401
        assert browser.get(probe, headers=tab(elsewhere)).status_code == 401


def test_tab_sessions_obey_the_same_revocation_and_csrf_rules_as_the_cookie(portal, monkeypatch):
    boss = staff()
    partner_id, _ = onboard(boss, "Aegean Wholesale", "deniz@aegean.example")
    register(new_client())
    browser = new_client()
    customer, partner, admin = tab_login(browser), tab_login(browser, "deniz@aegean.example"), tab_staff(browser)
    wrong = browser.post("/api/auth/staff", json={"token": "nope"}, headers=TAB)
    assert wrong.status_code == 401 and "session" not in wrong.json()

    for bad in ("junk", customer[::-1], auth.issue_session("customer", "CUS-404", 1),
                auth.issue_session("customer", "CUS-1", 1, seconds=-5), auth.issue_session("admin", "staff", "stale")):
        assert browser.get("/api/auth/me", headers=tab(bad)).json()["authenticated"] is False
        assert browser.get("/api/me/orders", headers=tab(bad)).status_code == 401
        assert browser.get("/api/state", headers=tab(bad)).status_code in (401, 403)

    nameless = {"X-Requested-With": ""}
    assert browser.post("/api/me/password", json={"current": PASSWORD, "password": "Brand-New-Pass-7"},
                        headers={**tab(customer), **nameless}).status_code == 403
    assert browser.post("/api/partner/password", json={"current": PASSWORD, "password": "Brand-New-Pass-7"},
                        headers={**tab(partner), **nameless}).status_code == 403
    assert browser.post("/api/partners", json={"name": "Sneaky Wholesale", "contactName": "No One",
                                               "email": "no.one@sneaky.example"},
                        headers={**tab(admin), **nameless}).status_code == 403

    assert boss.put(f"/api/partners/{partner_id}", json={"status": "disabled"}).status_code == 200
    assert browser.get("/api/partner/overview", headers=tab(partner)).status_code == 401
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN.replace("00001", "00002"))
    assert browser.get("/api/state", headers=tab(admin)).status_code == 401
