"""Sign-in for customers, partner contacts and staff, e-mail verification, password reset, partner invitations,
and the signed-in customer's own orders.

Customers only ever reach orders whose ownerId is their own account; any other order answers 404, exactly as a
missing one would. Staff and partner APIs reject customer sessions in app.security.authorize. Partner contacts
sign in through the same form as customers and are sent to their own portal (see app.partners_api).
"""

import hmac
import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import accounts, auth, identity, partners
from app.delivery import send_account_email
from app.domain import (SITES, correct_order, delivery_enabled, live_mode, owner_view, update_contact,
                        update_site)
from app.schemas import Contact, PortalOrder, check_customer, transact
from app.security import limit
from app.sso import public_staff, record_staff_event

log = logging.getLogger("ama.accounts")
router = APIRouter()

EMAIL = r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$"
PHONE = r"^\+[1-9][0-9]{7,14}$"
BAD_LOGIN = "That email and password don't match. Check them and try again, or reset your password."
BAD_RESET = "That code isn't valid. Check it, or ask for a new code."
BAD_INVITE = "That invitation link isn't valid or has expired. Ask your TürkBizTel contact to send a new one."
DISABLED_PARTNER = "This partner account is switched off. Please contact your TürkBizTel service manager."
CODE_PROBLEMS = {
    "wrong": "That code isn't right. Check the latest email and try again.",
    "expired": "That code has expired. Send yourself a new one.",
    "locked": "Too many wrong attempts. Send yourself a new code.",
    "missing": "There is no active code. Send yourself a new one.",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Registration(Strict):
    name: str = Field(min_length=2, max_length=80)
    company: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=120, pattern=EMAIL)
    phone: str | None = Field(default=None, pattern=PHONE)
    language: Literal["tr", "en"] = "en"
    password: str = Field(max_length=128)
    consent: Literal[True]

    @field_validator("phone", mode="before")
    @classmethod
    def blank_phone(cls, value):
        return value or None

    @field_validator("company")
    @classmethod
    def company_rule(cls, value):
        return check_customer(value)


class Profile(Strict):
    name: str = Field(min_length=2, max_length=80)
    company: str = Field(min_length=2, max_length=80)
    phone: str | None = Field(default=None, pattern=PHONE)
    language: Literal["tr", "en"] = "en"

    @field_validator("phone", mode="before")
    @classmethod
    def blank_phone(cls, value):
        return value or None

    @field_validator("company")
    @classmethod
    def company_rule(cls, value):
        return check_customer(value)


class Login(Strict):
    email: str = Field(max_length=120)
    password: str = Field(max_length=128)


class StaffLogin(Strict):
    token: str = Field(min_length=1, max_length=200)


class CodeInput(Strict):
    code: str = Field(pattern=r"^[0-9]{6}$")


class Forgot(Strict):
    email: str = Field(max_length=120)


class Reset(Strict):
    email: str = Field(max_length=120)
    code: str = Field(pattern=r"^[0-9]{6}$")
    password: str = Field(max_length=128)


class Invite(Strict):
    token: str = Field(min_length=20, max_length=200)


class InviteAccept(Invite):
    password: str = Field(max_length=128)


class PasswordChange(Strict):
    current: str = Field(max_length=128)
    password: str = Field(max_length=128)


class LinkOrder(Strict):
    orderId: str = Field(pattern=r"^AMA-[A-Z0-9]{10}$")
    trackingToken: str = Field(min_length=10, max_length=100)


class SiteUpdate(Strict):
    city: Literal["Istanbul", "Ankara", "Izmir"]
    siteAddress: str = Field(min_length=5, max_length=200)


