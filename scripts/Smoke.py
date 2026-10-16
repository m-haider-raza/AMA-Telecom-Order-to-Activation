"""Run against local or deployed demo; credentials stay out of console output."""
import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--credentials", type=Path, required=True)
args = parser.parse_args()
credentials = json.loads(args.credentials.read_text(encoding="utf-8-sig"))
base = credentials["demoUrl"].rstrip("/")


def call(path, body=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


assert call("/health")["status"] == "ok"
assert call("/api/state")["configuration"]["productionCompliant"] is False
partner = credentials["partnerToken"]
initialize = call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "ama-smoke", "version": "1.0"}}}, partner)
assert initialize["result"]["serverInfo"]["name"] == "ama-wholesale-simulator"
tools = call("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, partner)
assert len(tools["result"]["tools"]) == 2
payload = {"customer": "Demo Smoke Logistics", "city": "Istanbul", "product": "sdwan",
           "scenario": "auto-recovery", "language": "tr", "idempotencyKey": str(uuid4())}
result = call("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
    "name": "submit_partner_order", "arguments": payload}}, partner)
assert result["result"]["isError"] is False
order = json.loads(result["result"]["content"][0]["text"])
for _ in range(30):
    observed = call("/api/state")["orders"][order["id"]]
    if observed["state"] == "completed":
        break
    time.sleep(2)
assert observed["state"] == "completed", observed["state"]
assert observed["recoveryAttempts"] == 1
assert len({step["agent"] for step in observed["timeline"]}) == 5
assert observed["notifications"][0]["tr"] and observed["notifications"][0]["en"]
try:
    call(f"/api/orders/{order['id']}/decision", {"decision": "approve"}, partner)
    raise AssertionError("Partner was allowed to approve")
except HTTPError as error:
    assert error.code == 403
print("Demo smoke passed: authenticated MCP, all five agents, bounded recovery, bilingual updates, RBAC.")
