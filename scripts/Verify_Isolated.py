"""Verify only a newly deployed acceptance stack; never read original application settings."""

import argparse
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
    require(group.startswith("rg-amai-") and receipt.get("url"), "A new isolated deployment receipt is required")
    report = {"at": datetime.now(timezone.utc).isoformat(), "url": receipt["url"], "resourceGroup": group,
              "agent365": "not-registered", "originalStacksTouched": False, "runtimes": []}
    apps = az("containerapp", "list", "--resource-group", group, "--subscription", subscription,
              "--query", "[].{name:name,identity:identity,containers:properties.template.containers,fqdn:properties.configuration.ingress.fqdn}",
              "-o", "json", json_result=True)
    require(len(apps) == 7, "Exactly seven separate Container Apps must exist")
    boundary = next((app for app in apps if app["name"].startswith("amai-boundary-")), None)
    require(boundary and receipt["url"] == "https://" + boundary["fqdn"],
            "Receipt URL is not this new resource group's boundary")
    attached = boundary["identity"].get("userAssignedIdentities", {})
    require(len(attached) == 1, "Boundary must have exactly one identity")
    boundary_identity = next(iter(attached.values()))
    environment = {item["name"]: item.get("value") for item in boundary["containers"][0]["env"]}
    require(environment["AZURE_CLIENT_ID"] == boundary_identity["clientId"]
            and environment["ISOLATED_STORAGE_URL"] == receipt["storageUrl"], "Boundary credential/state binding mismatch")
    boundary_roles = az("role", "assignment", "list", "--assignee", boundary_identity["principalId"], "--all",
                        "--subscription", subscription, "--query", "[].{role:roleDefinitionName,scope:scope}",
                        "-o", "json", json_result=True)
    require(len(boundary_roles) == 2 and {role["role"] for role in boundary_roles} == {
        "AcrPull", "Storage Blob Data Contributor"}, "Boundary Azure RBAC is broader than expected")
    data_role = next(role for role in boundary_roles if role["role"] == "Storage Blob Data Contributor")
    require(f"/resourcegroups/{group.lower()}/" in data_role["scope"].lower()
            and data_role["scope"].lower().endswith("/blobservices/default/containers/boundary"),
            "State permissions are not scoped to this new container")
    for binding in receipt["identities"]:
        runtime = next((app for app in apps if app["name"].startswith(
            "amai-" + binding["agent"].replace("_", "-") + "-")), None)
        require(runtime is not None, f"Missing {binding['agent']} runtime")
        attached = runtime["identity"].get("userAssignedIdentities", {})
        require(len(attached) == 1 and binding["resourceId"] in attached, "Runtime has unexpected identity attachment")
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
        expected_scope = (f"/subscriptions/{subscription}/resourceGroups/{group}/providers/"
                          f"Microsoft.ContainerRegistry/registries/{receipt['registryName']}")
        require(len(assignments) == 1 and assignments[0]["role"] == "AcrPull"
                and assignments[0]["scope"].lower() == expected_scope.lower(), "Worker Azure RBAC exceeds registry scope")
        raw = az("containerapp", "logs", "show", "--name", runtime["name"], "--resource-group", group,
                 "--subscription", subscription, "--tail", "100", "--format", "json")
        require("identity.verified" in raw and binding["oid"] in raw, "Actual receiving-API identity proof missing")
        require("isolation.verified" in raw and "isolation.storage-denied" in raw
                and "isolation.other-identity-denied" in raw, "Actual Azure/tool denial probes have not passed")
        report["runtimes"].append({"agent": binding["agent"], "principalId": binding["oid"],
                                   "singleIdentity": True, "registryOnlyRbac": True, "identityAndDenialProbes": True})
    manager = {"Authorization": "Bearer " + credentials["admin"]}
    partner = {"Authorization": "Bearer " + credentials["partner"]}
    with httpx.Client(base_url=receipt["url"], timeout=30) as client:
        require(client.get("/isolated-health").status_code == 200, "Boundary state connectivity failed")
        deadline = time.monotonic() + 120
        while client.get("/health").status_code != 200:
            require(time.monotonic() < deadline, "Not all six actual runtime heartbeats became healthy")
            time.sleep(3)
        payload = {"customer": "Demo Isolated Verification", "city": "Istanbul", "product": "sdwan",
                   "scenario": "auto-recovery", "language": "en", "idempotencyKey": "verify-" + uuid4().hex}
        before = client.get("/api/state", headers=manager)
        before.raise_for_status()
        original_capacity = next(item["availableUnits"] for item in before.json()["inventory"]
                                 if item["city"] == payload["city"] and item["product"] == payload["product"])
        response = client.post("/api/orders", headers=partner, json=payload)
        response.raise_for_status()
        order_id = response.json()["id"]
        deadline = time.monotonic() + 180
        while True:
            snapshot = client.get("/api/state", headers=manager)
            snapshot.raise_for_status()
            state = snapshot.json()
            order = state["orders"][order_id]
            if order["state"] == "completed":
                break
            require(time.monotonic() < deadline, "Remote-worker recovery journey did not complete")
            time.sleep(3)
        require(order.get("recoveryAttempts") == 1 and order["reserved"], "Recovery/allocation policy failed")
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
        tools = {"isolated.authorization", "isolated.dispatch", "isolated.commit"}
        require(sum(row["action"] in tools for row in after["audit"]) ==
                sum(row["action"] in tools for row in baseline["audit"]),
                "Successful idle polling appended durable tool audit")
        report.update(orderId=order_id, recoveryAttempts=1, allocationCount=1, allFiveAgentAudit=True,
                      replayIdempotent=True, heartbeatAfter65Seconds=True, noIdleAuditGrowth=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--output", required=True)
    verify(parser.parse_args())
