"""Verify the new dual-login hosted-agent app without changing its source application."""

import argparse
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from azure.identity import AzureCliCredential

from Deploy_Foundry_Agents import package, request
from Deploy_Isolated import az


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(args):
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    copied = json.loads(args.copy_receipt.read_text(encoding="utf-8"))
    require(copied["status"] == "copied-and-verified" and copied["target"] == runtime["resourceGroup"],
            "Verified full data-copy receipt required")
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "status": "verifying",
              "url": runtime["url"], "dataCopy": copied, "originalAppsModified": False}

    def save():
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    save()
    apps = az("containerapp", "list", "--resource-group", runtime["resourceGroup"],
              "--subscription", runtime["subscriptionId"], "-o", "json", json_result=True)
    require(len(apps) == 7, "Exactly seven isolated runtimes are required")
    expected_image = runtime["registryName"] + ".azurecr.io/amai:" + runtime["imageTag"]
    require(all(app["properties"]["template"]["containers"][0]["image"] == expected_image for app in apps),
            "Every runtime must use the same reviewed new image")
    report["image"] = expected_image
    report["rbac"] = []
    for identity in [runtime["boundaryIdentity"], *runtime["identities"]]:
        assignments = az("role", "assignment", "list", "--assignee", identity["oid"], "--all",
                         "--subscription", runtime["subscriptionId"], "-o", "json", json_result=True)
        roles = {row["roleDefinitionName"] for row in assignments}
        worker = identity.get("agent")
        expected = {"AcrPull", "Foundry Agent Consumer"} if worker else {"AcrPull", "Storage Blob Data Contributor"}
        require(roles == expected and len(assignments) == 2, "Unexpected additional runtime RBAC")
        scope = (next(row["scope"] for row in runtime["foundry"]["bindings"] if row["agent"] == worker)
                 if worker else f"/subscriptions/{runtime['subscriptionId']}/resourceGroups/{runtime['resourceGroup']}"
                 f"/providers/Microsoft.Storage/storageAccounts/{urlparse(runtime['storageUrl']).hostname.split('.')[0]}"
                 "/blobServices/default/containers/boundary")
        non_registry = next(row for row in assignments if row["roleDefinitionName"] != "AcrPull")
        require(non_registry["scope"].lower() == scope.lower(), "Runtime resource scope is too broad")
        report["rbac"].append({"agent": worker or "boundary", "roles": sorted(roles), "scope": scope})
    save()
    with AzureCliCredential(tenant_id=runtime["tenantId"], process_timeout=60) as credential:
        code_hash = hashlib.sha256(package()).hexdigest()
        report["hostedAgents"] = []
        for binding in runtime["foundry"]["bindings"]:
            endpoint = binding["endpoint"].split("/endpoint/")[0] + "?api-version=v1"
            body = request(credential, "GET", endpoint)
            version = body["versions"]["latest"]
            require(version["status"] == "active"
                    and version["definition"]["code_configuration"]["content_hash"] == code_hash,
                    "Hosted agent source differs from the reviewed implementation")
            rules = body["agent_endpoint"]["version_selector"]["version_selection_rules"]
            require(rules == [{"type": "FixedRatio", "agent_version": version["version"],
                               "traffic_percentage": 100}], "Hosted endpoint must retain its exact version pin")
            report["hostedAgents"].append({"agent": binding["agent"], "version": version["version"],
                                           "codeSha256": code_hash})
    save()
    with httpx.Client(base_url=runtime["url"], timeout=45, headers={
            "Origin": runtime["url"], "X-Requested-With": "foundry-app-verification"}) as client:
        tab = {"X-Session-Mode": "tab"}
        response = client.post("/api/auth/staff", headers=tab, json={"token": credentials["staffAccessToken"]})
        response.raise_for_status()
        operator = {**tab, "X-Session": response.json()["session"]}
        me = client.get("/api/auth/me", headers=operator).json()
        require(me["staffAuth"] == "sso+token" and me["foundryArchitecture"]
                and me["staff"]["role"] == "ServiceOperator", "Fallback must be an operator, not a manager")
        response = client.get("/auth/sso/login", follow_redirects=False)
        require(response.status_code == 302, "SSO must redirect to Microsoft")
        location = urlparse(response.headers["location"])
        query = parse_qs(location.query)
        require(location.hostname == "login.microsoftonline.com" and runtime["tenantId"] in location.path
                and query["client_id"] == [runtime["sso"]["ClientId"]]
                and query["redirect_uri"] == [runtime["url"] + "/auth/sso/callback"]
                and query["response_type"] == ["code"] and query.get("code_challenge"),
                "SSO must use this tenant/app/origin and PKCE")
        report["staffLogin"] = {"fallbackRole": "ServiceOperator", "ssoAuthorizationRedirect": "verified",
                                "interactiveMicrosoftSignIn": "requires-the-users-work-account"}
        client.cookies.clear()
        require(client.get("/api/state", headers=tab).status_code == 401, "Anonymous console access accepted")
        require(client.post("/api/auth/staff", headers=tab,
                            json={"token": credentials["admin"]}).status_code == 401,
                "Legacy manager token must not be a login fallback")
        run_id = secrets.token_hex(5)
        response = client.post("/api/auth/register", headers=tab, json={
            "name": "Demo Foundry SSO Contact", "company": "Demo Foundry SSO Acceptance",
            "email": f"foundry-sso-{run_id}@example.invalid", "language": "en",
            "password": secrets.token_urlsafe(24) + "aA1!", "consent": True})
        response.raise_for_status()
        account = response.json()
        customer = {**tab, "X-Session": account["session"]}
        client.post("/api/auth/verify", headers=customer, json={"code": account["simulatedCode"]}).raise_for_status()
        response = client.post(f"/api/enterprises/{account['account']['id']}/orders", headers=operator, json={
            "city": "Istanbul", "product": "sdwan", "scenario": "happy", "idempotencyKey": "sso-normal-" + run_id})
        response.raise_for_status()
        normal_id = response.json()["id"]
        report["orders"] = [{"id": normal_id, "scenario": "operator-with-enterprise-approval"}]
        save()
        time.sleep(6)
        state = client.get("/api/state", headers=operator).json()
        require(state["orders"][normal_id]["state"] == "awaitingEnterpriseApproval",
                "A worker bypassed enterprise approval")
        require(client.post(f"/api/orders/{normal_id}/decision", headers=operator,
                            json={"decision": "approve"}).status_code == 403,
                "Fallback operator must not make manager decisions")
        client.post(f"/api/me/orders/{normal_id}/approval", headers=customer,
                    json={"decision": "approve"}).raise_for_status()
        partner = {"Authorization": "Bearer " + credentials["partner"]}
        response = client.post("/api/orders", headers=partner, json={
            "customer": "Demo Foundry SSO Recovery", "city": "Ankara", "product": "sdwan",
            "scenario": "auto-recovery", "language": "en", "idempotencyKey": "sso-recovery-" + run_id})
        response.raise_for_status()
        recovery_id = response.json()["id"]
        report["orders"].append({"id": recovery_id, "scenario": "auto-recovery"})
        save()
        deadline = time.monotonic() + 420
        while True:
            response = client.get("/api/state", headers=operator)
            response.raise_for_status()
            state = response.json()
            if all(state["orders"][row["id"]]["state"] == "completed" for row in report["orders"]):
                break
            require(time.monotonic() < deadline, "Hosted normal/recovery orders did not complete")
            time.sleep(5)
        client.get("/health").raise_for_status()
        for row in report["orders"]:
            row["events"] = [json.loads(entry["detail"])["correlationId"] for entry in state["audit"]
                             if entry["orderId"] == row["id"] and entry["action"] == "isolated.commit"]
        report["enterpriseApprovalGuard"] = "passed"
        report["managerPrivilegeDenial"] = 403
        report["allSixRuntimesReady"] = True
        save()
    workspace = az("monitor", "log-analytics", "workspace", "show", "--resource-group", runtime["resourceGroup"],
                   "--workspace-name", runtime["logWorkspace"], "--subscription", runtime["subscriptionId"],
                   "-o", "json", json_result=True)
    query = ("ContainerAppConsoleLogs_CL | where TimeGenerated >= datetime(" + report["startedAt"] + ") "
             "| where Log_s contains 'foundry.invocation' or Log_s contains 'foundry.isolation.denied' "
             "| project TimeGenerated, ContainerAppName_s, Log_s")
    deadline = time.monotonic() + 420
    with AzureCliCredential(tenant_id=runtime["tenantId"], process_timeout=60) as credential:
        while True:
            response = httpx.post("https://api.loganalytics.azure.com/v1/workspaces/" + workspace["customerId"]
                                  + "/query", json={"query": query}, headers={"Authorization": "Bearer "
                                  + credential.get_token("https://api.loganalytics.io/.default").token}, timeout=90)
            response.raise_for_status()
            logs = response.json()["tables"][0]["rows"]
            receipts = [json.loads(line.split("foundry.invocation.receipt ", 1)[1])
                        for _, _, line in logs if "foundry.invocation.receipt " in line]
            events = {event for row in report["orders"] for event in row["events"]}
            if events <= {receipt["eventId"] for receipt in receipts}:
                break
            require(time.monotonic() < deadline, "Durable logs have not accounted for every committed hosted call")
            time.sleep(20)
    for row in report["orders"]:
        calls = [receipt for receipt in receipts if receipt["eventId"] in row["events"]]
        require(len(calls) == len(row["events"]), "Duplicate or missing invocation receipts")
        require(sum(call["modelCalls"] for call in calls) == (3 if row["id"] == recovery_id else 2),
                "Unexpected actual model-call count")
        row["receipts"] = calls
    report.update(status="passed", finishedAt=datetime.now(timezone.utc).isoformat(),
                  durableInvocationReceipts=True)
    save()
    print(json.dumps({"status": report["status"], "url": report["url"],
                      "dataPreserved": True, "orders": [row["id"] for row in report["orders"]]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--copy-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())
