"""Wholesale partner organisations, held in the order state.

Partners are invite-only: staff create the organisation and its first contact, and that contact sets a password
from a single-use invitation link. A partner only ever reaches orders that belong to its own organisation. Expected
delivery dates and invoices are derived from those orders; invoices are pro-forma and nothing is billed.

Every function here runs inside Store.transact. Soft failures are returned; raising ValueError rolls the whole
transaction back, and NotFound lets the API answer 404 exactly as it would for a missing record.
"""

import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import accounts, auth
from app.domain import CATALOGUE, CITIES, SITES, TERMINAL, WAITING, address_matches_city, audit, enqueue, now, progress

PRICES = {item["id"]: item for item in CATALOGUE}
VAT_PERCENT = 20
CATEGORIES = ("delivery", "billing", "technical", "other")
STATUSES = ("open", "inProgress", "resolved")
MAX_CONTACTS = 10
MAX_CONCERNS = 100
MAX_ACTIVE_CONCERNS = 30
MAX_MESSAGES = 20
MAX_LISTED = 300
VISIBLE_ACTIONS = ("partner.delivery-updated", "partner.concern-raised", "partner.concern-reply",
                   "partner.concern-status", "partner.payment-recorded", "partner.order-placed")
PARTNER_FIELDS = ("id", "name", "paymentTermsDays", "currency", "billingEmail", "status", "apiClient", "createdAt")
HOLD_REASONS = {
    "change-window": "Waiting for the next network change window. Our service manager is scheduling it.",
    "route-conflict": "A possible route conflict is being checked by our service manager.",
    "capacity": "Waiting for network capacity in this city to be confirmed.",
}
TIMELINE = {
    "feasibility.check": "Order checked: city, address and capacity look good.",
    "address.customer-correction": "On hold: the installation address needs confirming.",
    "TMF622.to.TMF641": "Order planned into service and resource tasks.",
    "partner.reserve": "The wholesale route timed out, so we are retrying.",
    "playbook.partner-timeout.retry-once": "An alternate wholesale route was selected.",
    "partner.capacity+network.activate": "Capacity reserved and the service activated.",
    "customer.notify.tr-en": "Final confirmation sent to the customer.",
    "policy.hold": "Held for service-manager review.",
    "playbook.compensate-simulated-allocation": "Reserved capacity was released.",
    "customer.reminders-exhausted": "Reminders went unanswered, so a manager will follow up.",
}


class NotFound(LookupError):
    pass


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def partner_view(partner):
    return {key: partner.get(key) for key in PARTNER_FIELDS}


def account_view(user, partner):
    """Shaped like a customer account so the pages can treat both alike; company is the partner organisation."""
    return {"id": user["id"], "email": user["email"], "name": user["name"], "company": partner["name"],
            "phone": user.get("phone"), "language": user.get("language", "en"), "verified": True,
            "createdAt": user["createdAt"], "partnerId": partner["id"]}


def contact_view(user):
    """What staff see about a contact: never a password or invitation hash."""
    invite = user.get("invite")
    return {"id": user["id"], "name": user["name"], "email": user["email"], "phone": user.get("phone"),
            "language": user.get("language", "en"), "pending": not user.get("passwordHash"),
            "inviteExpiresAt": iso(invite["expires"]) if invite else None, "createdAt": user["createdAt"]}


def contacts_of(state, partner_id):
    users = [user for user in state.get("partnerUsers", {}).values() if user["partnerId"] == partner_id]
    return sorted(users, key=lambda user: user["createdAt"])


def get_partner(state, partner_id):
    partner = state.get("partners", {}).get(partner_id)
    if partner is None:
        raise NotFound("We could not find that partner")
    return partner


def add_contact(state, partner, data, invite, actor="service-manager"):
    users = state.setdefault("partnerUsers", {})
    if accounts.find_account(state, data["email"])[1]:
        raise ValueError("That email address already has a TürkBizTel account")
    if len(contacts_of(state, partner["id"])) >= MAX_CONTACTS:
        raise ValueError(f"A partner can have up to {MAX_CONTACTS} contacts")
    user = {"id": "PUS-" + uuid4().hex[:10].upper(), "partnerId": partner["id"],
            "email": accounts.normalise(data["email"]), "name": data["name"].strip(), "phone": data.get("phone"),
            "language": data.get("language", "en"), "passwordHash": None, "invite": invite, "reset": None,
            "verified": False, "sessionVersion": 1, "createdAt": now()}
    users[user["id"]] = user
    audit(state, user["id"], actor, "partner.contact-invited",
          f"{user['name']} was invited to the {partner['name']} partner portal")
    return user


