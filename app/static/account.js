"use strict";

/* The signed-in customer's pages: their orders, a new-order form and their profile. Everything is drawn from
   DOM nodes (see ui.js), so nothing a customer types can ever become markup. */

/* ---- what the server's order states mean to a customer ---- */

const STEPS = ["Received", "Site check", "Planning", "Activation", "Confirmed"];
const STAGE_INDEX = { validation: 1, decomposition: 2, provisioning: 3, fallout: 3, customer_update: 4, done: 5 };
const STEP_TONE = {
  awaitingCustomer: "blocked", awaitingEnterpriseApproval: "paused", held: "paused", pendingApproval: "paused", failed: "failed",
};
const STEP_WORDS = {
  done: "Completed: ", current: "Current step: ", blocked: "Needs your action: ",
  paused: "Waiting: ", failed: "Delayed: ", todo: "Not started: ",
};
const DOT_ICONS = { blocked: "alert", paused: "clock", failed: "x" };

const STATE_INFO = {
  acknowledged: { label: "Received", tone: "", icon: "package" },
  inProgress: { label: "In progress", tone: "info", icon: "refresh" },
  pendingApproval: { label: "Under review", tone: "warn", icon: "shield-check" },
  held: { label: "On hold", tone: "warn", icon: "clock" },
  awaitingCustomer: { label: "Action needed", tone: "action", icon: "alert" },
  awaitingEnterpriseApproval: { label: "Needs your approval", tone: "action", icon: "shield-check" },
  completed: { label: "Completed", tone: "ok", icon: "circle-check" },
  rejected: { label: "Not accepted", tone: "bad", icon: "x" },
  failed: { label: "Delayed", tone: "bad", icon: "alert" },
};
/* An order the company's own contact turned down, or the service team took back, is closed without being a failure. */
const ENDED_INFO = {
  declined: { label: "Declined", tone: "", icon: "x" },
  withdrawn: { label: "Withdrawn", tone: "", icon: "x" },
};
const STAGE_TEXT = {
  validation: "We're checking your installation address and service availability.",
  decomposition: "We're planning the work needed at your site.",
  provisioning: "We're setting up your service.",
  fallout: "We hit a small snag and are fixing it. No action is needed from you.",
  customer_update: "Almost there. We're confirming your activation.",
};

const PRODUCTS = {
  connectivity: {
    name: "Dedicated Internet", detail: "500 Mbps", icon: "globe", blurb: "A fast, private internet line for your office.",
  },
  sdwan: {
    name: "Managed SD-WAN", detail: "200 Mbps", icon: "share-2", blurb: "Connect all your sites securely over one smart network.",
  },
  cloud: {
    name: "Cloud Connect", detail: "1 Gbps", icon: "cloud", blurb: "A private, high-speed link to your cloud platforms.",
  },
};
const CITIES = [["Istanbul", "Istanbul"], ["Ankara", "Ankara"], ["Izmir", "Izmir"]];
const FREQUENCIES = [
  ["every-step", "Every step of the order"], ["daily", "Daily summary"], ["weekly", "Weekly summary"],
  ["critical-only", "Only when action is needed or the order is done"],
];
const LANGUAGES = [["en", "English"], ["tr", "Türkçe"]];
const CHANNELS = [["email", "Email", "mail"], ["sms", "SMS", "phone"]];

const ACTIVE = new Set(["acknowledged", "inProgress", "held", "pendingApproval", "failed"]);
const TERMINAL = new Set(["completed", "rejected", "failed"]);
const POLL_MS = 8000;

const state = {
  account: null, orders: [], mode: "demo", filter: "", accountSig: "", ordersSig: "", dirty: false, moreOpen: false,
  declining: "",
};

/* An enterprise order is placed by the service team and waits for this company's contact; until then nothing starts. */
const awaitingApproval = (order) => order.state === "awaitingEnterpriseApproval";
const approvalExpired = (order) => awaitingApproval(order) && Boolean(order.approval && order.approval.expired);
const needsApproval = (order) => awaitingApproval(order) && !approvalExpired(order);
const approvalEnd = (order) => {
  const status = order.state === "rejected" && order.approval ? order.approval.status : "";
  return status === "declined" || status === "withdrawn" ? status : "";
};

const needsAction = (order) => order.state === "awaitingCustomer" || needsApproval(order);
const isActive = (order) => ACTIVE.has(order.state) || approvalExpired(order);
const isAddressFix = (order) => Boolean(order.action && order.action.type === "address-correction");
const productOf = (order) => PRODUCTS[order.product] || { name: order.product, detail: "", blurb: "", icon: "package" };

function stateInfo(order) {
  const ended = approvalEnd(order);
  if (ended) return ENDED_INFO[ended];
  if (approvalExpired(order)) return { label: "Request expired", tone: "warn", icon: "clock" };
  return STATE_INFO[order.state] || { label: order.state, tone: "", icon: "package" };
}
const plural = (count, one) => `${count} ${one}${count === 1 ? "" : "s"}`;
const labelOf = (options, value) => (options.find(([key]) => key === value) || [value, value || "Not set"])[1];

function productTitle(order) {
  const product = productOf(order);
  return product.detail ? `${product.name} · ${product.detail}` : product.name;
}

