import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from azure.core.exceptions import ClientAuthenticationError
from fastapi.testclient import TestClient

from app.domain import process_next
from app import advisor
from app.main import app
from app.security import _requests
from app.store import Store
from tests.test_domain import payload

ADMIN = "test-manager-token-not-a-real-secret-00001"
PARTNER = "test-partner-token-not-a-real-secret-00001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "3600")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    _requests.clear()
    with TestClient(app) as test_client:
        yield test_client


def test_public_read_and_write_authorization(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    state = client.get("/api/state").json()
    assert state["configuration"]["productionCompliant"] is False
    assert "Token" not in json.dumps(state)
    assert client.post("/api/orders", json=payload()).status_code == 401
    assert client.post("/api/orders", json=payload(),
                       headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/audit.csv").status_code == 401
    assert client.get("/static/app.js").headers["content-security-policy"].startswith("default-src 'self'")


def test_read_only_animation_assets(client):
    page = client.get("/static/journey.html")
    assert page.status_code == 200
    assert "The waiting order" in page.text
    assert "/static/journey.html" in client.get("/").text
    for name in ("journey.js", "journey.css", "journey-data.json",
                 "current-scenario-poster.jpg", "current-scenario.vtt"):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
    data = client.get("/static/journey-data.json").json()
    assert data["scope"] == "Current scenario and challenges only"
    assert len(data["scenes"]) == 9
    video = client.get("/static/current-scenario.mp4", headers={"Range": "bytes=0-1023"})
    assert video.status_code == 206
    assert video.headers["content-type"] == "video/mp4"
    assert len(video.content) == 1024


def test_api_idempotency_validation_and_advice_not_faked(client):
    headers = {"Authorization": f"Bearer {ADMIN}"}
    response = client.post("/api/orders", json=payload(), headers=headers)
    assert response.status_code == 201
    order = response.json()
    assert client.post("/api/orders", json=payload(), headers=headers).json()["id"] == order["id"]
    invalid = payload()
    invalid["product"] = "invalid"
    assert client.post("/api/orders", json=invalid, headers=headers).status_code == 422
    assert client.post(f"/api/orders/{order['id']}/advice", headers=headers).status_code == 503
    invalid = payload()
    invalid["product"] = "cloud"
    assert client.post("/api/orders", json=invalid, headers=headers).status_code == 409


def test_demo_operator_and_partner_api_orders_accept_names_without_a_prefix(client, monkeypatch):
    monkeypatch.setenv("AMA_MODE", "demo")
    for token in (ADMIN, PARTNER):
        body = {**payload(key="unprefixed-order-" + token[:12]), "customer": "Anatolia Retail"}
        response = client.post("/api/orders", json=body, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 201, response.text
        assert response.json()["customer"] == "Anatolia Retail"


def test_partner_cannot_review_or_read_operator_orders(client):
    admin = {"Authorization": f"Bearer {ADMIN}"}
    partner = {"Authorization": f"Bearer {PARTNER}"}
    order = client.post("/api/orders", json=payload("human-exception"), headers=admin).json()
    app.state.store.transact(lambda state: [process_next(state, order["id"]) for _ in range(5)])
    assert client.post(f"/api/orders/{order['id']}/decision", json={"decision": "approve"},
                       headers=partner).status_code == 403
    assert client.get(f"/tmf-api/productOrderingManagement/v4/productOrder/{order['id']}",
                      headers=partner).status_code == 403
    response = client.post(f"/api/orders/{order['id']}/decision",
                           json={"decision": "approve"}, headers=admin)
    assert response.status_code == 200


def test_mcp_initialize_list_tools_and_submit(client):
    headers = {"Authorization": f"Bearer {PARTNER}", "Accept": "application/json, text/event-stream"}
    def rpc(method, params=None, identifier=1):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": identifier, "method": method,
                                       "params": params or {}}, headers=headers)
    result = rpc("initialize", {"protocolVersion": "2025-03-26",
                               "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}).json()
    assert result["result"]["protocolVersion"] == "2025-03-26"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers=headers).status_code == 202
    assert len(rpc("tools/list").json()["result"]["tools"]) == 2
    capacity = rpc("tools/call", {"name": "check_partner_capacity", "arguments": {"city": "Istanbul"}})
    assert json.loads(capacity.json()["result"]["content"][0]["text"])["simulated"] is True
    submitted = rpc("tools/call", {"name": "submit_partner_order", "arguments": payload()}).json()
    order = json.loads(submitted["result"]["content"][0]["text"])
    assert order["actor"] == "wholesale-partner"
    assert rpc("tools/call", {"name": "approve_change", "arguments": {}}).json()["result"]["isError"] is True
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       headers={**headers, "Origin": "https://attacker.invalid"}).status_code == 403


def test_local_snapshot_restart_and_transaction_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    store = Store()
    store.transact(lambda state: state.update(counter=0))
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: store.transact(lambda state: state.update(counter=state["counter"] + 1)), range(25)))
    assert Store().read()["counter"] == 25
    def failed(state):
        state["counter"] = 999
        raise ValueError("aborted")
    with pytest.raises(ValueError, match="aborted"):
        store.transact(failed)
    assert Store().read()["counter"] == 25


