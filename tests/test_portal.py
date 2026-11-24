import pytest

from app.delivery import deliver_outbox
from app.domain import customer_view, initial_state
from app.main import app
from app.security import _requests
from app.store import Store
from fastapi.testclient import TestClient

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
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    _requests.clear()
    with TestClient(app) as client:
        yield client


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
    headers = {"X-Tracking-Token": order["trackingToken"]}
    tracked = live.get(f"/api/portal/orders/{order['id']}", headers=headers)
    assert tracked.status_code == 200 and tracked.json()["id"] == order["id"]
    assert live.get(f"/api/portal/orders/{order['id']}").status_code == 404
    assert live.get(f"/api/portal/orders/{order['id']}", headers={"X-Tracking-Token": "x" * 40}).status_code == 404
    assert live.post("/api/portal/orders", json=order_body(scenario="human-exception")).status_code == 422
    assert live.post("/api/portal/orders", json=order_body(idempotencyKey="portal-key-0002", contact=None)
                     ).status_code == 422


def test_customer_can_change_update_preferences(live):
    order = live.post("/api/portal/orders", json=order_body()).json()
    headers = {"X-Tracking-Token": order["trackingToken"]}
    changed = live.put(f"/api/portal/orders/{order['id']}/contact", headers=headers, json={
        "contactName": "Ayse Yilmaz", "phone": "+905551112233", "preferredChannel": "sms",
        "updateFrequency": "weekly"})
    assert changed.status_code == 200 and changed.json()["contact"]["updateFrequency"] == "weekly"
    assert live.put(f"/api/portal/orders/{order['id']}/contact", json=CONTACT).status_code == 404


def test_address_outside_the_selected_city_is_held_then_customer_confirms(live):
    order = live.post("/api/portal/orders", json=order_body(siteAddress="Kizilay Mah. 5, Cankaya, Ankara")).json()
    headers = {"X-Tracking-Token": order["trackingToken"]}
    def tick(state):
        from app.domain import process_next
        process_next(state, order["id"])
    app.state.store.transact(tick)
    held = live.get(f"/api/portal/orders/{order['id']}", headers=headers).json()
    assert held["state"] == "awaitingCustomer" and held["action"]["type"] == "address-correction"
    assert "installation address" in held["updates"][-1]["en"]
    fixed = live.post(f"/api/portal/orders/{order['id']}/address", headers=headers,
                      json={"siteReference": held["action"]["expectedSiteReference"]})
    assert fixed.status_code == 200 and fixed.json()["state"] == "inProgress"


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
