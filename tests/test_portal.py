import pytest

from app import auth, main
from app.delivery import deliver_outbox, send_email
from app.domain import customer_view, initial_state, process_next
from app.main import app
from app.security import _requests
from app.store import Store
from fastapi.testclient import TestClient
from tests.test_accounts import new_client, register

ADMIN = "test-manager-token-not-a-real-secret-00001"
PARTNER = "test-partner-token-not-a-real-secret-00001"
CONTACT = {"contactName": "Ayse Yilmaz", "email": "ayse@acme.example", "preferredChannel": "email",
           "updateFrequency": "every-step"}


def order_body(**changes):
    return {"customer": "Acme Lojistik AS", "city": "Istanbul", "product": "sdwan", "language": "en",
            "idempotencyKey": "portal-key-0001", "siteAddress": "Levent Mah. 12, Besiktas, Istanbul",
            "contact": CONTACT, **changes}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "3600")
    monkeypatch.setenv("AMA_MODE", "live")
    monkeypatch.setenv("AMA_DELIVERY", "simulated")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    _requests.clear()
    auth.reset_throttles()
    with TestClient(app) as client:
        client.headers["X-Requested-With"] = "turkbiztel"
        yield client


def sign_in_to_order(client, order):
    assert register(client).status_code == 201
    response = client.post("/api/me/orders/link", json={
        "orderId": order["id"], "trackingToken": order["trackingToken"]})
    assert response.status_code == 200, response.text


def test_live_mode_has_no_demo_data_and_protects_the_dashboard_feed(live):
    assert live.get("/api/state").status_code == 401
    state = live.get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"}).json()
    assert state["orders"] == {} and state["configuration"]["environment"] == "Live customer ordering"
    assert live.get("/health").json()["syntheticDataOnly"] is False


def test_customer_places_and_tracks_an_order_with_a_real_company_name(live):
    created = live.post("/api/portal/orders", json=order_body())
    assert created.status_code == 201
    order = created.json()
    assert order["customer"] == "Acme Lojistik AS" and order["contact"]["email"] == "a***@acme.example"
    assert "timeline" not in order and "actor" not in order and "ayse@acme.example" not in created.text
    assert order["trackPath"] == f"/account#order/{order['id']}" and "token=" not in order["trackPath"]
    headers = {"X-Tracking-Token": order["trackingToken"]}
    assert live.get(f"/api/portal/orders/{order['id']}", headers=headers).status_code == 401
    sign_in_to_order(live, order)
    tracked = live.get(f"/api/portal/orders/{order['id']}")
    assert tracked.status_code == 200 and tracked.json()["id"] == order["id"]
    assert live.get(f"/api/portal/orders/{order['id']}", headers={"X-Tracking-Token": "x" * 40}).status_code == 200
    assert live.post("/api/portal/orders", json=order_body(scenario="human-exception")).status_code == 422
    assert live.post("/api/portal/orders", json=order_body(idempotencyKey="portal-key-0002", contact=None)
                     ).status_code == 422


def test_customer_can_change_update_preferences(live):
    order = live.post("/api/portal/orders", json=order_body()).json()
    sign_in_to_order(live, order)
    changed = live.put(f"/api/portal/orders/{order['id']}/contact", json={
        "contactName": "Ayse Yilmaz", "phone": "+905551112233", "preferredChannel": "sms",
        "updateFrequency": "weekly"})
    assert changed.status_code == 200 and changed.json()["contact"]["updateFrequency"] == "weekly"
    assert new_client().put(f"/api/portal/orders/{order['id']}/contact", json=CONTACT).status_code == 401


def test_address_outside_the_selected_city_is_held_then_customer_confirms(live):
    order = live.post("/api/portal/orders", json=order_body(siteAddress="Kizilay Mah. 5, Cankaya, Ankara")).json()
    sign_in_to_order(live, order)
    def tick(state):
        from app.domain import process_next
        process_next(state, order["id"])
    app.state.store.transact(tick)
    held = live.get(f"/api/portal/orders/{order['id']}").json()
    assert held["state"] == "awaitingCustomer" and held["action"]["type"] == "address-correction"
    assert "installation address" in held["updates"][-1]["en"]
    fixed = live.post(f"/api/portal/orders/{order['id']}/address",
                      json={"siteReference": held["action"]["expectedSiteReference"]})
    assert fixed.status_code == 200 and fixed.json()["state"] == "inProgress"


@pytest.mark.parametrize("mode", ["demo", "live"])
@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("POST", "/address"), ("PUT", "/contact")])
def test_tracking_code_cannot_replace_sign_in_or_order_ownership(live, monkeypatch, mode, method, suffix):
    monkeypatch.setenv("AMA_MODE", mode)
    order = live.post("/api/portal/orders", json=order_body()).json()
    path = f"/api/portal/orders/{order['id']}{suffix}"
    headers = {"X-Tracking-Token": order["trackingToken"]}
    payload = {"json": CONTACT} if suffix == "/contact" else (
        {"json": {"siteReference": order["siteReference"]}} if suffix else {})
    refused = new_client().request(method, path, headers=headers, **payload)
    assert refused.status_code == 401 and order["id"] not in refused.text
    sign_in_to_order(live, order)
    other = new_client()
    assert register(other, email="someone.else@acme.example").status_code == 201
    before = app.state.store.read()["orders"][order["id"]]
    refused = other.request(method, path, headers=headers, **payload)
    assert refused.status_code == 404 and order["id"] not in refused.text
    assert app.state.store.read()["orders"][order["id"]] == before


