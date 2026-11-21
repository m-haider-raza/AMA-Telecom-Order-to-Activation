import csv
import hashlib
import io
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.domain import process_next, tracking_token
from app.main import app
from app.security import _requests
from tests.test_accounts import ADMIN, CONTACT, PARTNER, PASSWORD, XHR, new_client, order_body, register

TENANT = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
CLIENT = "5f4e3d2c-1b0a-4f9e-8d7c-6b5a49382716"
PUBLIC = "https://tbt-sso.example.invalid"
TAB = {"X-Session-Mode": "tab"}
PEOPLE = {
    "ServiceManager": {"oid": "11111111-1111-4111-8111-111111111111", "upn": "mira.manager@contoso.example",
                       "name": "Mira Manager"},
    "ServiceOperator": {"oid": "22222222-2222-4222-8222-222222222222", "upn": "omer.operator@contoso.example",
                        "name": "Ömer Operatör"},
    "ServiceAuditor": {"oid": "33333333-3333-4333-8333-333333333333", "upn": "asli.auditor@contoso.example",
                       "name": "Aslı Auditor"},
}
BASE_ENV = {
    "DEMO_ADMIN_TOKEN": ADMIN, "DEMO_PARTNER_TOKEN": PARTNER, "DEMO_TICK_SECONDS": "3600", "AMA_MODE": "live",
    "AMA_DELIVERY": "live", "ACS_CONNECTION_STRING": "endpoint=https://mail.example.invalid/;accesskey=not-a-real-key",
    "ACS_EMAIL_SENDER": "DoNotReply@mail.example.invalid", "AMA_STAFF_AUTH": "sso", "AMA_SSO_TENANT_ID": TENANT,
    "AMA_SSO_CLIENT_ID": CLIENT, "AMA_SSO_CLIENT_SECRET": "test-client-secret-not-real",
    "AMA_SESSION_SECRET": "test-session-secret-not-a-real-secret-0001", "AMA_PUBLIC_ORIGIN": PUBLIC,
}


class Microsoft:
    """Stands in for the Microsoft client library, so no test reaches the network. The token endpoint answers
    whatever `result` (or `failure`) the test sets."""

    def __init__(self):
        self.calls, self.result, self.failure = [], None, None

    def initiate_auth_code_flow(self, scopes, redirect_uri=None, prompt=None, **kwargs):
        self.calls.append(("initiate", scopes, redirect_uri, prompt))
        return {"state": "state-1", "nonce": "nonce-1", "code_verifier": "verifier-1", "redirect_uri": redirect_uri,
                "scope": ["openid", "profile", "offline_access"], "claims_challenge": None,
                "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?client_id={CLIENT}"}

    def acquire_token_by_auth_code_flow(self, flow, response, scopes=None):
        self.calls.append(("exchange", dict(flow), dict(response)))
        if self.failure:
            raise self.failure
        return self.result