const FILTERS = {
  active: {
    label: "Active", tone: "info", icon: "refresh", test: isActive,
    empty: ["Nothing in progress", "Orders that are being set up will appear here."],
  },
  action: {
    label: "Need your action", tone: "action", icon: "alert", test: needsAction,
    empty: ["Nothing needs your attention", "You're all caught up. We'll tell you if we need anything from you."],
  },
  done: {
    label: "Completed", tone: "ok", icon: "circle-check", test: (order) => order.state === "completed",
    empty: ["No completed orders yet", "Orders that are finished will appear here."],
  },
};

/* ---- progress ---- */

/* `index` is the position (from 0) of the step in progress; STEPS.length means every step is done. */
function progressOf(order) {
  if (order.state === "completed") return { index: STEPS.length, tone: "" };
  if (order.state === "rejected") return { index: 1, tone: "" };
  if (awaitingApproval(order)) return { index: 0, tone: "paused" };
  return {
    index: Math.min(STAGE_INDEX[order.stage] ?? 1, STEPS.length - 1),
    tone: STEP_TONE[order.state] || "current",
  };
}

function stepKind(progress, position) {
  if (position < progress.index) return "done";
  return position === progress.index && progress.tone ? progress.tone : "todo";
}

function progressLabel(order) {
  if (order.state === "completed") return `All ${STEPS.length} steps complete`;
  const ended = approvalEnd(order);
  if (ended) return ended === "declined" ? "Order declined" : "Request withdrawn";
  if (order.state === "rejected") return "Order not accepted";
  if (awaitingApproval(order)) return approvalExpired(order) ? "Approval request expired" : "Waiting for your approval";
  const { index } = progressOf(order);
  return `Step ${index + 1} of ${STEPS.length} · ${STEPS[index]}`;
}

function statusText(order) {
  const ended = approvalEnd(order);
  if (ended) {
    return ended === "declined"
      ? "You declined this order. Nothing was provisioned."
      : "Our service team withdrew this request. Nothing was provisioned.";
  }
  switch (order.state) {
    case "completed": return "Your order is complete.";
    case "rejected": return "We couldn't accept this order. Our team will contact you.";
    case "failed": return "Something went wrong with the activation. We're looking into it.";
    case "held": return "Your order is on hold for a scheduled maintenance window. No action needed.";
    case "pendingApproval": return "A service manager is reviewing your order. No action needed from you.";
    case "acknowledged": return "We've received your order and will start shortly.";
    case "awaitingEnterpriseApproval":
      return approvalExpired(order)
        ? "This approval request expired. Ask the TürkBizTel service team to send a new one."
        : "Our service team placed this order for your company. It starts as soon as you approve it.";
    case "awaitingCustomer":
      return isAddressFix(order)
        ? "We need you to check your installation address before we can continue."
        : "We need a little more information from you before we can continue.";
    default: return STAGE_TEXT[order.stage] || "We're working on your order.";
  }
}

function stepper(order) {
  const progress = progressOf(order);
  return h("ol", { class: "stepper", "aria-label": "Order progress" }, STEPS.map((label, position) => {
    const kind = stepKind(progress, position);
    const dot = kind === "done" ? icon("check") : DOT_ICONS[kind] ? icon(DOT_ICONS[kind]) : String(position + 1);
    return h("li", { class: kind, "aria-current": kind === "done" || kind === "todo" ? false : "step" },
      h("span", { class: "dot" }, dot),
      h("span", { class: "label" }, h("span", { class: "sr-only", text: STEP_WORDS[kind] }), label));
  }));
}

function segments(order) {
  const progress = progressOf(order);
  return h("div", { class: "segments", "aria-hidden": "true" }, STEPS.map((_, position) => {
    const kind = stepKind(progress, position);
    return h("i", { class: kind === "todo" ? false : kind });
  }));
}

const chip = (info) => h("span", { class: info.tone ? `chip ${info.tone}` : "chip" }, icon(info.icon), info.label);

/* ---- small shared pieces ---- */

const input = (name, attrs = {}) => h("input", { type: "text", name, ...attrs });
const msgBox = () => h("p", { class: "msg", role: "alert", hidden: true });

function pageHead(title, sub, action) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", { tabindex: -1, text: title }), sub ? h("p", { class: "sub", text: sub }) : null),
    action);
}

function back() {
  return h("a", { class: "back", href: "#orders" }, icon("arrow-left"), "All orders");
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

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return [...crypto.getRandomValues(new Uint8Array(16))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/* Reads an order reference and tracking code out of a pasted tracking link, if that is what was pasted. */
function fromLink(text) {
  try {
    const params = new URLSearchParams(new URL(text.trim(), location.origin).hash.slice(1));
    const orderId = params.get("order");
    const token = params.get("token");
    return orderId && token ? { orderId, token } : null;
  } catch {
    return null;
  }
}

/* Cheap fingerprints, so a poll only repaints the page when something it can show has actually changed. */
const accountSignature = (account) => JSON.stringify(account);

const ordersSignature = (orders) => JSON.stringify(orders.map((order) => [
  order.id, order.state, order.stage, order.updatedAt, (order.updates || []).length,
  order.action && order.action.remindersSent, order.siteAddress, order.city, order.contact,
  order.approval && [order.approval.status, order.approval.expired, order.approval.expiresAt],
]));

/* ---- form pieces ---- */

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
      field("How often should we update you?", select("updateFrequency", FREQUENCIES, contact.updateFrequency || "every-step"))),
    segmented("preferredChannel", "How should we contact you?", CHANNELS, channel, prefix),
  ];
}