def create_partner(state, data, invite, actor="service-manager"):
    partners = state.setdefault("partners", {})
    if len(partners) >= int(os.getenv("AMA_MAX_PARTNERS", "50")):
        raise ValueError("New partners are closed in this environment")
    name = data["name"].strip()
    if any(item["name"].casefold() == name.casefold() for item in partners.values()):
        raise ValueError("A partner with this name already exists")
    partner = {"id": "PAR-" + uuid4().hex[:10].upper(), "name": name, "paymentTermsDays": data["paymentTermsDays"],
               "currency": "TRY", "billingEmail": data.get("billingEmail"), "status": "active", "apiClient": False,
               "payments": {}, "createdAt": now()}
    partners[partner["id"]] = partner
    audit(state, partner["id"], actor, "partner.created",
          f"Partner organisation {name} created with {data['paymentTermsDays']}-day payment terms")
    if data.get("apiClient"):
        set_api_client(state, partner, True)
    user = add_contact(state, partner, {"name": data["contactName"], "email": data["email"],
                                        "phone": data.get("phone"), "language": data.get("language", "en")},
                       invite, actor)
    return partner, user


def set_api_client(state, partner, enabled):
    """The shared partner API token represents exactly one organisation, so enabling it elsewhere clears it."""
    if enabled:
        for other in state["partners"].values():
            other["apiClient"] = False
    partner["apiClient"] = bool(enabled)


def update_partner(state, partner_id, changes, actor="service-manager"):
    partner = get_partner(state, partner_id)
    before = {key: partner.get(key) for key in changes}
    if "name" in changes:
        name = changes["name"].strip()
        if any(item["id"] != partner_id and item["name"].casefold() == name.casefold()
               for item in state["partners"].values()):
            raise ValueError("A partner with this name already exists")
        partner["name"] = name
    for key in ("paymentTermsDays", "billingEmail"):
        if key in changes:
            partner[key] = changes[key]
    if "apiClient" in changes:
        set_api_client(state, partner, changes["apiClient"])
    if "status" in changes and changes["status"] != partner["status"]:
        partner["status"] = changes["status"]
        if changes["status"] == "disabled":
            for user in contacts_of(state, partner_id):
                user["sessionVersion"] += 1
    audit(state, partner_id, actor, "partner.updated",
          "Partner settings changed: " + ", ".join(sorted(changes)),
          changes=[{"field": key, "from": before[key], "to": partner.get(key)}
                   for key in sorted(changes) if before[key] != partner.get(key)])
    return partner


def reinvite(state, partner_id, user_id, invite, actor="service-manager"):
    partner = get_partner(state, partner_id)
    user = state.get("partnerUsers", {}).get(user_id)
    if user is None or user["partnerId"] != partner_id:
        raise NotFound("We could not find that contact")
    if user.get("passwordHash"):
        raise ValueError("This contact already has a password. They can use “Forgot password” on the sign-in page.")
    user["invite"] = invite
    audit(state, user_id, actor, "partner.invite-resent",
          f"A new invitation was issued to {user['name']} at {partner['name']}")
    return user


def invite_owner(state, token):
    """The contact an invitation token was issued to, or None. Only keyed hashes are stored, never the token."""
    digest = auth.invite_hash(token)
    return next((item for item in state.get("partnerUsers", {}).values()
                 if item.get("invite") and hmac.compare_digest(item["invite"]["hash"], digest)), None)


def accept_invite(state, token, password_hash):
    """Turn an invitation into a password. Returns (user, "ok") or (None, reason)."""
    user = invite_owner(state, token)
    if user is None:
        return None, "invalid"
    if time.time() > user["invite"]["expires"]:
        return None, "expired"
    if state["partners"][user["partnerId"]]["status"] != "active":
        return None, "disabled"
    user.update(passwordHash=password_hash, invite=None, reset=None, verified=True,
                sessionVersion=user["sessionVersion"] + 1)
    audit(state, user["id"], "partner-portal", "partner.invite-accepted",
          f"{user['name']} accepted the invitation and set a password")
    return user, "ok"


