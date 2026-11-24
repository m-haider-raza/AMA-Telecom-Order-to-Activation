import json

import httpx
import pytest

from scripts import Deploy_Foundry as deployment


@pytest.mark.parametrize("group,account", [
    ("rg-amai-isolated-20261123", "amaif20261123sc"),
    ("rg-amai-foundry-test", "old-account"),
])
def test_refuses_unscoped_targets(tmp_path, group, account):
    with pytest.raises(ValueError):
        deployment.bootstrap(group, account, tmp_path / "receipt.json")


def test_refuses_wrong_tenant_before_creating_resources(tmp_path, monkeypatch):
    calls = []

    def az(*args):
        calls.append(args)
        return {"id": deployment.SUBSCRIPTION, "tenantId": "wrong"}

    monkeypatch.setattr(deployment, "az", az)
    with pytest.raises(ValueError, match="tenant"):
        deployment.bootstrap("rg-amai-foundry-test", "amaif123456", tmp_path / "receipt.json")
    assert calls == [("account", "show")]


def test_account_failure_is_persisted_without_creating_projects_or_roles(tmp_path, monkeypatch):
    calls = []

    def az(*args):
        calls.append(args)
        if args == ("account", "show"):
            return {"id": deployment.SUBSCRIPTION, "tenantId": deployment.TENANT}
        if args[:2] == ("group", "exists"):
            return False
        if args[:2] == ("group", "create"):
            return {}
        pytest.fail(f"Unexpected operation after account failure: {args[:2]}")

    requests = []

    def fail(url, body):
        requests.append((url, body))
        raise RuntimeError("HTTP500 correlation=synthetic-test")

    monkeypatch.setattr(deployment, "az", az)
    monkeypatch.setattr(deployment, "put", fail)
    path = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError, match="HTTP500"):
        deployment.bootstrap("rg-amai-foundry-test", "amaif123456", path)
    receipt = json.loads(path.read_text())
    assert receipt["status"] == "blocked"
    assert receipt["originalStacksTouched"] is False
    assert receipt["blocker"] == "HTTP500 correlation=synthetic-test"
    assert len(requests) == 1
    assert requests[0][1]["properties"]["disableLocalAuth"] is True
    assert "pilotProject" not in receipt
    assert "deploymentRole" not in receipt


def test_arm_failure_preserves_correlation_without_token(monkeypatch):
    class Credential:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_token(self, scope):
            return type("Token", (), {"token": "synthetic-secret-never-log"})()

    monkeypatch.setattr(deployment, "AzureCliCredential", Credential)
    monkeypatch.setattr(deployment.httpx, "request", lambda *args, **kwargs: httpx.Response(
        403, headers={"x-ms-request-id": "request-123", "x-ms-correlation-request-id": "correlation-123"},
        json={"error": {"code": "RequestDisallowedByAzure"}}))
    with pytest.raises(RuntimeError) as failure:
        deployment.put("https://management.azure.com/synthetic", {})
    assert "synthetic-secret-never-log" not in str(failure.value)
    error = json.loads(str(failure.value))
    assert error["httpStatus"] == 403
    assert error["requestId"] == "request-123"
    assert error["correlationId"] == "correlation-123"
