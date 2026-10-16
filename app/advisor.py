"""Optional live regional Azure OpenAI advice; never grants execution authority."""

import os

import httpx
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, ConfigDict, Field


class Advice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnosis: str = Field(min_length=1, max_length=1500)
    recommendedAction: str = Field(pattern="^(observe|retry_playbook|request_human_review)$")
    customerMessageEn: str = Field(min_length=1, max_length=800)
    customerMessageTr: str = Field(min_length=1, max_length=800)


def configured():
    return bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT"))


def advise(order):
    if not configured():
        raise RuntimeError("Live AI is not configured. The supervisor is running in explicit rules-demo mode.")
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
