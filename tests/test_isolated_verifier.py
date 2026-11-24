import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain import create_order, follow_up
from app.isolated.store import fresh_state
from tests.test_domain import payload


def test_idle_probe_does_not_confuse_due_reminders_with_no_work(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    verifier = importlib.import_module("Verify_Isolated")
    state = fresh_state()
    state.update(orders={}, events=[], audit=[], idempotency={})
    order = create_order(state, payload("human-exception"), "service-manager")
    order.update(state="pendingApproval", exception={"createdAt": order["updatedAt"], "reminders": 0})
    before = copy.deepcopy(state)
    assert verifier.scheduled_work_within(state, 75, reminder_interval=30)
    assert not verifier.scheduled_work_within(state, 75, reminder_interval=300)
    assert state == before
    follow_up(state, interval_seconds=0, actor="Trusted Policy Boundary")
    assert order["exception"]["reminders"] == 1
    assert not verifier.scheduled_work_within(state, 75, reminder_interval=300)


def test_fixture_app_verifier_refuses_live_email_before_any_external_calls(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    verifier = importlib.import_module("Verify_Foundry_App")
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"appConfiguration": {"deliveryMode": "live"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="requires simulated delivery"):
        verifier.verify(SimpleNamespace(runtime=runtime))
