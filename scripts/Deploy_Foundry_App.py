"""Deploy a new seven-app Foundry copy with Microsoft SSO and an operator-token fallback."""

import argparse
import json
import re
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

try:
    from . import Deploy_Isolated as isolated
except ImportError:
    import Deploy_Isolated as isolated

AGENTS = ("validation", "decomposition", "provisioning", "fallout", "customer_update", "supervisor")


def foundry_bindings(source, tenant, subscription):
    if source["tenantId"] != tenant or source["subscriptionId"] != subscription:
        raise ValueError("Foundry source tenant/subscription must match the new deployment")
    bindings = source["foundry"]["bindings"]
    if len(bindings) != 6 or {item["agent"] for item in bindings} != set(AGENTS):
        raise ValueError("Exactly six existing scoped Foundry bindings are required")
    result = {}
    for index, agent in enumerate(AGENTS):
        item = next(item for item in bindings if item["agent"] == agent)
        project = agent.replace("_", "-")
        pattern = (rf"https://[a-z0-9]+\.services\.ai\.azure\.com/api/projects/{project}"
                   rf"/agents/amai-{project}/endpoint/protocols/invocations\?api-version=v1")
        scope_pattern = (rf"/subscriptions/{re.escape(subscription)}/resourceGroups/rg-amai-foundry-[a-z0-9-]+"
                         rf"/providers/Microsoft.CognitiveServices/accounts/[a-z0-9]+/projects/{project}")
        if (not re.fullmatch(pattern, item["endpoint"]) or not re.fullmatch(scope_pattern, item["scope"])
                or item["role"] != "Foundry Agent Consumer"):
            raise ValueError("Only existing narrow hosted-agent endpoints and project scopes are permitted")
        other = next(row for row in bindings if row["agent"] == AGENTS[(index + 1) % 6])
        result[agent] = {"endpoint": item["endpoint"], "forbiddenEndpoint": other["endpoint"],
                         "scope": item["scope"]}
    return result


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def register_sso(receipt, manager):
    with tempfile.TemporaryDirectory(prefix="amai-sso-") as directory:
        path = Path(directory) / "registration.json"
        script = isolated.ROOT / "scripts" / "Register-StaffSso.ps1"
        command = (
            f"& {ps_quote(script)} -ResourceGroup {ps_quote(receipt['resourceGroup'])} "
            f"-Origin {ps_quote(receipt['boundaryOrigin'])} "
            f"-IdentityPrincipalId {ps_quote(receipt['boundaryIdentity']['oid'])} "
            f"-SubscriptionId {ps_quote(receipt['subscriptionId'])} -Managers {ps_quote(manager)} "
            f"-AllowUserConsent | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 {ps_quote(path)}"
        )
        response = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                   "-ExecutionPolicy", "RemoteSigned", "-Command", command],
                                  capture_output=True, text=True, encoding="utf-8")
        if response.returncode:
            raise RuntimeError("New SSO registration failed:\n" + response.stderr)
        registration = json.loads(path.read_text(encoding="utf-8-sig"))
        if registration["NotAssigned"] or not registration["Assigned"]:
            raise RuntimeError("The new SSO application has no verified manager assignment")
        if registration["TenantId"] != receipt["tenantId"]:
            raise ValueError("SSO registration tenant mismatch")
        return registration


def run(args):
    if not re.fullmatch(r"rg-amai-foundry-runtime-[a-z0-9-]+", args.resource_group):
        raise ValueError("Use a new rg-amai-foundry-runtime-* group")
    source = json.loads(Path(args.source_runtime).read_text(encoding="utf-8"))
    if source["resourceGroup"] == args.resource_group:
        raise ValueError("The existing Foundry runtime must remain unchanged")
    current = isolated.az("account", "show", "--subscription", args.subscription, "-o", "json", json_result=True)
    bindings = foundry_bindings(source, current["tenantId"], current["id"])
    def configure(receipt, credentials):
        receipt["sso"] = register_sso(receipt, args.manager)
        credentials["staffAccessToken"] = secrets.token_urlsafe(36)
        for identity in receipt["identities"]:
            scope = bindings[identity["agent"]]["scope"]
            isolated.az("role", "assignment", "create", "--assignee-object-id", identity["oid"],
                        "--assignee-principal-type", "ServicePrincipal", "--role", "Foundry Agent Consumer",
                        "--scope", scope, "--subscription", args.subscription,
                        "--name", str(uuid5(NAMESPACE_URL, identity["oid"] + scope)),
                        "-o", "none")
        receipt["foundry"] = {
            "accountId": source["foundry"]["accountId"],
            "bindings": [{"agent": agent, "principalId": next(row["oid"] for row in receipt["identities"]
                                                            if row["agent"] == agent),
                          "endpoint": binding["endpoint"], "scope": binding["scope"],
                          "role": "Foundry Agent Consumer"} for agent, binding in bindings.items()],
            "sourceRuntimeGroup": source["resourceGroup"],
        }
        return {"staffAuth": "sso+token", "ssoClientId": receipt["sso"]["ClientId"],
                "foundryBindings": bindings, "workersPaused": True}

    if args.resume:
        receipt_path = Path(args.receipt)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not receipt.get("imageTag"):
            group = isolated.az("group", "show", "--name", args.resource_group,
                                "--subscription", args.subscription, "-o", "json", json_result=True)
            if (receipt["resourceGroup"] != args.resource_group or receipt["tenantId"] != current["tenantId"]
                    or receipt["subscriptionId"] != args.subscription
                    or group.get("tags", {}).get("purpose") != "isolated-agent-acceptance"
                    or not receipt.get("servicePrincipalId")):
                raise ValueError("Only this deployment's completed bootstrap can be resumed")
            credentials = {name: secrets.token_urlsafe(36) for name in ("admin", "partner", "session")}
            receipt["appConfiguration"] = configure(receipt, credentials)
            isolated.save(Path(args.credentials), credentials)
            tag = "foundry-sso-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            receipt["imageTag"] = tag
            isolated.save(receipt_path, receipt)
            isolated.az("acr", "build", "--registry", receipt["registryName"], "--subscription", args.subscription,
                        "--image", f"amai:{tag}", str(isolated.ROOT), "--no-logs", "-o", "none")
            args.image_tag = tag
        isolated.resume(args)
    else:
        isolated.deploy(args, configure=configure)
    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["status"] = ("workers-paused-for-state-copy" if receipt["appConfiguration"]["workersPaused"]
                         else "deployed-awaiting-runtime-verification")
    isolated.save(receipt_path, receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--location", default="swedencentral", choices=["swedencentral"])
    parser.add_argument("--source-runtime", required=True)
    parser.add_argument("--manager", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--image-tag")
    run(parser.parse_args())
