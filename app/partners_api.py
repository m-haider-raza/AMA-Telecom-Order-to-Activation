"""The partner portal API and the staff tools that manage partner accounts.

A partner only ever reaches orders of its own organisation; anything else answers 404, exactly as a missing record
would. The partner is always taken from the signed session, never from the request. Staff endpoints use the same
authorisation as the rest of the console, so no partner or customer session can reach them.
"""

import logging
from contextlib import contextmanager
from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import Field, field_validator

from app import auth, identity, partners
from app.accounts_api import EMAIL, PHONE, PasswordChange, Strict, start_session, strong_password
from app.delivery import invite_link, send_concern_email, send_invite_email
from app.domain import create_order, delivery_enabled, public_order
from app.schemas import Contact, OrderInput, check_customer, transact
from app.security import authorize, limit

log = logging.getLogger("ama.partners")
router = APIRouter()

ORDER_ID = r"^AMA-[A-Z0-9]{10}$"


class Loose(Strict):
    """Strict models whose optional text fields may arrive as empty strings from a form."""

    @field_validator("phone", "billingEmail", "poReference", "accessNotes", "preferredDate", "reference",
                     "orderId", mode="before", check_fields=False)
    @classmethod
    def blank_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class NewContact(Loose):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=120, pattern=EMAIL)
    phone: str | None = Field(default=None, pattern=PHONE)
    language: Literal["tr", "en"] = "en"


class Organisation(Loose):
    """Partner organisation names follow the same demo rule as customer company names."""

    @field_validator("name", check_fields=False)
    @classmethod
    def organisation_rule(cls, value):
        return check_customer(value) if value is not None else None


class NewPartner(Organisation):
    name: str = Field(min_length=2, max_length=80)
    paymentTermsDays: int = Field(default=30, ge=0, le=120)
    billingEmail: str | None = Field(default=None, max_length=120, pattern=EMAIL)
    apiClient: bool = False
    contactName: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=120, pattern=EMAIL)
    phone: str | None = Field(default=None, pattern=PHONE)
    language: Literal["tr", "en"] = "en"


class PartnerChanges(Organisation):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    paymentTermsDays: int | None = Field(default=None, ge=0, le=120)
    billingEmail: str | None = Field(default=None, max_length=120, pattern=EMAIL)
    status: Literal["active", "disabled"] | None = None
    apiClient: bool | None = None


class Payment(Loose):
    orderId: str = Field(pattern=ORDER_ID)
    reference: str | None = Field(default=None, max_length=60)


class PartnerProfile(Loose):
    name: str = Field(min_length=2, max_length=80)
    phone: str | None = Field(default=None, pattern=PHONE)
    language: Literal["tr", "en"] = "en"


class DeliveryUpdate(Loose):
    city: Literal["Istanbul", "Ankara", "Izmir"]
    siteAddress: str = Field(min_length=5, max_length=200)
    contact: Contact | None = None
    preferredDate: date | None = None
    accessNotes: str | None = Field(default=None, max_length=300)
    poReference: str | None = Field(default=None, max_length=60)


class NewConcern(Loose):
    category: Literal["delivery", "billing", "technical", "other"]
    orderId: str | None = Field(default=None, pattern=ORDER_ID)
    subject: str = Field(min_length=3, max_length=120)
    text: str = Field(min_length=3, max_length=1000)


class Message(Strict):
    text: str = Field(min_length=1, max_length=1000)


class ConcernStatus(Strict):
    status: Literal["open", "inProgress", "resolved"]


@contextmanager
def not_found():
    try:
        yield
    except partners.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def run(request, action):
    with not_found():
        return transact(request.app, action)


def signed_partner(request):
    """The signed-in partner contact, their organisation and a fresh read of the state."""
    session = auth.request_session(request)
    if session is None:
        raise HTTPException(401, "Please sign in to continue")
    if session["r"] != "partner":
        raise HTTPException(403, "This sign-in belongs to another part of TürkBizTel. Sign out to use a partner account.")
    auth.require_csrf(request)
    snapshot = request.app.state.store.read()
    who = identity.resolve(request, snapshot)
    if who is None or who["role"] != "partner":
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return who["user"], who["partner"], snapshot


def mine(state, user, partner):
    """This contact and organisation as they are inside the current transaction."""
    return state["partnerUsers"][user["id"]], partners.get_partner(state, partner["id"])


