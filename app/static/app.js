"use strict";
let snapshot = null, selectedOrder = null, token = "", polling = false, errorShown = false, pollTimer = 0;
let session = {authenticated: false, mode: "demo"};
const NOTICE_KEY = "myturkei-notice";
const SESSION_KEY = "myturkei-session";
const HOME = {admin: "/console", partner: "/partner", customer: "/account"};
const $ = (selector) => document.querySelector(selector);
const productNames = {connectivity: "Dedicated Internet", sdwan: "Managed SD-WAN", cloud: "Cloud Connect"};
const stateNames = {acknowledged: "Accepted", inProgress: "In progress", pendingApproval: "Needs approval", held: "Window hold", awaitingCustomer: "Confirm site", awaitingEnterpriseApproval: "Awaiting enterprise", completed: "Active", rejected: "Rejected", failed: "Failed"};
/* An order the service team places for an enterprise waits for that enterprise's contact; no agent starts before then. */
const waitingForEnterprise = order => order.state === "awaitingEnterpriseApproval";
const answeredNo = {declined: "Declined by enterprise", withdrawn: "Withdrawn"};
const stateLabel = order => (order.state === "rejected" && answeredNo[order.enterpriseApproval?.status]) || stateNames[order.state] || order.state;
/* Declined and withdrawn requests were closed before any agent started. */
const neverStarted = order => waitingForEnterprise(order) || Boolean(answeredNo[order.enterpriseApproval?.status]);
const stageLabel = order => neverStarted(order) ? "Not started" : order.stage.replaceAll("_", " ");
const channelLabel = order => order.actor === "wholesale-partner" ? "MCP partner" : order.enterpriseApproval ? "On behalf of enterprise" : "Operator API";
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}
function append(parent, ...children) { children.forEach(child => parent.append(child)); return parent; }
function toast(message, error = false) {
  const box = $("#toast"); box.textContent = message; box.className = `toast${error ? " error-toast" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => box.classList.add("hidden"), 6000);
}
/* This tab's own sign-in: the session lives in sessionStorage, so another tab or window can be signed in as somebody else. */
function tabHeaders() {
  let stored = "";
  try { stored = sessionStorage.getItem(SESSION_KEY) || ""; } catch { /* Storage is blocked: this tab cannot stay signed in. */ }
  return {"X-Session-Mode": "tab", ...(stored ? {"X-Session": stored} : {})};
}
async function api(path, options = {}) {
  const headers = {"X-Requested-With": "turkbiztel", ...tabHeaders(), ...(options.body ? {"Content-Type": "application/json"} : {}), ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* Keep the HTTP failure visible when no JSON body is available. */ }
    throw Object.assign(new Error(detail), {status: response.status});
  }
  return response.status === 204 ? null : response.json();
}
/* A service-team member signed in with Microsoft holds one Entra app role. The server enforces it on every request;
   this only shapes the screen, so people are not offered buttons that would be refused. */
const ROLE_RANK = {ServiceAuditor: 1, ServiceOperator: 2, ServiceManager: 3};
const ROLE_NAMES = {ServiceManager: "Service manager", ServiceOperator: "Service operator", ServiceAuditor: "Service auditor"};
const NEED_RANK = {read: 1, operate: 2, manage: 3};
const NEED_TEXT = {operate: "change orders", manage: "make this decision or manage partners"};
function can(need) { return !session.staff || (ROLE_RANK[session.staff.role] || 0) >= NEED_RANK[need]; }
function roleNote(need) {
  return can(need) ? null : node("p", `Your role (${session.staff.roleLabel}) cannot ${NEED_TEXT[need]}. Ask a service manager.`, "muted small");
}
function requireToken(need = "operate") {
  if (!token && session.role !== "admin") {
    if (staffSso()) { leaveConsole("Sign in as staff to continue."); return false; }
    $("#access-dialog").showModal(); toast("Sign in as staff or enter your demo token, then retry the action."); return false;
  }
  if (!can(need)) { toast(`Your role (${session.staff.roleLabel}) cannot ${NEED_TEXT[need]}. Ask a service manager.`, true); return false; }
  return true;
}
function showPage(page) {
  document.querySelectorAll(".page").forEach(section => section.classList.add("hidden"));
  $(`#${page}-page`).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(button => button.classList.toggle("active", button.dataset.page === page));
  const agent = AGENTS.find(item => `agent-${item.stage}` === page);
  $("#page-title").textContent = agent ? `${agent.short} agent` : {overview: "Activation control tower", orders: "Order workspace", exceptions: "Exception desk", partners: "Wholesale partners", analytics: "Analytics", governance: "Trust & governance"}[page];
}
const AGENTS = [
  {stage: "validation", short: "Validate", name: "Order Validation Agent", role: "Checks the site address, feasibility and inventory before anything is built."},
  {stage: "decomposition", short: "Decompose", name: "Order Decomposition Agent", role: "Turns the commercial order into service and resource orders (TMF622 → TMF641)."},
  {stage: "provisioning", short: "Provision", name: "Provisioning Orchestration Agent", role: "Reserves partner capacity and activates the service inside change windows."},
  {stage: "fallout", short: "Recover", name: "Fallout Resolution Agent", role: "Diagnoses failures and runs allow-listed, bounded recovery playbooks."},
  {stage: "customer_update", short: "Communicate", name: "Customer Update Agent", role: "Sends proactive updates and reminders to customers by email or SMS."},
];
function buildAgentPages() {
  const host = $("#agent-pages");
  AGENTS.forEach(agent => {
    const section = node("section", null, "page hidden"); section.id = `agent-${agent.stage}-page`;
    const heading = append(node("div", null, "section-heading"), append(node("div"), node("span", "AGENT WORKSPACE", "eyebrow"), node("h2", agent.name), node("p", agent.role, "muted")), node("span", "Connecting", "pill live-pill"));
    const stats = node("div", null, "metrics"); stats.id = `agent-${agent.stage}-stats`;
    const now = append(node("div", null, "section-heading"), append(node("div"), node("h2", "Working on now")));
    const current = node("div", null, "card"); current.id = `agent-${agent.stage}-current`;
    const feedHead = append(node("div", null, "section-heading"), append(node("div"), node("h2", "Live activity"), node("p", "Newest first. Updates every 3 seconds.", "muted small")));
    const feed = node("div", null, "card"); feed.id = `agent-${agent.stage}-feed`;
    append(section, heading, stats, now, current, feedHead, feed); host.append(section);
  });
}
function agentActivity(agent) {
  const entries = [];
  Object.values(snapshot.orders).forEach(order => (order.timeline || []).forEach(entry => {
    if (entry.agent === agent.name) entries.push({order, entry});
  }));
  return entries.sort((a, b) => b.entry.at.localeCompare(a.entry.at));
}
function agentCurrent(agent) {
  const open = order => !["completed", "rejected", "failed", "cancelled", "awaitingEnterpriseApproval"].includes(order.state);
  const orders = Object.values(snapshot.orders).filter(open);
  if (agent.stage === "customer_update") {
    return orders.filter(order => order.stage === "customer_update" || order.state === "awaitingCustomer" || (order.outbox || []).some(item => item.status === "pending-delivery"));
  }
  return orders.filter(order => order.stage === agent.stage);
}
function agentStatus(order) {
  if (["held", "pendingApproval"].includes(order.state)) return "Waiting for manager decision";
  if (order.state === "awaitingCustomer") return "Waiting for customer to confirm the site";
  return "Processing";
}
function renderAgents() {
  AGENTS.forEach(agent => {
    const current = agentCurrent(agent), activity = agentActivity(agent);
    const busy = current.filter(order => agentStatus(order) === "Processing").length;
    const failures = activity.filter(item => item.entry.result !== "success").length;
    const last = activity[0];
    const pill = document.querySelector(`#agent-${agent.stage}-page .live-pill`);
    pill.textContent = busy ? `● Working on ${busy}` : current.length ? "● Waiting" : "○ Idle";
    pill.className = `pill live-pill ${busy ? "light" : current.length ? "amber" : ""}`;
    const stats = $(`#agent-${agent.stage}-stats`); stats.replaceChildren();
    [["In queue now", current.length, "Orders currently with this agent"], ["Actions taken", activity.length, "Recorded in the audit trail"], ["Needs a human", failures, "Holds and human-required results"], ["Last action", last ? new Date(last.entry.at).toLocaleTimeString() : "–", last ? last.entry.tool : "No activity yet"]]
      .forEach(([label, value, foot]) => stats.append(append(node("article", null, "metric"), node("div", label, "metric-label"), node("div", value, "metric-value"), node("div", foot, "metric-foot"))));
    const host = $(`#agent-${agent.stage}-current`); host.replaceChildren();
    if (!current.length) host.append(node("div", "Nothing in progress. This agent is idle.", "empty"));
    else {
      const table = node("table"), head = node("tr"), body = node("tbody");
      ["Enterprise / order", "Service", "Location", "Agent status", "Latest step"].forEach(title => head.append(node("th", title)));
      current.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).forEach(order => {
        const row = node("tr", null, "order-row"); row.tabIndex = 0;
        const company = append(node("td"), node("span", order.customer, "order-name"), node("span", order.id, "order-id"));
        const latest = (order.timeline || []).at(-1);
        append(row, company, node("td", productNames[order.product] || order.product), node("td", order.city), append(node("td"), node("span", agentStatus(order), `state ${order.state}`)), node("td", latest ? `${latest.tool}` : "–"));
        row.addEventListener("click", () => openOrder(order.id));
        row.addEventListener("keydown", event => { if (event.key === "Enter") openOrder(order.id); });
        body.append(row);
      });
      append(table, append(node("thead"), head), body); host.append(table);
    }
    const feed = $(`#agent-${agent.stage}-feed`); feed.replaceChildren();
    if (!activity.length) feed.append(node("div", "No activity recorded yet. Submit an order to see this agent work.", "empty"));
    else {
      const table = node("table"), head = node("tr"), body = node("tbody");
      ["Time", "Order", "Tool", "What happened", "Result"].forEach(title => head.append(node("th", title)));
      activity.slice(0, 25).forEach(({order, entry}) => {
        const row = node("tr", null, "order-row");
        append(row, node("td", new Date(entry.at).toLocaleTimeString()), node("td", order.id), node("td", entry.tool), node("td", entry.detail), append(node("td"), node("span", entry.result, `state ${entry.result === "success" ? "" : "held"}`)));
        row.addEventListener("click", () => openOrder(order.id));
        body.append(row);
      });
      append(table, append(node("thead"), head), body); feed.append(table);
    }
  });
}
function renderTable(target, orders) {
  const host = $(target); host.replaceChildren();
  if (!orders.length) { host.append(node("div", "No matching synthetic orders.", "empty")); return; }
  const table = node("table"), header = node("tr"), head = node("thead"), body = node("tbody");
  ["Enterprise / order", "Service", "Location", "Agent stage", "Status", "Channel"].forEach(title => header.append(node("th", title)));
  head.append(header);
  orders.forEach(order => {
    const row = node("tr", null, "order-row"); row.tabIndex = 0; row.setAttribute("aria-label", `View ${order.id}`);
    const company = node("td"); append(company, node("span", order.customer, "order-name"), node("span", order.id, "order-id"));
    append(row, company, node("td", productNames[order.product] || order.product), node("td", order.city), node("td", stageLabel(order)), append(node("td"), node("span", stateLabel(order), `state ${order.state}`)), node("td", channelLabel(order)));
    row.addEventListener("click", () => openOrder(order.id));
    row.addEventListener("keydown", event => { if (event.key === "Enter") openOrder(order.id); });
    body.append(row);
  });
  append(table, head, body); host.append(table);
}
function render() {
  const orders = Object.values(snapshot.orders).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const metrics = snapshot.metrics, exceptions = orders.filter(order => ["held", "pendingApproval"].includes(order.state));
  const asked = orders.filter(waitingForEnterprise).length;
  $("#metrics").replaceChildren();
  [
    ["Orders in workspace", metrics.orders, asked ? `${asked} awaiting an enterprise's approval` : "Synthetic enterprise service requests"],
    ["Services activated", metrics.completed, "Simulated end-to-end completions"],
    ["Manager attention", metrics.exceptions, "Governed exception & change-window holds"],
    ["Autonomous recoveries", metrics.recovered, "Allowlisted one-retry playbook"],
  ].forEach(([label, value, foot]) => $("#metrics").append(append(node("article", null, "metric"), node("div", label, "metric-label"), node("div", value, "metric-value"), node("div", foot, "metric-foot"))));
  $("#exception-count").textContent = exceptions.length;
  $("#ai-mode").textContent = snapshot.configuration.aiMode;
  $("#deployment-region").textContent = `${snapshot.configuration.region.toUpperCase()} / DEMO ENVIRONMENT`;
  renderTable("#recent-orders", orders.slice(0, 6)); renderTable("#all-orders", orders);
  const exceptionList = $("#exception-list"); exceptionList.replaceChildren();
  if (!exceptions.length) exceptionList.append(node("div", "No open policy holds. Launch a route-conflict or change-window scenario to demonstrate human review.", "card empty"));
  exceptions.forEach(order => {
    const button = node("button", "Review exception →", "button secondary"); button.onclick = () => openOrder(order.id);
    exceptionList.append(append(node("article", null, "card exception-card"), append(node("div"), node("span", order.id, "eyebrow"), node("h3", order.customer), node("p", order.exception?.detail || "Manager review required")), button));
  });
  const named = snapshot.audit.filter(entry => entry.by).length;
  $("#audit-state").textContent = metrics.auditChainValid ? `Hash chain verified · ${snapshot.audit.length} entries${named ? ` · ${named} by named staff` : ""} · not immutable evidence` : "WARNING: audit hash chain verification failed";
  const provenance = $("#provenance"); provenance.replaceChildren();
  snapshot.dataProvenance.forEach(source => {
    const item = node("div");
    if (source.url && source.url.startsWith("https://www.wikidata.org/")) {
      const link = node("a", `${source.name} · ${source.license}`, "source-link"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; item.append(link);
    } else item.append(node("p", `${source.name} · ${source.license}`, "small"));
    provenance.append(item);
  });
  renderAudit();
  renderAgents();
  if (selectedOrder) renderOrder(selectedOrder);
}
/* The audit trail. An entry made by a signed-in service-team member names the person (`by`) and lists what the
   action altered (`changes`); entries made by agents, customers and partners keep the plain actor. */
const AUDIT_ROWS = 100;
const STAFF_SESSION = /^staff-[0-9a-f]{32}$/;
function fillSelect(select, first, entries) {
  const mark = JSON.stringify(entries), keep = select.value;
  if (select.dataset.mark === mark) return;
  select.dataset.mark = mark;
  select.replaceChildren(...[["", first], ...entries].map(([value, text]) => { const option = node("option", text); option.value = value; return option; }));
  select.value = entries.some(([value]) => value === keep) ? keep : "";
}
const plain = value => value === null || value === undefined || value === "" ? "–" : typeof value === "string" ? value : JSON.stringify(value);
function auditRow(entry) {
  const by = entry.by;
  const who = by
    ? append(node("td"), node("span", by.name, "order-name"), node("span", `${by.upn} · ${ROLE_NAMES[by.role] || "Staff"}`, "order-id"))
    : node("td", entry.actor === "service-manager" ? "Service manager (shared token, no name)" : entry.actor, "audit-actor");
  const changes = node("td", null, "audit-changes");
  (entry.changes || []).forEach(change => changes.append(append(node("div", null, "audit-change"),
    node("span", change.field, "audit-field"), node("span", plain(change.from), "audit-from"),
    node("span", "→", "audit-arrow"), node("span", plain(change.to), "audit-to"))));
  if (!changes.firstChild) changes.append(node("span", "–", "muted"));
  return append(node("tr"),
    node("td", new Date(entry.at).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit"}), "audit-time"),
    node("td", STAFF_SESSION.test(entry.orderId) ? "Staff session" : entry.orderId), who,
    append(node("td"), node("span", entry.action, "order-name"), node("span", entry.detail, "order-id")), changes);
}
function renderAudit() {
  const entries = snapshot.audit, people = new Map();
  entries.forEach(entry => { if (entry.by) people.set(entry.by.oid, entry.by); });
  const person = $("#audit-person"), action = $("#audit-action");
  $("#audit-person-field").classList.toggle("hidden", !people.size);
  fillSelect(person, "Everyone", [...people].map(([oid, by]) => [oid, `${by.name} · ${ROLE_NAMES[by.role] || "Staff"}`]).sort((a, b) => a[1].localeCompare(b[1])));
  fillSelect(action, "All actions", [...new Set(entries.map(entry => entry.action))].sort().map(name => [name, name]));
  const reference = $("#audit-order").value.trim().toLowerCase();
  const found = entries.filter(entry => (!person.value || (entry.by && entry.by.oid === person.value)) && (!action.value || entry.action === action.value)
    && (!reference || entry.orderId.toLowerCase().includes(reference)));
  const host = $("#audit-table"); host.replaceChildren();
  if (!found.length) { host.append(node("div", entries.length ? "No audit entries match these filters." : "No audit entries yet.", "empty")); return; }
  const table = node("table"), head = node("tr"), body = node("tbody");
  ["Time", "Order / subject", "Done by", "Action", "What changed"].forEach(title => head.append(node("th", title)));
  found.slice(-AUDIT_ROWS).reverse().forEach(entry => body.append(auditRow(entry)));
  append(table, append(node("thead"), head), body); host.append(table);
  if (found.length > AUDIT_ROWS) host.append(node("p", `Showing the latest ${AUDIT_ROWS} of ${found.length} matching entries. Export the CSV for all of them.`, "muted small pad"));
}
function openOrder(id) { selectedOrder = id; renderOrder(id); $("#order-drawer").classList.remove("hidden"); $("#drawer-backdrop").classList.remove("hidden"); }
function closeOrder() { selectedOrder = null; $("#order-drawer").classList.add("hidden"); $("#drawer-backdrop").classList.add("hidden"); }
const DELIVERY_NOTE = {
  pending: "Sending the approval e-mail…",
  sent: "The approval e-mail was sent to the enterprise's contact.",
  failed: "The approval e-mail could not be delivered. Send it again, or ask the contact to sign in and approve in their account.",
  unavailable: "E-mail delivery isn't configured here, so nothing was sent. The contact can approve after signing in to their account.",
  simulated: "Demo mode: the e-mail is simulated and nothing was sent. The contact can approve in their account.",
};
const ANSWERED_VIA = {link: "from the link in the e-mail", account: "in their account"};
/* Who has to answer, until when, and what the service team can still do about it. */
function approvalBlock(order) {
  const record = order.enterpriseApproval, waiting = record.status === "pending";
  const expired = waiting && new Date(record.expiresAt) <= new Date();
  const when = value => new Date(value).toLocaleString();
  const contact = order.contact?.contactName || "the enterprise's contact";
  const requester = record.requestedBy?.name || record.requestedBy?.upn || "The service team";
  const answered = `${when(record.decidedAt)}${ANSWERED_VIA[record.via] ? ` ${ANSWERED_VIA[record.via]}` : ""}`;
  const title = expired ? "The approval request has expired" : {
    pending: `Waiting for ${contact} to approve`, approved: `${contact} approved this order`,
    declined: `${contact} declined this order`, withdrawn: "The service team withdrew this request"}[record.status] || record.status;
  const lines = [`Placed by ${requester} on ${when(record.requestedAt)}.`];
  if (waiting) {
    lines.push(expired
      ? `The request ran out on ${when(record.expiresAt)}. Send it again to give ${contact} a new link, or withdraw it.`
      : `Nothing is provisioned until ${contact} approves. The request is open until ${when(record.expiresAt)}.`);
    lines.push(`${DELIVERY_NOTE[record.delivery?.status] || ""}${record.sends > 1 ? ` Sent ${record.sends} times so far.` : ""}`.trim());
  } else if (record.status === "approved") lines.push(`Approved on ${answered}. The agents started right after.`);
  else if (record.status === "declined") lines.push(`Declined on ${answered}. Nothing was provisioned.`);
  else lines.push(`Withdrawn on ${when(record.decidedAt)}. Nothing was provisioned.`);
  const box = append(node("div", null, `approval-box ${expired ? "expired" : record.status}`), node("strong", title));
  lines.filter(Boolean).forEach(line => box.append(node("p", line)));
  if (!waiting) return box;
  const actions = node("div", null, "approval-actions"), resend = node("button", session.isolatedArchitecture && !session.delivery ? "Renew approval request" : "Send the e-mail again", "button secondary"), withdraw = node("button", "Withdraw request", "button secondary");
  resend.disabled = withdraw.disabled = !can("operate");
  resend.onclick = async () => {
    if (!requireToken()) return;
    resend.disabled = true;
    try { await api(`/api/orders/${order.id}/approval/resend`, {method: "POST"}); toast(session.isolatedArchitecture && !session.delivery ? `Approval request renewed for ${contact}; no email was sent.` : `Sending a new approval e-mail to ${contact}.`); await refresh(); }
    catch (error) { toast(error.message, true); } finally { resend.disabled = false; }
  };
  withdraw.onclick = async () => {
    if (!requireToken()) return;
    if (!confirm(`Withdraw the request for ${order.id}? The order is closed, nothing is provisioned, and the link in the e-mail stops working. This is recorded in the audit trail.`)) return;
    withdraw.disabled = true;
    try { await api(`/api/orders/${order.id}/approval/withdraw`, {method: "POST"}); toast(`Order ${order.id} withdrawn. Nothing was provisioned.`); await refresh(); }
    catch (error) { toast(error.message, true); } finally { withdraw.disabled = false; }
  };
  append(actions, resend, withdraw); box.append(actions);
  const note = roleNote("operate"); if (note) box.append(note);
  return box;
}
function renderOrder(id) {
  const order = snapshot.orders[id]; if (!order) return;
  const host = $("#order-detail"); host.replaceChildren();
  append(host, node("h2", order.customer), node("div", `${order.id} · ${productNames[order.product]} · ${order.city}`, "detail-subtitle"), node("span", stateLabel(order), `state ${order.state}`));
  const grid = node("div", null, "detail-grid");
  [["Scenario", order.scenario], ["Active stage", neverStarted(order) ? "Not started" : order.stage], ["Service orders", order.serviceOrders.length], ["Resource orders", order.resourceOrders.length], ["Deadline (demo contract)", new Date(order.deadline).toLocaleString()], ["Execution", "Network / partner simulator"]].forEach(([label, value]) => grid.append(append(node("div", null, "detail-cell"), node("small", label), node("div", value))));
  host.append(grid);
  if (order.enterpriseApproval) host.append(approvalBlock(order));
  if (order.exception) {
    const warning = append(node("div", null, "warning"), node("strong", "Policy hold · explicit human decision"), node("p", order.exception.detail));
    if (order.state === "awaitingCustomer") {
      const button = node("button", `Confirm demo site ${order.exception.expectedSiteReference}`, "button secondary");
      button.disabled = !can("operate");
      button.onclick = async () => {
        if (!requireToken()) return;
        try { await api(`/api/orders/${id}/address`, {method: "POST", body: JSON.stringify({siteReference: order.exception.expectedSiteReference})}); toast("Synthetic site confirmed by customer/operator."); await refresh(); }
        catch (error) { toast(error.message, true); }
      };
      warning.append(button);
      const note = roleNote("operate"); if (note) warning.append(note);
    } else {
      [["approve", "Approve simulated change"], ["reject", "Reject order"]].forEach(([decision, label]) => {
        const button = node("button", label, "button secondary");
        button.disabled = !can("manage");
        button.onclick = async () => {
          if (!requireToken("manage")) return;
          if (!confirm(`${label} for ${order.id}? This decision is audited and affects simulated resources only.`)) return;
          button.disabled = true;
          try { await api(`/api/orders/${id}/decision`, {method: "POST", body: JSON.stringify({decision})}); toast("Manager decision recorded."); await refresh(); }
          catch (error) { toast(error.message, true); } finally { button.disabled = false; }
        }; warning.append(button);
      });
      const note = roleNote("manage"); if (note) warning.append(note);
    }
    host.append(warning);
  }
  append(host, node("h3", "Agent execution timeline"));
  const timeline = node("div", null, "timeline");
  if (!order.timeline.length) timeline.append(node("p", "Queued for the durable order supervisor.", "muted"));
  order.timeline.forEach(item => timeline.append(append(node("article", null, "timeline-item"), node("h3", item.agent), node("code", item.tool), node("p", item.detail), node("time", `${new Date(item.at).toLocaleTimeString()} · ${item.result} · simulation`))));
  host.append(timeline);
  append(host, node("h3", "Customer updates / Müşteri bildirimleri"));
  order.notifications.forEach(message => host.append(append(node("div", null, "message"), node("span", "ENGLISH", "language"), node("div", message.en), node("span", "TÜRKÇE", "language"), node("div", message.tr), node("p", "Portal update · see the outbound message status below for email or SMS delivery", "muted small"))));
  append(host, node("h3", "Customer contact & update preferences"));
  const contact = order.contact;
  host.append(node("p", contact ? `${contact.contactName} · ${contact.preferredChannel === "email" ? contact.email : contact.phone} via ${contact.preferredChannel.toUpperCase()} · updates: ${contact.updateFrequency}` : "No contact details on file; no updates can be sent.", "muted small"));
  (order.outbox || []).forEach(item => host.append(append(node("div", null, "message"), node("span", `${item.kind.toUpperCase()} · ${item.status}`, "language"), node("div", order.language === "en" ? item.en : item.tr), node("p", `${item.channel}${item.to ? " → " + item.to : ""} · ${new Date(item.at).toLocaleTimeString()} · ${item.deliveredExternally ? "sent through the delivery provider" : "not sent externally"}`, "muted small"))));
  const adviceButton = node("button", "Request live AI advice (no execution authority)", "button secondary");
  adviceButton.disabled = !snapshot.configuration.aiAdviceEnabled || !can("operate")
    || Boolean(order.enterpriseApproval && order.enterpriseApproval.status !== "approved");
  adviceButton.onclick = async () => {
    if (!requireToken()) return;
    adviceButton.disabled = true;
    try { await api(`/api/orders/${id}/advice`, {method: "POST"}); toast("Live AI recommendation saved. No actions were executed."); await refresh(); }
    catch (error) { toast(error.message, true); }
    finally { adviceButton.disabled = false; }
  };
  host.append(adviceButton);
  if (order.aiAdvice) host.append(node("pre", JSON.stringify(order.aiAdvice, null, 2)));
  else host.append(node("p", "Rules-demo mode is always explicit. Live AI advice, when configured, cannot approve or provision.", "muted small"));
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    snapshot = await api("/api/state"); render();
    const health = await api("/health");
    $("#health").textContent = health.status === "ok" ? "Supervisor online" : "Supervisor degraded";
    errorShown = false;
  } catch (error) {
    if (error.status === 401) { leaveConsole("Your staff session has ended. Please sign in again."); return; }
    $("#health").textContent = "Connection issue";
    if (!errorShown) toast(`Refresh failed: ${error.message}`, true);
    errorShown = true;
  } finally { polling = false; }
}
function showPartnerChoice() { $("#order-partner-field").classList.toggle("hidden", $("#submission-channel").value !== "partner"); }
/* A service team signed in with Microsoft places orders for an enterprise it picks from the database; that
   enterprise's contact is asked to approve before anything starts. Other channels keep the demo form. */
let enterprisesListed = false;
const ORDER_TEXT = {
  demo: ["SYNTHETIC SERVICE REQUEST", "Start an order journey", "Use invented demo data only. No live service will be provisioned.", "Submit synthetic order →"],
  enterprise: ["SERVICE REQUEST FOR AN ENTERPRISE", "Place an order for an enterprise", "Choose the enterprise this order is for. No live service will be provisioned.", "Send for enterprise approval →"]
};
function staffSso() { return ["sso", "sso+token"].includes(session.staffAuth); }
function enterpriseOrder() { return (staffSso() || session.enterpriseApprovals) && $("#submission-channel").value === "operator"; }
function showOrderMode() {
  const enterprise = enterpriseOrder();
  if (session.isolatedArchitecture) $("#order-approval-note").textContent = session.delivery
    ? "The enterprise's contact receives an approval email and can approve from its link or their account. No agent starts and nothing is provisioned until they approve."
    : "The enterprise's contact must approve this order in their account. Approval email is simulated; nothing is sent. No agent starts and nothing is provisioned until they approve.";
  [["#order-enterprise-field", false], ["#order-approval-note", false], ["#order-customer-field", true], ["#order-language-field", true], ["#order-contact-block", true]]
    .forEach(([selector, demoOnly]) => $(selector).classList.toggle("hidden", demoOnly === enterprise));
  // A hidden control is still validated and submitted unless it is disabled too.
  $("#order-enterprise").disabled = !enterprise;
  $("#order-customer-field input").disabled = $("#order-language-field select").disabled = $("#order-contact-block").disabled = enterprise;
  const [eyebrow, title, intro, submit] = ORDER_TEXT[enterprise ? "enterprise" : "demo"];
  $("#order-eyebrow").textContent = eyebrow; $("#order-title").textContent = title;
  $("#order-intro").textContent = intro; $("#submit-order").textContent = submit;
  if (!enterprise) $("#order-enterprise-hint").classList.add("hidden");
  else if (!enterprisesListed) loadEnterprises();
}
async function loadEnterprises() {
  const select = $("#order-enterprise"), hint = $("#order-enterprise-hint");
  const option = (text, value = "") => Object.assign(node("option", text), {value});
  enterprisesListed = true; hint.classList.add("hidden");
  select.replaceChildren(option("Loading enterprises…"));
  try {
    const {enterprises} = await api("/api/enterprises"), usable = enterprises.filter(item => item.verified);
    select.replaceChildren(option(usable.length ? "Choose an enterprise…" : "No enterprise to choose from"));
    enterprises.forEach(item => {
      const entry = option(`${item.company} · ${item.contactName} (${item.email})${item.waiting ? ` · ${item.waiting} awaiting approval` : ""}${item.verified ? "" : " · e-mail not verified"}`, item.id);
      entry.disabled = !item.verified; entry.dataset.contact = item.contactName; select.append(entry);
    });
    const problem = !enterprises.length ? "No enterprise has registered yet. An enterprise appears here once it has created its account."
      : !usable.length ? "No enterprise has verified its e-mail address yet, so none can be asked to approve an order." : "";
    hint.textContent = problem; hint.classList.toggle("hidden", !problem);
  } catch (error) {
    enterprisesListed = false;
    select.replaceChildren(option("The enterprises could not be loaded"));
    hint.textContent = error.message; hint.classList.remove("hidden");
  }
}
function startOrder(partner = false) {
  $("#submission-channel").value = partner ? "partner" : "operator"; $("#order-error").textContent = ""; enterprisesListed = false;
  showPartnerChoice(); showOrderMode(); $("#order-dialog").showModal();
}
$("#submission-channel").addEventListener("change", () => { showPartnerChoice(); showOrderMode(); });
const correctionOption = node("option", "Invalid site → customer correction");
correctionOption.value = "address-correction"; document.querySelector('select[name="scenario"]').append(correctionOption);
document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
$("#new-order").onclick = () => startOrder(); $("#journey-button").onclick = () => startOrder();
$("#partner-order").onclick = () => startOrder(true);
$("#close-drawer").onclick = closeOrder; $("#drawer-backdrop").onclick = closeOrder;
$("#access-button").onclick = () => $("#access-dialog").showModal();
document.querySelectorAll(".close-dialog").forEach(button => button.onclick = () => button.closest("dialog").close());
$("#access-form").onsubmit = event => {
  event.preventDefault(); token = new FormData(event.target).get("token").trim();
  event.target.reset(); $("#access-dialog").close(); $("#access-button").textContent = "Token loaded";
  toast("Token loaded in tab memory. Server checks its permissions on every action.");
};
async function placeForEnterprise({enterpriseId, city, product, scenario}) {
  const contact = $("#order-enterprise").selectedOptions[0]?.dataset.contact || "the enterprise's contact";
  $("#submit-order").disabled = true; $("#order-error").textContent = "";
  try {
    const order = await api(`/api/enterprises/${encodeURIComponent(enterpriseId)}/orders`, {method: "POST", body: JSON.stringify({city, product, scenario, idempotencyKey: crypto.randomUUID()})});
    $("#order-dialog").close(); toast(`Order ${order.id} placed. Waiting for ${contact} to approve it.`);
    await refresh(); openOrder(order.id);
  } catch (error) { $("#order-error").textContent = error.message; }
  finally { $("#submit-order").disabled = false; }
}
$("#order-form").onsubmit = async event => {
  event.preventDefault(); if (!requireToken()) return;
  const data = Object.fromEntries(new FormData(event.target).entries()), channel = data.channel; delete data.channel;
  if (data.enterpriseId) { await placeForEnterprise(data); return; }
  data.contact = {contactName: data.contactName, preferredChannel: data.preferredChannel, updateFrequency: data.updateFrequency};
  if (data.email) data.contact.email = data.email;
  if (data.phone) data.contact.phone = data.phone;
  ["contactName", "email", "phone", "preferredChannel", "updateFrequency"].forEach(key => delete data[key]);
  data.idempotencyKey = crypto.randomUUID();
  $("#submit-order").disabled = true; $("#order-error").textContent = "";
  try {
    let order;
    const partnerId = channel === "partner" ? $("#order-partner").value : "";
    if (partnerId) order = await api(`/api/partners/${encodeURIComponent(partnerId)}/orders`, {method: "POST", body: JSON.stringify(data)});
    else if (channel === "partner") {
      const result = await api("/mcp", {method: "POST", body: JSON.stringify({jsonrpc: "2.0", id: crypto.randomUUID(), method: "tools/call", params: {name: "submit_partner_order", arguments: data}})});
      if (result.error || result.result.isError) throw new Error(result.error?.message || result.result.content[0].text);
      order = JSON.parse(result.result.content[0].text);
    } else order = await api("/api/orders", {method: "POST", body: JSON.stringify(data)});
    $("#order-dialog").close(); toast(`Order ${order.id} accepted by durable supervisor.`);
    await refresh(); openOrder(order.id);
  } catch (error) { $("#order-error").textContent = error.message; }
  finally { $("#submit-order").disabled = false; }
};
$("#capacity-button").onclick = async () => {
  if (!requireToken()) return;
  try {
    const response = await api("/mcp", {method: "POST", body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "tools/call", params: {name: "check_partner_capacity", arguments: {city: "Istanbul"}}})});
    $("#partner-result").textContent = JSON.stringify(response.result || response.error, null, 2);
  } catch (error) { toast(error.message, true); }
};
$("#audit-download").onclick = async () => {
  if (!requireToken("read")) return;
  try {
    const response = await fetch("/api/audit.csv", {headers: {...tabHeaders(), ...(token ? {Authorization: `Bearer ${token}`} : {})}});
    if (!response.ok) throw new Error(`Audit export rejected (${response.status})`);
    const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
    link.href = url; link.download = "ama-demo-audit.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
};
["#audit-person", "#audit-action"].forEach(selector => $(selector).addEventListener("change", () => { if (snapshot) renderAudit(); }));
$("#audit-order").addEventListener("input", () => { if (snapshot) renderAudit(); });
document.addEventListener("keydown", event => { if (event.key === "Escape") closeOrder(); });
function applyRole() {
  const reason = can("operate") ? "" : `Your role (${session.staff.roleLabel}) cannot change orders.`;
  ["#new-order", "#journey-button", "#partner-order", "#capacity-button"].forEach(selector => {
    const button = $(selector); button.disabled = Boolean(reason); button.title = reason;
  });
}
function showSession() {
  const admin = session.role === "admin", sso = staffSso(), staff = session.staff;
  $("#who").textContent = !admin ? "" : staff ? `${staff.name} · ${staff.roleLabel}` : "Signed in · Service manager";
  $("#who").title = staff ? staff.upn : "";
  $("#who").classList.toggle("named", Boolean(staff));
  $("#who").classList.toggle("hidden", !admin);
  $("#signout").classList.toggle("hidden", !admin);
  $("#signin-link").classList.toggle("hidden", admin);
  $("#access-button").classList.toggle("hidden", admin || sso);
  // The walkthrough page signs in with pasted tokens, which a Microsoft-signed-in team does not have.
  $("#walkthrough-link").classList.toggle("hidden", sso);
  $("#signin-link").textContent = session.staffAuth === "sso" ? "Sign in with Microsoft" : "Staff sign in";
  $("#audit-lede").textContent = session.staffAuth === "sso+token"
    ? "Microsoft sign-in records the person and their role. Actions made with the shared staff access token are marked as such."
    : sso
    ? "Every action by a signed-in team member is filed with their name, sign-in and role, and with what it changed."
    : "Actions by agents, customers and partners are recorded here. Staff share one manager token, so their actions are not attributed to a person.";
  if (sso) $("#partner-result").textContent = "Capacity answers appear here once a service operator or manager runs a check.";
  applyRole();
}
function leaveConsole(message) {
  clearInterval(pollTimer);
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* Nothing to forget when storage is blocked. */ }
  if (message) sessionStorage.setItem(NOTICE_KEY, message);
  location.replace("/login#staff");
}
$("#signout").onclick = async () => {
  try { await api("/api/auth/logout", {method: "POST"}); leaveConsole(); }
  catch (error) { toast(`Sign-out failed: ${error.message}`, true); }
};
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
async function boot() {
  try { session = await api("/api/auth/me"); } catch { /* Keep the defaults; the first poll reports a connection problem. */ }
  if (session.authenticated && session.role !== "admin") { location.replace(HOME[session.role] || "/login"); return; }
  if (!session.authenticated && session.mode === "live") { leaveConsole(); return; }
  showSession();
  document.body.classList.remove("booting");
  refresh(); pollTimer = setInterval(refresh, 3000);
}
buildAgentPages(); boot();
