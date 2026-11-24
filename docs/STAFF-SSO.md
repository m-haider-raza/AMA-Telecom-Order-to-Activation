# Service-team single sign-on

The original application lets the service team in with one shared **access token**. Anyone holding the token is "the
service manager", so the audit trail can say *that* an order was changed but not *who* changed it.

The same code base has a second sign-in mode for the service team: **Microsoft Entra ID single sign-on**. Each person
signs in with their own work account, what they can do depends on a role you assign in Entra, and every change they
make is recorded against their name. Customers are not affected: they still register and sign in with their own
e-mail address and password.

The mode is chosen when the app starts (`AMA_STAFF_AUTH`). The default is `token`, so the existing deployment and its
tests behave exactly as before. Single sign-on is deployed as a **separate, standalone stack** in its own resource
group; it never replaces or modifies the token-based app.

## Foundry with Microsoft sign-in and operator access tokens

For the latest **six hosted agents and isolated trusted boundary**, use
[Foundry dual-login application](FOUNDRY-SSO-APP.md). The optional prompt-agent
advisor deployment below is a different, legacy single-app architecture.

Use `sso+token` to offer Microsoft sign-in first and a staff access token on the same sign-in page for engineers
waiting for their work account. The fallback is a **shared operator credential**, not the old manager token: it
cannot make manager decisions or administer partners. Its audit entries are labeled **Staff access token (shared)**,
not attributed to a person. Both routes retain the same enterprise approval requirement. Share the fallback only
through the service desk and rotate it when access is no longer needed; rotation invalidates existing fallback
sessions without signing out Microsoft users.

