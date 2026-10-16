import asyncio
import csv
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from azure.core.exceptions import AzureError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import advisor
from app.domain import approve_exception, audit, correct_order, create_order, metrics, partner_capacity, process_next
from app.security import authorize, limit
from app.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ama.supervisor")
STATIC = Path(__file__).parent / "static"


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: str = Field(min_length=5, max_length=80, pattern=r"^Demo ")
    city: Literal["Istanbul", "Ankara", "Izmir"]
    product: Literal["connectivity", "sdwan", "cloud"]
    scenario: Literal["happy", "auto-recovery", "human-exception", "change-window", "address-correction"] = "happy"
    language: Literal["tr", "en"] = "tr"
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    siteReference: Literal["IST-001", "ANK-001", "IZM-001", "DEMO-UNKNOWN"] | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


class SiteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    siteReference: Literal["IST-001", "ANK-001", "IZM-001"]


def transact(app, action):
    try:
        return app.state.store.transact(action)
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc


async def supervisor(app):
    while True:
        try:
            def tick(state):
                for order_id in list(state["orders"]):
                    process_next(state, order_id)
            await asyncio.to_thread(app.state.store.transact, tick)
            app.state.worker_success = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed transaction must remain visible and retry without losing queued work.
            log.exception("Supervisor transaction failed; persisted work will be retried")
        await asyncio.sleep(float(os.getenv("DEMO_TICK_SECONDS", "2")))


