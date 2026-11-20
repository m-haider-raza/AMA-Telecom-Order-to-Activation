"use strict";
let snapshot = null, selectedOrder = null, token = "", polling = false, errorShown = false, pollTimer = 0;
let session = {authenticated: false, mode: "demo"};
const NOTICE_KEY = "myturkei-notice";
const SESSION_KEY = "myturkei-session";
const HOME = {admin: "/console", partner: "/partner", customer: "/account"};
const $ = (selector) => document.querySelector(selector);
const productNames = {connectivity: "Dedicated Internet", sdwan: "Managed SD-WAN", cloud: "Cloud Connect"};
const stateNames = {acknowledged: "Accepted", inProgress: "In progress", pendingApproval: "Needs approval", held: "Window hold", awaitingCustomer: "Confirm site", completed: "Active", rejected: "Rejected", failed: "Failed"};
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
function requireToken() {
  if (token || session.role === "admin") return true;
  $("#access-dialog").showModal(); toast("Sign in as staff or enter your demo token, then retry the action."); return false;
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
  const open = order => !["completed", "rejected", "failed", "cancelled"].includes(order.state);
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
    append(row, company, node("td", productNames[order.product] || order.product), node("td", order.city), node("td", order.stage.replaceAll("_", " ")), append(node("td"), node("span", stateNames[order.state] || order.state, `state ${order.state}`)), node("td", order.actor === "wholesale-partner" ? "MCP partner" : "Operator API"));
    row.addEventListener("click", () => openOrder(order.id));
    row.addEventListener("keydown", event => { if (event.key === "Enter") openOrder(order.id); });
    body.append(row);
  });
  append(table, head, body); host.append(table);
}
function render() {
  const orders = Object.values(snapshot.orders).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const metrics = snapshot.metrics, exceptions = orders.filter(order => ["held", "pendingApproval"].includes(order.state));
  $("#metrics").replaceChildren();
  [
    ["Orders in workspace", metrics.orders, "Synthetic enterprise service requests"],
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
  $("#audit-state").textContent = metrics.auditChainValid ? `Hash chain verified · ${snapshot.audit.length} entries · not immutable evidence` : "WARNING: audit hash chain verification failed";
  const provenance = $("#provenance"); provenance.replaceChildren();
  snapshot.dataProvenance.forEach(source => {
    const item = node("div");
    if (source.url && source.url.startsWith("https://www.wikidata.org/")) {
      const link = node("a", `${source.name} · ${source.license}`, "source-link"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; item.append(link);
    } else item.append(node("p", `${source.name} · ${source.license}`, "small"));
    provenance.append(item);
  });
  const auditHost = $("#audit-table"); auditHost.replaceChildren();
  const auditTable = node("table"), auditHead = node("tr"), auditBody = node("tbody");
  ["Time", "Order", "Actor / agent", "Action"].forEach(value => auditHead.append(node("th", value)));
  auditTable.append(append(node("thead"), auditHead));
  snapshot.audit.slice(-15).reverse().forEach(entry => auditBody.append(append(node("tr"), node("td", new Date(entry.at).toLocaleTimeString()), node("td", entry.orderId), node("td", entry.actor), node("td", entry.action))));
  append(auditTable, auditBody); auditHost.append(auditTable);
  renderAgents();
  if (selectedOrder) renderOrder(selectedOrder);
}
function openOrder(id) { selectedOrder = id; renderOrder(id); $("#order-drawer").classList.remove("hidden"); $("#drawer-backdrop").classList.remove("hidden"); }
function closeOrder() { selectedOrder = null; $("#order-drawer").classList.add("hidden"); $("#drawer-backdrop").classList.add("hidden"); }
function renderOrder(id) {
  const order = snapshot.orders[id]; if (!order) return;
  const host = $("#order-detail"); host.replaceChildren();
  append(host, node("h2", order.customer), node("div", `${order.id} · ${productNames[order.product]} · ${order.city}`, "detail-subtitle"), node("span", stateNames[order.state] || order.state, `state ${order.state}`));
  const grid = node("div", null, "detail-grid");
  [["Scenario", order.scenario], ["Active stage", order.stage], ["Service orders", order.serviceOrders.length], ["Resource orders", order.resourceOrders.length], ["Deadline (demo contract)", new Date(order.deadline).toLocaleString()], ["Execution", "Network / partner simulator"]].forEach(([label, value]) => grid.append(append(node("div", null, "detail-cell"), node("small", label), node("div", value))));
  host.append(grid);
  if (order.exception) {
    const warning = append(node("div", null, "warning"), node("strong", "Policy hold · explicit human decision"), node("p", order.exception.detail));
    if (order.state === "awaitingCustomer") {
      const button = node("button", `Confirm demo site ${order.exception.expectedSiteReference}`, "button secondary");
      button.onclick = async () => {
        if (!requireToken()) return;
        try { await api(`/api/orders/${id}/address`, {method: "POST", body: JSON.stringify({siteReference: order.exception.expectedSiteReference})}); toast("Synthetic site confirmed by customer/operator."); await refresh(); }
        catch (error) { toast(error.message, true); }
      };
      warning.append(button);
    } else [["approve", "Approve simulated change"], ["reject", "Reject order"]].forEach(([decision, label]) => {
      const button = node("button", label, "button secondary");
      button.onclick = async () => {
        if (!requireToken()) return;
        if (!confirm(`${label} for ${order.id}? This decision is audited and affects simulated resources only.`)) return;
        button.disabled = true;
        try { await api(`/api/orders/${id}/decision`, {method: "POST", body: JSON.stringify({decision})}); toast("Manager decision recorded."); await refresh(); }
        catch (error) { toast(error.message, true); } finally { button.disabled = false; }
      }; warning.append(button);
    }); host.append(warning);
  }
  append(host, node("h3", "Agent execution timeline"));
  const timeline = node("div", null, "timeline");
  if (!order.timeline.length) timeline.append(node("p", "Queued for the durable order supervisor.", "muted"));
  order.timeline.forEach(item => timeline.append(append(node("article", null, "timeline-item"), node("h3", item.agent), node("code", item.tool), node("p", item.detail), node("time", `${new Date(item.at).toLocaleTimeString()} · ${item.result} · simulation`))));
  host.append(timeline);
  append(host, node("h3", "Customer updates / Müşteri bildirimleri"));
  order.notifications.forEach(message => host.append(append(node("div", null, "message"), node("span", "ENGLISH", "language"), node("div", message.en), node("span", "TÜRKÇE", "language"), node("div", message.tr), node("p", "Simulated portal delivery · no email or SMS sent", "muted small"))));
  append(host, node("h3", "Customer contact & update preferences"));
  const contact = order.contact;
  host.append(node("p", contact ? `${contact.contactName} · ${contact.preferredChannel === "email" ? contact.email : contact.phone} via ${contact.preferredChannel.toUpperCase()} · updates: ${contact.updateFrequency}` : "No contact details on file; no updates can be sent.", "muted small"));
  (order.outbox || []).forEach(item => host.append(append(node("div", null, "message"), node("span", `${item.kind.toUpperCase()} · ${item.status}`, "language"), node("div", order.language === "en" ? item.en : item.tr), node("p", `${item.channel}${item.to ? " → " + item.to : ""} · ${new Date(item.at).toLocaleTimeString()} · simulation, nothing was sent`, "muted small"))));
  const adviceButton = node("button", "Request live AI advice (no execution authority)", "button secondary");
  adviceButton.disabled = !snapshot.configuration.aiMode.startsWith("Regional");
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
function startOrder(partner = false) {
  $("#submission-channel").value = partner ? "partner" : "operator"; $("#order-error").textContent = ""; showPartnerChoice(); $("#order-dialog").showModal();
}
$("#submission-channel").addEventListener("change", showPartnerChoice);
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
$("#order-form").onsubmit = async event => {
  event.preventDefault(); if (!requireToken()) return;
  const data = Object.fromEntries(new FormData(event.target).entries()), channel = data.channel; delete data.channel;
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
  if (!requireToken()) return;
  try {
    const response = await fetch("/api/audit.csv", {headers: {...tabHeaders(), ...(token ? {Authorization: `Bearer ${token}`} : {})}});
    if (!response.ok) throw new Error(`Audit export rejected (${response.status})`);
    const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
    link.href = url; link.download = "ama-demo-audit.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
};
document.addEventListener("keydown", event => { if (event.key === "Escape") closeOrder(); });
function showSession() {
  const admin = session.role === "admin";
  $("#who").textContent = admin ? "Signed in · Service manager" : "";
  $("#who").classList.toggle("hidden", !admin);
  $("#signout").classList.toggle("hidden", !admin);
  $("#signin-link").classList.toggle("hidden", admin);
  $("#access-button").classList.toggle("hidden", admin);
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