function emailProblem(value, required) {
  const text = value.trim();
  if (!text) return required ? "Enter an email address so we can send you updates by email." : "";
  return EMAIL_RX.test(text) ? "" : EMAIL_PROBLEM;
}

function phoneProblem(value, required) {
  const text = normalizePhone(value.trim());
  if (!text) return required ? "Enter a mobile number so we can send you text messages." : "";
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

function companyProblem(value) {
  const text = value.trim();
  if (text.length < 2) return "Enter your company name.";
  if (state.mode !== "live" && !text.startsWith("Demo ")) {
    return "In this demo, company names start with “Demo ”, for example “Demo Bosphorus Foods”.";
  }
  return "";
}

const demoHint = () => (state.mode === "live" ? null : "This is a demo site: company names start with “Demo ”.");

const stuckNote = () => h("p", { class: "note" }, icon("info"),
  "If your order gets stuck, for example because a detail looks incorrect, we message you immediately and "
  + "remind you up to 3 times, whatever frequency you choose.");

const orderPath = (id, suffix = "") => `/api/me/orders/${encodeURIComponent(id)}${suffix}`;
const orderHash = (id) => `#order/${encodeURIComponent(id)}`;
const failText = (error) => (error instanceof ApiError ? error.message : "Something went wrong. Please try again.");

function facts(rows) {
  return h("dl", { class: "facts" }, rows.map(([label, value, mono]) => h("div", {},
    h("dt", { text: label }),
    h("dd", { class: mono ? "mono" : false, text: value || "Not provided" }))));
}

function phoneInput(value) {
  const control = input("phone", {
    type: "tel", value: value || "", autocomplete: "tel", placeholder: "+905550000000", maxlength: 20,
  });
  control.addEventListener("change", () => {
    control.value = normalizePhone(control.value.trim());
  });
  return control;
}

/* ---- email confirmation ---- */

const CODE_PROBLEM = "Enter the 6-digit code from your email.";

function clearCodeNotes() {
  sessionStorage.removeItem(DEMO_CODE_KEY);
  sessionStorage.removeItem(MAIL_PROBLEM_KEY);
}

function repaintVerifyBox() {
  const old = $("#verify-box");
  if (!old) return;
  const fresh = verifyBox();
  keepFocus(() => (fresh ? old.replaceWith(fresh) : old.remove()));
}

async function resendCode(message) {
  say(message, "");
  try {
    const result = await api("/api/auth/resend", { method: "POST" });
    if (result.verified) {
      await refresh();
    } else if (result.simulatedCode) {
      sessionStorage.setItem(DEMO_CODE_KEY, result.simulatedCode);
      sessionStorage.removeItem(MAIL_PROBLEM_KEY);
      repaintVerifyBox();
    } else if (result.delivery === "unavailable") {
      sessionStorage.setItem(MAIL_PROBLEM_KEY, "1");
      say(message, "We couldn't send the email right now. Please try again later.", "error");
    } else {
      sessionStorage.removeItem(MAIL_PROBLEM_KEY);
      say(message, "A new code is on its way. Check your inbox and your junk folder.", "ok");
    }
  } catch (error) {
    say(message, failText(error), "error");
  }
}

function verifyBox() {
  if (!state.account || state.account.verified) return null;
  const demoCode = state.mode === "live" ? "" : sessionStorage.getItem(DEMO_CODE_KEY) || "";
  const mailProblem = !demoCode && Boolean(sessionStorage.getItem(MAIL_PROBLEM_KEY));
  const code = h("input", {
    type: "text", name: "code", class: "code-input", inputmode: "numeric", autocomplete: "one-time-code",
    maxlength: 6, placeholder: "000000", spellcheck: "false", "aria-label": "6-digit code",
  });
  const message = msgBox();
  const resend = h("button", { type: "button", class: "btn secondary", "data-key": "resend-code", text: "Send a new code" });
  resend.addEventListener("click", () => busy(resend, () => resendCode(message)));
  const form = h("form", { class: "inline-form", novalidate: true },
    code,
    h("button", { type: "submit", class: "btn", "data-key": "confirm-code", text: "Confirm email" }),
    resend,
    message);
  onSubmit(form, message, [["code", (value) => !/^[0-9]{6}$/.test(value.trim()) && CODE_PROBLEM]], async () => {
    const result = await api("/api/auth/verify", { method: "POST", body: { code: code.value.trim() } });
    clearCodeNotes();
    toast(result.claimed
      ? `Email confirmed. We added ${plural(result.claimed, "earlier order")} to your account.`
      : "Email confirmed. Thank you.");
    await refresh();
  });
  return h("section", { class: "banner info", id: "verify-box", "aria-labelledby": "verify-title" },
    icon("mail"),
    h("div", { class: "banner-body" },
      h("strong", { id: "verify-title", text: "Confirm your email address" }),
      h("p", {}, state.mode !== "live" ? "Enter the simulated 6-digit code for " : "Enter the 6-digit code we emailed to ", h("b", { text: state.account.email }),
        ". It secures your account and brings in any orders you placed before signing up."),
      demoCode
        ? h("p", {}, "This is a demo site, so no email is sent. Your code is ", h("span", { class: "demo-code mono", text: demoCode }))
        : null,
      mailProblem ? h("p", { text: "We couldn't send that email just now. Choose “Send a new code” to try again." }) : null,
      h("p", { class: "hint", text: state.mode !== "live" ? "Codes work for 15 minutes. Generate a new simulated code if needed." : "Codes work for 15 minutes. Can't find it? Check your junk folder or send a new code." }),
      form));
}

/* ---- my orders ---- */

const byNewest = (a, b) => (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0);

const sortedOrders = (orders) => [...orders].sort(
  (a, b) => Number(needsAction(b)) - Number(needsAction(a)) || byNewest(a, b));

function orderCard(order) {
  const urgent = needsAction(order);
  return h("li", { class: urgent ? "card needs-action" : "card" },
    h("div", { class: "card-top" },
      h("span", { class: "product-icon", "aria-hidden": "true" }, icon(productOf(order).icon)),
      h("div", {},
        h("h2", { class: "card-title" },
          h("a", { class: "card-link", href: orderHash(order.id), "data-key": `order-${order.id}` },
            productTitle(order), h("span", { class: "sr-only", text: `, order ${order.id}` }))),
        h("p", { class: "card-meta" },
          h("span", { class: "mono", text: order.id }),
          h("span", {}, icon("map-pin"), order.city),
          h("span", {}, icon("calendar"), `Ordered ${fmtDate(order.createdAt)}`))),
      chip(stateInfo(order))),
    h("div", { class: "progress" },
      segments(order),
      h("p", { class: "progress-text" }, h("span", { text: progressLabel(order) }), updated(order.updatedAt))),
    h("p", { class: "status-line", text: statusText(order) }),
    urgent
      ? h("p", { class: "card-cta", "aria-hidden": "true" }, needsApproval(order) ? "Review now" : "Fix now", icon("arrow-right"))
      : null);
}

function tiles() {
  return h("div", { class: "tiles", role: "group", "aria-label": "Filter orders by status" },
    Object.entries(FILTERS).map(([key, filter]) => h("button", {
      type: "button",
      class: `tile ${filter.tone}`,
      "aria-pressed": String(state.filter === key),
      "data-key": `tile-${key}`,
      onclick: () => {
        state.filter = state.filter === key ? "" : key;
        paintList({ announce: true });
      },
    },
    h("span", { class: "tile-icon", "aria-hidden": "true" }, icon(filter.icon)),
    h("span", {},
      h("span", { class: "num", text: String(state.orders.filter(filter.test).length) }),
      h("span", { class: "tile-label", text: filter.label })))));
}

function showAllOrders() {
  const previous = state.filter;
  state.filter = "";
  paintList({ announce: true, fallback: `tile-${previous}` });
}

function listParts() {
  if (!state.orders.length) {
    return [empty("package", "No orders yet", "When you place an order, you can follow every step of it here.",
      h("a", { class: "btn", href: "#new" }, icon("plus"), "Place your first order"))];
  }
  const filter = FILTERS[state.filter];
  const shown = sortedOrders(filter ? state.orders.filter(filter.test) : state.orders);
  if (shown.length) return [tiles(), h("ul", { class: "orders" }, shown.map(orderCard))];
  return [tiles(), empty(filter.icon, filter.empty[0], filter.empty[1],
    h("button", { type: "button", class: "btn secondary", "data-key": "show-all", text: "Show all orders", onclick: showAllOrders }))];
}

/* Repaints only the list, so anything typed elsewhere on the page is left alone. */
function paintList({ announce = false, fallback = "" } = {}) {
  const box = $("#orders-list");
  if (!box) return;
  keepFocus(() => box.replaceChildren(...listParts()), box, fallback);
  const note = $("#orders-announcer");
  if (!announce || !note) return;
  const filter = FILTERS[state.filter];
  const total = state.orders.length;
  note.textContent = filter
    ? `Showing ${state.orders.filter(filter.test).length} of ${total} orders (${filter.label.toLowerCase()}).`
    : `Showing all ${total} orders.`;
}

function attentionBanner() {
  const waiting = state.orders.filter(needsAction);
  if (!waiting.length) return null;
  const several = waiting.length > 1;
  const approvals = waiting.filter(needsApproval).length;
  const onlyApprovals = approvals === waiting.length;
  const text = !approvals
    ? "Something we need from you is missing or looks wrong. It only takes a minute to put right."
    : onlyApprovals
      ? `Our service team placed ${several ? "these orders" : "this order"} for your company. `
        + `Nothing starts until you approve ${several ? "them" : "it"}.`
      : "Some orders are waiting for your approval and others need a detail put right. "
        + "Nothing waiting for approval starts until you approve it.";
  return h("div", { class: "banner action", role: "status" },
    icon(onlyApprovals ? "shield-check" : "alert"),
    h("div", { class: "banner-body" },
      h("strong", { text: `${plural(waiting.length, "order")} ${several ? "need" : "needs"} your ${onlyApprovals ? "approval" : "attention"}` }),
      h("p", { text })),
    several
      ? h("button", {
        type: "button", class: "btn small", "data-key": "show-attention", text: "Show them",
        onclick: () => {
          state.filter = "action";
          paintList({ announce: true });
        },
      })
      : h("a", { class: "btn small", href: orderHash(waiting[0].id), text: needsApproval(waiting[0]) ? "Review" : "Fix now" }));
}

function linkBox() {
  const reference = input("orderId", {
    placeholder: "AMA-ABC1234567", maxlength: 300, autocomplete: "off", autocapitalize: "characters", spellcheck: "false",
  });
  const code = input("trackingToken", { maxlength: 100, autocomplete: "off", autocapitalize: "none", spellcheck: "false" });
  reference.addEventListener("input", () => {
    const pasted = fromLink(reference.value);
    if (!pasted) return;
    reference.value = pasted.orderId;
    code.value = pasted.token;
  });
  const message = msgBox();
  const form = h("form", { novalidate: true },
    h("div", { class: "row" },
      field("Order reference", reference, "Tip: you can paste your whole tracking link here."),
      field("Tracking code", code)),
    message,
    h("button", { type: "submit", class: "btn secondary", text: "Add this order" }));
  onSubmit(form, message, [
    ["orderId", (value) => !/^AMA-[A-Z0-9]{10}$/.test(value.trim().toUpperCase()) && "Order references look like AMA-ABC1234567."],
    ["trackingToken", (value) => value.trim().length < 10 && "Enter the tracking code from your order confirmation."],
  ], async () => {
    const order = await api("/api/me/orders/link", {
      method: "POST", body: { orderId: reference.value.trim().toUpperCase(), trackingToken: code.value.trim() },
    });
    toast("Order added to your account.");
    await load();
    location.hash = orderHash(order.id);
  });
  const box = h("details", { class: "more" },
    h("summary", {}, icon("link"), "Placed an order without an account?"),
    h("div", { class: "more-body" },
      h("p", { class: "hint", text: "Add it here with the order reference and tracking code from your confirmation, and you can follow it from your account." }),
      form));
  box.open = state.moreOpen;
  box.addEventListener("toggle", () => {
    state.moreOpen = box.open;
  });
  return box;
}

function ordersView() {
  return ["My orders", [
    pageHead("My orders", `Hello, ${firstName(state.account.name)}. Here's where everything stands.`,
      h("a", { class: "btn", href: "#new" }, icon("plus"), "New order")),
    verifyBox(),
    attentionBanner(),
    h("div", { id: "orders-list" }, listParts()),
    h("p", { class: "sr-only", id: "orders-announcer", role: "status" }),
    linkBox(),
  ]];
}

/* ---- one order ---- */

function statusPanel(order) {
  return h("section", { class: "panel", "aria-labelledby": "status-title" },
    h("div", { class: "status-head" },
      h("h2", { id: "status-title", text: "Where your order is" }),
      h("span", { class: "hint" }, updated(order.updatedAt))),
    stepper(order),
    h("p", { class: "status-text", text: statusText(order) }));
}

function addressFix(order) {
  const reminders = (order.action && order.action.remindersSent) || 0;
  const city = select("city", CITIES, order.city);
  const address = input("siteAddress", {
    value: order.siteAddress || "", autocomplete: "street-address", maxlength: 200, required: true,
  });
  const message = msgBox();
  const form = h("form", { novalidate: true },
    field("City", city),
    field("Installation address", address, "Include the street, district and city."),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "fix-address", text: "Save the new address" }));
  onSubmit(form, message, [
    ["siteAddress", (value) => value.trim().length < 5 && "Enter the installation address, including the district and city."],
  ], async () => {
    await api(orderPath(order.id, "/site"), { method: "POST", body: { city: city.value, siteAddress: address.value.trim() } });
    toast("Thank you. Your address was updated and your order is moving again.");
    await refresh();
  });

  const confirmMessage = msgBox();
  const confirm = h("button", { type: "button", class: "btn secondary", "data-key": "confirm-site", text: `Yes, it's in ${order.city}` });
  confirm.addEventListener("click", () => busy(confirm, async () => {
    say(confirmMessage, "");
    try {
      await api(orderPath(order.id, "/confirm-site"), { method: "POST" });
      toast("Thank you. Your order is moving again.");
      await refresh();
    } catch (error) {
      say(confirmMessage, failText(error), "error");
    }
  }));

  return h("section", { class: "panel action", "aria-labelledby": "action-title" },
    h("h2", { id: "action-title", text: "We need you to check your installation address" }),
    h("p", {
      class: "sub",
      text: `Your order is paused because the address we have doesn't mention ${order.city}. `
        + "It takes a minute to fix, and we'll carry on straight away.",
    }),
    facts([["Address on file", order.siteAddress]]),
    reminders ? h("p", { class: "hint", text: `We've sent you ${plural(reminders, "reminder")} about this.` }) : null,
    h("div", { class: "row" },
      h("div", { class: "option" },
        h("h3", { text: "The address is wrong or incomplete" }),
        h("p", { text: "Enter the correct address and we'll use it from now on." }),
        form),
      h("div", { class: "option" },
        h("h3", { text: "The address is right" }),
        h("p", { text: `If the site really is in ${order.city}, just confirm and we'll continue.` }),
        confirmMessage,
        confirm)));
}

