"""Transactional dispatch and independently validated policy execution."""

import hashlib
import json
from datetime import datetime, timezone

from app.domain import (AWAITING_ENTERPRISE, SITES, TERMINAL, WAITING, audit, follow_up, pause, process_next)
from app.isolated.contracts import AGENTS, VERSION, propose

BLOCKED = TERMINAL | WAITING | {"awaitingCustomer", AWAITING_ENTERPRISE}


class Denied(ValueError):
    pass


def eligible(order):
    return order["state"] not in BLOCKED and order["stage"] in AGENTS


def facts_for(state, order, agent):
    product = next(item for item in state["catalogue"] if item["id"] == order["product"])
    if agent == "validation":
        capacity = next(item for item in state["inventory"]
                        if item["city"] == order["city"] and item["product"] == order["product"])
        return {"siteReference": order["siteReference"], "expectedSite": SITES[order["city"]],
                "availableUnits": capacity["availableUnits"], "bandwidthMbps": product["bandwidthMbps"]}
    if agent == "decomposition":
        return {"services": product["services"], "resources": product["resources"]}
    if agent == "provisioning":
        return {"planId": order["id"], "services": order["serviceOrders"], "resources": order["resourceOrders"]}
    if agent == "fallout":
        return {"failure": "simulated-partner-timeout", "attempts": order.get("recoveryAttempts", 0)}
    return {"orderId": order["id"], "state": "simulated-activated"}


