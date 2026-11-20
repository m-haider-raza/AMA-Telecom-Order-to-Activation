"""Customer accounts held in the order state: registration, e-mail verification, password resets, owned orders.

Every function here runs inside Store.transact. Soft failures such as a wrong code are returned, not raised, so
the attempt counter is saved; raising ValueError rolls the whole transaction back.

Customer and partner contacts share one sign-in form, so e-mail addresses are unique across both kinds of account.
"""

import hmac
import os
import re
from uuid import uuid4

from app import auth
from app.domain import audit, create_order, now, tracking_token

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
ACCOUNT_FIELDS = ("id", "email", "name", "company", "phone", "language", "verified", "createdAt")


def normalise(email):
    return email.strip().lower()


def public_account(customer):
    return {key: customer.get(key) for key in ACCOUNT_FIELDS}


def find_by_email(state, email):
    email = normalise(email)
    return next((item for item in state.get("customers", {}).values() if item["email"] == email), None)


def find_account(state, email):
    """The customer or partner contact that owns this address, as (kind, record); (None, None) when unknown."""
    email = normalise(email)
    for kind, collection in (("customer", "customers"), ("partner", "partnerUsers")):
        found = next((item for item in state.get(collection, {}).values() if item["email"] == email), None)
        if found:
            return kind, found
    return None, None


def register(state, data, password_hash, code):
    customers = state.setdefault("customers", {})
    if find_account(state, data["email"])[1]:
        raise ValueError("An account with this email already exists. Sign in, or use “Forgot password”.")
    if len(customers) >= int(os.getenv("AMA_MAX_CUSTOMERS", "1000")):
        raise ValueError("New registrations are closed in this environment")
    customer = {
        "id": "CUS-" + uuid4().hex[:10].upper(), "email": normalise(data["email"]), "name": data["name"].strip(),
        "company": data["company"].strip(), "phone": data.get("phone"), "language": data.get("language", "en"),
        "passwordHash": password_hash, "verified": False, "verification": code, "reset": None,
        "sessionVersion": 1, "createdAt": now()}
    customers[customer["id"]] = customer
    audit(state, customer["id"], "customer-portal", "account.registered",
          "Customer account created; e-mail address not yet verified")
    return customer


def claimable(order, customer):
    contact = order.get("contact") or {}
    return (not order.get("ownerId") and order.get("actor") == "customer-portal"
            and normalise(contact.get("email") or "") == customer["email"])


def has_claimable(state, customer):
    return any(claimable(order, customer) for order in state["orders"].values())


def claim_orders(state, customer):
    """Attach earlier guest orders to a verified account whose e-mail address matches the order contact."""
    claimed = 0
    for order in state["orders"].values():
        if claimable(order, customer):
            order["ownerId"] = customer["id"]
            claimed += 1
            audit(state, order["id"], "customer-portal", "order.claimed",
                  "Earlier guest order linked to a verified customer account")
    return claimed


def verify(state, customer_id, code):
    customer = state["customers"][customer_id]
    if customer["verified"]:
        return {"ok": True, "claimed": 0}
    outcome = auth.check_code(customer.get("verification"), "verify", customer["email"], code)
    if outcome != "ok":
        return {"ok": False, "reason": outcome}
    customer.update(verified=True, verification=None)
    audit(state, customer_id, "customer-portal", "account.verified", "E-mail address verified with a one-time code")
    return {"ok": True, "claimed": claim_orders(state, customer)}


def refresh_verification(state, customer_id, code):
    customer = state["customers"][customer_id]
    if customer["verified"]:
        return "verified"
    if auth.cooling_down(customer.get("verification")):
        return "cooldown"
    customer["verification"] = code
    return "ok"


def start_reset(state, email, code):
    """Store a reset code for the account, if it exists and is not being flooded. Returns the account or None.

    A partner contact who has not accepted the invitation yet has no password to reset, so none is offered.
    """
    kind, account = find_account(state, email)
    if account is None or auth.cooling_down(account.get("reset")):
        return None
    if kind == "partner" and not account.get("passwordHash"):
        return None
    account["reset"] = code
    return account


def finish_reset(state, email, code, password_hash):
    kind, account = find_account(state, email)
    if account is None or (kind == "partner" and not account.get("passwordHash")):
        return "wrong"
    outcome = auth.check_code(account.get("reset"), "reset", account["email"], code)
    if outcome != "ok":
        return outcome
    account.update(passwordHash=password_hash, reset=None, verified=True, verification=None,
                   sessionVersion=account["sessionVersion"] + 1)
    audit(state, account["id"], "partner-portal" if kind == "partner" else "customer-portal",
          "account.password-reset", "Password reset with an e-mailed one-time code; earlier sessions revoked")
    if kind == "customer":
        claim_orders(state, account)
    return "ok"


def change_password(state, customer_id, password_hash):
    customer = state["customers"][customer_id]
    customer.update(passwordHash=password_hash, sessionVersion=customer["sessionVersion"] + 1)
    audit(state, customer_id, "customer-portal", "account.password-changed",
          "Password changed by the signed-in customer; earlier sessions revoked")
    return customer


def update_profile(state, customer_id, data):
    customer = state["customers"][customer_id]
    customer.update(name=data["name"].strip(), company=data["company"].strip(), phone=data.get("phone"),
                    language=data["language"])
    return customer


def owned_orders(state, customer_id):
    orders = [order for order in state["orders"].values() if order.get("ownerId") == customer_id]
    return sorted(orders, key=lambda order: order["createdAt"], reverse=True)


def place_order(state, customer, data):
    limit = int(os.getenv("AMA_MAX_ORDERS_PER_CUSTOMER", "25"))
    before, owned = set(state["orders"]), len(owned_orders(state, customer["id"]))
    order = create_order(state, data, "customer-portal")
    if order["id"] in before:
        if order.get("ownerId") != customer["id"]:
            raise ValueError("Idempotency key was already used for a different order")
        return order
    if owned >= limit:
        raise ValueError(f"An account can hold {limit} orders; contact us to raise this limit")
    order["ownerId"] = customer["id"]
    return order


def link_order(state, customer_id, order_id, supplied):
    """Attach a guest order to the account when the customer can prove access with its tracking code."""
    order = state["orders"].get(order_id)
    if (order is None or order.get("actor") != "customer-portal"
            or not hmac.compare_digest(supplied.encode(), tracking_token(order_id).encode())
            or order.get("ownerId") not in (None, customer_id)):
        raise ValueError("We could not find an order with that reference and tracking code")
    if not order.get("ownerId"):
        order["ownerId"] = customer_id
        audit(state, order_id, "customer-portal", "order.linked", "Guest order linked using its tracking code")
    return order