/* ---- an order the service team placed for this company: its contact decides whether it starts ---- */

let answering = false;

async function decideApproval(order, decision, message) {
  if (answering) return;
  answering = true;
  say(message, "");
  try {
    await api(orderPath(order.id, "/approval"), { method: "POST", body: { decision } });
  } catch (error) {
    answering = false;
    if (!(error instanceof ApiError && error.status === 409)) {
      say(message, failText(error), "error");
      return;
    }
    // Someone answered, withdrew or renewed the request meanwhile: show how it stands now.
    toast(error.message, "error");
    await refresh();
    return;
  }
  answering = false;
  state.declining = "";
  toast(decision === "approve"
    ? "Thank you. Your order is approved and our agents are starting on it now."
    : "You declined this order. Nothing was provisioned.");
  await refresh();
}

function approvalFacts(order) {
  const { requestedBy, requestedAt, expiresAt } = order.approval || {};
  return facts([
    ["Service", productTitle(order)],
    ["City", order.city],
    ["Placed by", requestedBy ? `${requestedBy}, TürkBizTel service team` : "TürkBizTel service team"],
    ["Requested", fmtStamp(requestedAt)],
    ["Please answer by", fmtStamp(expiresAt)],
  ]);
}

function approvalPanel(order) {
  if (approvalExpired(order)) {
    return h("section", { class: "panel", "aria-labelledby": "action-title" },
      h("h2", { id: "action-title", text: "This approval request has expired" }),
      h("p", { class: "sub", text: `Please ask the TürkBizTel service team to send a new request and mention order ${order.id}.` }));
  }
  const { requestedBy } = order.approval || {};
  const message = msgBox();
  const area = h("div", { class: "decision" });

  const approve = h("button", {
    type: "button", class: "btn", "data-key": "approve-order",
    onclick: (event) => busy(event.currentTarget, () => decideApproval(order, "approve", message)),
  }, icon("circle-check"), "Approve this order");
  const decline = h("button", {
    type: "button", class: "btn secondary", "data-key": "decline-order", onclick: () => ask(true),
  }, icon("x"), "Decline this order");
  const choices = h("div", { class: "decision-row" }, approve, decline);

  const confirmBox = h("div", {
    class: "decision-confirm", role: "group", tabindex: "-1", "aria-labelledby": "decline-question", "data-key": "decline-box",
    onkeydown: (event) => {
      if (event.key === "Escape") ask(false);
    },
  },
  h("p", { id: "decline-question", text: "Decline this order? Nothing will be set up and the order will be closed." }),
  h("div", { class: "decision-row" },
    h("button", {
      type: "button", class: "btn", "data-key": "confirm-decline",
      onclick: (event) => busy(event.currentTarget, () => decideApproval(order, "decline", message)),
    }, "Yes, decline it"),
    h("button", { type: "button", class: "btn ghost", "data-key": "cancel-decline", onclick: () => ask(false) }, "Go back")));

  function ask(on) {
    if (answering) return;
    state.declining = on ? order.id : "";
    say(message, "");
    area.replaceChildren(on ? confirmBox : choices);
    (on ? confirmBox : decline).focus({ preventScroll: true });
  }

  // A repaint (a poll, say) must not close a question the customer is in the middle of answering.
  area.append(state.declining === order.id ? confirmBox : choices);

  return h("section", { class: "panel action", "aria-labelledby": "action-title" },
    h("h2", { id: "action-title", text: "Please approve this order" }),
    h("p", {
      class: "sub",
      text: `${requestedBy ? `${requestedBy} from the TürkBizTel service team` : "The TürkBizTel service team"} `
        + `placed this order for ${order.customer}. Nothing is set up until you approve it.`,
    }),
    approvalFacts(order),
    area,
    message,
    h("p", {
      class: "hint mt",
      text: `Not expecting this order? Choose Decline, or contact the TürkBizTel service team and mention order ${order.id}.`,
    }));
}

