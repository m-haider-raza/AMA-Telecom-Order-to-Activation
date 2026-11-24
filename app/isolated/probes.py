"""Acceptance probes against only this new stack, using the runtime's actual identity."""

import json
import logging

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

log = logging.getLogger("ama.isolated.probes")


def verify_isolation(client, credential, headers, agent, principal_id, storage_url, forbidden_client_id, audience):
    with ManagedIdentityCredential(client_id=forbidden_client_id) as other:
        try:
            other.get_token("api://" + audience + "/.default")
        except ClientAuthenticationError:
            log.info("isolation.other-identity-denied %s",
                     json.dumps({"principalId": principal_id, "agent": agent, "outcome": "not-attached"}))
        else:
            raise RuntimeError("Runtime unexpectedly acquired another worker's managed identity")
    checks = [("/internal/identity", "GET", {"X-Agent-Name": "provisioning"}, None, 403),
              ("/api/orders/acceptance-probe/decision", "POST", {}, {"decision": "approve"}, 403)]
    if agent == "supervisor":
        checks.append(("/internal/work", "GET", {}, None, 403))
    else:
        checks.append(("/internal/dispatch", "POST", {}, None, 403))
    checks.append(("/internal/submit", "POST", {}, {
        "eventId": "synthetic-authorization-probe", "revision": "0" * 64,
        "operation": "plan.create" if agent != "decomposition" else "reserve.activate", "proposal": {}}, 403))
    for path, method, extra, body, expected in checks:
        response = client.request(method, path, headers={**headers, **extra}, json=body)
        if response.status_code != expected:
            raise RuntimeError(f"Isolation probe failed: {agent} {path} returned {response.status_code}")
        log.info("isolation.denied %s", json.dumps({"principalId": principal_id, "agent": agent,
                                                  "operation": path, "status": response.status_code}))
    wrong_audience = credential.get_token("https://management.azure.com/.default").token
    response = client.get("/internal/identity", headers={"Authorization": "Bearer " + wrong_audience})
    if response.status_code != 401:
        raise RuntimeError("The receiving boundary did not reject a wrong-audience Azure token")
    with BlobServiceClient(storage_url, credential=credential) as blobs:
        try:
            blobs.get_blob_client("boundary", "isolated.json").download_blob()
        except HttpResponseError as error:
            if error.status_code != 403:
                raise RuntimeError(f"Expected RBAC denial, received {error.status_code}") from error
            log.info("isolation.storage-denied %s", json.dumps({"principalId": principal_id, "agent": agent,
                                                              "status": 403, "scope": "new-boundary-container"}))
        else:
            raise RuntimeError("Worker unexpectedly has direct state-store access")
    log.info("isolation.verified %s", json.dumps({"principalId": principal_id, "agent": agent,
                                                "claims": "receiving-Entra-validator",
                                                "toolDenials": True, "storageRbacDenial": True}))