def fingerprint(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def policy_hold(state, order):
    if not eligible(order):
        return
    if datetime.now(timezone.utc) > datetime.fromisoformat(order["deadline"]) and not order.get("slaWaiver"):
        order["slaBreached"] = True
        pause(state, order, "sla-breach", "Seven-day SLA requires explicit manager review")
    elif order["stage"] == "provisioning":
        if order["scenario"] == "human-exception" and not order.get("riskApproved"):
            pause(state, order, "route-conflict", "High-risk simulated change requires explicit manager review")
        elif order["scenario"] == "change-window" and not order.get("windowApproved"):
            pause(state, order, "change-window", "Closed simulated window requires explicit manager review")


def sources(state):
    yield "live", state
    benchmark = state.get("benchmark")
    if benchmark and benchmark["run"]["status"] == "running":
        yield "benchmark", benchmark["sim"]


def source_for(state, job):
    return state if job.get("scope", "live") == "live" else state["benchmark"]["sim"]


class Boundary:
    def __init__(self, store, emit=None):
        self.store, self.emit = store, emit or (lambda record: None)

    def record(self, principal, operation, decision, outcome, order_id=None, correlation=None):
        detail = {"principalId": principal.oid if principal else None,
                  "agent": principal.agent if principal else None, "agentVersion": VERSION,
                  "orderId": order_id, "correlationId": correlation,
                  "operation": operation, "authorization": decision, "outcome": outcome}
        self.store.transact(lambda state: audit(state, order_id, "authenticated-tool-boundary",
                                               "isolated.authorization", json.dumps(detail, sort_keys=True)))
        self.emit(detail)

    def dispatch(self, principal):
        if principal.agent != "supervisor":
            raise Denied("Only the dispatch identity can dispatch")

        def change(state):
            jobs = state.setdefault("isolatedJobs", {})
            created = 0
            for scope, source in sources(state):
                follow_up(source)
                for order in source["orders"].values():
                    policy_hold(source, order)
                for event in source["events"]:
                    order = source["orders"][event["orderId"]]
                    if event["status"] == "queued" and eligible(order) and event["id"] not in jobs:
                        jobs[event["id"]] = {"eventId": event["id"], "orderId": order["id"], "scope": scope,
                                             "agent": order["stage"], "status": "pending"}
                        created += 1
                        audit(state, order["id"], "authenticated-tool-boundary", "isolated.dispatch",
                              json.dumps({"principalId": principal.oid, "agent": "supervisor",
                                          "agentVersion": VERSION, "operation": "dispatch",
                                          "authorization": "allow", "outcome": "queued",
                                          "orderId": order["id"], "correlationId": event["id"],
                                          "scope": scope}, sort_keys=True))
                if scope == "benchmark" and all(order["state"] in TERMINAL for order in source["orders"].values()):
                    state["benchmark"]["run"]["status"] = "complete"
                    state["benchmark"]["run"]["completedAt"] = datetime.now(timezone.utc).isoformat()
            return {"pending": sum(job["status"] == "pending" for job in jobs.values()), "created": created}
        return self.store.transact(change)

    def work(self, principal):
        if principal.agent not in AGENTS:
            raise Denied("Supervisor cannot read worker contracts")
        state = self.store.read()
        for job in state.get("isolatedJobs", {}).values():
            source = source_for(state, job)
            order = source["orders"][job["orderId"]]
            if (job["agent"] == principal.agent and job["status"] == "pending"
                    and eligible(order) and order["stage"] == principal.agent):
                facts = facts_for(source, order, principal.agent)
                return {**job, "facts": facts, "revision": fingerprint(facts),
                        "operation": AGENTS[principal.agent][1], "version": VERSION}
        return None

    def submit(self, principal, event_id, operation, revision, proposal):
        if principal.agent not in AGENTS or operation != AGENTS[principal.agent][1]:
            raise Denied("Tool operation is outside this verified identity's permissions")

        def change(state):
            job = state.get("isolatedJobs", {}).get(event_id)
            if not job or job["agent"] != principal.agent:
                raise Denied("Cross-agent or unknown dispatch")
            digest = fingerprint({"operation": operation, "revision": revision, "proposal": proposal})
            if job["status"] == "completed":
                if job["receipt"]["digest"] != digest:
                    raise Denied("Conflicting replay")
                return job["receipt"]
            source = source_for(state, job)
            order = source["orders"][job["orderId"]]
            if not eligible(order) or order["stage"] != principal.agent:
                raise Denied("Order is not authorized for this operation")
            first = next((e for e in source["events"]
                          if e["orderId"] == order["id"] and e["status"] == "queued"), None)
            if not first or first["id"] != event_id:
                raise Denied("Stale or out-of-sequence dispatch")
            facts = facts_for(source, order, principal.agent)
            if revision != fingerprint(facts) or proposal != propose(principal.agent, facts):
                raise Denied("Worker result failed independent contract validation")
            if principal.agent == "fallout" and (order["scenario"] != "auto-recovery"
                                                or order.get("recoveryAttempts", 0) != 0):
                raise Denied("Recovery allowlist/retry bound failed")
            if principal.agent == "provisioning" and (not order["serviceOrders"] or not order["resourceOrders"]):
                raise Denied("A validated plan is required")
            # Worker fields never mutate state. Recompute the transition at the trusted boundary,
            # which rechecks capacity, deadline, approval and change-window policies atomically.
            process_next(source, order["id"])
            benchmark = state.get("benchmark")
            if job.get("scope") == "benchmark" and all(
                    item["state"] in TERMINAL for item in source["orders"].values()):
                benchmark["run"]["status"] = "complete"
                benchmark["run"]["completedAt"] = datetime.now(timezone.utc).isoformat()
            receipt = {"digest": digest, "eventId": event_id, "orderId": order["id"],
                       "state": order["state"], "stage": order["stage"]}
            job["status"], job["receipt"] = "completed", receipt
            detail = {"principalId": principal.oid, "agent": principal.agent, "agentVersion": VERSION,
                      "orderId": order["id"], "correlationId": event_id, "operation": operation,
                      "authorization": "allow", "outcome": order["state"]}
            audit(state, order["id"], "authenticated-tool-boundary", "isolated.commit",
                  json.dumps(detail, sort_keys=True))
            return receipt
        result = self.store.transact(change)
        self.emit({"principalId": principal.oid, "agent": principal.agent, "agentVersion": VERSION,
                   "orderId": result["orderId"], "correlationId": event_id, "operation": operation,
                   "authorization": "allow", "outcome": result["state"]})
        return result
