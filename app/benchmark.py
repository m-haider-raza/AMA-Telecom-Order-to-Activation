"""Reproducible benchmark: a fixed mix of synthetic orders run through the real agents, apart from live orders.

The orders live in state["benchmark"]["sim"], a separate order book. They never appear in the live order list, the
TMF endpoints, the audit export or the live capacity counters, and they do not count against AMA_MAX_ORDERS.

The scenario mix and the delay before a person decides are assumptions, recorded on the run so the report can show
them. The mix keeps the assignment's 30% baseline incident rate and assumes the agents' playbooks resolve most of
those incidents on their own; only the incidents no playbook can resolve wait for a person.
"""

import logging
import random
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from app.domain import (CATALOGUE, CITIES, SITES, TERMINAL, WAITING, approve_exception, audit, correct_order,
                        create_order, now, process_next)

log = logging.getLogger(__name__)

SIZES = (50, 100)
MIX_PER_100 = (("happy", 70), ("auto-recovery", 23), ("human-exception", 3), ("change-window", 2),
               ("address-correction", 2))
HUMAN_DELAY_SECONDS = 20
TIMEOUT_SECONDS = 600
CAPACITY_PER_PRODUCT = 1000
ACTOR = "service-manager"


def mix_for(size):
    """Scenario counts for size orders: the per-100 mix scaled by the largest-remainder method (ties go first)."""
    counts = {name: count * size // 100 for name, count in MIX_PER_100}
    remainders = sorted(MIX_PER_100, key=lambda item: item[1] * size % 100, reverse=True)
    for name, _ in remainders[:size - sum(counts.values())]:
        counts[name] += 1
    return counts


def new_sim():
    return {
        "orders": {}, "catalogue": deepcopy(CATALOGUE),
        "inventory": [{"city": city, "product": item["id"], "availableUnits": CAPACITY_PER_PRODUCT,
                       "changeWindow": "simulation"} for city in CITIES for item in CATALOGUE],
        "audit": [], "events": [], "notifications": [], "outbox": [], "idempotency": {},
    }


def scenarios_for(size):
    scenarios = [name for name, count in mix_for(size).items() for _ in range(count)]
    random.Random(size).shuffle(scenarios)
    return scenarios


def start(state, size=100, human_delay=HUMAN_DELAY_SECONDS, actor=ACTOR):
    if size not in SIZES:
        raise ValueError("Benchmark size must be one of: " + ", ".join(map(str, SIZES)))
    current = state.get("benchmark")
    if current and current["run"]["status"] == "running":
        raise ValueError("A benchmark run is already in progress")
    run = {"id": "BM-" + uuid4().hex[:8].upper(), "startedAt": now(), "size": size, "mix": mix_for(size),
           "humanDelaySeconds": human_delay, "status": "running", "completedAt": None, "error": None}
    sim = new_sim()
    cities, products = list(CITIES), [item["id"] for item in CATALOGUE]
    for index, scenario in enumerate(scenarios_for(size)):
        create_order(sim, {
            "customer": f"Demo Benchmark {index + 1:03}", "city": cities[index % 3],
            "product": products[(index // 3) % 3], "scenario": scenario,
            "language": "tr" if index % 2 else "en", "idempotencyKey": f"bench-{run['id']}-{index:04}",
        }, ACTOR, max_orders=size)
    state["benchmark"] = {"run": run, "sim": sim}
    audit(state, run["id"], actor, "benchmark.started",
          f"{size} synthetic orders queued in an isolated simulation; live orders are not affected")
    return run


def decision_due(order, delay_seconds, current):
    """True once an order has waited the assumed human response time for a manager or customer."""
    exception = order.get("exception")
    if not exception or order["state"] not in WAITING | {"awaitingCustomer"}:
        return False
    return (current - datetime.fromisoformat(exception["createdAt"])).total_seconds() >= delay_seconds


def compact(sim):
    """Drop what the report never reads so the document stays small."""
    sim["audit"].clear()
    sim["outbox"].clear()
    sim["notifications"].clear()
    for order in sim["orders"].values():
        order["outbox"], order["notifications"] = [], []
        if order["state"] in TERMINAL:
            order["serviceOrders"], order["resourceOrders"] = [], []


def advance(state, at=None):
    """One supervisor pass over the benchmark orders. Never raises: a broken run is marked failed instead."""
    benchmark = state.get("benchmark")
    if not benchmark or benchmark["run"]["status"] != "running":
        return
    run, sim = benchmark["run"], benchmark["sim"]
    current = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    try:
        for order_id, order in list(sim["orders"].items()):
            if decision_due(order, run["humanDelaySeconds"], current):
                if order["state"] == "awaitingCustomer":
                    correct_order(sim, order_id, ACTOR, SITES[order["city"]])
                else:
                    approve_exception(sim, order_id, ACTOR, "approve")
            process_next(sim, order_id)
        compact(sim)
        if all(order["state"] in TERMINAL for order in sim["orders"].values()):
            run["status"], run["completedAt"] = "complete", now()
            sim["idempotency"] = {}
        elif (current - datetime.fromisoformat(run["startedAt"])).total_seconds() > TIMEOUT_SECONDS:
            run["status"], run["error"] = "failed", f"Not finished within {TIMEOUT_SECONDS} seconds"
    except Exception as error:
        log.exception("Benchmark run %s failed", run["id"])
        run["status"], run["error"] = "failed", f"{type(error).__name__}: {error}"[:300]
