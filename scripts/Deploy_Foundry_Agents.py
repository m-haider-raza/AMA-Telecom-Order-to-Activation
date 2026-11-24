"""Deploy bounded hosted-code agents only inside this deployment's verified Foundry project."""

import argparse
import hashlib
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential

from Deploy_Foundry import API_VERSION, PURPOSE, SUBSCRIPTION, TENANT, az, put

ROOT = Path(__file__).resolve().parents[1]


def request(credential, method, url, **kwargs):
    token = credential.get_token("https://ai.azure.com/.default").token
    headers = {"Authorization": "Bearer " + token, **kwargs.pop("headers", {})}
    response = httpx.request(method, url, headers=headers, timeout=180, **kwargs)
    if response.is_error:
        raise RuntimeError(json.dumps({"httpStatus": response.status_code,
                                      "requestId": response.headers.get("x-ms-request-id"),
                                      "correlationId": response.headers.get("x-ms-correlation-request-id"),
                                      "error": response.text}))
    return response.json()


def package():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in ("app/__init__.py", "app/isolated/__init__.py", "app/isolated/contracts.py",
                         "app/foundry/__init__.py", "app/foundry/contracts.py", "app/foundry/main.py"):
            path = ROOT.joinpath(*filename.split("/"))
            # Fixed archive timestamps make repeated uploads content-addressably idempotent.
            info = zipfile.ZipInfo(filename, (2026, 1, 1, 0, 0, 0))
            archive.writestr(info, path.read_bytes())
        archive.writestr(zipfile.ZipInfo("requirements.txt", (2026, 1, 1, 0, 0, 0)),
                         (ROOT / "app" / "foundry" / "requirements.txt").read_bytes())
    return stream.getvalue()


def deploy(receipt_path, output_path, agent):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    account = az("account", "show")
    if (account["id"] != SUBSCRIPTION or account["tenantId"] != TENANT
            or receipt["subscriptionId"] != SUBSCRIPTION or receipt["tenantId"] != TENANT):
        raise ValueError("Exact authorized tenant/subscription required")
    group = az("group", "show", "--name", receipt["resourceGroup"])
    if group.get("tags", {}).get("purpose") != PURPOSE:
        raise ValueError("An owned tagged pilot group is required")
    resource = az("rest", "--method", "GET", "--url",
                  f"https://management.azure.com{receipt['accountId']}?api-version={API_VERSION}")
    if resource["properties"]["provisioningState"] != "Succeeded":
        raise ValueError("A successfully provisioned Foundry account is required")
    project_name = agent.replace("_", "-")
    agent_name = "amai-" + project_name
    project_scope = receipt["accountId"] + "/projects/" + project_name
    if agent != "validation":
        projects = az("rest", "--method", "GET", "--url",
                      f"https://management.azure.com{receipt['accountId']}/projects?api-version={API_VERSION}")
        project = next((item for item in projects["value"] if item["id"].lower() == project_scope.lower()), None)
        if project is None:
            project = put(f"https://management.azure.com{project_scope}?api-version={API_VERSION}", {
                "location": receipt["region"], "identity": {"type": "SystemAssigned"},
                "properties": {"displayName": "Synthetic " + project_name, "description": "No real carrier tools"}})
        if project["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("Project did not provision successfully")
        user = az("ad", "signed-in-user", "show")["id"]
        az("role", "assignment", "create", "--assignee-object-id", user, "--assignee-principal-type", "User",
           "--role", "Foundry Project Manager", "--scope", project_scope)
    endpoint = f"https://{receipt['accountName']}.services.ai.azure.com/api/projects/{project_name}"
    result = {"startedAt": datetime.now(timezone.utc).isoformat(), "agent": agent,
              "projectEndpoint": endpoint, "status": "creating", "inferenceCallsIssued": 0}
    def save():
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    save()
    code = package()
    result["codeSha256"] = hashlib.sha256(code).hexdigest()
    definition = {"description": "Synthetic bounded proposal only; no infrastructure or approval tools",
                  "definition": {"kind": "hosted", "cpu": "1", "memory": "2Gi",
                                 "protocol_versions": [{"protocol": "invocations", "version": "2.0.0"}],
                                 "code_configuration": {"runtime": "python_3_13",
                                                        "entry_point": ["python", "-m", "app.foundry.main"],
                                                        "dependency_resolution": "remote_build"},
                                 "environment_variables": {"PYTHONNOUSERSITE": "1", "ISOLATED_AGENT": agent}}}
    with AzureCliCredential(tenant_id=TENANT, process_timeout=60) as credential:
        try:
            data = request(credential, "POST", f"{endpoint}/agents/{agent_name}/versions?api-version=v1",
                           headers={"x-ms-code-zip-sha256": result["codeSha256"]},
                           files={"metadata": (None, json.dumps(definition), "application/json"),
                                  "code": ("agent.zip", code, "application/zip")})
            result["createdVersion"] = data
            save()
            for _ in range(120):
                data = request(credential, "GET",
                               f"{endpoint}/agents/{agent_name}/versions/{data['version']}?api-version=v1")
                result["observedVersion"] = data
                save()
                if data.get("status") == "active":
                    break
                if data.get("status") == "failed":
                    raise RuntimeError(json.dumps(data.get("error")))
                time.sleep(10)
            else:
                raise RuntimeError("Hosted agent provisioning exceeded bounded twenty-minute wait")
            endpoint_config = {"agent_endpoint": {
                "version_selector": {"version_selection_rules": [
                    {"type": "FixedRatio", "agent_version": data["version"], "traffic_percentage": 100}]},
                "protocol_configuration": {"invocations": {}}}}
            result["selectedAgent"] = request(
                credential, "PATCH", f"{endpoint}/agents/{agent_name}?api-version=v1",
                headers={"Content-Type": "application/merge-patch+json"}, json=endpoint_config)
            result.update(status="active-awaiting-invocation",
                          endpoint=f"{endpoint}/agents/{agent_name}/endpoint/protocols/invocations?api-version=v1")
        except (RuntimeError, httpx.HTTPError, AzureError) as error:
            result.update(status="blocked", error=str(error))
            raise
        finally:
            result["finishedAt"] = datetime.now(timezone.utc).isoformat()
            save()
    print(json.dumps({"agent": agent, "status": result["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent", choices=["validation", "decomposition", "provisioning", "fallout",
                                          "customer_update", "supervisor"], required=True)
    args = parser.parse_args()
    deploy(args.receipt, args.output, args.agent)
