import asyncio
import csv
import hmac
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from azure.core.exceptions import AzureError
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app import advisor, analytics, auth, benchmark, partners, sso
from app.branded_static import BrandedStaticFiles
from app.accounts_api import router as accounts_router
from app.approvals_api import router as approvals_router
from app.partners_api import router as partners_router
from app.sso import router as sso_router
from app.delivery import configuration_problems, deliver_outbox
from app.domain import (approve_exception, audit, correct_order, create_order, customer_view, delivery_enabled,
                        follow_up, live_mode, metrics, partner_capacity, process_next, public_order,
                        tracking_token, update_contact, verify_audit)
from app.schemas import (BenchmarkRequest, Contact, Decision, OrderInput, PortalOrder,  # noqa: F401
                         SiteConfirmation, check_customer, configure_staff_log, transact)
from app.security import authorize, limit
from app.store import open_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# At INFO the Azure SDK prints every request and response header, which alone exhausts the daily log quota.
logging.getLogger("azure").setLevel(logging.WARNING)
log = logging.getLogger("ama.supervisor")
STATIC = Path(__file__).parent / "static"
PAGES = ("/", "/order", "/track", "/login", "/account", "/approve", "/console", "/partner", "/case-study",
         "/assignment")


async def supervisor(app):
    while True:
        try:
            def tick(state):
                for order_id in list(state["orders"]):
                    process_next(state, order_id)
                follow_up(state)
                benchmark.advance(state)
            await asyncio.to_thread(app.state.store.transact, tick)
            if delivery_enabled():
                await asyncio.to_thread(deliver_outbox, app.state.store)
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
    if auth.staff_auth_mode() not in auth.STAFF_AUTH_MODES:
        raise RuntimeError("AMA_STAFF_AUTH must be 'token', 'sso', or 'sso+token'")
    if auth.sso_enabled():
        problems = sso.configuration_problems()
        if problems:
            raise RuntimeError(f"AMA_STAFF_AUTH={auth.staff_auth_mode()} is not fully configured: " + "; ".join(problems))
    if auth.fallback_enabled():
        fallback = os.getenv("AMA_STAFF_ACCESS_TOKEN", "")
        if len(fallback) < 32 or fallback != fallback.strip() or any(fallback == os.getenv(name) for name in (
                "DEMO_ADMIN_TOKEN", "DEMO_PARTNER_TOKEN", "AMA_SESSION_SECRET", "AMA_TRACKING_SECRET")):
            raise RuntimeError("Set a distinct random AMA_STAFF_ACCESS_TOKEN of at least 32 characters")
    if problems := advisor.configuration_problems():
        raise RuntimeError("; ".join(problems))
    configure_staff_log()
    if delivery_enabled():
        problems = configuration_problems()
        if problems:
            raise RuntimeError("AMA_DELIVERY=live is not fully configured: " + "; ".join(problems))
    public_origin = os.getenv("DEMO_PUBLIC_ORIGIN")
    if public_origin:
        try:
            parsed = urlsplit(public_origin)
            port = parsed.port
            valid = (parsed.scheme == "https" and parsed.hostname and (port is None or port > 0)
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                     and not parsed.username and not parsed.password)
        except ValueError as exc:
            raise RuntimeError("DEMO_PUBLIC_ORIGIN must be an HTTPS origin without a path or credentials") from exc
        if not valid:
            raise RuntimeError("DEMO_PUBLIC_ORIGIN must be an HTTPS origin without a path or credentials")
    app.state.store = open_store()
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


app = FastAPI(title="TürkBizTel Activation", version="1.0.0", lifespan=lifespan)
app.include_router(accounts_router)
app.include_router(approvals_router)
app.include_router(partners_router)
app.include_router(sso_router)


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
    # Only this site may frame its own pages: the case-study page shows the live app in a frame.
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith(("/api", "/tmf-api", "/mcp")):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path in PAGES or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health")
def health(request: Request):
    healthy = time.monotonic() - request.app.state.worker_success < 60
    return JSONResponse({"status": "ok" if healthy else "degraded",
                         "syntheticDataOnly": not live_mode()}, status_code=200 if healthy else 503)


