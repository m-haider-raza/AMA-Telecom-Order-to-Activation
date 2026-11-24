"use strict";
/* Wholesale partner administration for the console. It runs after app.js and borrows its helpers
   (api, toast, requireToken, session, token, snapshot, openOrder); every name of its own stays private.
   Toasts sit behind an open dialog, so feedback inside a dialog is written into the dialog itself. */
(() => {
  const POLL_MS = 5000, MAX_INBOX = 60;
  const CATEGORY = {delivery: "Delivery", billing: "Billing", technical: "Technical", other: "Other"};
  const CONCERN_STATE = {open: "Open", inProgress: "In progress", resolved: "Resolved"};
  const CONCERN_TONE = {open: "warn", inProgress: "info", resolved: "ok"};
  const INVOICE_STATE = {scheduled: "Scheduled", issued: "Issued", overdue: "Overdue", paid: "Paid"};
  const INVOICE_TONE = {scheduled: "mute", issued: "info", overdue: "bad", paid: "ok"};
  const INVOICE_RANK = {overdue: 0, issued: 1, scheduled: 2, paid: 3};
  const TERMS = [[0, "Due on receipt"], [14, "14 days"], [30, "30 days"], [45, "45 days"], [60, "60 days"]];
  const lockedText = () => session.staffAuth === "sso" ? "Sign in with Microsoft to manage partner accounts."
    : session.staffAuth === "sso+token" ? "Sign in as staff to manage partner accounts."
    : "Sign in as staff, or load the manager token from Demo access, to manage partner accounts.";
  const refusedText = () => session.staff ? `Your role (${session.staff.roleLabel}) cannot open partner accounts.`
    : "This token can't manage partner accounts. Use the manager token, or sign in as staff.";
  const SHARED_TOKEN = "Shared partner token (no organisation)";

  let data = null, signature = "", managed = "", viewing = "", shown = "", inFlight = null;

  function make(tag, props, ...children) {
    const element = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") element.textContent = value;
      else if (key === "class") element.className = value;
      else element.setAttribute(key, value === true ? "" : value);
    });
    element.append(...children.flat().filter(child => child !== null && child !== undefined && child !== false));
    return element;
  }
  const enc = encodeURIComponent;
  const send = (path, method, body) => api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
  const money = value => new Intl.NumberFormat("en-GB", {style: "currency", currency: "TRY", currencyDisplay: "code", maximumFractionDigits: 0}).format(value || 0).replace(/\u00a0/g, " ");
  const stamp = iso => iso ? new Date(iso).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "–";
  const dayText = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString([], {day: "numeric", month: "short", year: "numeric"}) : "–";
  const terms = days => days === 0 ? "due on receipt" : `${days}-day terms`;
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
  const shorten = (text, limit) => text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  const chip = (text, tone) => make("span", {class: tone ? `chip ${tone}` : "chip", text});
  const heads = titles => make("thead", null, make("tr", null, titles.map(title => make("th", {text: title}))));
  const findPartner = id => data && data.partners.find(partner => partner.id === id);

  function explain(error) {
    try {
      const problems = JSON.parse(error.message);
      if (Array.isArray(problems)) {
        return problems.map(item => `${(item.loc || []).slice(1).join(" › ") || "Input"}: ${String(item.msg || "not valid").replace(/^Value error, /, "")}`).join("; ");
      }
    } catch { /* A plain sentence from the server needs no translation. */ }
    return error.message;
  }
  function say(element, message, good = false) {
    element.textContent = message; element.className = good ? "form-ok" : "form-error";
  }
  async function working(button, task) {
    button.disabled = true;
    try { await task(); } finally { button.disabled = false; }
  }

  /* Loading */
  function lock(message) {
    const mark = `locked:${message}`;
    if (signature === mark) return;
    data = null; signature = mark;
    [$("#partner-list"), $("#concern-list")].forEach(host => host.replaceChildren(make("div", {class: "empty", text: message})));
    $("#concern-summary").textContent = ""; $("#concern-count").classList.add("hidden");
    fillChoices();
  }
  async function load() {
    if (session.role !== "admin" && !token) { lock(lockedText()); return; }
    try { adopt(await api("/api/partners")); }
    catch (error) {
      if (error.status === 401) lock(lockedText());
      else if (error.status === 403) lock(refusedText());
      else if (!data) lock(`Partner accounts could not be loaded: ${error.message}`);
    }
  }
  function poll(force = false) {
    if (!force && (document.hidden || document.body.classList.contains("booting"))) return Promise.resolve();
    if (inFlight) return force ? inFlight.then(() => poll(true)) : inFlight;
    inFlight = load().finally(() => { inFlight = null; });
    return inFlight;
  }
  function adopt(next) {
    const mark = JSON.stringify(next);
    if (mark === signature) return;
    signature = mark; data = next;
    paintList(); paintInbox(); paintBadge(); fillChoices();
    const open = managed && findPartner(managed);
    if (open) { paintFacts(open); paintPeopleList(open); }
    if (viewing) paintConcern();
  }

  /* Partner accounts */
  function paintList() {
    const host = $("#partner-list"); host.replaceChildren();
    if (!data.partners.length) { host.append(make("div", {class: "empty", text: "No partner accounts yet. Add a partner to invite its first contact."})); return; }
    host.append(make("table", null, heads(["Partner", "People", "Deliveries", "Payments", "Concerns", ""]), make("tbody", null, data.partners.map(partnerRow))));
  }
  function partnerRow(partner) {
    const invited = partner.contacts.filter(person => person.pending).length, pay = partner.payments;
    const row = make("tr", {class: "order-row", tabindex: "0", "aria-label": `Manage ${partner.name}`},
      make("td", null,
        make("span", {class: "order-name"}, partner.name, partner.status === "active" ? null : [" ", chip("Switched off", "bad")]),
        make("span", {class: "order-id", text: `${partner.id} · ${terms(partner.paymentTermsDays)}${partner.apiClient ? " · API client" : ""}`})),
      make("td", null, make("div", {text: plural(partner.contacts.length, "person", "people")}),
        invited ? make("span", {class: "order-id", text: `${invited} invited, not signed in yet`}) : null),
      make("td", null, make("div", {text: `${partner.openOrders} open of ${partner.orders}`}),
        partner.needsInput ? make("span", {class: "order-id", text: `${partner.needsInput} need the site confirmed`}) : null),
      make("td", null, make("div", {text: money(pay.outstanding)}),
        pay.overdueCount ? chip(`${money(pay.overdue)} overdue`, "bad") : make("span", {class: "order-id", text: `${money(pay.paid)} paid`})),
      make("td", null, partner.openConcerns ? chip(`${partner.openConcerns} open`, "warn") : make("span", {class: "muted", text: "None"})),
      make("td", {class: "link-cell", text: "Manage →"}));
    row.addEventListener("click", () => openManage(partner.id));
    row.addEventListener("keydown", event => { if (event.key === "Enter") openManage(partner.id); });
    return row;
  }
  function fillChoices() {
    const select = $("#order-partner"), keep = select.value;
    const options = (data ? data.partners.filter(partner => partner.status === "active") : [])
      .map(partner => make("option", {value: partner.id, text: partner.name}));
    select.replaceChildren(make("option", {value: "", text: SHARED_TOKEN}), ...options);
    select.value = options.some(option => option.value === keep) ? keep : "";
  }

  function openAdd() {
    if (!requireToken("manage")) return;
    const name = $("#partner-form").elements.namedItem("name");
    name.placeholder = "Anatolia Wholesale AS";
    $("#partner-error").textContent = ""; $("#partner-dialog").showModal();
  }
  async function createPartner(event) {
    event.preventDefault(); if (!requireToken("manage")) return;
    const form = event.currentTarget, fields = new FormData(form), field = key => String(fields.get(key) || "").trim();
    const body = {
      name: field("name"), contactName: field("contactName"), email: field("email"), phone: field("phone") || null,
      language: field("language"), paymentTermsDays: Number(field("paymentTermsDays")), billingEmail: field("billingEmail") || null,
      apiClient: form.elements.namedItem("apiClient").checked,
    };
    await working($("#partner-submit"), async () => {
      try {
        const result = await send("/api/partners", "POST", body);
        form.reset(); $("#partner-dialog").close();
        await poll(true);
        showInvite(result.contact, result.invite, result.partner.name);
      } catch (error) { say($("#partner-error"), explain(error)); }
    });
  }

  function showInvite(person, invite, organisation) {
    $("#invite-title").textContent = `Invitation for ${person.name}`;
    $("#invite-lede").textContent = `Send this link to ${person.email}. ${person.name} opens it, chooses a password and lands in the ${organisation} partner portal.`;
    $("#invite-link").value = `${location.origin}${invite.path}`;
    $("#invite-copy").textContent = "Copy link";
    $("#invite-note").textContent = `${invite.delivery === "email" ? `We also emailed this link to ${invite.email}.` : "This link was not emailed, so copy it and send it yourself."} It works once and expires ${stamp(invite.expiresAt)}. Open it in a private window to try the partner experience without leaving this staff session.`;
    $("#invite-dialog").showModal();
  }
  async function copyInvite(event) {
    const button = event.currentTarget, input = $("#invite-link");
    try { await navigator.clipboard.writeText(input.value); button.textContent = "Copied ✓"; }
    catch { input.select(); button.textContent = "Press Ctrl+C to copy"; }
    setTimeout(() => { button.textContent = "Copy link"; }, 2500);
  }

  /* One partner: terms, people and payments */
  function openManage(id) {
    const partner = findPartner(id); if (!partner) return;
    managed = id;
    paintFacts(partner); paintSettings(partner, ""); paintPeople(partner); paintInvoices(partner, "");
    $("#manage-dialog").showModal();
  }
  function paintFacts(partner) {
    $("#manage-title").textContent = partner.name;
    const facts = [["Status", partner.status === "active" ? "Active" : "Switched off"], ["Payment terms", terms(partner.paymentTermsDays)],
      ["Deliveries", `${partner.openOrders} open of ${partner.orders}`], ["Outstanding", money(partner.payments.outstanding)],
      ["Overdue", money(partner.payments.overdue)], ["Open concerns", String(partner.openConcerns)]];
    $("#manage-facts").replaceChildren(...facts.map(([label, value]) => make("div", {class: "detail-cell"}, make("small", {text: label}), make("div", {text: value}))));
  }

  function paintSettings(partner, note) {
    const options = TERMS.some(([days]) => days === partner.paymentTermsDays) ? TERMS : [...TERMS, [partner.paymentTermsDays, `${partner.paymentTermsDays} days`]];
    const message = make("p", {role: "status"}), save = make("button", {class: "button primary", type: "submit", text: "Save changes"});
    const toggle = make("button", {class: "button outline", type: "button", text: partner.status === "active" ? "Switch partner off" : "Switch partner back on"});
    const name = make("input", {name: "name", value: partner.name, required: true, minlength: "2", maxlength: "80", autocomplete: "off"});
    const form = make("form", {class: "mini-form"},
      make("label", null, "Organisation", name),
      make("div", {class: "two-column"},
        make("label", null, "Payment terms", make("select", {name: "paymentTermsDays"},
          options.map(([days, label]) => make("option", {value: String(days), text: label, selected: days === partner.paymentTermsDays})))),
        make("label", null, "Billing email (optional)", make("input", {name: "billingEmail", type: "email", value: partner.billingEmail || "", maxlength: "120", autocomplete: "off"}))),
      make("label", {class: "check"}, make("input", {type: "checkbox", name: "apiClient", checked: partner.apiClient}),
        make("span", {text: "The shared partner API token (MCP and TM Forum orders) acts for this organisation. Only one organisation can hold it."})),
      message, make("div", {class: "row-actions"}, save, toggle));
    say(message, note, true);
    form.addEventListener("submit", event => {
      event.preventDefault(); if (!requireToken("manage")) return;
      const fields = new FormData(form), field = key => String(fields.get(key) || "").trim();
      const body = {paymentTermsDays: Number(field("paymentTermsDays")), billingEmail: field("billingEmail") || null, apiClient: form.elements.namedItem("apiClient").checked};
      if (field("name") !== partner.name) body.name = field("name");
      saveSettings(partner, body, "Saved.", save, message);
    });
    toggle.addEventListener("click", () => {
      if (!requireToken("manage")) return;
      const off = partner.status === "active";
      if (off && !confirm(`Switch off ${partner.name}? Everyone at this partner is signed out and cannot sign in until you switch it back on.`)) return;
      saveSettings(partner, {status: off ? "disabled" : "active"}, off ? "Partner switched off." : "Partner is active again.", toggle, message);
    });
    $("#manage-settings").replaceChildren(form);
  }
  function saveSettings(partner, body, note, button, message) {
    return working(button, async () => {
      try {
        await send(`/api/partners/${enc(partner.id)}`, "PUT", body);
        await poll(true);
        const fresh = findPartner(partner.id);
        if (fresh && managed === partner.id) { paintFacts(fresh); paintSettings(fresh, note); }
      } catch (error) { say(message, explain(error)); }
    });
  }

  function paintPeople(partner) {
    $("#manage-people").replaceChildren(make("div", {class: "people", id: "people-list"}), make("p", {id: "people-note", role: "status"}), inviteForm(partner));
    paintPeopleList(partner);
  }
  function paintPeopleList(partner) {
    const list = $("#people-list");
    if (!list) return;
    list.replaceChildren(...(partner.contacts.length ? partner.contacts.map(person => personRow(partner, person))
      : [make("p", {class: "muted", text: "No one can sign in for this partner yet. Invite a person below."})]));
  }
  function personRow(partner, person) {
    const expired = person.pending && person.inviteExpiresAt && new Date(person.inviteExpiresAt) < new Date();
    const state = !person.pending ? chip("Active", "ok") : expired ? chip("Invitation expired", "bad")
      : chip(person.inviteExpiresAt ? `Invited · until ${stamp(person.inviteExpiresAt)}` : "Invited", "warn");
    const side = make("div", {class: "person-side"}, state);
    if (person.pending) {
      const resend = make("button", {class: "button secondary compact", type: "button", text: "Send a new invitation"});
      resend.addEventListener("click", () => {
        if (!requireToken("manage")) return;
        working(resend, async () => {
          try {
            const result = await send(`/api/partners/${enc(partner.id)}/users/${enc(person.id)}/invite`, "POST");
            say($("#people-note"), "");
            await poll(true);
            showInvite(result.contact, result.invite, partner.name);
          } catch (error) { say($("#people-note"), explain(error)); }
        });
      });
      side.append(resend);
    }
    return make("div", {class: "person"},
      make("div", null, make("strong", {text: person.name}), make("span", {text: [person.email, person.phone].filter(Boolean).join(" · ")})), side);
  }
  function inviteForm(partner) {
    const message = make("p", {role: "status"}), submit = make("button", {class: "button secondary", type: "submit", text: "Create invitation"});
    const form = make("form", {class: "mini-form"},
      make("h3", {text: "Invite another person"}),
      make("div", {class: "three-column"},
        make("label", null, "Name", make("input", {name: "name", required: true, minlength: "2", maxlength: "80", autocomplete: "off"})),
        make("label", null, "Email", make("input", {name: "email", type: "email", required: true, maxlength: "120", autocomplete: "off"})),
        make("label", null, "Language", make("select", {name: "language"}, make("option", {value: "en", text: "English"}), make("option", {value: "tr", text: "Türkçe"})))),
      message, make("div", {class: "row-actions"}, submit));
    say(message, "");
    form.addEventListener("submit", event => {
      event.preventDefault(); if (!requireToken("manage")) return;
      const fields = new FormData(form);
      working(submit, async () => {
        try {
          const result = await send(`/api/partners/${enc(partner.id)}/users`, "POST",
            {name: String(fields.get("name")).trim(), email: String(fields.get("email")).trim(), language: String(fields.get("language"))});
          form.reset(); say(message, "");
          await poll(true);
          showInvite(result.contact, result.invite, partner.name);
        } catch (error) { say(message, explain(error)); }
      });
    });
    return form;
  }

  async function paintInvoices(partner, note) {
    const host = $("#manage-invoices");
    if (!note) host.replaceChildren(make("p", {class: "muted", text: "Loading invoices…"}));
    try {
      const result = await api(`/api/partners/${enc(partner.id)}/invoices`);
      if (managed === partner.id) host.replaceChildren(...invoiceSection(partner, result, note));
    } catch (error) {
      if (managed === partner.id) host.replaceChildren(make("p", {class: "form-error", text: explain(error)}));
    }
  }
  function invoiceSection(partner, result, note) {
    const pay = result.payments, status = make("p", {role: "status"});
    say(status, note, true);
    if (!result.invoices.length) return [make("p", {class: "muted", text: "No invoices yet. One is scheduled for every order this partner places."})];
    const reference = make("input", {name: "reference", maxlength: "60", placeholder: "Optional, shown to the partner", autocomplete: "off"});
    const rows = [...result.invoices]
      .sort((a, b) => INVOICE_RANK[a.status] - INVOICE_RANK[b.status] || (a.dueDate || "").localeCompare(b.dueDate || ""))
      .map(invoice => invoiceRow(partner, invoice, reference, status));
    return [
      make("p", {class: "muted", text: `Outstanding ${money(pay.outstanding)} · overdue ${money(pay.overdue)} · paid ${money(pay.paid)} · scheduled ${money(pay.scheduled)}`}),
      make("label", null, "Payment reference for the next payment you record", reference), status,
      make("div", {class: "card scroll-card"}, make("table", null, heads(["Invoice", "Service", "Total", "Due", "Status", ""]), make("tbody", null, rows))),
    ];
  }
  function invoiceRow(partner, invoice, reference, status) {
    const paid = invoice.status === "paid", open = invoice.status === "issued" || invoice.status === "overdue";
    let action = make("span", {class: "muted small", text: "Issued on delivery"});
    if (open || paid) {
      action = make("button", {class: paid ? "button outline compact" : "button primary compact", type: "button", text: paid ? "Undo payment" : "Mark paid"});
      action.addEventListener("click", () => settle(partner, invoice, reference, status, action));
    }
    return make("tr", null,
      make("td", null, make("span", {class: "order-name", text: invoice.number}), make("span", {class: "order-id", text: invoice.customer})),
      make("td", null, make("div", {text: productNames[invoice.product] || invoice.product}), make("span", {class: "order-id", text: invoice.city})),
      make("td", {text: money(invoice.total)}),
      make("td", null, make("div", {text: dayText(invoice.dueDate)}), paid ? make("span", {class: "order-id", text: `paid ${dayText(invoice.paidAt && invoice.paidAt.slice(0, 10))}${invoice.reference ? ` · ${invoice.reference}` : ""}`}) : null),
      make("td", null, chip(INVOICE_STATE[invoice.status] || invoice.status, INVOICE_TONE[invoice.status])),
      make("td", null, action));
  }
  function settle(partner, invoice, reference, status, button) {
    if (!requireToken("manage")) return;
    const paid = invoice.status === "paid", base = `/api/partners/${enc(partner.id)}/payments`;
    if (paid && !confirm(`Reverse the recorded payment for ${invoice.number}?`)) return;
    return working(button, async () => {
      try {
        if (paid) await send(`${base}/${enc(invoice.orderId)}`, "DELETE");
        else await send(base, "POST", {orderId: invoice.orderId, reference: reference.value.trim() || null});
        await poll(true);
        const fresh = findPartner(partner.id);
        if (fresh && managed === partner.id) {
          paintFacts(fresh);
          await paintInvoices(fresh, paid ? `Payment for ${invoice.number} reversed.` : `${invoice.number} marked as paid.`);
        }
      } catch (error) { say(status, explain(error)); }
    });
  }

  /* Concerns inbox */
  function paintInbox() {
    const host = $("#concern-list"), summary = $("#concern-summary"); host.replaceChildren();
    summary.textContent = data.openConcerns ? `${data.openConcerns} open` : "All caught up";
    summary.className = data.openConcerns ? "pill amber" : "pill";
    if (!data.concerns.length) { host.append(make("div", {class: "empty", text: "No concerns yet. When a partner raises one it appears here."})); return; }
    const list = [...data.concerns].sort((a, b) => Number(a.status === "resolved") - Number(b.status === "resolved") || b.updatedAt.localeCompare(a.updatedAt));
    host.append(make("table", null, heads(["Concern", "Partner", "Category", "Order", "Status", "Last activity"]), make("tbody", null, list.slice(0, MAX_INBOX).map(concernRow))));
    if (list.length > MAX_INBOX) host.append(make("p", {class: "muted small pad", text: `Showing the latest ${MAX_INBOX} of ${list.length} concerns.`}));
  }
  function concernRow(concern) {
    const last = concern.messages.at(-1);
    const row = make("tr", {class: "order-row", tabindex: "0", "aria-label": `Open concern ${concern.subject}`},
      make("td", null, make("span", {class: "order-name", text: shorten(concern.subject, 60)}), make("span", {class: "order-id", text: concern.id})),
      make("td", {text: concern.partnerName}),
      make("td", {text: CATEGORY[concern.category] || concern.category}),
      make("td", {text: concern.orderId || "–"}),
      make("td", null, chip(CONCERN_STATE[concern.status] || concern.status, CONCERN_TONE[concern.status])),
      make("td", null, make("div", {text: stamp(concern.updatedAt)}),
        last ? make("span", {class: "order-id", text: `${last.from === "staff" ? "Service team" : last.name}: ${shorten(last.text, 40)}`}) : null));
    row.addEventListener("click", () => openConcern(concern.id));
    row.addEventListener("keydown", event => { if (event.key === "Enter") openConcern(concern.id); });
    return row;
  }
  function paintBadge() {
    const badge = $("#concern-count");
    badge.textContent = data.openConcerns; badge.classList.toggle("hidden", !data.openConcerns);
    badge.title = plural(data.openConcerns, "open partner concern", "open partner concerns");
  }

  function openConcern(id) {
    viewing = id; shown = "";
    $("#concern-reply").reset(); $("#concern-error").textContent = "";
    const reader = !can("operate");
    $("#concern-hint").textContent = reader ? `Your role (${session.staff.roleLabel}) can read concerns but not reply to them.`
      : session.delivery ? "The partner sees your reply in their portal and we email it to them."
      : "The partner sees your reply in their portal. Email delivery is not switched on in this environment.";
    [$("#concern-send"), $("#concern-status"), $("#concern-reply").elements.namedItem("text")].forEach(control => { control.disabled = reader; });
    paintConcern();
    $("#concern-dialog").showModal();
  }
  function paintConcern() {
    const concern = data && data.concerns.find(item => item.id === viewing);
    if (!concern) return;
    const mark = `${concern.updatedAt}|${concern.status}|${concern.messages.length}`;
    if (mark === shown) return;
    shown = mark;
    $("#concern-eyebrow").textContent = `${(CATEGORY[concern.category] || concern.category).toUpperCase()} CONCERN · ${concern.id}`;
    $("#concern-title").textContent = concern.subject;
    const known = concern.orderId && snapshot && snapshot.orders[concern.orderId], meta = [
      chip(CONCERN_STATE[concern.status] || concern.status, CONCERN_TONE[concern.status]),
      make("span", {class: "muted", text: `${concern.partnerName} · raised by ${concern.createdByName} · ${stamp(concern.createdAt)}`}),
    ];
    if (known) {
      const view = make("button", {class: "text-button", type: "button", text: `View order ${concern.orderId} →`});
      view.addEventListener("click", () => { $("#concern-dialog").close(); openOrder(concern.orderId); });
      meta.push(view);
    } else if (concern.orderId) meta.push(make("span", {class: "muted", text: `Order ${concern.orderId}`}));
    $("#concern-meta").replaceChildren(...meta);
    const thread = $("#concern-thread");
    thread.replaceChildren(...concern.messages.map(message => make("article", {class: message.from === "staff" ? "bubble staff" : "bubble"},
      make("small", {text: `${message.name} · ${stamp(message.at)}`}), make("div", {text: message.text}))));
    thread.scrollTop = thread.scrollHeight;
    $("#concern-status").textContent = concern.status === "resolved" ? "Reopen concern" : "Mark as resolved";
  }
  async function reply(event) {
    event.preventDefault(); if (!requireToken() || !viewing) return;
    const form = event.currentTarget, text = String(new FormData(form).get("text")).trim(), note = $("#concern-error");
    if (!text) return;
    await working($("#concern-send"), async () => {
      try {
        await send(`/api/concerns/${enc(viewing)}/messages`, "POST", {text});
        form.reset(); say(note, "");
        await poll(true);
      } catch (error) { say(note, explain(error)); }
    });
  }
  async function toggleConcern(event) {
    const concern = data && data.concerns.find(item => item.id === viewing);
    if (!concern || !requireToken()) return;
    await working(event.currentTarget, async () => {
      try {
        await send(`/api/concerns/${enc(concern.id)}/status`, "PUT", {status: concern.status === "resolved" ? "open" : "resolved"});
        say($("#concern-error"), "");
        await poll(true);
      } catch (error) { say($("#concern-error"), explain(error)); }
    });
  }

  /* A read-only role may look at a partner account but not change it. The server refuses the writes anyway; this
     disables the controls and says why inside the dialog, because a toast would sit behind it. */
  function guardManage() {
    if (can("manage")) return;
    const dialog = $("#manage-dialog");
    dialog.querySelectorAll("input, select, textarea, button:not(.close-dialog)").forEach(control => { control.disabled = true; });
    if (dialog.querySelector("#manage-readonly")) return;
    $("#manage-facts").before(make("p", {id: "manage-readonly", class: "muted", role: "status",
      text: `Your role (${session.staff.roleLabel}) can read this account but not change it. Ask a service manager.`}));
  }
  function guardAdd() {
    const reason = can("manage") ? "" : `Your role (${session.staff.roleLabel}) cannot add partners.`;
    $("#partner-add").disabled = Boolean(reason); $("#partner-add").title = reason;
  }
  new MutationObserver(guardManage).observe($("#manage-dialog"), {childList: true, subtree: true});

  $("#partner-add").addEventListener("click", openAdd);
  $("#partner-form").addEventListener("submit", createPartner);
  $("#invite-copy").addEventListener("click", copyInvite);
  $("#concern-reply").addEventListener("submit", reply);
  $("#concern-status").addEventListener("click", toggleConcern);
  $("#manage-dialog").addEventListener("close", () => { managed = ""; });
  $("#concern-dialog").addEventListener("close", () => { viewing = ""; shown = ""; });
  $("#access-form").addEventListener("submit", () => setTimeout(() => poll(true), 0));
  document.querySelectorAll('[data-page="partners"]').forEach(button => button.addEventListener("click", () => poll(true)));
  document.addEventListener("visibilitychange", () => poll());
  setInterval(() => poll(), POLL_MS);
  const booted = setInterval(() => {
    if (document.body.classList.contains("booting")) return;
    clearInterval(booted); guardAdd(); poll(true);
  }, 200);
})();
