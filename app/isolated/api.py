"""Existing customer/partner/staff UX with no in-process agent loop."""

import json
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import auth, sso
from app.accounts_api import whoami
from app.domain import approve_exception, correct_order, public_order
from app.isolated.boundary import Boundary, Denied
from app.isolated.identity import EntraVerifier
from app.isolated.store import BlobStore, LocalStore
from app.isolated.telemetry import configure
from app.main import app as legacy_app, mcp as legacy_mcp, state as legacy_state, submit as legacy_submit
from app.schemas import OrderInput, configure_staff_log, transact
from app.security import authorize

log = logging.getLogger("ama.isolated.authorization")


def emit(record):
    log.info("isolated.authorization %s", json.dumps(record, sort_keys=True))


@asynccontextmanager
async def lifespan(application):
    configure("boundary")
    forbidden = ("AMA_SQL_SERVER", "AMA_SQL_DATABASE", "AZURE_STORAGE_ACCOUNT_URL", "AMA_SQL_ADMIN_TOKEN")
    if any(os.getenv(name) for name in forbidden):
        raise RuntimeError("Legacy storage/SQL settings are prohibited in the isolated copy")
    if os.getenv("AMA_MODE", "demo") != "demo" or os.getenv("AMA_DELIVERY", "simulated") != "simulated":
        raise RuntimeError("This isolated release permits synthetic fixtures and simulated delivery only")
    tokens = [os.getenv("DEMO_ADMIN_TOKEN", ""), os.getenv("DEMO_PARTNER_TOKEN", "")]
    if min(map(len, tokens)) < 24 or tokens[0] == tokens[1]:
        raise RuntimeError("New, distinct staff and partner credentials are required")
    if auth.staff_auth_mode() not in auth.STAFF_AUTH_MODES:
        raise RuntimeError("Invalid staff authentication mode")
    if auth.sso_enabled() and sso.configuration_problems():
        raise RuntimeError("New stack SSO configuration is incomplete")
    auth.validate_fallback()
    configure_staff_log()
    application.state.verifier = EntraVerifier.from_env()
    endpoint = os.getenv("ISOLATED_STORAGE_URL")
    if endpoint:
        application.state.store = BlobStore(endpoint, "boundary", os.environ["AZURE_CLIENT_ID"])
        credential = application.state.store.credential
        token = credential.get_token("api://" + os.environ["ISOLATED_AUDIENCE"] + "/.default").token
        bound = application.state.verifier.verify_boundary(
            token, os.environ["ISOLATED_BOUNDARY_PRINCIPAL_ID"], os.environ["AZURE_CLIENT_ID"])
        boundary_oid = bound.oid
    else:
        if os.getenv("ISOLATED_LOCAL") != "1":
            raise RuntimeError("Explicit ISOLATED_LOCAL=1 is required for local-only state")
        application.state.store = LocalStore(os.getenv("ISOLATED_LOCAL_PATH", "data/isolated.sqlite"))
        boundary_oid = None
    application.state.store.transact(lambda state: None)
    application.state.boundary = Boundary(application.state.store, emit, boundary_principal_id=boundary_oid)
    if boundary_oid:
        application.state.boundary.record(bound, "boundary.identity", "allow", "verified")
    application.state.runtime_seen = {}
    try:
        yield
    finally:
        application.state.store.close()


# Reuse endpoint contracts, not the legacy application instance or its background executor.
app = FastAPI(title="TurkBizTel isolated identities", version="2.0.0", lifespan=lifespan,
              routes=[route for route in legacy_app.routes if getattr(route, "path", None) not in {
                  "/health", "/api/state", "/api/auth/me", "/api/orders", "/tmf-api/productOrderingManagement/v4/productOrder",
                  "/mcp"}],
              middleware=list(legacy_app.user_middleware),
              exception_handlers=dict(legacy_app.exception_handlers))


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eventId: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal: dict


@app.middleware("http")
async def tool_guard(request: Request, call_next):
    if not request.url.path.startswith("/internal/"):
        # Agent bearer tokens are never portal/staff credentials.
        if request.headers.get("authorization", "").startswith("Bearer "):
            supplied = request.headers["authorization"][7:]
            if any(hmac.compare_digest(supplied, os.getenv(name, "")) for name in
                   ("DEMO_ADMIN_TOKEN", "DEMO_PARTNER_TOKEN")):
                return await call_next(request)
            try:
                caller = request.app.state.verifier.verify(supplied)
            except jwt.PyJWTError:
                caller = None
            request.app.state.boundary.record(caller, request.url.path, "deny", "agent-token-on-portal-route")
            return JSONResponse({"detail": "Agent credentials are accepted only by the tool boundary"}, 403)
        return await call_next(request)
    principal = None
    operation = request.url.path
    boundary = request.app.state.boundary
    try:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 16384:
            raise jwt.InvalidTokenError("Missing application credential")
        principal = request.app.state.verifier.verify(header[7:])
        if any(name in request.headers for name in ("x-agent", "x-agent-name", "x-principal-id")):
            raise Denied("Caller-supplied identity assertions are prohibited")
        request.state.principal = principal
        request.app.state.runtime_seen[principal.agent] = time.monotonic()
        response = await call_next(request)
        if response.status_code >= 400:
            event_id = getattr(request.state, "event_id", None)
            job = request.app.state.store.read().get("isolatedJobs", {}).get(event_id, {})
            boundary.record(principal, operation, "deny", f"http-{response.status_code}",
                            order_id=job.get("orderId"), correlation=event_id)
        elif request.url.path == "/internal/identity":
            boundary.record(principal, operation, "allow", "success")
        return response
    except jwt.InvalidTokenError:
        boundary.record(None, operation, "deny", "invalid-credential")
        return JSONResponse({"detail": "Invalid Entra application credential"}, 401)
    except jwt.PyJWKClientConnectionError:
        boundary.record(None, operation, "deny", "entra-signing-keys-unavailable")
        return JSONResponse({"detail": "Entra signing keys unavailable; no credential accepted"}, 503)
    except jwt.PyJWKClientError:
        boundary.record(None, operation, "deny", "unknown-entra-signing-key")
        return JSONResponse({"detail": "Invalid Entra signing key"}, 401)
    except Denied as error:
        boundary.record(principal, operation, "deny", str(error))
        return JSONResponse({"detail": str(error)}, 403)


