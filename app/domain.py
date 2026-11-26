"""Guarded, event-driven telecom simulation. No live network changes occur here."""

import hashlib
import hmac
import json
import os
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

CITIES = {
    "Istanbul": {"wikidata": "Q406", "latitude": 41.0082, "longitude": 28.9784},
    "Ankara": {"wikidata": "Q3640", "latitude": 39.9334, "longitude": 32.8597},
    "Izmir": {"wikidata": "Q35997", "latitude": 38.4237, "longitude": 27.1428},
}
CATALOGUE = [
    {"id": "connectivity", "name": "Dedicated Internet Access", "bandwidthMbps": 500,
     "monthlyTry": 18500, "services": ["Access circuit", "Internet service"],
     "resources": ["Fibre access port", "IPv4 /29 block"]},
    {"id": "sdwan", "name": "Managed SD-WAN", "bandwidthMbps": 200,
     "monthlyTry": 24500, "services": ["Access circuit", "SD-WAN overlay", "Managed security"],
     "resources": ["Fibre access port", "Virtual edge", "Security policy"]},
    {"id": "cloud", "name": "Enterprise Cloud Connect", "bandwidthMbps": 1000,
     "monthlyTry": 32000, "services": ["Private access", "Cloud interconnect"],
     "resources": ["Fibre access port", "Virtual cross-connect"]},
]
SCENARIOS = {"happy", "auto-recovery", "human-exception", "change-window", "address-correction"}
SITES = {"Istanbul": "IST-001", "Ankara": "ANK-001", "Izmir": "IZM-001"}
TERMINAL = {"completed", "rejected", "failed"}
WAITING = {"held", "pendingApproval"}
# An order the service team placed for an enterprise waits here, untouched by any agent, until the enterprise's
# contact approves it (see app.approvals). It is deliberately not in WAITING: a manager cannot decide it.
AWAITING_ENTERPRISE = "awaitingEnterpriseApproval"
APPROVAL_DAYS = 7


def live_mode():
    return os.getenv("AMA_MODE", "demo") == "live"


def delivery_enabled():
    return os.getenv("AMA_DELIVERY", "simulated") == "live"


def now():
    return datetime.now(timezone.utc).isoformat()