def strong_password(password, email):
    try:
        auth.check_password(password, email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def send_code(email, name, purpose, code, language):
    try:
        send_account_email(email, name, purpose, code, language)
    except Exception as exc:  # a mail outage must not break the request; the customer can ask for another code
        log.warning("Could not send the %s code: %s", purpose, type(exc).__name__)


def deliver_code(background, purpose, customer, code):
    """E-mail the code when delivery is live. Only a demo site may show it on screen; a live site that cannot send
    e-mail must never hand a one-time code to whoever asked for it."""
    if delivery_enabled():
        background.add_task(send_code, customer["email"], customer["name"], purpose, code, customer["language"])
        return {"delivery": "email"}
    if live_mode():
        log.error("AMA_MODE=live needs AMA_DELIVERY=live to send %s codes; none was sent", purpose)
        return {"delivery": "unavailable"}
    return {"delivery": "simulated", "simulatedCode": code}


def start_session(request, response, account, role="customer"):
    return auth.grant(request, response, auth.issue_session(role, account["id"], account["sessionVersion"]))


def signed_in(request):
    """The signed-in customer and a fresh read of the state; 401 without a session, 403 for other kinds of sign-in."""
    session = auth.request_session(request)
    if session is None:
        raise HTTPException(401, "Please sign in to continue")
    if session["r"] != "customer":
        raise HTTPException(403, "This sign-in belongs to another part of TürkBizTel. Sign out to use a customer account.")
    auth.require_csrf(request)
    snapshot = request.app.state.store.read()
    customer = snapshot.get("customers", {}).get(session["s"])
    if customer is None or customer["sessionVersion"] != session["v"]:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return customer, snapshot


def owned(state, customer_id, order_id):
    order = state["orders"].get(order_id)
    if order is None or order.get("ownerId") != customer_id:
        raise HTTPException(404, "We could not find that order")
    return order


def with_order(request, customer, order_id, action):
    def run(state):
        return action(state, owned(state, customer["id"], order_id))
    return owner_view(transact(request.app, run))


@router.get("/api/auth/me")
def whoami(request: Request):
    base = {"authenticated": False, "mode": "live" if live_mode() else "demo", "delivery": delivery_enabled(),
            "staffAuth": auth.staff_auth_mode()}
    who = identity.resolve(request)
    if who is None:
        return base
    if who["role"] == "admin":
        staff = who.get("staff")
        if staff:
            return {**base, "authenticated": True, "role": "admin", "name": staff["name"], "staff": public_staff(staff)}
        return {**base, "authenticated": True, "role": "admin", "name": "Service manager"}
    if who["role"] == "partner":
        return {**base, "authenticated": True, "role": "partner",
                "account": partners.account_view(who["user"], who["partner"]),
                "partner": partners.partner_view(who["partner"])}
    return {**base, "authenticated": True, "role": "customer", "account": accounts.public_account(who["customer"])}


@router.post("/api/auth/register", status_code=201)
def register(payload: Registration, request: Request, response: Response, background: BackgroundTasks):
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.MAIL_IP, address)
    email = accounts.normalise(payload.email)
    strong_password(payload.password, email)
    auth.MAIL_IP.add(address)
    code, record = auth.new_code("verify", email)
    password_hash = auth.hash_password(payload.password)
    data = payload.model_dump()
    customer = transact(request.app, lambda state: accounts.register(state, data, password_hash, record))
    handover = start_session(request, response, customer)
    return {"account": accounts.public_account(customer), **handover,
            **deliver_code(background, "verify", customer, code)}


@router.post("/api/auth/verify")
def verify(payload: CodeInput, request: Request):
    customer, _ = signed_in(request)
    result = transact(request.app, lambda state: accounts.verify(state, customer["id"], payload.code))
    if not result["ok"]:
        raise HTTPException(422, CODE_PROBLEMS[result["reason"]])
    return {"verified": True, "claimed": result["claimed"]}


@router.post("/api/auth/resend")
def resend(request: Request, background: BackgroundTasks):
    customer, _ = signed_in(request)
    address = auth.client_ip(request)
    auth.guard(auth.MAIL_IP, address)
    code, record = auth.new_code("verify", customer["email"])
    outcome = transact(request.app, lambda state: accounts.refresh_verification(state, customer["id"], record))
    if outcome == "verified":
        return {"verified": True}
    if outcome == "cooldown":
        raise HTTPException(429, "Please wait a minute before asking for another code.", headers={"Retry-After": "60"})
    auth.MAIL_IP.add(address)
    return {"sent": True, **deliver_code(background, "verify", customer, code)}


