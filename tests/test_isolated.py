import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.domain import approve_exception, correct_order, create_order, verify_audit
from app.isolated.boundary import Boundary, Denied
from app.isolated.contracts import AGENTS, propose
from app.isolated.identity import EntraVerifier, Principal
from app.isolated.store import BlobStore, LocalStore
from tests.test_domain import payload

TENANT = str(uuid4())
AUDIENCE = str(uuid4())
BINDINGS = {agent: {"oid": str(uuid4()), "clientId": str(uuid4())} for agent in [*AGENTS, "supervisor"]}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def principal(agent):
    return Principal(BINDINGS[agent]["oid"], agent, BINDINGS[agent]["clientId"])


def token(agent, **overrides):
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": AUDIENCE, "tid": TENANT,
              "oid": BINDINGS[agent]["oid"], "azp": BINDINGS[agent]["clientId"], "ver": "2.0",
              "roles": [f"Worker.{agent}"], "iat": int(time.time()) - 1, "nbf": int(time.time()) - 1,
              "exp": int(time.time()) + 300, **overrides}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "synthetic-test-key"})


@pytest.fixture
def verifier():
    keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=KEY.public_key()))
    return EntraVerifier(TENANT, AUDIENCE, BINDINGS, keys)


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("AMA_MODE", "demo")
    result = Boundary(LocalStore(tmp_path / "isolated.sqlite"))
    result.store.transact(lambda state: state.update(orders={}, events=[], audit=[], idempotency={}))
    return result


def submit_job(boundary, agent, job=None):
    job = job or boundary.work(principal(agent))
    return boundary.submit(principal(agent), job["eventId"], job["operation"], job["revision"],
                           propose(agent, job["facts"]))


def advance(boundary, order_id, count=15):
    for _ in range(count):
        boundary.dispatch(principal("supervisor"))
        order = boundary.store.read()["orders"][order_id]
        if order["stage"] in AGENTS:
            job = boundary.work(principal(order["stage"]))
            if job:
                submit_job(boundary, order["stage"], job)
    return boundary.store.read()["orders"][order_id]


def test_all_six_principals_verified(verifier):
    for agent in BINDINGS:
        assert verifier.verify(token(agent)) == principal(agent)


@pytest.mark.parametrize("claims", [
    {"aud": "wrong"}, {"iss": "https://evil.invalid"}, {"tid": str(uuid4())},
    {"exp": int(time.time()) - 1}, {"nbf": int(time.time()) + 100},
    {"oid": BINDINGS["provisioning"]["oid"]}, {"azp": BINDINGS["provisioning"]["clientId"]},
    {"roles": ["Worker.provisioning"]}, {"roles": ["Worker.validation", "Worker.provisioning"]},
    {"scp": "user_impersonation"}, {"ver": "1.0"}, {"iat": int(time.time()) + 100},
])
def test_claims_and_impersonation_rejected(verifier, claims):
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(token("validation", **claims))


def test_invalid_signature_unsigned_and_missing_claim(verifier):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token("validation"), options={"verify_signature": False})
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(jwt.encode(claims, other, algorithm="RS256"))
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(jwt.encode(claims, None, algorithm="none"))
    del claims["oid"]
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(jwt.encode(claims, KEY, algorithm="RS256"))


def test_shared_principal_configuration_rejected():
    bindings = {**BINDINGS, "fallout": BINDINGS["provisioning"]}
    with pytest.raises(ValueError, match="Shared"):
        EntraVerifier(TENANT, AUDIENCE, bindings)


def test_boundary_attestation_has_no_worker_roles_or_impersonation(verifier):
    oid, client_id = str(uuid4()), str(uuid4())
    bearer = token("validation", oid=oid, azp=client_id, roles=[])
    assert verifier.verify_boundary(bearer, oid, client_id) == Principal(oid, "trusted-policy-boundary", client_id)
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify(bearer)
    with pytest.raises(jwt.InvalidTokenError):
        verifier.verify_boundary(token("validation"), BINDINGS["validation"]["oid"], BINDINGS["validation"]["clientId"])