def deliver_invite(background, partner, user, token, invite):
    """The one-time link always goes back to the staff member; it is also e-mailed when delivery is live."""
    result = {"token": token, "path": f"/login#invite={token}", "expiresAt": partners.iso(invite["expires"]),
              "delivery": "manual", "contactId": user["id"], "email": user["email"]}
    link = invite_link(token)
    if delivery_enabled() and link:
        background.add_task(send_invite, user["email"], user["name"], partner["name"], link, user["language"])
        result["delivery"] = "email"
    return result


def send_invite(email, name, partner, link, language):
    try:
        send_invite_email(email, name, partner, link, language)
    except Exception as exc:  # the staff member still holds the link and can pass it on
        log.warning("Could not send a partner invitation: %s", type(exc).__name__)


def send_reply(email, name, subject, text, concern_id, language):
    try:
        send_concern_email(email, name, subject, text, concern_id, language)
    except Exception as exc:  # the reply is saved in the portal either way
        log.warning("Could not send a concern reply: %s", type(exc).__name__)


@router.get("/api/partner/overview")
def partner_overview(request: Request):
    user, partner, snapshot = signed_partner(request)
    return partners.overview(snapshot, user, partner)


@router.get("/api/partner/deliveries/{order_id}")
def partner_delivery(order_id: str, request: Request):
    user, partner, snapshot = signed_partner(request)
    with not_found():
        return partners.delivery_detail(snapshot, partner, partners.owned_order(snapshot, partner, order_id))


@router.put("/api/partner/deliveries/{order_id}/delivery")
def update_delivery(order_id: str, payload: DeliveryUpdate, request: Request):
    user, partner, _ = signed_partner(request)
    limit(request)
    data = payload.model_dump()

    def action(state):
        contact, organisation = mine(state, user, partner)
        order = partners.update_delivery(state, organisation, contact, order_id, data)
        return partners.delivery_detail(state, organisation, order)
    return run(request, action)


@router.post("/api/partner/concerns", status_code=201)
def raise_concern(payload: NewConcern, request: Request):
    user, partner, _ = signed_partner(request)
    limit(request)
    data = payload.model_dump()

    def action(state):
        contact, organisation = mine(state, user, partner)
        return partners.concern_view(partners.raise_concern(state, organisation, contact, data))
    return run(request, action)


@router.post("/api/partner/concerns/{concern_id}/messages", status_code=201)
def reply_to_concern(concern_id: str, payload: Message, request: Request):
    user, partner, _ = signed_partner(request)
    limit(request)

    def action(state):
        contact, organisation = mine(state, user, partner)
        return partners.concern_view(partners.add_message(state, concern_id, contact, payload.text, organisation))
    return run(request, action)


@router.put("/api/partner/concerns/{concern_id}")
def change_concern(concern_id: str, payload: ConcernStatus, request: Request):
    user, partner, _ = signed_partner(request)
    limit(request)

    def action(state):
        _, organisation = mine(state, user, partner)
        return partners.concern_view(partners.set_concern_status(state, concern_id, payload.status, organisation))
    return run(request, action)


@router.put("/api/partner/profile")
def change_profile(payload: PartnerProfile, request: Request):
    user, partner, _ = signed_partner(request)
    limit(request)
    data = payload.model_dump()

    def action(state):
        contact, organisation = mine(state, user, partner)
        return partners.account_view(partners.update_profile(state, contact["id"], data), organisation)
    return {"account": run(request, action)}


@router.post("/api/partner/password")
def change_password(payload: PasswordChange, request: Request, response: Response):
    user, _, _ = signed_partner(request)
    pair = f"{user['email']}|{auth.client_ip(request)}"
    auth.guard(auth.LOGIN_PAIR, pair)
    if not auth.verify_password(payload.current, user["passwordHash"]):
        auth.LOGIN_PAIR.add(pair)
        raise HTTPException(422, "Your current password isn't right.")
    strong_password(payload.password, user["email"])
    password_hash = auth.hash_password(payload.password)
    updated = run(request, lambda state: partners.change_password(state, user["id"], password_hash))
    return {"ok": True, **start_session(request, response, updated, "partner")}