function actionPanel(order) {
  if (awaitingApproval(order)) return approvalPanel(order);
  if (!needsAction(order)) return null;
  if (isAddressFix(order)) return addressFix(order);
  return h("section", { class: "panel action", "aria-labelledby": "action-title" },
    h("h2", { id: "action-title", text: "We need a little more information" }),
    h("p", {
      class: "sub",
      text: `Please contact the TürkBizTel service team and mention order ${order.id}. We'll carry on as soon as we hear from you.`,
    }));
}

function updatesPanel(order) {
  const turkish = order.language === "tr";
  const items = [...(order.updates || [])].reverse();
  return h("section", { class: "panel", "aria-labelledby": "updates-title" },
    h("h2", { id: "updates-title", text: "Updates" }),
    items.length
      ? h("ol", { class: "timeline" }, items.map((update) => h("li", {},
        h("time", { class: "when", datetime: update.at, text: fmtStamp(update.at) }),
        h("div", { class: "what", lang: turkish ? "tr" : "en", text: (turkish ? update.tr : update.en) || update.en || update.tr || "" }))))
      : h("p", { class: "hint", text: "No updates yet. We'll post them here as your order moves along." }));
}

function detailsPanel(order) {
  return h("section", { class: "panel", "aria-labelledby": "details-title" },
    h("h2", { id: "details-title", text: "Order details" }),
    facts([
      ["Reference", order.id, true],
      ["Service", productTitle(order)],
      ["Company", order.customer],
      ["City", order.city],
      ["Installation address", order.siteAddress],
      ["Ordered", fmtDate(order.createdAt)],
    ]));
}

