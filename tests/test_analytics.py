from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app import analytics, benchmark, projection, sqlwrite
from app.analytics import compute, percentile, people_wait, spread, step_timings
from app.domain import approve_exception, correct_order, create_order, initial_state, process_next, verify_audit

START = "2026-03-01T10:00:00+00:00"


def at(offset):
    return (datetime.fromisoformat(START) + timedelta(seconds=offset)).isoformat()


def payload(scenario, key):
    return {"customer": "Demo Test Enterprise", "city": "Istanbul", "product": "sdwan",
            "scenario": scenario, "language": "tr", "idempotencyKey": key}


def finish(state, order, decision="approve"):
    for _ in range(10):
        process_next(state, order["id"])
    if order["state"] in ("pendingApproval", "held"):
        approve_exception(state, order["id"], "service-manager", decision)
    elif order["state"] == "awaitingCustomer":
        correct_order(state, order["id"], "service-manager", "IST-001")
    for _ in range(10):
        process_next(state, order["id"])
    return order


def completed_run(size=50):
    state = initial_state()
    benchmark.start(state, size, human_delay=0)
    for _ in range(40):
        benchmark.advance(state)
        if state["benchmark"]["run"]["status"] != "running":
            break
    return state


@pytest.fixture(scope="module")
def benchmarked():
    return completed_run(50)


@pytest.mark.parametrize("size", benchmark.SIZES)
def test_scenario_mix_keeps_the_assumed_incident_rates(size):
    mix = benchmark.mix_for(size)
    assert sum(mix.values()) == size
    assert len(benchmark.scenarios_for(size)) == size
    needing_people = mix["human-exception"] + mix["change-window"] + mix["address-correction"]
    assert 100 * needing_people / size < analytics.TARGET_FALLOUT
    incidents = needing_people + mix["auto-recovery"]
    assert abs(100 * incidents / size - analytics.BASELINE_FALLOUT) <= 1


def test_benchmark_runs_every_order_through_the_agents_apart_from_live_orders(benchmarked):
    state = benchmarked
    run = state["benchmark"]["run"]
    live_orders = len(initial_state()["orders"])
    assert run["status"] == "complete" and run["error"] is None
    assert len(state["benchmark"]["sim"]["orders"]) == 50
    assert len(state["orders"]) == live_orders
    assert all(order["state"] == "completed" for order in state["benchmark"]["sim"]["orders"].values())
    assert state["audit"][-1]["action"] == "benchmark.started" and verify_audit(state)


def test_benchmark_report_shows_both_targets_met(benchmarked):
    report = compute(benchmarked)
    assert report["scope"] == "benchmark"
    assert report["sample"] == {"orders": 50, "completed": 50, "decided": 50, "inFlight": 0,
                                "sufficient": True, "minimum": analytics.MIN_SAMPLE}
    assert report["cycle"]["verdict"] == "met"
    assert report["cycle"]["avg"] < analytics.SLA_SECONDS
    assert report["cycle"]["withinSlaPercent"] == 100.0
    assert report["fallout"]["verdict"] == "met"
    assert report["fallout"]["falloutPercent"] < analytics.TARGET_FALLOUT
    assert report["fallout"]["falloutOrders"] == 3
    assert report["fallout"]["outcomes"]["auto-recovered"] == 12
    assert [item["stage"] for item in report["agents"]] == [stage for stage, _ in analytics.AGENTS]
    measured = {item["stage"]: item["total"]["count"] for item in report["agents"]}
    assert all(measured[stage] for stage in ("validation", "decomposition", "provisioning", "customer_update"))
    assert any("assumption" in note for note in report["notes"])


def test_a_run_needs_a_valid_size_and_cannot_overlap_another():
    state = initial_state()
    with pytest.raises(ValueError, match="size"):
        benchmark.start(state, 75)
    benchmark.start(state, 50, human_delay=0)
    with pytest.raises(ValueError, match="in progress"):
        benchmark.start(state, 50)


def test_report_stays_on_live_orders_until_a_run_is_complete():
    state = initial_state()
    benchmark.start(state, 50, human_delay=0)
    assert compute(state)["scope"] == "live"
    assert compute(state, "benchmark")["sample"]["orders"] == 50
    assert compute(state, "all")["sample"]["orders"] == 50 + len(state["orders"])
    with pytest.raises(ValueError, match="scope"):
        compute(state, "yesterday")


def test_a_failing_run_is_recorded_and_never_breaks_the_supervisor(monkeypatch):
    state = initial_state()
    benchmark.start(state, 50, human_delay=0)

    def explode(*_):
        raise RuntimeError("agent exploded")
    monkeypatch.setattr(benchmark, "process_next", explode)
    benchmark.advance(state)
    run = state["benchmark"]["run"]
    assert run["status"] == "failed" and "RuntimeError: agent exploded" in run["error"]
    assert compute(state)["scope"] == "live"
    benchmark.start(state, 50)