@router.get("/api/partners")
def list_partners(request: Request):
    authorize(request)
    return partners.admin_overview(request.app.state.store.read())


@router.post("/api/partners", status_code=201)
def create_partner(payload: NewPartner, request: Request, background: BackgroundTasks):
    authorize(request)
    limit(request)
    token, invite = auth.new_invite()
    data = payload.model_dump()
    partner, user = run(request, lambda state: partners.create_partner(state, data, invite))
    return {"partner": partners.partner_view(partner), "contact": partners.contact_view(user),
            "invite": deliver_invite(background, partner, user, token, invite)}


@router.put("/api/partners/{partner_id}")
def update_partner(partner_id: str, payload: PartnerChanges, request: Request):
    authorize(request)
    limit(request)
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items()
               if value is not None or key == "billingEmail"}
    if not changes:
        raise HTTPException(422, "Nothing to change")
    return partners.partner_view(run(request, lambda state: partners.update_partner(state, partner_id, changes)))


@router.post("/api/partners/{partner_id}/users", status_code=201)
def invite_contact(partner_id: str, payload: NewContact, request: Request, background: BackgroundTasks):
    authorize(request)
    limit(request)
    token, invite = auth.new_invite()
    data = payload.model_dump()

    def action(state):
        organisation = partners.get_partner(state, partner_id)
        if organisation["status"] != "active":
            raise ValueError("Switch the partner back on before inviting new contacts")
        return organisation, partners.add_contact(state, organisation, data, invite)
    partner, user = run(request, action)
    return {"contact": partners.contact_view(user), "invite": deliver_invite(background, partner, user, token, invite)}


@router.post("/api/partners/{partner_id}/users/{user_id}/invite")
def resend_invite(partner_id: str, user_id: str, request: Request, background: BackgroundTasks):
    authorize(request)
    limit(request)
    token, invite = auth.new_invite()

    def action(state):
        return partners.get_partner(state, partner_id), partners.reinvite(state, partner_id, user_id, invite)
    partner, user = run(request, action)
    return {"contact": partners.contact_view(user), "invite": deliver_invite(background, partner, user, token, invite)}


@router.get("/api/partners/{partner_id}/invoices")
def partner_invoices(partner_id: str, request: Request):
    authorize(request)
    snapshot = request.app.state.store.read()
    with not_found():
        partner = partners.get_partner(snapshot, partner_id)
    invoices = partners.invoices_of(snapshot, partner)
    return {"partner": partners.partner_view(partner), "invoices": invoices,
            "payments": partners.payment_summary(invoices)}


@router.post("/api/partners/{partner_id}/payments", status_code=201)
def record_payment(partner_id: str, payload: Payment, request: Request):
    authorize(request)
    limit(request)
    return run(request, lambda state: partners.record_payment(state, partner_id, payload.orderId, payload.reference))


@router.delete("/api/partners/{partner_id}/payments/{order_id}")
def undo_payment(partner_id: str, order_id: str, request: Request):
    authorize(request)
    limit(request)
    return run(request, lambda state: partners.undo_payment(state, partner_id, order_id))


@router.post("/api/partners/{partner_id}/orders", status_code=201)
def place_order(partner_id: str, payload: OrderInput, request: Request):
    authorize(request)
    limit(request)
    data = payload.model_dump()
    return public_order(run(request, lambda state: partners.create_for_partner(
        state, partners.get_partner(state, partner_id), data, create_order)))


@router.post("/api/concerns/{concern_id}/messages", status_code=201)
def staff_reply(concern_id: str, payload: Message, request: Request, background: BackgroundTasks):
    authorize(request)
    limit(request)

    def action(state):
        concern = partners.add_message(state, concern_id, None, payload.text)
        contact = state.get("partnerUsers", {}).get(concern["createdBy"])
        return partners.concern_view(concern), concern["subject"], contact
    view, subject, contact = run(request, action)
    if contact and delivery_enabled():
        background.add_task(send_reply, contact["email"], contact["name"], subject, payload.text.strip(),
                            concern_id, contact.get("language", "en"))
    return view


@router.put("/api/concerns/{concern_id}/status")
def staff_status(concern_id: str, payload: ConcernStatus, request: Request):
    authorize(request)
    limit(request)
    return run(request, lambda state: partners.concern_view(
        partners.set_concern_status(state, concern_id, payload.status)))
