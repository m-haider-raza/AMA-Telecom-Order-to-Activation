"""Order-to-delivery analytics derived from order timelines and the event queue.

Per-agent time runs from "work available" (the event was queued) to "the agent finished its step": queue wait
(created -> picked up by the supervisor) plus processing (picked up -> step recorded). Time spent waiting for a
manager decision or a customer correction is reported separately and is never attributed to an agent.

Every figure here is measured on the simulator. It shows the orchestration layer, not partner or field work, and it is
not production KPI evidence.
"""

from collections import defaultdict
from datetime import datetime
from statistics import mean, median

BASELINE_DAYS, TARGET_DAYS = 35, 7
BASELINE_FALLOUT, TARGET_FALLOUT = 30.0, 8.0
SLA_SECONDS = TARGET_DAYS * 86400
MIN_SAMPLE = 30
PAIR_WINDOW_SECONDS = 10.0
PEOPLE = {"human-required", "customer-required"}
NOT_A_HOLD = {"customer.reminders-exhausted"}
SCOPES = ("auto", "all", "live", "benchmark")
AGENTS = (
    ("validation", "Order Validation Agent"), ("decomposition", "Order Decomposition Agent"),
    ("provisioning", "Provisioning Orchestration Agent"), ("fallout", "Fallout Resolution Agent"),
    ("customer_update", "Customer Update Agent"),
)
OUTCOMES = ("straight-through", "auto-recovered", "needs-people", "failed", "in-flight")


def parse(value):
    return datetime.fromisoformat(value)


def seconds(start, end):
    return (parse(end) - parse(start)).total_seconds()


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def step_timings(order, events):
    """One entry per timeline step: queue/process/total seconds, or None for steps no queued event triggered.

    Each processed event is paired with the first step recorded right after it was picked up. Steps taken outside
    event processing (a manager rejection, reminder escalation) stay unpaired, so they never distort agent time.
    """
    steps = order["timeline"]
    timings = [None] * len(steps)
    claimed = set()
    processed = sorted((event for event in events if event.get("processedAt")), key=lambda event: event["processedAt"])
    for event in processed:
        picked_up = parse(event["processedAt"])
        index = next((position for position, entry in enumerate(steps)
                      if position not in claimed and parse(entry["at"]) >= picked_up), None)
        if index is None:
            continue
        process = (parse(steps[index]["at"]) - picked_up).total_seconds()
        if process > PAIR_WINDOW_SECONDS:
            continue
        queue = max((picked_up - parse(event["createdAt"])).total_seconds(), 0.0)
        timings[index] = {"queue": queue, "process": process, "total": queue + process}
        claimed.add(index)
    return timings


def people_wait(order, events):
    """Seconds the order spent held for a manager decision or customer correction (resolved holds only)."""
    resumptions = sorted([parse(item["at"]) for item in order.get("approvals", [])]
                         + [parse(event["createdAt"]) for event in events])
    waited = 0.0
    for entry in order["timeline"]:
        if entry["result"] not in PEOPLE or entry["tool"] in NOT_A_HOLD:
            continue
        held = parse(entry["at"])
        resumed = next((moment for moment in resumptions if moment > held), None)
        if resumed:
            waited += (resumed - held).total_seconds()
    return waited


def classify(order):
    """How the order got through: straight-through, fixed by an agent playbook, or needing a person."""
    results = {entry["result"] for entry in order["timeline"]}
    if order["state"] == "failed":
        return "failed"
    if results & PEOPLE or order["state"] == "rejected":
        return "needs-people"
    if order.get("recovered") or "timeout" in results:
        return "auto-recovered"
    return "straight-through" if order["state"] == "completed" else "in-flight"


def order_facts(order, events):
    timings = step_timings(order, events)
    return {
        "id": order["id"], "scenario": order["scenario"], "state": order["state"], "outcome": classify(order),
        "elapsed": order.get("elapsedSeconds") if order["state"] == "completed" else None,
        "agentSeconds": sum(item["total"] for item in timings if item),
        "peopleSeconds": people_wait(order, events), "timings": timings,
    }


def turned_away(order):
    """Declined by the enterprise, or withdrawn by the service team, before any agent started on it. It never entered
    the pipeline, so it says nothing about how the pipeline performs and must not count as fallout."""
    return (order.get("enterpriseApproval") or {}).get("status") in ("declined", "withdrawn")


def facts_for(source):
    """(order, facts) for every order of a state-shaped dict that entered the pipeline."""
    events = defaultdict(list)
    for event in source["events"]:
        events[event["orderId"]].append(event)
    return [(order, order_facts(order, events[order["id"]])) for order in source["orders"].values()
            if not turned_away(order)]


def spread(values):
    return {"count": len(values), "avg": mean(values) if values else None,
            "median": median(values) if values else None, "p90": percentile(values, 0.9),
            "max": max(values) if values else None}


