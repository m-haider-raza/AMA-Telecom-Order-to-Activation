from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import (
    approve_exception, correct_order, create_order, follow_up, initial_state, metrics, partner_capacity, process_next,
    verify_audit,
)


def payload(scenario="happy", key="unique-test-order"):
    return {"customer": "Demo Test Enterprise", "city": "Istanbul", "product": "sdwan",
            "scenario": scenario, "language": "tr", "idempotencyKey": key}


def run(state, order, steps=10):
    for _ in range(steps):
        process_next(state, order["id"])
    return order


def test_happy_path_is_guarded_decomposed_and_bilingual():
    state = initial_state()
    order = create_order(state, payload(), "service-manager")
    original = partner_capacity(state, "Istanbul", "sdwan")["capacity"][0]["availableUnits"]
    run(state, order)
    assert order["state"] == "completed"
    assert len(order["serviceOrders"]) == 3
    assert len(order["resourceOrders"]) == 3
    assert {entry["agent"] for entry in order["timeline"]} == {
        "Order Validation Agent", "Order Decomposition Agent",
        "Provisioning Orchestration Agent", "Customer Update Agent",
    }
    assert order["notifications"][0]["en"]
    assert order["notifications"][0]["tr"]
    assert partner_capacity(state, "Istanbul", "sdwan")["capacity"][0]["availableUnits"] == original - 1
    before = deepcopy(state)
    run(state, order)
    assert state == before
    assert verify_audit(state)


def test_idempotency_replays_and_rejects_conflicting_payloads():
    state = initial_state()
    order = create_order(state, payload(), "service-manager")
    count = len(state["events"])
    assert create_order(state, payload(), "service-manager")["id"] == order["id"]
    assert len(state["events"]) == count
    different = payload()
    different["product"] = "cloud"
    with pytest.raises(ValueError, match="different order"):
        create_order(state, different, "service-manager")
    partner = create_order(state, payload(), "wholesale-partner")
    assert partner["id"] != order["id"]


@pytest.mark.parametrize("scenario,held", [("human-exception", "pendingApproval"), ("change-window", "held")])
def test_human_and_window_guards(scenario, held):
    state = initial_state()
    order = create_order(state, payload(scenario), "wholesale-partner")
    run(state, order)
    assert order["state"] == held
    assert order["reserved"] is False
    unchanged = deepcopy(order)
    run(state, order)
    assert order == unchanged
    with pytest.raises(ValueError, match="Only the service manager"):
        approve_exception(state, order["id"], "wholesale-partner", "approve")
    approve_exception(state, order["id"], "service-manager", "approve")
    run(state, order)
    assert order["state"] == "completed"
    assert order["approvals"][0]["simulated"] is True


def test_rejection_never_provisions():
    state = initial_state()
    order = create_order(state, payload("human-exception"), "service-manager")
    run(state, order)
    approve_exception(state, order["id"], "service-manager", "reject")
    run(state, order)
    assert order["state"] == "rejected" and not order["reserved"]


def test_fallout_recovery_is_bounded_and_all_five_agents_execute():
    state = initial_state()
    order = create_order(state, payload("auto-recovery"), "service-manager")
    run(state, order, 15)
    assert order["state"] == "completed"
    assert order["recovered"] and order["recoveryAttempts"] == 1
    assert len({entry["agent"] for entry in order["timeline"]}) == 5
    assert len([entry for entry in order["timeline"] if entry["result"] == "timeout"]) == 1


def test_one_event_per_transition_and_resume_from_persisted_snapshot():
    state = initial_state()
    order = create_order(state, payload(), "service-manager")
    process_next(state, order["id"])
    assert order["stage"] == "decomposition"
    assert len([event for event in state["events"]
                if event["orderId"] == order["id"] and event["status"] == "processed"]) == 1
    restarted = deepcopy(state)
    run(restarted, restarted["orders"][order["id"]])
    assert restarted["orders"][order["id"]]["state"] == "completed"


