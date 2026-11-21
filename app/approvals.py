"""Orders a service engineer places for an enterprise, and the approval that enterprise gives before any work starts.

An enterprise is a customer account: its company, name and verified e-mail address are the point of contact. Staff
pick one from the directory and place the order; the order then waits in awaitingEnterpriseApproval, untouched by
any agent, until that contact answers. The contact can answer from a one-time link in the e-mail, which needs no
sign-in, or from the order in their own account. A signed-in staff member can never answer for them.

Nothing a staff member can read (the order, the outbox, the notifications) ever holds a link or a token: the link is
rebuilt from the saved record at the moment the e-mail is sent. Sending again bumps the record's version, which
retires every earlier link.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app import auth
from app.delivery import approval_link, send_approval_email
from app.domain import (APPROVAL_DAYS, AWAITING_ENTERPRISE, CATALOGUE, approval_expired, audit, create_order,
                        delivery_enabled, enqueue, live_mode, mask_email, note, now)

log = logging.getLogger("ama.approvals")

PURPOSE = "enterprise-approval"
MAX_PENDING = 5  # open requests one enterprise may have at a time, so staff cannot flood a contact's inbox
MAX_SENDS = 5  # e-mails per order, the first one included
LINK_GRACE = 30 * 24 * 3600  # a link stays readable this long past its expiry, so it can say that it expired
BAD_LINK = "That approval link isn't valid or has expired. Ask the TürkBizTel service team to send a new one."
REPLACED = ("A newer approval e-mail replaced this link. Open the latest e-mail from TürkBizTel, "
            "or sign in to your account.")
LINK_PROBLEMS = {"invalid": BAD_LINK, "replaced": REPLACED}


def signing_ready():
    """Links are signed with the session secret. Without one none is issued and none is accepted; the enterprise can
    still answer from its account."""
    return bool(os.getenv("AMA_SESSION_SECRET") or os.getenv("AMA_TRACKING_SECRET")
                or (not auth.sso_enabled() and os.getenv("DEMO_ADMIN_TOKEN")))


def first_delivery():
    """Where the e-mail stands when a request is made: due, impossible here, or simulated in a demo."""
    if delivery_enabled():
        return "pending"
    return "unavailable" if live_mode() else "simulated"


def in_days(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def link_token(order_id, record):
    expires = datetime.fromisoformat(record["expiresAt"]).timestamp()
    return auth.sign_blob(PURPOSE, {"o": order_id, "v": record["version"], "e": int(expires) + LINK_GRACE})


def inspect_link(state, token):
    """The order a link was signed for and what is wrong with the link: None, "invalid" or "replaced"."""
    body = auth.read_blob(PURPOSE, token) if signing_ready() else None
    order = state["orders"].get(body.get("o")) if body else None
    record = order and order.get("enterpriseApproval")
    if not record:
        return None, "invalid"
    if body.get("v") != record["version"]:
        return order, "replaced"
    return order, None


def link_status(record):
    return "expired" if approval_expired(record) else record["status"]


def product_name(order):
    item = next((entry for entry in CATALOGUE if entry["id"] == order["product"]), None)
    return item and f"{item['name']} ({item['bandwidthMbps']} Mbps)"


def summary(order):
    """What the holder of a valid link may see: enough to decide, and nothing about anyone else."""
    record = order["enterpriseApproval"]
    return {"orderId": order["id"], "company": order["customer"], "contactName": order["contact"]["contactName"],
            "product": product_name(order), "city": order["city"], "language": order["language"],
            "requestedBy": record["requestedBy"]["name"], "requestedAt": record["requestedAt"],
            "expiresAt": record["expiresAt"]}


def waiting_counts(state):
    counts = {}
    for order in state["orders"].values():
        if order["state"] == AWAITING_ENTERPRISE and order.get("ownerId"):
            counts[order["ownerId"]] = counts.get(order["ownerId"], 0) + 1
    return counts


def directory(state):
    """Every enterprise the service team can place an order for. The address is masked: staff need to recognise the
    contact, not collect the addresses. An enterprise whose e-mail is not verified is listed but cannot be chosen."""
    counts = waiting_counts(state)
    rows = [{"id": customer["id"], "company": customer["company"], "contactName": customer["name"],
             "email": mask_email(customer["email"]), "language": customer["language"],
             "verified": bool(customer.get("verified")), "waiting": counts.get(customer["id"], 0)}
            for customer in state.get("customers", {}).values()]
    return sorted(rows, key=lambda row: (row["company"].casefold(), row["id"]))


def open_request(state, order_id):
    order = state["orders"][order_id]
    record = order.get("enterpriseApproval")
    if not record or record["status"] != "pending" or order["state"] != AWAITING_ENTERPRISE:
        raise ValueError("This order is no longer waiting for approval")
    return order, record


def place(state, customer, data, actor):
    """Create an order for an enterprise and hold it until its contact approves. Returns (order, created); a replay of
    the same idempotency key returns the order already placed with created=False, so nothing is sent twice."""
    if not customer.get("verified"):
        raise ValueError(f"{customer['company']} has not verified its e-mail address yet, so it cannot be asked to "
                         "approve an order")
    waiting = waiting_counts(state).get(customer["id"], 0)
    contact = {"contactName": customer["name"], "email": customer["email"], "preferredChannel": "email",
               "updateFrequency": "every-step"}
    if customer.get("phone"):
        contact["phone"] = customer["phone"]
    payload = {"customer": customer["company"], "city": data["city"], "product": data["product"],
               "scenario": data["scenario"], "language": customer["language"],
               "idempotencyKey": data["idempotencyKey"], "contact": contact}
    known = set(state["orders"])
    order = create_order(state, payload, actor, start=False)
    if order["id"] in known:
        return order, False
    if waiting >= MAX_PENDING:  # raising here discards the order just created
        raise ValueError(f"{customer['company']} already has {MAX_PENDING} orders waiting for its approval. Ask its "
                         "contact to answer them, or withdraw one, before placing another.")
    timestamp = now()
    staff = getattr(actor, "staff", None) or {}
    order.update(ownerId=customer["id"], state=AWAITING_ENTERPRISE, updatedAt=timestamp, enterpriseApproval={
        "status": "pending", "version": 1, "requestedAt": timestamp, "lastRequestedAt": timestamp,
        "expiresAt": in_days(APPROVAL_DAYS), "sends": 1, "delivery": {"status": first_delivery()},
        "requestedBy": {"name": staff.get("name"), "upn": staff.get("upn"), "role": staff.get("role")}})
    note(state, order,
         "Our service team placed this order for your company. Approve it to start; nothing is provisioned until "
         "you do.",
         "Hizmet ekibimiz bu siparişi şirketiniz adına oluşturdu. Başlaması için onaylayın; siz onaylamadan hiçbir "
         "hizmet devreye alınmaz.")
    audit(state, order["id"], actor, "order.approval-requested",
          f"Order placed for {customer['company']}; approval requested from {customer['name']}")
    return order, True


def decide(state, order_id, decision, via):
    """Record the enterprise's answer. `via` is "link" or "account"."""
    order, record = open_request(state, order_id)
    if approval_expired(record):
        raise ValueError("This approval request has expired. Ask the service team to send a new one.")
    timestamp = now()
    contact = order["contact"]["contactName"]
    where = "from the link in the e-mail" if via == "link" else "from their account"
    if decision == "approve":
        record.update(status="approved", decidedAt=timestamp, via=via)
        order.update(state="acknowledged", stage="validation", approvedAt=timestamp, updatedAt=timestamp,
                     deadline=in_days(7))
        enqueue(state, order, "order.received")
        note(state, order, "Thank you. Your order is approved and our agents are starting on it now.",
             "Teşekkürler. Siparişiniz onaylandı ve ajanlarımız üzerinde çalışmaya başlıyor.")
        audit(state, order_id, "enterprise-contact", "order.enterprise-approved", f"{contact} approved the order {where}")
    else:
        record.update(status="declined", decidedAt=timestamp, via=via)
        order.update(state="rejected", stage="done", exception=None, updatedAt=timestamp)
        note(state, order, "You declined this order. Nothing was provisioned.",
             "Bu siparişi reddettiniz. Hiçbir hizmet devreye alınmadı.")
        audit(state, order_id, "enterprise-contact", "order.enterprise-declined", f"{contact} declined the order {where}")
    return order