@app.exception_handler(Denied)
async def denied_handler(request, error):
    request.state.denial_outcome = str(error)
    return JSONResponse({"detail": str(error)}, 403)


@app.post("/internal/dispatch")
def dispatch(request: Request):
    return request.app.state.boundary.dispatch(request.state.principal)


@app.get("/internal/work")
def work(request: Request):
    return request.app.state.boundary.work(request.state.principal)


@app.post("/internal/submit")
def submit(payload: Submission, request: Request):
    request.state.event_id = payload.eventId
    return request.app.state.boundary.submit(request.state.principal, payload.eventId, payload.operation,
                                             payload.revision, payload.proposal)


@app.get("/internal/identity")
def identity(request: Request):
    principal = request.state.principal
    return {"principalId": principal.oid, "agent": principal.agent, "clientId": principal.client_id}


@app.get("/isolated-health")
def isolated_health(request: Request):
    request.app.state.store.read()
    return {"status": "ok", "architecture": "isolated-v1", "networkProvisioning": "simulated",
            "agent365": "not-registered"}


@app.get("/health")
def health(request: Request):
    request.app.state.store.read()
    seen = request.app.state.runtime_seen
    expected = request.app.state.verifier.principals
    ready = all(time.monotonic() - seen.get(agent, 0) < 60 for agent in expected)
    return JSONResponse({"status": "ok" if ready else "degraded", "syntheticDataOnly": True,
                         "verifiedRuntimes": sorted(seen)}, status_code=200 if ready else 503)


@app.get("/api/auth/me")
def me(request: Request):
    return {**whoami(request), "enterpriseApprovals": True, "isolatedArchitecture": True,
            "foundryArchitecture": os.getenv("ISOLATED_FOUNDRY") == "1"}


@app.get("/api/state")
def state(request: Request):
    authorize(request, need="read")
    result = legacy_state(request)
    result.pop("isolatedJobs", None)
    result["configuration"].update(architecture="isolated-v1", agent365="not-registered",
                                   targetMonthlyBudgetUsd=None)
    if os.getenv("ISOLATED_FOUNDRY") == "1":
        result["configuration"].update(architecture="foundry-hosted-isolated-v1",
                                       aiMode="Microsoft Foundry hosted agents (task-scoped)")
    return result


@app.post("/api/orders", status_code=201)
@app.post("/tmf-api/productOrderingManagement/v4/productOrder", status_code=201)
def order_submit(payload: OrderInput, request: Request):
    actor = authorize(request, partner_allowed=True, need="operate")
    if actor == "service-manager":
        raise HTTPException(409, "Staff must use the enterprise order workflow; customer approval is required")
    return legacy_submit(payload, request)


@app.post("/mcp")
async def mcp(request: Request):
    actor = authorize(request, partner_allowed=True, need="operate")
    try:
        message = await request.json()
    except ValueError:
        return await legacy_mcp(request)
    if (isinstance(message, dict) and message.get("method") == "tools/call"
            and isinstance(message.get("params"), dict)
            and message["params"].get("name") == "submit_partner_order" and actor != "wholesale-partner"):
        raise HTTPException(403, "Only the authenticated partner channel can submit wholesale orders")
    return await legacy_mcp(request)


class BenchmarkDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "correct-site"]
    siteReference: str | None = None


@app.get("/api/isolated/benchmark/holds")
def benchmark_holds(request: Request):
    authorize(request, need="read")
    state = request.app.state.store.read()
    sim = state.get("benchmark", {}).get("sim", {})
    return {"orders": [public_order(order) for order in sim.get("orders", {}).values()
                       if order["state"] in {"held", "pendingApproval", "awaitingCustomer"}]}


@app.post("/api/isolated/benchmark/orders/{order_id}/decision")
def benchmark_decide(order_id: str, payload: BenchmarkDecision, request: Request):
    actor = authorize(request, need="manage")

    def change(state):
        sim = state.get("benchmark", {}).get("sim")
        if not sim or order_id not in sim["orders"]:
            raise HTTPException(404, "Benchmark order not found")
        if payload.decision == "correct-site":
            return public_order(correct_order(sim, order_id, actor, payload.siteReference))
        return public_order(approve_exception(sim, order_id, actor, payload.decision))
    return transact(request.app, change)


# Included legacy account routers may be represented as nested router routes by FastAPI.
# The isolated auth capability response must take precedence without mutating those routers.
app.router.routes.sort(key=lambda route: 0 if getattr(route, "path", None) == "/api/auth/me" else 1)