function contactPanel(order) {
  const contact = order.contact;
  const title = h("h2", { id: "contact-title", text: "How we keep you informed" });
  const panel = (...children) => h("section", { class: "panel", "aria-labelledby": "contact-title" }, title, ...children);
  if (TERMINAL.has(order.state)) {
    return panel(
      h("p", { class: "note" }, icon("info"), "This order is closed, so its contact preferences can no longer be changed."),
      contact
        ? facts([
          ["Contact person", contact.contactName],
          ["Email address", contact.email],
          ["Mobile number", contact.phone],
          ["Contact by", labelOf(CHANNELS, contact.preferredChannel)],
          ["Updates", labelOf(FREQUENCIES, contact.updateFrequency)],
        ])
        : h("p", { class: "hint", text: "No contact details on file." }));
  }
  const account = state.account;
  const current = contact || {
    contactName: account.name, email: account.email, phone: account.phone, preferredChannel: "email", updateFrequency: "every-step",
  };
  const message = msgBox();
  const form = h("form", { novalidate: true },
    contactFields(current, `contact-${order.id}`),
    stuckNote(),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "save-contact", text: "Save contact details" }));
  onSubmit(form, message, contactSpecs, async () => {
    await api(orderPath(order.id, "/contact"), { method: "PUT", body: contactBody(form) });
    toast("Contact details saved.");
    await refresh();
  });
  return panel(form);
}

