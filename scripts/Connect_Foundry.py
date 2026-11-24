"""Connect only new isolated managed-identity bridges to their own Foundry projects."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from Deploy_Foundry import PURPOSE, SUBSCRIPTION, TENANT, az


def connect(runtime_path, foundry_path):
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    foundry = json.loads(foundry_path.read_text(encoding="utf-8"))
    current = az("account", "show")
    if (current["id"] != SUBSCRIPTION or current["tenantId"] != TENANT
            or any(r["subscriptionId"] != SUBSCRIPTION or r["tenantId"] != TENANT for r in (runtime, foundry))):
        raise ValueError("Original authorized tenant/subscription must match")
    if not runtime["resourceGroup"].startswith("rg-amai-foundry-runtime-"):
        raise ValueError("Only the new Foundry bridge stack is permitted")
    for receipt, purpose in ((runtime, "isolated-agent-acceptance"), (foundry, PURPOSE)):
        group = az("group", "show", "--name", receipt["resourceGroup"])
        if group.get("tags", {}).get("purpose") != purpose:
            raise ValueError("Deployment purpose tag mismatch")
    apps = az("containerapp", "list", "--resource-group", runtime["resourceGroup"])
    runtime["foundry"] = {"accountId": foundry["accountId"], "bindings": [], "status": "connecting"}
    def save():
        runtime_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    save()
    try:
        for index, binding in enumerate(runtime["identities"]):
            agent = binding["agent"]
            project = agent.replace("_", "-")
            own_scope = foundry["accountId"] + "/projects/" + project
            own_endpoint = (f"https://{foundry['accountName']}.services.ai.azure.com/api/projects/{project}"
                            f"/agents/amai-{project}/endpoint/protocols/invocations?api-version=v1")
            other = runtime["identities"][(index + 1) % len(runtime["identities"])]["agent"].replace("_", "-")
            other_endpoint = (f"https://{foundry['accountName']}.services.ai.azure.com/api/projects/{other}"
                              f"/agents/amai-{other}/endpoint/protocols/invocations?api-version=v1")
            app = next(item for item in apps if item["name"].startswith("amai-" + project + "-"))
            attached = app["identity"]["userAssignedIdentities"]
            if len(attached) != 1 or binding["resourceId"].lower() not in {key.lower() for key in attached}:
                raise ValueError("Unexpected identity attachment")
            az("role", "assignment", "create", "--assignee-object-id", binding["oid"],
               "--assignee-principal-type", "ServicePrincipal", "--role", "Foundry Agent Consumer",
               "--scope", own_scope)
            az("containerapp", "update", "--name", app["name"], "--resource-group", runtime["resourceGroup"],
               "--set-env-vars", "FOUNDRY_AGENT_ENDPOINT=" + own_endpoint,
               "FOUNDRY_FORBIDDEN_ENDPOINT=" + other_endpoint)
            runtime["foundry"]["bindings"].append({"agent": agent, "principalId": binding["oid"],
                                                  "scope": own_scope, "endpoint": own_endpoint,
                                                  "role": "Foundry Agent Consumer"})
            save()
        runtime["foundry"].update(status="connected-awaiting-verification",
                                 connectedAt=datetime.now(timezone.utc).isoformat())
    except (RuntimeError, ValueError) as error:
        runtime["foundry"].update(status="blocked", error=str(error))
        raise
    finally:
        save()
    print(json.dumps({"status": runtime["foundry"]["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--foundry-receipt", type=Path, required=True)
    args = parser.parse_args()
    connect(args.runtime_receipt, args.foundry_receipt)