def test_a_run_that_cannot_finish_times_out():
    state = initial_state()
    run = benchmark.start(state, 50, human_delay=10 ** 6)
    for _ in range(10):
        benchmark.advance(state)
    assert run["status"] == "running"
    later = datetime.fromisoformat(run["startedAt"]) + timedelta(seconds=benchmark.TIMEOUT_SECONDS + 1)
    benchmark.advance(state, at=later.isoformat())
    assert run["status"] == "failed" and "Not finished" in run["error"]


@pytest.mark.parametrize("scenario,outcome", [
    ("happy", "straight-through"), ("auto-recovery", "auto-recovered"), ("human-exception", "needs-people"),
    ("change-window", "needs-people"), ("address-correction", "needs-people"),
])
def test_each_order_is_classified_by_how_it_got_through(scenario, outcome):
    state = initial_state()
    order = create_order(state, payload(scenario, "classify-" + scenario), "service-manager")
    assert analytics.classify(finish(state, order)) == outcome
    assert order["state"] == "completed"


def test_a_rejected_order_counts_as_fallout_and_a_failed_one_too():
    assert analytics.classify({"state": "rejected", "timeline": []}) == "needs-people"
    assert analytics.classify({"state": "failed", "timeline": [{"result": "success"}]}) == "failed"
    assert analytics.classify({"state": "inProgress", "timeline": [{"result": "success"}]}) == "in-flight"


def test_agent_time_pairs_each_step_with_the_event_that_triggered_it():
    order = {"timeline": [{"at": at(3)}, {"at": at(13)}, {"at": at(500)}]}
    events = [
        {"createdAt": at(0), "processedAt": at(2)},
        {"createdAt": at(10), "processedAt": at(12.5)},
        {"createdAt": at(300), "processedAt": at(300)},
        {"createdAt": at(400)},
    ]
    first, second, third = step_timings(order, events)
    assert first == {"queue": 2.0, "process": 1.0, "total": 3.0}
    assert second == {"queue": 2.5, "process": 0.5, "total": 3.0}
    assert third is None


def test_waiting_for_a_person_is_reported_apart_from_agent_time():
    order = {"timeline": [
        {"at": at(10), "result": "success", "tool": "feasibility.check"},
        {"at": at(20), "result": "human-required", "tool": "policy.hold"},
        {"at": at(900), "result": "customer-required", "tool": "customer.reminders-exhausted"},
    ], "approvals": [{"at": at(620)}]}
    assert people_wait(order, []) == 600.0
    assert people_wait({"timeline": [{"at": at(20), "result": "human-required", "tool": "policy.hold"}],
                        "approvals": []}, []) == 0.0


def test_percentiles_and_spread():
    assert percentile([], 0.9) is None
    assert percentile([4, 1, 3, 2], 0.5) == 2.5
    assert percentile([5], 0.9) == 5
    result = spread([1, 2, 3, 10])
    assert (result["count"], result["avg"], result["median"], result["max"]) == (4, 4, 2.5, 10)
    assert result["p90"] == pytest.approx(7.9)
    assert spread([]) == {"count": 0, "avg": None, "median": None, "p90": None, "max": None}


def test_an_empty_book_has_no_verdict():
    state = initial_state()
    state["orders"], state["events"] = {}, []
    report = compute(state)
    assert report["cycle"]["verdict"] == "no-data" and report["fallout"]["verdict"] == "no-data"
    assert report["sample"]["sufficient"] is False


def test_projection_rows_match_their_declared_columns(benchmarked):
    state = deepcopy(benchmarked)
    live = create_order(state, {**payload("happy", "projection-live"),
                                "contact": {"preferredChannel": "email", "email": "owner@example.com",
                                            "phone": "+905551112233", "updateFrequency": "every-step"}},
                        "customer-portal")
    finish(state, live)
    rows = projection.project(state)
    assert set(rows) == set(projection.TABLES)
    assert len(rows["Orders"]) == len(state["orders"]) + 50
    assert {key[0] for key in rows["Orders"]} == {"live", "benchmark"}
    assert {key[0] for key in rows["AuditLog"]} == {"live"}
    for name, table in projection.TABLES.items():
        for row in rows[name].values():
            assert len(row) == len(table.names)
            for value, (column, kind), limit in zip(row, table.columns, table.limits):
                assert value is not None or "NOT NULL" not in kind, (name, column)
                if limit and isinstance(value, str):
                    assert projection.units(value) <= limit, (name, column)
    flattened = [str(value) for table in rows.values() for row in table.values() for value in row]
    assert not any("owner@example.com" in value or "5551112233" in value for value in flattened)