@router.post("/api/auth/login")
def login(payload: Login, request: Request, response: Response):
    """One sign-in form for customers and partner contacts; the reply says which portal to open."""
    auth.require_csrf(request)
    email, address = accounts.normalise(payload.email), auth.client_ip(request)
    checks = ((auth.LOGIN_PAIR, f"{email}|{address}"), (auth.LOGIN_IP, address), (auth.LOGIN_ACCOUNT, email))
    for window, key in checks:
        auth.guard(window, key)
    snapshot = request.app.state.store.read()
    kind, account = accounts.find_account(snapshot, email)
    correct = auth.verify_password(payload.password, account.get("passwordHash") if account else None)
    if not (account and correct):
        for window, key in checks:
            window.add(key)
        raise HTTPException(401, BAD_LOGIN)
    auth.LOGIN_PAIR.clear(checks[0][1])
    if kind == "partner":
        partner = snapshot["partners"].get(account["partnerId"])
        if not partner or partner["status"] != "active":
            raise HTTPException(403, DISABLED_PARTNER)
        handover = start_session(request, response, account, "partner")
        return {"role": "partner", "account": partners.account_view(account, partner), **handover}
    handover = start_session(request, response, account)
    return {"role": "customer", "account": accounts.public_account(account), **handover}


@router.post("/api/auth/invite/check")
def check_invite(payload: Invite, request: Request):
    """Lets the invitation page greet the invited contact before they choose a password."""
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.LOGIN_IP, address)
    snapshot = request.app.state.store.read()
    user = partners.invite_owner(snapshot, payload.token.strip())
    partner = snapshot["partners"].get(user["partnerId"]) if user else None
    if not (user and partner and partner["status"] == "active") or time.time() > user["invite"]["expires"]:
        auth.LOGIN_IP.add(address)
        raise HTTPException(422, BAD_INVITE)
    return {"name": user["name"], "email": user["email"], "partner": partner["name"]}


@router.post("/api/auth/invite/accept")
def accept_invite(payload: InviteAccept, request: Request, response: Response):
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.LOGIN_IP, address)
    token = payload.token.strip()
    owner = partners.invite_owner(request.app.state.store.read(), token)
    if owner is None:
        auth.LOGIN_IP.add(address)
        raise HTTPException(422, BAD_INVITE)
    strong_password(payload.password, owner["email"])
    password_hash = auth.hash_password(payload.password)
    user, outcome = transact(request.app, lambda state: partners.accept_invite(state, token, password_hash))
    if outcome != "ok":
        auth.LOGIN_IP.add(address)
        raise HTTPException(422, BAD_INVITE)
    partner = request.app.state.store.read()["partners"][user["partnerId"]]
    handover = start_session(request, response, user, "partner")
    return {"role": "partner", "account": partners.account_view(user, partner), **handover}


@router.post("/api/auth/staff")
def staff_login(payload: StaffLogin, request: Request, response: Response):
    if auth.sso_enabled():
        raise HTTPException(403, "The service team signs in with Microsoft. Use Sign in with Microsoft.")
    auth.require_csrf(request)
    address = auth.client_ip(request)
    pair = f"staff|{address}"
    auth.guard(auth.LOGIN_PAIR, pair)
    auth.guard(auth.LOGIN_IP, address)
    admin = os.getenv("DEMO_ADMIN_TOKEN", "")
    if not (admin and hmac.compare_digest(payload.token.strip().encode(), admin.encode())):
        auth.LOGIN_PAIR.add(pair)
        auth.LOGIN_IP.add(address)
        raise HTTPException(401, "That access token is not valid.")
    auth.LOGIN_PAIR.clear(pair)
    handover = auth.grant(request, response, auth.issue_session("admin", "staff", auth.admin_version()))
    return {"role": "admin", **handover}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.require_csrf(request)
    person = auth.staff_person(auth.request_session(request)) if auth.sso_enabled() else None
    if person:
        try:
            record_staff_event(request.app, person, "staff.signed-out", "Signed out")
        except HTTPException:
            log.warning("The sign-out could not be written to the audit trail")
    auth.clear_session_cookie(response, request)
    return {"ok": True}


