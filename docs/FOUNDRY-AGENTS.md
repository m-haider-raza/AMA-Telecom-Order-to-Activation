# Isolated Foundry agents

This is a separate synthetic-only copy of the isolated application. Six real
hosted-code agents run in six Foundry projects; they are not registry stubs or
legacy Assistants. A new trusted boundary and six narrow managed-identity bridges
run in seven Container Apps. No original application, identity, credentials or
state is reused. Agent 365 tenant enablement is **not** claimed.

## Deployed architecture

Tenant `6828d9b6-f0ce-43c7-9e56-14bc61a6b93c`, subscription
`9f674eb0-b40e-475d-bf85-a2a53238f25b`:

- Foundry: `rg-amai-foundry-20261123`, account `amaif20261123sc`, Sweden Central.
- Bridges/boundary: `rg-amai-foundry-runtime-20261123`.
- Boundary: <https://amai-boundary-az4277efijrnm.whiterock-b52a2025.swedencentral.azurecontainerapps.io>.

| Project / hosted agent | Model | Function |
|---|---|---|
| `validation` / `amai-validation` | None | Exact deterministic site/catalogue/capacity proposal |
| `decomposition` / `amai-decomposition` | `amai-mini` | Bounded dependency explanation beside a deterministic proposal |
| `provisioning` / `amai-provisioning` | None | Deterministic simulated reservation proposal |
| `fallout` / `amai-fallout` | `amai-mini` | Bounded simulated-timeout explanation; recovery remains allowlisted |
| `customer-update` / `amai-customer-update` | `amai-nano` | Short customer-safe wording; authoritative notification remains deterministic |
| `supervisor` / `amai-supervisor` | None | Dispatch pending work only |

Invocation endpoint for each project:
`https://amaif20261123sc.services.ai.azure.com/api/projects/<project>/agents/amai-<project>/endpoint/protocols/invocations?api-version=v1`.

`amai-mini` is `gpt-5.4-mini`, and `amai-nano` is `gpt-5.4-nano`; both are pinned
to `2026-03-17`, `GlobalStandard`, capacity 2, with `NoAutoUpgrade`.
The small current-GA models avoid unnecessary frontier-model expense and the
deprecated GPT-4.1 mini/nano lifecycle. **GlobalStandard does not guarantee
EU-only inference processing**. Only synthetic data is permitted in this pilot.

Every bridge has one managed identity, AcrPull on the new registry, and Foundry
Agent Consumer on its own project only. Hosted agent instance identities and
blueprints are separate from bridge identities. Hosted code has no state,
telecom, approval or external carrier tools. Only the trusted boundary holds
container-scoped state access; it recomputes proposals and atomically validates
transitions. Models cannot approve exceptions or commit allocations.

## Bounds and accounting

Each request has an exact per-agent facts contract, maximum encoded facts 4096
bytes. Model requests use strict single-field JSON, explanations at most 500
characters, no tools, `store=false`, low reasoning effort, and output caps
768 tokens for mini / 256 for nano. Invalid or incomplete output fails closed;
there is no silent model fallback.

A completed normal order uses one mini decomposition explanation and one nano
completion message, not three artificial customer updates. A recovered order
adds one mini fallout explanation. Validation/provisioning/supervisor make zero
model calls; provisioning can run twice on recovery. Supervisor dispatch
invocations are shared batches and must not be assigned fictitious per-order
counts. Successful per-event/revision proposals are cached against submit replay.

Receipts preserve event ID, agent/version, response ID, returned model version,
latency, attempt logs, input/output/reasoning/cache usage. Reasoning is a **subset
of output**, not an additional billable total. `cache_write_tokens=null` means
the provider omitted that metric, not measured zero. Deterministic no-inference
calls have zero tokens. HTTP failures without provider usage are unknown, not
zero. Standalone diagnostic calls are not per-order averages and must remain
separate from completed-order receipts.

Token pricing alone excludes six hosted agents' compute, seven Container Apps,
registry, storage and logging. Superseded hosted versions must be inventoried;
an unselected version is not necessarily stopped or free. No billing invoice
measurement is implied by a deployment or token receipt.

## Reproduction and acceptance

Use a new private receipt location; do not commit generated credentials.
Deployment scripts validate the tenant/subscription and owned purpose-tagged
resources. Account key authentication is disabled. They do not grant tenant
roles, consent, delete resources, or print tokens.

```powershell
python scripts\Deploy_Foundry.py --resource-group <new-owned-group> --account <new-account> --receipt <private-foundry-receipt.json>
python scripts\Deploy_Foundry_Models.py --help
python scripts\Deploy_Foundry_Agents.py --help
python scripts\Connect_Foundry.py --help
python scripts\Verify_Foundry.py `
  --runtime-receipt <private-runtime-receipt.json> `
  --foundry-receipt <private-foundry-receipt.json> `
  --credentials <private-runtime-credentials.json> `
  --output <private-final-verification.json>
```

