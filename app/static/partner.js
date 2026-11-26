"use strict";

/* The signed-in partner's pages: deliveries with their expected dates, payments, concerns and their own account.
   Everything is drawn from DOM nodes (see ui.js), so nothing a partner types can ever become markup. */

/* ---- what the server's order states mean to a partner ---- */

const STEPS = ["Received", "Site check", "Planning", "Activation", "Confirmed"];
const STAGE_INDEX = { validation: 1, decomposition: 2, provisioning: 3, fallout: 3, customer_update: 4, done: 5 };
const STEP_TONE = { awaitingCustomer: "blocked", held: "paused", pendingApproval: "paused", failed: "failed" };
const STEP_WORDS = {
  done: "Completed: ", current: "Current step: ", blocked: "Needs your input: ",
  paused: "Waiting: ", failed: "Stopped: ", todo: "Not started: ",
};
const DOT_ICONS = { blocked: "alert", paused: "clock", failed: "x" };

const STATE_INFO = {
  acknowledged: { label: "Received", tone: "", icon: "package" },
  inProgress: { label: "In progress", tone: "info", icon: "refresh" },
  pendingApproval: { label: "Under review", tone: "warn", icon: "shield-check" },
  held: { label: "On hold", tone: "warn", icon: "clock" },
  awaitingCustomer: { label: "Needs your input", tone: "action", icon: "alert" },
  completed: { label: "Delivered", tone: "ok", icon: "circle-check" },
  rejected: { label: "Not accepted", tone: "bad", icon: "x" },
  failed: { label: "Not completed", tone: "bad", icon: "alert" },
};
const STAGE_TEXT = {
  validation: "We're checking the installation address and service availability.",
  decomposition: "We're planning the work needed at the site.",
  provisioning: "We're setting up the service.",
  fallout: "We hit a small snag and are fixing it. No action is needed from you.",
  customer_update: "Almost there. We're confirming the activation.",
};
const OUTLOOK = {
  onTrack: { label: "On track", tone: "ok", icon: "circle-check" },
  atRisk: { label: "At risk", tone: "warn", icon: "alert" },
  delayed: { label: "Delayed", tone: "bad", icon: "clock" },
  delivered: { label: "Delivered", tone: "ok", icon: "circle-check" },
  closed: { label: "Closed", tone: "", icon: "x" },
};
const INVOICE_STATUS = {
  overdue: { label: "Overdue", tone: "bad", icon: "alert", rank: 0 },
  issued: { label: "Payment due", tone: "info", icon: "credit-card", rank: 1 },
  scheduled: { label: "Scheduled", tone: "", icon: "clock", rank: 2 },
  paid: { label: "Paid", tone: "ok", icon: "circle-check", rank: 3 },
};
const CONCERN_STATUS = {
  open: { label: "Open", tone: "action", icon: "message" },
  inProgress: { label: "In progress", tone: "info", icon: "refresh" },
  resolved: { label: "Resolved", tone: "ok", icon: "circle-check" },
};
const CATEGORIES = [
  ["delivery", "Delivery"], ["billing", "Billing and payments"], ["technical", "Technical"], ["other", "Something else"],
];

const PRODUCTS = {
  connectivity: { name: "Dedicated Internet", detail: "500 Mbps", icon: "globe" },
  sdwan: { name: "Managed SD-WAN", detail: "200 Mbps", icon: "share-2" },
  cloud: { name: "Cloud Connect", detail: "1 Gbps", icon: "cloud" },
};
const CITIES = [["Istanbul", "Istanbul"], ["Ankara", "Ankara"], ["Izmir", "Izmir"]];
const FREQUENCIES = [
  ["every-step", "Every step of the order"], ["daily", "Daily summary"], ["weekly", "Weekly summary"],
  ["critical-only", "Only when action is needed or the order is done"],
];
const LANGUAGES = [["en", "English"], ["tr", "Türkçe"]];
const CHANNELS = [["email", "Email", "mail"], ["sms", "SMS", "phone"]];

const TERMINAL = new Set(["completed", "rejected", "failed"]);
const LOCKED = new Set(["held", "pendingApproval"]);
const POLL_MS = 8000;

const MONEY = new Intl.NumberFormat("en-GB", {
  style: "currency", currency: "TRY", currencyDisplay: "narrowSymbol", minimumFractionDigits: 0, maximumFractionDigits: 2,
});
const DAY_FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

const state = {
  account: null, partner: null, deliveries: [], invoices: [], payments: null, concerns: [],
  filter: "all", query: "", sigs: {}, dirty: false,
};
/* The partner-safe history of the delivery being viewed, fetched on demand. */
const timelineCache = { id: "", items: null, sig: "" };

const isOpen = (delivery) => !TERMINAL.has(delivery.state);
const productOf = (delivery) => PRODUCTS[delivery.product] || { name: delivery.product, detail: "", icon: "package" };
const stateInfo = (delivery) => STATE_INFO[delivery.state] || { label: delivery.state, tone: "", icon: "package" };
const outlookInfo = (delivery) => OUTLOOK[delivery.outlook.status]
  || { label: delivery.outlook.status, tone: "", icon: "calendar" };
const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const labelOf = (options, value) => (options.find(([key]) => key === value) || [value, value || "Not set"])[1];
const money = (value) => (Number.isFinite(value) ? MONEY.format(value) : "");
const stamp = (iso) => Date.parse(iso) || 0;

function productTitle(delivery) {
  const product = productOf(delivery);
  return product.detail ? `${product.name} · ${product.detail}` : product.name;
}