def update_profile(state, user_id, data):
    user = state["partnerUsers"][user_id]
    user.update(name=data["name"].strip(), phone=data.get("phone"), language=data["language"])
    return user


def change_password(state, user_id, password_hash):
    user = state["partnerUsers"][user_id]
    user.update(passwordHash=password_hash, sessionVersion=user["sessionVersion"] + 1)
    audit(state, user_id, "partner-portal", "account.password-changed",
          "Password changed by the signed-in partner contact; earlier sessions revoked")
    return user


def owns(partner, order):
    """Orders carry the partner that placed them; token and MCP orders pre-date partners and belong to the API client."""
    if order.get("partnerId"):
        return order["partnerId"] == partner["id"]
    return bool(partner.get("apiClient")) and order.get("actor") == "wholesale-partner"


def channel_owns(state, order):
    """The shared partner token reaches what it submitted plus the orders of its API-client organisation."""
    if order.get("actor") != "wholesale-partner":
        return False
    owner = order.get("partnerId")
    return not owner or bool(state.get("partners", {}).get(owner, {}).get("apiClient"))


def orders_of(state, partner):
    found = [order for order in state["orders"].values() if owns(partner, order)]
    return sorted(found, key=lambda order: order["createdAt"], reverse=True)


def owned_order(state, partner, order_id):
    order = state["orders"].get(order_id)
    if order is None or not owns(partner, order):
        raise NotFound("We could not find that delivery")
    return order


def outlook(order, at=None):
    """The expected delivery date with a plain-language status, derived from the order itself."""
    current = at or datetime.now(timezone.utc)
    if order["state"] == "completed":
        return {"status": "delivered", "date": order.get("completedAt") or order["updatedAt"],
                "reason": "Delivered and activated."}
    if order["state"] in TERMINAL:
        return {"status": "closed", "date": None, "reason": "This order was closed without delivery."}
    deadline = order["deadline"]
    if order.get("slaBreached") or current > datetime.fromisoformat(deadline):
        return {"status": "delayed", "date": deadline,
                "reason": "The planned date has passed and our service manager is reviewing this delivery. "
                          "Raise a concern if you need an update."}
    if order["state"] == "awaitingCustomer":
        return {"status": "atRisk", "date": deadline,
                "reason": "We need the installation address confirmed before we can continue. "
                          "Update the delivery details to get this moving."}
    if order["state"] in WAITING:
        kind = (order.get("exception") or {}).get("type")
        return {"status": "atRisk", "date": deadline,
                "reason": HOLD_REASONS.get(kind, "Our service manager is reviewing this delivery.")}
    return {"status": "onTrack", "date": deadline, "reason": "On track for the expected date."}


def delivery_view(order):
    return {
        "id": order["id"], "customer": order["customer"], "city": order["city"], "product": order["product"],
        "state": order["state"], "stage": order["stage"], "createdAt": order["createdAt"],
        "updatedAt": order["updatedAt"], "language": order["language"], "siteAddress": order.get("siteAddress"),
        "siteReference": order["siteReference"], "contact": order.get("contact"),
        "delivery": order.get("delivery") or {}, "outlook": outlook(order),
        "needsInput": order["state"] == "awaitingCustomer",
    }


def timeline(state, order):
    """A partner-safe history: friendly milestones and the partner's own changes, never agent internals."""
    items = [{"at": order["createdAt"], "text": "Order received."}]
    items += [{"at": entry["at"], "text": TIMELINE.get(entry["tool"], "Progress update recorded.")}
              for entry in order["timeline"]]
    items += [{"at": entry["at"], "text": entry["detail"]} for entry in state["audit"]
              if entry["orderId"] == order["id"] and entry["action"] in VISIBLE_ACTIONS]
    return sorted(items, key=lambda item: item["at"])


def delivery_detail(state, partner, order):
    """One delivery with its partner-safe history, invoice and the concerns raised about it."""
    return {**delivery_view(order), "timeline": timeline(state, order), "invoice": invoice_for(partner, order),
            "concerns": [concern_view(item) for item in concerns_of(state, partner["id"])
                         if item["orderId"] == order["id"]]}


