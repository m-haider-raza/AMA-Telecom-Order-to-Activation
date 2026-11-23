"""Verify only a newly deployed acceptance stack; never read original application settings."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from Deploy_Isolated import az


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(args):
    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    group, subscription = receipt["resourceGroup"], receipt["subscriptionId"]
    require(group.startswith("rg-amai-"), "A new isolated deployment receipt is required")
    report = {"at": datetime.now(timezone.utc).isoformat(), "status": "verifying",
              "url": receipt.get("url"), "resourceGroup": group,
              "agent365": "not-registered", "originalStacksTouched": False, "runtimes": []}
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    expected_scope = (f"/subscriptions/{subscription}/resourceGroups/{group}/providers/"
                      f"Microsoft.ContainerRegistry/registries/{receipt['registryName']}")
    apps = az("containerapp", "list", "--resource-group", group, "--subscription", subscription,
              "--query", "[].{name:name,identity:identity,containers:properties.template.containers,fqdn:properties.configuration.ingress.fqdn,state:properties.provisioningState}",
              "-o", "json", json_result=True)
    boundary = next((app for app in apps if app["name"].startswith("amai-boundary-")), None)
    require(boundary and boundary["fqdn"], "This new group's boundary has no actual app URL")
    report.update(url="https://" + boundary["fqdn"],
                  apps=[{"name": app["name"], "provisioningState": app["state"]} for app in apps],
                  missingRuntimes=[binding["agent"] for binding in receipt["identities"]
                                   if not any(app["name"].startswith(
                                       "amai-" + binding["agent"].replace("_", "-") + "-") for app in apps)])
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    require(not receipt.get("url") or receipt["url"] == report["url"],
            "Receipt URL is not this new resource group's boundary")
    require(len(apps) == 7, f"Exactly seven separate Container Apps must exist; found {len(apps)}")
    require(receipt.get("imageTag"), "The deployed image tag is missing from this new receipt")
    expected_image = receipt["registryName"] + ".azurecr.io/amai:" + receipt["imageTag"]
    image = az("acr", "repository", "show", "--name", receipt["registryName"], "--subscription", subscription,
               "--image", "amai:" + receipt["imageTag"], "-o", "json", json_result=True)
    report.update(image=expected_image, imageDigest=image["digest"])
    require(all(app["containers"][0]["image"] == expected_image for app in apps),
            "Every runtime must use the reviewed image in this new registry")
    attached = boundary["identity"].get("userAssignedIdentities", {})
    require(len(attached) == 1, "Boundary must have exactly one identity")
    boundary_identity = next(iter(attached.values()))
    require(boundary["containers"][0]["args"][0] == "app.isolated.api:app",
            "Boundary is not using the isolated trusted entrypoint")
    environment = {item["name"]: item.get("value") for item in boundary["containers"][0]["env"]}
    require(environment["AZURE_CLIENT_ID"] == boundary_identity["clientId"]
            and environment["ISOLATED_STORAGE_URL"] == receipt["storageUrl"], "Boundary credential/state binding mismatch")
    boundary_roles = az("role", "assignment", "list", "--assignee", boundary_identity["principalId"], "--all",
                        "--subscription", subscription, "--query", "[].{role:roleDefinitionName,scope:scope}",
                        "-o", "json", json_result=True)
    require(len(boundary_roles) == 2 and {role["role"] for role in boundary_roles} == {
        "AcrPull", "Storage Blob Data Contributor"}, "Boundary Azure RBAC is broader than expected")
    registry_role = next(role for role in boundary_roles if role["role"] == "AcrPull")
    require(registry_role["scope"].lower() == expected_scope.lower(), "Boundary registry RBAC is too broad")
    data_role = next(role for role in boundary_roles if role["role"] == "Storage Blob Data Contributor")
    require(f"/resourcegroups/{group.lower()}/" in data_role["scope"].lower()
            and data_role["scope"].lower().endswith("/blobservices/default/containers/boundary"),
            "State permissions are not scoped to this new container")
    for binding in receipt["identities"]:
        runtime = next((app for app in apps if app["name"].startswith(
            "amai-" + binding["agent"].replace("_", "-") + "-")), None)
        require(runtime is not None, f"Missing {binding['agent']} runtime")
        attached = runtime["identity"].get("userAssignedIdentities", {})
        require(len(attached) == 1 and binding["resourceId"].lower() in {key.lower() for key in attached},
                "Runtime has unexpected identity attachment")
        assigned_identity = next(iter(attached.values()))
        require(assigned_identity["clientId"] == binding["clientId"]
                and assigned_identity["principalId"] == binding["oid"], "Attached principal/client binding differs")
        container = runtime["containers"][0]
        environment = {item["name"]: item.get("value") for item in container["env"]}
        require(environment["AZURE_CLIENT_ID"] == binding["clientId"], "Managed identity client ID mismatch")
        require(environment["ISOLATED_AGENT"] == binding["agent"], "Worker role mismatch")
        require(container["args"] == ["-m", "app.isolated.runtime"], "An unrestricted runtime was deployed")
        require(not any(key in environment for key in (
            "DEMO_ADMIN_TOKEN", "DEMO_PARTNER_TOKEN", "ISOLATED_STORAGE_URL", "AMA_SQL_SERVER", "AZURE_CLIENT_SECRET")),
            "Worker received shared/state/approval credentials")
        assignments = az("role", "assignment", "list", "--assignee", binding["oid"], "--all",
                         "--subscription", subscription, "--query", "[].{role:roleDefinitionName,scope:scope}",
                         "-o", "json", json_result=True)
        require(len(assignments) == 1 and assignments[0]["role"] == "AcrPull"
                and assignments[0]["scope"].lower() == expected_scope.lower(), "Worker Azure RBAC exceeds registry scope")
        raw = az("containerapp", "logs", "show", "--name", runtime["name"], "--resource-group", group,
                 "--subscription", subscription, "--tail", "100", "--format", "json")
        require("identity.verified" in raw and binding["oid"] in raw, "Actual receiving-API identity proof missing")
        require("isolation.verified" in raw and "isolation.storage-denied" in raw
                and "isolation.other-identity-denied" in raw, "Actual Azure/tool denial probes have not passed")
        report["runtimes"].append({"agent": binding["agent"], "principalId": binding["oid"],
                                   "singleIdentity": True, "registryOnlyRbac": True, "identityAndDenialProbes": True,
                                   "logEvidenceSha256": hashlib.sha256(raw.encode()).hexdigest()})
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    manager = {"Authorization": "Bearer " + credentials["admin"]}
    partner = {"Authorization": "Bearer " + credentials["partner"]}
    with httpx.Client(base_url=report["url"], timeout=30) as client:
        require(client.get("/isolated-health").status_code == 200, "Boundary state connectivity failed")
        deadline = time.monotonic() + 120
        while client.get("/health").status_code != 200:
            require(time.monotonic() < deadline, "Not all six actual runtime heartbeats became healthy")
            time.sleep(3)
        payload = {"customer": "Demo Isolated Verification", "city": "Istanbul", "product": "sdwan",
                   "scenario": "auto-recovery", "language": "en", "idempotencyKey": "verify-" + uuid4().hex}
        before = client.get("/api/state", headers=manager)
        before.raise_for_status()
        attestations = [json.loads(row["detail"]) for row in before.json()["audit"]
                        if row["action"] == "isolated.authorization"]
        require(any(row["operation"] == "boundary.identity" and row["authorization"] == "allow"
                    and row["principalId"] == boundary_identity["principalId"] for row in attestations),
                "Actual Entra-attested trusted boundary identity is missing")
        report["boundaryPrincipalId"] = boundary_identity["principalId"]
        original_capacity = next(item["availableUnits"] for item in before.json()["inventory"]
                                 if item["city"] == payload["city"] and item["product"] == payload["product"])
        response = client.post("/api/orders", headers=partner, json=payload)
        response.raise_for_status()
        order_id = response.json()["id"]
        report["orderId"] = order_id
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        worker = next(app for app in apps if app["name"].startswith("amai-provisioning-"))
        revision = az("containerapp", "show", "--name", worker["name"], "--resource-group", group,
                      "--subscription", subscription, "--query", "properties.latestReadyRevisionName", "-o", "tsv")
        require(revision, "No ready provisioning revision is available for restart verification")
        restart_at = datetime.now(timezone.utc).isoformat()
        az("containerapp", "revision", "restart", "--name", worker["name"], "--resource-group", group,
           "--subscription", subscription, "--revision", revision, "-o", "none")
        report["restartedRuntime"] = {"agent": "provisioning", "revision": revision, "duringSubmittedJourney": True}
        deadline = time.monotonic() + 180
        while True:
            snapshot = client.get("/api/state", headers=manager)
            snapshot.raise_for_status()
            state = snapshot.json()
            order = state["orders"][order_id]
            restarted_identity = any(
                row["at"] >= restart_at and row["action"] == "isolated.authorization"
                and (detail := json.loads(row["detail"]))["operation"] == "/internal/identity"
                and detail["authorization"] == "allow"
                and detail["principalId"] == next(binding["oid"] for binding in receipt["identities"]
                                                  if binding["agent"] == "provisioning")
                for row in state["audit"])
            if order["state"] == "completed" and restarted_identity:
                break
            require(time.monotonic() < deadline, "Remote-worker recovery journey did not complete")
            time.sleep(3)
        require(order.get("recoveryAttempts") == 1 and order["reserved"], "Recovery/allocation policy failed")
        report["restartIdentityReattested"] = True
        capacity = next(item["availableUnits"] for item in state["inventory"]
                        if item["city"] == payload["city"] and item["product"] == payload["product"])
        require(capacity == original_capacity - 1, "Allocation was duplicated or not committed")
        replay = client.post("/api/orders", headers=partner, json=payload)
        replay.raise_for_status()
        require(replay.json()["id"] == order_id, "Intake idempotency replay failed")
        audit = client.get("/api/audit", headers=manager, params={"order": order_id})
        audit.raise_for_status()
        require(audit.json()["verified"], "Durable audit hash chain failed")
        commits = [json.loads(row["detail"]) for row in audit.json()["entries"]
                   if row["action"] == "isolated.commit"]
        require({item["agent"] for item in commits} == {item["agent"] for item in receipt["identities"]
                                                       if item["agent"] != "supervisor"},
                "All five independently authenticated agent commits must be present")
        for commit in commits:
            binding = next(item for item in receipt["identities"] if item["agent"] == commit["agent"])
            require(commit["principalId"] == binding["oid"] and commit["correlationId"]
                    and commit["authorization"] == "allow" and commit["operation"] and commit["agentVersion"],
                    "Durable commit provenance is incomplete")
        baseline = client.get("/api/state", headers=manager).json()
        time.sleep(65)
        require(client.get("/health").status_code == 200, "Runtime heartbeats degraded after 60 seconds")
        after = client.get("/api/state", headers=manager).json()
        require(after["inventory"] == baseline["inventory"] and after["notifications"] == baseline["notifications"],
                "Idle/replayed execution duplicated allocations or notifications")
        require(after == baseline, "No-work polling or portal reads changed the durable state snapshot")
        tools = {"isolated.authorization", "isolated.dispatch", "isolated.commit"}
        require(sum(row["action"] in tools for row in after["audit"]) ==
                sum(row["action"] in tools for row in baseline["audit"]),
                "Successful idle polling appended durable tool audit")
        report.update(orderId=order_id, recoveryAttempts=1, allocationCount=1, allFiveAgentAudit=True,
                      replayIdempotent=True, heartbeatAfter65Seconds=True, noIdleAuditGrowth=True)
    report["status"] = "passed"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        verify(args)
    except (RuntimeError, httpx.HTTPError, OSError, ValueError) as error:
        output = Path(args.output)
        if output.exists():
            report = json.loads(output.read_text(encoding="utf-8"))
            report.update(status="blocked", blocker=str(error))
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise
