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
                    verified = True
                if agent == "supervisor":
                    response = client.post("/internal/dispatch", headers=headers)
                else:
                    response = client.get("/internal/work", headers=headers)
                    response.raise_for_status()
                    job = response.json()
                    if job is None:
                        time.sleep(2)
                        continue
                    response = client.post("/internal/submit", headers=headers, json={
                        "eventId": job["eventId"], "operation": AGENTS[agent][1], "revision": job["revision"],
                        "proposal": propose(agent, job["facts"])})
                response.raise_for_status()
            except (httpx.HTTPError, AzureError) as error:
                # Persisted tickets are never acknowledged on failure. Restart/retry sees the same ticket.
                log.error("runtime.operation.failed agent=%s errorType=%s", agent, type(error).__name__)
                verified = False
            time.sleep(2)


if __name__ == "__main__":
    run()