def test_dispatch_policy_actions_are_boundary_attributed_not_agent_impersonation(boundary):
    boundary.boundary_principal_id = str(uuid4())
    order = boundary.store.transact(lambda state: create_order(state, payload("human-exception"), "service-manager"))
    result = advance(boundary, order["id"])
    assert result["state"] == "pendingApproval"
    state = boundary.store.read()
    guards = [row for row in state["audit"] if row["orderId"] == order["id"] and row["action"] == "policy.hold"]
    assert guards and all(row["actor"] == "Trusted Policy Boundary" for row in guards)
    policy = next(json.loads(row["detail"]) for row in state["audit"]
                  if row["orderId"] == order["id"] and row["action"] == "isolated.policy")
    assert policy["principalId"] == boundary.boundary_principal_id
    assert policy["agent"] == "trusted-policy-boundary"
    assert policy["initiatingPrincipalId"] == BINDINGS["supervisor"]["oid"]
    assert policy["operation"] == "policy.hold" and policy["outcome"] == "pendingApproval"


@pytest.mark.parametrize("agent", AGENTS)
def test_every_cross_agent_operation_and_supervisor_denied(boundary, agent):
    order = boundary.store.transact(lambda state: create_order(state, payload(), "service-manager"))
    boundary.dispatch(principal("supervisor"))
    job = boundary.work(principal("validation"))
    for target in AGENTS:
        if target != agent:
            with pytest.raises(Denied):
                boundary.submit(principal(agent), job["eventId"], AGENTS[target][1], job["revision"], {})
    with pytest.raises(Denied):
        boundary.dispatch(principal(agent))
    with pytest.raises(Denied):
        boundary.submit(principal("supervisor"), job["eventId"], "validation.submit", job["revision"], {})
    with pytest.raises(Denied):
        boundary.work(principal("supervisor"))
    assert boundary.store.read()["orders"][order["id"]]["stage"] == "validation"


@pytest.mark.parametrize("scenario,expected", [
    ("happy", "completed"), ("auto-recovery", "completed"), ("human-exception", "pendingApproval"),
    ("change-window", "held"), ("address-correction", "awaitingCustomer"),
])
def test_existing_outcomes_and_manual_gates(boundary, scenario, expected):
    order = boundary.store.transact(lambda state: create_order(state, payload(scenario), "service-manager"))
    result = advance(boundary, order["id"])
    assert result["state"] == expected
    if expected in ("pendingApproval", "held"):
        assert not result["reserved"]
        boundary.store.transact(lambda state: approve_exception(state, order["id"], "service-manager", "approve"))
        assert advance(boundary, order["id"])["state"] == "completed"
    if expected == "awaitingCustomer":
        assert not result["reserved"]
        boundary.store.transact(lambda state: correct_order(state, order["id"], "service-manager", "IST-001"))
        assert advance(boundary, order["id"])["state"] == "completed"
    assert verify_audit(boundary.store.read())


def test_arbitrary_result_and_replay_conflicts_cannot_mutate_state(boundary):
    order = boundary.store.transact(lambda state: create_order(state, payload(), "service-manager"))
    boundary.dispatch(principal("supervisor"))
    job = boundary.work(principal("validation"))
    with pytest.raises(Denied, match="independent"):
        boundary.submit(principal("validation"), job["eventId"], job["operation"], job["revision"],
                        {"state": "completed", "riskApproved": True})
    first = submit_job(boundary, "validation", job)
    assert submit_job(boundary, "validation", job) == first
    with pytest.raises(Denied, match="Conflicting replay"):
        boundary.submit(principal("validation"), job["eventId"], job["operation"], job["revision"], {})
    assert boundary.store.read()["orders"][order["id"]]["stage"] == "decomposition"


