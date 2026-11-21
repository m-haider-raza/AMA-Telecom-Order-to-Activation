"""The enterprise picker for the service team, and the approval an enterprise gives before its order can start.

The staff routes need the operate level. The two link routes are public, because the signed token in the e-mail is
the only credential an enterprise's contact needs: they refuse a request without the XHR header, are throttled per
address, count every bad token against that throttle, and never answer 401, so a page that is not behind a sign-in
is never sent to one. Deciding takes a POST, which a mail scanner that merely opens the link cannot send.
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import Field

from app import approvals, auth
from app.accounts_api import Strict, signed_in, with_order
from app.domain import public_order
from app.schemas import transact
from app.security import authorize, limit

router = APIRouter()


class EnterpriseOrder(Strict):
    city: Literal["Istanbul", "Ankara", "Izmir"]
    product: Literal["connectivity", "sdwan", "cloud"]
    scenario: Literal["happy", "auto-recovery", "human-exception", "change-window", "address-correction"] = "happy"
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


class Answer(Strict):
    decision: Literal["approve", "decline"]


class LinkCheck(Strict):
    token: str = Field(min_length=20, max_length=400)


class LinkAnswer(LinkCheck):
    decision: Literal["approve", "decline"]


def send_later(request, background, order):
    record = order["enterpriseApproval"]
    if record["delivery"]["status"] == "pending":
        background.add_task(approvals.send_approval, request.app.state.store, order["id"], record["version"])


@router.get("/api/enterprises")
def enterprises(request: Request):
    authorize(request, need="operate")
    return {"enterprises": approvals.directory(request.app.state.store.read())}


@router.post("/api/enterprises/{enterprise_id}/orders")
def place_for_enterprise(enterprise_id: str, payload: EnterpriseOrder, request: Request, response: Response,
                         background: BackgroundTasks):
    """Place an order on an enterprise's behalf. It waits for the enterprise's contact, who is e-mailed now."""
    actor = authorize(request, need="operate")
    limit(request)

    def run(state):
        customer = state.get("customers", {}).get(enterprise_id)
        if customer is None:
            raise HTTPException(404, "We could not find that enterprise")
        return approvals.place(state, customer, payload.model_dump(), actor)

    order, created = transact(request.app, run)
    response.status_code = 201 if created else 200
    if created:
        send_later(request, background, order)
    return public_order(order)


def staff_action(request, order_id, action):
    actor = authorize(request, need="operate")
    limit(request)

    def run(state):
        if order_id not in state["orders"]:
            raise HTTPException(404, "Order not found")
        return action(state, order_id, actor)

    return transact(request.app, run)


@router.post("/api/orders/{order_id}/approval/resend")
def resend_request(order_id: str, request: Request, background: BackgroundTasks):
    order = staff_action(request, order_id, approvals.resend)
    send_later(request, background, order)
    return public_order(order)


@router.post("/api/orders/{order_id}/approval/withdraw")
def withdraw_request(order_id: str, request: Request):
    return public_order(staff_action(request, order_id, approvals.withdraw))


def guarded(request):
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.APPROVAL_IP, address)
    return address


def read_link(request, address, token):
    order, problem = approvals.inspect_link(request.app.state.store.read(), token)
    if problem == "invalid":
        auth.APPROVAL_IP.add(address)
        raise HTTPException(404, approvals.BAD_LINK)
    return order, problem


@router.post("/api/approvals/check")
def check_link(payload: LinkCheck, request: Request):
    order, problem = read_link(request, guarded(request), payload.token)
    if problem:
        return {"status": problem, "message": approvals.LINK_PROBLEMS[problem]}
    return {"status": approvals.link_status(order["enterpriseApproval"]), **approvals.summary(order)}


@router.post("/api/approvals/decide")
def answer_link(payload: LinkAnswer, request: Request):
    _, problem = read_link(request, guarded(request), payload.token)
    if problem:
        raise HTTPException(409, approvals.LINK_PROBLEMS[problem])
    order = transact(request.app, lambda state: approvals.decide_by_link(state, payload.token, payload.decision))
    return {"status": order["enterpriseApproval"]["status"], **approvals.summary(order)}


@router.post("/api/me/orders/{order_id}/approval")
def answer_in_account(order_id: str, payload: Answer, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    return with_order(request, customer, order_id,
                      lambda state, order: approvals.decide(state, order["id"], payload.decision, "account"))
