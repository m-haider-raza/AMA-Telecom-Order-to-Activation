"""Verify real synthetic API routes; never report simulated success as Azure deployment."""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def verify_demo(client, manager, partner, *, allow_manager_decisions=False, timeout=120, poll_seconds=1):
    if not allow_manager_decisions:
        raise ValueError("Explicitly allow audited synthetic manager decisions before running this demo")
    require(len(manager) >= 24 and len(partner) >= 24 and manager != partner, "Distinct demo tokens are required")

    def call(method, path, body=None, role=None, expected=200):
        headers = {"Authorization": f"Bearer {manager if role == 'manager' else partner}"} if role else {}
        response = client.request(method, path, json=body, headers=headers)
        require(response.status_code == expected,
                f"{method} {path}: expected HTTP {expected}, received {response.status_code}")
        return response.json()

    def rpc(method, params=None):
        result = call("POST", "/mcp", {
            "jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params or {},
        }, "partner")
        require("error" not in result, "MCP returned a protocol error")
        return result["result"]

    require(call("GET", "/health")["status"] == "ok", "Supervisor is not healthy")
    before = call("GET", "/api/state")
    require(before["configuration"]["productionCompliant"] is False, "Expected a clearly labelled synthetic demo")
    require(len(before["orders"]) <= 190, "Too few free order slots; use a separate synthetic demo environment")
    inventory = next(item for item in before["inventory"]
                     if item["city"] == "Istanbul" and item["product"] == "connectivity")
    require(inventory["availableUnits"] >= 6, "This walkthrough needs six free simulated capacity units")
    initialized = rpc("initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "ama-live-demo-verifier", "version": "1.0"},
    })
    require(initialized["serverInfo"]["name"] == "ama-wholesale-simulator", "Unexpected MCP server")
    require({tool["name"] for tool in rpc("tools/list")["tools"]} ==
            {"check_partner_capacity", "submit_partner_order"}, "Unexpected partner tool authority")
    capacity = rpc("tools/call", {"name": "check_partner_capacity", "arguments": {"city": "Istanbul"}})
    require(not capacity["isError"], "Partner capacity tool failed")
    cases = [
        ("straight-through", "happy", None),
        ("timeout-recovery", "auto-recovery", None),
        ("manager-approval", "human-exception", "approve"),
        ("manager-rejection", "human-exception", "reject"),
        ("window-release", "change-window", "approve"),
        ("customer-correction", "address-correction", "correct"),
    ]
    tracked, holds, actions = {}, {}, {}
    duplicate_checked = False
    for name, scenario, decision in cases:
        payload = {"customer": "Demo Verified Walkthrough", "city": "Istanbul", "product": "connectivity",
                   "scenario": scenario, "language": "tr", "idempotencyKey": uuid4().hex}
        if name == "timeout-recovery":
            result = rpc("tools/call", {"name": "submit_partner_order", "arguments": payload})
            require(not result["isError"], "Partner order submission failed")
            order = json.loads(result["content"][0]["text"])
            require(order["actor"] == "wholesale-partner", "MCP did not preserve partner identity")
        else:
            order = call("POST", "/tmf-api/productOrderingManagement/v4/productOrder", payload, "manager", 201)
            require(order["actor"] == "service-manager", "The manager credential does not have the manager role")
        tracked[name] = {"id": order["id"], "scenario": scenario, "decision": decision}
        if name == "straight-through":
            duplicate = call("POST", "/tmf-api/productOrderingManagement/v4/productOrder", payload, "manager", 201)
            require(duplicate["id"] == order["id"], "Duplicate acceptance created a second order")
            invalid = {**payload, "product": "unsupported-product", "idempotencyKey": uuid4().hex}
            call("POST", "/api/orders", invalid, "manager", 422)
            conflict = {**payload, "customer": "Demo Different Intent"}
            call("POST", "/api/orders", conflict, "manager", 409)
            duplicate_checked = True
    deadline = time.monotonic() + timeout
    while True:
        snapshot = call("GET", "/api/state")
        terminal = True
        for name, item in tracked.items():
            order = snapshot["orders"][item["id"]]
            if order["state"] in {"completed", "rejected", "failed"}:
                continue
            terminal = False
            if name in actions or not order["exception"]:
                continue
            decision = item["decision"]
            require(decision is not None, f"Unexpected hold on {name}: {order['exception']['type']}")
            require(not order["reserved"], f"{name} allocated resources before its explicit input")
            holds[name] = {"state": order["state"], "exception": order["exception"], "reserved": order["reserved"]}
            if decision == "correct":
                require(order["state"] == "awaitingCustomer", "Site correction did not pause for customer input")
                call("POST", f"/api/orders/{order['id']}/address", {"siteReference": "IST-001"}, "manager")
                actions[name] = "customer.address-corrected"
            else:
                expected_exception = "change-window" if name == "window-release" else "route-conflict"
                require(order["exception"]["type"] == expected_exception, f"Wrong exception on {name}")
                call("GET", f"/tmf-api/productOrderingManagement/v4/productOrder/{order['id']}", role="partner", expected=403)
                call("POST", f"/api/orders/{order['id']}/decision", {"decision": "approve"}, "partner", 403)
                held = call("GET", "/api/state")["orders"][order["id"]]
                require(held["state"] == order["state"] and not held["approvals"],
                        "A denied partner request changed the held order")
                call("POST", f"/api/orders/{order['id']}/decision", {"decision": decision}, "manager")
                actions[name] = f"exception.{decision}"
        if terminal:
            break
        require(time.monotonic() < deadline, "Workflow verification timed out; inspect persisted holds and supervisor health")
        time.sleep(poll_seconds)
    require(snapshot["metrics"]["auditChainValid"], "Audit hash chain verification failed")
    report_cases = []
    for name, item in tracked.items():
        order = snapshot["orders"][item["id"]]
        rejected = item["decision"] == "reject"
        require(order["state"] == ("rejected" if rejected else "completed"), f"{name} did not reach its expected result")
        entries = order["timeline"]
        tools = [entry["tool"] for entry in entries]
        require("feasibility.check" in tools and "TMF622.to.TMF641" in tools, f"{name} skipped validation or planning")
        require(all(link["productOrderId"] == order["id"]
                    for link in order["serviceOrders"] + order["resourceOrders"]), "Linked work lost order identity")
        if rejected:
            require(not order["reserved"] and "partner.capacity+network.activate" not in tools,
                    "Rejected work was allocated or activated")
        else:
            require(order["reserved"] and tools.count("partner.capacity+network.activate") == 1,
                    f"{name} did not allocate exactly once")
            require(tools[-1] == "customer.notify.tr-en", "Customer success was announced before final activation")
            require(all(link["state"] == "completed" for link in order["serviceOrders"] + order["resourceOrders"]),
                    "Completed order has incomplete linked work")
        require(all(message["tr"] and message["en"] and not message["deliveredExternally"]
                    for message in order["notifications"]) and order["notifications"], "Missing bilingual portal evidence")
        if name == "timeout-recovery":
            require(order["recoveryAttempts"] == 1 and len({entry["agent"] for entry in entries}) == 5,
                    "Recovery did not execute all five agents with one retry")
            require(tools.index("partner.reserve") < tools.index("playbook.partner-timeout.retry-once") <
                    tools.index("partner.capacity+network.activate"), "Recovery did not hand control back correctly")
        if item["decision"]:
            require(name in holds, f"{name} did not expose a visible input gate")
            require(any(entry["orderId"] == order["id"] and entry["action"] == actions[name]
                        for entry in snapshot["audit"]), f"{name} input was not audited")
        report_cases.append({
            "name": name, "orderId": order["id"], "state": order["state"],
            "holdObserved": holds.get(name), "inputRecorded": actions.get(name),
            "partnerApprovalDenied": item["decision"] in {"approve", "reject"},
            "order": order,
            "events": [event for event in snapshot["events"] if event["orderId"] == order["id"]],
            "audit": [entry for entry in snapshot["audit"] if entry["orderId"] == order["id"]],
        })
    return {
        "status": "passed", "configuration": snapshot["configuration"], "cases": report_cases,
        "idempotentAcceptanceVerified": duplicate_checked, "unsupportedProductRejected": True,
        "conflictingIdempotencyKeyRejected": True, "auditChainValid": True,
        "note": "Observed synthetic APIs and audited automated demo decisions, not a human acceptance or production test. "
                "This does not prove Azure deployment, Türkiye residency, actual carrier integration or business KPIs.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-manager-decisions", action="store_true",
                        help="Allow this test to record synthetic manager approvals/rejections and customer corrections")
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Existing evidence is protected; choose a fresh report path")
    credentials = json.loads(args.credentials.read_text(encoding="utf-8-sig"))
    url = urlparse(credentials["demoUrl"])
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"})) or (
        url.username or url.password or url.query or url.fragment
    ):
        raise ValueError("Use HTTPS, or loopback HTTP for local verification; never put credentials in the URL")
    with httpx.Client(base_url=credentials["demoUrl"].rstrip("/"), timeout=30, follow_redirects=False,
                      headers={"Origin": f"{url.scheme}://{url.netloc}"}) as client:
        report = verify_demo(client, credentials["adminToken"], credentials["partnerToken"],
                             allow_manager_decisions=args.allow_manager_decisions)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=False)
        target.write("\n")
    print("Six live synthetic routes passed; all five specialists, holds, decisions, recovery and RBAC verified.")
    print(f"Evidence saved locally: {args.report}")


if __name__ == "__main__":
    main()