/* Calendar dates ("2027-03-12") are not moments in time: format them as written, in every timezone. */
function fmtDay(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T00:00:00Z`);
  return value && !Number.isNaN(date.getTime()) ? DAY_FORMAT.format(date) : "";
}

const dayOffset = (days) => new Date(Date.now() + days * 86400000).toISOString().slice(0, 10);

const deliveryHash = (id) => `#deliveries/${encodeURIComponent(id)}`;
const concernHash = (id) => `#concerns/${encodeURIComponent(id)}`;
const deliveryPath = (id, suffix = "") => `/api/partner/deliveries/${encodeURIComponent(id)}${suffix}`;
const concernPath = (id, suffix = "") => `/api/partner/concerns/${encodeURIComponent(id)}${suffix}`;

function newConcernHash(orderId = "", category = "") {
  const params = new URLSearchParams();
  if (orderId) params.set("order", orderId);
  if (category) params.set("category", category);
  const query = params.toString();
  return query ? `#concerns/new?${query}` : "#concerns/new";
}

const deliveryById = (id) => state.deliveries.find((delivery) => delivery.id === id);
const invoiceOf = (orderId) => state.invoices.find((invoice) => invoice.orderId === orderId) || null;
const concernById = (id) => state.concerns.find((concern) => concern.id === id);
const openConcerns = () => state.concerns.filter((concern) => concern.status !== "resolved");

const FILTERS = {
  all: {
    label: "All deliveries", tone: "", icon: "truck", test: () => true,
    empty: ["No deliveries yet", "Deliveries placed for you will appear here."],
  },
  progress: {
    label: "In progress", tone: "info", icon: "refresh", test: isOpen,
    empty: ["Nothing in progress", "Deliveries that are being set up will appear here."],
  },
  input: {
    label: "Needs your input", tone: "action", icon: "alert", test: (delivery) => delivery.needsInput,
    empty: ["Nothing needs your input", "You're all caught up. We'll tell you if we need anything from you."],
  },
  delivered: {
    label: "Delivered", tone: "ok", icon: "circle-check", test: (delivery) => delivery.state === "completed",
    empty: ["Nothing delivered yet", "Completed deliveries will appear here."],
  },
};

/* ---- progress and expected dates ---- */

/* `index` is the position (from 0) of the step in progress; STEPS.length means every step is done. */
function progressOf(delivery) {
  if (delivery.state === "completed") return { index: STEPS.length, tone: "" };
  if (delivery.state === "rejected") return { index: 1, tone: "" };
  return {
    index: Math.min(STAGE_INDEX[delivery.stage] ?? 1, STEPS.length - 1),
    tone: STEP_TONE[delivery.state] || "current",
  };
}

function stepKind(progress, position) {
  if (position < progress.index) return "done";
  return position === progress.index && progress.tone ? progress.tone : "todo";
}

function progressLabel(delivery) {
  if (delivery.state === "completed") return `All ${STEPS.length} steps complete`;
  if (delivery.state === "rejected") return "Delivery not accepted";
  const { index } = progressOf(delivery);
  return `Step ${index + 1} of ${STEPS.length} · ${STEPS[index]}`;
}

function statusText(delivery) {
  switch (delivery.state) {
    case "completed": return "This delivery is complete and the service is active.";
    case "rejected": return "We couldn't accept this order. Raise a concern and our team will get back to you.";
    case "failed": return "Something went wrong with the activation. Our team is looking into it.";
    case "held": return "This delivery is on hold for a scheduled maintenance window. No action needed.";
    case "pendingApproval": return "A service manager is reviewing this delivery. No action needed from you.";
    case "acknowledged": return "We've received this order and will start shortly.";
    case "awaitingCustomer": return "We need the installation address confirmed before we can continue.";
    default: return STAGE_TEXT[delivery.stage] || "We're working on this delivery.";
  }
}

/* The label and date shown next to the expected-delivery calendar icon. */
function etaWords(delivery) {
  const { status, date } = delivery.outlook;
  if (status === "delivered") return ["Delivered on", fmtDate(date)];
  if (!date) return ["", "No delivery date"];
  return ["Expected delivery", fmtDate(date)];
}

function stepper(delivery) {
  const progress = progressOf(delivery);
  return h("ol", { class: "stepper", "aria-label": "Delivery progress" }, STEPS.map((label, position) => {
    const kind = stepKind(progress, position);
    const dot = kind === "done" ? icon("check") : DOT_ICONS[kind] ? icon(DOT_ICONS[kind]) : String(position + 1);
    return h("li", { class: kind, "aria-current": kind === "done" || kind === "todo" ? false : "step" },
      h("span", { class: "dot" }, dot),
      h("span", { class: "label" }, h("span", { class: "sr-only", text: STEP_WORDS[kind] }), label));
  }));
}

function segments(delivery) {
  const progress = progressOf(delivery);
  return h("div", { class: "segments", "aria-hidden": "true" }, STEPS.map((_, position) => {
    const kind = stepKind(progress, position);
    return h("i", { class: kind === "todo" ? false : kind });
  }));
}

const chip = (info) => h("span", { class: info.tone ? `chip ${info.tone}` : "chip" }, icon(info.icon), info.label);

/* ---- small shared pieces ---- */

const input = (name, attrs = {}) => h("input", { type: "text", name, ...attrs });
const msgBox = () => h("p", { class: "msg", role: "alert", hidden: true });
const failText = (error) => (error instanceof ApiError ? error.message : "Something went wrong. Please try again.");

function textarea(name, attrs = {}) {
  const { text, ...rest } = attrs;
  return h("textarea", { name, rows: 4, text: text || "", ...rest });
}

function pageHead(title, sub, action) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", { tabindex: -1, text: title }), sub ? h("p", { class: "sub", text: sub }) : null),
    action);
}

function back(href = "#deliveries", label = "All deliveries") {
  return h("a", { class: "back", href }, icon("arrow-left"), label);
}

function empty(iconName, title, text, action, level = "h2") {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon", "aria-hidden": "true" }, icon(iconName)),
    h(level, { tabindex: -1, text: title }),
    h("p", { text }),
    action);
}

function agoText(iso) {
  const text = ago(iso);
  return text ? `Updated ${text}` : "";
}

const updated = (iso) => h("span", { "data-ago": iso, text: agoText(iso) });

function tickAgo() {
  for (const node of $$("[data-ago]")) node.textContent = agoText(node.dataset.ago);
}

/*
 * Runs `task`, then puts keyboard focus back on the same control (found by data-key) if it was replaced.
 * Only acts when focus was inside `root`; `fallbackKey` is used if the old control no longer exists.
 */
function keepFocus(task, root = document, fallbackKey = "") {
  const active = document.activeElement;
  const inside = Boolean(active && active !== document.body && root.contains(active));
  const key = inside && active.dataset ? active.dataset.key || "" : "";
  task();
  if (!inside) return;
  for (const candidate of [key, fallbackKey]) {
    const target = candidate ? $(`[data-key="${CSS.escape(candidate)}"]`, root) : null;
    if (target) {
      target.focus({ preventScroll: true });
      return;
    }
  }
}

function facts(rows) {
  return h("dl", { class: "facts" }, rows.map(([label, value, mono]) => h("div", {},
    h("dt", { text: label }),
    h("dd", { class: mono ? "mono" : false, text: value || "Not provided" }))));
}

/* ---- form pieces ---- */

function phoneInput(value) {
  const control = input("phone", {
    type: "tel", value: value || "", autocomplete: "tel", placeholder: "+905550000000", maxlength: 20,
  });
  control.addEventListener("change", () => {
    control.value = normalizePhone(control.value.trim());
  });
  return control;
}

function segmented(name, legend, options, value, prefix) {
  return h("fieldset", { class: "segmented" },
    h("legend", { text: legend }),
    options.map(([optionValue, text, iconName]) => {
      const id = `${prefix}-${name}-${optionValue}`;
      return [
        h("input", { class: "seg-input", type: "radio", name, id, value: optionValue, checked: optionValue === value }),
        h("label", { class: "seg", for: id }, iconName ? icon(iconName) : null, text),
      ];
    }));
}

function contactFields(contact, prefix) {
  const channel = contact.preferredChannel || (contact.phone && !contact.email ? "sms" : "email");
  return [
    h("div", { class: "row" },
      field("Contact person", input("contactName", {
        value: contact.contactName || "", autocomplete: "name", maxlength: 80, required: true,
      })),
      field("Email address", input("email", {
        type: "email", value: contact.email || "", autocomplete: "email", inputmode: "email", maxlength: 120,
        autocapitalize: "none", spellcheck: "false",
      }))),
    h("div", { class: "row" },
      field("Mobile number", phoneInput(contact.phone), "Needed for text-message updates."),
      field("How often should we send updates?",
        select("updateFrequency", FREQUENCIES, contact.updateFrequency || "every-step"))),
    segmented("preferredChannel", "How should we send updates?", CHANNELS, channel, prefix),
  ];
}

function emailProblem(value, required) {
  const text = value.trim();
  if (!text) return required ? "Enter an email address so we can send updates by email." : "";
  return EMAIL_RX.test(text) ? "" : EMAIL_PROBLEM;
}

function phoneProblem(value, required) {
  const text = normalizePhone(value.trim());
  if (!text) return required ? "Enter a mobile number so we can send text messages." : "";
  return PHONE_RX.test(text) ? "" : PHONE_PROBLEM;
}

const contactSpecs = [
  ["contactName", (value) => value.trim().length < 2 && "Enter the name of the person we should contact."],
  ["email", (value, form) => emailProblem(value, form.elements.preferredChannel.value === "email")],
  ["phone", (value, form) => phoneProblem(value, form.elements.preferredChannel.value === "sms")],
];

