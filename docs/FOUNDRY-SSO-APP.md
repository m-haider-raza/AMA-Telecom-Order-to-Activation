# Foundry application with two staff sign-in options

This edition uses the **six real hosted Foundry agents**, not just an optional
prompt-agent advisor. It preserves the existing customer, wholesale partner,
service console and case-study UI. The five task workers and dispatch-only
supervisor still have distinct bridge identities, own-project Foundry Consumer
roles, and no direct state access. The trusted boundary retains exclusive state
access and independently enforces every task contract and approval.

It is a new seven-Container-App stack, with its own private state container and
credentials. Existing application revisions, data and sign-in modes are not
changed. Reviewed hosted agent versions and model deployments are reused; the
new bridges receive only their own project invocation permission.

## New application

[Open the service-team sign-in](https://amai-boundary-qwc6ymcrjozog.mangosand-20d8d735.swedencentral.azurecontainerapps.io/login#staff)
or [the complete case study](https://amai-boundary-qwc6ymcrjozog.mangosand-20d8d735.swedencentral.azurecontainerapps.io/case-study).
This stack is `rg-amai-foundry-runtime-sso-20261124`. The case study includes the
20-slide architecture deck with the four Foundry slides integrated into its story.

The verified full snapshot preserved all **7 existing orders and 239 audit
entries**, including the complete catalogue, capacity, histories and other
source fields. No customer accounts or partner organizations existed in that
source snapshot. The source remained unchanged; the destination added exactly
one copy-provenance audit entry before its workers were activated. Its source
SHA256 is `d6302f11f900a54c164f567977c5e8efc21f37117872e75ac53affbc62c5776d`.
Acceptance uses additional, explicitly named synthetic records on the new app.

## Staff login

`AMA_STAFF_AUTH=sso+token` shows both choices on `/login#staff`:

- **Sign in with Microsoft**: the existing OIDC authorization-code/PKCE flow,
  tenant and issuer validation, managed-identity federation and Entra app roles.
  The new registration assigns the deployment owner as ServiceManager.
- **Staff access token**: a separate random onboarding credential exchanged for a
  short-lived, per-tab **ServiceOperator** session. It cannot approve manager
  exceptions, manage partners/payments, act as an agent, or bypass enterprise
  approval. Its audit identity is explicitly `Staff access token (shared)`;
  it does not pretend to identify the individual engineer.

The legacy manager bearer token is not accepted as a staff login in this mode.
Rotating `AMA_STAFF_ACCESS_TOKEN` invalidates fallback sessions without signing
out named SSO users. The session-signing secret is separate from all access
tokens. The partner integration token is a separate machine credential.

For this deployed copy, the owner's local checkout stores the engineer token in
`demo-credentials.foundry-sso.local.json`, under `staffAccessToken`. That file is
gitignored and is not available from GitHub. Share the onboarding credential
only through an approved private channel.

The registration permits first-sign-in user consent for the basic OIDC scopes
only; the application still requires an assigned service role. Tenant consent,
Conditional Access and MFA policies continue to apply. No tenant-wide role or
consent grant is made by this deployment.

## Deploy and preserve the data

Use the existing authorized Azure CLI context and **new** private receipt paths:

```powershell
python scripts\Deploy_Foundry_App.py `
  --subscription <subscription-id> `
  --resource-group rg-amai-foundry-runtime-sso-<unique-suffix> `
  --source-runtime <existing-foundry-runtime-receipt.json> `
  --manager <manager-user-principal-name> `
  --receipt <new-runtime.local.json> `
  --credentials <new-credentials.local.json>

python scripts\Copy_Foundry_State.py `
  --source-runtime <existing-foundry-runtime-receipt.json> `
  --target-runtime <new-runtime.local.json> `
  --output <private-copy-receipt.json>
```

The deploy script creates a separate staff app registration and six new worker
app-role assignments. It starts the new bridges **paused**. It does not create
another Foundry account, model deployment or hosted version.

The copy reads the source once, validates its audit chain and requires in-flight
orders/benchmarks to settle. It transfers a complete consistent snapshot through
authorized container exec, including order IDs, account password hashes,
catalogue, capacity, concerns, histories and the original audit chain. It
changes no source records and rejects a destination that changed during copy.
The destination adds one explicit provenance/audit entry. Data is held in the
operator process memory, not printed or committed. Temporary data on the new
container is removed after successful import; a failed transfer must be cleaned
up before abandoning the new stack.

The result is a **one-time independent copy**, not shared storage or ongoing
synchronization. Existing browser sessions and signed one-time links belong to
the original origin/secrets; sign in again and issue fresh verification, reset
or invitation links from the new app when needed.

After checking the private copy receipt, set `workersPaused=false` in the
private runtime receipt's `appConfiguration` and resume with the recorded
image tag:

```powershell
python scripts\Deploy_Foundry_App.py `
  --subscription <subscription-id> `
  --resource-group <new-runtime-group> `
  --source-runtime <existing-foundry-runtime-receipt.json> `
  --manager <manager-user-principal-name> `
  --receipt <new-runtime.local.json> `
  --credentials <new-credentials.local.json> `
  --resume --image-tag <recorded-image-tag>
```

Resume retains the SSO and exact per-agent Foundry bindings; it does not apply
the legacy template defaults over them. Verify all six runtime attestations,
real hosted invocation receipts, both login routes and role denials before
sharing the new origin. Never publish the credentials file or a live session.

## Achieved acceptance

Live verification passed on **24 November 2026 at 10:14:53 UTC**, using the
`foundry-sso-20261124-final` image on all seven apps:

- All six hosted endpoints retain their reviewed source hash and version pins;
  each bridge has only its own project Consumer role and registry pull access.
  The boundary has registry pull and access to its own state container only.
- The token signs in as ServiceOperator; anonymous and legacy manager-token
  access are rejected, and an operator manager-decision attempt returns 403.
- Normal order `AMA-21D10EBB19` waited for enterprise approval before completing.
  Recovery order `AMA-F3F45AEA3B` also completed through real hosted-agent calls.
  Durable provider receipts account for every committed task in both orders,
  with two and three model calls respectively.
- The existing branded login shows both sign-in choices. The deployed login
  script and integrated architecture PowerPoint/PDF match the current source.

The real managed-identity federation exchange and tenant-specific Microsoft
authorization-code/PKCE redirect succeeded. **Interactive work-account sign-in
was not performed on the user's behalf**: the first user must complete their
own Microsoft consent/MFA, subject to tenant policy.

## Pilot boundaries

This remains a synthetic-only Sweden Central pilot with simulated network
provisioning and email/SMS delivery. The mini/nano GlobalStandard model policy
and deterministic no-model roles are unchanged; global inference is not an
in-country residency guarantee. Agent 365 tenant enablement is not claimed.
The additional seven runtime apps have separate compute, registry, storage and
logging costs; token pricing is not total application cost.

See [Foundry agent implementation](FOUNDRY-AGENTS.md) and
[staff role and SSO details](STAFF-SSO.md).

The completed presentation sources are also versioned in GitHub:
[implementation, scope and identity slides](presentations/Agentic_Order_to_Activation_Foundry_Implementation.pptx)
([PDF](presentations/Agentic_Order_to_Activation_Foundry_Implementation.pdf)) and
[model selection and measured economics](presentations/TurkBizTel-Foundry-Model-Economics.pptx)
([PDF](presentations/TurkBizTel-Foundry-Model-Economics.pdf)).