def test_expired_customer_session_cannot_use_a_tracking_code(live):
    order = live.post("/api/portal/orders", json=order_body()).json()
    sign_in_to_order(live, order)
    customer = live.get("/api/auth/me").json()["account"]
    headers = {
        "X-Session-Mode": "tab",
        "X-Session": auth.issue_session("customer", customer["id"], 1, seconds=-5),
        "X-Tracking-Token": order["trackingToken"],
    }
    assert live.get(f"/api/portal/orders/{order['id']}", headers=headers).status_code == 401


@pytest.mark.parametrize(("method", "suffix"), [("POST", "/address"), ("PUT", "/contact")])
def test_portal_changes_recheck_ownership_inside_the_write_transaction(live, monkeypatch, method, suffix):
    order = live.post("/api/portal/orders", json=order_body(siteAddress="Kizilay Mah. 5, Cankaya, Ankara")).json()
    sign_in_to_order(live, order)
    app.state.store.transact(lambda state: process_next(state, order["id"]))
    before = app.state.store.read()["orders"][order["id"]]
    other = new_client()
    next_owner = register(other, email="new.owner@acme.example").json()["account"]["id"]
    original = main.transact

    def transfer_before_write(application, change):
        def transfer(state):
            state["orders"][order["id"]]["ownerId"] = next_owner
        application.state.store.transact(transfer)
        return original(application, change)
    monkeypatch.setattr(main, "transact", transfer_before_write)
    payload = {"siteReference": customer_view(before)["action"]["expectedSiteReference"]} if suffix == "/address" else {
        **CONTACT, "contactName": "Changed Contact"}
    response = live.request(method, f"/api/portal/orders/{order['id']}{suffix}", json=payload)
    assert response.status_code == 404
    after = app.state.store.read()["orders"][order["id"]]
    assert after["ownerId"] == next_owner
    assert {**after, "ownerId": before["ownerId"]} == before


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize(("fields", "route"), [
    ({}, "/account#order/"),
    ({"partnerId": "PAR-0123456789"}, "/partner#deliveries/"),
    ({"actor": "wholesale-partner"}, "/partner#deliveries/"),
])
def test_order_update_email_links_to_a_signed_in_portal_without_a_tracking_token(monkeypatch, language, fields, route):
    monkeypatch.setenv("AMA_PUBLIC_ORIGIN", "https://turkbiztel.example")
    mail = []
    monkeypatch.setattr("app.delivery.send_plain_email", lambda *args: mail.append(args))
    order = {"id": "AMA-0123456789", "language": language, **fields}
    send_email(CONTACT, order, {"en": "Your order has an update.", "tr": "Siparişiniz güncellendi."})
    assert len(mail) == 1 and mail[0][0] == CONTACT["email"]
    body = mail[0][2]
    assert f"https://turkbiztel.example{route}{order['id']}" in body
    assert "token=" not in body and "/track#" not in body
    assert ("Sign in" if language == "en" else "giriş yapın") in body


def test_customer_view_hides_internal_data():
    state = initial_state()
    view = customer_view(next(iter(state["orders"].values())))
    assert not {"timeline", "actor", "approvals", "outbox", "aiMode"} & set(view)


def test_real_delivery_sends_once_and_records_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("AMA_MODE", "live")
    monkeypatch.setenv("AMA_DELIVERY", "live")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    store = Store()
    from app.domain import create_order, process_next
    body = {"customer": "Acme Lojistik AS", "city": "Istanbul", "product": "sdwan", "scenario": "happy",
            "language": "en", "idempotencyKey": "delivery-key-1", "contact": CONTACT}
    store.transact(lambda state: create_order(state, body, "service-manager"))
    store.transact(lambda state: [process_next(state, key) for key in list(state["orders"])])
    sent = []
    assert deliver_outbox(store, sender=lambda contact, order, item: sent.append((contact["email"], item["id"]))) == 1
    assert sent[0][0] == "ayse@acme.example"
    assert deliver_outbox(store, sender=lambda *args: sent.append(args)) == 0 and len(sent) == 1
    order = next(iter(store.read()["orders"].values()))
    assert order["outbox"][0]["status"] == "sent" and order["outbox"][0]["deliveredExternally"] is True

    def broken(*args):
        raise OSError("smtp down")

    def add_pending(state):
        order = next(iter(state["orders"].values()))
        from app.domain import dispatch
        dispatch(state, order, "Second", "Ikinci", "critical")
    store.transact(add_pending)
    for _ in range(3):
        assert deliver_outbox(store, sender=broken) == 0
    last = next(iter(store.read()["orders"].values()))["outbox"][-1]
    assert last["status"] == "failed" and last["attempts"] == 3 and last["deliveredExternally"] is False
    assert deliver_outbox(store, sender=broken) == 0


def test_enabling_delivery_does_not_send_preexisting_simulated_digests(monkeypatch):
    from app.domain import create_order, dispatch, flush_digests
    monkeypatch.setenv("AMA_MODE", "live")
    monkeypatch.setenv("AMA_DELIVERY", "simulated")
    state = initial_state()
    order = create_order(state, {
        "customer": "Demo Digest", "city": "Istanbul", "product": "sdwan", "scenario": "happy",
        "language": "en", "idempotencyKey": "simulated-digest-test",
        "contact": {**CONTACT, "updateFrequency": "daily"},
    }, "customer-portal")
    item = dispatch(state, order, "Scheduled update", "Scheduled update", "progress")
    assert item["status"] == "scheduled-digest"
    monkeypatch.setenv("AMA_DELIVERY", "live")
    flush_digests(state, item["sendAfter"])
    assert item["status"] == "simulated-sent" and item["deliveredExternally"] is False
