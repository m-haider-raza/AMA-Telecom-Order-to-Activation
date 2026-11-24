"""Provision a separately tagged Foundry pilot; never modify an existing application stack."""

import argparse
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

import httpx
from azure.identity import AzureCliCredential

AZ = shutil.which("az")
TENANT = "6828d9b6-f0ce-43c7-9e56-14bc61a6b93c"
SUBSCRIPTION = "9f674eb0-b40e-475d-bf85-a2a53238f25b"
PURPOSE = "foundry-agent-acceptance"
API_VERSION = "2026-11-01"


def az(*args):
    result = subprocess.run([AZ, *args, "-o", "json"], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def put(url, body, method="PUT"):
    with AzureCliCredential(tenant_id=TENANT, process_timeout=60) as credential:
        token = credential.get_token("https://management.azure.com/.default").token
        response = httpx.request(method, url, json=body,
                                 headers={"Authorization": "Bearer " + token}, timeout=120)
    if response.is_error:
        raise RuntimeError(json.dumps({"httpStatus": response.status_code,
                                      "requestId": response.headers.get("x-ms-request-id"),
                                      "correlationId": response.headers.get("x-ms-correlation-request-id"),
                                      "error": response.json()}))
    return response.json()


def bootstrap(group, account, receipt_path, location="swedencentral", two_stage=False):
    if not re.fullmatch(r"rg-amai-foundry-[a-z0-9-]+", group):
        raise ValueError("A new explicitly scoped Foundry group is required")
    if not re.fullmatch(r"amaif[a-z0-9]{6,30}", account):
        raise ValueError("An explicitly scoped Foundry account name is required")
    current = az("account", "show")
    if current["id"] != SUBSCRIPTION or current["tenantId"] != TENANT:
        raise ValueError("Authorized tenant and subscription must match exactly")
    receipt = {"resourceGroup": group, "accountName": account, "subscriptionId": SUBSCRIPTION,
               "tenantId": TENANT, "region": location, "purpose": PURPOSE,
               "createdAt": datetime.now(timezone.utc).isoformat(), "originalStacksTouched": False,
               "agent365": "not-enabled-or-claimed", "status": "starting"}
    receipt["twoStage"] = two_stage
    if receipt_path.exists():
        old = json.loads(receipt_path.read_text(encoding="utf-8"))
        if any(old.get(key, False if key == "twoStage" else None) != receipt[key] for key in
               ("resourceGroup", "accountName", "subscriptionId", "tenantId", "region", "twoStage")):
            raise ValueError("Receipt target mismatch")
        receipt = old
        existing = az("group", "show", "--name", group)
        if existing.get("tags", {}).get("purpose") != PURPOSE:
            raise ValueError("Resume requires this deployment's purpose tag")
    else:
        if az("group", "exists", "--name", group):
            raise ValueError("Refusing to adopt an existing group")
        az("group", "create", "--name", group, "--location", location,
           "--tags", "purpose=" + PURPOSE, "data=synthetic-only")
    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    save()
    scope = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{group}/providers/Microsoft.CognitiveServices/accounts/{account}"
    receipt["accountId"] = scope
    try:
        properties = {"customSubDomainName": account, "disableLocalAuth": True, "publicNetworkAccess": "Enabled"}
        if not two_stage:
            properties["allowProjectManagement"] = True
        url = "https://management.azure.com" + scope + "?api-version=" + API_VERSION
        resource = put(url, {
            "location": location, "kind": "AIServices", "sku": {"name": "S0"},
            "identity": {"type": "SystemAssigned"}, "tags": {"purpose": PURPOSE},
            "properties": properties})
        for _ in range(60):
            state = resource["properties"].get("provisioningState")
            if state == "Succeeded":
                break
            if state in ("Failed", "Canceled"):
                raise RuntimeError(f"Account provisioning failed: {state}")
            time.sleep(5)
            resource = az("rest", "--method", "GET", "--url", url)
        else:
            raise RuntimeError("Account provisioning did not finish within five minutes")
        if two_stage:
            receipt["plainAccountState"] = state
            save()
            resource = put(url, {"properties": {"allowProjectManagement": True}}, method="PATCH")
        receipt["accountProvisioningState"] = resource["properties"].get("provisioningState")
        save()
        project_scope = scope + "/projects/validation"
        project = put("https://management.azure.com" + project_scope + "?api-version=" + API_VERSION, {
            "location": location, "identity": {"type": "SystemAssigned"},
            "properties": {"displayName": "Synthetic validation pilot", "description": "No real carrier tools"}})
        receipt["pilotProject"] = project
        receipt["projectEndpoint"] = f"https://{account}.services.ai.azure.com/api/projects/validation"
        save()
        user = az("ad", "signed-in-user", "show")["id"]
        role = "eadc314b-1a2d-4efa-be10-5d325db5065e"
        assignment = str(uuid5(NAMESPACE_URL, project_scope + user + role))
        put(f"https://management.azure.com{project_scope}/providers/Microsoft.Authorization/roleAssignments/"
            f"{assignment}?api-version=2022-04-01", {
                "properties": {"principalId": user, "principalType": "User",
                               "roleDefinitionId": f"/subscriptions/{SUBSCRIPTION}/providers/"
                                                   f"Microsoft.Authorization/roleDefinitions/{role}"}})
        receipt["deploymentRole"] = {"principalId": user, "roleId": role, "scope": project_scope}
        receipt["status"] = "pilot-project-ready"
        save()
    except Exception as error:
        receipt.update(status="blocked", blocker=str(error))
        save()
        raise
    print(json.dumps({"status": receipt["status"], "endpoint": receipt["projectEndpoint"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--location", choices=["swedencentral", "westeurope", "eastus2"], default="swedencentral")
    parser.add_argument("--two-stage", action="store_true")
    args = parser.parse_args()
    bootstrap(args.resource_group, args.account, args.receipt, args.location, args.two_stage)
