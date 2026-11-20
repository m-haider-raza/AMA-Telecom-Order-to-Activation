"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const specs = [
    {key:"validation", name:"Validation", actor:"Order Validation Agent", duty:"Check the service site and available capacity.", exception:"Invalid site -> customer correction. Missing capacity -> manager review."},
    {key:"decomposition", name:"Decomposition", actor:"Order Decomposition Agent", duty:"Map approved products to linked service and resource orders.", exception:"Unsupported products stop at intake. New catalogue mappings need production approval."},
    {key:"provisioning", name:"Provisioning", actor:"Provisioning Orchestration Agent", duty:"Check policy, then allocate once and activate simulated work.", exception:"Timeout -> recovery. Risk/window conflict -> manager. Never invent capacity."},
    {key:"fallout", name:"Fallout recovery", actor:"Fallout Resolution Agent", duty:"Retry one allowlisted timeout, then hand back to provisioning.", exception:"Unknown-fault human triage is proposed production work, not an implemented queue."},
    {key:"customer_update", name:"Customer updates", actor:"Customer Update Agent", duty:"Persist truthful Turkish/English portal updates after activation.", exception:"No SMS/email is sent. External delivery incidents need production service ownership."}
  ];
  let state = null, selected = "", pending = false, refreshing = false, optionKey = "";
  let permissionProof = null;
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const fail = error => { $("error").textContent = error.message; };
  const token = role => {
    const value = $(role).value.trim();
    if (value.length < 24) throw new Error(`Load the ${role} demo token first. Azure credentials do not belong here.`);
    return value;
  };
  async function request(path, {body, role, expected = 200} = {}) {
    const headers = {Accept:"application/json"};
    if (role) headers.Authorization = `Bearer ${token(role)}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {method:body === undefined ? "GET" : "POST", headers,
      body:body === undefined ? undefined : JSON.stringify(body), signal:AbortSignal.timeout(30000)});
    const data = await response.json();
    if (response.status !== expected) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      throw new Error(`HTTP ${response.status}: ${detail}`);
    }
    return data;
  }
  async function action(work) {
    if (pending) return;
    pending = true; $("error").textContent = "";
    $("start").disabled = true;
    document.querySelectorAll(".actions button").forEach(button => { button.disabled = true; });
    try { await work(); await refresh(); }
    catch (error) { fail(error); }
    finally {
      pending = false; $("start").disabled = false;
      document.querySelectorAll(".actions button").forEach(button => { button.disabled = false; });
    }
  }
  function render() {
    const config = state.configuration;
    $("environment").textContent = `${config.region.toUpperCase()} / ${config.aiMode}. Synthetic data only. ` +
      "This is not a Türkiye-residency-compliant production deployment.";
    const orders = Object.values(state.orders).sort((a,b) => b.createdAt.localeCompare(a.createdAt));
    const key = orders.map(order => order.id).join(",");
    if (key !== optionKey) {
      $("order").replaceChildren(element("option", "Select an order"));
      $("order").firstChild.value = "";
      orders.forEach(order => {
        const option = element("option", `${order.id} / ${order.scenario}`);
        option.value = order.id; $("order").append(option);
      });
      optionKey = key;
    }
    $("order").value = selected;
    const order = state.orders[selected];
    $("export").disabled = !order;
    $("exception").hidden = !order?.exception;
    $("agents").replaceChildren();
    specs.forEach(spec => {
      const entries = order?.timeline.filter(item => item.agent === spec.actor) || [];
      const current = order?.stage === spec.key;
      const blocked = current && ["held", "pendingApproval", "awaitingCustomer"].includes(order.state);
      const status = !order ? "Not observed" : blocked ? "BLOCKED / human or customer input" :
        current && !["completed","rejected","failed"].includes(order.state) ? "NEXT SAVED STAGE / queued" :
          entries.length ? `OBSERVED / ${entries.length} persisted tool event(s)` :
            spec.key === "fallout" && order.scenario !== "auto-recovery" ? "Not required on this route" : "Not yet observed";
      const card = element("article", undefined, `agent ${blocked ? "blocked" : current ? "queued" : entries.length ? "observed" : ""}`);
      card.dataset.agent = spec.key;
      card.append(element("h3", spec.name), element("span", status, "status"), element("p", spec.duty),
        element("p", spec.exception));
      $("agents").append(card);
    });
    if (!order) {
      $("order-state").textContent = "Choose a route to begin.";
      $("handoff").textContent = "One persisted event at a time. No unlimited execution authority.";
      $("timeline").replaceChildren(element("li", "No order selected."));
      $("events").replaceChildren(); $("messages").replaceChildren();
      $("linked").textContent = "No order selected.";
      return;
    }
    $("order-state").textContent = `${order.id} / ${order.state}`;
    const queued = state.events.filter(event => event.orderId === order.id && event.status === "queued");
    $("handoff").textContent = order.exception ? `STOP: ${order.exception.type}. No automatic release.` :
      order.state === "completed" ? "Completed: simulated activation and persisted customer updates." :
        order.state === "rejected" ? "Rejected by the service manager. No false activation." :
          `Next saved stage: ${order.stage}. Queued event: ${queued.map(event => event.type).join(", ") || "none"}.`;
    $("linked").textContent = `${order.serviceOrders.length} service orders / ${order.resourceOrders.length} resource orders. ` +
      `Allocation reserved: ${order.reserved}. Recovery attempts: ${order.recoveryAttempts || 0}. ` +
      `Audit hash chain: ${state.metrics.auditChainValid ? "valid (not immutable)" : "INVALID"}.`;
    $("timeline").replaceChildren();
    order.timeline.forEach(item => {
      const row = element("li");
      row.append(element("strong", item.agent), element("div", item.tool), element("div", item.detail),
        element("small", `${item.at} / ${item.result} / simulated`));
      $("timeline").append(row);
    });
    if (!order.timeline.length) $("timeline").append(element("li", "Accepted; waiting for the supervisor's next tick."));
    $("events").replaceChildren();
    state.events.filter(event => event.orderId === order.id).forEach(event => {
      const row = element("li", `${event.type} / ${event.status}`);
      row.append(element("small", event.createdAt)); $("events").append(row);
    });
    $("messages").replaceChildren();
    order.notifications.forEach(message => {
      const row = element("div", undefined, "message");
      row.append(element("p", `EN: ${message.en}`), element("p", `TR: ${message.tr}`),
        element("small", "Persisted portal simulation / no external delivery"));
      $("messages").append(row);
    });
    if (!order.exception) return;
    const customer = order.state === "awaitingCustomer";
    $("exception-title").textContent = order.exception.type;
    $("exception-detail").textContent = order.exception.detail;
    $("exception-owner").textContent = customer ?
      `Owner: customer supplies the site; authorized submitter confirms ${order.exception.expectedSiteReference}.` :
      "Owner: service manager. Approval/rejection is audited; approval cannot fabricate capacity.";
    $("correct").hidden = !customer;
    $("approve").hidden = customer || order.exception.type === "capacity";
    $("reject").hidden = customer;
    $("deny-partner").hidden = customer;
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try { state = await request("/api/state"); render(); }
    catch (error) { $("environment").textContent = "Workflow connection failed. No live state is being claimed."; fail(error); }
    finally { refreshing = false; }
  }
  $("launch").onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const payload = {customer:"Demo Guided Walkthrough", city:"Istanbul", product:"connectivity",
        scenario:$("scenario").value, language:"tr", idempotencyKey:crypto.randomUUID()};
      let order;
      if ($("channel").value === "partner") {
        const result = await request("/mcp", {role:"partner", body:{jsonrpc:"2.0", id:crypto.randomUUID(),
          method:"tools/call", params:{name:"submit_partner_order", arguments:payload}}});
        if (result.error || result.result.isError) throw new Error(result.error?.message || result.result.content[0].text);
        order = JSON.parse(result.result.content[0].text);
      } else {
        order = await request("/tmf-api/productOrderingManagement/v4/productOrder", {role:"manager", body:payload, expected:201});
      }
      selected = order.id; permissionProof = null; $("permission-result").textContent = "";
    });
  };
  $("order").onchange = () => {
    selected = $("order").value; permissionProof = null; $("permission-result").textContent = "";
    if (state) render();
  };
  ["approve", "reject"].forEach(decision => {
    $(decision).onclick = () => action(async () => {
      if (!confirm(`Service manager: ${decision} ${selected}? This changes simulated work and records an audit event.`)) return;
      await request(`/api/orders/${encodeURIComponent(selected)}/decision`, {role:"manager", body:{decision}});
    });
  });
  $("correct").onclick = () => action(async () => {
    const reference = state.orders[selected]?.exception?.expectedSiteReference;
    if (!reference || !confirm(`Confirm customer-provided fictional site ${reference} for ${selected}?`)) return;
    await request(`/api/orders/${encodeURIComponent(selected)}/address`, {role:$("manager").value.trim() ? "manager" : "partner",
      body:{siteReference:reference}});
  });
  $("deny-partner").onclick = () => action(async () => {
    const tools = await request("/mcp", {role:"partner", body:{jsonrpc:"2.0", id:crypto.randomUUID(), method:"tools/list"}});
    if (tools.error || !tools.result?.tools) throw new Error("The partner token did not authenticate with MCP.");
    const operatorOrder = Object.values(state.orders).find(order => order.actor === "service-manager");
    if (!operatorOrder) throw new Error("An operator-owned order is needed to verify the token role without changing work.");
    await request(`/tmf-api/productOrderingManagement/v4/productOrder/${encodeURIComponent(operatorOrder.id)}`,
      {role:"partner", expected:403});
    const held = state.orders[selected];
    await request(`/api/orders/${encodeURIComponent(selected)}/decision`, {role:"partner", body:{decision:"approve"}, expected:403});
    const observed = (await request("/api/state")).orders[selected];
    if (observed.state !== held.state || observed.approvals.length !== held.approvals.length) {
      throw new Error("The order changed during the permission check; inspect the latest audit before claiming it stayed on hold.");
    }
    permissionProof = {orderId:selected, check:"partner approval denied", httpStatus:403, observedAt:new Date().toISOString()};
    $("permission-result").textContent = "Observed HTTP 403: partner approval denied. Order remains on hold.";
  });
  $("credentials").onchange = () => action(async () => {
    const file = $("credentials").files[0];
    if (!file) return;
    try {
      const credentials = JSON.parse(await file.text());
      if (new URL(credentials.demoUrl).origin !== location.origin) throw new Error("Credentials belong to a different application origin.");
      if (![credentials.adminToken, credentials.partnerToken].every(value => typeof value === "string" && value.length >= 24) ||
          credentials.adminToken === credentials.partnerToken) throw new Error("Expected distinct manager and partner demo tokens.");
      $("manager").value = credentials.adminToken; $("partner").value = credentials.partnerToken;
      $("access-status").textContent = "Demo tokens loaded in tab memory. Server authorization remains authoritative.";
    } finally { $("credentials").value = ""; }
  });
  $("forget").onclick = () => {
    $("manager").value = ""; $("partner").value = "";
    $("access-status").textContent = "Tokens forgotten."; $("credentials").value = "";
  };
  $("export").onclick = () => {
    const order = state?.orders[selected];
    if (!order) return;
    const report = {observedAt:new Date().toISOString(), origin:location.origin, configuration:state.configuration,
      order, events:state.events.filter(event => event.orderId === selected),
      audit:state.audit.filter(entry => entry.orderId === selected), partnerApprovalCheck:permissionProof,
      note:"Observed synthetic workflow, not production KPI or residency evidence."};
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:"application/json"}));
    const link = element("a"); link.href = url; link.download = `${selected}-demo-evidence.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  async function poll() { await refresh(); setTimeout(poll, 1500); }
  poll();
})();