function contactBody(form) {
  const { contactName, email, phone, preferredChannel, updateFrequency } = form.elements;
  const body = {
    contactName: contactName.value.trim(),
    preferredChannel: preferredChannel.value,
    updateFrequency: updateFrequency.value,
  };
  if (email.value.trim()) body.email = email.value.trim();
  const mobile = normalizePhone(phone.value.trim());
  if (mobile) body.phone = mobile;
  return body;
}

/* ---- deliveries ---- */

const dueOf = (delivery) => (delivery.outlook.date ? stamp(delivery.outlook.date) : Infinity);

/* Deliveries that need the partner first, then open ones by expected date, then closed ones, newest first. */
function byUrgency(a, b) {
  if (a.needsInput !== b.needsInput) return a.needsInput ? -1 : 1;
  const open = isOpen(a);
  if (open !== isOpen(b)) return open ? -1 : 1;
  if (open) return (dueOf(a) - dueOf(b)) || (stamp(b.createdAt) - stamp(a.createdAt));
  return stamp(b.updatedAt) - stamp(a.updatedAt);
}

function matches(delivery, query) {
  const words = query.toLocaleLowerCase("en").split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = [delivery.id, delivery.customer, delivery.city, productOf(delivery).name, delivery.siteAddress,
    (delivery.delivery || {}).poReference].filter(Boolean).join(" ").toLocaleLowerCase("en");
  return words.every((word) => text.includes(word));
}

function visibleDeliveries() {
  const filter = FILTERS[state.filter] || FILTERS.all;
  return state.deliveries.filter((delivery) => filter.test(delivery) && matches(delivery, state.query)).sort(byUrgency);
}

function deliveryCard(delivery) {
  const urgent = delivery.needsInput;
  const info = outlookInfo(delivery);
  const [label, date] = etaWords(delivery);
  return h("li", { class: urgent ? "card needs-action" : "card" },
    h("div", { class: "card-top" },
      h("span", { class: "product-icon", "aria-hidden": "true" }, icon(productOf(delivery).icon)),
      h("div", {},
        h("h2", { class: "card-title" },
          h("a", { class: "card-link", href: deliveryHash(delivery.id), "data-key": `delivery-${delivery.id}` },
            productTitle(delivery), h("span", { class: "sr-only", text: `, delivery ${delivery.id} for ${delivery.customer}` }))),
        h("p", { class: "card-meta" },
          h("span", { class: "mono", text: delivery.id }),
          h("span", {}, icon("building"), delivery.customer),
          h("span", {}, icon("map-pin"), delivery.city))),
      chip(stateInfo(delivery))),
    h("div", { class: info.tone ? `card-eta ${info.tone}` : "card-eta" },
      icon("calendar"),
      label ? h("span", { text: label }) : null,
      h("strong", { text: date }),
      chip(info)),
    h("div", { class: "progress" },
      segments(delivery),
      h("p", { class: "progress-text" }, h("span", { text: progressLabel(delivery) }), updated(delivery.updatedAt))),
    h("p", { class: "status-line", text: statusText(delivery) }),
    urgent ? h("p", { class: "card-cta", "aria-hidden": "true" }, "Add details", icon("arrow-right")) : null);
}

function tileParts() {
  if (!state.deliveries.length) return [];
  return [h("div", { class: "tiles four", role: "group", "aria-label": "Filter deliveries by status" },
    Object.entries(FILTERS).map(([key, filter]) => h("button", {
      type: "button",
      class: `tile ${filter.tone}`,
      "aria-pressed": String(state.filter === key),
      "data-key": `tile-${key}`,
      onclick: () => {
        state.filter = state.filter === key ? "all" : key;
        paintList({ announce: true });
      },
    },
    h("span", { class: "tile-icon", "aria-hidden": "true" }, icon(filter.icon)),
    h("span", {},
      h("span", { class: "num", text: String(state.deliveries.filter(filter.test).length) }),
      h("span", { class: "tile-label", text: filter.label })))))];
}

function clearSearch() {
  state.query = "";
  const box = $('[data-key="search"]');
  if (box) box.value = "";
  paintList({ announce: true, fallback: "search" });
}

function showAllDeliveries() {
  const previous = state.filter;
  state.filter = "all";
  paintList({ announce: true, fallback: `tile-${previous}` });
}

function listParts() {
  const shown = visibleDeliveries();
  if (shown.length) return [h("ul", { class: "orders" }, shown.map(deliveryCard))];
  if (state.query) {
    return [empty("help", "No deliveries match your search", "Try a different company name, reference or city.",
      h("button", { type: "button", class: "btn secondary", "data-key": "clear-search", text: "Clear search", onclick: clearSearch }))];
  }
  const filter = FILTERS[state.filter] || FILTERS.all;
  return [empty(filter.icon, filter.empty[0], filter.empty[1], state.filter === "all"
    ? null
    : h("button", {
      type: "button", class: "btn secondary", "data-key": "show-all", text: "Show all deliveries", onclick: showAllDeliveries,
    }))];
}

function needsInputBanner() {
  const waiting = state.deliveries.filter((delivery) => delivery.needsInput);
  if (!waiting.length) return null;
  const several = waiting.length > 1;
  return h("div", { class: "banner action", role: "status" },
    icon("alert"),
    h("div", { class: "banner-body" },
      h("strong", { text: several ? `${waiting.length} deliveries need your input` : "1 delivery needs your input" }),
      h("p", { text: "We need the installation address confirmed before we can continue. It only takes a minute." })),
    several
      ? h("button", {
        type: "button", class: "btn small", "data-key": "show-input", text: "Show them",
        onclick: () => {
          state.filter = "input";
          paintList({ announce: true });
        },
      })
      : h("a", { class: "btn small", href: deliveryHash(waiting[0].id), text: "Add details" }));
}

function overdueBanner() {
  const summary = state.payments || {};
  if (!summary.overdueCount) return null;
  return h("div", { class: "banner warn", role: "status" },
    icon("credit-card"),
    h("div", { class: "banner-body" },
      h("strong", { text: `${plural(summary.overdueCount, "invoice")} overdue (${money(summary.overdue)})` }),
      h("p", { text: "Please arrange payment, or raise a concern if you think something is wrong." })),
    h("a", { class: "btn small", href: "#payments", text: "View payments" }));
}

function announceList() {
  const note = $("#deliveries-announcer");
  if (!note) return;
  const filter = FILTERS[state.filter] || FILTERS.all;
  const query = state.query.trim();
  note.textContent = `Showing ${visibleDeliveries().length} of ${state.deliveries.length} deliveries `
    + `(${filter.label.toLowerCase()})${query ? ` matching “${query}”` : ""}.`;
}

/* Repaints only the banners, tiles and list, so a half-typed search is never disturbed. */
function paintList({ announce = false, fallback = "" } = {}) {
  const alerts = $("#deliveries-alerts");
  const tilesBox = $("#deliveries-tiles");
  const list = $("#deliveries-list");
  const search = $("#deliveries-search");
  if (!alerts || !tilesBox || !list || !search) return;
  keepFocus(() => {
    alerts.replaceChildren(...[needsInputBanner(), overdueBanner()].filter(Boolean));
    tilesBox.replaceChildren(...tileParts());
    list.replaceChildren(...listParts());
    search.hidden = !state.deliveries.length;
  }, $("#view"), fallback);
  if (announce) announceList();
}