Create a [Foundry prompt agent](https://learn.microsoft.com/azure/foundry/agents/quickstarts/prompt-agent) in an
existing Foundry project with your approved model deployment. Do not attach tools or data connections. The agent
provides an advisory diagnosis and bilingual messages from categorical order state only; it never receives customer
details or executes a recommendation. The app continues to run the order workflow and enforce approvals.

Deploy to a **new resource group** so the current apps and data remain untouched:

```powershell
.\scripts\Deploy.ps1 -SubscriptionId <subscription-id> -ResourceGroup rg-ama30-foundry -Location swedencentral `
  -EnableSql -Live -StaffAuth 'sso+token' -StaffManagers 'person@contoso.com' `
  -FoundryProjectEndpoint 'https://<account>.services.ai.azure.com/api/projects/<project>' `
  -FoundryAgentName 'order-advisor'
```

Grant the app's managed identity (`managedIdentityPrincipalId` in `deployment.foundry.local.json`) permission to
invoke the agent at the Foundry project scope, using **Foundry Agent Consumer**. The local, git-ignored
`demo-credentials.foundry.local.json` contains the generated `staffAccessToken`; never commit or publish it.

To bring existing records into the new stack, use the [data copy procedure](#copying-the-data-from-the-token-mode-app)
with the intended source receipt and `-TargetReceipt deployment.foundry.local.json`. Run with `-DryRun` first.
Use a dedicated destination rather than pointing two running apps at the same data store.

## Roles

People get a role through a Microsoft Entra **app role** on the enterprise application. If someone holds more than one,
the highest applies.

| Role (Entra value) | Shown as | What it allows |
|---|---|---|
| `ServiceAuditor` | Service auditor | Read only: orders, the audit trail, the audit export, analytics and the partner list. Changes nothing. |
| `ServiceOperator` | Service operator | Everything an auditor can, plus working orders: placing an order for an enterprise (its contact must approve it before anything starts), correcting an address, asking for AI advice, running the benchmark, answering partner concerns and placing orders for a partner. |
| `ServiceManager` | Service manager | Everything an operator can, plus the decisions that need a manager (approve or reject an exception) and managing partners and their payments. |

A request above a person's role is refused with `403` and a message that names their role. A request with no session
gets `401 Sign in with Microsoft to continue`.

## How sign-in works

1. The service team opens **Service team sign-in** on the login page and chooses **Sign in with Microsoft**.
2. The app redirects to Microsoft Entra ID (OpenID Connect authorization-code flow with PKCE, `select_account`).
3. Microsoft returns the browser to `/auth/sso/callback`. The app checks the tenant, the issuer, the audience, the
   expiry and that the token carries one of the three app roles. Someone from the right tenant with no role is
   refused and the attempt is recorded.
4. The browser lands on `/login#sso`, which exchanges a two-minute, single-use cookie for the staff session of that
   tab. The session carries the person's object ID, user principal name, display name and role.

There is **no client secret**. The app proves its identity to Microsoft with its **managed identity** through a
federated credential on the application registration. By default the enterprise application is set to *assignment
required*, so Microsoft also refuses anyone who has not been assigned (see [Approving the app](#approving-the-app)).
Either way the app itself refuses anyone who holds none of the three roles. In this mode the shared access token is
rejected for every staff call.

The partner token that wholesale systems use for TM Forum TMF622 orders and the MCP endpoint is a machine credential,
not a person. It is unchanged, and its calls are recorded as `wholesale-partner`.

The authorization code comes back in the callback URL (the default query response mode). It works only once and is
useless without the PKCE verifier, which stays in a signed, `HttpOnly` cookie in the browser that began the sign-in.
The app's access log prints the callback path without its query string, so the code and state never reach the logs.
Microsoft's `form_post` mode is not used because it would need that cookie to be `SameSite=None`.

## Deploy the standalone stack

You need Azure CLI signed in (`az login`) with a user account that can create Azure resources and register
applications in the tenant. Everything is created in a **new** resource group, because `Deploy.ps1` refuses an
existing one.

```powershell
.\scripts\Deploy.ps1 -SubscriptionId <subscription-id> -ResourceGroup rg-ama30-sso -Location swedencentral `
  -EnablePrivateStorage -EnableOpenAI -Live -StaffAuth sso -StaffManagers person@contoso.com
```

If script execution is restricted, start it the way your organization allows, for example
`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Deploy.ps1 ...`. Do not weaken a signed-script policy
that your organization enforces.

| Option | Meaning |
|---|---|
| `-Live` | The production-style site: customers register with their own account, and verification e-mail is sent through Azure Communication Services created in the same resource group. Single sign-on requires it. |
| `-StaffAuth sso` | Service team sign in with Microsoft Entra ID. `sso+token` adds the operator fallback; the default `token` keeps the legacy manager token. |
| `-StaffManagers`, `-StaffOperators`, `-StaffAuditors` | User principal names to assign each role. With none named, the signed-in Azure CLI user becomes the manager. |
| `-StaffUserConsent` | Each person approves the app for themselves at their first sign-in, instead of an administrator approving it once. For tenants where no administrator is available. See [Approving the app](#approving-the-app). |
| `-StaffSessionHours` | How long a staff session lasts, 1 to 12 hours (default 4). |
| `-ReceiptPath` | Where the deployment receipt is written. The default is `deployment.sso.local.json`, which never overwrites the receipt of the original app. |

The script then:

1. deploys the resources (below) and builds the image in Azure Container Registry;
2. runs [`scripts/Register-StaffSso.ps1`](../scripts/Register-StaffSso.ps1), which registers the application with its
   three app roles, creates the enterprise application (assignment required, unless you chose `-StaffUserConsent`),
   adds the federated credential for the app's managed identity, makes you its owner and assigns the people you named;
3. starts the app with the single sign-on settings and writes `deployment.sso.local.json` and
   `demo-credentials.sso.local.json` (both ignored by Git). The credentials file holds the app address and the partner
   token only. **No manager token is created for people to use.**

`Register-StaffSso.ps1` is safe to run again. It repairs or extends the registration and assigns people it has not
already assigned. It leaves the approval setting (see [Approving the app](#approving-the-app)) as it is unless you
pass `-AllowUserConsent` or `-RequireAssignment`.

**Resources.** A virtual network with a private Blob endpoint, a Container Apps environment and app, Azure Container
Registry (Basic), storage, Azure OpenAI (`gpt-4.1-mini`), Log Analytics (capped at 0.1 GB a day, 30 days), a managed
identity, and Azure Communication Services with an Azure-managed e-mail domain. There is **no Azure SQL**. Allow
roughly US$15 to 35 a month, and review Cost Management, because the script's budget target is not a spending cap.

## Approving the app

The sign-in asks Microsoft Graph for three basic permissions and nothing else: `openid`, `profile` and
`offline_access` (sign the person in, read their basic profile and keep the session). Somebody has to approve that
once, and Microsoft's rule decides who: **an application that requires assignment must be approved by an
administrator**, even in a tenant that lets people approve apps themselves. Until it is approved, the person stops at
Microsoft's *Need admin approval* page (error `AADSTS90094`), before they reach the app. Choose one way when you
deploy.

| | An administrator approves (default) | Each person approves (`-StaffUserConsent`) |
|---|---|---|
| Enterprise application | *Assignment required* | *Assignment not required* |
| Who can sign in at Microsoft | Only the people you assigned | Anyone in the tenant who approves the app |
| Who can use the console | Only people with a role | Only people with a role. The app refuses everyone else and records `staff.sso-denied` |
| First sign-in | No prompt | Microsoft shows a one-time approval prompt |
| Needs | An administrator who can grant consent | A tenant that lets people approve apps asking for basic permissions (the Microsoft default) |
| Suits | Production | Demonstrations, pilots, and tenants where no administrator is available |

**An administrator approves.** Open the link that `Deploy.ps1` or `Register-StaffSso.ps1` prints, or go to Enterprise
applications > the application > **Permissions**, and choose **Grant admin consent**. Or, as an administrator, run
`az ad app permission admin-consent --id <application-id>`. Then ask people to sign in again.

**Each person approves.** Deploy with `-StaffUserConsent`, or run `Register-StaffSso.ps1` again with
`-AllowUserConsent`, which turns *assignment required* off. The person signs in, reads Microsoft's prompt and accepts.
Access is still granted by the role alone: someone in the tenant who holds no role sees "…has no TürkBizTel service
role" and the attempt goes on the audit trail. Because anyone in the tenant can reach the Microsoft sign-in in this
mode, assign roles carefully and watch the audit trail for `staff.sso-denied`.

To move from one way to the other, run `Register-StaffSso.ps1` again with `-AllowUserConsent` (people approve) or
`-RequireAssignment` (an administrator approves, so approve the app first or sign-in stops at *Need admin approval*).
Without either, the script leaves the setting as it is, so adding people never changes how the app is approved. The
script changes only the *assignment required* setting and never removes an approval that was already given.

## Add, change or remove people

**1. The person needs an account in your tenant.** Someone who already has one skips to step 2 and signs in with its
user principal name, for example `name@yourtenant.onmicrosoft.com`. Someone from another organisation, such as a
partner colleague or a Microsoft employee, is invited as a guest first, and only then does their own e-mail address
work: **Users** > **New user** > **Invite external user**. The tenant's
*External collaboration settings* decide who may invite. Where invitations are limited to administrators and holders of
the *Guest Inviter* role, ask one of them. The invited person's name and e-mail address are what the audit trail
records.

**2. Give them a role.** In the Microsoft Entra admin center open **Enterprise applications**, choose the application
named `TürkBizTel Service Console (<resource group>)`, then **Users and groups**. **Add user/group** assigns a role,
and removing an assignment removes access. You can also assign groups, which suits most teams. The owner of the
application can do this; no administrator role is needed.

**Or with the script.** Run `Register-StaffSso.ps1` again with `-Managers`, `-Operators` or `-Auditors`, naming each
person by user principal name or, for a guest, by the e-mail address they were invited with. It needs the app's address
and managed identity, which you can read from the resource group (first select the subscription with
`az account set --subscription <subscription-id>`):

```powershell
$group = 'rg-ama30-sso'
$app = az containerapp list -g $group --query "[0].properties.configuration.ingress.fqdn" -o tsv
$identity = az identity list -g $group --query "[0].principalId" -o tsv
.\scripts\Register-StaffSso.ps1 -ResourceGroup $group -Origin "https://$app" -IdentityPrincipalId $identity `
  -Operators person@contoso.com
```

**3. They sign in.** They open the app, choose **Sign in with Microsoft** under *Service team?* and sign in. In the
*each person approves* mode they accept Microsoft's one-time prompt first.

Multi-factor authentication, conditional access and sign-in logs all come from your Entra tenant and apply to this
application like any other.

If a user's first sign-in shows *Need admin approval*, see [Approving the app](#approving-the-app).

**When a change takes effect.** A new or changed role applies at the person's next sign-in. A removed person keeps
the access of a session they already hold until it expires (default 4 hours, never more than 12), because the app does
not call Microsoft on every request. To end every session at once, replace the `session-secret` secret on the
container app and restart its revision. That also retires every approval link already e-mailed to an enterprise, which
staff can send again.

## Placing an order for an enterprise

A service person does not type a company name any more. In this mode **New order** opens **Place an order for an
enterprise** and lists every enterprise in the database, so the engineer picks the one the order is for. An enterprise
is a customer account: its company, the contact person and that person's verified e-mail address. The list shows the
address masked, so staff can recognise the contact without collecting addresses, and how many orders the enterprise
already has waiting. An enterprise whose e-mail address is not verified is listed but cannot be chosen, because there
is nobody to ask. Service operators and managers can place orders; auditors cannot.

| Step | What happens |
|---|---|
| 1. The engineer picks the enterprise, the city, the service and the scenario, then selects **Send for enterprise approval** | The order is saved as *Awaiting enterprise*. No agent touches it and nothing is provisioned. |
| 2. The contact is e-mailed | The e-mail, in the language of the enterprise's account, names the company, the order, the service, the city and the engineer, and carries a personal link valid for 7 days. The order also shows as *Needs your approval* in the contact's account. |
| 3. The contact answers | From the link (no sign-in, no password) or from their own account. **Approve** hands the order to the agents, which start at validation, and the order's delivery clock starts then. **Decline** closes it, and nothing is provisioned. |
| 4. The console shows the answer | Who answered and how, by the link or from the account. A declined or withdrawn order shows *Not started*, because no agent worked on it, and Analytics leaves it out so it does not count as fallout. |

**Only the enterprise can approve.** A signed-in staff member can never answer for the enterprise, whatever their role:
the request is refused with `403`. The e-mail goes to the enterprise account's own verified address, never to a contact
detail that staff can edit on the order, and no link or token is ever stored where staff can read it.

**What the link is.** It is signed with the app's session secret and opens `/approve`. The token sits after the `#` in
the address, which browsers never send to a server, so it stays out of access logs and referrer headers. Opening the
link only shows the request. Answering is a separate `POST` that a mail scanner following the link cannot make, and it
works once: opening the link afterwards shows the answer that was given. The page shows the company, the service, the
city and the engineer's display name, and nothing else about the engineer. Sending the e-mail again retires every
earlier link, and replacing the session secret retires all of them.

**If the contact does not answer.** The request stays open for 7 days. After that it is not cancelled for you: it shows
as expired, the contact can no longer answer it, and the order stays *Awaiting enterprise*. Select **Send the e-mail
again** for a new link and a fresh 7 days, or **Withdraw request**, which closes the order and stops the link working.

**Limits.** One resend a minute, at most five e-mails for one order, and at most five orders waiting for one enterprise
at a time, so the service team cannot flood a contact's inbox. The server enforces these, not just the buttons.

| The console says | Meaning and fix |
|---|---|
| "…has not verified its e-mail address yet, so it cannot be asked to approve an order" | The contact has not entered the 6-digit code from sign-up. Ask them to confirm it, then place the order. |
| "…already has 5 orders waiting for its approval" | Ask the contact to answer some, or withdraw one. |
| "The request was sent moments ago. Wait a minute before sending it again." | Resends are one a minute. |
| "The request has already been sent 5 times…" | Withdraw the order, and place it again once the contact is ready. |
| "The approval e-mail could not be delivered…" | The mail provider refused it. Send it again, or ask the contact to open their account, where the order is waiting. |

**What this does not cover.** An order a wholesale partner submits (the partner token, TM Forum TMF622 and MCP) and an
order a customer places in their own account are not placed by service staff for an enterprise, so they need no
approval. In this mode `POST /api/orders` called with a staff session answers `409` and points to
`POST /api/enterprises/{id}/orders`.

## Copying the data from the token-mode app

This stack has its own database, so it starts empty: no enterprises, partners or orders. To start from what the
token-mode app already holds, copy it once. The two apps stay separate: one signs the service team in with the access
token, the other with Microsoft Entra ID, and each keeps its own data.

```powershell
.\scripts\Copy-AppData.ps1 -DryRun   # shows what would be copied, changes nothing
.\scripts\Copy-AppData.ps1           # copies
```

It reads the receipts the deploy scripts leave behind: `deployment.local.json` for the token-mode app (the source,
which is only read) and `deployment.sso.local.json` for this one (the target). The Azure CLI must be signed in with the
right to run commands in both container apps. Both databases are private, so each step runs inside the container of
the app it touches (`az containerapp exec`). The account data passes through the computer running the script, is never
printed or saved, and the output shows counts, never names or e-mail addresses.

| | |
|---|---|
| **Copied** | Enterprise accounts, with the password hash, so an enterprise signs in here with the password it already has. Partner organisations and their contacts, a contact who had set a password keeping it. Every order that has settled: completed, rejected or failed, or waiting for a person (a change-window hold, a manager's decision or the customer's correction), with its timeline, service and resource orders, messages and approvals, and the events and idempotency keys behind it. Partner concerns and their messages. The audit trail. The capacity units the copied orders hold. |
| **Not copied** | Sessions (each app signs its own, so everyone signs in once more), one-time e-mail codes, invitation, approval and tracking links (each is signed with its own app's secret), and the benchmark simulation. The copy sends no e-mail. |
| **Left for a later run** | An order the agents are still working on, one waiting for an enterprise's approval link, and one whose customer the token-mode app is still sending reminders to. It stays where it is, so only one app acts on it. Run the script again once it has settled. |
| **One time, one way** | Nothing is synchronised afterwards. Running the script again brings over what has settled or been created since; a change made to a record that was already copied (a new password, a verified address, a decision on an order) is not carried to the other app. |
| **Safe to repeat** | A record this app already has is left exactly as it is, so a password changed here is never overwritten. An e-mail address or id held here by a *different* account, or a partner name already in use, is reported as a conflict and skipped. Copying stops at the app's limits (`AMA_MAX_ORDERS`, `AMA_MAX_CUSTOMERS`, `AMA_MAX_PARTNERS`) and reports the rest as *full*. |
| **Messages** | A message the token-mode app queued but had not sent is marked `copied-not-sent`: that app sends it, and this one never sends it a second time. Messages already sent or failed there are history and are not retried. |
| **Audit trail** | The token-mode trail is added to the end of this app's own and signed again, so this chain stays intact. Each copied entry keeps its time, actor, action, detail, person and changes, and says which entry of the original it was. One `data.copied` entry records the copy and where the original chain ended. A token-mode chain that does not verify is not copied. |

An enterprise whose e-mail address was not verified in the token-mode app is copied as unverified. It is listed in
**Place an order for an enterprise** but cannot be chosen until its contact signs in here and confirms the address
(**Send a new code**, then **Confirm email**).

**A partner contact who had not yet set a password** is copied without one, because an invitation link cannot move.
The report counts them. Open the partner in the console and select **Send a new invitation** for each.

**Pick one app to act on a copied waiting order.** Both apps now hold it. If someone acts on it here (staff approve,
reject or release it, or the customer corrects it), the app carries on and e-mails the contact, as it does for any
order, and the token-mode app does not know.

**Approval e-mails go to the real address of each copied account.** Place test orders only for enterprises whose
mailbox you own.

`az containerapp exec` refuses a command of more than about 1,800 characters, so the script sends its helper and each
batch in small pieces to a private folder of the container, runs it there and deletes the folder afterwards. Nothing is
written to either database until the whole batch has arrived and checked out, and the import itself is a single save.
It takes a while: when Azure answers *429 Too Many Requests* (it limits how often exec sessions may be opened) the
script waits and tries again by itself, so allow half an hour or more.

## Who did what

Every staff action that changes anything is recorded with the person, not just a role.

| Recorded | Where |
|---|---|
| Sign in (`staff.signed-in`), sign out (`staff.signed-out`) and a refused sign-in by someone in the tenant with no role (`staff.sso-denied`) | Audit trail, subject "Staff session" |
| Every order change: who (name, user principal name, role), what, and for field-level edits the old and new value | Audit trail, **What changed** column |
| An order placed for an enterprise (`order.approval-requested`), sent again (`order.approval-resent`) or withdrawn (`order.approval-withdrawn`) | Audit trail, under the person who did it |
| The enterprise's answer (`order.enterprise-approved`, `order.enterprise-declined`), with whether it came from the link or the account | Audit trail, under `enterprise-contact` |

Ways to read it:

- **Console.** *Audit trail* has a **Done by** filter (people), an **Action** filter and an order search.
  **Export authorized audit CSV** (further down the page) exports everything.
- **API.** `GET /api/audit` (any staff role, including auditor) takes `person` (user principal name or object ID),
  `order`, `action` (a prefix) and `limit` (1 to 500, default 100). It returns the entries, newest first, the list
  of people with their action counts, and `verified`, which is the result of checking the hash chain. The chain
  detects simple edits; it is not immutable regulatory evidence or a digital signature.
  `GET /api/audit.csv` has the columns `timestamp, order, actor, action, detail, done_by, upn, role, changes`.
- **Logs.** Each person-attributed entry is also written to the container's standard output as one JSON line, after the
  change has been saved, which gives a second copy outside the application state:

  ```kusto
  ContainerAppConsoleLogs_CL
  | where Log_s startswith '{"log":"staff-audit"'
  | extend entry = parse_json(Log_s)
  | project TimeGenerated, person = tostring(entry.by.upn), role = tostring(entry.by.role),
            action = tostring(entry.action), orderId = tostring(entry.orderId), changes = entry.changes
  ```

Entries made by the agents, by customers, by an enterprise's contact and by the partner token keep their plain actor name.

## Settings the app reads

The deployment sets these. They matter if you run the app somewhere else. The app **refuses to start** in single
sign-on mode when any is missing or malformed, so a half-configured site never serves the console.

| Setting | Meaning |
|---|---|
| `AMA_STAFF_AUTH` | `token` (default), `sso` (Entra only), or `sso+token` (Entra plus the operator fallback). |
| `AMA_STAFF_ACCESS_TOKEN` | Required for `sso+token`: a distinct random secret of at least 32 characters. |
| `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME` | Configure the Foundry advisor. Set both; it takes precedence over the legacy Azure OpenAI advisor. |
| `AMA_SSO_TENANT_ID`, `AMA_SSO_CLIENT_ID` | Directory and application IDs (GUIDs). |
| `AMA_SESSION_SECRET` | At least 32 characters. Signs sessions. |
| `AZURE_CLIENT_ID` | The managed identity that proves the application's identity to Microsoft. For local development only, `AMA_SSO_CLIENT_SECRET` can be used instead. |
| `AMA_PUBLIC_ORIGIN` | The site's address. Must be `https`, or `http` on localhost. The redirect address is `<origin>/auth/sso/callback`. |
| `AMA_STAFF_SESSION_HOURS` | Session length, 0.25 to 12 (default 4). |
| `AMA_DELIVERY=live` | Needed to send customers their verification codes and the e-mail that asks an enterprise to approve an order. |

## Sign-in messages

| The person sees | Meaning and fix |
|---|---|
| "…has not been given access…" or "…has no TürkBizTel service role" | No role assigned. Assign one under **Users and groups**, then sign in again. |
| "This username may be incorrect. Make sure you typed it correctly" on Microsoft's own sign-in page | The account does not exist in your tenant, so Microsoft stops before the app is reached. Someone from another organisation, a Microsoft employee for example, must first be invited as a guest and then given a role; see [Add, change or remove people](#add-change-or-remove-people). Retyping the address does not help. If the person already has an account in your tenant, they sign in with that account's user principal name instead. |
| "Need admin approval" on a Microsoft page (`AADSTS90094`) | The enterprise application requires assignment and no administrator has approved it. An administrator approves it once, or you switch to `-StaffUserConsent`. See [Approving the app](#approving-the-app). |
| "Your organisation has not allowed this app yet" | Consent is required. An administrator approves the application, as above. |
| "That account belongs to a different organisation" | They signed in with an account from another tenant. |
| "That sign-in took too long, or it was started in a different browser" | The sign-in lasts 10 minutes and must finish in the browser that started it. |
| "Too many sign-in attempts" | Wait a few minutes. |

Free text from Microsoft is never shown to the person.

## Limitations

- **Revocation is not instant.** See *When a change takes effect* above.
- **Azure SQL keeps the original audit columns.** When the optional SQL store is used, its reporting table `AuditLog`
  has no person or change columns. The full entry, with the person, is in the hash-chained audit that is part of the
  stored application state, and in the log copy. The standalone SSO stack does not use SQL.
- **One tenant.** Only accounts from the tenant that owns the registration can sign in. People from other
  organisations sign in as guests invited to that tenant, which needs someone who may invite guests there.
- **One replica.** The single-use hand-off and the sign-in throttles are held in the app's memory, so the template
  pins one replica. Running more needs those moved to shared storage first.
- **An enterprise is one customer account.** The point of contact is the person on that account, and the request goes
  to their verified address. There is no separate contact field or distribution list, so a company with several
  people who each registered appears once per account.
- **Data is copied, not shared.** The token-mode app and this one keep separate databases. The copy described above is
  a one-time, one-way snapshot: later changes to a record that was already copied (a new password, a verified address,
  a decision on an order) do not follow it, and the script never overwrites a record this app already has.
- **Approval requests do not cancel themselves.** After 7 days a request shows as expired and waits for the service
  team to send it again or withdraw it. Nothing closes it automatically.
- **The approval e-mail needs the live mail path.** Without `AMA_DELIVERY=live` nothing is sent: the console says so,
  and the contact can still answer from their account.
- **A demonstration.** The data is synthetic and the Türkiye data-residency gap described in
  [PRODUCTION.md](PRODUCTION.md) still applies.

## Remove it

```powershell
.\scripts\Remove-Demo.ps1 -SubscriptionId <subscription-id> -ResourceGroup rg-ama30-sso `
  -ConfirmResourceGroup rg-ama30-sso -RemoveStaffSso
```

This deletes the resource group and the Entra application. Microsoft Entra keeps a deleted registration for 30 days
and can restore it. To remove only the registration, run
`Register-StaffSso.ps1 -ResourceGroup rg-ama30-sso -Remove`.
