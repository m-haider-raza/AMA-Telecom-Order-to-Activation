import copy
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.isolated.identity import EntraVerifier
from app.isolated.migration import digest, import_snapshot
from app.isolated.store import LocalStore
from app.security import _requests
from scripts.Deploy_Foundry_App import AGENTS, foundry_bindings
from tests.test_isolated import verifier  # noqa: F401
from tests.test_sso import CLIENT, PUBLIC, TENANT, Microsoft, identity

FALLBACK = "synthetic-onboarding-operator-credential-1234567890"


def configure(monkeypatch, tmp_path, entra_verifier):
    for key in ("AMA_SQL_SERVER", "AMA_SQL_DATABASE", "AZURE_STORAGE_ACCOUNT_URL", "AMA_SQL_ADMIN_TOKEN",
                "ISOLATED_STORAGE_URL", "AMA_TRACKING_SECRET"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "AMA_MODE": "demo", "AMA_DELIVERY": "simulated", "AMA_STAFF_AUTH": "sso+token",
        "AMA_STAFF_ACCESS_TOKEN": FALLBACK, "DEMO_ADMIN_TOKEN": "unused-manager-" + "x" * 30,
        "DEMO_PARTNER_TOKEN": "new-partner-" + "y" * 30, "AMA_SESSION_SECRET": "new-session-" + "z" * 40,
        "AMA_SSO_TENANT_ID": TENANT, "AMA_SSO_CLIENT_ID": CLIENT,
        "AMA_SSO_CLIENT_SECRET": "synthetic-local-oidc-secret", "AMA_PUBLIC_ORIGIN": PUBLIC,
        "ISOLATED_LOCAL": "1", "ISOLATED_LOCAL_PATH": str(tmp_path / "api.sqlite"), "ISOLATED_FOUNDRY": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(EntraVerifier, "from_env", classmethod(lambda cls: entra_verifier))
    auth.reset_throttles()
    _requests.clear()


def test_isolated_app_supports_both_staff_logins_without_granting_token_manager_privileges(
        tmp_path, monkeypatch, verifier):  # noqa: F811
    configure(monkeypatch, tmp_path, verifier)
    microsoft = Microsoft()
    monkeypatch.setattr("app.sso.msal_app", lambda: microsoft)
    from app.isolated.api import app
    tab = {"X-Session-Mode": "tab"}
    with TestClient(app) as client:
        client.headers.update({"Origin": PUBLIC, "X-Requested-With": "fetch"})
        response = client.post("/api/auth/staff", headers=tab, json={"token": FALLBACK})
        assert response.status_code == 200
        operator = {**tab, "X-Session": response.json()["session"]}
        me = client.get("/api/auth/me", headers=operator).json()
        assert me["foundryArchitecture"] and me["staffAuth"] == "sso+token"
        assert me["staff"]["role"] == "ServiceOperator"
        state = client.get("/api/state", headers=operator)
        assert state.status_code == 200
        assert state.json()["configuration"]["architecture"] == "foundry-hosted-isolated-v1"
        assert client.post("/api/partners", headers=operator, json={
            "name": "Demo No Authority", "contactName": "Test Contact", "email": "test@example.invalid",
        }).status_code == 403
        assert client.post("/internal/dispatch", headers=operator).status_code == 401
        assert client.post("/api/orders", headers=operator, json={
            "customer": "Demo Enterprise", "city": "Istanbul", "product": "sdwan",
            "scenario": "happy", "language": "en", "idempotencyKey": "no-bypass",
        }).status_code == 409
        assert client.get("/auth/sso/login", follow_redirects=False).status_code == 302
        microsoft.result = identity()
        callback = client.get("/auth/sso/callback?code=abc&state=state-1", follow_redirects=False)
        assert callback.headers["location"] == "/login#sso"
        manager_token = client.post("/api/auth/sso/handoff", headers=tab).json()["session"]
        manager = {**tab, "X-Session": manager_token}
        assert client.get("/api/auth/me", headers=manager).json()["staff"]["role"] == "ServiceManager"
        monkeypatch.setenv("AMA_STAFF_ACCESS_TOKEN", FALLBACK + "-rotated")
        assert client.get("/api/state", headers=operator).status_code == 401
        assert client.get("/api/state", headers=manager).status_code == 200


def test_isolated_dual_login_refuses_weak_fallback_at_startup(tmp_path, monkeypatch, verifier):  # noqa: F811
    configure(monkeypatch, tmp_path, verifier)
    monkeypatch.setenv("AMA_STAFF_ACCESS_TOKEN", "short")
    from app.isolated.api import app
    with pytest.raises(RuntimeError, match="AMA_STAFF_ACCESS_TOKEN"), TestClient(app):
        pass


def settled(store):
    snapshot = store.read()
    snapshot.update(orders={}, events=[], audit=[], isolatedJobs={},
                    customers={"C-1": {"passwordHash": "preserved-hash"}}, concerns={"Q-1": {"text": "original"}})
    return snapshot


def test_full_snapshot_copy_preserves_data_chain_and_source_without_duplicate_import(tmp_path):
    store = LocalStore(tmp_path / "target.sqlite")
    before = store.read()
    source = settled(store)
    source["inventory"][0]["availableUnits"] = 42
    untouched = copy.deepcopy(source)
    result = import_snapshot(store, source, "source-boundary", digest(before))
    copied = store.read()
    assert source == untouched
    assert copied["customers"] == source["customers"] and copied["inventory"] == source["inventory"]
    assert copied["concerns"] == source["concerns"]
    from app.domain import verify_audit
    assert verify_audit(copied)
    assert import_snapshot(store, source, "source-boundary", digest(before)) == result
    assert store.read() == copied


def test_snapshot_copy_refuses_changed_destination_and_invalid_source(tmp_path):
    store = LocalStore(tmp_path / "target.sqlite")
    before = store.read()
    source = settled(store)
    with pytest.raises(ValueError, match="Destination changed"):
        import_snapshot(store, source, "source", "wrong")
    assert store.read() == before
    source["audit"] = [{"hash": "invalid"}]
    with pytest.raises((ValueError, KeyError)):
        import_snapshot(store, source, "source", digest(before))
    assert store.read() == before


def test_snapshot_copy_waits_for_inflight_orders_without_discarding_them(tmp_path):
    store = LocalStore(tmp_path / "target.sqlite")
    source = store.read()
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="in-flight"):
        import_snapshot(store, source, "source", digest(before))
    assert store.read() == before


def test_existing_foundry_bindings_are_narrow_and_cannot_target_another_project():
    source = {"tenantId": TENANT, "subscriptionId": CLIENT, "foundry": {"bindings": []}}
    for agent in AGENTS:
        project = agent.replace("_", "-")
        source["foundry"]["bindings"].append({
            "agent": agent, "role": "Foundry Agent Consumer",
            "scope": f"/subscriptions/{CLIENT}/resourceGroups/rg-amai-foundry-source/"
                     f"providers/Microsoft.CognitiveServices/accounts/amaif123/projects/{project}",
            "endpoint": f"https://amaif123.services.ai.azure.com/api/projects/{project}/agents/amai-{project}"
                        "/endpoint/protocols/invocations?api-version=v1"})
    assert set(foundry_bindings(source, TENANT, CLIENT)) == set(AGENTS)
    source["foundry"]["bindings"][0]["endpoint"] = source["foundry"]["bindings"][1]["endpoint"]
    with pytest.raises(ValueError, match="narrow"):
        foundry_bindings(source, TENANT, CLIENT)
