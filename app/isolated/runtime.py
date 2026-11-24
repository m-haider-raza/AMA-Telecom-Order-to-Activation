"""One process, one managed identity, one bounded agent (or dispatch-only supervisor)."""

import json
import logging
import os
import time

import httpx
from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential

from app.isolated.contracts import AGENTS, VERSION, propose
from app.isolated.telemetry import configure
from app.isolated.probes import verify_isolation

log = logging.getLogger("ama.isolated.runtime")


def foundry_proposal(credential, agent, facts, event_id):
    endpoint = os.environ["FOUNDRY_AGENT_ENDPOINT"]
    if not endpoint.startswith("https://") or ".services.ai.azure.com/" not in endpoint:
        raise RuntimeError("An explicit Foundry HTTPS endpoint is required")
    token = credential.get_token("https://ai.azure.com/.default").token
    log.info("foundry.invocation.started agent=%s eventId=%s", agent, event_id)
    started = time.monotonic()
    response = httpx.post(endpoint, headers={"Authorization": "Bearer " + token},
                          json={"facts": facts}, timeout=120)
    if response.is_error:
        log.error("foundry.invocation.failed agent=%s eventId=%s httpStatus=%s usageKnown=false",
                  agent, event_id, response.status_code)
        response.raise_for_status()
    result = response.json()
    if result.get("agent") != agent:
        raise RuntimeError("Foundry response does not match this bounded worker")
    log.info("foundry.invocation.receipt %s", json.dumps({
        "agent": agent, "eventId": event_id, "modelCalls": result["modelCalls"],
        "usage": result["usage"], "responseId": result.get("responseId"), "model": result.get("model"),
        "agentVersion": result.get("agentVersion"), "elapsedMs": round((time.monotonic() - started) * 1000)}))
    return result["proposal"]


def verify_foundry(credential):
    token = credential.get_token("https://ai.azure.com/.default").token
    headers = {"Authorization": "Bearer " + token}
    for label, endpoint in (("cross-project", os.environ["FOUNDRY_FORBIDDEN_ENDPOINT"]),
                            ("agent-edit", os.environ["FOUNDRY_AGENT_ENDPOINT"].split("/endpoint/")[0]
                             + "/versions?api-version=v1")):
        response = httpx.post(endpoint, headers=headers, json={}, timeout=60)
        if response.status_code != 403:
            raise RuntimeError(f"Expected Foundry {label} denial, received {response.status_code}")
        log.info("foundry.isolation.denied operation=%s status=403", label)


def run():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    agent = os.environ["ISOLATED_AGENT"]
    if agent not in {*AGENTS, "supervisor"}:
        raise RuntimeError("An explicit bounded runtime is required")
    configure(agent)
    if any(os.getenv(key) for key in ("ISOLATED_STORAGE_URL", "AZURE_STORAGE_ACCOUNT_URL",
                                     "AMA_SQL_SERVER", "AZURE_CLIENT_SECRET", "DEMO_ADMIN_TOKEN",
                                     "DEMO_PARTNER_TOKEN")):
        raise RuntimeError("Workers may not receive state-store or shared application credentials")
    credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    base = os.environ["ISOLATED_BOUNDARY_URL"]
    if not base.startswith("https://"):
        raise RuntimeError("The deployed tool boundary must use HTTPS")
    expected_oid = os.environ["ISOLATED_PRINCIPAL_ID"]
    scope = "api://" + os.environ["ISOLATED_AUDIENCE"] + "/.default"
    cached_job, cached_proposal = None, None
    with credential, httpx.Client(base_url=base, timeout=30) as client:
        verified = False
        while True:
            try:
                token = credential.get_token(scope).token
                headers = {"Authorization": "Bearer " + token}
                if not verified:
                    response = client.get("/internal/identity", headers=headers)
                    response.raise_for_status()
                    identity = response.json()
                    if identity != {"principalId": expected_oid, "agent": agent,
                                    "clientId": os.environ["AZURE_CLIENT_ID"]}:
                        raise RuntimeError("Receiving boundary verified an unexpected runtime identity")
                    log.info("identity.verified %s", json.dumps({**identity, "version": VERSION}))
                    if os.getenv("ISOLATED_VERIFY_DENIALS") == "1":
                        verify_isolation(client, credential, headers, agent, expected_oid,
                                         os.environ["ISOLATED_TEST_STORAGE_URL"],
                                         os.environ["ISOLATED_FORBIDDEN_CLIENT_ID"], os.environ["ISOLATED_AUDIENCE"])
                    if os.getenv("FOUNDRY_AGENT_ENDPOINT"):
                        verify_foundry(credential)
                    verified = True
                if agent == "supervisor":
                    response = client.post("/internal/dispatch", headers=headers)
                    response.raise_for_status()
                    if os.getenv("FOUNDRY_AGENT_ENDPOINT") and response.json()["created"]:
                        foundry_proposal(credential, agent, {"pending": response.json()["pending"]}, "dispatch")
                else:
                    response = client.get("/internal/work", headers=headers)
                    response.raise_for_status()
                    job = response.json()
                    if job is None:
                        time.sleep(2)
                        continue
                    key = (job["eventId"], job["revision"])
                    if key != cached_job:
                        cached_proposal = (foundry_proposal(credential, agent, job["facts"], job["eventId"])
                                           if os.getenv("FOUNDRY_AGENT_ENDPOINT") else propose(agent, job["facts"]))
                        cached_job = key
                    response = client.post("/internal/submit", headers=headers, json={
                        "eventId": job["eventId"], "operation": AGENTS[agent][1], "revision": job["revision"],
                        "proposal": cached_proposal})
                response.raise_for_status()
            except (httpx.HTTPError, AzureError) as error:
                # Persisted tickets are never acknowledged on failure. Restart/retry sees the same ticket.
                log.error("runtime.operation.failed agent=%s errorType=%s", agent, type(error).__name__)
                verified = False
            time.sleep(2)


if __name__ == "__main__":
    run()