function orderView(id) {
  const order = state.orders.find((item) => item.id === id);
  if (!order) {
    return ["Order not found", [
      back(),
      empty("help", "We can't find that order", "It may belong to a different account, or the link may be wrong.",
        h("a", { class: "btn", href: "#orders", text: "Back to my orders" }), "h1"),
    ]];
  }
  return [productTitle(order), [
    back(),
    h("div", { class: "detail-head" },
      h("div", {},
        h("h1", { tabindex: -1, text: productTitle(order) }),
        h("p", { class: "sub" }, h("span", { class: "mono", text: order.id }), ` · ${order.city} · Ordered ${fmtDate(order.createdAt)}`)),
      chip(stateInfo(order))),
    h("div", { class: "stack" },
      statusPanel(order),
      actionPanel(order),
      h("div", { class: "cols" }, updatesPanel(order), detailsPanel(order)),
      contactPanel(order)),
  ]];
}

/* ---- new order ---- */

function choiceCards() {
  return Object.entries(PRODUCTS).map(([value, product]) => {
    const id = `product-${value}`;
    return [
      h("input", { class: "choice-input", type: "radio", name: "product", id, value }),
      h("label", { class: "choice", for: id },
        h("span", { class: "product-icon", "aria-hidden": "true" }, icon(product.icon)),
        h("span", { class: "choice-name", text: product.name }),
        h("span", { class: "choice-detail", text: product.detail }),
        h("span", { class: "choice-blurb", text: product.blurb }),
        h("span", { class: "check", "aria-hidden": "true" }, icon("check"))),
    ];
  });
}

function newOrderView() {
  const account = state.account;
  const contact = {
    contactName: account.name, email: account.email, phone: account.phone,
    preferredChannel: account.email || !account.phone ? "email" : "sms", updateFrequency: "every-step",
  };
  const message = msgBox();
  const form = h("form", { class: "stack", novalidate: true },
    h("section", { class: "panel", "aria-labelledby": "service-title" },
      h("h2", { id: "service-title", text: "1. Choose a service" }),
      h("fieldset", { class: "choices", "aria-labelledby": "service-title" }, choiceCards())),
    h("section", { class: "panel", "aria-labelledby": "site-title" },
      h("h2", { id: "site-title", text: "2. Installation site" }),
      h("div", { class: "row" },
        field("Company", input("customer", {
          value: account.company || "", autocomplete: "organization", maxlength: 80, required: true,
        }), demoHint()),
        field("City", select("city", [["", "Choose a city"], ...CITIES], "", { required: true }))),
      field("Installation address", input("siteAddress", {
        autocomplete: "street-address", maxlength: 200, required: true,
      }), "Include the street, district and city.")),
    h("section", { class: "panel", "aria-labelledby": "contact-title" },
      h("h2", { id: "contact-title", text: "3. Who should we keep informed?" }),
      contactFields(contact, "new"),
      stuckNote()),
    h("div", {},
      message,
      h("div", { class: "button-row" },
        h("button", { type: "submit", class: "btn", "data-key": "place-order", text: "Place order" }),
        h("a", { class: "btn ghost", href: "#orders", text: "Cancel" }))));

  let key = newKey();
  let sent = "";
  onSubmit(form, message, [
    ["product", (value) => !value && "Choose a service."],
    ["customer", companyProblem],
    ["city", (value) => !value && "Choose the city of your site."],
    ["siteAddress", (value) => value.trim().length < 5 && "Enter the installation address, including the district and city."],
    ...contactSpecs,
  ], async () => {
    const { customer, city, product, siteAddress } = form.elements;
    const body = {
      customer: customer.value.trim(),
      city: city.value,
      product: product.value,
      language: account.language,
      siteAddress: siteAddress.value.trim(),
      contact: contactBody(form),
    };
    const fingerprint = JSON.stringify(body);
    if (sent && sent !== fingerprint) key = newKey();
    sent = fingerprint;
    const order = await api("/api/me/orders", { method: "POST", body: { ...body, idempotencyKey: key } });
    toast("Order placed. You can follow it here.");
    await load();
    location.hash = orderHash(order.id);
  });
  return ["New order", [pageHead("New order", "Tell us what you need and where. We'll take it from there."), form]];
}

/* ---- profile ---- */

