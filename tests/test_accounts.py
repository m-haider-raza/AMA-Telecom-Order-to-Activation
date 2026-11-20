import base64
import json

import pytest

from app import accounts, auth
from app.domain import process_next
from app.main import app
from app.security import _requests
from fastapi.testclient import TestClient

ADMIN = "test-manager-token-not-a-real-secret-00001"
PARTNER = "test-partner-token-not-a-real-secret-00001"
XHR = {"X-Requested-With": "turkbiztel"}
PASSWORD = "Correct-Horse-42"
CONTACT = {"contactName": "Ayse Yilmaz", "email": "ayse@acme.example", "preferredChannel": "email",
           "updateFrequency": "every-step"}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "3600")
    monkeypatch.setenv("AMA_MODE", "live")
    monkeypatch.setenv("AMA_DELIVERY", "live")
    monkeypatch.setenv("ACS_CONNECTION_STRING", "endpoint=https://mail.example.invalid/;accesskey=not-a-real-key")
    monkeypatch.setenv("ACS_EMAIL_SENDER", "DoNotReply@mail.example.invalid")
    for name in ("AZURE_STORAGE_ACCOUNT_URL", "DEMO_PUBLIC_ORIGIN", "AMA_SESSION_SECRET", "AMA_TRACKING_SECRET"):
        monkeypatch.delenv(name, raising=False)
    mailbox = []
    monkeypatch.setattr("app.accounts_api.send_account_email",
                        lambda email, name, purpose, code, language="en": mailbox.append((email, purpose, code)))
    monkeypatch.setattr("app.main.deliver_outbox", lambda *args, **kwargs: 0)
    _requests.clear()
    auth.reset_throttles()
    with TestClient(app) as client:
        client.headers.update(XHR)
        client.mailbox = mailbox
        yield client


def sent_code(client, purpose):
    """The newest one-time code the app e-mailed for this purpose; the mailbox stands in for a real inbox."""
    return next(code for _, kind, code in reversed(client.mailbox) if kind == purpose)


def new_client():
    client = TestClient(app)
    client.headers.update(XHR)
    return client


def register(client, email="ayse@acme.example", password=PASSWORD, **changes):
    return client.post("/api/auth/register", json={
        "name": "Ayse Yilmaz", "company": "Acme Lojistik AS", "email": email, "language": "en",
        "password": password, "consent": True, **changes})


def order_body(key="account-key-0001", **changes):
    return {"customer": "Acme Lojistik AS", "city": "Istanbul", "product": "sdwan", "language": "en",
            "idempotencyKey": key, "siteAddress": "Levent Mah. 12, Besiktas, Istanbul", "contact": CONTACT,
            **changes}


def tick(order_id):
    app.state.store.transact(lambda state: process_next(state, order_id))


def test_registration_signs_the_customer_in_and_never_returns_secrets(live):
    response = register(live)
    assert response.status_code == 201 and response.json()["account"]["verified"] is False
    assert "scrypt$" not in response.text and "passwordHash" not in response.text and PASSWORD not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    me = live.get("/api/auth/me").json()
    assert me["authenticated"] and me["role"] == "customer" and me["account"]["email"] == "ayse@acme.example"
    assert live.post("/api/auth/logout").status_code == 200
    assert live.get("/api/auth/me").json()["authenticated"] is False


def test_weak_passwords_and_duplicate_accounts_are_refused(live):
    assert register(live, password="short").status_code == 422
    assert register(live, password="ayse@acme.example").status_code == 422
    assert register(live, password="Password123").status_code == 422
    assert register(live, consent=False).status_code == 422
    assert register(live).status_code == 201
    duplicate = register(live, email="AYSE@acme.example")
    assert duplicate.status_code == 409 and "already exists" in duplicate.json()["detail"]


def test_sign_in_failures_are_generic_and_throttled_per_client(live):
    register(live)
    live.cookies.clear()
    unknown = live.post("/api/auth/login", json={"email": "nobody@acme.example", "password": PASSWORD})
    wrong = live.post("/api/auth/login", json={"email": "ayse@acme.example", "password": "Wrong-Horse-42"})
    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json()

    def attempt(client_ip, password):
        return live.post("/api/auth/login", json={"email": "ayse@acme.example", "password": password},
                         headers={"X-Forwarded-For": f"203.0.113.9, {client_ip}"})
    for _ in range(5):
        assert attempt("198.51.100.1", "Wrong-Horse-42").status_code == 401
    blocked = attempt("198.51.100.1", PASSWORD)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert attempt("198.51.100.2", PASSWORD).status_code == 200