def test_a_change_is_written_as_delete_and_insert_and_a_removal_as_delete():
    old = {name: {} for name in projection.TABLES}
    new = {name: {} for name in projection.TABLES}
    old["Inventory"] = {("Istanbul", "sdwan"): ("Istanbul", "sdwan", 10), ("Ankara", "sdwan"): ("Ankara", "sdwan", 5),
                        ("Izmir", "sdwan"): ("Izmir", "sdwan", 7)}
    new["Inventory"] = {("Istanbul", "sdwan"): ("Istanbul", "sdwan", 9), ("Ankara", "sdwan"): ("Ankara", "sdwan", 5),
                        ("Bursa", "sdwan"): ("Bursa", "sdwan", 1)}
    plan = projection.diff(old, new)["Inventory"]
    assert sorted(plan["delete"]) == [("Istanbul", "sdwan"), ("Izmir", "sdwan")]
    assert sorted(plan["insert"]) == [("Bursa", "sdwan", 1), ("Istanbul", "sdwan", 9)]
    assert all(not plan["insert"] and not plan["delete"] for plan in projection.diff(new, new).values())


def test_long_text_is_cut_to_the_column_without_splitting_a_character():
    table = projection.Table((("Name", "nvarchar(5) NOT NULL"), ("Count", "int NOT NULL")), key=("Name",))
    assert table.fit(("abc", 1)) == ("abc", 1)
    assert table.fit(("abcdefgh", 1)) == ("abcde", 1)
    emoji = table.fit(("a" + "\U0001F600" * 4, 1))[0]
    assert emoji == "a\U0001F600\U0001F600" and projection.units(emoji) <= 5


class Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters=()):
        self.calls.append((sql, tuple(parameters)))


def test_inserts_respect_the_driver_limits_and_write_missing_values_as_null():
    table = projection.TABLES["Orders"]
    rows = [tuple(None if name == "PartnerId" else f"{name}-{number}" for name in table.names)
            for number in range(300)]
    statements = list(sqlwrite.insert_statements("Orders", rows))
    assert len(statements) > 1
    for sql, parameters in statements:
        assert len(parameters) <= sqlwrite.MAX_PARAMETERS and sql.count("(?") <= sqlwrite.MAX_ROWS
        assert sql.count("?") == len(parameters)
    assert sum(sql.count("NULL") for sql, _ in statements) == 300
    assert sum(len(parameters) for _, parameters in statements) == 300 * (len(table.names) - 1)
    assert list(sqlwrite.insert_statements("Orders", [])) == []


def test_deletes_name_the_whole_key_and_stay_within_the_parameter_limit():
    single = list(sqlwrite.delete_statements("Messages", [("m1",), ("m2",)]))
    assert single == [("DELETE FROM ama.Messages WHERE MessageId IN (?, ?)", ("m1", "m2"))]
    pair = list(sqlwrite.delete_statements("OrderSteps", [("live", "O-1", 1), ("live", "O-1", 2)]))
    assert pair == [("DELETE FROM ama.OrderSteps WHERE (Scope = ? AND OrderId = ? AND Seq = ?) "
                     "OR (Scope = ? AND OrderId = ? AND Seq = ?)", ("live", "O-1", 1, "live", "O-1", 2))]
    many = list(sqlwrite.delete_statements("Orders", [("live", f"O-{number}") for number in range(2500)]))
    assert len(many) == 3 and all(len(parameters) <= sqlwrite.MAX_PARAMETERS for _, parameters in many)
    assert sum(len(parameters) for _, parameters in many) == 5000


def test_a_rebuild_empties_every_table_before_inserting_and_a_plan_deletes_before_inserting():
    cursor = Recorder()
    rows = {name: {} for name in projection.TABLES}
    rows["Inventory"] = {("Istanbul", "sdwan"): ("Istanbul", "sdwan", 10)}
    sqlwrite.rebuild(cursor, rows)
    sql = [call[0] for call in cursor.calls]
    assert sql[:len(projection.TABLES)] == [f"DELETE FROM ama.{name}" for name in projection.TABLES]
    assert sql[len(projection.TABLES):] == [
        "INSERT INTO ama.Inventory (City, Product, AvailableUnits) VALUES (?, ?, ?)"]
    cursor = Recorder()
    plan = {name: {"delete": [], "insert": []} for name in projection.TABLES}
    plan["Inventory"] = {"delete": [("Istanbul", "sdwan")], "insert": [("Istanbul", "sdwan", 9)]}
    sqlwrite.write_plan(cursor, plan)
    assert [call[0].split()[0] for call in cursor.calls] == ["DELETE", "INSERT"]