@app.get("/api/state")
def state(request: Request):
    if live_mode() or auth.sso_enabled():
        authorize(request, need="read")
    snapshot = request.app.state.store.read()
    for private in ("customers", "partners", "partnerUsers", "concerns", "benchmark"):
        snapshot.pop(private, None)
    snapshot["orders"] = {key: public_order(value) for key, value in snapshot["orders"].items()}
    return {
        **snapshot, "metrics": metrics(snapshot),
        "configuration": {
            "environment": "Live customer ordering" if live_mode() else "Synthetic-data demonstration",
            "region": os.getenv("AZURE_REGION", "local"),
            "aiMode": advisor.label(), "aiAdviceEnabled": advisor.configured(),
            "networkMode": "Simulated network / partner / CRM adapters",
            "customerDelivery": "E-mail and SMS" if delivery_enabled() else "Simulated (nothing is sent)",
            "productionCompliant": False,
            "targetMonthlyBudgetUsd": 100,
        },
    }


@app.post("/api/portal/orders", status_code=201)
def portal_submit(payload: PortalOrder, request: Request):
    limit(request)
    data = {**payload.model_dump(), "scenario": "happy"}
    order = transact(request.app, lambda snapshot: create_order(snapshot, data, "customer-portal"))
    token = tracking_token(order["id"])
    return {**customer_view(order), "trackingToken": token,
            "trackPath": f"/track#order={order['id']}&token={token}"}


def portal_order(request, snapshot, order_id):
    supplied = request.headers.get("x-tracking-token", "")
    order = snapshot["orders"].get(order_id)
    if order is None or not hmac.compare_digest(supplied, tracking_token(order_id)):
        raise HTTPException(404, "Order not found or tracking token is wrong")
    return order


@app.get("/api/portal/orders/{order_id}")
def portal_track(order_id: str, request: Request):
    limit(request)
    return customer_view(portal_order(request, request.app.state.store.read(), order_id))


@app.post("/api/portal/orders/{order_id}/address")
def portal_confirm_site(order_id: str, payload: SiteConfirmation, request: Request):
    limit(request)
    portal_order(request, request.app.state.store.read(), order_id)
    return customer_view(transact(request.app, lambda snapshot: correct_order(
        snapshot, order_id, "customer-portal", payload.siteReference)))


@app.put("/api/portal/orders/{order_id}/contact")
def portal_update_contact(order_id: str, payload: Contact, request: Request):
    limit(request)
    portal_order(request, request.app.state.store.read(), order_id)
    return customer_view(transact(request.app, lambda snapshot: update_contact(
        snapshot, order_id, payload.model_dump())))


def create_for_channel(snapshot, payload, actor):
    """Token and MCP orders belong to the shared partner channel; replaying a key never returns a partner's order."""
    order = create_order(snapshot, payload, actor)
    if actor == "wholesale-partner" and not partners.channel_owns(snapshot, order):
        raise ValueError("Idempotency key was already used for a different order")
    return order


@app.post("/api/orders", status_code=201)
@app.post("/tmf-api/productOrderingManagement/v4/productOrder", status_code=201)
def submit(payload: OrderInput, request: Request):
    actor = authorize(request, partner_allowed=True, need="operate")
    limit(request)
    if auth.sso_enabled() and actor == "service-manager":
        # An order placed by a person on the service team belongs to an enterprise and waits for its approval.
        raise HTTPException(409, "Service staff place an order for an enterprise from the console "
                                 "(POST /api/enterprises/{id}/orders), so its contact can approve it first")
    return public_order(transact(request.app, lambda snapshot: create_for_channel(
        snapshot, payload.model_dump(), actor)))


def find_order(snapshot, order_id):
    if order_id not in snapshot["orders"]:
        raise HTTPException(404, "Order not found")
    return snapshot["orders"][order_id]


@app.get("/tmf-api/productOrderingManagement/v4/productOrder")
def product_orders(request: Request):
    actor = authorize(request, partner_allowed=True, need="read")
    snapshot = request.app.state.store.read()
    orders = list(snapshot["orders"].values())
    if actor == "wholesale-partner":
        orders = [order for order in orders if partners.channel_owns(snapshot, order)]
    return [public_order(order) for order in orders]


@app.get("/tmf-api/productOrderingManagement/v4/productOrder/{order_id}")
def product_order(order_id: str, request: Request):
    actor = authorize(request, partner_allowed=True, need="read")
    snapshot = request.app.state.store.read()
    order = find_order(snapshot, order_id)
    if actor == "wholesale-partner" and not partners.channel_owns(snapshot, order):
        raise HTTPException(403, "Partner access is limited to partner-submitted orders")
    return public_order(order)