def decide_by_link(state, token, decision):
    order, problem = inspect_link(state, token)
    if problem:
        raise ValueError(LINK_PROBLEMS[problem])
    return decide(state, order["id"], decision, "link")


def resend(state, order_id, actor):
    """Ask again. The new e-mail carries a new link and a fresh deadline; every earlier link stops working."""
    order, record = open_request(state, order_id)
    last = datetime.fromisoformat(record["lastRequestedAt"]).timestamp()
    if time.time() - last < auth.RESEND_SECONDS:
        raise ValueError("The request was sent moments ago. Wait a minute before sending it again.")
    if record["sends"] >= MAX_SENDS:
        raise ValueError(f"The request has already been sent {MAX_SENDS} times. Withdraw the order and place it again "
                         "once the contact is ready.")
    timestamp = now()
    record.update(version=record["version"] + 1, lastRequestedAt=timestamp, expiresAt=in_days(APPROVAL_DAYS),
                  sends=record["sends"] + 1, delivery={"status": first_delivery()})
    order["updatedAt"] = timestamp
    audit(state, order_id, actor, "order.approval-resent", f"Approval request sent again (e-mail {record['sends']})")
    return order


def withdraw(state, order_id, actor):
    """The service team takes the order back before the enterprise has answered."""
    order, record = open_request(state, order_id)
    timestamp = now()
    record.update(status="withdrawn", decidedAt=timestamp, via="staff")
    order.update(state="rejected", stage="done", exception=None, updatedAt=timestamp)
    note(state, order, "Our service team withdrew this order request. Nothing was provisioned.",
         "Hizmet ekibimiz bu sipariş talebini geri çekti. Hiçbir hizmet devreye alınmadı.")
    audit(state, order_id, actor, "order.approval-withdrawn", "Order withdrawn before the enterprise answered")
    return order