def sources(state, scope):
    benchmark = state.get("benchmark")
    done = benchmark and benchmark["run"]["status"] == "complete"
    if scope == "auto":
        scope = "benchmark" if done else "live"
    chosen = []
    if scope in ("all", "live"):
        chosen.append(("live", state))
    if scope in ("all", "benchmark") and benchmark:
        chosen.append(("benchmark", benchmark["sim"]))
    return scope, chosen


def compute(state, scope="auto"):
    if scope not in SCOPES:
        raise ValueError("Unknown analytics scope")
    scope, chosen = sources(state, scope)
    rows, per_agent = [], defaultdict(lambda: {"total": [], "queue": [], "process": []})
    for name, source in chosen:
        for order, facts in facts_for(source):
            rows.append({**facts, "scope": name})
            for entry, timing in zip(order["timeline"], facts["timings"]):
                if timing:
                    bucket = per_agent[entry["agent"]]
                    for key in ("total", "queue", "process"):
                        bucket[key].append(timing[key])
    completed = [row for row in rows if row["state"] == "completed"]
    cycle = spread([row["elapsed"] for row in completed])
    average = cycle["avg"]
    counts = {outcome: sum(row["outcome"] == outcome for row in rows) for outcome in OUTCOMES}
    decided = len(rows) - counts["in-flight"]
    fallout = counts["needs-people"] + counts["failed"]
    incidents = fallout + counts["auto-recovered"]
    fallout_percent = round(100 * fallout / decided, 1) if decided else None
    return {
        "scope": scope,
        "scopes": scope_summary(state),
        "sample": {"orders": len(rows), "completed": len(completed), "decided": decided,
                   "inFlight": counts["in-flight"], "sufficient": decided >= MIN_SAMPLE, "minimum": MIN_SAMPLE},
        "cycle": {
            **cycle, "slaSeconds": SLA_SECONDS, "baselineDays": BASELINE_DAYS, "targetDays": TARGET_DAYS,
            "withinSlaPercent": round(100 * sum(row["elapsed"] <= SLA_SECONDS for row in completed)
                                      / len(completed), 1) if completed else None,
            "budgetUsedPercent": average / SLA_SECONDS * 100 if average is not None else None,
            "verdict": "no-data" if average is None else "met" if average <= SLA_SECONDS else "not-met",
        },
        "lifecycle": {
            "agentSeconds": mean(row["agentSeconds"] for row in completed) if completed else None,
            "peopleSeconds": mean(row["peopleSeconds"] for row in completed) if completed else None,
        },
        "agents": [{
            "stage": stage, "agent": name, **{key: spread(per_agent[name][key]) for key in ("total", "queue", "process")},
        } for stage, name in AGENTS],
        "fallout": {
            "outcomes": counts, "falloutOrders": fallout, "incidentOrders": incidents, "decided": decided,
            "falloutPercent": fallout_percent,
            "incidentPercent": round(100 * incidents / decided, 1) if decided else None,
            "agentResolvedPercent": round(100 * counts["auto-recovered"] / incidents, 1) if incidents else None,
            "baselinePercent": BASELINE_FALLOUT, "targetPercent": TARGET_FALLOUT,
            "verdict": "no-data" if fallout_percent is None else "met" if fallout_percent < TARGET_FALLOUT else "not-met",
        },
        "scenarios": scenario_table(rows),
        "notes": notes(scope),
    }


def scenario_table(rows):
    table = []
    for scenario in sorted({row["scenario"] for row in rows}):
        group = [row for row in rows if row["scenario"] == scenario]
        done = [row["elapsed"] for row in group if row["state"] == "completed"]
        table.append({"scenario": scenario, "orders": len(group), "completed": len(done),
                      "avgSeconds": mean(done) if done else None,
                      "outcomes": {outcome: sum(row["outcome"] == outcome for row in group)
                                   for outcome in OUTCOMES if any(row["outcome"] == outcome for row in group)}})
    return table


def scope_summary(state):
    live = [order["state"] for order in state["orders"].values()]
    benchmark = state.get("benchmark")
    return {
        "live": {"orders": len(live), "completed": live.count("completed")},
        "benchmark": None if not benchmark else {
            "orders": len(benchmark["sim"]["orders"]), "status": benchmark["run"]["status"],
            "completed": sum(order["state"] == "completed" for order in benchmark["sim"]["orders"].values()),
            "run": benchmark["run"]},
    }


def notes(scope):
    items = [
        "Measured on a simulator with synthetic data. Partner and network activation are simulated, so these figures "
        "show orchestration time only; they are not production KPI evidence.",
        "Agent time is queue wait plus processing. In this demo it is dominated by the supervisor polling interval.",
        "Time waiting for a manager decision or a customer correction is shown separately, never as agent time.",
        "Fallout means an order that needed a person (manager decision or customer correction) or failed. "
        "Incidents an agent playbook fixed on its own are not fallout.",
    ]
    if scope in ("all", "benchmark"):
        items.append("The benchmark incident mix is an assumption: it keeps the 30% baseline incident rate and assumes "
                     "the playbook resolves most incidents. Validate that assumption in a pilot.")
    return items