function searchRow() {
  const box = h("input", {
    type: "search", "aria-label": "Search deliveries", "data-key": "search", maxlength: 80, autocomplete: "off",
    placeholder: "Search by company, reference or city", value: state.query,
  });
  let timer = 0;
  box.addEventListener("input", () => {
    state.query = box.value.trim();
    paintList();
    clearTimeout(timer);
    timer = setTimeout(announceList, 600);
  });
  return h("div", { class: "search-row", id: "deliveries-search", hidden: !state.deliveries.length }, box);
}

function deliveriesView() {
  return ["Deliveries", [
    pageHead("Deliveries", `Hello, ${firstName(state.account.name)}. Here is where each order stands and when it should arrive.`,
      h("a", { class: "btn secondary", href: newConcernHash() }, icon("message"), "Raise a concern")),
    h("div", { id: "deliveries-alerts" }, [needsInputBanner(), overdueBanner()].filter(Boolean)),
    h("div", { id: "deliveries-tiles" }, tileParts()),
    searchRow(),
    h("div", { id: "deliveries-list" }, listParts()),
    h("p", { class: "sr-only", id: "deliveries-announcer", role: "status" }),
  ]];
}

/* ---- one delivery ---- */

function etaBlock(delivery) {
  const info = outlookInfo(delivery);
  const [label, date] = etaWords(delivery);
  return h("div", { class: info.tone ? `eta ${info.tone}` : "eta" },
    h("span", { class: "eta-icon", "aria-hidden": "true" }, icon(delivery.outlook.status === "delivered" ? "circle-check" : "calendar")),
    h("div", { class: "eta-body" },
      h("span", { class: "eta-label", text: label || "Closed" }),
      h("span", { class: "eta-date", text: date }),
      h("p", { class: "eta-reason", text: delivery.outlook.reason })),
    chip(info));
}

function statusPanel(delivery) {
  return h("section", { class: "panel", "aria-labelledby": "status-title" },
    h("div", { class: "status-head" },
      h("h2", { id: "status-title", text: "Where this delivery is" }),
      h("span", { class: "hint" }, updated(delivery.updatedAt))),
    stepper(delivery),
    h("p", { class: "status-text", text: statusText(delivery) }));
}

/* What the partner can change on a delivery: where it goes, when they would like it and who to keep informed. */
function deliveryForm(delivery) {
  const locked = LOCKED.has(delivery.state);
  const details = delivery.delivery || {};
  const city = select("city", CITIES, delivery.city, locked ? { disabled: true } : {});
  const address = input("siteAddress", {
    value: delivery.siteAddress || "", autocomplete: "off", maxlength: 200, required: true, readonly: locked,
  });
  const date = input("preferredDate", {
    type: "date", value: details.preferredDate || "", min: dayOffset(0), max: dayOffset(365),
  });
  const reference = input("poReference", { value: details.poReference || "", maxlength: 60, autocomplete: "off" });
  const notes = textarea("accessNotes", { text: details.accessNotes || "", maxlength: 300, rows: 3 });
  const current = delivery.contact || {
    contactName: state.account.name, email: state.account.email, phone: state.account.phone,
    preferredChannel: "email", updateFrequency: "every-step",
  };
  const message = msgBox();
  const form = h("form", { novalidate: true },
    h("div", { class: "row" },
      field("City", city),
      field("Installation address", address, "Include the street, district and city.")),
    h("div", { class: "row" },
      field(optional("Preferred delivery date"), date, "Helps our planners. The expected date above is our current estimate."),
      field(optional("Your PO reference"), reference)),
    field(optional("Access notes"), notes, "For example, who to ask for at reception or the best time to visit."),
    h("h3", { class: "form-section", text: "Who should we update about this delivery?" }),
    contactFields(current, `delivery-${delivery.id}`),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "save-delivery", text: "Save delivery details" }));
  onSubmit(form, message, [
    ["siteAddress", (value) => !locked && value.trim().length < 5
      && "Enter the installation address, including the district and city."],
    ["preferredDate", (value) => {
      if (!value) return "";
      if (value < dayOffset(0)) return "Choose today or a later date, or clear the field.";
      return value > dayOffset(365) ? "Choose a date within the next year." : "";
    }],
    ...contactSpecs,
  ], async () => {
    const resolved = delivery.needsInput;
    const saved = await api(deliveryPath(delivery.id, "/delivery"), {
      method: "PUT",
      body: {
        city: city.value,
        siteAddress: address.value.trim(),
        preferredDate: date.value || null,
        poReference: reference.value.trim() || null,
        accessNotes: notes.value.trim() || null,
        contact: contactBody(form),
      },
    });
    keepTimeline(saved);
    toast(resolved ? "Thank you. The address is confirmed and the delivery is moving again." : "Delivery details saved.");
    await refresh();
  });
  return form;
}

function detailsPanel(delivery) {
  const urgent = delivery.needsInput;
  const details = delivery.delivery || {};
  const contact = delivery.contact || {};
  const panel = (...children) => h("section", { class: urgent ? "panel action" : "panel", "aria-labelledby": "details-title" },
    h("h2", { id: "details-title", text: urgent ? "We need the installation address confirmed" : "Delivery details" }),
    ...children);
  if (!isOpen(delivery)) {
    return panel(
      facts([
        ["Installation address", delivery.siteAddress],
        ["City", delivery.city],
        ["Site reference", delivery.siteReference, true],
        ["Preferred date", fmtDay(details.preferredDate)],
        ["PO reference", details.poReference],
        ["Access notes", details.accessNotes],
        ["Contact person", contact.contactName],
        ["Email address", contact.email],
        ["Mobile number", contact.phone],
      ]),
      h("p", { class: "note" }, icon("info"),
        "This delivery is closed, so its details can no longer be changed. If something is wrong, raise a concern and we will help."),
      h("a", { class: "btn secondary small", href: newConcernHash(delivery.id, "delivery") }, icon("message"), "Raise a concern"));
  }
  return panel(
    urgent
      ? h("p", {
        class: "sub",
        text: `The address on file doesn't mention ${delivery.city}. Correct the address, or choose the right city, `
          + "and we'll carry on straight away.",
      })
      : null,
    facts([urgent ? ["Address on file", delivery.siteAddress] : null, ["Site reference", delivery.siteReference, true]].filter(Boolean)),
    LOCKED.has(delivery.state)
      ? h("p", { class: "note" }, icon("info"),
        "A service manager is reviewing this delivery, so the address is locked for now. You can still change the other "
        + "details, or raise a concern if the address needs to change.")
      : null,
    deliveryForm(delivery));
}

/* ---- history ---- */

function timelineParts() {
  if (!timelineCache.items) return [h("p", { class: "hint", text: "Loading the history…" })];
  if (!timelineCache.items.length) return [h("p", { class: "hint", text: "No history yet." })];
  return [h("ol", { class: "timeline" }, [...timelineCache.items].reverse().map((item) => h("li", {},
    h("time", { class: "when", datetime: item.at, text: fmtStamp(item.at) }),
    h("div", { class: "what", text: item.text }))))];
}

function keepTimeline(detail) {
  timelineCache.id = detail.id;
  timelineCache.items = detail.timeline;
  timelineCache.sig = JSON.stringify(detail.timeline);
}

function historyPanel(delivery) {
  if (timelineCache.id !== delivery.id) Object.assign(timelineCache, { id: delivery.id, items: null, sig: "" });
  return h("section", { class: "panel", "aria-labelledby": "history-title" },
    h("h2", { id: "history-title", text: "History" }),
    h("div", { id: "timeline-box" }, timelineParts()));
}