def test_capacity_and_sla_cannot_silently_pass():
    state = initial_state()
    for item in state["inventory"]:
        item["availableUnits"] = 0
    order = create_order(state, payload(), "service-manager")
    run(state, order)
    assert order["exception"]["type"] == "capacity"
    with pytest.raises(ValueError, match="cannot be invented"):
        approve_exception(state, order["id"], "service-manager", "approve")
    second = create_order(initial_state(), payload(key="expired-order-001"), "service-manager")
    state = initial_state()
    state["orders"][second["id"]] = second
    state["events"].append({"id": "expired-event", "orderId": second["id"], "status": "queued"})
    second["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    process_next(state, second["id"])
    assert second["exception"]["type"] == "sla-breach"
    assert second["slaBreached"] and not second["reserved"]


def test_metrics_are_honest_and_audit_detects_mutation():
    state = initial_state()
    result = metrics(state)
    assert result["targetDays"] == 7 and result["targetFalloutPercent"] == 8
    assert result["productionKpiEvidence"] is False
    assert result["auditChainValid"] is True
    state["audit"][0]["detail"] = "altered"
    assert verify_audit(state) is False


def test_customer_address_correction_is_required_and_resumes_validation():
    state = initial_state()
    order = create_order(state, payload("address-correction"), "wholesale-partner")
    run(state, order)
    assert order["state"] == "awaitingCustomer" and not order["reserved"]
    with pytest.raises(ValueError, match="not awaiting a manager"):
        approve_exception(state, order["id"], "service-manager", "approve")
    with pytest.raises(ValueError, match="does not match"):
        correct_order(state, order["id"], "wholesale-partner", "ANK-001")
    correct_order(state, order["id"], "wholesale-partner", "IST-001")
    run(state, order)
    assert order["state"] == "completed"


def test_late_sla_rejection_compensates_simulated_resources():
    state = initial_state()
    order = create_order(state, payload(), "service-manager")
    before = partner_capacity(state, "Istanbul", "sdwan")["capacity"][0]["availableUnits"]
    run(state, order, 3)
    assert order["stage"] == "customer_update" and order["reserved"]
    order["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    process_next(state, order["id"])
    approve_exception(state, order["id"], "service-manager", "reject")
    assert order["state"] == "rejected" and not order["reserved"]
    assert partner_capacity(state, "Istanbul", "sdwan")["capacity"][0]["availableUnits"] == before
    assert all(item["state"] == "cancelled" for item in order["resourceOrders"])


@pytest.mark.parametrize("field,value", [("city", "London"), ("product", "free-unlimited"),
                                         ("customer", "Real Customer"), ("scenario", "bypass-approval")])
def test_invalid_domain_inputs(field, value):
    state = initial_state()
    value_payload = payload()
    value_payload[field] = value
    with pytest.raises(ValueError):
        create_order(state, value_payload, "service-manager")


CONTACT = {"contactName": "Demo Contact", "email": "ayse@example.com", "phone": "+905550000001",
           "preferredChannel": "email", "updateFrequency": "every-step"}


def with_contact(scenario="happy", key="contact-test-order", **overrides):
    return {**payload(scenario, key), "contact": {**CONTACT, **overrides}}


def test_contact_updates_follow_chosen_frequency():
    state = initial_state()
    every = create_order(state, with_contact(key="freq-every-step"), "service-manager")
    daily = create_order(state, with_contact(key="freq-daily", updateFrequency="daily"), "service-manager")
    quiet = create_order(state, with_contact(key="freq-quiet", updateFrequency="critical-only"), "service-manager")
    for order in (every, daily, quiet):
        run(state, order)
    def statuses(order):
        return [(item["kind"], item["status"]) for item in order["outbox"]]

    assert statuses(every) == [("progress", "simulated-sent")] * 2 + [("critical", "simulated-sent")]
    assert ("progress", "skipped-by-preference") in statuses(quiet)
    assert statuses(quiet)[-1] == ("critical", "simulated-sent")
    assert all(item["status"] == "simulated-sent" for item in daily["outbox"])
    assert all(item["to"] == "a***@example.com" and not item["deliveredExternally"] for item in every["outbox"])


def test_digest_waits_for_its_interval_until_a_critical_update():
    state = initial_state()
    order = create_order(state, with_contact(key="digest-wait", scenario="human-exception", updateFrequency="weekly"),
                         "service-manager")
    run(state, order)
    assert order["state"] == "pendingApproval"
    assert [item["status"] for item in order["outbox"]] == ["simulated-sent"] * len(order["outbox"])
    parked = create_order(state, with_contact(key="digest-parked", updateFrequency="weekly"), "service-manager")
    process_next(state, parked["id"])
    assert parked["outbox"][0]["status"] == "scheduled-digest"
    follow_up(state, at=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat())
    assert parked["outbox"][0]["status"] == "simulated-sent"


def test_orders_without_contact_record_that_nothing_can_be_sent():
    state = initial_state()
    order = run(state, create_order(state, payload(key="no-contact-order"), "service-manager"))
    assert order["contact"] is None and order["outbox"][-1]["status"] == "skipped-no-contact"


def test_stuck_order_with_wrong_site_triggers_bounded_automatic_reminders():
    state = initial_state()
    order = create_order(state, with_contact("address-correction", "stuck-site-order", updateFrequency="critical-only"),
                         "wholesale-partner")
    run(state, order)
    assert order["state"] == "awaitingCustomer"
    assert "IST-001" in order["outbox"][-1]["en"]
    start = datetime.fromisoformat(order["exception"]["createdAt"])
    follow_up(state, interval_seconds=60, at=(start + timedelta(seconds=30)).isoformat())
    assert len(order["outbox"]) == 1
    for index in range(1, 6):
        follow_up(state, interval_seconds=60, at=(start + timedelta(seconds=61 * index)).isoformat())
    reminders = [item for item in order["outbox"] if "Reminder" in item["en"]]
    assert len(reminders) == 3 and all(item["status"] == "simulated-sent" for item in reminders)
    assert order["exception"]["escalated"] and order["timeline"][-1]["tool"] == "customer.reminders-exhausted"
    correct_order(state, order["id"], "wholesale-partner", "IST-001")
    run(state, order)
    assert order["state"] == "completed" and verify_audit(state)


@pytest.mark.parametrize("bad", [{"preferredChannel": "sms", "phone": None}, {"updateFrequency": "hourly"},
                                 {"preferredChannel": "pigeon"}])
def test_invalid_contact_is_rejected(bad):
    with pytest.raises(ValueError):
        create_order(initial_state(), with_contact(key="invalid-contact", **bad), "service-manager")
