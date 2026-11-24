import json

import pytest

from app.foundry.contracts import compute, model_request, model_result


def provider_response(**overrides):
    return {"status": "completed",
            "usage": {"input_tokens": 140, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 30}},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"explanation": "Synthetic status only."})}]}],
            **overrides}


def test_model_advice_is_separate_from_authoritative_proposal():
    result = compute("customer_update", {"facts": {"orderId": "synthetic-1", "state": "simulated-activated"}})
    assert result["proposal"] == {"template": "synthetic-activation-tr-en", "orderId": "synthetic-1"}
    text, usage = model_result(provider_response())
    assert text == "Synthetic status only."
    assert usage["reasoning_tokens"] == 30
    assert usage["output_tokens"] == 100  # Reasoning is included, never added a second time.
    assert "explanation" not in result["proposal"]


@pytest.mark.parametrize("body", [
    {"facts": {"pending": 1}, "approve": True},
    {"facts": {"pending": True}},
    {"facts": {"pending": -1}},
    {"facts": {"pending": 100001}},
    {"facts": {"pending": 1, "approval": True}},
])
def test_supervisor_cannot_accept_authority_or_unbounded_inputs(body):
    with pytest.raises(ValueError):
        compute("supervisor", body)


def test_worker_facts_do_not_accept_cross_agent_contract():
    with pytest.raises(ValueError):
        compute("validation", {"facts": {"orderId": "synthetic-1", "state": "simulated-activated"}})


@pytest.mark.parametrize("response", [
    provider_response(status="incomplete"),
    provider_response(usage=None),
    provider_response(output=[{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"explanation": "x" * 501})}]}]),
    provider_response(output=[{"type": "message", "content": [
        {"type": "output_text", "text": '{"explanation":"ok","approve":true}'}]}]),
])
def test_incomplete_unmeasured_and_unbounded_outputs_fail_closed(response):
    with pytest.raises(ValueError):
        model_result(response)


def test_request_is_bounded_stateless_tool_free_and_uses_explicit_message_shape():
    request = model_request("fallout", {"failure": "simulated-partner-timeout", "attempts": 0})
    assert request["max_output_tokens"] == 768
    assert request["store"] is False
    assert "tools" not in request
    assert all(item["type"] == "message" for item in request["input"])
    assert request["text"]["format"]["strict"] is True
