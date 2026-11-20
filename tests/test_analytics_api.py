"""The analytics endpoints: who may read the report, where its data comes from, and the benchmark trigger."""

import pytest
from fastapi.testclient import TestClient

from app import benchmark
from app.main import app
from app.security import _requests
from tests.test_accounts import ADMIN, PARTNER, new_client, register
from tests.test_accounts import live as live_site  # noqa: F401  (the live-mode fixture)

BEARER = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "3600")
    for name in ("AZURE_STORAGE_ACCOUNT_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
                 "AMA_SQL_SERVER", "AMA_SQL_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    _requests.clear()
    with TestClient(app) as test_client:
        yield test_client


def test_the_demo_report_is_public_and_checks_its_scope(client):
    report = client.get("/api/analytics")
    assert report.status_code == 200
    body = report.json()
    assert {"scope", "scopes", "sample", "cycle", "agents", "fallout", "scenarios", "notes"} <= set(body)
    assert (body["cycle"]["baselineDays"], body["cycle"]["targetDays"]) == (35, 7)
    assert (body["fallout"]["baselinePercent"], body["fallout"]["targetPercent"]) == (30.0, 8.0)
    empty = client.get("/api/analytics?scope=benchmark").json()
    assert empty["scope"] == "benchmark" and empty["sample"]["orders"] == 0
    assert client.get("/api/analytics?scope=everything").status_code == 422


def test_the_data_source_says_where_the_numbers_come_from(client):
    source = client.get("/api/analytics/source").json()
    assert source["kind"] == "local-file" and "Azure SQL is not configured" in source["note"]


def test_the_console_has_an_analytics_tab_wired_to_its_own_script(client):
    html = client.get("/console").text
    assert 'data-page="analytics"' in html and 'id="analytics-page"' in html
    script = html.split('src="/static/analytics.js', 1)[1].split('"', 1)[0]
    asset = client.get(f"/static/analytics.js{script}")
    assert asset.status_code == 200 and "/api/analytics" in asset.text
    assert 'style="' not in html.split('id="analytics-page"', 1)[1].split("</section>", 1)[0]
    assert "setAttribute(\"style\"" not in asset.text and "innerHTML" not in asset.text


def test_the_sql_inspection_is_passed_through_but_its_errors_are_not(client, monkeypatch):
    store = app.state.store
    monkeypatch.setattr(store, "inspect", lambda: {"server": "example", "inSync": True}, raising=False)
    assert client.get("/api/analytics/source").json() == {"kind": "azure-sql", "server": "example", "inSync": True}

    def broken():
        raise RuntimeError("Login failed for user 'ama30-identity' on 10.245.0.36")

    monkeypatch.setattr(store, "inspect", broken, raising=False)
    failed = client.get("/api/analytics/source")
    assert failed.json() == {"kind": "azure-sql", "error": "The database could not be inspected right now"}
    assert "10.245" not in failed.text and "ama30-identity" not in failed.text


def test_only_the_service_manager_can_start_a_benchmark_and_only_one_runs_at_a_time(client):
    assert client.post("/api/analytics/benchmark", json={}).status_code == 401
    for token in ("wrong", PARTNER):
        assert client.post("/api/analytics/benchmark", json={},
                           headers={"Authorization": f"Bearer {token}"}).status_code == 403
    for body in ({"size": 7}, {"size": 100, "orders": 5}, {"size": "many"}):
        assert client.post("/api/analytics/benchmark", json=body, headers=BEARER).status_code == 422

    started = client.post("/api/analytics/benchmark", json={}, headers=BEARER)
    assert started.status_code == 202
    run = started.json()
    assert run["id"].startswith("BM-") and run["size"] == 100 and run["status"] == "running"
    again = client.post("/api/analytics/benchmark", json={"size": 50}, headers=BEARER)
    assert again.status_code == 409 and "already in progress" in again.json()["detail"]

    queued = client.get("/api/analytics?scope=benchmark").json()
    assert queued["sample"]["orders"] == 100 and queued["sample"]["inFlight"] == 100
    state = client.get("/api/state")
    assert "benchmark" not in state.json() and "Demo Benchmark" not in state.text


def test_a_finished_benchmark_becomes_the_default_view_and_meets_the_targets(client):
    assert client.post("/api/analytics/benchmark", json={"size": 50}, headers=BEARER).status_code == 202
    store = app.state.store
    store.transact(lambda state: state["benchmark"]["run"].update(humanDelaySeconds=0))
    for _ in range(60):
        store.transact(benchmark.advance)
        if store.read()["benchmark"]["run"]["status"] != "running":
            break
    assert store.read()["benchmark"]["run"]["status"] == "complete"

    report = client.get("/api/analytics").json()
    assert report["scope"] == "benchmark" and report["sample"]["sufficient"] is True
    assert report["cycle"]["verdict"] == "met" and report["fallout"]["verdict"] == "met"
    assert report["fallout"]["falloutPercent"] < 8.0
    assert [item["stage"] for item in report["agents"]] and all(item["total"] is not None for item in report["agents"])
    live_orders = list(client.get("/api/state").json()["orders"].values())
    assert len(live_orders) == 4 and not any(order["customer"].startswith("Demo Benchmark") for order in live_orders)


def test_in_live_mode_only_staff_can_read_the_report_or_start_a_benchmark(live_site):  # noqa: F811
    for path in ("/api/analytics", "/api/analytics/source"):
        assert live_site.get(path).status_code == 401
    assert live_site.post("/api/analytics/benchmark", json={}).status_code == 401

    assert register(live_site).status_code == 201
    for path in ("/api/analytics", "/api/analytics/source"):
        assert live_site.get(path).status_code == 403
    assert live_site.post("/api/analytics/benchmark", json={}).status_code == 403

    outsider = new_client()
    assert outsider.get("/api/analytics", headers={"Authorization": f"Bearer {PARTNER}"}).status_code == 403
    assert outsider.get("/api/analytics", headers=BEARER).status_code == 200

    staff = new_client()
    assert staff.post("/api/auth/staff", json={"token": ADMIN}).status_code == 200
    assert staff.get("/api/analytics").status_code == 200
    assert staff.get("/api/analytics/source").json()["kind"] == "local-file"
    assert staff.post("/api/analytics/benchmark", json={"size": 50},
                      headers={"X-Requested-With": ""}).status_code == 403
    assert staff.post("/api/analytics/benchmark", json={"size": 50}).status_code == 202