def audit(state, order_id, actor, action, detail, changes=None):
    """Append a hash-chained entry. `actor` stays the plain role string. When a signed-in staff member acted, `by`
    names the person and `changes` lists what the action altered; both are part of the hashed entry, so neither can
    be edited afterwards without breaking the chain."""
    previous = state["audit"][-1]["hash"] if state["audit"] else "0" * 64
    item = {"sequence": len(state["audit"]) + 1, "at": now(), "orderId": order_id,
            "actor": str(actor), "action": action, "detail": detail, "previousHash": previous}
    staff = getattr(actor, "staff", None)
    if staff:
        item["by"] = dict(staff)
        if changes:
            item["changes"] = changes
    item["hash"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    state["audit"].append(item)
    return item


def verify_audit(state):
    previous = "0" * 64
    for index, entry in enumerate(state["audit"], 1):
        body = {key: value for key, value in entry.items() if key != "hash"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if entry["sequence"] != index or entry["previousHash"] != previous or entry["hash"] != digest:
            return False
        previous = digest
    return True


def enqueue(state, order, event_type):
    state["events"].append({"id": str(uuid4()), "orderId": order["id"], "type": event_type,
                            "status": "queued", "createdAt": now()})


def step(state, order, agent, tool, detail, result="success"):
    timestamp = now()
    entry = {"at": timestamp, "agent": agent, "tool": tool, "detail": detail,
             "result": result, "simulated": True}
    order["timeline"].append(entry)
    order["updatedAt"] = timestamp
    audit(state, order["id"], agent, tool, detail)


def partner_capacity(state, city, product=None):
    if city not in CITIES:
        raise ValueError("Unsupported Turkish demo city")
    return {"city": city, "simulated": True, "partner": "Demo Balkan Wholesale",
            "capacity": [dict(item) for item in state["inventory"]
                         if item["city"] == city and (product is None or item["product"] == product)]}


def initial_state():
    state = {
        "schemaVersion": 1, "orders": {}, "catalogue": deepcopy(CATALOGUE),
        "inventory": [{"city": city, "product": item["id"], "availableUnits": 24,
                       "changeWindow": "22:00-06:00 Europe/Istanbul (simulation)"}
                      for city in CITIES for item in CATALOGUE],
        "cityFacts": [{"name": name, **value, "source": "Wikidata CC0"}
                      for name, value in CITIES.items()],
        "audit": [], "events": [], "notifications": [], "idempotency": {},
        "dataProvenance": [
            {"name": "Turkish city reference facts", "license": "CC0",
             "url": "https://www.wikidata.org/wiki/Wikidata:Licensing",
             "note": "Three public city names/coordinates; see cityFacts for entity identifiers."},
            {"name": "Customers, prices, capacity, orders, incidents and messages",
             "license": "Project-authored synthetic fixtures",
             "note": "Invented demo data. Not operator data, market pricing or observed business results."},
        ],
    }
    for index, scenario in enumerate(("happy", "auto-recovery", "human-exception", "change-window")):
        if live_mode():
            break
        create_order(state, {
            "customer": ("Demo Marmara Logistics", "Demo Anatolia Retail", "Demo Aegean Manufacturing",
                         "Demo Bosphorus Cloud")[index],
            "city": list(CITIES)[index % 3], "product": CATALOGUE[index % 3]["id"],
            "scenario": scenario, "language": "tr", "idempotencyKey": f"seed-order-{index:04}",
        }, "service-manager")
    return state


def address_matches_city(address, city):
    """Cheap first-line check that a customer-entered address really is in the selected city."""
    def fold(text):
        text = unicodedata.normalize("NFKD", (text or "").casefold().replace("ı", "i"))
        return "".join(char for char in text if not unicodedata.combining(char))
    return fold(city) in fold(address)


def create_order(state, payload, actor, max_orders=None, start=True):
    """Accept an order. With start=False nothing is queued, so no agent picks it up until the caller starts it."""
    required = {"customer", "city", "product", "scenario", "language", "idempotencyKey"}
    if set(payload) - {"siteReference", "contact", "siteAddress"} != required:
        raise ValueError("Order fields do not match the documented demo contract")
    contact = payload.get("contact")
    if contact is not None:
        if (not isinstance(contact, dict) or contact.get("updateFrequency") not in UPDATE_FREQUENCIES
                or contact.get("preferredChannel") not in ("email", "sms")
                or not contact.get(contact["preferredChannel"] == "email" and "email" or "phone")):
            raise ValueError("Contact details must include the preferred channel's address and a valid update frequency")
    if actor not in ("service-manager", "wholesale-partner", "customer-portal"):
        raise ValueError("Untrusted order actor")
    if payload["city"] not in CITIES or payload["product"] not in {item["id"] for item in CATALOGUE}:
        raise ValueError("Unsupported city or catalogue product")
    if payload["scenario"] not in SCENARIOS or payload["language"] not in ("tr", "en"):
        raise ValueError("Unsupported scenario or language")
    if not isinstance(payload["customer"], str) or not payload["customer"].strip():
        raise ValueError("A customer name is required")
    key = payload["idempotencyKey"]
    if not isinstance(key, str) or not 8 <= len(key) <= 128:
        raise ValueError("Supply an idempotency key of 8-128 characters")
    scoped_key = actor + ":" + key
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if scoped_key in state["idempotency"]:
        previous = state["idempotency"][scoped_key]
        if previous["fingerprint"] != fingerprint:
            raise ValueError("Idempotency key was already used for a different order")
        return state["orders"][previous["orderId"]]
    limit = max_orders if max_orders is not None else int(os.getenv("AMA_MAX_ORDERS", "200"))
    if len(state["orders"]) >= limit:
        raise ValueError(f"This environment is limited to {limit} orders; raise AMA_MAX_ORDERS or archive old orders")
    timestamp = now()
    order_id = "AMA-" + uuid4().hex[:10].upper()
    order = {
        "id": order_id, **payload, "actor": str(actor), "state": "acknowledged", "stage": "validation",
        "createdAt": timestamp, "updatedAt": timestamp,
        "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "timeline": [], "serviceOrders": [], "resourceOrders": [], "notifications": [],
        "approvals": [], "exception": None, "reserved": False, "recovered": False,
        "aiMode": "deterministic-rules-simulator", "contact": contact, "outbox": [],
        "siteReference": payload.get("siteReference") or (
            "DEMO-UNKNOWN" if payload["scenario"] == "address-correction"
            or (actor == "customer-portal" and not address_matches_city(payload.get("siteAddress"), payload["city"]))
            else SITES[payload["city"]]),
    }
    state["orders"][order_id] = order
    state["idempotency"][scoped_key] = {"fingerprint": fingerprint, "orderId": order_id}
    audit(state, order_id, actor, "order.accepted", "Synthetic order accepted through guarded API")
    if start:
        enqueue(state, order, "order.received")
    return order


UPDATE_FREQUENCIES = {"every-step", "daily", "weekly", "critical-only"}
DIGEST_SECONDS = {"daily": 86400, "weekly": 604800}
REMINDER_LIMIT = 3


def mask_email(email):
    return email and email[0] + "***@" + email.split("@", 1)[1]


def mask_contact(contact):
    if not contact:
        return None
    email, phone = contact.get("email"), contact.get("phone")
    return {**contact,
            "email": mask_email(email),
            "phone": phone and "*" * (len(phone) - 4) + phone[-4:]}


def public_order(order):
    return {**{key: value for key, value in order.items() if key != "ownerId"},
            "contact": mask_contact(order.get("contact"))}


def tracking_token(order_id):
    """Proof for linking an earlier guest order to a signed-in account; never authorizes status access."""
    secret = os.getenv("AMA_TRACKING_SECRET") or os.getenv("DEMO_ADMIN_TOKEN", "")
    return hmac.new(secret.encode(), order_id.encode(), hashlib.sha256).hexdigest()[:40]


def order_status_path(order):
    """Order updates lead to the appropriate signed-in portal, without a bearer credential."""
    if order.get("partnerId") or order.get("actor") == "wholesale-partner":
        return f"/partner#deliveries/{order['id']}"
    return f"/account#order/{order['id']}"


def approval_expired(record):
    """A request nobody answered in time stays pending, so the service team can renew it; it just cannot be decided."""
    return record["status"] == "pending" and datetime.fromisoformat(record["expiresAt"]) <= datetime.now(timezone.utc)


def approval_view(order):
    """What the enterprise may see of the approval it is asked for: who asked, when, and until when. Never a link."""
    record = order.get("enterpriseApproval")
    if not record:
        return None
    return {"status": record["status"], "requestedAt": record["requestedAt"], "expiresAt": record["expiresAt"],
            "requestedBy": (record.get("requestedBy") or {}).get("name"), "decidedAt": record.get("decidedAt"),
            "expired": approval_expired(record)}


def customer_view(order):
    """Customer-safe projection: no audit trail, agent internals, partner or other customers' data."""
    exception = order.get("exception") or {}
    return {
        "id": order["id"], "customer": order["customer"], "city": order["city"], "product": order["product"],
        "state": order["state"], "stage": order["stage"], "createdAt": order["createdAt"],
        "updatedAt": order["updatedAt"], "deadline": order["deadline"], "language": order["language"],
        "siteAddress": order.get("siteAddress"), "siteReference": order["siteReference"],
        "contact": mask_contact(order.get("contact")), "approval": approval_view(order),
        "action": ({"type": exception.get("type"), "expectedSiteReference": exception.get("expectedSiteReference"),
                    "remindersSent": exception.get("reminders", 0)} if exception else None),
        "updates": [{"at": item["at"], "en": item["en"], "tr": item["tr"]} for item in order["notifications"]],
    }


def owner_view(order):
    """The same safe projection, with the contact in full: the signed-in owner is entitled to see it."""
    return {**customer_view(order), "contact": order.get("contact")}


def update_site(state, order_id, city, site_address):
    """Let the customer correct the city and installation address of an order that is waiting on them."""
    order = state["orders"][order_id]
    if order["state"] != "awaitingCustomer" or order["stage"] != "validation":
        raise ValueError("This order is not waiting for an address correction")
    site_address = (site_address or "").strip()
    if city not in CITIES or not 5 <= len(site_address) <= 200:
        raise ValueError("Enter the installation address and choose one of our service cities")
    if not address_matches_city(site_address, city):
        raise ValueError(f"That address does not mention {city}. Add the district and city, or choose the right city.")
    order.update(city=city, siteAddress=site_address, siteReference=SITES[city], exception=None,
                 state="inProgress", updatedAt=now())
    progress(state, order, "Thank you, your address was updated and your order is moving again.",
             "Teşekkürler, adresiniz güncellendi ve siparişiniz devam ediyor.")
    audit(state, order_id, "customer-portal", "customer.address-updated",
          f"Customer corrected the installation address (service city {city})")
    enqueue(state, order, "customer.address-confirmed")
    return order


def update_contact(state, order_id, contact):
    order = state["orders"][order_id]
    if order["state"] in TERMINAL:
        raise ValueError("This order is closed; contact preferences can no longer be changed")
    if (contact.get("updateFrequency") not in UPDATE_FREQUENCIES
            or contact.get("preferredChannel") not in ("email", "sms")
            or not contact.get("email" if contact["preferredChannel"] == "email" else "phone")):
        raise ValueError("Contact details must include the preferred channel's address and a valid update frequency")
    order["contact"] = contact
    order["updatedAt"] = now()
    audit(state, order_id, "customer-portal", "customer.contact-updated",
          f"Customer set channel={contact['preferredChannel']} frequency={contact['updateFrequency']}")
    return order


def _recipient(contact):
    if not contact:
        return None
    channel = contact["preferredChannel"]
    return mask_contact(contact)["email" if channel == "email" else "phone"]


def dispatch(state, order, en, tr, kind):
    """Queue a customer message honouring contact details and update frequency.

    kind "critical" (action needed, delays, outcomes) always goes out immediately;
    kind "progress" follows the customer's chosen cadence. External delivery is separately configured.
    """
    contact = order.get("contact")
    timestamp = now()
    item = {"id": str(uuid4()), "orderId": order["id"], "at": timestamp, "en": en, "tr": tr, "kind": kind,
            "channel": f"Simulated {contact['preferredChannel']}" if contact else "None",
            "to": _recipient(contact), "deliveredExternally": False}
    if contact is None:
        item["status"] = "skipped-no-contact"
    elif kind == "progress" and contact["updateFrequency"] == "critical-only":
        item["status"] = "skipped-by-preference"
    elif kind == "progress" and contact["updateFrequency"] in DIGEST_SECONDS:
        item["status"] = "scheduled-digest"
        item["sendAfter"] = (datetime.fromisoformat(timestamp)
                             + timedelta(seconds=DIGEST_SECONDS[contact["updateFrequency"]])).isoformat()
    else:
        item["status"] = sent_status()
        item["sentAt"] = timestamp
        if contact and kind == "critical":
            release_digest(order, timestamp)
    if contact and delivery_enabled():
        item["channel"] = contact["preferredChannel"].upper()
        item["attempts"] = 0
        if item["status"] == "pending-delivery":
            item.pop("sentAt", None)
    order.setdefault("outbox", []).append(item)
    state.setdefault("outbox", []).append(item)
    return item


def sent_status():
    return "pending-delivery" if delivery_enabled() else "simulated-sent"


def release_digest(order, timestamp):
    for queued in order.get("outbox", []):
        if queued["status"] == "scheduled-digest":
            queued["status"] = "simulated-sent" if queued["channel"].startswith("Simulated ") else sent_status()
            if queued["status"] == "simulated-sent":
                queued["sentAt"] = timestamp


def flush_digests(state, at=None):
    at = at or now()
    for order in state["orders"].values():
        due = [item for item in order.get("outbox", [])
               if item["status"] == "scheduled-digest" and item["sendAfter"] <= at]
        if due:
            release_digest(order, at)


def follow_up(state, interval_seconds=None, at=None, actor="Customer Update Agent"):
    """Automatically chase customers whose order is stuck (incorrect details) or under review."""
    if interval_seconds is None:
        interval_seconds = float(os.getenv("DEMO_REMINDER_SECONDS", "300"))
    current = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    flush_digests(state, current.isoformat())
    for order in state["orders"].values():
        if order["state"] not in WAITING | {"awaitingCustomer"} or not order.get("exception"):
            continue
        exception = order["exception"]
        since = datetime.fromisoformat(exception.get("lastContactAt") or exception.get("createdAt")
                                       or order["updatedAt"])
        if (current - since).total_seconds() < interval_seconds:
            continue
        sent = exception.get("reminders", 0)
        if sent >= REMINDER_LIMIT:
            if not exception.get("escalated"):
                exception["escalated"] = True
                step(state, order, actor, "customer.reminders-exhausted",
                     f"{REMINDER_LIMIT} automatic reminders sent without resolution; manager follow-up needed",
                     "human-required")
            continue
        if order["state"] == "awaitingCustomer" and order.get("siteAddress"):
            en = (f"Reminder {sent + 1}/{REMINDER_LIMIT}: your order is on hold because the installation "
                  f"address does not appear to be in {order['city']}. Please confirm the address and city.")
            tr = (f"Hatırlatma {sent + 1}/{REMINDER_LIMIT}: Kurulum adresi {order['city']} şehrinde "
                  "görünmediği için siparişiniz bekliyor. Lütfen adresi ve şehri doğrulayın.")
        elif order["state"] == "awaitingCustomer":
            expected = exception["expectedSiteReference"]
            en = (f"Reminder {sent + 1}/{REMINDER_LIMIT}: your order is on hold because the site reference "
                  f"does not match the selected city. Please confirm {expected}.")
            tr = (f"Hatırlatma {sent + 1}/{REMINDER_LIMIT}: Hizmet noktası referansı seçilen şehirle "
                  f"eşleşmediği için siparişiniz bekliyor. Lütfen {expected} değerini doğrulayın.")
        else:
            en = (f"Reminder {sent + 1}/{REMINDER_LIMIT}: your order is still waiting for service-manager "
                  "review. We will update you as soon as it is decided.")
            tr = (f"Hatırlatma {sent + 1}/{REMINDER_LIMIT}: Siparişiniz hâlâ hizmet yöneticisinin "
                  "incelemesini bekliyor. Karar verildiğinde sizi bilgilendireceğiz.")
        dispatch(state, order, en, tr, "critical")
        exception["reminders"] = sent + 1
        exception["lastContactAt"] = current.isoformat()
        audit(state, order["id"], actor, "customer.reminder",
              f"Automatic stuck-order reminder {sent + 1}/{REMINDER_LIMIT} queued")


def note(state, order, en, tr):
    """A message in the enterprise portal only: nothing is queued for e-mail or SMS."""
    item = {"id": str(uuid4()), "orderId": order["id"], "at": now(), "en": en, "tr": tr,
            "channel": "Simulated enterprise portal", "deliveredExternally": False}
    order["notifications"].append(item)
    state["notifications"].append(item)
    return item


def notify(state, order, en, tr, kind="critical"):
    item = note(state, order, en, tr)
    dispatch(state, order, en, tr, kind)
    return item


def progress(state, order, en, tr):
    dispatch(state, order, en, tr, "progress")


def pause(state, order, kind, detail, actor="Provisioning Orchestration Agent"):
    order["state"] = "held" if kind == "change-window" else "pendingApproval"
    order["exception"] = {"type": kind, "detail": detail, "createdAt": now(),
                          "resumeStage": order["stage"]}
    step(state, order, actor, "policy.hold", detail, "human-required")
    notify(state, order, "Your order needs service-manager review." if live_mode()
           else "Your demo order needs service-manager review.",
           "Siparişiniz hizmet yöneticisinin incelemesini bekliyor." if live_mode()
           else "Demo siparişiniz hizmet yöneticisinin incelemesini bekliyor.")


def process_next(state, order_id, policy_actor="Provisioning Orchestration Agent"):
    order = state["orders"][order_id]
    if order["state"] in TERMINAL | WAITING | {"awaitingCustomer", AWAITING_ENTERPRISE}:
        return order
    pending = next((event for event in state["events"]
                    if event["orderId"] == order_id and event["status"] == "queued"), None)
    if pending is None:
        return order
    if datetime.now(timezone.utc) > datetime.fromisoformat(order["deadline"]) and not order.get("slaWaiver"):
        order["slaBreached"] = True
        pause(state, order, "sla-breach", "Seven-day contractual demo SLA has elapsed; explicit review required",
              actor=policy_actor)
        return order
    pending["status"] = "processed"
    pending["processedAt"] = now()
    order["state"] = "inProgress"
    product = next(item for item in state["catalogue"] if item["id"] == order["product"])
    capacity = next(item for item in state["inventory"]
                    if item["city"] == order["city"] and item["product"] == order["product"])
    stage = order["stage"]
    if stage == "validation":
        if order["siteReference"] != SITES[order["city"]]:
            order["state"] = "awaitingCustomer"
            order["exception"] = {"type": "address-correction",
                                  "detail": "Synthetic site reference does not match the selected service city.",
                                  "expectedSiteReference": SITES[order["city"]]}
            step(state, order, "Order Validation Agent", "address.customer-correction",
                 "Invalid synthetic site reference; customer confirmation required", "customer-required")
            if order.get("siteAddress"):
                notify(state, order, "Your order is on hold: the installation address does not appear to be in "
                       f"{order['city']}. Please confirm the address and city.",
                       "Siparişiniz bekliyor: kurulum adresi "
                       f"{order['city']} şehrinde görünmüyor. Lütfen adresi ve şehri doğrulayın.")
            else:
                notify(state, order, "Your order is on hold: the site reference does not match the selected city. "
                       f"Please confirm {SITES[order['city']]}.",
                       "Siparişiniz bekliyor: hizmet noktası referansı seçilen şehirle eşleşmiyor. "
                       f"Lütfen {SITES[order['city']]} değerini doğrulayın.")
            order["exception"]["createdAt"] = now()
            return order
        if capacity["availableUnits"] < 1:
            pause(state, order, "capacity", "No available demo capacity; manager must reject or resolve inventory",
                  actor=policy_actor)
            return order
        step(state, order, "Order Validation Agent", "feasibility.check",
             f"Public city reference, synthetic address zone and {product['bandwidthMbps']} Mbps capacity validated")
        order["stage"] = "decomposition"
        progress(state, order, "Your order details were validated.", "Sipariş bilgileriniz doğrulandı.")
        enqueue(state, order, "order.validated")
    elif stage == "decomposition":
        order["serviceOrders"] = [
            {"id": f"{order_id}-S{index}", "name": name, "state": "acknowledged",
             "productOrderId": order_id, "specification": "TMF641-inspired subset"}
            for index, name in enumerate(product["services"], 1)]
        order["resourceOrders"] = [
            {"id": f"{order_id}-R{index}", "name": name, "state": "planned", "productOrderId": order_id}
            for index, name in enumerate(product["resources"], 1)]
        step(state, order, "Order Decomposition Agent", "TMF622.to.TMF641",
             f"Decomposed into {len(order['serviceOrders'])} service and {len(order['resourceOrders'])} resource orders")
        order["stage"] = "provisioning"
        enqueue(state, order, "services.planned")
    elif stage == "provisioning":
        if order["scenario"] == "change-window" and not order.get("windowApproved"):
            pause(state, order, "change-window",
                  "Outside the simulated change window. Manager can open a labelled demo window; no real override.",
                  actor=policy_actor)
        elif order["scenario"] == "human-exception" and not order.get("riskApproved"):
            pause(state, order, "route-conflict",
                  "Simulated overlapping route / high-risk customer change exceeds autonomous playbook authority",
                  actor=policy_actor)
        elif order["scenario"] == "auto-recovery" and not order["recovered"]:
            step(state, order, "Provisioning Orchestration Agent", "partner.reserve",
                 "Simulated partner timeout; no resources were committed", "timeout")
            order["stage"] = "fallout"
            enqueue(state, order, "fallout.detected")
        elif capacity["availableUnits"] < 1:
            pause(state, order, "capacity", "Capacity was consumed by another order; review required",
                  actor=policy_actor)
        else:
            availability = partner_capacity(state, order["city"], order["product"])
            if not availability["capacity"]:
                raise ValueError("Partner capacity tool returned no inventory")
            if not order["reserved"]:
                capacity["availableUnits"] -= 1
                order["reserved"] = True
            for item in order["serviceOrders"] + order["resourceOrders"]:
                item["state"] = "completed"
            step(state, order, "Provisioning Orchestration Agent", "partner.capacity+network.activate",
                 "Guarded in-process simulator allocated one capacity unit; service/resource orders activated")
            order["stage"] = "customer_update"
            progress(state, order, "Your service has been provisioned and is being activated.",
                     "Hizmetiniz hazırlandı ve etkinleştiriliyor.")
            enqueue(state, order, "services.activated")
    elif stage == "fallout":
        order["recovered"] = True
        order["recoveryAttempts"] = 1
        step(state, order, "Fallout Resolution Agent", "playbook.partner-timeout.retry-once",
             "Allowlisted simulated retry selected alternate wholesale route; authority did not expand")
        order["stage"] = "provisioning"
        enqueue(state, order, "fallout.recovered")
    elif stage == "customer_update":
        if live_mode():
            notify(state, order, "Your order has been processed. We will confirm your service activation date.",
                   "Siparişiniz işlendi. Hizmet etkinleştirme tarihini ayrıca bildireceğiz.")
        else:
            notify(state, order, "Your synthetic service is active. No live telecom service was provisioned.",
                   "Sentetik hizmetiniz etkinleştirildi. Gerçek bir telekom hizmeti sağlanmadı.")
        step(state, order, "Customer Update Agent", "customer.notify.tr-en",
             "Turkish and English updates written to simulated customer portal")
        order["state"] = "completed"
        order["stage"] = "done"
        order["completedAt"] = now()
        # The clock runs from the enterprise's approval when there was one: its wait is not the platform's time.
        order["elapsedSeconds"] = (
            datetime.fromisoformat(order["completedAt"])
            - datetime.fromisoformat(order.get("approvedAt") or order["createdAt"])
        ).total_seconds()
    else:
        raise ValueError(f"Unknown supervisor stage: {stage}")
    return order


def correct_order(state, order_id, actor, site_reference):
    order = state["orders"][order_id]
    if actor != "service-manager" and actor != order["actor"]:
        raise ValueError("Only the submitting partner or service manager can correct this order")
    if order["state"] != "awaitingCustomer" or order["stage"] != "validation":
        raise ValueError("Order is not awaiting customer site confirmation")
    if site_reference != SITES[order["city"]]:
        raise ValueError("The synthetic site reference does not match the service city")
    previous_site, previous_state = order["siteReference"], order["state"]
    order["siteReference"] = site_reference
    order["exception"] = None
    progress(state, order, "Thank you, the site reference was confirmed and your order is moving again.",
             "Teşekkürler, hizmet noktası doğrulandı ve siparişiniz devam ediyor.")
    order["state"] = "inProgress"
    order["updatedAt"] = now()
    audit(state, order_id, actor, "customer.address-corrected", f"Confirmed synthetic site {site_reference}",
          changes=[{"field": "siteReference", "from": previous_site, "to": site_reference},
                   {"field": "state", "from": previous_state, "to": "inProgress"}])
    enqueue(state, order, "customer.address-confirmed")
    return order


def approve_exception(state, order_id, actor, decision):
    if actor != "service-manager":
        raise ValueError("Only the service manager can decide exceptions")
    order = state["orders"][order_id]
    if order["state"] not in WAITING or not order["exception"]:
        raise ValueError("Order is not awaiting a manager decision")
    if decision not in ("approve", "reject"):
        raise ValueError("Decision must be approve or reject")
    exception_type = order["exception"]["type"]
    if decision == "approve" and exception_type == "capacity":
        raise ValueError("Capacity cannot be invented by approval. Reject this order or resolve inventory first.")
    order["approvals"].append({"at": now(), "actor": str(actor), "decision": decision,
                               "exception": exception_type, "simulated": True})
    audit(state, order_id, actor, f"exception.{decision}", f"Explicit decision for {exception_type}",
          changes=[{"field": f"exception ({exception_type})", "from": "awaiting decision",
                    "to": "approved" if decision == "approve" else "rejected"},
                   {"field": "state", "from": order["state"],
                    "to": "rejected" if decision == "reject" else "inProgress"}])
    if decision == "reject":
        if order["reserved"]:
            capacity = next(item for item in state["inventory"]
                            if item["city"] == order["city"] and item["product"] == order["product"])
            capacity["availableUnits"] += 1
            order["reserved"] = False
            for item in order["serviceOrders"] + order["resourceOrders"]:
                item["state"] = "cancelled"
            step(state, order, "Fallout Resolution Agent", "playbook.compensate-simulated-allocation",
                 "Manager rejection released the synthetic allocation; no live network rollback occurred")
        order["state"] = "rejected"
        order["stage"] = "done"
        notify(state, order, "Your order was rejected by the service manager." if live_mode()
               else "Your synthetic order was rejected by the service manager.",
               "Siparişiniz hizmet yöneticisi tarafından reddedildi." if live_mode()
               else "Sentetik siparişiniz hizmet yöneticisi tarafından reddedildi.")
    else:
        if exception_type == "change-window":
            order["windowApproved"] = True
        elif exception_type == "route-conflict":
            order["riskApproved"] = True
        elif exception_type == "sla-breach":
            order["slaWaiver"] = True
        order["state"] = "inProgress"
        if not any(item["orderId"] == order_id and item["status"] == "queued" for item in state["events"]):
            enqueue(state, order, "exception.approved")
    order["exception"] = None
    order["updatedAt"] = now()
    return order


def metrics(state):
    orders = list(state["orders"].values())
    completed = [item for item in orders if item["state"] == "completed"]
    fallout = [item for item in orders if item["scenario"] != "happy"]
    return {
        "orders": len(orders), "completed": len(completed),
        "exceptions": sum(item["state"] in WAITING for item in orders),
        "recovered": sum(item["recovered"] for item in orders),
        "partnerOrders": sum(item["actor"] == "wholesale-partner" for item in orders),
        "customerCorrections": sum(item["state"] == "awaitingCustomer" for item in orders),
        "simulationSecondsAverage": round(sum(item["elapsedSeconds"] for item in completed) / len(completed), 1)
        if completed else None,
        "scenarioFalloutPercent": round(100 * len(fallout) / len(orders), 1) if orders else 0,
        "baselineDays": 35, "baselineFalloutPercent": 30, "targetDays": 7, "targetFalloutPercent": 8,
        "businessCase": {"annualOrders": 12000, "revenueForwardTry": 900000000, "reworkSavingsTry": 150000000},
        "measurementNote": "Scenario mix and elapsed demo seconds are not measured production KPIs. "
                           "Baseline, targets and business-case amounts are assignment assumptions.",
        "auditChainValid": verify_audit(state), "productionKpiEvidence": False,
    }