@router.post("/api/auth/forgot", status_code=202)
def forgot(payload: Forgot, request: Request, background: BackgroundTasks):
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.MAIL_IP, address)
    auth.MAIL_IP.add(address)
    email = accounts.normalise(payload.email)
    code, record = auth.new_code("reset", email)
    customer = transact(request.app, lambda state: accounts.start_reset(state, email, record))
    result = {"message": "If an account exists for that email, we have sent it a 6-digit code."}
    if customer:
        delivered = deliver_code(background, "reset", customer, code)
        if "simulatedCode" in delivered:  # demo sites only; a live reply is identical for known and unknown emails
            result["simulatedCode"] = delivered["simulatedCode"]
    return result


@router.post("/api/auth/reset")
def reset(payload: Reset, request: Request):
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.LOGIN_IP, address)
    email = accounts.normalise(payload.email)
    strong_password(payload.password, email)
    password_hash = auth.hash_password(payload.password)
    outcome = transact(request.app, lambda state: accounts.finish_reset(state, email, payload.code, password_hash))
    if outcome != "ok":
        auth.LOGIN_IP.add(address)
        raise HTTPException(422, BAD_RESET)
    return {"ok": True}


@router.get("/api/me/orders")
def my_orders(request: Request):
    customer, snapshot = signed_in(request)
    if customer["verified"] and accounts.has_claimable(snapshot, customer):
        transact(request.app, lambda state: accounts.claim_orders(state, state["customers"][customer["id"]]))
        customer, snapshot = signed_in(request)
    return {"account": accounts.public_account(customer),
            "orders": [owner_view(order) for order in accounts.owned_orders(snapshot, customer["id"])]}


@router.post("/api/me/orders", status_code=201)
def place_order(payload: PortalOrder, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    data = {**payload.model_dump(), "scenario": "happy"}
    return owner_view(transact(request.app, lambda state: accounts.place_order(
        state, state["customers"][customer["id"]], data)))


@router.post("/api/me/orders/link")
def link_order(payload: LinkOrder, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    return owner_view(transact(request.app, lambda state: accounts.link_order(
        state, customer["id"], payload.orderId, payload.trackingToken.strip())))


@router.get("/api/me/orders/{order_id}")
def my_order(order_id: str, request: Request):
    customer, snapshot = signed_in(request)
    return owner_view(owned(snapshot, customer["id"], order_id))


@router.post("/api/me/orders/{order_id}/site")
def change_site(order_id: str, payload: SiteUpdate, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    return with_order(request, customer, order_id, lambda state, order: update_site(
        state, order["id"], payload.city, payload.siteAddress))


@router.post("/api/me/orders/{order_id}/confirm-site")
def confirm_site(order_id: str, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    return with_order(request, customer, order_id, lambda state, order: correct_order(
        state, order["id"], "customer-portal", SITES[order["city"]]))


@router.put("/api/me/orders/{order_id}/contact")
def change_contact(order_id: str, payload: Contact, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    return with_order(request, customer, order_id, lambda state, order: update_contact(
        state, order["id"], payload.model_dump()))


@router.put("/api/me/profile")
def change_profile(payload: Profile, request: Request):
    customer, _ = signed_in(request)
    limit(request)
    updated = transact(request.app, lambda state: accounts.update_profile(state, customer["id"], payload.model_dump()))
    return {"account": accounts.public_account(updated)}


@router.post("/api/me/password")
def change_password(payload: PasswordChange, request: Request, response: Response):
    customer, _ = signed_in(request)
    pair = f"{customer['email']}|{auth.client_ip(request)}"
    auth.guard(auth.LOGIN_PAIR, pair)
    if not auth.verify_password(payload.current, customer["passwordHash"]):
        auth.LOGIN_PAIR.add(pair)
        raise HTTPException(422, "Your current password isn't right.")
    strong_password(payload.password, customer["email"])
    password_hash = auth.hash_password(payload.password)
    updated = transact(request.app, lambda state: accounts.change_password(state, customer["id"], password_hash))
    return {"ok": True, **start_session(request, response, updated)}