def invoice_for(partner, order, today=None):
    """The pro-forma invoice for one order: scheduled while it is in flight, issued on delivery, then paid or overdue."""
    if order["state"] in ("rejected", "failed"):
        return None
    today = today or datetime.now(timezone.utc).date()
    product = PRICES[order["product"]]
    net = product["monthlyTry"]
    vat = net * VAT_PERCENT // 100
    invoice = {"number": "INV-" + order["id"].split("-", 1)[1], "orderId": order["id"],
               "customer": order["customer"], "product": order["product"], "city": order["city"],
               "description": f"{product['name']}, first month in advance", "currency": "TRY", "net": net,
               "vat": vat, "vatPercent": VAT_PERCENT, "total": net + vat, "status": "scheduled",
               "issuedAt": None, "dueDate": None, "paidAt": None, "reference": None, "simulated": True}
    issued = order.get("completedAt")
    if not issued:
        return invoice
    due = datetime.fromisoformat(issued).date() + timedelta(days=int(partner.get("paymentTermsDays", 30)))
    payment = (partner.get("payments") or {}).get(order["id"])
    invoice.update(status="paid" if payment else ("overdue" if today > due else "issued"),
                   issuedAt=issued, dueDate=due.isoformat())
    if payment:
        invoice.update(paidAt=payment["paidAt"], reference=payment.get("reference"))
    return invoice


def invoices_of(state, partner):
    found = (invoice_for(partner, order) for order in orders_of(state, partner))
    return [invoice for invoice in found if invoice]


def payment_summary(invoices):
    def total(*statuses):
        return sum(item["total"] for item in invoices if item["status"] in statuses)
    unpaid = sorted((item for item in invoices if item["status"] in ("issued", "overdue")),
                    key=lambda item: item["dueDate"])
    first = unpaid[0] if unpaid else None
    return {"currency": "TRY", "outstanding": total("issued", "overdue"), "overdue": total("overdue"),
            "overdueCount": sum(item["status"] == "overdue" for item in invoices), "paid": total("paid"),
            "scheduled": total("scheduled"),
            "nextDue": {"number": first["number"], "dueDate": first["dueDate"], "total": first["total"]}
            if first else None}


def record_payment(state, partner_id, order_id, reference, actor="service-manager"):
    partner = get_partner(state, partner_id)
    order = state["orders"].get(order_id)
    invoice = invoice_for(partner, order) if order and owns(partner, order) else None
    if invoice is None:
        raise NotFound("We could not find that invoice")
    if invoice["status"] not in ("issued", "overdue"):
        raise ValueError("Only an issued, unpaid invoice can be marked as paid")
    partner.setdefault("payments", {})[order_id] = {"paidAt": now(), "reference": reference or None,
                                                    "recordedBy": str(actor)}
    audit(state, order_id, actor, "partner.payment-recorded",
          f"Payment recorded for {invoice['number']} ({partner['name']})",
          changes=[{"field": f"invoice {invoice['number']}", "from": invoice["status"], "to": "paid"}])
    return invoice_for(partner, order)


def undo_payment(state, partner_id, order_id, actor="service-manager"):
    partner = get_partner(state, partner_id)
    if order_id not in partner.get("payments", {}):
        raise NotFound("No payment is recorded for that invoice")
    del partner["payments"][order_id]
    restored = invoice_for(partner, state["orders"][order_id])
    audit(state, order_id, actor, "partner.payment-reversed",
          f"A recorded payment for INV-{order_id.split('-', 1)[1]} was reversed ({partner['name']})",
          changes=[{"field": f"invoice INV-{order_id.split('-', 1)[1]}", "from": "paid",
                    "to": restored["status"] if restored else "unpaid"}])
    return restored


