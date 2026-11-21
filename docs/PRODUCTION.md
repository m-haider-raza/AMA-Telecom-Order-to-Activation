# Production readiness and Turkish telecom deployment

## The residency boundary is a release gate

The assignment requires **all customer data and inference to remain in Türkiye**. A synthetic-only West Europe demonstration was explicitly approved, but that does not waive production obligations. Azure's [geography inventory](https://azure.microsoft.com/explore/global-infrastructure/geographies/) and [data-residency guidance](https://azure.microsoft.com/explore/global-infrastructure/data-residency/) must be rechecked before procurement. No operational Türkiye public region was available for this delivery.

A credible production path is an operator-owned or approved Türkiye-hosted platform for the workflow services, order data, event bus, secrets, logs, backups and inference. Evaluate Azure Local / Arc-enabled infrastructure for management only after verifying which management metadata or telemetry would leave the country. Arc alone is not a data-residency guarantee. A Türkiye-hosted inference server with approved model licensing and controlled egress can replace the optional Azure OpenAI advisor; cloud inference outside Türkiye must remain disabled where the mandate forbids it.

Do not equate EU hosting, encryption or a regional Azure OpenAI deployment with Turkish localization. Regional Azure OpenAI controls inference geography but cannot place inference in a nonexistent Azure public region. Global deployment can route inference outside the selected region; see [deployment types](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/deployment-types).

Legal counsel, the operator's DPO and security/regulatory owners must determine the actual applicable KVKK/BTK obligations, transfer mechanisms, lawful basis, retention and audit-evidence requirements. This repository does not provide a legal compliance determination.

## Azure Local hybrid target shown in the animated film