def test_session_cookie_is_secure_behind_the_https_proxy(live):
    register(live)
    live.cookies.clear()
    plain = live.post("/api/auth/login", json={"email": "ayse@acme.example", "password": PASSWORD})
    assert "secure" not in plain.headers["set-cookie"].lower()
    live.cookies.clear()
    proxied = live.post("/api/auth/login", json={"email": "ayse@acme.example", "password": PASSWORD},
                        headers={"X-Forwarded-Proto": "https"})
    assert "secure" in proxied.headers["set-cookie"].lower()


def test_forged_and_expired_session_cookies_are_ignored(live):
    token = auth.issue_session("customer", "CUS-1", 1)
    payload, signature = token.split(".")
    assert auth.read_session(token)["s"] == "CUS-1"
    forged = base64.urlsafe_b64encode(json.dumps({"r": "admin", "s": "staff", "v": auth.admin_version(),
                                                  "e": 9999999999}).encode()).rstrip(b"=").decode()
    assert auth.read_session(f"{forged}.{signature}") is None
    assert auth.read_session(payload + "." + signature[::-1]) is None
    assert auth.read_session(auth.issue_session("customer", "CUS-1", 1, seconds=-5)) is None
    live.cookies.set(auth.COOKIE, f"{forged}.{signature}")
    assert live.get("/api/state").status_code == 401 and live.get("/api/me/orders").status_code == 401


def test_password_rules():
    for weak in ("short", "aaaaaaaaaaaa", "Password123", "Ayse.Yilmaz!1", "TurkBizTel123", "TürkBizTel123"):
        with pytest.raises(ValueError):
            auth.check_password(weak, "ayse.yilmaz@acme.example")
    auth.check_password(PASSWORD, "ayse@acme.example")
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored) and not auth.verify_password("Wrong-Horse-42", stored)
    assert not auth.verify_password(PASSWORD, None)


def test_customer_places_and_sees_only_their_own_orders(live):
    register(live)
    placed = live.post("/api/me/orders", json=order_body())
    assert placed.status_code == 201
    order = placed.json()
    assert order["contact"]["email"] == "ayse@acme.example" and "timeline" not in order
    assert live.post("/api/me/orders", json=order_body()).json()["id"] == order["id"]
    listed = live.get("/api/me/orders").json()
    assert [item["id"] for item in listed["orders"]] == [order["id"]]

    other = new_client()
    register(other, email="mehmet@beta.example", name="Mehmet Demir", company="Beta AS")
    assert other.get("/api/me/orders").json()["orders"] == []
    assert other.get(f"/api/me/orders/{order['id']}").status_code == 404
    assert other.put(f"/api/me/orders/{order['id']}/contact", json=CONTACT).status_code == 404
    assert other.post(f"/api/me/orders/{order['id']}/confirm-site").status_code == 404
    assert other.post("/api/me/orders", json=order_body()).status_code == 409
    anonymous = new_client()
    assert anonymous.get("/api/me/orders").status_code == 401
    assert anonymous.get(f"/api/me/orders/{order['id']}").status_code == 401


def test_customers_cannot_reach_staff_or_partner_apis_and_state_never_lists_accounts(live):
    register(live)
    order = live.post("/api/me/orders", json=order_body()).json()
    for method, path in (("get", "/api/state"), ("get", "/api/audit.csv"), ("get", "/tmf-api/serviceOrdering/v4/serviceOrder"),
                         ("get", "/tmf-api/productOrderingManagement/v4/productOrder"),
                         ("post", f"/api/orders/{order['id']}/decision")):
        kwargs = {"json": {"decision": "approve"}} if method == "post" else {}
        assert getattr(live, method)(path, **kwargs).status_code == 403, path
    state = new_client().get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"})
    assert state.status_code == 200 and "customers" not in state.json()
    assert "scrypt$" not in state.text and "ownerId" not in state.text and "passwordHash" not in state.text


def test_staff_session_opens_the_console_api_but_writes_need_the_csrf_header(live):
    assert live.post("/api/auth/staff", json={"token": "nope"}).status_code == 401
    assert live.get("/api/state").status_code == 401
    assert live.post("/api/auth/staff", json={"token": ADMIN}).json() == {"role": "admin"}
    assert live.get("/api/state").status_code == 200
    assert live.get("/api/auth/me").json()["role"] == "admin"
    order = live.post("/api/portal/orders", json=order_body(key="guest-key-0001")).json()
    no_header = {"X-Requested-With": ""}
    assert live.post(f"/api/orders/{order['id']}/decision", json={"decision": "approve"},
                     headers=no_header).status_code == 403
    assert live.post(f"/api/orders/{order['id']}/decision", json={"decision": "approve"}).status_code == 409
    assert live.get("/api/me/orders").status_code == 403
    live.post("/api/auth/logout")
    assert live.get("/api/state").status_code == 401


