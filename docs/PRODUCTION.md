# Production readiness and Turkish telecom deployment

## The residency boundary is a release gate

The assignment requires **all customer data and inference to remain in Türkiye**. A synthetic-only West Europe demonstration was explicitly approved, but that does not waive production obligations. Azure's [geography inventory](https://azure.microsoft.com/explore/global-infrastructure/geographies/) and [data-residency guidance](https://azure.microsoft.com/explore/global-infrastructure/data-residency/) must be rechecked before procurement. No operational Türkiye public region was available for this delivery.

A credible production path is an operator-owned or approved Türkiye-hosted platform for the workflow services, order data, event bus, secrets, logs, backups and inference. Evaluate Azure Local / Arc-enabled infrastructure for management only after verifying which management metadata or telemetry would leave the country. Arc alone is not a data-residency guarantee. A Türkiye-hosted inference server with approved model licensing and controlled egress can replace the optional Azure OpenAI advisor; cloud inference outside Türkiye must remain disabled where the mandate forbids it.

Do not equate EU hosting, encryption or a regional Azure OpenAI deployment with Turkish localization. Regional Azure OpenAI controls inference geography but cannot place inference in a nonexistent Azure public region. Global deployment can route inference outside the selected region; see [deployment types](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/deployment-types).

Legal counsel, the operator's DPO and security/regulatory owners must determine the actual applicable KVKK/BTK obligations, transfer mechanisms, lawful basis, retention and audit-evidence requirements. This repository does not provide a legal compliance determination.

## Close the gaps before touching a carrier

| Area | Demo implementation | Production acceptance gate |
|---|---|---|
| Identity | Two separate demo bearer tokens; read-only synthetic views public | Entra/OIDC identity, MFA, individual identities, tenant isolation, short-lived tokens, conditional access and least-privilege operator roles |
| TM Forum | Explicitly documented TMF-inspired DTOs/endpoints | Full versioned TMF622/641/652/638/639 contracts as applicable, OpenAPI validation, conformance tests, subscriptions/hubs and partner contracts |
| OSS/BSS adapters | Shared in-process network/CRM/partner simulators | Authenticated real CRM, inventory, network-domain and field-service adapters with circuit breakers, reconciliation, idempotent side effects and compensating actions |
| Workflow | Single replica, atomic persisted queue and supervisor | Durable distributed workflow/event bus with partitions, outbox/inbox, dead-letter handling, backpressure, HA and tested disaster recovery |
| Resource truth | One in-snapshot synthetic capacity unit per order | Authoritative inventory reservations, fencing tokens, real provisioning status and reconciliation after uncertain timeouts |
| Change control | Clearly labelled demo window approval and route-risk gate | Real change calendar in Europe/Istanbul, maintenance freezes, blast-radius policy, CAB/change-ticket links, separation of duties and rollback verification |
| Address correction | Fictional city-specific site IDs | Approved address/coverage inventory, customer confirmation workflow, PII minimization and no unapproved geocoding egress |
| LLM agency | Optional schema-validated live advisor; deterministic guarded execution | Evaluated domain agent tool plans, prompt-injection defenses, explicit tool allowlists, authorization checks, quotas, model change control and human escalation |
| Audit | Hash-linked JSON log and CSV | Append-only/WORM evidence, independently anchored hashes/signatures, controlled retention, clock discipline and access evidence |
| Secrets/network | Managed identity for Azure resources; app secrets for demo access; public endpoints | Private endpoints, egress allowlists, Key Vault or equivalent sovereign store, automatic rotation, scanning and incident response |
| Observability | Health endpoint, structured failures and capped workspace | OTEL trace correlation across systems, SLA alarms, queue age, stuck orders, playbook error budgets and incident runbooks |
| Customer communication | Persisted TR/EN portal simulation | Approved templates, actual localized channels, consent, delivery receipts, retries and accessibility review |
| Metrics | Clearly separated simulated counts and assignment targets | Timestamped representative pilot, cohort definitions, first-pass yield, fallout aging, self-service share and real survey-derived NPS |

## Operational runbook

**Stuck order:** Inspect the order timeline, persisted event status and exception. A hold is an intended policy outcome, not a retryable error. Correct the fictional site, resolve the human decision, or reject. Do not edit the Blob manually to invent capacity or completion.

**Supervisor unhealthy:** `/health` becomes HTTP 503 if no successful supervisor transaction occurs for 60 seconds. Check Container Apps system/console logs, Blob RBAC, managed-identity client ID, storage reachability and snapshot readability. Failed transactions are logged and retried; the service does not acknowledge a mutation that was not saved.

**Storage/RBAC propagation:** Initial managed-identity assignments can take time. Allow the deployment/revision to retry startup, then inspect role scopes. Do not enable shared keys or public Blob access as a workaround. Preserve the prior Blob version before any recovery and verify the hash chain after restoring it.

**Blob conflict:** ETag failures trigger bounded transaction retries. These protect the complete snapshot against lost updates. Simulator effects live in that same snapshot; this does not provide exactly-once effects against real carrier devices. A real adapter needs external idempotency and reconciliation.

**Live AI failure:** The API returns an explicit 502, not a synthetic answer pretending to be AI. Daily limits are counted before inference, including failed attempts. The supervised order remains unchanged; advice is not an approval. Investigate regional model/quota, identity authorization and service health.

**Cost:** Review the dedicated RG's accrued and forecast cost. One replica is intentionally always on. Limit build frequency and monitor log capping. Optional budget notifications are not a kill switch. Stopping/deleting the exact dedicated demo RG is an operator decision, not an automated destructive fallback.

**Credential rotation:** Generate two new independent random values; update Container Apps secrets and restart the revision. Share demo credentials through an approved channel, not a repository, screenshot or public URL. The local credentials file is gitignored but still sensitive; restrict local file permissions. Individual accountability requires replacing shared demo tokens before production.

**Pilot evaluation:** Establish a frozen representative test cohort and pre-register activation-time/fallout definitions. Measure elapsed order-to-activation in days, fallout requiring manual work, autonomous recovery success and partner self-service share. Report NPS only from actual surveys. Include negative cases: address mismatch, capacity exhaustion, duplicate submission, partner timeout, missed change window, cross-partner access, AI failure, malicious instructions, revoked roles and restart during provisioning.