function detailsForm() {
  const account = state.account;
  const message = msgBox();
  const form = h("form", { novalidate: true },
    h("div", { class: "row" },
      field("Your name", input("name", { value: account.name, autocomplete: "name", maxlength: 80, required: true })),
      field("Company", input("company", {
        value: account.company, autocomplete: "organization", maxlength: 80, required: true,
      }), demoHint())),
    h("div", { class: "row" },
      field(optional("Mobile number"), phoneInput(account.phone)),
      field("Language", select("language", LANGUAGES, account.language))),
    field("Email address", input("email", {
      type: "email", value: account.email, readonly: true, autocomplete: "email",
    }), "This is the address you sign in with. To change it, contact the service team."),
    message,
    h("button", { type: "submit", class: "btn", "data-key": "save-profile", text: "Save changes" }));
  onSubmit(form, message, [
    ["name", (value) => value.trim().length < 2 && "Enter your name."],
    ["company", companyProblem],
    ["phone", (value) => phoneProblem(value, false)],
  ], async () => {
    const { name, company, phone, language } = form.elements;
    const body = { name: name.value.trim(), company: company.value.trim(), language: language.value };
    const mobile = normalizePhone(phone.value.trim());
    if (mobile) body.phone = mobile;
    await api("/api/me/profile", { method: "PUT", body });
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
    await api("/api/me/password", {
      method: "POST", body: { current: form.elements.current.value, password: form.elements.password.value },
    });
    toast("Password changed. Other devices have been signed out.");
    form.reset();
    // Not bubbling, so this only refreshes the strength meter and does not mark the page as edited.
    form.elements.password.dispatchEvent(new Event("input"));
  });
  return form;
}

function profileView() {
  const account = state.account;
  const confirmed = account.verified
    ? { label: "Email confirmed", tone: "ok", icon: "circle-check" }
    : { label: "Email not confirmed", tone: "warn", icon: "alert" };
  return ["Profile", [
    pageHead("Profile", "Your details and sign-in settings."),
    verifyBox(),
    h("div", { class: "cols" },
      h("section", { class: "panel", "aria-labelledby": "details-title" },
        h("h2", { id: "details-title", text: "Your details" }),
        h("div", { class: "profile-head" },
          h("span", { class: "avatar big", "aria-hidden": "true", text: initials(account.name) }),
          h("div", {},
            h("strong", { class: "profile-name", text: account.name }),
            h("p", { class: "sub", text: account.email }),
            chip(confirmed))),
        detailsForm()),
      h("section", { class: "panel", "aria-labelledby": "password-title" },
        h("h2", { id: "password-title", text: "Change password" }),
        passwordForm())),
  ]];
}

/* ---- routing and rendering ---- */

function currentRoute() {
  const hash = location.hash.replace(/^#/, "");
  if (hash === "new") return { name: "new", nav: "new" };
  if (hash === "profile") return { name: "profile", nav: "profile" };
  if (hash.startsWith("order/")) {
    let id = "";
    try {
      id = decodeURIComponent(hash.slice("order/".length));
    } catch {
      id = "";
    }
    return { name: "order", nav: "orders", id };
  }
  return { name: "orders", nav: "orders" };
}

function buildView(route) {
  if (route.name === "new") return newOrderView();
  if (route.name === "profile") return profileView();
  if (route.name === "order") return orderView(route.id);
  return ordersView();
}

/*
 * Paints the page for the current route. `navigated` is true when the customer moved to a different page,
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
  document.title = `${title} · TürkBizTel`;
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
}

/* ---- data ---- */

function fillTopbar() {
  $("#avatar").textContent = initials(state.account.name);
  $("#who-name").textContent = state.account.name;
}

function updateBadge() {
  const badge = $("#badge");
  const count = state.orders.filter(needsAction).length;
  badge.hidden = count === 0;
  badge.replaceChildren(
    h("span", { "aria-hidden": "true", text: String(count) }),
    h("span", { class: "sr-only", text: ` ${count === 1 ? "order needs" : "orders need"} your action` }));
}

/* Stores fresh data and says which parts of it differ from what was on screen. Never touches `dirty`. */
function adopt(data) {
  const accountSig = accountSignature(data.account);
  const ordersSig = ordersSignature(data.orders);
  const changed = { account: accountSig !== state.accountSig, orders: ordersSig !== state.ordersSig };
  state.account = data.account;
  state.orders = data.orders;
  state.accountSig = accountSig;
  state.ordersSig = ordersSig;
  if (changed.account) fillTopbar();
  if (changed.orders) updateBadge();
  return changed;
}

async function load() {
  const data = await api("/api/me/orders");
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

const isRelevant = (route, changed) => {
  if (route.name === "new") return false;
  if (route.name === "profile") return changed.account;
  return changed.account || changed.orders;
};

async function poll() {
  if (document.hidden || polling || !state.account) return;
  polling = true;
  try {
    const changed = adopt(await api("/api/me/orders"));
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
    clearCodeNotes();
    location.replace("/login");
    await new Promise(() => {});
  });
}

/* ---- start ---- */

const loading = () => h("p", { class: "loading", role: "status" },
  h("span", { class: "spinner", "aria-hidden": "true" }), "Loading your orders…");

function failure(text) {
  const retry = h("button", { type: "button", class: "btn", text: "Try again" });
  retry.addEventListener("click", () => {
    $("#view").replaceChildren(loading());
    boot();
  });
  $("#view").replaceChildren(empty("alert", "We couldn't load your account", text, retry, "h1"));
}

async function boot() {
  try {
    const me = await api("/api/auth/me");
    if (!me.authenticated) {
      location.replace("/login");
      return;
    }
    if (me.role !== "customer") {
      location.replace(HOME[me.role] || "/login");
      return;
    }
    state.mode = me.mode === "live" ? "live" : "demo";
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
  $("#view").addEventListener("input", () => {
    state.dirty = true;
  });
  window.addEventListener("hashchange", () => {
    state.dirty = false;
    state.declining = "";
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