@asynccontextmanager
async def lifespan(app):
    if len(os.getenv("DEMO_ADMIN_TOKEN", "")) < 24 or len(os.getenv("DEMO_PARTNER_TOKEN", "")) < 24:
        raise RuntimeError("Set distinct DEMO_ADMIN_TOKEN and DEMO_PARTNER_TOKEN values of at least 24 characters")
    if os.environ["DEMO_ADMIN_TOKEN"] == os.environ["DEMO_PARTNER_TOKEN"]:
        raise RuntimeError("Manager and partner tokens must differ")
    app.state.store = Store()
    app.state.store.transact(lambda state: None)
    app.state.worker_success = time.monotonic()
    task = asyncio.create_task(supervisor(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app.state.store.close()


app = FastAPI(title="AMA Telecom Activation", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def headers(request, call_next):
    if request.headers.get("content-length", "0").isdigit():
        if int(request.headers.get("content-length", "0")) > 16384:
            return JSONResponse({"detail": "Request is too large"}, status_code=413)
    if request.method == "POST" and len(await request.body()) > 16384:
        return JSONResponse({"detail": "Request is too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith(("/api", "/tmf-api", "/mcp")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health(request: Request):
    healthy = time.monotonic() - request.app.state.worker_success < 60
    return JSONResponse({"status": "ok" if healthy else "degraded",
                         "syntheticDataOnly": True}, status_code=200 if healthy else 503)


@app.get("/api/state")
def state(request: Request):
    snapshot = request.app.state.store.read()
    return {
        **snapshot, "metrics": metrics(snapshot),
        "configuration": {
            "environment": "Synthetic-data demonstration",
            "region": os.getenv("AZURE_REGION", "local"),
            "aiMode": "Regional Azure OpenAI advisor" if advisor.configured() else "Deterministic rules demo",
            "networkMode": "Simulated network / partner / CRM adapters",
            "productionCompliant": False,
            "targetMonthlyBudgetUsd": 100,
        },
    }


@app.post("/api/orders", status_code=201)
@app.post("/tmf-api/productOrderingManagement/v4/productOrder", status_code=201)
def submit(payload: OrderInput, request: Request):
    actor = authorize(request, partner_allowed=True)
    limit(request)
    return transact(request.app, lambda snapshot: create_order(snapshot, payload.model_dump(), actor))


def find_order(snapshot, order_id):
    if order_id not in snapshot["orders"]:
        raise HTTPException(404, "Order not found")
    return snapshot["orders"][order_id]


@app.get("/tmf-api/productOrderingManagement/v4/productOrder")
def product_orders(request: Request):
    actor = authorize(request, partner_allowed=True)
    orders = list(request.app.state.store.read()["orders"].values())
    if actor == "wholesale-partner":
        orders = [order for order in orders if order.get("actor") == actor]
    return orders


@app.get("/tmf-api/productOrderingManagement/v4/productOrder/{order_id}")
def product_order(order_id: str, request: Request):
    actor = authorize(request, partner_allowed=True)
    order = find_order(request.app.state.store.read(), order_id)
    if actor == "wholesale-partner" and order.get("actor") != actor:
        raise HTTPException(403, "Partner access is limited to partner-submitted orders")
    return order


@app.get("/tmf-api/serviceOrdering/v4/serviceOrder")
def service_orders(request: Request):
    authorize(request)
    return [service for order in request.app.state.store.read()["orders"].values()
            for service in order.get("serviceOrders", [])]


@app.get("/tmf-api/resourceOrderManagement/v4/resourceOrder")
def resource_orders(request: Request):
    authorize(request)
    return [resource for order in request.app.state.store.read()["orders"].values()
            for resource in order.get("resourceOrders", [])]


@app.post("/api/orders/{order_id}/decision")
def decide(order_id: str, payload: Decision, request: Request):
    actor = authorize(request)
    limit(request)
    find_order(request.app.state.store.read(), order_id)
    return transact(request.app, lambda snapshot: approve_exception(
        snapshot, order_id, actor, payload.decision,
    ))


@app.post("/api/orders/{order_id}/advice")
def advice(order_id: str, request: Request):
    actor = authorize(request)
    limit(request)
    order = find_order(request.app.state.store.read(), order_id)
    if not advisor.configured():
        raise HTTPException(503, "Live Azure OpenAI is not configured. Rules-demo mode is explicitly enabled.")
    def reserve_advice(snapshot):
        day = datetime.now(timezone.utc).date().isoformat()
        usage = snapshot.setdefault("aiUsage", {})
        if usage.get("day") != day:
            usage.update(day=day, requests=0)
        if usage["requests"] >= 30:
            raise ValueError("The persisted 30-request daily AI demo limit has been reached")
        usage["requests"] += 1
    transact(request.app, reserve_advice)
    try:
        result = advisor.advise(order)
    except (httpx.HTTPError, AzureError, ValidationError, KeyError, RuntimeError) as exc:
        log.exception("Live advisor request failed")
        raise HTTPException(502, "Live AI request failed; no AI recommendation was saved or executed") from exc
    def save(snapshot):
        current = find_order(snapshot, order_id)
        current["aiAdvice"] = result
        audit(snapshot, order_id, actor, "live-ai-advice",
              "Validated regional AI recommendation; executionAuthority=false")
        return result
    return transact(request.app, save)


@app.post("/api/orders/{order_id}/address")
def confirm_site(order_id: str, payload: SiteConfirmation, request: Request):
    actor = authorize(request, partner_allowed=True)
    limit(request)
    find_order(request.app.state.store.read(), order_id)
    return transact(request.app, lambda snapshot: correct_order(
        snapshot, order_id, actor, payload.siteReference,
    ))


@app.get("/api/audit.csv")
def audit_csv(request: Request):
    authorize(request)
    entries = request.app.state.store.read()["audit"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "order", "actor", "action", "detail"])
    for item in entries:
        values = [str(item.get(key, "")) for key in ("at", "orderId", "actor", "action", "detail")]
        writer.writerow(["'" + value if value.startswith(("=", "+", "-", "@")) else value for value in values])
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="ama-demo-audit.csv"'})


MCP_TOOLS = [{
    "name": "check_partner_capacity",
    "description": "Read simulated wholesale capacity for a Turkish city. Does not provision a live network.",
    "inputSchema": {"type": "object", "properties": {
        "city": {"type": "string", "enum": ["Istanbul", "Ankara", "Izmir"]}},
        "required": ["city"], "additionalProperties": False},
}, {
    "name": "submit_partner_order",
    "description": "Submit a synthetic wholesale order to the guarded order supervisor.",
    "inputSchema": OrderInput.model_json_schema(),
}]


@app.post("/mcp")
async def mcp(request: Request):
    actor = authorize(request, partner_allowed=True)
    limit(request)
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin MCP requests are not accepted")
    try:
        message = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Expected a JSON-RPC request") from exc
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid request"}}, status_code=400)
    if not isinstance(message.get("method"), str) or not isinstance(message.get("params", {}), dict):
        return {"jsonrpc": "2.0", "id": message.get("id"),
                "error": {"code": -32600, "message": "Method must be a string and params must be an object"}}
    identifier = message.get("id")
    method = message.get("method")
    if method in ("notifications/initialized", "notifications/cancelled") and "id" not in message:
        return Response(status_code=202)
    result = None
    error = None
    if method == "initialize":
        requested = message.get("params", {}).get("protocolVersion")
        version = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18") else "2025-03-26"
        result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "ama-wholesale-simulator", "version": "1.0.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": MCP_TOOLS}
    elif method == "tools/call":
        params = message.get("params", {})
        args = params.get("arguments", {})
        try:
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be an object")
            if params.get("name") == "check_partner_capacity":
                if set(args) != {"city"} or args["city"] not in ("Istanbul", "Ankara", "Izmir"):
                    raise ValueError("A supported city is required")
                value = partner_capacity(request.app.state.store.read(), args["city"])
            elif params.get("name") == "submit_partner_order":
                payload = OrderInput.model_validate(args)
                def submit_partner(snapshot):
                    order = create_order(snapshot, payload.model_dump(), "wholesale-partner")
                    audit(snapshot, order["id"], actor, "partner.mcp.submit",
                          "Authenticated caller submitted through wholesale simulator wrapper")
                    return order
                value = transact(request.app, submit_partner)
            else:
                raise ValueError("Unknown partner tool")
            import json
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                      "isError": False}
        except (ValueError, HTTPException) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        error = {"code": -32601, "message": "Method not found"}
    return {"jsonrpc": "2.0", "id": identifier, **({"error": error} if error else {"result": result})}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
