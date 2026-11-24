"""Stateless, invocation-only Foundry worker. No portal, storage or carrier tools."""

import asyncio
import logging
import os

import httpx
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.foundry.contracts import MODELS, compute, model_request, model_result

log = logging.getLogger("ama.foundry")
host = InvocationAgentServerHost()


def explain(agent, facts):
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    with ManagedIdentityCredential() as credential:
        token = credential.get_token("https://ai.azure.com/.default").token
        response = httpx.post(endpoint + "/openai/v1/responses", json=model_request(agent, facts),
                              headers={"Authorization": "Bearer " + token}, timeout=90)
    response.raise_for_status()
    body = response.json()
    # Log the provider receipt before parsing so incomplete/invalid paid outputs remain accounted.
    log.info("foundry.model.receipt agent=%s responseId=%s usage=%s", agent, body.get("id"), body.get("usage"))
    explanation, usage = model_result(body)
    return {"explanation": explanation, "modelCalls": 1, "usage": usage,
            "responseId": body["id"], "model": body["model"]}


@host.invoke_handler
async def handle(request: Request) -> Response:
    try:
        body = await request.json()
        agent = os.environ["ISOLATED_AGENT"]
        result = compute(agent, body)
    except (ValueError, KeyError, TypeError) as error:
        log.warning("foundry.contract.denied errorType=%s", type(error).__name__)
        return JSONResponse({"error": "Invalid bounded worker contract"}, status_code=400)
    if agent in MODELS:
        try:
            result.update(await asyncio.to_thread(explain, agent, body["facts"]))
        except (httpx.HTTPError, AzureError, ValueError, KeyError) as error:
            log.error("foundry.model.failed agent=%s errorType=%s usageMayBeUnknown=true", agent, type(error).__name__)
            return JSONResponse({"error": "Model invocation failed; no fallback", "usageKnown": False},
                                status_code=502)
    return JSONResponse({**result, "agentVersion": os.environ.get("FOUNDRY_AGENT_VERSION")})


if __name__ == "__main__":
    host.run()