def test_invalid_rpc_shape_returns_protocol_error(client):
    headers = {"Authorization": f"Bearer {PARTNER}"}
    response = client.post("/mcp", json=[], headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": []},
                           headers=headers)
    assert response.json()["error"]["code"] == -32600


def test_ai_is_advisory_and_daily_cap_is_persisted(client, monkeypatch):
    headers = {"Authorization": f"Bearer {ADMIN}"}
    order = client.post("/api/orders", json=payload(), headers=headers).json()
    monkeypatch.setattr(advisor, "configured", lambda: True)
    monkeypatch.setattr(advisor, "advise", lambda order: {
        "diagnosis": "Simulated route review needed", "recommendedAction": "request_human_review",
        "customerMessageEn": "Pending review", "customerMessageTr": "İnceleme bekleniyor",
        "mode": "live-regional-azure-openai", "executionAuthority": False,
    })
    for _ in range(30):
        assert client.post(f"/api/orders/{order['id']}/advice", headers=headers).status_code == 200
    assert client.post(f"/api/orders/{order['id']}/advice", headers=headers).status_code == 409
    current = client.get("/api/state").json()["orders"][order["id"]]
    assert not current["reserved"]
    assert not current["approvals"]
    assert client.get("/api/state").json()["metrics"]["auditChainValid"]


def test_oversized_mutation_is_rejected(client):
    response = client.post("/mcp", content=b"x" * 20000,
                           headers={"Authorization": f"Bearer {PARTNER}", "Content-Type": "application/json"})
    assert response.status_code == 413


def test_failed_live_ai_does_not_fake_advice_or_execute(client, monkeypatch):
    headers = {"Authorization": f"Bearer {ADMIN}"}
    order = client.post("/api/orders", json=payload(), headers=headers).json()
    monkeypatch.setattr(advisor, "configured", lambda: True)
    def failure(order):
        raise ClientAuthenticationError("Synthetic test authentication failure")
    monkeypatch.setattr(advisor, "advise", failure)
    response = client.post(f"/api/orders/{order['id']}/advice", headers=headers)
    assert response.status_code == 502
    current = client.get("/api/state").json()["orders"][order["id"]]
    assert "aiAdvice" not in current and not current["reserved"]


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_foundry_agent_receives_only_workflow_fields_and_cannot_execute(client, monkeypatch, status):
    import json
    import httpx

    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://demo.services.ai.azure.com/api/projects/activation")
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", "order-advisor")
    headers = {"Authorization": f"Bearer {ADMIN}"}
    order = client.post("/api/orders", json=payload(), headers=headers).json()
    actual_client = httpx.Client

    class Credential:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_token(self, scope):
            assert scope == "https://ai.azure.com/.default"
            return type("Token", (), {"token": "test-managed-identity"})()

    def foundry(request):
        assert request.url.path.endswith("/agents/order-advisor/endpoint/protocols/openai/responses")
        assert request.url.params["api-version"] == "v1"
        assert request.headers["Authorization"] == "Bearer test-managed-identity"
        body = json.loads(request.content)
        assert not body["store"]
        context = json.loads(body["input"][0]["content"].split("\n")[-1])
        assert set(context) == {"state", "stage", "product", "scenario", "exceptionType"}
        text = json.dumps({"diagnosis": "Review the route", "recommendedAction": "request_human_review",
                           "customerMessageEn": "Under review", "customerMessageTr": "Inceleme bekleniyor"})
        return httpx.Response(200, json={"status": status, "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]})

    monkeypatch.setattr(advisor, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(advisor.httpx, "Client",
                        lambda **kwargs: actual_client(transport=httpx.MockTransport(foundry), **kwargs))
    response = client.post(f"/api/orders/{order['id']}/advice", headers=headers)
    snapshot = client.get("/api/state").json()
    assert snapshot["configuration"]["aiMode"] == "Microsoft Foundry agent"
    current = snapshot["orders"][order["id"]]
    assert not current["reserved"] and not current["approvals"]
    if status == "completed":
        assert response.status_code == 200 and response.json()["executionAuthority"] is False
        assert current["aiAdvice"]["mode"] == "live-foundry-agent"
    else:
        assert response.status_code == 502 and "aiAdvice" not in current


def test_contact_is_validated_and_never_exposed_unmasked(client):
    headers = {"Authorization": f"Bearer {ADMIN}"}
    body = {**payload(key="api-contact-order"), "contact": {
        "contactName": "Demo Contact", "email": "ayse@example.com", "preferredChannel": "email",
        "updateFrequency": "daily"}}
    created = client.post("/api/orders", json=body, headers=headers)
    assert created.status_code == 201 and created.json()["contact"]["email"] == "a***@example.com"
    assert "ayse@example.com" not in client.get("/api/state").text
    for bad in ({"preferredChannel": "sms"}, {"email": "not-an-email"}, {"updateFrequency": "hourly"}):
        rejected = client.post("/api/orders", json={**payload(key="api-bad-contact"),
                              "contact": {**body["contact"], **bad}}, headers=headers)
        assert rejected.status_code == 422
