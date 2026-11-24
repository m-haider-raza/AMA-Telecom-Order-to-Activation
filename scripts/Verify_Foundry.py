"""Verify real hosted versions, narrow bridges, representative orders and provider token receipts."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from azure.identity import AzureCliCredential

from Deploy_Foundry import API_VERSION, SUBSCRIPTION, TENANT, az
from Deploy_Foundry_Agents import package, request
from Verify_Isolated import require, verify


def run(args):
    runtime = json.loads(args.runtime_receipt.read_text(encoding="utf-8"))
    foundry = json.loads(args.foundry_receipt.read_text(encoding="utf-8"))
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "status": "verifying",
              "agents": [], "orders": [], "originalStacksTouched": False,
              "agent365": "not-enabled-or-claimed"}
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.resume_completed_orders else None
    if previous:
        require(previous.get("trustedBoundaryVerification", {}).get("status") == "passed"
                and len(previous.get("orders", [])) == 2, "Resume requires passed boundary acceptance and two orders")
        report.update(orders=previous["orders"],
                      trustedBoundaryVerification=previous["trustedBoundaryVerification"],
                      resumedFrom=previous["startedAt"])
    def save():
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    save()
    current = az("account", "show")
    require(current["id"] == SUBSCRIPTION and current["tenantId"] == TENANT, "Azure context mismatch")
    code_hash = hashlib.sha256(package()).hexdigest()
    with AzureCliCredential(tenant_id=TENANT, process_timeout=60) as credential:
        for binding in runtime["identities"]:
            name = binding["agent"].replace("_", "-")
            endpoint = (f"https://{foundry['accountName']}.services.ai.azure.com/api/projects/{name}"
                        f"/agents/amai-{name}")
            agent = request(credential, "GET", endpoint + "?api-version=v1")
            version = agent["versions"]["latest"]
            selected = agent["agent_endpoint"]["version_selector"]["version_selection_rules"]
            require(version["status"] == "active" and selected == [
                {"type": "FixedRatio", "agent_version": version["version"], "traffic_percentage": 100}],
                "Agent endpoint must be pinned to the active reviewed version")
            require(version["definition"]["code_configuration"]["content_hash"] == code_hash,
                    "Hosted code differs from the reviewed source")
            report["agents"].append({"agent": binding["agent"], "id": version["id"],
                                      "version": version["version"], "codeSha256": code_hash,
                                      "endpoint": endpoint + "/endpoint/protocols/invocations?api-version=v1",
                                      "instanceIdentity": agent["instance_identity"],
                                      "blueprint": agent["blueprint"], "selectedRules": selected})
            save()
        validation_endpoint = next(agent["endpoint"] for agent in report["agents"]
                                   if agent["agent"] == "validation")
        unauthenticated = httpx.post(validation_endpoint, json={"facts": {}}, timeout=30)
        require(unauthenticated.status_code == 401, "Hosted invocation accepted unauthenticated access")
        token = credential.get_token("https://ai.azure.com/.default").token
        forbidden = httpx.post(validation_endpoint, json={"facts": {}, "approveException": True},
                               headers={"Authorization": "Bearer " + token}, timeout=30)
        require(forbidden.status_code == 400, "Hosted agent accepted an approval field")
        report["hostedNegativeChecks"] = {"unauthenticated": 401, "approvalField": 400, "modelCalls": 0}
    model_scope = "https://management.azure.com" + foundry["accountId"] + "/deployments"
    models = az("rest", "--method", "GET", "--url", model_scope + "?api-version=" + API_VERSION)["value"]
    require({model["name"] for model in models} == {"amai-mini", "amai-nano"}, "Unexpected model deployments")
    for model in models:
        require(model["sku"]["name"] == "GlobalStandard"
                and model["properties"]["model"]["version"] == "2026-03-17",
                "Model SKU/version differs from the pinned acceptance configuration")
    report["models"] = models
    isolated_output = args.output.with_name("foundry-runtime-verification.json")
    if not previous:
        verify(SimpleNamespace(receipt=str(args.runtime_receipt), credentials=str(args.credentials),
                               output=str(isolated_output)))
        report["trustedBoundaryVerification"] = json.loads(isolated_output.read_text(encoding="utf-8"))
        report["orders"].append({"id": report["trustedBoundaryVerification"]["orderId"], "scenario": "auto-recovery"})
    save()
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    with httpx.Client(base_url=runtime["url"], timeout=30) as client:
        manager = {"Authorization": "Bearer " + credentials["admin"]}
        partner = {"Authorization": "Bearer " + credentials["partner"]}
        payload = {"customer": "Demo Foundry Normal Verification", "city": "Istanbul", "product": "sdwan",
                   "scenario": "happy", "language": "en", "idempotencyKey": "foundry-normal-" + uuid4().hex}
        if previous:
            normal_id = next(order["id"] for order in report["orders"] if order["scenario"] == "happy")
        else:
            response = client.post("/api/orders", headers=partner, json=payload)
            response.raise_for_status()
            normal_id = response.json()["id"]
            report["orders"].append({"id": normal_id, "scenario": "happy"})
        save()
        deadline = time.monotonic() + 180
        while True:
            response = client.get("/api/state", headers=manager)
            response.raise_for_status()
            state = response.json()
            if state["orders"][normal_id]["state"] == "completed":
                break
            require(time.monotonic() < deadline, "Actual hosted-agent normal order failed to complete")
            time.sleep(3)
        if not previous:
            replay = client.post("/api/orders", headers=partner, json=payload)
            replay.raise_for_status()
            require(replay.json()["id"] == normal_id, "Normal order replay created a duplicate")
        require(all(not order.get("reserved") and order["state"] != "completed" for order in state["orders"].values()
                    if order["scenario"] in ("human-exception", "change-window")),
                "Model/bridge bypassed a held approval or change-window guardrail")
    apps = az("containerapp", "list", "--resource-group", runtime["resourceGroup"])
    require(len(apps) == 7 and all(app["properties"]["template"]["containers"][0]["image"]
            == report["trustedBoundaryVerification"]["image"] for app in apps),
            "Runtime image changed after boundary acceptance")
    receipts, attempts, failures = [], [], []
    workspace = az("monitor", "log-analytics", "workspace", "show",
                   "--resource-group", runtime["resourceGroup"], "--workspace-name", runtime["logWorkspace"])
    query = ("ContainerAppConsoleLogs_CL | where TimeGenerated >= datetime("
             + report["trustedBoundaryVerification"]["at"] + ") "
             "| where Log_s contains 'foundry.invocation' | project TimeGenerated, ContainerAppName_s, Log_s")
    with AzureCliCredential(tenant_id=TENANT, process_timeout=60) as credential:
        response = httpx.post("https://api.loganalytics.azure.com/v1/workspaces/"
                              + workspace["customerId"] + "/query", json={"query": query},
                              headers={"Authorization": "Bearer " + credential.get_token(
                                  "https://api.loganalytics.io/.default").token}, timeout=90)
        response.raise_for_status()
        rows = response.json()["tables"][0]["rows"]
    report["durableLogEvidence"] = {"workspace": workspace["customerId"], "query": query,
                                   "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
    for at, app_name, log in rows:
        binding = next((binding for binding in runtime["identities"] if app_name.startswith(
            "amai-" + binding["agent"].replace("_", "-") + "-")), None)
        if not binding:
            continue
        if "foundry.invocation.receipt " in log:
            body = json.loads(log.split("foundry.invocation.receipt ", 1)[1])
            receipts.append({"at": at, **body})
        elif "foundry.invocation.started" in log:
            attempts.append({"at": at, "agent": binding["agent"], "log": log})
        elif "foundry.invocation.failed" in log:
            failures.append({"at": at, "agent": binding["agent"], "log": log, "usage": None})
    report.update(invocationReceipts=receipts, invocationAttempts=attempts, invocationFailures=failures)
    idle_start = datetime.fromisoformat(report["trustedBoundaryVerification"]["idleWindowStartedAt"])
    require(not any(idle_start <= datetime.fromisoformat(attempt["at"]) < idle_start + timedelta(seconds=65)
                    for attempt in attempts), "Quiescent idle polling invoked a hosted agent")
    report["noIdleHostedInvocations"] = True
    save()
    for order in report["orders"]:
        require(state["orders"][order["id"]]["state"] == "completed", "Accepted order is not completed")
        event_ids = {json.loads(row["detail"])["correlationId"] for row in state["audit"]
                     if row["orderId"] == order["id"] and row["action"] == "isolated.commit"}
        calls = [receipt for receipt in receipts if receipt["eventId"] in event_ids]
        order_attempts = [attempt for attempt in attempts
                          if any(event_id in attempt["log"] for event_id in event_ids)]
        require(calls, "No actual Foundry invocation receipts for completed order")
        require({call["eventId"] for call in calls} == event_ids,
                "Durable logs do not yet contain a receipt for every committed event")
        require(len(order_attempts) == len(calls), "Completed order has an unaccounted invocation attempt")
        usage = {key: 0 for key in ("input_tokens", "output_tokens", "reasoning_tokens",
                                   "cached_tokens", "cache_write_tokens")}
        per_agent = {agent: {"invocations": 0, "modelCalls": 0,
                            **{key: 0 for key in usage}} for agent in
                     ("validation", "decomposition", "provisioning", "fallout", "customer_update")}
        for call in calls:
            known = call["usage"]
            for key in usage:
                value = 0 if key == "cache_write_tokens" and call["modelCalls"] == 0 else known.get(key)
                require((key == "cache_write_tokens" and value is None)
                        or (type(value) is int and value >= 0), "Provider usage detail is missing")
                for total in (usage, per_agent[call["agent"]]):
                    total[key] = None if value is None or total[key] is None else total[key] + value
            count = per_agent[call["agent"]]
            count["invocations"] += 1
            count["modelCalls"] += call["modelCalls"]
            expected = next(agent["version"] for agent in report["agents"] if agent["agent"] == call["agent"])
            require(call.get("agentVersion") == expected, "Order invoked an unreviewed hosted version")
        require(not any(any(event_id in failure["log"] for event_id in event_ids) for failure in failures),
                "Order contains an invocation with unknown usage")
        expected_calls = {"decomposition": 1, "customer_update": 1}
        if order["scenario"] == "auto-recovery":
            expected_calls["fallout"] = 1
        require({agent: count["modelCalls"] for agent, count in per_agent.items() if count["modelCalls"]}
                == expected_calls, "Unexpected model invocation or retry count")
        require({agent: count["invocations"] for agent, count in per_agent.items()} == {
            "validation": 1, "decomposition": 1, "customer_update": 1,
            "provisioning": 2 if order["scenario"] == "auto-recovery" else 1,
            "fallout": 1 if order["scenario"] == "auto-recovery" else 0},
            "Unexpected hosted invocation count or missing restart-spanning receipt")
        order.update(usage=usage, totalTokens=usage["input_tokens"] + usage["output_tokens"],
                     perAgent=per_agent, receipts=calls, attempts=order_attempts, supervisorModelCalls=0,
                     cacheWriteUsage="Unknown if omitted by provider; null is not measured zero",
                     supervisorInvocationAllocation="Dispatch batches are shared; aggregate receipts retained")
        save()
    report.update(status="passed", finishedAt=datetime.now(timezone.utc).isoformat())
    save()
    print(json.dumps({"status": report["status"], "orders": [
        {"id": order["id"], "scenario": order["scenario"], "usage": order["usage"]} for order in report["orders"]]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--foundry-receipt", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-completed-orders", action="store_true",
                        help="Recheck pins and receipts of two orders after a passed boundary acceptance")
    args = parser.parse_args()
    try:
        run(args)
    except (RuntimeError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
        if args.output.exists():
            report = json.loads(args.output.read_text(encoding="utf-8"))
            report.update(status="blocked", error=str(error), finishedAt=datetime.now(timezone.utc).isoformat())
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise
