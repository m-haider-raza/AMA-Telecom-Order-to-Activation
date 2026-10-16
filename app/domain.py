"""Guarded, event-driven telecom simulation. No live network changes occur here."""

import hashlib
import json
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


def now():
    return datetime.now(timezone.utc).isoformat()


def audit(state, order_id, actor, action, detail):
    previous = state["audit"][-1]["hash"] if state["audit"] else "0" * 64
    item = {"sequence": len(state["audit"]) + 1, "at": now(), "orderId": order_id,
            "actor": actor, "action": action, "detail": detail, "previousHash": previous}
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
        create_order(state, {
            "customer": ("Demo Marmara Logistics", "Demo Anatolia Retail", "Demo Aegean Manufacturing",
                         "Demo Bosphorus Cloud")[index],
            "city": list(CITIES)[index % 3], "product": CATALOGUE[index % 3]["id"],
            "scenario": scenario, "language": "tr", "idempotencyKey": f"seed-order-{index:04}",
        }, "service-manager")
    return state


def create_order(state, payload, actor):
    required = {"customer", "city", "product", "scenario", "language", "idempotencyKey"}
    if set(payload) - {"siteReference"} != required:
        raise ValueError("Order fields do not match the documented demo contract")
    if actor not in ("service-manager", "wholesale-partner"):
        raise ValueError("Untrusted order actor")
    if payload["city"] not in CITIES or payload["product"] not in {item["id"] for item in CATALOGUE}:
        raise ValueError("Unsupported city or catalogue product")
    if payload["scenario"] not in SCENARIOS or payload["language"] not in ("tr", "en"):
        raise ValueError("Unsupported scenario or language")
    if not isinstance(payload["customer"], str) or not payload["customer"].startswith("Demo "):
        raise ValueError("Only synthetic company names beginning with 'Demo ' are allowed")
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
    if len(state["orders"]) >= 200:
        raise ValueError("Demo is limited to 200 orders; deploy a separate demo environment for more")
    timestamp = now()
    order_id = "AMA-" + uuid4().hex[:10].upper()
    order = {
        "id": order_id, **payload, "actor": actor, "state": "acknowledged", "stage": "validation",
        "createdAt": timestamp, "updatedAt": timestamp,
        "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "timeline": [], "serviceOrders": [], "resourceOrders": [], "notifications": [],
        "approvals": [], "exception": None, "reserved": False, "recovered": False,
        "aiMode": "deterministic-rules-simulator",
        "siteReference": payload.get("siteReference") or (
            "DEMO-UNKNOWN" if payload["scenario"] == "address-correction" else SITES[payload["city"]]),
    }
    state["orders"][order_id] = order
    state["idempotency"][scoped_key] = {"fingerprint": fingerprint, "orderId": order_id}
    audit(state, order_id, actor, "order.accepted", "Synthetic order accepted through guarded API")
    enqueue(state, order, "order.received")
    return order


def notify(state, order, en, tr):
    item = {"id": str(uuid4()), "orderId": order["id"], "at": now(), "en": en, "tr": tr,
            "channel": "Simulated enterprise portal", "deliveredExternally": False}
    order["notifications"].append(item)
    state["notifications"].append(item)
    return item


def pause(state, order, kind, detail):
    order["state"] = "held" if kind == "change-window" else "pendingApproval"
    order["exception"] = {"type": kind, "detail": detail, "createdAt": now(),
                          "resumeStage": order["stage"]}
    step(state, order, "Provisioning Orchestration Agent", "policy.hold", detail, "human-required")
    notify(state, order, "Your demo order needs service-manager review.",
           "Demo siparişiniz hizmet yöneticisinin incelemesini bekliyor.")


def process_next(state, order_id):
    order = state["orders"][order_id]
    if order["state"] in TERMINAL | WAITING | {"awaitingCustomer"}:
        return order
    pending = next((event for event in state["events"]
                    if event["orderId"] == order_id and event["status"] == "queued"), None)
    if pending is None:
        return order
    if datetime.now(timezone.utc) > datetime.fromisoformat(order["deadline"]) and not order.get("slaWaiver"):
        order["slaBreached"] = True
        pause(state, order, "sla-breach", "Seven-day contractual demo SLA has elapsed; explicit review required")
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
            notify(state, order, "Please confirm your synthetic service-site reference.",
                   "Lütfen sentetik hizmet noktası referansınızı doğrulayın.")
            return order
        if capacity["availableUnits"] < 1:
            pause(state, order, "capacity", "No available demo capacity; manager must reject or resolve inventory")
            return order
        step(state, order, "Order Validation Agent", "feasibility.check",
             f"Public city reference, synthetic address zone and {product['bandwidthMbps']} Mbps capacity validated")
        order["stage"] = "decomposition"
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
                  "Outside the simulated change window. Manager can open a labelled demo window; no real override.")
        elif order["scenario"] == "human-exception" and not order.get("riskApproved"):
            pause(state, order, "route-conflict",
                  "Simulated overlapping route / high-risk customer change exceeds autonomous playbook authority")
        elif order["scenario"] == "auto-recovery" and not order["recovered"]:
            step(state, order, "Provisioning Orchestration Agent", "partner.reserve",
                 "Simulated partner timeout; no resources were committed", "timeout")
            order["stage"] = "fallout"
            enqueue(state, order, "fallout.detected")
        elif capacity["availableUnits"] < 1:
            pause(state, order, "capacity", "Capacity was consumed by another order; review required")
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
            enqueue(state, order, "services.activated")
    elif stage == "fallout":
        order["recovered"] = True
        order["recoveryAttempts"] = 1
        step(state, order, "Fallout Resolution Agent", "playbook.partner-timeout.retry-once",
             "Allowlisted simulated retry selected alternate wholesale route; authority did not expand")
        order["stage"] = "provisioning"
        enqueue(state, order, "fallout.recovered")
    elif stage == "customer_update":
        notify(state, order, "Your synthetic service is active. No live telecom service was provisioned.",
               "Sentetik hizmetiniz etkinleştirildi. Gerçek bir telekom hizmeti sağlanmadı.")
        step(state, order, "Customer Update Agent", "customer.notify.tr-en",
             "Turkish and English updates written to simulated customer portal")
        order["state"] = "completed"
        order["stage"] = "done"
        order["completedAt"] = now()
        order["elapsedSeconds"] = (
            datetime.fromisoformat(order["completedAt"]) - datetime.fromisoformat(order["createdAt"])
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
    order["siteReference"] = site_reference
    order["exception"] = None
    order["state"] = "inProgress"
    order["updatedAt"] = now()
    audit(state, order_id, actor, "customer.address-corrected", f"Confirmed synthetic site {site_reference}")
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
    order["approvals"].append({"at": now(), "actor": actor, "decision": decision,
                               "exception": exception_type, "simulated": True})
    audit(state, order_id, actor, f"exception.{decision}", f"Explicit decision for {exception_type}")
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
        notify(state, order, "Your synthetic order was rejected by the service manager.",
               "Sentetik siparişiniz hizmet yöneticisi tarafından reddedildi.")
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