async function loadTimeline(id) {
  const box = $("#timeline-box");
  try {
    const detail = await api(deliveryPath(id));
    if (timelineCache.id !== id || !box || !box.isConnected) return;
    if (timelineCache.items && timelineCache.sig === JSON.stringify(detail.timeline)) return;
    keepTimeline(detail);
    box.replaceChildren(...timelineParts());
  } catch (error) {
    if (!(error instanceof ApiError)) console.error(error);
    if (box && box.isConnected && timelineCache.id === id && !timelineCache.items) {
      box.replaceChildren(h("p", { class: "hint", text: "We couldn't load the history just now. It will refresh shortly." }));
    }
  }
}

/* ---- the delivery's invoice and concerns ---- */

function invoicePanel(delivery) {
  const invoice = invoiceOf(delivery.id);
  if (!invoice) {
    return h("section", { class: "panel", "aria-labelledby": "invoice-title" },
      h("h2", { id: "invoice-title", text: "Invoice" }),
      h("p", { class: "hint", text: "There is no invoice for this delivery." }));
  }
  const terms = state.partner ? state.partner.paymentTermsDays : 30;
  const rows = [
    ["Invoice number", invoice.number, true],
    ["Issued", invoice.issuedAt ? fmtDate(invoice.issuedAt) : "When the delivery is complete"],
    ["Payment due", invoice.dueDate ? fmtDay(invoice.dueDate) : `${plural(terms, "day")} after delivery`],
  ];
  if (invoice.paidAt) rows.push(["Paid on", fmtDate(invoice.paidAt)]);
  if (invoice.reference) rows.push(["Payment reference", invoice.reference]);
  return h("section", { class: "panel", "aria-labelledby": "invoice-title" },
    h("div", { class: "status-head" },
      h("h2", { id: "invoice-title", text: "Invoice" }),
      chip(INVOICE_STATUS[invoice.status] || { label: invoice.status, tone: "", icon: "credit-card" })),
    h("p", { text: invoice.description }),
    h("dl", { class: "invoice-lines" },
      h("div", {}, h("dt", { text: "Net" }), h("dd", { text: money(invoice.net) })),
      h("div", {}, h("dt", { text: `VAT ${invoice.vatPercent}%` }), h("dd", { text: money(invoice.vat) })),
      h("div", { class: "grand" }, h("dt", { text: "Total" }), h("dd", { text: money(invoice.total) }))),
    h("div", { class: "mt" }, facts(rows)),
    h("p", { class: "note mt" }, icon("info"),
      "This is a pro-forma invoice for the demo. No real payment is taken here; the TürkBizTel team records payments."),
    h("a", { class: "btn secondary small", href: newConcernHash(delivery.id, "billing") }, icon("message"), "Raise a billing concern"));
}

const byLatest = (a, b) => stamp(b.updatedAt) - stamp(a.updatedAt);
const concernChip = (concern) => chip(CONCERN_STATUS[concern.status] || { label: concern.status, tone: "", icon: "message" });

function concernsPanel(delivery) {
  const related = state.concerns.filter((concern) => concern.orderId === delivery.id).sort(byLatest);
  return h("section", { class: "panel", "aria-labelledby": "concerns-title" },
    h("h2", { id: "concerns-title", text: "Concerns" }),
    related.length
      ? h("ul", { class: "mini-list" }, related.map((concern) => h("li", {},
        h("a", { href: concernHash(concern.id), text: concern.subject }), concernChip(concern))))
      : h("p", { class: "hint mb", text: "You haven't raised a concern about this delivery." }),
    h("a", { class: "btn secondary small", href: newConcernHash(delivery.id, "delivery") },
      icon("message"), "Raise a concern about this delivery"));
}

function deliveryView(id) {
  const delivery = deliveryById(id);
  if (!delivery) {
    return ["Delivery not found", [
      back(),
      empty("help", "We can't find that delivery", "It may not belong to your organisation, or the link may be wrong.",
        h("a", { class: "btn", href: "#deliveries", text: "Back to deliveries" }), "h1"),
    ]];
  }
  return [productTitle(delivery), [
    back(),
    h("div", { class: "detail-head" },
      h("div", {},
        h("h1", { tabindex: -1, text: productTitle(delivery) }),
        h("p", { class: "sub" }, h("span", { class: "mono", text: delivery.id }),
          ` · ${delivery.customer} · ${delivery.city} · Ordered ${fmtDate(delivery.createdAt)}`)),
      chip(stateInfo(delivery))),
    h("div", { class: "stack" },
      etaBlock(delivery),
      statusPanel(delivery),
      h("div", { class: "cols" },
        h("div", { class: "stack" }, detailsPanel(delivery), historyPanel(delivery)),
        h("div", { class: "stack" }, invoicePanel(delivery), concernsPanel(delivery)))),
  ]];
}

/* ---- payments ---- */

const termsText = (days) => (days ? `${plural(days, "day")} after delivery` : "On delivery");
const invoiceRank = (invoice) => (INVOICE_STATUS[invoice.status] || { rank: 9 }).rank;
const invoiceChip = (invoice) => chip(INVOICE_STATUS[invoice.status]
  || { label: invoice.status, tone: "", icon: "credit-card" });

function byInvoice(a, b) {
  return (invoiceRank(a) - invoiceRank(b))
    || String(a.dueDate || "9999-12-31").localeCompare(String(b.dueDate || "9999-12-31"))
    || a.number.localeCompare(b.number);
}

function paymentTile(tone, iconName, value, label, sub) {
  return h("div", { class: tone ? `tile static ${tone}` : "tile static" },
    h("span", { class: "tile-icon", "aria-hidden": "true" }, icon(iconName)),
    h("span", {},
      h("span", { class: "num money", text: value }),
      h("span", { class: "tile-label", text: label }),
      sub ? h("span", { class: "tile-sub", text: sub }) : null));
}

function paymentTiles(summary) {
  const next = summary.nextDue;
  return h("div", { class: "tiles four", role: "group", "aria-label": "Payment summary" },
    paymentTile("info", "credit-card", money(summary.outstanding), "Outstanding", "Invoiced and not yet paid"),
    paymentTile(summary.overdueCount ? "bad" : "", "alert", money(summary.overdue), "Overdue",
      summary.overdueCount ? plural(summary.overdueCount, "invoice") : "Nothing overdue"),
    paymentTile("ok", "circle-check", money(summary.paid), "Paid to date"),
    paymentTile("", "calendar", next ? money(next.total) : "None", "Next payment due",
      next ? `${next.number} · due ${fmtDay(next.dueDate)}` : "Nothing due"));
}

function termsNote(summary) {
  const terms = state.partner ? state.partner.paymentTermsDays : 30;
  const coming = summary.scheduled ? ` A further ${money(summary.scheduled)} will be invoiced as your open deliveries complete.` : "";
  return h("p", { class: "note" }, icon("info"), h("span", {},
    `Invoices fall due ${termsText(terms).toLowerCase()}. Amounts are in Turkish lira (TRY) and totals include 20% VAT. `
    + "These are simulated pro-forma invoices: no real payment is taken here, and the TürkBizTel team records payments "
    + `when they arrive.${coming}`));
}

const INVOICE_COLUMNS = [["Invoice", ""], ["Due", ""], ["Net", "amount"], ["VAT", "amount"], ["Total", "amount"], ["Status", ""]];

const paidNote = (invoice) => `Paid ${fmtDate(invoice.paidAt)}${invoice.reference ? ` · ${invoice.reference}` : ""}`;

