"""Small, customer-data-free contracts; no worker receives a state snapshot."""

AGENTS = {
    "validation": ("Order Validation Agent", "validation.submit"),
    "decomposition": ("Order Decomposition Agent", "plan.create"),
    "provisioning": ("Provisioning Orchestration Agent", "reserve.activate"),
    "fallout": ("Fallout Resolution Agent", "recovery.retry-once"),
    "customer_update": ("Customer Update Agent", "notification.queue"),
}
VERSION = "isolated-v1"


def propose(agent, facts):
    """Worker computation is advisory until independently checked against current boundary facts."""
    if agent == "validation":
        return {"validSite": facts["siteReference"] == facts["expectedSite"],
                "hasCapacity": facts["availableUnits"] > 0}
    if agent == "decomposition":
        return {"services": facts["services"], "resources": facts["resources"]}
    if agent == "provisioning":
        return {"tool": "simulated.reserve.activate", "units": 1, "planId": facts["planId"]}
    if agent == "fallout":
        return {"playbook": "partner-timeout.retry-once", "attempt": 1}
    if agent == "customer_update":
        return {"template": "synthetic-activation-tr-en", "orderId": facts["orderId"]}
    raise ValueError("Unknown worker")