def configure(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    for name in ("AZURE_STORAGE_ACCOUNT_URL", "DEMO_PUBLIC_ORIGIN", "AMA_TRACKING_SECRET", "AZURE_CLIENT_ID",
                 "AMA_STAFF_SESSION_HOURS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**BASE_ENV, **env}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


MAIL_FIELDS = ("email", "name", "company", "order_id", "requester", "product", "city", "expires", "link", "language")


@pytest.fixture
def sso(tmp_path, monkeypatch):
    """The app in single sign-on mode. Nothing leaves the process: `client.mailbox` holds the one-time codes that
    would be e-mailed to customers and `client.approvals` the approval requests that would be e-mailed to an
    enterprise's contact, each as a dict named after what send_approval_email receives."""
    configure(monkeypatch, tmp_path)
    microsoft = Microsoft()
    invites, mailbox, approvals = [], [], []
    monkeypatch.setattr("app.sso.msal_app", lambda: microsoft)
    monkeypatch.setattr("app.accounts_api.send_account_email",
                        lambda email, name, purpose, code, language="en": mailbox.append((email, purpose, code)))
    monkeypatch.setattr("app.partners_api.send_invite_email", lambda *args: invites.append(args))
    monkeypatch.setattr("app.partners_api.send_concern_email", lambda *args: None)
    monkeypatch.setattr("app.approvals.send_approval_email",
                        lambda *args: approvals.append(dict(zip(MAIL_FIELDS, args))))
    monkeypatch.setattr("app.main.deliver_outbox", lambda *args, **kwargs: 0)
    _requests.clear()
    auth.reset_throttles()
    with TestClient(app) as client:
        client.headers.update(XHR)
        client.microsoft = microsoft
        client.mailbox, client.approvals = mailbox, approvals
        yield client


def identity(role="ServiceManager", **changes):
    person = PEOPLE[role]
    claims = {"tid": TENANT, "aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
              "exp": time.time() + 3600, "oid": person["oid"], "preferred_username": person["upn"],
              "name": person["name"], "roles": [role], **changes}
    return {"id_token_claims": claims}


def callback(client, role="ServiceManager", **changes):
    """Microsoft sends the browser back to the app after a sign-in that began at /auth/sso/login."""
    client.microsoft.result = identity(role, **changes)
    assert client.get("/auth/sso/login", follow_redirects=False).status_code == 302
    return client.get("/auth/sso/callback?code=abc&state=state-1", follow_redirects=False)


def sign_in(client, role="ServiceManager", **changes):
    """One person's whole browser journey; returns the session their browser tab then holds."""
    arrived = callback(client, role, **changes)
    assert arrived.headers["location"] == "/login#sso", arrived.headers["location"]
    handoff = client.post("/api/auth/sso/handoff", headers=TAB)
    assert handoff.status_code == 200, handoff.text
    return handoff.json()["session"]


def tab(token):
    return {**TAB, "X-Session": token}


def person_of(role):
    return {**PEOPLE[role], "role": role}


def trail(client, token, **query):
    reply = client.get("/api/audit", params=query, headers=tab(token))
    assert reply.status_code == 200, reply.text
    return reply.json()


def enroll(sso, email="ayse@acme.example", verified=True, **changes):
    """An enterprise in its own browser: registered and, unless told otherwise, with its e-mail verified, which is
    what lets the service team place an order for it. Returns that browser and its account."""
    browser = new_client()
    created = register(browser, email=email, **changes)
    assert created.status_code == 201, created.text
    if verified:
        code = next(item[2] for item in reversed(sso.mailbox) if item[:2] == (email, "verify"))
        assert browser.post("/api/auth/verify", json={"code": code}).status_code == 200
    return browser, created.json()["account"]


def place_for(sso, headers, enterprise_id, key, **changes):
    """The service team placing an order for an enterprise. Returns the reply."""
    return sso.post(f"/api/enterprises/{enterprise_id}/orders", headers=headers, json={
        "city": "Istanbul", "product": "sdwan", "scenario": "happy", "idempotencyKey": key, **changes})


def approval_token(sso, order_id):
    """The token in the newest link e-mailed to the enterprise's contact for this order."""
    link = next(mail["link"] for mail in reversed(sso.approvals) if mail["order_id"] == order_id)
    return link.split("#token=", 1)[1]


def answer(sso, order_id, decision="approve"):
    """The contact opening that link and answering. Returns the reply."""
    return sso.post("/api/approvals/decide", json={"token": approval_token(sso, order_id), "decision": decision})


def staff_order(sso, headers, enterprise_id, key, **changes):
    """An order the way the service team now starts one: placed for an enterprise, then approved by its contact."""
    placed = place_for(sso, headers, enterprise_id, key, **changes)
    assert placed.status_code == 201, placed.text
    assert answer(sso, placed.json()["id"]).status_code == 200
    return placed.json()


def test_sign_in_goes_to_microsoft_and_keeps_only_a_signed_flow_cookie(sso):
    response = sso.get("/auth/sso/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize")
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"].lower()
    assert "tbt_sso_flow=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/auth/sso" in cookie
    assert sso.microsoft.calls == [("initiate", [], f"{PUBLIC}/auth/sso/callback", "select_account")]
    kept = auth.read_blob("sso-flow", response.cookies["tbt_sso_flow"])
    assert kept["f"]["state"] == "state-1" and "auth_uri" not in kept["f"]


def test_sso_routes_do_not_exist_in_token_mode(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path, AMA_STAFF_AUTH=None)
    with TestClient(app) as client:
        client.headers.update(XHR)
        assert client.get("/auth/sso/login", follow_redirects=False).status_code == 404
        assert client.get("/auth/sso/callback", follow_redirects=False).status_code == 404
        assert client.post("/api/auth/sso/handoff").status_code == 404
        assert client.get("/api/auth/me").json()["staffAuth"] == "token"
        assert client.post("/api/auth/staff", json={"token": ADMIN}).status_code == 200


def test_the_app_will_not_start_with_incomplete_or_unknown_staff_sign_in_settings(tmp_path, monkeypatch):
    for missing in ("AMA_SSO_TENANT_ID", "AMA_SSO_CLIENT_ID", "AMA_SESSION_SECRET", "AMA_PUBLIC_ORIGIN"):
        configure(monkeypatch, tmp_path, **{missing: None})
        with pytest.raises(RuntimeError, match=f"fully configured.*{missing}"):
            with TestClient(app):
                pass
    configure(monkeypatch, tmp_path, AMA_SSO_CLIENT_SECRET=None)
    with pytest.raises(RuntimeError, match="AZURE_CLIENT_ID"):
        with TestClient(app):
            pass
    configure(monkeypatch, tmp_path, AMA_SESSION_SECRET="too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        with TestClient(app):
            pass
    configure(monkeypatch, tmp_path, AMA_PUBLIC_ORIGIN="http://app.example.invalid")
    with pytest.raises(RuntimeError, match="https origin"):
        with TestClient(app):
            pass
    configure(monkeypatch, tmp_path, AMA_STAFF_AUTH="entra")
    with pytest.raises(RuntimeError, match="either 'token' or 'sso'"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("query,code", [
    ("error=access_denied", "denied"),
    ("error=invalid_request&error_description=AADSTS50105%3A+not+assigned+to+a+role", "not-assigned"),
    ("error=interaction_required&error_description=AADSTS65001%3A+consent+is+needed", "consent"),
    ("error=server_error&error_description=%3Cscript%3Ealert(1)%3C/script%3E", "failed"),
])
def test_microsoft_errors_become_fixed_codes_that_carry_none_of_its_text(sso, query, code):
    sso.get("/auth/sso/login", follow_redirects=False)
    response = sso.get(f"/auth/sso/callback?{query}", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == f"/login#sso-error={code}"
    assert sso.microsoft.calls[-1][0] == "initiate"


def test_a_callback_needs_the_flow_this_browser_started(sso):
    expired = "/login#sso-error=expired"
    assert sso.get("/auth/sso/callback?code=abc", follow_redirects=False).headers["location"] == expired
    sso.get("/auth/sso/login", follow_redirects=False)
    flow = sso.cookies.get("tbt_sso_flow", path="/auth/sso")
    sso.cookies.clear()
    sso.cookies.set("tbt_sso_flow", flow[:-3] + "AAA", path="/auth/sso")
    assert sso.get("/auth/sso/callback?code=abc", follow_redirects=False).headers["location"] == expired
    sso.cookies.clear()
    sso.cookies.set("tbt_sso_flow", auth.sign_blob("sso-handoff", {"f": {}, "e": int(time.time()) + 60}),
                    path="/auth/sso")
    assert sso.get("/auth/sso/callback?code=abc", follow_redirects=False).headers["location"] == expired
    sso.cookies.clear()
    sso.cookies.set("tbt_sso_flow", auth.sign_blob("sso-flow", {"f": {"state": "x"}, "e": int(time.time()) - 5}),
                    path="/auth/sso")
    assert sso.get("/auth/sso/callback?code=abc", follow_redirects=False).headers["location"] == expired
    assert [call[0] for call in sso.microsoft.calls] == ["initiate"]


@pytest.mark.parametrize("failure,code", [
    (ValueError("state mismatch"), "expired"), (RuntimeError("nonce mismatch"), "failed"),
    (ConnectionError("login.microsoftonline.com is unreachable"), "unavailable"),
])
def test_a_token_exchange_that_fails_never_signs_anyone_in(sso, failure, code):
    sso.microsoft.failure = failure
    sso.get("/auth/sso/login", follow_redirects=False)
    response = sso.get("/auth/sso/callback?code=abc&state=state-1", follow_redirects=False)
    assert response.headers["location"] == f"/login#sso-error={code}"
    assert "tbt_sso_handoff" not in response.headers.get("set-cookie", "")
    assert sso.post("/api/auth/sso/handoff", headers=TAB).status_code == 401


def test_an_error_from_the_token_endpoint_and_an_unreachable_sign_in_page_are_reported(sso):
    sso.get("/auth/sso/login", follow_redirects=False)
    sso.microsoft.result = {"error": "invalid_grant", "error_description": "AADSTS70008: the code has expired"}
    assert sso.get("/auth/sso/callback?code=abc", follow_redirects=False).headers["location"] == "/login#sso-error=failed"

    def unreachable(*args, **kwargs):
        raise ConnectionError("no route to login.microsoftonline.com")
    sso.microsoft.initiate_auth_code_flow = unreachable
    assert sso.get("/auth/sso/login", follow_redirects=False).headers["location"] == "/login#sso-error=unavailable"


@pytest.mark.parametrize("changes,code", [
    ({"tid": "99999999-9999-4999-8999-999999999999"}, "tenant"),
    ({"aud": "someone-elses-application"}, "failed"),
    ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "failed"),
    ({"exp": time.time() - 60}, "failed"),
    ({"exp": "soon"}, "failed"),
    ({"oid": "not-a-guid"}, "failed"),
    ({"oid": None}, "failed"),
    ({"roles": None}, "no-role"),
    ({"roles": []}, "no-role"),
    ({"roles": ["User.Read", "Admin"]}, "no-role"),
    ({"roles": "ServiceManager"}, "no-role"),
])
def test_an_identity_is_accepted_only_for_this_tenant_this_app_and_a_service_role(sso, changes, code):
    arrived = callback(sso, **changes)
    assert arrived.headers["location"] == f"/login#sso-error={code}"
    assert "tbt_sso_handoff" not in arrived.headers.get("set-cookie", "")
    assert sso.post("/api/auth/sso/handoff", headers=TAB).status_code == 401


def test_somebody_without_a_service_role_is_recorded_but_never_given_a_session(sso):
    arrived = callback(sso, roles=[], name="Eve\nFake log line\x00", preferred_username="eve@contoso.example")
    assert arrived.headers["location"] == "/login#sso-error=no-role"
    manager = sign_in(sso)
    refused = [item for item in trail(sso, manager)["entries"] if item["action"] == "staff.sso-denied"]
    assert len(refused) == 1 and refused[0]["actor"] == "sso-refused" and "by" not in refused[0]
    assert "eve@contoso.example" in refused[0]["detail"]


def test_a_sign_in_becomes_one_session_that_names_the_person(sso):
    token = sign_in(sso)
    me = sso.get("/api/auth/me", headers=tab(token)).json()
    assert me["authenticated"] and me["role"] == "admin" and me["staffAuth"] == "sso"
    assert me["name"] == "Mira Manager"
    assert me["staff"] == {"name": "Mira Manager", "upn": PEOPLE["ServiceManager"]["upn"],
                           "role": "ServiceManager", "roleLabel": "Service manager"}
    assert sso.get("/api/state", headers=tab(token)).status_code == 200
    assert sso.get("/api/state", headers=TAB).status_code == 401
    anonymous = sso.get("/api/auth/me", headers=TAB).json()
    assert anonymous["authenticated"] is False and anonymous["staffAuth"] == "sso"
    session = auth.read_session(token)
    assert session["e"] - time.time() == pytest.approx(4 * 3600, abs=30)


def test_a_guest_from_another_organisation_is_named_by_the_address_they_signed_in_with(sso):
    token = sign_in(sso, "ServiceOperator", oid="44444444-4444-4444-8444-444444444444",
                    preferred_username="visitor@partner.example", name="Vera Visitor")
    me = sso.get("/api/auth/me", headers=tab(token)).json()
    assert me["staff"] == {"name": "Vera Visitor", "upn": "visitor@partner.example",
                           "role": "ServiceOperator", "roleLabel": "Service operator"}


def test_the_hand_off_cookie_works_once_and_only_as_a_hand_off(sso):
    arrived = callback(sso)
    handoff = arrived.cookies["tbt_sso_handoff"]
    cookie = arrived.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api/auth/sso" in cookie
    assert sso.post("/api/auth/sso/handoff", headers=TAB).status_code == 200
    replay = sso.post("/api/auth/sso/handoff", headers={**TAB, "Cookie": f"tbt_sso_handoff={handoff}"})
    assert replay.status_code == 401 and "session" not in replay.text
    flow = sso.get("/auth/sso/login", follow_redirects=False).cookies["tbt_sso_flow"]
    borrowed = sso.post("/api/auth/sso/handoff", headers={**TAB, "Cookie": f"tbt_sso_handoff={flow}"})
    assert borrowed.status_code == 401
    session = sso.post("/api/auth/sso/handoff", headers={**TAB, "Cookie": f"tbt_sso_handoff={handoff[:-3]}AAA"})
    assert session.status_code == 401
    no_header = {"Cookie": f"tbt_sso_handoff={handoff}", "X-Requested-With": ""}
    assert sso.post("/api/auth/sso/handoff", headers=no_header).status_code == 403


def test_a_browser_that_is_not_a_tab_receives_the_session_as_a_cookie(sso):
    callback(sso)
    handoff = sso.post("/api/auth/sso/handoff")
    assert handoff.status_code == 200 and "session" not in handoff.json()
    assert "httponly" in handoff.headers["set-cookie"].lower()
    assert sso.get("/api/state").status_code == 200
    assert sso.get("/api/auth/me").json()["staff"]["upn"] == PEOPLE["ServiceManager"]["upn"]


def test_tokens_and_old_sessions_are_not_a_way_in_to_the_sso_app(sso):
    assert sso.post("/api/auth/staff", json={"token": ADMIN}).status_code == 403
    assert sso.get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 403
    assert sso.post("/api/orders", json=order_body(), headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 403
    assert sso.get("/api/state").status_code == 401
    token_mode = auth.issue_session("admin", "staff", hashlib.sha256(ADMIN.encode()).hexdigest()[:12])
    assert sso.get("/api/state", headers=tab(token_mode)).status_code == 401
    nameless = auth.issue_session("admin", "staff", auth.staff_version())
    assert sso.get("/api/state", headers=tab(nameless)).status_code == 401
    person = person_of("ServiceManager")
    expired = auth.issue_session("admin", person["oid"], auth.staff_version(), seconds=-5, person=person)
    assert sso.get("/api/state", headers=tab(expired)).status_code == 401
    token = sign_in(sso, "ServiceAuditor")
    head, signature = token.split(".")
    session = auth.read_session(token)
    session["p"]["r"] = "ServiceManager"
    forged = auth._b64(json.dumps(session, separators=(",", ":")).encode())
    assert sso.get("/api/state", headers=tab(f"{forged}.{signature}")).status_code == 401
    assert sso.get("/api/state", headers=tab(token)).status_code == 200 and head


def test_the_partner_channel_token_still_serves_the_wholesale_api(sso):
    channel = {"Authorization": f"Bearer {PARTNER}"}
    placed = sso.post("/api/orders", json=order_body("channel-order-0001"), headers=channel)
    assert placed.status_code == 201 and placed.json()["actor"] == "wholesale-partner"
    assert sso.get("/api/state", headers=channel).status_code == 403
    assert sso.get("/api/audit", headers=channel).status_code == 403


def test_each_role_can_do_only_what_its_level_allows(sso):
    tokens = {role: tab(sign_in(sso, role)) for role in PEOPLE}
    auditor, operator, manager = tokens["ServiceAuditor"], tokens["ServiceOperator"], tokens["ServiceManager"]
    for headers in tokens.values():
        for path in ("/api/state", "/api/audit", "/api/audit.csv", "/api/analytics", "/api/analytics/source",
                     "/api/partners", "/tmf-api/serviceOrdering/v4/serviceOrder",
                     "/tmf-api/productOrderingManagement/v4/productOrder"):
            assert sso.get(path, headers=headers).status_code == 200, path

    refused = sso.post("/api/orders", json=order_body("role-order-0001"), headers=auditor)
    assert refused.status_code == 403 and "Service auditor" in refused.json()["detail"]
    _, acme = enroll(sso)
    assert sso.get("/api/enterprises", headers=auditor).status_code == 403
    assert place_for(sso, auditor, acme["id"], "role-order-0001").status_code == 403
    for headers in (operator, manager):
        assert sso.get("/api/enterprises", headers=headers).status_code == 200
    order_id = staff_order(sso, operator, acme["id"], "role-order-0002", scenario="human-exception")["id"]
    app.state.store.transact(lambda state: [process_next(state, order_id) for _ in range(5)])
    decision = {"json": {"decision": "approve"}}
    assert sso.post(f"/api/orders/{order_id}/decision", headers=auditor, **decision).status_code == 403
    assert sso.post(f"/api/orders/{order_id}/decision", headers=operator, **decision).status_code == 403
    assert sso.post(f"/api/orders/{order_id}/decision", headers=manager, **decision).status_code == 200

    assert sso.post("/api/analytics/benchmark", json={"size": 50}, headers=auditor).status_code == 403
    assert sso.post(f"/api/orders/{order_id}/advice", headers=auditor).status_code == 403
    assert sso.post(f"/api/orders/{order_id}/advice", headers=operator).status_code == 503
    partner = {"name": "Demo Balkan Wholesale 2", "contactName": "Deniz Kaya", "email": "deniz@wholesale.example"}
    assert sso.post("/api/partners", json=partner, headers=operator).status_code == 403
    assert sso.post("/api/partners", json=partner, headers=auditor).status_code == 403
    assert sso.post("/api/partners", json=partner, headers=manager).status_code == 201
    mcp = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert sso.post("/mcp", json=mcp, headers=auditor).status_code == 403
    assert sso.post("/mcp", json=mcp, headers=operator).status_code == 200


def test_staff_actions_carry_the_person_and_what_they_changed_and_the_chain_still_verifies(sso):
    operator, manager = sign_in(sso, "ServiceOperator"), sign_in(sso, "ServiceManager")
    _, acme = enroll(sso)
    placed = staff_order(sso, tab(operator), acme["id"], "trail-order-0001", scenario="human-exception")
    app.state.store.transact(lambda state: [process_next(state, placed["id"]) for _ in range(5)])
    assert sso.post(f"/api/orders/{placed['id']}/decision", json={"decision": "approve"},
                    headers=tab(manager)).status_code == 200

    report = trail(sso, manager)
    assert report["verified"] is True
    created = next(item for item in report["entries"] if item["action"] == "order.accepted")
    assert created["by"] == person_of("ServiceOperator") and created["actor"] == "service-manager"
    assert created["orderId"] == placed["id"] and len(created["hash"]) == 64
    asked = next(item for item in report["entries"] if item["action"] == "order.approval-requested")
    assert asked["by"] == person_of("ServiceOperator") and asked["orderId"] == placed["id"]
    agreed = next(item for item in report["entries"] if item["action"] == "order.enterprise-approved")
    assert agreed["actor"] == "enterprise-contact" and "by" not in agreed and agreed["orderId"] == placed["id"]
    decided = next(item for item in report["entries"] if item["action"] == "exception.approve")
    assert decided["by"] == person_of("ServiceManager")
    assert decided["changes"][0] == {"field": "exception (route-conflict)", "from": "awaiting decision",
                                     "to": "approved"}
    assert {row["upn"]: row["actions"] > 0 for row in report["people"]} == {
        PEOPLE["ServiceManager"]["upn"]: True, PEOPLE["ServiceOperator"]["upn"]: True}
    machine = next(item for item in report["entries"] if item["actor"].endswith("Agent"))
    assert "by" not in machine


def test_the_audit_trail_can_be_filtered_by_person_order_and_action(sso):
    operator, manager = sign_in(sso, "ServiceOperator"), sign_in(sso, "ServiceManager")
    _, acme = enroll(sso)
    first = place_for(sso, tab(operator), acme["id"], "filter-order-0001").json()
    second = place_for(sso, tab(manager), acme["id"], "filter-order-0002").json()
    mine = trail(sso, manager, person=PEOPLE["ServiceOperator"]["upn"].upper())
    assert mine["total"] == len(mine["entries"]) >= 2
    assert {item["by"]["upn"] for item in mine["entries"]} == {PEOPLE["ServiceOperator"]["upn"]}
    assert trail(sso, manager, person=PEOPLE["ServiceOperator"]["oid"])["total"] == mine["total"]
    assert trail(sso, manager, person="nobody@contoso.example")["entries"] == []
    only_second = trail(sso, manager, order=second["id"])
    assert {item["orderId"] for item in only_second["entries"]} == {second["id"]}
    accepted = trail(sso, manager, action="order.accepted")
    assert {item["orderId"] for item in accepted["entries"]} == {first["id"], second["id"]}
    newest = trail(sso, manager, limit=1)
    assert len(newest["entries"]) == 1 and newest["total"] > 1
    assert newest["entries"][0]["sequence"] == max(item["sequence"] for item in trail(sso, manager)["entries"])
    assert sso.get("/api/audit?limit=0", headers=tab(manager)).status_code == 422
    assert sso.get("/api/audit?limit=501", headers=tab(manager)).status_code == 422


def test_changing_who_did_something_after_the_fact_breaks_the_chain(sso):
    operator = sign_in(sso, "ServiceOperator")
    _, acme = enroll(sso)
    assert place_for(sso, tab(operator), acme["id"], "tamper-order-0001").status_code == 201
    assert trail(sso, operator)["verified"] is True

    def blame_someone_else(state):
        entry = next(item for item in state["audit"] if item["action"] == "order.accepted")
        entry["by"]["upn"] = PEOPLE["ServiceManager"]["upn"]
    app.state.store.transact(blame_someone_else)
    assert trail(sso, operator)["verified"] is False


def test_sign_in_and_sign_out_are_audited_under_the_person_and_not_under_any_order(sso):
    token = sign_in(sso, "ServiceOperator")
    assert sso.post("/api/auth/logout", headers=tab(token)).status_code == 200
    events = [item for item in trail(sso, token)["entries"] if item["action"].startswith("staff.")]
    assert [item["action"] for item in events] == ["staff.signed-out", "staff.signed-in"]
    assert {item["by"]["oid"] for item in events} == {PEOPLE["ServiceOperator"]["oid"]}
    assert {item["orderId"] for item in events} == {"staff-" + PEOPLE["ServiceOperator"]["oid"].replace("-", "")}
    assert events[1]["detail"].startswith("Signed in with Microsoft from ")


def test_the_csv_export_adds_who_and_what_changed(sso):
    operator, manager = sign_in(sso, "ServiceOperator"), sign_in(sso, "ServiceManager")
    _, acme = enroll(sso)
    placed = staff_order(sso, tab(operator), acme["id"], "csv-order-0001", scenario="human-exception")
    app.state.store.transact(lambda state: [process_next(state, placed["id"]) for _ in range(5)])
    sso.post(f"/api/orders/{placed['id']}/decision", json={"decision": "reject"}, headers=tab(manager))
    reply = sso.get("/api/audit.csv", headers=tab(manager))
    rows = list(csv.DictReader(io.StringIO(reply.text)))
    assert list(rows[0]) == ["timestamp", "order", "actor", "action", "detail", "done_by", "upn", "role", "changes"]
    accepted = next(row for row in rows if row["action"] == "order.accepted")
    assert (accepted["done_by"], accepted["upn"], accepted["role"]) == (
        "Ömer Operatör", PEOPLE["ServiceOperator"]["upn"], "ServiceOperator")
    rejected = next(row for row in rows if row["action"] == "exception.reject")
    assert json.loads(rejected["changes"])[0]["to"] == "rejected"
    assert next(row for row in rows if row["actor"].endswith("Agent"))["done_by"] == ""


def test_staff_actions_are_also_written_to_the_container_log_after_they_are_saved(sso):
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())
    handler = Collect()
    logging.getLogger("ama.audit").addHandler(handler)
    try:
        operator = sign_in(sso, "ServiceOperator")
        _, acme = enroll(sso)
        assert place_for(sso, tab(operator), acme["id"], "log-order-0001").status_code == 201
        assert place_for(sso, tab(operator), acme["id"], "log-order-0001", city="Ankara").status_code == 409
    finally:
        logging.getLogger("ama.audit").removeHandler(handler)
    entries = [json.loads(line) for line in lines]
    accepted = [item for item in entries if item["action"] == "order.accepted"]
    assert len(accepted) == 1, "a rejected request must not be logged as done"
    assert accepted[0]["log"] == "staff-audit" and accepted[0]["by"] == person_of("ServiceOperator")
    asked = [item for item in entries if item["action"] == "order.approval-requested"]
    assert len(asked) == 1 and asked[0]["by"] == person_of("ServiceOperator")
    assert [item["action"] for item in entries][0] == "staff.signed-in"
    assert all("\n" not in line for line in lines)


def test_the_access_log_keeps_the_callback_code_and_state_out_of_the_log():
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())
    access = logging.getLogger("uvicorn.access")
    handler, level = Collect(), access.level
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    try:
        for path in ("/auth/sso/callback?code=SECRET-CODE&state=SECRET-STATE&error_description=mira@contoso.example",
                     "/auth/sso/callback", "/api/audit?person=mira", "/auth/sso/callbackx?code=1"):
            access.info('%s - "%s %s HTTP/%s" %d', "10.0.0.1:5000", "GET", path, "1.1", 302)
    finally:
        access.removeHandler(handler)
        access.setLevel(level)
    assert lines == ['10.0.0.1:5000 - "GET /auth/sso/callback HTTP/1.1" 302',
                     '10.0.0.1:5000 - "GET /auth/sso/callback HTTP/1.1" 302',
                     '10.0.0.1:5000 - "GET /api/audit?person=mira HTTP/1.1" 302',
                     '10.0.0.1:5000 - "GET /auth/sso/callbackx?code=1 HTTP/1.1" 302']


def test_nothing_that_names_staff_reaches_customers_or_partners(sso):
    operator, manager = sign_in(sso, "ServiceOperator"), sign_in(sso, "ServiceManager")
    created = sso.post("/api/partners", json={"name": "Demo Balkan Wholesale 2", "contactName": "Deniz Kaya",
                                              "email": "deniz@wholesale.example", "paymentTermsDays": 14},
                       headers=tab(manager)).json()
    contact = new_client()
    assert contact.post("/api/auth/invite/accept", json={
        "token": created["invite"]["token"], "password": PASSWORD}).status_code == 200
    order = sso.post(f"/api/partners/{created['partner']['id']}/orders",
                     json=order_body("partner-order-0001"), headers=tab(operator))
    assert order.status_code == 201
    order_id = order.json()["id"]
    concern = contact.post("/api/partner/concerns", json={
        "category": "delivery", "orderId": order_id, "subject": "Where is it", "text": "Please update us"})
    assert concern.status_code == 201
    reply = sso.post(f"/api/concerns/{concern.json()['id']}/messages", json={"text": "We are on it"},
                     headers=tab(operator))
    assert reply.status_code == 201
    assert sso.put(f"/api/concerns/{concern.json()['id']}/status", json={"status": "resolved"},
                   headers=tab(sign_in(sso, "ServiceAuditor"))).status_code == 403
    assert sso.put(f"/api/concerns/{concern.json()['id']}/status", json={"status": "resolved"},
                   headers=tab(operator)).status_code == 200

    seen = [contact.get("/api/partner/overview").text, contact.get(f"/api/partner/deliveries/{order_id}").text,
            sso.get(f"/api/portal/orders/{order_id}", headers={"X-Tracking-Token": tracking_token(order_id)}).text]
    for text in seen:
        for person in PEOPLE.values():
            assert person["upn"] not in text and person["name"] not in text and person["oid"] not in text
    people = trail(sso, manager)["people"]
    assert {row["upn"] for row in people} >= {PEOPLE["ServiceOperator"]["upn"], PEOPLE["ServiceManager"]["upn"]}
    replied = next(item for item in trail(sso, manager)["entries"] if item["action"] == "partner.concern-reply")
    assert replied["by"] == person_of("ServiceOperator") and replied["changes"][0]["to"] == "inProgress"
    closed = next(item for item in trail(sso, manager)["entries"] if item["action"] == "partner.concern-status")
    assert closed["by"] == person_of("ServiceOperator") and closed["changes"][0]["to"] == "resolved"


def test_customers_keep_signing_in_with_their_own_credentials(sso):
    assert register(sso).status_code == 201
    assert sso.get("/api/auth/me").json()["role"] == "customer"
    placed = sso.post("/api/me/orders", json=order_body("customer-order-0001"))
    assert placed.status_code == 201
    for path in ("/api/state", "/api/audit", "/api/audit.csv", "/api/partners"):
        assert sso.get(path).status_code == 403, path
    assert sso.post("/api/auth/logout").status_code == 200
    login = sso.post("/api/auth/login", json={"email": "ayse@acme.example", "password": PASSWORD})
    assert login.status_code == 200
    assert [item["id"] for item in sso.get("/api/me/orders").json()["orders"]] == [placed.json()["id"]]
    assert CONTACT["email"] == "ayse@acme.example"


def test_staff_sessions_last_four_hours_by_default_and_never_more_than_twelve(monkeypatch):
    monkeypatch.delenv("AMA_STAFF_SESSION_HOURS", raising=False)
    assert auth.staff_session_seconds() == 4 * 3600
    for value, seconds in (("1", 3600), ("100", 12 * 3600), ("0", 900), ("junk", 4 * 3600)):
        monkeypatch.setenv("AMA_STAFF_SESSION_HOURS", value)
        assert auth.staff_session_seconds() == seconds