def update_delivery(state, partner, user, order_id, data):
    """Let a partner correct where and how an open delivery is made; every change is audited."""
    order = owned_order(state, partner, order_id)
    if order["state"] in TERMINAL:
        raise ValueError("This delivery is closed, so its details can no longer be changed. "
                         "Raise a concern if something is wrong.")
    city, address = data["city"], data["siteAddress"].strip()
    if city not in CITIES or not address_matches_city(address, city):
        raise ValueError(f"That address does not mention {city}. Add the district and city, or choose the right city.")
    preferred = data.get("preferredDate")
    today = datetime.now(timezone.utc).date()
    if preferred and not today <= preferred <= today + timedelta(days=365):
        raise ValueError("Choose a preferred delivery date from today up to one year ahead")
    changed = []
    site_changed = (city, address) != (order["city"], order.get("siteAddress"))
    unblocks = order["state"] == "awaitingCustomer" and order["stage"] == "validation"
    if site_changed or unblocks:
        if order["state"] in WAITING:
            raise ValueError("A service manager is reviewing this delivery, so the address cannot change right now. "
                             "Raise a concern and we will help.")
        if city != order["city"] and order.get("reserved"):
            raise ValueError(f"Capacity is already reserved in {order['city']}. "
                             "Raise a concern to move this delivery to another city.")
        order.update(city=city, siteAddress=address, siteReference=SITES[city])
        if site_changed:
            changed.append("installation address")
        if unblocks:
            order.update(exception=None, state="inProgress")
            progress(state, order, "Thank you, the delivery address was confirmed and your order is moving again.",
                     "Teşekkürler, teslimat adresi doğrulandı ve siparişiniz devam ediyor.")
            enqueue(state, order, "customer.address-confirmed")
            changed.append("address confirmed")
    if data.get("contact") and data["contact"] != order.get("contact"):
        order["contact"] = data["contact"]
        changed.append("contact")
    details = {"preferredDate": preferred.isoformat() if preferred else None,
               "accessNotes": (data.get("accessNotes") or "").strip() or None,
               "poReference": (data.get("poReference") or "").strip() or None}
    if details != {key: (order.get("delivery") or {}).get(key) for key in details}:
        order["delivery"] = {**details, "updatedAt": now(), "updatedBy": user["name"]}
        changed.append("delivery preferences")
    if changed:
        order["updatedAt"] = now()
        audit(state, order_id, "wholesale-partner", "partner.delivery-updated",
              f"Delivery details updated by {user['name']} ({partner['name']}): {', '.join(changed)}")
    return order


def create_for_partner(state, partner, payload, create, actor="service-manager"):
    """Staff place an order on a partner's behalf; the partner then sees it like any other of its deliveries."""
    if partner["status"] != "active":
        raise ValueError("This partner account is switched off, so it cannot receive new orders")
    before = set(state["orders"])
    order = create(state, payload, "wholesale-partner")
    if order["id"] in before:
        if order.get("partnerId") != partner["id"]:
            raise ValueError("Idempotency key was already used for a different order")
        return order
    order["partnerId"] = partner["id"]
    audit(state, order["id"], actor, "partner.order-placed",
          f"Order placed by TürkBizTel staff on behalf of {partner['name']}")
    return order


def concerns_of(state, partner_id):
    found = [item for item in state.get("concerns", {}).values() if item["partnerId"] == partner_id]
    return sorted(found, key=lambda item: item["updatedAt"], reverse=True)


def concern_view(concern):
    view = {key: concern[key] for key in ("id", "partnerId", "orderId", "category", "subject", "status",
                                         "createdAt", "updatedAt", "createdByName")}
    # Adapt the old system label for display without rewriting the conversation or personal names.
    view["messages"] = [
        {**message, "name": "TürkBizTel service team"}
        if message["from"] == "staff" and message["name"] in ("MyTurkei service team", "MyTurkey service team")
        else dict(message) for message in concern["messages"]
    ]
    return view


def raise_concern(state, partner, user, data):
    mine = concerns_of(state, partner["id"])
    if len(mine) >= MAX_CONCERNS:
        raise ValueError(f"You have reached the limit of {MAX_CONCERNS} concerns; ask us to archive resolved ones")
    if sum(item["status"] != "resolved" for item in mine) >= MAX_ACTIVE_CONCERNS:
        raise ValueError(f"You already have {MAX_ACTIVE_CONCERNS} open concerns. Add to one of them instead.")
    order_id = data.get("orderId")
    if order_id:
        owned_order(state, partner, order_id)
    timestamp = now()
    concern = {"id": "CON-" + uuid4().hex[:10].upper(), "partnerId": partner["id"], "orderId": order_id or None,
               "category": data["category"], "subject": data["subject"].strip(), "status": "open",
               "createdAt": timestamp, "updatedAt": timestamp, "createdBy": user["id"],
               "createdByName": user["name"],
               "messages": [{"at": timestamp, "from": "partner", "name": user["name"], "text": data["text"].strip()}]}
    state.setdefault("concerns", {})[concern["id"]] = concern
    audit(state, order_id or concern["id"], "wholesale-partner", "partner.concern-raised",
          f"{user['name']} ({partner['name']}) raised a {data['category']} concern: {concern['subject']}")
    return concern


