# Agent 365 registration status

**24 November 2026, 19:21 UTC: retried; catalog registration is blocked.**
A real registration POST for the current validation agent returned HTTP **403**
(`UnknownError`, empty error message). No catalog entries, identities, blueprints,
tenant grants or runtime changes were created by this retry. Agent 365 telemetry
is not configured or verified; the app correctly remains `not-registered`.

## What was checked and attempted

The intended scope is the five current Foundry task roles plus the dispatch
supervisor used by `rg-amai-foundry-runtime-sso-20261124`. The trusted boundary is
not an autonomous agent. The old request targeted `rg-amai-isolated-20261123`;
its runtime identity IDs were not reused.

| Check | Verified result |
|---|---|
| Current Foundry data-plane metadata | All six pinned agents have an **active, existing** hosted agent identity and blueprint |
| Graph blueprint read | HTTP 200; 12 Foundry-created project/agent blueprints returned, including all six current agent blueprints |
| Signed-in Azure CLI user | `admin@mngenvmcap206412.onmicrosoft.com`; directory-role read returned **Global Reader**, not Agent ID Developer or Administrator |
| Current Graph token | No delegated `AgentRegistration.ReadWrite.All` scope; client was Microsoft Azure CLI, not the earlier Agent 365 CLI registration app |
| Catalog collection GET | HTTP 404, `UnknownError`, empty message. This does **not** establish that Agent 365 is disabled: collection listing is not documented on the current resource's methods page |
| Documented registration POST | `https://graph.microsoft.com/beta/copilot/agentRegistrations`; validation agent submitted with its existing Foundry identity/blueprint, actual source endpoint, creation timestamp and signed-in owner |
| POST result | HTTP 403, `UnknownError`, empty message; request ID `956329e1-ae01-4d30-ad08-b8d0e15b75f9` |
| Remaining five catalog writes | Not attempted after the shared authorization failure; no repeated failed calls or duplicate identities |

The server supplied no more specific error text. Missing registration scope and
the reader-only directory role are independently verified prerequisites, not an
invented expansion of that empty error message. A successful ARM deployment or
Foundry identity is not proof of Agent 365 catalog registration.

## Existing identities to reuse

These are the **hosted agents' Entra identifiers**, not their bridge Container Apps'
managed identities. Foundry and Graph both confirmed the blueprint IDs.

| Agent / pinned version | Existing agent identity | Existing blueprint ID |
|---|---|---|
| Validation / 3 | `168b0016-c356-464b-8f52-6de3bbca6cc4` | `edd38906-ec16-44ec-b5b6-e880a7b70b46` |
| Decomposition / 4 | `5556d70b-54c1-404a-bb0e-904f8ab688cc` | `5e61eb7d-8466-463e-971e-e48296682eea` |
| Provisioning / 2 | `c80deb75-57a3-4aa2-9c8b-46536488b9ee` | `2f1f8fd7-d390-4688-b0b6-f4541d55e801` |
| Fallout / 4 | `63d4ade6-ab5b-46e1-b0fa-56c2bbad01e4` | `277c9b62-fd3b-4fe3-9b73-aa1e9c373c42` |
| Customer update / 4 | `cfb8027e-bb0d-4230-848a-7fad555d41f7` | `ff80d955-31cc-4d6b-be12-5bed2fea365f` |
| Supervisor / 3 | `07bfba5d-5282-4fa0-92ef-1fb53fd80871` | `e19f76e6-e8d1-44fc-9bf0-b375c9bccce0` |

## Administrator action required

An authorized tenant administrator must verify Agent 365 tenant enablement and
approve a suitable registration client with Microsoft Graph
`AgentRegistration.ReadWrite.All`. For delegated onboarding, use the documented
Agent ID Developer/Administrator role path, or an appropriately authorized
administrator; **Global Reader is not write authority**. Sign in to that approved
client and obtain a fresh token carrying the consented registration permission.
This retry did not self-grant roles, admin consent or licenses.

Before writing, look up existing catalog records through a supported API/admin
surface and match the Foundry source-agent endpoint. Reuse the existing six hosted
identities and blueprints; do not create duplicates or replace runtime bindings.
Read back every new catalog registration and confirm its identity/source linkage.
Creating new blueprints is unnecessary for this catalog-only retry; the older
request's blueprint-creation permissions are not a prerequisite for reusing them.

Catalog registration is separate from delivered S2S observability. The current
getting-started guidance additionally requires Agent 365 enablement, at least one
**assigned** qualifying E7/Agent 365 user license for observability, and correctly
configured Microsoft OpenTelemetry/S2S export. License assignment and delivered
spans were not verified here. Mailbox/AI-teammate onboarding is unnecessary for
these background agents.

Business authorization, receiving-API allowlists and Azure RBAC must remain
unchanged after catalog registration.

## Authoritative references

- [Agent 365 getting started](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/get-started)
- [Create an agent registration](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/agent-registration/agentregistration-create)
- [Agent registration resource and supported methods](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/api/admin-settings/agent-registration/resources/agentregistration)

The registration API is a **beta** API; Microsoft does not support beta APIs for
production use. This remains a synthetic pilot, not production governance.