function invoiceRow(invoice) {
  const product = PRODUCTS[invoice.product] || { name: invoice.product };
  const cell = (label, content, attrs = {}) => h("td", { role: "cell", "data-label": label, ...attrs }, content);
  return h("tr", { role: "row" },
    cell("Invoice", h("div", {},
      h("a", { class: "mono strong", href: deliveryHash(invoice.orderId), text: invoice.number }),
      h("span", { class: "cell-sub", text: `${invoice.customer} · ${product.name}` }))),
    cell("Due", invoice.dueDate ? fmtDay(invoice.dueDate) : "After delivery"),
    cell("Net", money(invoice.net), { class: "amount" }),
    cell("VAT", money(invoice.vat), { class: "amount" }),
    cell("Total", money(invoice.total), { class: "amount strong" }),
    cell("Status", h("div", { class: "status-cell" }, invoiceChip(invoice),
      invoice.paidAt ? h("span", { class: "cell-sub", text: paidNote(invoice) }) : null)),
    cell("", h("a", { class: "btn secondary small", href: newConcernHash(invoice.orderId, "billing") },
      icon("message"), "Raise a concern")));
}

function invoiceTable(invoices) {
  return h("div", { class: "table-wrap" },
    h("table", { class: "invoices", role: "table", "aria-label": "Invoices" },
      h("caption", { class: "sr-only", text: "Your invoices, most urgent first" }),
      h("thead", { role: "rowgroup" }, h("tr", { role: "row" },
        INVOICE_COLUMNS.map(([label, kind]) => h("th", {
          role: "columnheader", scope: "col", class: kind || false, text: label,
        })),
        h("th", { role: "columnheader", scope: "col" }, h("span", { class: "sr-only", text: "Actions" })))),
      h("tbody", { role: "rowgroup" }, invoices.map(invoiceRow))));
}

function paymentsView() {
  const summary = {
    outstanding: 0, overdue: 0, overdueCount: 0, paid: 0, scheduled: 0, nextDue: null, ...state.payments,
  };
  const invoices = [...state.invoices].sort(byInvoice);
  return ["Payments", [
    pageHead("Payments", "What you owe, what you've paid and when each invoice is due."),
    summary.overdueCount
      ? h("div", { class: "banner action", role: "status" },
        icon("alert"),
        h("div", { class: "banner-body" },
          h("strong", { text: `${plural(summary.overdueCount, "invoice")} overdue` }),
          h("p", {
            text: `${money(summary.overdue)} is past its due date. Please arrange payment, or raise a concern if you think `
              + "something is wrong.",
          })),
        h("a", { class: "btn small", href: newConcernHash("", "billing"), text: "Raise a billing concern" }))
      : null,
    paymentTiles(summary),
    termsNote(summary),
    invoices.length
      ? h("section", { class: "panel", "aria-labelledby": "invoices-title" },
        h("h2", { id: "invoices-title", text: "Invoices" }),
        invoiceTable(invoices))
      : empty("credit-card", "No invoices yet", "An invoice is issued for each delivery once it is complete.",
        h("a", { class: "btn secondary", href: "#deliveries", text: "View deliveries" })),
  ]];
}

/* ---- concerns ---- */

const MAX_MESSAGES = 20;

function preview(text, limit = 140) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

function concernCard(concern) {
  const last = concern.messages[concern.messages.length - 1];
  return h("li", { class: "card" },
    h("div", { class: "card-top" },
      h("span", { class: "product-icon", "aria-hidden": "true" }, icon("message")),
      h("div", {},
        h("h2", { class: "card-title" },
          h("a", { class: "card-link", href: concernHash(concern.id), "data-key": `concern-${concern.id}` },
            concern.subject, h("span", { class: "sr-only", text: `, concern ${concern.id}` }))),
        h("p", { class: "card-meta" },
          h("span", { class: "mono", text: concern.id }),
          h("span", { text: labelOf(CATEGORIES, concern.category) }),
          concern.orderId ? h("span", {}, icon("package"), concern.orderId) : null,
          updated(concern.updatedAt))),
      concernChip(concern)),
    last
      ? h("p", { class: "status-line", text: `${last.from === "staff" ? "TürkBizTel" : "You"}: ${preview(last.text)}` })
      : null);
}

function concernGroup(title, concerns) {
  if (!concerns.length) return [];
  return [h("h2", { class: "group-title", text: `${title} (${concerns.length})` }), h("ul", { class: "orders" }, concerns.map(concernCard))];
}

function concernsView() {
  const open = openConcerns().sort(byLatest);
  const resolved = state.concerns.filter((concern) => concern.status === "resolved").sort(byLatest);
  return ["Concerns", [
    pageHead("Concerns", "Tell us if something is wrong and we'll reply right here.",
      h("a", { class: "btn", href: newConcernHash() }, icon("plus"), "Raise a concern")),
    state.concerns.length
      ? [concernGroup("Open concerns", open), concernGroup("Resolved", resolved)]
      : empty("message", "You haven't raised any concerns",
        "If a delivery, an invoice or the service isn't right, tell us here and we'll help.",
        h("a", { class: "btn", href: newConcernHash(), text: "Raise a concern" })),
  ]];
}

function newConcernView(params) {
  const wantedOrder = params.get("order") || "";
  const order = deliveryById(wantedOrder) ? wantedOrder : "";
  const wantedCategory = params.get("category");
  const category = CATEGORIES.some(([key]) => key === wantedCategory) ? wantedCategory : "delivery";
  const categories = select("category", CATEGORIES, category);
  const deliveries = select("orderId", [
    ["", "Not about a specific delivery"],
    ...state.deliveries.map((delivery) => [delivery.id, `${delivery.id} · ${delivery.customer} · ${productOf(delivery).name}`]),
  ], order);
  const subject = input("subject", { maxlength: 120, required: true, autocomplete: "off" });
  const text = textarea("text", { maxlength: 1000, rows: 6, required: true });
  const message = msgBox();
  const form = h("form", { novalidate: true },
    h("div", { class: "row" },
      field("What is it about?", categories),
      field(optional("Related delivery"), deliveries)),
    field("Subject", subject, "A short summary, for example “The installation date has moved”."),
    field("Details", text, "Tell us what happened and what you need from us."),
    message,
    h("div", { class: "inline-form" },
      h("button", { type: "submit", class: "btn", "data-key": "send-concern", text: "Send concern" }),
      h("a", { class: "btn ghost", href: "#concerns", text: "Cancel" })));
  onSubmit(form, message, [
    ["subject", (value) => (value.trim().length < 3 || value.trim().length > 120)
      && "Enter a short subject of 3 to 120 characters."],
    ["text", (value) => {
      if (value.trim().length < 3) return "Tell us a little more so we can help.";
      return value.trim().length > 1000 && "Please keep the details to 1,000 characters or fewer.";
    }],
  ], async () => {
    const created = await api("/api/partner/concerns", {
      method: "POST",
      body: {
        category: categories.value, orderId: deliveries.value || null, subject: subject.value.trim(), text: text.value.trim(),
      },
    });
    toast("Thank you. We've received your concern and will reply here.");
    await load();
    location.hash = concernHash(created.id);
  });
  return ["Raise a concern", [
    back("#concerns", "All concerns"),
    pageHead("Raise a concern", "Tell us what's wrong. A TürkBizTel team member will reply in this conversation."),
    h("section", { class: "panel", "aria-label": "New concern" }, form),
  ]];
}

function bubble(message) {
  return h("li", { class: message.from === "partner" ? "bubble mine" : "bubble" },
    h("div", { class: "bubble-head" },
      h("strong", { text: message.name }),
      h("time", { datetime: message.at, text: fmtStamp(message.at) })),
    h("div", { class: "bubble-text", text: message.text }));
}