def test_concurrency_lost_response_restart_and_notifications_at_most_once(boundary):
    order = boundary.store.transact(lambda state: create_order(state, payload("auto-recovery"), "service-manager"))
    boundary.dispatch(principal("supervisor"))
    job = boundary.work(principal("validation"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: submit_job(boundary, "validation", job), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    restarted = Boundary(LocalStore(boundary.store.path))
    # A worker died after commit but before seeing its response; receipt survives process restart.
    assert submit_job(restarted, "validation", job) == receipts[0]
    result = advance(restarted, order["id"])
    assert result["state"] == "completed" and result["recoveryAttempts"] == 1
    before = restarted.store.read()
    advance(restarted, order["id"])
    after = restarted.store.read()
    assert before["inventory"] == after["inventory"]
    assert before["notifications"] == after["notifications"]
    capacity = next(item for item in after["inventory"] if item["city"] == "Istanbul" and item["product"] == "sdwan")
    assert capacity["availableUnits"] == 23
    commits = [json.loads(entry["detail"]) for entry in after["audit"] if entry["action"] == "isolated.commit"
               and entry["orderId"] == order["id"]]
    assert {item["agent"] for item in commits} == set(AGENTS)
    assert all(item["principalId"] == BINDINGS[item["agent"]]["oid"] for item in commits)


def test_stale_capacity_and_sla_rechecked_at_commit(boundary):
    order = boundary.store.transact(lambda state: create_order(state, payload(), "service-manager"))
    boundary.dispatch(principal("supervisor"))
    job = boundary.work(principal("validation"))
    boundary.store.transact(lambda state: [item.update(availableUnits=0) for item in state["inventory"]])
    with pytest.raises(Denied, match="independent"):
        submit_job(boundary, "validation", job)
    submit_job(boundary, "validation")
    assert boundary.store.read()["orders"][order["id"]]["exception"]["type"] == "capacity"
    other = boundary.store.transact(lambda state: create_order(state, payload(key="sla-test-unique"), "service-manager"))
    boundary.store.transact(lambda state: state["orders"][other["id"]].update(
        deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    result = advance(boundary, other["id"])
    assert result["exception"]["type"] == "sla-breach" and not result["reserved"]


def test_enterprise_gate_never_dispatches(boundary):
    order = boundary.store.transact(lambda state: create_order(state, payload(), "service-manager", start=False))
    boundary.store.transact(lambda state: state["orders"][order["id"]].update(state="awaitingEnterpriseApproval"))
    advance(boundary, order["id"])
    assert boundary.store.read()["orders"][order["id"]]["state"] == "awaitingEnterpriseApproval"


@pytest.fixture
def client(tmp_path, monkeypatch, verifier):
    for key in ("AMA_SQL_SERVER", "AMA_SQL_DATABASE", "AZURE_STORAGE_ACCOUNT_URL", "AMA_SQL_ADMIN_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMA_MODE", "demo")
    monkeypatch.setenv("AMA_DELIVERY", "simulated")
    monkeypatch.setenv("AMA_STAFF_AUTH", "token")
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", "new-manager-" + "x" * 30)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", "new-partner-" + "y" * 30)
    monkeypatch.setenv("ISOLATED_LOCAL", "1")
    monkeypatch.setenv("ISOLATED_LOCAL_PATH", str(tmp_path / "api.sqlite"))
    monkeypatch.delenv("ISOLATED_STORAGE_URL", raising=False)
    monkeypatch.setattr(EntraVerifier, "from_env", classmethod(lambda cls: verifier))
    from app.isolated.api import app
    with TestClient(app) as instance:
        yield instance


def test_http_denied_calls_spoofing_and_durable_audit(client):
    for headers, expected in [
        ({}, 401), ({"Authorization": "Bearer invalid"}, 401),
        ({"Authorization": "Bearer " + token("validation"), "X-Agent-Name": "provisioning"}, 403),
        ({"Authorization": "Bearer " + token("validation")}, 403),
    ]:
        assert client.post("/internal/dispatch", headers=headers).status_code == expected
    assert client.get("/api/state", headers={"Authorization": "Bearer " + token("validation")}).status_code == 403
    state = client.app.state.store.read()
    entries = [json.loads(row["detail"]) for row in state["audit"] if row["action"] == "isolated.authorization"]
    assert len(entries) == 5
    assert all(row["authorization"] == "deny" for row in entries)
    assert entries[-2]["principalId"] == BINDINGS["validation"]["oid"]
    assert "Bearer" not in json.dumps(entries) and token("validation") not in json.dumps(state)


def test_http_contract_workflow_and_portals(client):
    dispatch_headers = {"Authorization": "Bearer " + token("supervisor")}
    assert client.post("/internal/dispatch", headers=dispatch_headers).status_code == 200
    headers = {"Authorization": "Bearer " + token("validation")}
    job = client.get("/internal/work", headers=headers).json()
    assert set(job["facts"]) == {"siteReference", "expectedSite", "availableUnits", "bandwidthMbps"}
    body = {"eventId": job["eventId"], "operation": job["operation"], "revision": job["revision"],
            "proposal": propose("validation", job["facts"])}
    assert client.post("/internal/submit", headers=headers, json={**body, "agent": "provisioning"}).status_code == 422
    assert client.post("/internal/submit", headers=headers, json=body).status_code == 200
    assert client.post("/internal/submit", headers=headers, json=body).status_code == 200
    for path in ("/case-study", "/login", "/partner", "/account", "/console"):
        assert client.get(path).status_code == 200
    assert client.get("/isolated-health").json()["agent365"] == "not-registered"


def test_isolated_home_opens_shared_signin_without_exposing_the_staff_console(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.content == client.get("/login").content
    assert 'id="panel-signin"' in response.text and 'id="staff-link"' in response.text
    assert 'src="/static/app.js' not in response.text
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert client.get("/api/state").status_code == 401


def test_empty_polls_do_not_write_state_and_health_uses_real_heartbeats(client, monkeypatch):
    client.app.state.store.transact(lambda state: state.update(orders={}, events=[], isolatedJobs={}))
    before = client.app.state.store.read()
    for _ in range(4):
        for agent in BINDINGS:
            headers = {"Authorization": "Bearer " + token(agent)}
            if agent == "supervisor":
                assert client.post("/internal/dispatch", headers=headers).status_code == 200
            else:
                assert client.get("/internal/work", headers=headers).json() is None
    assert client.app.state.store.read() == before
    assert client.get("/health").status_code == 200
    old = time.monotonic()
    monkeypatch.setattr("app.isolated.api.time.monotonic", lambda: old + 65)
    assert client.get("/health").status_code == 503
    for agent in BINDINGS:
        path = "/internal/dispatch" if agent == "supervisor" else "/internal/work"
        client.request("POST" if agent == "supervisor" else "GET", path,
                       headers={"Authorization": "Bearer " + token(agent)})
    assert client.get("/health").status_code == 200
    assert client.app.state.store.read() == before


def test_blob_reads_and_noops_do_not_upload_and_conflicts_recompute():
    from azure.core.exceptions import ResourceModifiedError
    body = {"counter": 0}
    uploads = []
    store = BlobStore.__new__(BlobStore)
    blob = SimpleNamespace()

    def download():
        data = json.dumps(body).encode()
        return SimpleNamespace(readall=lambda: data, properties=SimpleNamespace(etag=str(body["counter"])))

    def upload(encoded, **options):
        uploads.append(options)
        if len(uploads) == 1:
            body["counter"] = 10
            raise ResourceModifiedError("Synthetic concurrent commit")
        body.update(json.loads(encoded))
    blob.download_blob, blob.upload_blob = download, upload
    store.blob = blob
    assert store.read() == {"counter": 0}
    assert store.transact(lambda state: state["counter"]) == 0
    assert uploads == []
    assert store.transact(lambda state: state.update(counter=state["counter"] + 1)) is None
    assert body["counter"] == 11
    assert uploads[-1]["etag"] == "10"


def test_failed_transaction_rolls_back_after_crash(boundary):
    before = boundary.store.read()

    def crash(state):
        state["inventory"][0]["availableUnits"] -= 1
        raise RuntimeError("Synthetic crash before commit")
    with pytest.raises(RuntimeError):
        boundary.store.transact(crash)
    assert boundary.store.read() == before


def test_staff_and_partner_credentials_preserved_without_agent_approval_authority(client):
    headers = {"Authorization": "Bearer " + "new-manager-" + "x" * 30}
    assert client.get("/api/enterprises", headers=headers).status_code == 200
    assert client.get("/api/state").status_code == 401
    assert "isolatedJobs" not in client.get("/api/state", headers=headers).json()
    assert client.post("/api/orders", headers=headers, json=payload()).status_code == 409
    partner_headers = {"Authorization": "Bearer " + "new-partner-" + "y" * 30}
    assert client.post("/api/orders", headers=partner_headers, json=payload()).status_code == 201
    assert client.get("/api/auth/me").json()["enterpriseApprovals"] is True


def test_benchmark_uses_remote_contracts_and_never_autoapproves(client):
    headers = {"Authorization": "Bearer " + "new-manager-" + "x" * 30}
    assert client.post("/api/analytics/benchmark", headers=headers, json={"size": 50}).status_code == 202
    boundary = client.app.state.boundary
    for _ in range(350):
        boundary.dispatch(principal("supervisor"))
        progressed = False
        for agent in AGENTS:
            job = boundary.work(principal(agent))
            if job:
                submit_job(boundary, agent, job)
                progressed = True
        if not progressed:
            break
    held = client.get("/api/isolated/benchmark/holds", headers=headers).json()["orders"]
    assert held and all(not order["reserved"] for order in held)
    for order in held:
        body = {"decision": "correct-site", "siteReference": order["exception"]["expectedSiteReference"]} if (
            order["state"] == "awaitingCustomer") else {"decision": "approve"}
        url = f"/api/isolated/benchmark/orders/{order['id']}/decision"
        assert client.post(url, headers={"Authorization": "Bearer " + token("supervisor")}, json=body).status_code == 403
        assert client.post(url, headers=headers, json=body).status_code == 200
    for _ in range(100):
        boundary.dispatch(principal("supervisor"))
        for agent in AGENTS:
            job = boundary.work(principal(agent))
            if job:
                submit_job(boundary, agent, job)
    state = boundary.store.read()
    assert state["benchmark"]["run"]["status"] == "complete"
    assert all(order["state"] == "completed" for order in state["benchmark"]["sim"]["orders"].values())


def test_isolated_enterprise_approval_and_customer_ownership_end_to_end(client):
    xhr = {"X-Requested-With": "isolated-test", "X-Session-Mode": "tab"}
    users = []
    for index in range(2):
        reply = client.post("/api/auth/register", headers=xhr, json={
            "name": f"Demo Contact {index}", "company": f"Demo Approval Enterprise {index}",
            "email": f"contact{index}@isolated.example", "language": "en",
            "password": "Synthetic-Only_7!Account", "consent": True})
        assert reply.status_code == 201, reply.text
        body = reply.json()
        user_headers = {**xhr, "X-Session": body["session"]}
        assert client.post("/api/auth/verify", headers=user_headers,
                           json={"code": body["simulatedCode"]}).status_code == 200
        users.append((body["account"]["id"], user_headers))
    staff = {"Authorization": "Bearer " + "new-manager-" + "x" * 30}
    reply = client.post(f"/api/enterprises/{users[0][0]}/orders", headers=staff, json={
        "city": "Istanbul", "product": "sdwan", "scenario": "happy", "idempotencyKey": "isolated-enterprise-001"})
    assert reply.status_code == 201, reply.text
    order_id = reply.json()["id"]
    boundary = client.app.state.boundary
    assert advance(boundary, order_id)["state"] == "awaitingEnterpriseApproval"
    assert client.get(f"/api/me/orders/{order_id}", headers=users[1][1]).status_code == 404
    approval_path = f"/api/me/orders/{order_id}/approval"
    assert client.post(approval_path, headers=users[1][1], json={"decision": "approve"}).status_code == 404
    assert client.post(approval_path, headers={"Authorization": "Bearer " + token("supervisor")},
                       json={"decision": "approve"}).status_code == 403
    assert client.post(approval_path, headers=users[0][1], json={"decision": "approve"}).status_code == 200
    assert advance(boundary, order_id)["state"] == "completed"
    assert client.get("/api/state", headers=users[0][1]).status_code == 403


def test_benchmark_completes_when_last_hold_is_explicitly_rejected(boundary):
    from app.benchmark import new_sim

    def fixture(state):
        sim = new_sim()
        order = create_order(sim, payload("human-exception"), "service-manager")
        state["benchmark"] = {"sim": sim, "run": {"status": "running", "id": "synthetic-benchmark"}}
        return order["id"]
    order_id = boundary.store.transact(fixture)
    for _ in range(10):
        boundary.dispatch(principal("supervisor"))
        for agent in AGENTS:
            job = boundary.work(principal(agent))
            if job:
                submit_job(boundary, agent, job)
    boundary.store.transact(lambda state: approve_exception(
        state["benchmark"]["sim"], order_id, "service-manager", "reject"))
    boundary.dispatch(principal("supervisor"))
    assert boundary.store.read()["benchmark"]["run"]["status"] == "complete"