@app.get("/tmf-api/serviceOrdering/v4/serviceOrder")
def service_orders(request: Request):
    authorize(request, need="read")
    return [service for order in request.app.state.store.read()["orders"].values()
            for service in order.get("serviceOrders", [])]


@app.get("/tmf-api/resourceOrderManagement/v4/resourceOrder")
def resource_orders(request: Request):
    authorize(request, need="read")
    return [resource for order in request.app.state.store.read()["orders"].values()
            for resource in order.get("resourceOrders", [])]


@app.post("/api/orders/{order_id}/decision")
def decide(order_id: str, payload: Decision, request: Request):
    actor = authorize(request, need="manage")
    limit(request)
    find_order(request.app.state.store.read(), order_id)
    return public_order(transact(request.app, lambda snapshot: approve_exception(
        snapshot, order_id, actor, payload.decision,
    )))


@app.post("/api/orders/{order_id}/advice")
def advice(order_id: str, request: Request):
    actor = authorize(request, need="operate")
    limit(request)
    if not advisor.configured():
        find_order(request.app.state.store.read(), order_id)
        raise HTTPException(503, "No live AI advisor is configured. Rules-demo mode is explicitly enabled.")
    def reserve_advice(snapshot):
        order = find_order(snapshot, order_id)
        if order.get("enterpriseApproval") and order["enterpriseApproval"]["status"] != "approved":
            raise ValueError("Enterprise approval is required before requesting agent advice")
        day = datetime.now(timezone.utc).date().isoformat()
        usage = snapshot.setdefault("aiUsage", {})
        if usage.get("day") != day:
            usage.update(day=day, requests=0)
        if usage["requests"] >= 30:
            raise ValueError("The persisted 30-request daily AI demo limit has been reached")
        usage["requests"] += 1
        return order
    order = transact(request.app, reserve_advice)
    try:
        result = advisor.advise(order)
    except (httpx.HTTPError, AzureError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        log.warning("Live advisor request failed (%s)", type(exc).__name__)
        raise HTTPException(502, "Live AI request failed; no AI recommendation was saved or executed") from exc
    def save(snapshot):
        current = find_order(snapshot, order_id)
        current["aiAdvice"] = result
        audit(snapshot, order_id, actor, "live-ai-advice",
              f"Validated {advisor.label()} recommendation; executionAuthority=false")
        return result
    return transact(request.app, save)


@app.post("/api/orders/{order_id}/address")
def confirm_site(order_id: str, payload: SiteConfirmation, request: Request):
    actor = authorize(request, partner_allowed=True, need="operate")
    limit(request)
    current = request.app.state.store.read()
    order = find_order(current, order_id)
    if actor == "wholesale-partner" and not partners.channel_owns(current, order):
        raise HTTPException(409, "Only the submitting partner or service manager can correct this order")
    return public_order(transact(request.app, lambda snapshot: correct_order(
        snapshot, order_id, actor, payload.siteReference,
    )))


@app.get("/api/audit.csv")
def audit_csv(request: Request):
    authorize(request, need="read")
    entries = request.app.state.store.read()["audit"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "order", "actor", "action", "detail", "done_by", "upn", "role", "changes"])
    for item in entries:
        by = item.get("by") or {}
        changes = json.dumps(item["changes"], ensure_ascii=False, sort_keys=True) if item.get("changes") else ""
        values = [str(item.get(key, "")) for key in ("at", "orderId", "actor", "action", "detail")]
        values += [str(by.get("name", "")), str(by.get("upn", "")), str(by.get("role", "")), changes]
        writer.writerow(["'" + value if value.startswith(("=", "+", "-", "@")) else value for value in values])
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="ama-demo-audit.csv"'})


