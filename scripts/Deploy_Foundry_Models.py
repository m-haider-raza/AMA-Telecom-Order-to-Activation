"""Create only pinned small-model deployments in the owned Foundry pilot."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from azure.core.exceptions import AzureError

from Deploy_Foundry import API_VERSION, PURPOSE, SUBSCRIPTION, TENANT, az, put


def deploy(receipt_path, output_path):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    current = az("account", "show")
    if (current["id"] != SUBSCRIPTION or current["tenantId"] != TENANT
            or receipt["subscriptionId"] != SUBSCRIPTION or receipt["tenantId"] != TENANT):
        raise ValueError("Exact authorized tenant/subscription required")
    group = az("group", "show", "--name", receipt["resourceGroup"])
    if group.get("tags", {}).get("purpose") != PURPOSE:
        raise ValueError("Only this owned Foundry pilot is permitted")
    result = {"startedAt": datetime.now(timezone.utc).isoformat(), "accountId": receipt["accountId"],
              "deployments": [], "status": "creating"}
    def save():
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    save()
    try:
        for size in ("mini", "nano"):
            model = {"format": "OpenAI", "name": "gpt-5.4-" + size, "version": "2026-03-17"}
            scope = receipt["accountId"] + "/deployments/amai-" + size
            resource = put(f"https://management.azure.com{scope}?api-version={API_VERSION}", {
                "sku": {"name": "GlobalStandard", "capacity": 2},
                "properties": {"model": model, "versionUpgradeOption": "NoAutoUpgrade"}})
            result["deployments"].append(resource)
            save()
        result["status"] = "deployed-awaiting-inference"
    except (RuntimeError, AzureError) as error:
        result.update(status="blocked", error=str(error))
        raise
    finally:
        result["finishedAt"] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({"status": result["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    deploy(args.receipt, args.output)