def get_concern(state, concern_id, partner=None):
    concern = state.get("concerns", {}).get(concern_id)
    if concern is None or (partner is not None and concern["partnerId"] != partner["id"]):
        raise NotFound("We could not find that concern")
    return concern


def add_message(state, concern_id, author, text, partner=None, actor="service-manager"):
    """Append a reply. author is the signed-in partner contact, or None for the TürkBizTel service team."""
    concern = get_concern(state, concern_id, partner)
    if len(concern["messages"]) >= MAX_MESSAGES:
        raise ValueError("This conversation is full. Please raise a new concern to continue.")
    timestamp = now()
    concern["messages"].append({"at": timestamp, "from": "partner" if author else "staff",
                                "name": author["name"] if author else "TürkBizTel service team",
                                "text": text.strip()})
    concern["updatedAt"] = timestamp
    previous = concern["status"]
    if author and concern["status"] == "resolved":
        concern["status"] = "open"
    elif not author and concern["status"] == "open":
        concern["status"] = "inProgress"
    audit(state, concern["orderId"] or concern_id, "wholesale-partner" if author else actor,
          "partner.concern-reply", f"Reply added to concern {concern_id}",
          changes=[{"field": f"concern {concern_id} status", "from": previous, "to": concern["status"]}]
          if concern["status"] != previous else None)
    return concern


def set_concern_status(state, concern_id, status, partner=None, actor="service-manager"):
    concern = get_concern(state, concern_id, partner)
    if partner is not None and status not in ("open", "resolved"):
        raise ValueError("Partners can close a concern or reopen it")
    if status == concern["status"]:
        return concern
    previous = concern["status"]
    concern.update(status=status, updatedAt=now())
    audit(state, concern["orderId"] or concern_id, "wholesale-partner" if partner else actor,
          "partner.concern-status", f"Concern {concern_id} marked {status}",
          changes=[{"field": f"concern {concern_id} status", "from": previous, "to": status}])
    return concern


def overview(state, user, partner):
    """Everything the partner portal shows, read in one consistent snapshot."""
    orders = orders_of(state, partner)
    invoices = [invoice for invoice in (invoice_for(partner, order) for order in orders) if invoice]
    by_order = {invoice["orderId"]: invoice for invoice in invoices}
    deliveries = []
    for order in orders[:MAX_LISTED]:
        view = delivery_view(order)
        invoice = by_order.get(order["id"])
        view["invoice"] = {key: invoice[key] for key in ("number", "status", "total", "dueDate")} if invoice else None
        deliveries.append(view)
    states = [order["state"] for order in orders]
    return {
        "account": account_view(user, partner), "partner": partner_view(partner), "deliveries": deliveries,
        "invoices": invoices[:MAX_LISTED], "payments": payment_summary(invoices),
        "counts": {"all": len(orders), "needsInput": states.count("awaitingCustomer"),
                   "delivered": states.count("completed"),
                   "inProgress": sum(item not in TERMINAL for item in states)},
        "concerns": [concern_view(item) for item in concerns_of(state, partner["id"])],
    }


def admin_overview(state):
    """Staff's view of every partner and the open concerns inbox."""
    listed, names = [], {}
    for partner in state.get("partners", {}).values():
        orders = orders_of(state, partner)
        invoices = [invoice for invoice in (invoice_for(partner, order) for order in orders) if invoice]
        concerns = concerns_of(state, partner["id"])
        names[partner["id"]] = partner["name"]
        listed.append({**partner_view(partner), "contacts": [contact_view(user) for user in
                                                              contacts_of(state, partner["id"])],
                       "orders": len(orders), "openOrders": sum(order["state"] not in TERMINAL for order in orders),
                       "needsInput": sum(order["state"] == "awaitingCustomer" for order in orders),
                       "payments": payment_summary(invoices),
                       "openConcerns": sum(item["status"] != "resolved" for item in concerns)})
    inbox = sorted(state.get("concerns", {}).values(), key=lambda item: item["updatedAt"], reverse=True)
    return {"partners": sorted(listed, key=lambda item: item["name"].casefold()),
            "concerns": [{**concern_view(item), "partnerName": names.get(item["partnerId"], "Unknown partner")}
                         for item in inbox],
            "openConcerns": sum(item["status"] != "resolved" for item in inbox)}