@app.get("/api/audit")
def audit_trail(request: Request, person: str = Query("", max_length=120), order: str = Query("", max_length=64),
                action: str = Query("", max_length=64), size: int = Query(100, ge=1, le=500, alias="limit")):
    """Who did what: the newest audit entries first, the people who have acted, and whether the hash chain that
    protects the trail is still intact. `person` matches a staff member's sign-in name or directory object ID."""
    authorize(request, need="read")
    snapshot = request.app.state.store.read()
    people = {}
    for item in snapshot["audit"]:
        by = item.get("by")
        if by:
            count = people.get(by["oid"], {}).get("actions", 0) + 1
            people[by["oid"]] = {**by, "actions": count, "lastAt": item["at"]}
    wanted = person.strip().lower()
    chosen = [item for item in snapshot["audit"]
              if (not wanted or wanted in (item.get("by", {}).get("oid", ""), item.get("by", {}).get("upn", "").lower()))
              and (not order or item["orderId"] == order) and item["action"].startswith(action)]
    shown = ("sequence", "at", "orderId", "actor", "action", "detail", "by", "changes", "hash")
    return {"verified": verify_audit(snapshot), "total": len(chosen),
            "people": sorted(people.values(), key=lambda row: row["lastAt"], reverse=True),
            "entries": [{key: item[key] for key in shown if key in item} for item in reversed(chosen[-size:])]}


@app.get("/api/analytics")
def analytics_report(request: Request, scope: Literal["auto", "all", "live", "benchmark"] = "auto"):
    if live_mode() or auth.sso_enabled():
        authorize(request, need="read")
    return analytics.compute(request.app.state.store.read(), scope)


@app.get("/api/analytics/source")
def analytics_source(request: Request):
    """Where the data is stored, and whether the SQL tables hold what the order book says they should."""
    if live_mode() or auth.sso_enabled():
        authorize(request, need="read")
    store = request.app.state.store
    if not hasattr(store, "inspect"):
        return {"kind": "azure-blob" if os.getenv("AZURE_STORAGE_ACCOUNT_URL") else "local-file",
                "note": "Azure SQL is not configured for this environment; the report is computed from the "
                        "order book snapshot."}
    try:
        return {"kind": "azure-sql", **store.inspect()}
    except Exception:
        log.exception("Could not inspect the SQL store")
        return {"kind": "azure-sql", "error": "The database could not be inspected right now"}


@app.post("/api/analytics/benchmark", status_code=202)
def analytics_benchmark(payload: BenchmarkRequest, request: Request):
    actor = authorize(request, need="operate")
    limit(request)
    return transact(request.app, lambda snapshot: benchmark.start(snapshot, payload.size, actor=actor))


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
    actor = authorize(request, partner_allowed=True, need="operate")
    limit(request)
    origin = request.headers.get("origin")
    expected_origin = os.getenv("DEMO_PUBLIC_ORIGIN") or str(request.base_url)
    if origin and origin.rstrip("/") != expected_origin.rstrip("/"):
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
                    order = create_for_channel(snapshot, payload.model_dump(), "wholesale-partner")
                    audit(snapshot, order["id"], actor, "partner.mcp.submit",
                          "Authenticated caller submitted through wholesale simulator wrapper")
                    return order
                value = public_order(transact(request.app, submit_partner))
            else:
                raise ValueError("Unknown partner tool")
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                      "isError": False}
        except (ValueError, HTTPException) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        error = {"code": -32601, "message": "Method not found"}
    return {"jsonrpc": "2.0", "id": identifier, **({"error": error} if error else {"result": result})}


app.mount("/static", BrandedStaticFiles(directory=STATIC), name="static")


def page(name):
    return FileResponse(STATIC / name)


@app.get("/", include_in_schema=False)
def index():
    """Live mode: the one landing page. Demo mode keeps the public control tower here so the synthetic
    demonstration stays open to everybody.

    No page asks who is signed in. Every browser tab keeps its own session, which the server only sees on API
    calls, so the page scripts check /api/auth/me themselves and send people to the area that is theirs."""
    return page("login.html" if live_mode() else "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    # An invitation link must still work for somebody who is signed in as someone else.
    return page("login.html")


@app.get("/console", include_in_schema=False)
def console_page():
    return page("index.html")


@app.get("/partner", include_in_schema=False)
def partner_page():
    return page("partner.html")


@app.get("/case-study", include_in_schema=False)
@app.get("/assignment", include_in_schema=False)
def case_study_page():
    """The guided assignment page: the problem today, the architecture, the solution film and the live app.
    It is public and holds no credentials or customer data."""
    return page("case-study.html")


@app.get("/account", include_in_schema=False)
def account_page():
    return page("account.html")


@app.get("/approve", include_in_schema=False)
def approve_page():
    """The page an enterprise's contact opens from the approval e-mail. It needs no sign-in: the signed token in
    the address fragment, which never reaches a server, is the credential."""
    return page("approve.html")


@app.get("/order", include_in_schema=False)
@app.get("/track", include_in_schema=False)
def portal_page():
    return page("portal.html")
