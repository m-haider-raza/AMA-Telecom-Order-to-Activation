"""Optional Foundry Agent Service or regional Azure OpenAI advice; no execution authority."""

import json
import os
import re

import httpx
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, ConfigDict, Field


class Advice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnosis: str = Field(min_length=1, max_length=1500)
    recommendedAction: str = Field(pattern="^(observe|retry_playbook|request_human_review)$")
    customerMessageEn: str = Field(min_length=1, max_length=800)
    customerMessageTr: str = Field(min_length=1, max_length=800)


INSTRUCTIONS = (
    "Advise on this simulated telecom order. Return only JSON with diagnosis, recommendedAction "
    "(observe, retry_playbook, or request_human_review), customerMessageEn and customerMessageTr. "
    "Do not approve work, execute actions, or claim actual network changes or completion."
)
PROJECT_URL = r"https://[a-z0-9-]+\.services\.ai\.azure\.com/api/projects/[\w-]+"
AGENT_NAME = r"[a-zA-Z0-9_-]+"


def foundry_settings():
    return (os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/"),
            os.getenv("FOUNDRY_AGENT_NAME", "").strip())


def configuration_problems():
    endpoint, agent = foundry_settings()
    if (endpoint or agent) and not (re.fullmatch(PROJECT_URL, endpoint) and re.fullmatch(AGENT_NAME, agent)):
        return ["Set FOUNDRY_PROJECT_ENDPOINT to a Microsoft Foundry project HTTPS URL and FOUNDRY_AGENT_NAME "
                "to a prompt agent name"]
    return []


def configured():
    return bool(all(foundry_settings()) or
                (os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT")))


def label():
    if all(foundry_settings()):
        return "Microsoft Foundry agent"
    return "Regional Azure OpenAI advisor" if configured() else "Deterministic rules demo"


def foundry_advice(order, endpoint, agent, credential):
    # Categorical workflow fields only: no order ID, contact information or exception free text.
    context = {key: order.get(key) for key in ("state", "product", "scenario", "stage")}
    context["exceptionType"] = (order.get("exception") or {}).get("type")
    bearer = credential.get_token("https://ai.azure.com/.default").token
    with httpx.Client(timeout=35) as client:
        response = client.post(
            f"{endpoint}/agents/{agent}/endpoint/protocols/openai/responses",
            params={"api-version": "v1"},
            headers={"Authorization": f"Bearer {bearer}"},
            json={"input": [{"role": "user", "content": f"{INSTRUCTIONS}\n{json.dumps(context)}"}],
                  "store": False, "max_output_tokens": 2000},
        )
        response.raise_for_status()
        body = response.json()
    if body["status"] != "completed":
        raise RuntimeError("Foundry agent did not complete its response")
    outputs = [item for item in body["output"] if item["type"] != "reasoning"]
    if len(outputs) != 1 or outputs[0]["type"] != "message" or outputs[0]["role"] != "assistant":
        raise RuntimeError("Foundry agent did not return a single assistant message")
    parts = outputs[0]["content"]
    if not parts or any(part["type"] != "output_text" for part in parts):
        raise RuntimeError("Foundry agent did not return text advice")
    advice = Advice.model_validate_json("".join(part["text"] for part in parts))
    return {**advice.model_dump(), "mode": "live-foundry-agent", "executionAuthority": False}


def advise(order):
    if not configured():
        raise RuntimeError("Live AI is not configured. The supervisor is running in explicit rules-demo mode.")
    project, agent = foundry_settings()
    if problems := configuration_problems():
        raise RuntimeError(problems[0])
    if project:
        with DefaultAzureCredential() as credential:
            return foundry_advice(order, project, agent, credential)
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    # Only synthetic operational fields are sent; tokens and customer-entered text are excluded.
    context = {key: order.get(key) for key in ("id", "state", "product", "scenario", "stage", "exception")}
    credential = DefaultAzureCredential()
    try:
        bearer = credential.get_token("https://cognitiveservices.azure.com/.default").token
        with httpx.Client(timeout=35) as client:
            response = client.post(
                f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21",
                headers={"Authorization": f"Bearer {bearer}"},
                json={
                    "messages": [
                        {"role": "system", "content": (
                            "You advise a synthetic Turkish telecom demo. Never claim actual network changes. "
                            "Return JSON with diagnosis, recommendedAction (observe, retry_playbook, or "
                            "request_human_review), customerMessageEn, customerMessageTr. "
                            "Do not approve work or invent completion. All execution is separately policy-guarded."
                        )},
                        {"role": "user", "content": str(context)},
                    ],
                    "response_format": {"type": "json_object"}, "max_tokens": 700, "temperature": 0.2,
                },
            )
            response.raise_for_status()
            advice = Advice.model_validate_json(response.json()["choices"][0]["message"]["content"])
            return {**advice.model_dump(), "mode": "live-regional-azure-openai", "executionAuthority": False}
    finally:
        credential.close()