**This is a proposed production architecture, not infrastructure deployed by this
repository.** The current local-file implementation and synthetic-only West Europe
Container Apps / Azure SQL or Blob / optional Azure OpenAI deployment are unchanged. Microsoft's
[public geography inventory](https://azure.microsoft.com/en-us/explore/global-infrastructure/geographies/),
checked 30 October 2026, does not list a Türkiye public region. This is not a claim
that Microsoft has no physical network or edge facilities in the country.

[Azure Local](https://learn.microsoft.com/en-us/azure/azure-local/overview)
extends Azure infrastructure capabilities to customer-owned sites. Deploy approved
hardware at an operator-owned or approved Turkish datacenter. Run the portal/API,
guarded supervisor, agents and OSS/BSS adapters in supported local VMs or containers.
Provision an appropriate **local** database/event system, customer-facing identity,
secrets management, logs/audit, approved model inference and backup/DR within Türkiye.
These application services do not appear automatically when Azure Local is installed,
nor does every Azure public-cloud service have a local equivalent. A local model
endpoint must replace the demo's foreign Azure OpenAI advisor; model licensing,
capacity, supported hardware, inference quality and operational controls are gates.

The film separates the **customer data plane** from optional foreign cloud services:

| Boundary flow | Proposed external Azure role | Constraint |
|---|---|---|
| Approved images into Türkiye | Azure Container Registry supplies approved application artifacts | No production customer data or secrets in build context, image layers or registries; review registry access records and network metadata, image integrity and supply-chain controls |
| Platform management metadata out | Azure Arc / associated governance | Explicitly assess service-required metadata and control-plane records; do not assume application redaction can filter Arc's own metadata |
| Opt-in aggregate operational metrics out | Azure Monitor | Only legally approved aggregates after re-identification review; no automatic raw logs, order IDs, customer/site identifiers, prompts or traces; this is a design constraint requiring implementation, not a repository capability |
| Customer payloads, prompts, raw logs, backups out | None by default | Block at the application and network boundary; foreign Azure OpenAI and foreign Azure Backup/Storage must not process these under the stated mandate |

[Azure Arc's data/privacy documentation](https://learn.microsoft.com/en-us/azure/azure-arc/servers/security-data-privacy)
states that its Connected Machine agent sends system metadata during normal
operation and cannot opt out of that collection. Connected Azure Local/Arc is
therefore **not** a blanket guarantee that every kind of data stays in Türkiye.
Individual service telemetry, identity, diagnostics, support exports, resource names,
tags and management access must be reviewed as well as workload payloads. Encryption,
private endpoints, pseudonyms and aggregate metrics do not alone establish compliance.

If policy forbids platform metadata leaving too, evaluate
[disconnected operations for Azure Local](https://learn.microsoft.com/en-us/azure/azure-local/manage/disconnected-operations-overview).
This uses a local control plane without a live Azure public-cloud connection.
It requires Microsoft eligibility/access, a qualifying agreement and support,
supported hardware and dedicated management capacity, and an approved operational
model. It is an alternative to connected Arc/Monitor services, **not** a switch
that lets those same live external services keep operating without connectivity.
Approved offline artifact/update import and support procedures require their own
controlled supply-chain process. Neither mode is claimed to be KVKK/BTK certified.

The film's standards references are
[TMF622 product ordering](https://www.tmforum.org/open-digital-architecture/open-apis/product-ordering-management-api-TMF622/v5.0),
[TMF641 service ordering](https://www.tmforum.org/open-digital-architecture/open-apis/service-ordering-management-api-TMF641/v4.1)
and [MCP HTTP/JSON-RPC](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
The repository's simplified v4-path TMF-inspired implementation does not claim the
latest complete schemas or conformance. TMF652 resource ordering and TMF638/639
inventory contracts remain production acceptance work as applicable.

## Close the gaps before touching a carrier

| Area | Demo implementation | Production acceptance gate |
|---|---|---|
| Identity | **Token mode** (the default): two separate demo bearer tokens, and one shared staff token that opens an 8-hour session. **Single sign-on mode** (`AMA_STAFF_AUTH=sso`, see [STAFF-SSO.md](STAFF-SSO.md)): no shared staff token; each person signs in with Microsoft Entra ID, holds one of three app roles, and gets a session of 4 hours by default (12 at most) that names them. Both modes: password accounts for customers and invited partner contacts; stateless signed sessions held per browser tab (sign-out is client-side, so a copied token lives until it expires or a password or the staff token changes); read-only synthetic views public in demo mode | Entra/OIDC identity for customers and partners as well, MFA and conditional access on every account (for staff these come from the tenant that owns the app registration), tenant isolation, role assignment through groups with access reviews, a server-side session store with revocation (today a removed person keeps access until the session ends) and short-lived tokens, and least-privilege operator roles |
| TM Forum | Explicitly documented TMF-inspired DTOs/endpoints | Full versioned TMF622/641/652/638/639 contracts as applicable, OpenAPI validation, conformance tests, subscriptions/hubs and partner contracts |
| OSS/BSS adapters | Shared in-process network/CRM/partner simulators | Authenticated real CRM, inventory, network-domain and field-service adapters with circuit breakers, reconciliation, idempotent side effects and compensating actions |
| Workflow | Single replica, atomic persisted queue and supervisor | Durable distributed workflow/event bus with partitions, outbox/inbox, dead-letter handling, backpressure, HA and tested disaster recovery |
| Resource truth | One in-snapshot synthetic capacity unit per order | Authoritative inventory reservations, fencing tokens, real provisioning status and reconciliation after uncertain timeouts |
| Change control | Clearly labelled demo window approval and route-risk gate | Real change calendar in Europe/Istanbul, maintenance freezes, blast-radius policy, CAB/change-ticket links, separation of duties and rollback verification |
| Address correction | Fictional city-specific site IDs | Approved address/coverage inventory, customer confirmation workflow, PII minimization and no unapproved geocoding egress |
| LLM agency | Optional schema-validated live advisor; deterministic guarded execution | Evaluated domain agent tool plans, prompt-injection defenses, explicit tool allowlists, authorization checks, quotas, model change control and human escalation |
| Audit | Hash-linked JSON log and CSV | Append-only/WORM evidence, independently anchored hashes/signatures, controlled retention, clock discipline and access evidence |
| Secrets/network | Managed identity for Azure resources and no SQL password (Microsoft Entra-only); app secrets for demo access (in single sign-on mode there is no staff token and no client secret: the app proves itself to Entra with its managed identity, and the secrets left are the session-signing key and the partner token); the SQL database and Blob store reachable only through private endpoints in the SQL option; public app endpoint | Private ingress behind a WAF, egress allowlists, Key Vault or equivalent sovereign store, automatic rotation, scanning and incident response |
| Observability | Health endpoint, structured failures and capped workspace | OTEL trace correlation across systems, SLA alarms, queue age, stuck orders, playbook error budgets and incident runbooks |
| Customer communication | Persisted TR/EN portal simulation; e-mail through Azure Communication Services when `AMA_DELIVERY=live`; SMS not connected | Approved templates, actual localized channels, consent, delivery receipts, retries and accessibility review |
| Partner portal | Invite-only partner accounts at `/partner` with password sign-in, per-organisation isolation, expected dates derived from the simulated SLA, pro-forma invoices (20% VAT, payment terms), a concerns inbox and staff-recorded payments; the service team administers it (one shared staff token, or each person's own Microsoft sign-in in single sign-on mode); partner data lives in the single order-state document (a Blob snapshot, or one JSON row in Azure SQL with derived tables; `AMA_MAX_PARTNERS`) | SSO and MFA for partner identities, conditional access and MFA for staff (single sign-on mode already gives named roles and a person-level audit trail), invoices and payment status from the billing/ERP system of record (no money moves here), concerns raised as tickets in the service-desk tool with SLAs and attachments, per-organisation API credentials for the TMF/MCP channel (today one shared token, optionally mapped to a single API-client organisation), a database with per-tenant access controls and data-retention rules, consented e-mail/SMS notifications with delivery receipts, and rate limiting and abuse monitoring behind a WAF |
| Metrics | The Analytics tab: per-agent time, order-to-delivery time and fallout, measured on persisted simulator timelines and a reproducible benchmark and shown against the 7-day and 8% targets; separated from the assignment's baselines | Timestamped representative pilot, cohort definitions, first-pass yield, fallout aging, self-service share and real survey-derived NPS |

## Azure SQL database and the Analytics report

With `enableSql` (`-EnableSql` in `scripts/Deploy.ps1`) all application data lives in **Azure SQL Database** instead of the Blob snapshot. It is a synthetic-data demonstration store, not a production data platform: the middle column is what is built, the right column what must change before a carrier relies on it.

| Area | Demo implementation | Production acceptance gate |
|---|---|---|
| Data model | `ama.StateDocument` holds the whole order state as one JSON row in the same shape as the Blob snapshot, read and written under `UPDLOCK, HOLDLOCK` so replicas cannot overwrite each other. The same transaction refreshes relational tables (`ama.Orders`, `OrderSteps`, `Events`, `AuditLog`, `Messages`, `Customers`, `Partners`, `Concerns`, `Inventory`) from it, and the views `ama.vw_OrderCycle`, `vw_AgentPerformance` and `vw_KpiSummary` report on them. A projection failure rolls back to a savepoint and never blocks the document write | A normalised transactional model with per-aggregate writes, an outbox for events, schema migrations from a reviewed pipeline, and capacity sized from the pilot instead of a single-document write path |
| Network | The template creates the server with `publicNetworkAccess` **Disabled**, so the only path is a private endpoint, with the `privatelink.database.windows.net` zone linked to the Container Apps VNet. The deployed server was observed on 20 November 2026 with public access **Enabled** and four client-IP firewall rules of the kind the Azure portal adds for administration. Closing that is the first item in the hardening checklist of `docs/azure-architecture.md` | Operator access through a jump host, Bastion or VPN with private DNS forwarding; no public path to the server |
| Identity | Microsoft Entra-only authentication, so there are no SQL logins or passwords. The app signs in with its user-assigned managed identity and a fresh token per connection. Its contained database user has `SELECT, INSERT, UPDATE, DELETE` on the `ama` schema only: no DDL, no `db_owner` | An Entra **group** as server administrator with just-in-time elevation, separate migration and runtime identities, and access reviews |
| Bootstrap | The private endpoint also blocks the administrator's own machine, so `python -m app.sqlbootstrap` runs once as a Container Apps **job inside the environment**. `Deploy.ps1` creates it, runs it with the signed-in administrator's short-lived token and deletes it when it passes. It creates the app identity's user and grant, builds the schema, self-tests the store (including a 100-order benchmark) and removes its test data. The app then starts with `AMA_SQL_AUTO_SCHEMA=false` and cannot change the schema | Schema changes from a reviewed release pipeline with approval, not from an operator-run job |
| Move from Blob | When `AMA_SQL_SERVER` and `AMA_SQL_DATABASE` are set and the SQL document is empty, the first start imports the existing Blob or file snapshot once. Afterwards the Blob copy is a stale backup that is never updated | A rehearsed cut-over with reconciliation counts, a freeze window and a tested rollback |
| Protection | TLS 1.2 minimum, transparent data encryption (the service default), Standard S0 (10 DTU, 2 GB), locally redundant backups and the service's default point-in-time restore | Customer-managed keys where required, auditing and threat detection to an in-country sink, in-country zone/geo-redundant backups with a tested restore, retention and legal-hold rules |

The **Analytics** tab (staff) reports on this data. It shows the average time each agent takes (queue wait plus processing; manager and customer holds are reported separately and never charged to an agent), the order-to-delivery time (mean, median and 90th percentile) and the fallout rate, each against the assignment's targets: **35 days to under 7** and **30% fallout to under 8%**. Results from fewer than 30 decided orders are labelled *Indicative*; with no orders the tab says *No data yet*.

*Run benchmark* sends a fixed mix of 50 or 100 synthetic orders through the real agents on a separate benchmark order book, so the report has a reproducible sample without touching live orders. Per 100 orders the mix is 70 straight-through, 23 recovered by the agents' playbooks, 3 waiting for a manager, 2 missing a change window and 2 with an address problem. It keeps the assignment's 30% incident rate and **assumes** the playbooks resolve most of it, so the 7% fallout is a consequence of that assumption rather than an observation. A 100-order run on the deployed app averaged **18.3 seconds** from order to delivery with **7.0%** fallout, and the report marked both targets as met. Read that as "the orchestration layer adds almost no waiting time and the metric pipeline works end to end". It does not measure partner field work, wholesale-partner response or customer delays, which make up the 35 days today, and it is not production KPI evidence; the pilot evaluation below is the gate for that claim.

## Cost model

Two different things are costed. Neither is a quote.

**The demonstration environment** is priced from the Azure Retail Prices API (Sweden Central, USD list price, 13 November 2026), always on, before credits, discounts and tax:

| Component | Basis | USD per month |
|---|---|---|
| Container Apps | One 0.25 vCPU / 0.5 GiB replica, consumption plan, always on (worst case) | 19.71 |
| Azure SQL Database | Standard S0 (10 DTU), 2 GB | 14.72 |
| Private endpoints | Two, for Blob and SQL (7.30 each) | 14.60 |
| Log Analytics | Up to 3 GB a month at the 0.1 GB-a-day cap (2.99 per GB) | 8.97 |
| Registry, private DNS, storage, e-mail, optional AI | Basic registry 5.07, two private DNS zones 1.00, the rest about 1.00 | 7.07 |
| Microsoft Defender for SQL | Free trial on this subscription; list price per server per month afterwards | 15.00 |
| **Estimated total** | About US$65 while the trial lasts | **80.07** |

That is inside the approved US$100 a month target, which remains a target and not a spending cap. For comparison, US$5.81 had actually been billed for 1 to 13 November 2026, before the SQL database and private endpoints appeared in the billing data. Re-run the estimate before relying on it; prices, credits and the Defender trial change.

**The production programme** on Azure Local (the architecture deck's cost slide) is a planning model built on stated assumptions, to be replaced by supplier quotes and the operator's own rates:

| Item | Assumption | Result |
|---|---|---|
| Build | Blended US$10,000 per person-month; Shadow, Assist and Act phases of 3 months with 10, 12 and 14 people; 20% contingency | 108 person-months = US$1.08 million; US$1.296 million with contingency, about TRY 65 million |
| Run | 192 Azure Local physical cores at US$10 per core per month; a 3-person run team at US$10,000 per person-month | US$23,040 plus US$360,000 = about US$383,000 a year |
| Benefit | The assignment's TRY 150 million rework figure, treated as annual, about US$2.985 million at 50.258 TRY per US$ (ECB reference rate, 12 November 2026) | About US$2.60 million net a year; payback about 6 months after go-live |

Left out: the Scale phase, operator hardware, OSS/BSS adapter licences, regulatory and legal work, and change management. The benefit is the assignment's own target, not a result.

## Operational runbook

**Stuck order:** Inspect the order timeline, persisted event status and exception. A hold is an intended policy outcome, not a retryable error. Correct the fictional site, resolve the human decision, or reject. Do not edit the Blob or the SQL state document manually to invent capacity or completion.

**Supervisor unhealthy:** `/health` becomes HTTP 503 if no successful supervisor transaction occurs for 60 seconds. Check Container Apps system/console logs, Blob RBAC, managed-identity client ID, storage reachability and snapshot readability. With Azure SQL, also check that the SQL private endpoint is approved, that `privatelink.database.windows.net` resolves to a private address from the environment, that the app identity's database user exists, and that `AMA_SQL_SERVER` and `AMA_SQL_DATABASE` are both set (setting only one stops the app at start-up). Failed transactions are logged and retried; the service does not acknowledge a mutation that was not saved.

**Storage/RBAC propagation:** Initial managed-identity assignments can take time. Allow the deployment/revision to retry startup, then inspect role scopes. Do not enable shared keys or public Blob access as a workaround. Preserve the prior Blob version before any recovery and verify the hash chain after restoring it.

**Blob conflict:** ETag failures trigger bounded transaction retries. These protect the complete snapshot against lost updates. Simulator effects live in that same snapshot; this does not provide exactly-once effects against real carrier devices. A real adapter needs external idempotency and reconciliation.

**SQL store unavailable or schema missing:** The app logs the failure and retries; `/health` turns 503 after 60 seconds without a successful supervisor transaction. A "schema is missing or out of date" error means the one-off bootstrap has not run or the app is newer than the database: run `python -m app.sqlbootstrap` as the Entra administrator from inside the private network (`Deploy.ps1 -EnableSql` does this with a temporary Container Apps job whose execution history and console logs are in the portal). It is safe to re-run: it only creates what is missing and re-applies the grant, and it writes and then removes self-test data only while the database is still empty (with existing data it runs read-only checks). Do not open public access to the server as a workaround. Before any restore, keep the point-in-time restore window in mind (the default is short) and compare the state document's version with the derived tables.

**Live AI failure:** The API returns an explicit 502, not a synthetic answer pretending to be AI. Daily limits are counted before inference, including failed attempts. The supervised order remains unchanged; advice is not an approval. Investigate regional model/quota, identity authorization and service health.

**Service team cannot sign in (single sign-on):** The login page shows a fixed message for each cause (see *Sign-in messages* in [STAFF-SSO.md](STAFF-SSO.md)); the usual fix is to assign the person a role under the enterprise application's *Users and groups*. A removed person keeps access until their session ends (4 hours by default). To end every staff session at once, replace the `session-secret` Container Apps secret and restart the revision. The console log shows `Sign-in refused`, `Microsoft refused the sign-in` and the `staff-audit` lines; the callback's authorization code and state are never written to it.

**Cost:** Review the dedicated RG's accrued and forecast cost. One replica is intentionally always on, and the Azure SQL database (S0) and its private endpoints are billed hourly even when idle; the estimate is about US$80 a month against the US$100 target (see Cost model). Limit build frequency and monitor log capping. Optional budget notifications are not a kill switch. Stopping/deleting the exact dedicated demo RG is an operator decision, not an automated destructive fallback.

**Credential rotation:** Generate two new independent random values; update Container Apps secrets and restart the revision. Share demo credentials through an approved channel, not a repository, screenshot or public URL. The local credentials file is gitignored but still sensitive; restrict local file permissions. Individual accountability requires replacing shared demo tokens before production; the single sign-on stack already does that for staff, so there only the session secret (rotating it ends every staff session) and the partner token are rotated, and people are managed in Entra.

**Pilot evaluation:** Establish a frozen representative test cohort and pre-register activation-time/fallout definitions. Measure elapsed order-to-activation in days, fallout requiring manual work, autonomous recovery success and partner self-service share. Report NPS only from actual surveys. Include negative cases: address mismatch, capacity exhaustion, duplicate submission, partner timeout, missed change window, cross-partner access, AI failure, malicious instructions, revoked roles and restart during provisioning.