def record_delivery(state, order_id, version, status):
    order = state["orders"].get(order_id)
    record = order and order.get("enterpriseApproval")
    if record and record["version"] == version and record["status"] == "pending":
        record["delivery"] = {"status": status, "at": now()}


def send_approval(store, order_id, version):
    """E-mail the request to the enterprise's contact, outside any lock. The recipient is the account's own verified
    address, never the order's editable contact, and the link is built here, so it lives nowhere staff can read."""
    snapshot = store.read()
    order = snapshot["orders"].get(order_id)
    record = order and order.get("enterpriseApproval")
    if not record or record["version"] != version or record["status"] != "pending":
        return
    status = "failed"
    try:
        customer = snapshot.get("customers", {}).get(order.get("ownerId"))
        if customer is None or not customer.get("verified"):
            raise LookupError("no verified recipient")
        link = approval_link(link_token(order_id, record)) if signing_ready() else ""
        expires = datetime.fromisoformat(record["expiresAt"]).astimezone(timezone.utc)
        send_approval_email(customer["email"], customer["name"], customer["company"], order_id,
                            record["requestedBy"]["name"], product_name(order), order["city"],
                            expires.strftime("%Y-%m-%d %H:%M UTC"), link, customer["language"])
        status = "sent"
    except Exception as exc:  # a mail outage must not break the request; staff can send it again
        log.warning("Could not send the approval request for %s: %s", order_id, type(exc).__name__)
    store.transact(lambda state: record_delivery(state, order_id, version, status))
