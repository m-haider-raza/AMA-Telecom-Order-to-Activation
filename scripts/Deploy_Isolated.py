"""Create only a distinct synthetic acceptance stack with existing Azure CLI authorization."""

import argparse
import json
import re
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
AZ = shutil.which("az")


def az(*args, json_result=False):
    result = subprocess.run([AZ, *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        # CLI diagnostics are retained; tokens and secure parameter values are not printed here.
        raise RuntimeError(f"Azure {' '.join(args[:2])} failed:\n{result.stderr}")
    return json.loads(result.stdout) if json_result else result.stdout.strip()


def save(path, body):
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def resume(args):
    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (not args.resource_group.startswith("rg-amai-") or receipt["resourceGroup"] != args.resource_group
            or receipt["subscriptionId"] != args.subscription or not args.image_tag):
        raise ValueError("Resume requires this new stack's receipt and an explicit existing image tag")
    purpose = az("group", "show", "--name", args.resource_group, "--subscription", args.subscription,
                 "--query", "tags.purpose", "-o", "tsv")
    if purpose != "isolated-agent-acceptance":
        raise ValueError("Only the tagged new acceptance stack can be resumed")
    account = az("account", "show", "--subscription", args.subscription, "-o", "json", json_result=True)
    if account["tenantId"] != receipt["tenantId"] or account["state"] != "Enabled":
        raise ValueError("The original authorized tenant/subscription must match")
    az("acr", "repository", "show", "--name", receipt["registryName"], "--subscription", args.subscription,
       "--image", "amai:" + args.image_tag, "-o", "none")
    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    values = {"location": args.location, "deployApps": True, "audience": receipt["audience"],
              "principals": {item["agent"]: {"oid": item["oid"], "clientId": item["clientId"]}
                             for item in receipt["identities"]}, "imageTag": args.image_tag,
              "adminToken": credentials["admin"], "partnerToken": credentials["partner"],
              "sessionSecret": credentials["session"]}
    values.update(receipt.get("appConfiguration", {}))
    if "staffAccessToken" in credentials:
        values["staffAccessToken"] = credentials["staffAccessToken"]
    with tempfile.TemporaryDirectory(prefix="amai-resume-") as directory:
        parameters = Path(directory) / "parameters.json"
        save(parameters, {"parameters": {key: {"value": value} for key, value in values.items()}})
        final = az("deployment", "group", "create", "--name", "isolated-apps", "--template-file",
                   str(ROOT / "infra" / "isolated.bicep"), "--parameters", f"@{parameters}",
                   "--query", "properties.outputs", "-o", "json", "--resource-group", args.resource_group,
                   "--subscription", args.subscription, json_result=True)
    receipt.update(status="deployed-awaiting-runtime-verification", url=final["url"]["value"], imageTag=args.image_tag)
    receipt.pop("blocker", None)
    save(receipt_path, receipt)
    print(f"New isolated copy: {receipt['url']}")


def deploy(args, configure=None):
    if not AZ:
        raise RuntimeError("Azure CLI is required")
    if not re.fullmatch(r"rg-amai-[a-z0-9-]+", args.resource_group):
        raise ValueError("Only clearly distinct rg-amai-* stacks are permitted")
    receipt_path, credentials_path = Path(args.receipt), Path(args.credentials)
    if receipt_path.exists() or credentials_path.exists():
        raise ValueError("Receipt/credential paths must be new")
    account = az("account", "show", "--subscription", args.subscription, "-o", "json", json_result=True)
    if account["state"] != "Enabled" or account["environmentName"] != "AzureCloud":
        raise ValueError("An enabled existing AzureCloud authorization is required")
    if az("group", "exists", "--name", args.resource_group, "--subscription", args.subscription) == "true":
        raise ValueError("Refusing an existing resource group")
    az("bicep", "build", "--file", str(ROOT / "infra" / "isolated.bicep"), "--stdout")
    az("group", "create", "--name", args.resource_group, "--location", args.location,
       "--subscription", args.subscription, "--tags", "purpose=isolated-agent-acceptance", "data=synthetic-only",
       "-o", "none")
    receipt = {"resourceGroup": args.resource_group, "subscriptionId": args.subscription,
               "tenantId": account["tenantId"], "status": "infrastructure", "originalStacksTouched": False,
               "agent365": "not-registered", "createdAt": datetime.now(timezone.utc).isoformat()}
    save(receipt_path, receipt)
    target = ["--resource-group", args.resource_group, "--subscription", args.subscription]
    try:
        outputs = az("deployment", "group", "create", "--name", "isolated-bootstrap", "--template-file",
                     str(ROOT / "infra" / "isolated.bicep"), "--parameters", f"location={args.location}",
                     "--query", "properties.outputs", "-o", "json", *target, json_result=True)
        for name in ("identities", "registryName", "storageUrl", "logWorkspace",
                     "boundaryName", "boundaryOrigin", "boundaryIdentity"):
            receipt[name] = outputs[name]["value"]
        save(receipt_path, receipt)
        roles = [{"allowedMemberTypes": ["Application"], "description": f"Only {identity['agent']} contracts",
                  "displayName": f"Isolated {identity['agent']}", "id": str(uuid4()), "isEnabled": True,
                  "value": f"Worker.{identity['agent']}"} for identity in receipt["identities"]]
        with tempfile.TemporaryDirectory(prefix="amai-deploy-") as temporary:
            folder = Path(temporary)
            manifest = folder / "manifest.json"
            save(manifest, {"displayName": f"{args.resource_group}-tools", "signInAudience": "AzureADMyOrg",
                            "api": {"requestedAccessTokenVersion": 2}, "appRoles": roles})
            application = az("rest", "--method", "POST", "--uri", "https://graph.microsoft.com/v1.0/applications",
                             "--body", f"@{manifest}", "-o", "json", json_result=True)
            receipt.update(audience=application["appId"], applicationObjectId=application["id"])
            save(receipt_path, receipt)
            az("ad", "app", "update", "--id", application["id"], "--identifier-uris", f"api://{application['appId']}")
            service = az("ad", "sp", "create", "--id", application["appId"], "-o", "json", json_result=True)
            receipt["servicePrincipalId"] = service["id"]
            save(receipt_path, receipt)
            bindings = {}
            for identity, role in zip(receipt["identities"], roles, strict=True):
                bindings[identity["agent"]] = {"oid": identity["oid"], "clientId": identity["clientId"]}
                role_file = folder / "role.json"
                save(role_file, {"principalId": identity["oid"], "resourceId": service["id"], "appRoleId": role["id"]})
                az("rest", "--method", "POST", "--uri",
                   f"https://graph.microsoft.com/v1.0/servicePrincipals/{identity['oid']}/appRoleAssignments",
                   "--body", f"@{role_file}", "-o", "none")
            receipt["status"] = "app-roles-assigned"
            save(receipt_path, receipt)
            credentials = {name: secrets.token_urlsafe(36) for name in ("admin", "partner", "session")}
            if configure is not None:
                receipt["appConfiguration"] = configure(receipt, credentials)
            save(credentials_path, credentials)
            tag = "isolated-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            receipt["imageTag"] = tag
            save(receipt_path, receipt)
            az("acr", "build", "--registry", receipt["registryName"], "--subscription", args.subscription,
               "--image", f"amai:{tag}", str(ROOT), "--no-logs", "-o", "none")
            parameters = folder / "parameters.json"
            values = {"location": args.location, "deployApps": True, "audience": receipt["audience"],
                      "principals": bindings, "imageTag": tag, "adminToken": credentials["admin"],
                      "partnerToken": credentials["partner"], "sessionSecret": credentials["session"]}
            values.update(receipt.get("appConfiguration", {}))
            if "staffAccessToken" in credentials:
                values["staffAccessToken"] = credentials["staffAccessToken"]
            save(parameters, {"parameters": {key: {"value": value} for key, value in values.items()}})
            final = az("deployment", "group", "create", "--name", "isolated-apps", "--template-file",
                       str(ROOT / "infra" / "isolated.bicep"), "--parameters", f"@{parameters}",
                       "--query", "properties.outputs", "-o", "json", *target, json_result=True)
            receipt.update(status="deployed-awaiting-runtime-verification", url=final["url"]["value"], imageTag=tag)
            save(receipt_path, receipt)
            print(f"New isolated copy: {receipt['url']}")
    except Exception as error:
        receipt.update(status="blocked", blocker=str(error))
        save(receipt_path, receipt)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--location", default="swedencentral", choices=["swedencentral", "westeurope", "northeurope"])
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--image-tag")
    args = parser.parse_args()
    if args.resume:
        resume(args)
    else:
        deploy(args)