def test_rotating_the_admin_token_ends_staff_sessions(live, monkeypatch):
    live.post("/api/auth/staff", json={"token": ADMIN})
    assert live.get("/api/state").status_code == 200
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN.replace("00001", "00002"))
    assert live.get("/api/state").status_code == 401


def test_earlier_guest_orders_join_the_account_only_after_the_email_is_verified(live):
    mine = live.post("/api/portal/orders", json=order_body(key="guest-key-0001")).json()
    live.post("/api/portal/orders", json=order_body(
        key="guest-key-0002", contact={**CONTACT, "email": "someone.else@acme.example"}))
    register(live)
    code = sent_code(live, "verify")
    assert live.get("/api/me/orders").json()["orders"] == []
    verified = live.post("/api/auth/verify", json={"code": code})
    assert verified.status_code == 200 and verified.json() == {"verified": True, "claimed": 1}
    assert [item["id"] for item in live.get("/api/me/orders").json()["orders"]] == [mine["id"]]
    audit = new_client().get("/api/audit.csv", headers={"Authorization": f"Bearer {ADMIN}"}).text
    assert "account.registered" in audit and "account.verified" in audit and "order.claimed" in audit


def test_wrong_codes_are_counted_and_lock_after_five_attempts(live):
    register(live)
    code = sent_code(live, "verify")
    wrong = "111111" if code == "000000" else "000000"
    for _ in range(5):
        assert live.post("/api/auth/verify", json={"code": wrong}).status_code == 422
    locked = live.post("/api/auth/verify", json={"code": code})
    assert locked.status_code == 422 and "Too many wrong attempts" in locked.json()["detail"]
    assert live.post("/api/auth/resend").status_code == 429


def test_password_reset_replaces_the_password_and_revokes_old_sessions(live):
    register(live)
    stale = dict(live.cookies)
    live.cookies.clear()
    quiet = live.post("/api/auth/forgot", json={"email": "nobody@acme.example"})
    forgot = live.post("/api/auth/forgot", json={"email": "ayse@acme.example"})
    assert quiet.status_code == forgot.status_code == 202 and quiet.json() == forgot.json()
    assert [kind for _, kind, _ in live.mailbox].count("reset") == 1
    code = sent_code(live, "reset")
    wrong = "111111" if code == "000000" else "000000"
    assert live.post("/api/auth/reset", json={
        "email": "ayse@acme.example", "code": wrong, "password": "Brand-New-Pass-7"}).status_code == 422
    assert live.post("/api/auth/reset", json={
        "email": "ayse@acme.example", "code": code, "password": "Brand-New-Pass-7"}).status_code == 200
    assert live.post("/api/auth/login", json={"email": "ayse@acme.example", "password": PASSWORD}).status_code == 401
    assert live.post("/api/auth/login", json={
        "email": "ayse@acme.example", "password": "Brand-New-Pass-7"}).status_code == 200
    assert live.get("/api/auth/me").json()["account"]["verified"] is True
    live.cookies.clear()
    live.cookies.set(auth.COOKIE, stale[auth.COOKIE])
    assert live.get("/api/me/orders").status_code == 401


def test_changing_the_password_signs_other_devices_out(live):
    register(live)
    other = new_client()
    other.post("/api/auth/login", json={"email": "ayse@acme.example", "password": PASSWORD})
    assert other.get("/api/me/orders").status_code == 200
    assert live.post("/api/me/password", json={"current": "Wrong-Horse-42", "password": "Brand-New-Pass-7"}
                     ).status_code == 422
    assert live.post("/api/me/password", json={"current": PASSWORD, "password": "Brand-New-Pass-7"}).status_code == 200
    assert live.get("/api/me/orders").status_code == 200
    assert other.get("/api/me/orders").status_code == 401