function statusToggle(concern, message) {
  const resolved = concern.status === "resolved";
  const button = h("button", {
    type: "button", class: "btn secondary", "data-key": "toggle-status",
    text: resolved ? "Reopen this concern" : "Mark as resolved",
  });
  button.addEventListener("click", () => busy(button, async () => {
    say(message, "");
    try {
      await api(concernPath(concern.id), { method: "PUT", body: { status: resolved ? "open" : "resolved" } });
      toast(resolved ? "This concern is open again." : "Marked as resolved. Send a message any time to reopen it.");
      await refresh();
    } catch (error) {
      if (!(error instanceof ApiError)) console.error(error);
      say(message, failText(error), "error");
    }
  }));
  return button;
}

function replyForm(concern) {
  const resolved = concern.status === "resolved";
  const message = msgBox();
  if (concern.messages.length >= MAX_MESSAGES) {
    return h("div", {},
      h("p", { class: "note" }, icon("info"), h("span", {}, "This conversation is full. ",
        h("a", { href: newConcernHash(concern.orderId || "", concern.category), text: "Raise a new concern" }), " to carry on.")),
      message,
      h("div", { class: "inline-form" }, statusToggle(concern, message)));
  }
  const text = textarea("text", { maxlength: 1000, rows: 3, required: true, "data-key": "reply" });
  const form = h("form", { novalidate: true },
    field(resolved ? "Add a message" : "Your reply", text, resolved ? "Sending a message reopens this concern." : undefined),
    message,
    h("div", { class: "inline-form" },
      h("button", { type: "submit", class: "btn", "data-key": "send-reply", text: "Send message" }),
      statusToggle(concern, message)));
  onSubmit(form, message, [
    ["text", (value) => !value.trim() && "Write a message first."],
  ], async () => {
    await api(concernPath(concern.id, "/messages"), { method: "POST", body: { text: text.value.trim() } });
    toast(resolved ? "Message sent. This concern is open again." : "Message sent.");
    await refresh();
  });
  return form;
}

function concernView(id) {
  const concern = concernById(id);
  if (!concern) {
    return ["Concern not found", [
      back("#concerns", "All concerns"),
      empty("help", "We can't find that concern", "It may not belong to your organisation, or the link may be wrong.",
        h("a", { class: "btn", href: "#concerns", text: "Back to concerns" }), "h1"),
    ]];
  }
  const related = concern.orderId ? deliveryById(concern.orderId) : null;
  return [concern.subject, [
    back("#concerns", "All concerns"),
    h("div", { class: "detail-head" },
      h("div", {},
        h("h1", { tabindex: -1, text: concern.subject }),
        h("p", { class: "sub" }, h("span", { class: "mono", text: concern.id }),
          ` · ${labelOf(CATEGORIES, concern.category)} · Raised ${fmtDate(concern.createdAt)} by ${concern.createdByName}`)),
      concernChip(concern)),
    h("section", { class: "panel", "aria-labelledby": "thread-title" },
      h("div", { class: "status-head" },
        h("h2", { id: "thread-title", text: "Conversation" }),
        h("span", { class: "hint" }, updated(concern.updatedAt))),
      concern.orderId
        ? h("p", { class: "note" }, icon("package"), h("span", {}, "About delivery ",
          h("a", { href: deliveryHash(concern.orderId), text: related ? `${concern.orderId} · ${related.customer}` : concern.orderId })))
        : null,
      h("ol", { class: "thread", "aria-label": "Messages" }, concern.messages.map(bubble)),
      replyForm(concern)),
  ]];
}

/* ---- account ---- */

function profileForm() {
  const account = state.account;
  const message = msgBox();
  const form = h("form", { novalidate: true },
    h("div", { class: "row" },
      field("Your name", input("name", { value: account.name, autocomplete: "name", maxlength: 80, required: true })),
      field(optional("Mobile number"), phoneInput(account.phone))),
    h("div", { class: "row" },
      field("Language", select("language", LANGUAGES, account.language)),
      field("Email address", input("email", {
        type: "email", value: account.email, readonly: true, autocomplete: "email",
      }), "You sign in with this address. To change it, contact your TürkBizTel representative.")),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "save-profile", text: "Save changes" }));
  onSubmit(form, message, [
    ["name", (value) => value.trim().length < 2 && "Enter your name."],
    ["phone", (value) => phoneProblem(value, false)],
  ], async () => {
    const { name, phone, language } = form.elements;
    const body = { name: name.value.trim(), language: language.value };
    const mobile = normalizePhone(phone.value.trim());
    if (mobile) body.phone = mobile;
    await api("/api/partner/profile", { method: "PUT", body });
    toast("Profile saved.");
    await refresh();
  });
  return form;
}

function passwordForm() {
  const email = state.account.email;
  const message = msgBox();
  const form = h("form", { novalidate: true },
    passwordField({ label: "Current password", name: "current", autocomplete: "current-password" }),
    passwordField({
      label: "New password", name: "password", autocomplete: "new-password", meter: true, emailOf: () => email,
    }),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "change-password", text: "Change password" }));
  onSubmit(form, message, [
    ["current", (value) => !value && "Enter your current password."],
    ["password", (value) => {
      const score = passwordScore(value, email);
      return score.level < 2 && score.text;
    }],
  ], async () => {
    await api("/api/partner/password", {
      method: "POST", body: { current: form.elements.current.value, password: form.elements.password.value },
    });
    toast("Password changed. Other devices have been signed out.");
    form.reset();
    // Not bubbling, so this only refreshes the strength meter and does not mark the page as edited.
    form.elements.password.dispatchEvent(new Event("input"));
  });
  return form;
}

function organisationPanel() {
  const partner = state.partner;
  return h("section", { class: "panel", "aria-labelledby": "org-title" },
    h("h2", { id: "org-title", text: "Your organisation" }),
    facts([
      ["Organisation", partner.name],
      ["Payment terms", termsText(partner.paymentTermsDays)],
      ["Currency", "Turkish lira (TRY)"],
      ["Billing email", partner.billingEmail],
      ["Account status", partner.status === "active" ? "Active" : "Switched off"],
    ]));
}

function helpPanel() {
  return h("section", { class: "panel", "aria-labelledby": "help-title" },
    h("h2", { id: "help-title", text: "Need something else?" }),
    h("p", {
      class: "sub",
      text: "To change your organisation's details or add a colleague, raise a concern and the TürkBizTel team will take care of it.",
    }),
    h("a", { class: "btn secondary", href: newConcernHash("", "other") }, icon("message"), "Raise a concern"));
}

function accountView() {
  const account = state.account;
  return ["Account", [
    pageHead("Account", "Your details, sign-in settings and organisation."),
    h("div", { class: "cols" },
      h("div", { class: "stack" },
        h("section", { class: "panel", "aria-labelledby": "details-title" },
          h("h2", { id: "details-title", text: "Your details" }),
          h("div", { class: "profile-head" },
            h("span", { class: "avatar big", "aria-hidden": "true", text: initials(account.name) }),
            h("div", {},
              h("strong", { class: "profile-name", text: account.name }),
              h("p", { class: "sub", text: account.email }),
              chip({ label: state.partner.name, tone: "", icon: "building" }))),
          profileForm()),
        h("section", { class: "panel", "aria-labelledby": "password-title" },
          h("h2", { id: "password-title", text: "Change password" }),
          passwordForm())),
      h("div", { class: "stack" }, organisationPanel(), helpPanel())),
  ]];
}

/* ---- routing and rendering ---- */

