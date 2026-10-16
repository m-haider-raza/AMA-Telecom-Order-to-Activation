"use strict";
let snapshot = null, selectedOrder = null, token = "", polling = false, errorShown = false;
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
async function api(path, options = {}) {
  const headers = {...(options.body ? {"Content-Type": "application/json"} : {}), ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* Keep the HTTP failure visible when no JSON body is available. */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
function requireToken() {
  if (token) return true;
  $("#access-dialog").showModal(); toast("Enter your demo token, then retry the action."); return false;
}
function showPage(page) {
  document.querySelectorAll(".page").forEach(section => section.classList.add("hidden"));
  $(`#${page}-page`).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(button => button.classList.toggle("active", button.dataset.page === page));
  $("#page-title").textContent = {overview: "Activation control tower", orders: "Order workspace", exceptions: "Exception desk", partners: "Wholesale partners", governance: "Trust & governance"}[page];
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
    $("#health").textContent = "Connection issue";
    if (!errorShown) toast(`Refresh failed: ${error.message}`, true);
    errorShown = true;
  } finally { polling = false; }
}
function startOrder(partner = false) {
  $("#submission-channel").value = partner ? "partner" : "operator"; $("#order-error").textContent = ""; $("#order-dialog").showModal();
}
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
  data.idempotencyKey = crypto.randomUUID();
  $("#submit-order").disabled = true; $("#order-error").textContent = "";
  try {
    let order;
    if (channel === "partner") {
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
    const response = await fetch("/api/audit.csv", {headers: {Authorization: `Bearer ${token}`}});
    if (!response.ok) throw new Error(`Audit export rejected (${response.status})`);
    const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
    link.href = url; link.download = "ama-demo-audit.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
};
document.addEventListener("keydown", event => { if (event.key === "Escape") closeOrder(); });
refresh(); setInterval(refresh, 3000);