def test_customer_corrects_the_address_of_a_held_order(live):
    register(live)
    order = live.post("/api/me/orders", json=order_body(siteAddress="Kizilay Mah. 5, Cankaya, Ankara")).json()
    tick(order["id"])
    held = live.get(f"/api/me/orders/{order['id']}").json()
    assert held["state"] == "awaitingCustomer" and held["action"]["type"] == "address-correction"
    refused = live.post(f"/api/me/orders/{order['id']}/site", json={
        "city": "Istanbul", "siteAddress": "Kizilay Mah. 5, Cankaya, Ankara"})
    assert refused.status_code == 409 and "Istanbul" in refused.json()["detail"]
    fixed = live.post(f"/api/me/orders/{order['id']}/site", json={
        "city": "Ankara", "siteAddress": "Kizilay Mah. 5, Cankaya, Ankara"})
    assert fixed.status_code == 200 and fixed.json()["city"] == "Ankara" and fixed.json()["action"] is None
    tick(order["id"])
    assert live.get(f"/api/me/orders/{order['id']}").json()["stage"] == "decomposition"
    assert live.post(f"/api/me/orders/{order['id']}/site", json={
        "city": "Ankara", "siteAddress": "Kizilay Mah. 5, Cankaya, Ankara"}).status_code == 409


def test_customer_can_confirm_the_address_and_edit_contact_preferences(live):
    register(live)
    order = live.post("/api/me/orders", json=order_body(siteAddress="Kizilay Mah. 5, Cankaya, Ankara")).json()
    assert live.post(f"/api/me/orders/{order['id']}/confirm-site").status_code == 409
    tick(order["id"])
    confirmed = live.post(f"/api/me/orders/{order['id']}/confirm-site")
    assert confirmed.status_code == 200 and confirmed.json()["state"] == "inProgress"
    changed = live.put(f"/api/me/orders/{order['id']}/contact", json={
        "contactName": "Ayse Yilmaz", "phone": "+905551112233", "preferredChannel": "sms",
        "updateFrequency": "weekly"})
    assert changed.status_code == 200 and changed.json()["contact"]["phone"] == "+905551112233"


def test_a_guest_order_can_be_linked_with_its_tracking_code(live):
    guest = live.post("/api/portal/orders", json=order_body(
        key="guest-key-0003", contact={**CONTACT, "email": "colleague@acme.example"})).json()
    register(live)
    link = {"orderId": guest["id"], "trackingToken": guest["trackingToken"]}
    assert live.post("/api/me/orders/link", json={**link, "trackingToken": "f" * 40}).status_code == 409
    assert live.post("/api/me/orders/link", json=link).json()["id"] == guest["id"]
    assert [item["id"] for item in live.get("/api/me/orders").json()["orders"]] == [guest["id"]]
    other = new_client()
    register(other, email="mehmet@beta.example", name="Mehmet Demir", company="Beta AS")
    assert other.post("/api/me/orders/link", json=link).status_code == 409


def test_profile_changes_keep_the_email_and_verification_state(live):
    register(live)
    changed = live.put("/api/me/profile", json={"name": "Ayse Kaya", "company": "Acme Lojistik AS",
                                                "phone": "+905551112233", "language": "tr"})
    assert changed.status_code == 200 and changed.json()["account"]["name"] == "Ayse Kaya"
    assert changed.json()["account"]["email"] == "ayse@acme.example"
    assert live.put("/api/me/profile", json={"name": "Ayse Kaya", "company": "Acme", "phone": "0555",
                                             "language": "tr"}).status_code == 422
    assert accounts.normalise(" Ayse@ACME.example ") == "ayse@acme.example"


def test_a_live_site_without_mail_delivery_never_reveals_one_time_codes(live, monkeypatch):
    monkeypatch.delenv("AMA_DELIVERY")
    registered = register(live)
    assert registered.status_code == 201 and registered.json()["delivery"] == "unavailable"
    assert "simulatedCode" not in registered.text
    known = live.post("/api/auth/forgot", json={"email": "ayse@acme.example"})
    unknown = live.post("/api/auth/forgot", json={"email": "nobody@acme.example"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert live.mailbox == []


def test_a_demo_site_shows_the_code_on_screen_instead_of_mailing_it(live, monkeypatch):
    monkeypatch.setenv("AMA_MODE", "demo")
    monkeypatch.delenv("AMA_DELIVERY")
    assert register(live).status_code == 422
    registered = register(live, company="Demo Lojistik AS")
    body = registered.json()
    assert registered.status_code == 201 and body["delivery"] == "simulated" and len(body["simulatedCode"]) == 6
    assert live.post("/api/auth/verify", json={"code": body["simulatedCode"]}).json()["verified"] is True
    assert live.mailbox == []