function decodeId(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function currentRoute() {
  const hash = location.hash.replace(/^#/, "");
  const cut = hash.indexOf("?");
  const params = new URLSearchParams(cut < 0 ? "" : hash.slice(cut + 1));
  const [section, ...rest] = (cut < 0 ? hash : hash.slice(0, cut)).split("/");
  const raw = rest.join("/");
  const id = raw ? decodeId(raw) : "";
  if (section === "payments") return { name: "payments", nav: "payments" };
  if (section === "account") return { name: "account", nav: "account" };
  if (section === "concerns") {
    if (id === "new") return { name: "newConcern", nav: "concerns", params };
    if (id) return { name: "concern", nav: "concerns", id };
    return { name: "concerns", nav: "concerns" };
  }
  if (section === "deliveries" && id) return { name: "delivery", nav: "deliveries", id };
  return { name: "deliveries", nav: "deliveries" };
}

function buildView(route) {
  if (route.name === "delivery") return deliveryView(route.id);
  if (route.name === "payments") return paymentsView();
  if (route.name === "concerns") return concernsView();
  if (route.name === "newConcern") return newConcernView(route.params);
  if (route.name === "concern") return concernView(route.id);
  if (route.name === "account") return accountView();
  return deliveriesView();
}

/*
 * Paints the page for the current route. `navigated` is true when the partner moved to a different page,
 * which scrolls to the top and moves focus to the heading. Otherwise focus stays on the same control.
 */
function render({ navigated = false } = {}) {
  if (!state.account) return;
  const view = $("#view");
  const active = document.activeElement;
  const hadFocus = Boolean(active && active !== view && view.contains(active));
  const focusKey = hadFocus && active.dataset ? active.dataset.key || "" : "";
  const route = currentRoute();
  const [title, nodes] = buildView(route);
  view.replaceChildren(...[nodes].flat(Infinity).filter(Boolean));
  document.title = `${title} · TürkBizTel Partners`;
  for (const link of $$("[data-nav]")) {
    if (link.dataset.nav === route.nav) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const heading = $("h1", view);
  if (navigated) {
    view.classList.remove("enter");
    void view.offsetWidth;
    view.classList.add("enter");
    window.scrollTo(0, 0);
    if (heading) heading.focus({ preventScroll: true });
  } else if (hadFocus) {
    const target = focusKey ? $(`[data-key="${CSS.escape(focusKey)}"]`, view) : null;
    (target || heading || view).focus({ preventScroll: true });
  }
  if (route.name === "delivery" && $("#timeline-box")) loadTimeline(route.id);
}

/* ---- data ---- */

function fillTopbar() {
  $("#avatar").textContent = initials(state.account.name);
  $("#who-name").textContent = state.account.name;
  $("#who-org").textContent = state.partner.name;
}

function setBadge(key, count, one, many) {
  const badge = $(`#badge-${key}`);
  badge.hidden = count === 0;
  badge.replaceChildren(
    h("span", { "aria-hidden": "true", text: String(count) }),
    h("span", { class: "sr-only", text: ` ${count === 1 ? one : many}` }));
}

function updateBadges() {
  setBadge("deliveries", state.deliveries.filter((delivery) => delivery.needsInput).length,
    "delivery needs your input", "deliveries need your input");
  setBadge("payments", state.payments ? state.payments.overdueCount : 0, "invoice is overdue", "invoices are overdue");
  setBadge("concerns", openConcerns().length, "open concern", "open concerns");
}

/* Stores fresh data and says which parts of it differ from what was on screen. Never touches `dirty`. */
function adopt(data) {
  const sigs = {
    profile: JSON.stringify([data.account, data.partner]),
    deliveries: JSON.stringify(data.deliveries),
    money: JSON.stringify([data.invoices, data.payments]),
    concerns: JSON.stringify(data.concerns),
  };
  const changed = {};
  for (const key of Object.keys(sigs)) changed[key] = sigs[key] !== state.sigs[key];
  Object.assign(state, {
    account: data.account, partner: data.partner, deliveries: data.deliveries,
    invoices: data.invoices, payments: data.payments, concerns: data.concerns, sigs,
  });
  if (changed.profile) fillTopbar();
  if (changed.deliveries || changed.money || changed.concerns) updateBadges();
  return changed;
}

async function load() {
  const data = await api("/api/partner/overview");
  state.dirty = false;
  return adopt(data);
}

async function refresh() {
  try {
    await load();
    render();
  } catch (error) {
    if (!(error instanceof ApiError)) console.error(error);
  }
}

/* ---- live updates ---- */

let polling = false;
let failures = 0;

function showStale() {
  if ($("#stale")) return;
  $("#view").prepend(h("div", { class: "banner info", id: "stale", role: "status" },
    icon("refresh"),
    h("div", { class: "banner-body" },
      h("strong", { text: "This page has new updates" }),
      h("p", { text: "Showing them will reset anything you haven't saved yet." })),
    h("button", {
      type: "button", class: "btn small", text: "Show latest",
      onclick: () => {
        state.dirty = false;
        render();
      },
    })));
}

/* Which fresh data is worth repainting the current page for. A half-written concern is never repainted. */
const isRelevant = (route, changed) => {
  if (route.name === "newConcern") return false;
  if (route.name === "account") return changed.profile;
  if (route.name === "payments") return changed.money || changed.profile;
  if (route.name === "concerns" || route.name === "concern") return changed.concerns;
  if (route.name === "delivery") return changed.deliveries || changed.money || changed.concerns;
  return changed.deliveries || changed.money;
};

async function poll() {
  if (document.hidden || polling || !state.account) return;
  polling = true;
  try {
    const changed = adopt(await api("/api/partner/overview"));
    failures = 0;
    $("#connection").hidden = true;
    if (!isRelevant(currentRoute(), changed)) return;
    const editing = state.dirty || Boolean($("#view [aria-busy=\"true\"]"));
    if (editing) showStale();
    else render();
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      location.reload();
      return;
    }
    if (!(error instanceof ApiError)) console.error(error);
    if (++failures >= 3) $("#connection").hidden = false;
  } finally {
    polling = false;
  }
}

/* ---- sign out ---- */

async function signOut() {
  await busy($("#signout"), async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      toast("We couldn't sign you out. Check your connection and try again.", "error");
      return;
    }
    sessionStorage.removeItem(NOTICE_KEY);
    location.replace("/login");
    await new Promise(() => {});
  });
}

/* ---- start ---- */

const loading = () => h("p", { class: "loading", role: "status" },
  h("span", { class: "spinner", "aria-hidden": "true" }), "Loading your deliveries…");

function failure(text) {
  const retry = h("button", { type: "button", class: "btn", text: "Try again" });
  retry.addEventListener("click", () => {
    $("#view").replaceChildren(loading());
    boot();
  });
  $("#view").replaceChildren(empty("alert", "We couldn't load your partner account", text, retry, "h1"));
}

async function boot() {
  try {
    const me = await api("/api/auth/me");
    if (!me.authenticated) {
      location.replace(signInPath());
      return;
    }
    if (me.role !== "partner") {
      location.replace(HOME[me.role] || "/login");
      return;
    }
    await load();
  } catch (error) {
    if (!(error instanceof ApiError)) console.error(error);
    failure(failText(error));
    return;
  }
  document.body.classList.remove("booting");
  render();
  setInterval(poll, POLL_MS);
  setInterval(tickAgo, 30000);
}

function wire() {
  $("#view").addEventListener("input", (event) => {
    // Searching the list is not an edit: it must never block live updates.
    if (event.target.dataset && event.target.dataset.key === "search") return;
    state.dirty = true;
  });
  window.addEventListener("hashchange", () => {
    state.dirty = false;
    render({ navigated: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
  window.addEventListener("online", poll);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) location.reload();
  });
  // A hash link would be read as a route, so move focus by hand instead.
  $(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    $("#view").focus();
  });
  $("#signout").addEventListener("click", signOut);
}

wire();
boot();
