import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import _requests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_live_demo", ROOT / "scripts" / "Verify_Demo.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
MANAGER = "local-test-manager-token-not-a-secret-000"
PARTNER = "local-test-partner-token-not-a-secret-000"


@pytest.fixture
def live_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "live-demo.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", MANAGER)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "0.01")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("DEMO_PUBLIC_ORIGIN", raising=False)
    _requests.clear()
    with TestClient(app) as client:
        yield client


def test_six_live_routes_preserve_holds_and_verify_all_five_specialists(live_client):
    report = verifier.verify_demo(live_client, MANAGER, PARTNER,
                                  allow_manager_decisions=True, timeout=15, poll_seconds=.02)
    assert report["status"] == "passed" and len(report["cases"]) == 6
    assert report["idempotentAcceptanceVerified"] and report["auditChainValid"]
    cases = {case["name"]: case for case in report["cases"]}
    assert cases["manager-rejection"]["state"] == "rejected"
    assert cases["timeout-recovery"]["order"]["recoveryAttempts"] == 1
    for name in ("manager-approval", "manager-rejection", "window-release", "customer-correction"):
        assert cases[name]["holdObserved"]["reserved"] is False
        assert cases[name]["inputRecorded"]
    assert MANAGER not in json.dumps(report) and PARTNER not in json.dumps(report)


def test_demo_decisions_need_explicit_permission_before_any_write(live_client):
    before = live_client.get("/api/state").json()["orders"]
    with pytest.raises(ValueError, match="Explicitly allow"):
        verifier.verify_demo(live_client, MANAGER, PARTNER)
    assert set(live_client.get("/api/state").json()["orders"]) == set(before)


def test_live_walkthrough_assets_obey_existing_content_security_policy(live_client):
    for filename in ("live-demo.html", "live-demo.js", "live-demo.css"):
        response = live_client.get(f"/static/{filename}")
        assert response.status_code == 200
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
    page = live_client.get("/static/live-demo.html").text
    assert "not autonomous LLM execution" in page
    assert "Holds are never released automatically" in page
    assert "live-demo.html" in live_client.get("/").text


def test_mcp_uses_explicit_https_origin_behind_ingress_without_trusting_forwarded_headers(live_client, monkeypatch):
    monkeypatch.setenv("DEMO_PUBLIC_ORIGIN", "https://demo.example.test")
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Authorization": f"Bearer {PARTNER}", "Origin": "https://demo.example.test"}
    assert live_client.post("/mcp", json=message, headers=headers).status_code == 200
    for origin in ("http://testserver", "http://demo.example.test", "https://attacker.invalid", "null"):
        response = live_client.post("/mcp", json=message, headers={
            **headers, "Origin": origin, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "attacker.invalid",
        })
        assert response.status_code == 403


@pytest.mark.parametrize("origin", [
    "http://demo.example.test", "https://demo.example.test/path", "https://user:password@demo.example.test",
    "https://demo.example.test?unexpected=true", "https://demo.example.test:invalid", "https://[invalid",
])
def test_invalid_public_origin_is_a_visible_startup_error(monkeypatch, origin):
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", MANAGER)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_PUBLIC_ORIGIN", origin)
    with pytest.raises(RuntimeError, match="DEMO_PUBLIC_ORIGIN"):
        with TestClient(app):
            pass