Hosted deployment uses deterministic ZIP content hashes and explicitly pins
each invocation endpoint to the reviewed numeric version. Updating bridge
images must preserve the added Foundry environment bindings; blindly redeploying
the original isolated template would remove them.

The final verifier checks exact hosted code hashes/version pins and model
deployments, seven image-identical runtimes, narrow RBAC, real receiving-API
identity attestations, cross-project and agent-edit denials, state/other-identity
denials, recovery through a provisioning restart, intake replay, atomic allocation,
audit integrity, held approvals/windows, and actual normal/recovery order usage.
The dated private acceptance receipt is the authority for achieved results.

Final acceptance on 2026-11-24 passed for `amai-validation:3`,
`amai-decomposition:4`, `amai-provisioning:2`, `amai-fallout:4`,
`amai-customer-update:4`, and `amai-supervisor:3`. All share deployed ZIP SHA-256
`997c0039a906626b81ff714c5464051182b003d9743072895a8fd4c7bbfc524b`.
The seven runtime images share digest
`sha256:3194728d59366990e3625b0ba59a8d307e5f83954d7a4a2a7e33e6f7dbd41852`.

| Actual synthetic order | Model calls | Input | Output | Reasoning subset | Total |
|---|---|---|---|---|---|
| Normal `AMA-C8778730FF` | mini 1, nano 1 | 292 | 255 | 130 | 547 |
| Recovery `AMA-E0DC66EB54` | mini 2, nano 1 | 422 | 357 | 179 | 779 |

Both orders report cache-read/write tokens 0, with no failed/unaccounted
invocation attempts. These are two measured samples, not averages or production
forecasts. Normal hosted worker calls are validation 1, decomposition 1,
provisioning 1, customer update 1; recovery adds fallout 1 and provisioning's
second call. Their detailed private JSON includes all provider response IDs,
versions, attempts and latency. Supervisor aggregate dispatch receipts remain
separate.

Restart-spanning receipts come from the new workspace's **durable Log Analytics
logs**, correlated against every `isolated.commit` audit event. Current-replica
console logs alone can lose provisioning receipts after the intentional restart.
The verifier fails on missing events/attempts rather than reporting missing
deterministic invocations as zero. If ingestion has not caught up, the
`--resume-completed-orders` option rechecks pins and receipts without creating
orders or making additional model calls.

The original 65-second idle test initially overlapped legitimate seeded
approval/change-window reminders. The verifier now deep-copies state and uses the
existing `follow_up` logic to find a genuinely quiescent 75-second horizon before
testing. It does **not** suppress reminders, reset state, allow idle model calls,
or weaken exact full-state equality and no durable audit growth. A regression
test proves the scheduling predictor does not mutate the real snapshot.
Final durable logs also prove **zero hosted invocations** during that idle window.

The final scoped inventory retained 14 superseded ready versions. Their 20
historical sessions were already idle with `stopped_at`, with zero running
superseded sessions: no stop or deletion was needed. Version status `active`
means ready, not necessarily running billable compute. Current-version sessions
can incur compute charges until suspension; the unchanged platform default idle
timeout is 900 seconds. No hosted identity has an additional Azure RBAC grant;
its platform-default project inference/session permissions still apply.

## Earlier failures and retained resources

The first bounded attempt failed AIServices provisioning. The explicitly
requested retry recovered the same owned Sweden account on 2026-11-23. Earlier
support receipts remain useful; they no longer describe current deployment.

| Group / account | Earlier result / retained residual |
|---|---|
| `rg-amai-foundry-20261123` / `amaif20261123sc` | Recovered; now hosts the six projects and two models |
| `rg-amai-foundry-weu-20261123` / `amaif20261123weu` | HTTP403, region rejecting new customers; group retained empty |
| `rg-amai-foundry-step-20261123` / `amaif20261123step` | HTTP500; failed plain AIServices account retained |
| `rg-amai-foundry-eus2-20261123` / `amaif20261123eus2` | HTTP500; failed account retained |

Earlier correlation IDs: Sweden `aad4c263-3dd4-4041-9957-3d736b9a56a1`,
West Europe `a6a2dc83-ff6d-46d1-ad2b-25efd37c22c7`, plain Sweden
`1e8f1ab1-0500-4333-9369-778fea56f8f6`, East US 2
`6b17640f-2081-40c2-8f7d-7fcb1450974c`.
No residual resource is automatically deleted. Inventory failed accounts through
direct Cognitive Services queries: generic ARM resource listing can omit them.

## References

- [Isolated trusted boundary](ISOLATED-AGENTS.md)
- [Create a Foundry project](https://learn.microsoft.com/en-us/azure/foundry/how-to/create-projects)
- [Hosted-code deployment and protocol](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent-code)
- [Hosted-agent permissions](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-permissions)
- [Model retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule)
