"use strict";

/* Shared helpers for the TürkBizTel sign-in, customer and partner pages. Page content is built from DOM nodes and
   textContent only, never from HTML strings, so customer-supplied text can never become markup. */

/* Where each kind of signed-in person lands. */
const HOME = { admin: "/console", partner: "/partner", customer: "/account" };
const NOTICE_KEY = "myturkei-notice";
const SESSION_KEY = "myturkei-session";
const DEMO_CODE_KEY = "myturkei-demo-code";
const MAIL_PROBLEM_KEY = "myturkei-mail-problem";
const EMAIL_RX = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;
const PHONE_RX = /^\+[1-9][0-9]{7,14}$/;
const EMAIL_PROBLEM = "Enter a valid email address, like name@company.com.";
const PHONE_PROBLEM = "Enter the mobile number in international format, like +905551234567.";
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  node.append(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

function icon(name, extra = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", `icon ${extra}`.trim());
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `/static/turkbiztel-icons.svg#${name}`);
  svg.append(use);
  return svg;
}

/* ---- talking to the server ---- */

class ApiError extends Error {
  constructor(message, status, retryAfter = 0) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

const FIELD_LABELS = {
  name: "Your name", company: "Company", email: "Email address", phone: "Mobile number", password: "Password",
  current: "Current password", code: "The code", city: "City", product: "Service", siteAddress: "Installation address",
  contactName: "Contact person", customer: "Company", orderId: "Order reference", trackingToken: "Tracking code",
  preferredChannel: "Update channel", updateFrequency: "Update frequency", language: "Language", token: "Access token",
  subject: "Subject", text: "Your message", category: "Category", paymentTermsDays: "Payment terms",
  billingEmail: "Billing email", accessNotes: "Access notes", poReference: "PO reference",
  preferredDate: "Preferred delivery date",
};
const PATTERN_HINTS = {
  email: "Enter a valid email address, like name@company.com.",
  phone: "Enter the mobile number in international format, like +905551234567.",
  code: "Enter the 6-digit code from your email.",
  orderId: "Order references look like AMA-ABC1234567.",
};

function problemText(item) {
  const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
  const label = FIELD_LABELS[field] || "This field";
  const ctx = item.ctx || {};
  switch (item.type) {
    case "missing":
      return `${label} is required.`;
    case "string_too_short":
      return ctx.min_length > 1 ? `${label} needs at least ${ctx.min_length} characters.` : `${label} is required.`;
    case "string_too_long":
      return `${label} can be at most ${ctx.max_length} characters.`;
    case "string_pattern_mismatch":
      return PATTERN_HINTS[field] || `${label} isn't in the right format.`;
    case "literal_error":
      return field === "consent"
        ? "Please tick the box to agree before you continue."
        : `Please choose one of the available options for ${label.toLowerCase()}.`;
    case "value_error":
      return String(item.msg || "").replace(/^Value error, /, "") || `${label} isn't valid.`;
    default:
      return `${label} isn't valid.`;
  }
}

function friendly(data, status) {
  if (status >= 500) return "Something went wrong on our side. Please try again in a moment.";
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = [...new Set(detail.map(problemText).filter(Boolean))];
    if (messages.length) return messages.join(" ");
  }
  if (status === 429) return "Too many attempts. Please wait a moment and try again.";
  if (status === 413) return "That request is too large. Please shorten it and try again.";
  return "That didn't work. Please check what you entered and try again.";
}

/* ---- this tab's own sign-in ----
   The signed session lives in this tab's sessionStorage, not in a cookie that every tab of the browser shares, so each
   tab or window signs in separately: a customer, a partner and the service team can be open side by side. A refresh
   keeps the sign-in; closing the tab ends it. */

function tabSession() {
  try {
    return sessionStorage.getItem(SESSION_KEY) || "";
  } catch {
    return "";
  }
}

function forgetSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage is blocked, so there is nothing to forget.
  }
}

function keepSession(path, data) {
  if (data && typeof data.session === "string") {
    try {
      sessionStorage.setItem(SESSION_KEY, data.session);
    } catch {
      // Storage is blocked: the page still works, it just cannot stay signed in.
    }
  } else if (path === "/api/auth/logout" || (path === "/api/auth/me" && data && data.authenticated === false)) {
    forgetSession();
  }
}

function orderDestination(role, path) {
  const pattern = role === "customer" ? /^\/account#order\/AMA-[A-Z0-9]{10}$/
    : role === "partner" ? /^\/partner#deliveries\/AMA-[A-Z0-9]{10}$/ : null;
  const match = pattern && pattern.exec(path);
  return match && match[0] === path ? path : "";
}

function signInPath() {
  const path = location.pathname + location.hash;
  const next = orderDestination("customer", path) || orderDestination("partner", path);
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

function loginDestination(role) {
  const next = new URLSearchParams(location.search).get("next") || "";
  return orderDestination(role, next) || HOME[role] || HOME.customer;
}

async function api(path, { method = "GET", body } = {}) {
  const init = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-Requested-With": "turkbiztel", "X-Session-Mode": "tab" },
  };
  const session = tabSession();
  if (session) init.headers["X-Session"] = session;
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError("We couldn't reach TürkBizTel. Check your connection and try again.", 0);
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (response.ok) {
    keepSession(path, data);
    return data;
  }
  // The sign-in page also lives at "/", so a refused password must stay there and show its message.
  if (response.status === 401 && !document.body.classList.contains("auth-page")) {
    const reason = data && typeof data.detail === "string" ? data.detail : "Please sign in to continue.";
    forgetSession();
    sessionStorage.setItem(NOTICE_KEY, reason);
    location.replace(signInPath());
    return new Promise(() => {});
  }
  throw new ApiError(friendly(data, response.status), response.status, Number(response.headers.get("Retry-After")) || 0);
}

/* ---- feedback ---- */

function say(element, text, kind) {
  if (!element) return;
  element.textContent = text || "";
  element.hidden = !text;
  element.classList.toggle("error", Boolean(text) && kind === "error");
  element.classList.toggle("ok", Boolean(text) && kind === "ok");
}

/* Marks a button busy while `task` runs. aria-disabled (not disabled) keeps keyboard focus on the button. */
async function busy(button, task) {
  if (button.classList.contains("is-busy")) return undefined;
  button.classList.add("is-busy");
  button.setAttribute("aria-busy", "true");
  button.setAttribute("aria-disabled", "true");
  try {
    return await task();
  } finally {
    button.classList.remove("is-busy");
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-disabled");
  }
}

function toast(message, kind = "ok") {
  let box = $("#toasts");
  if (!box) {
    box = h("div", { id: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(box);
  }
  const item = h("div", { class: `toast ${kind}` }, icon(kind === "error" ? "alert" : "circle-check"), h("span", { text: message }));
  box.append(item);
  setTimeout(() => item.remove(), 5000);
}

/* Returns the first problem as a message (and focuses the field), or "" when every check passes.
   Each spec is [fieldName, check(value, form)], where check returns a message or a falsy value. */
function validate(form, specs) {
  if (!form.dataset.watching) {
    form.dataset.watching = "1";
    form.addEventListener("input", (event) => event.target.removeAttribute("aria-invalid"));
  }
  for (const control of form.elements) control.removeAttribute("aria-invalid");
  for (const [name, check] of specs) {
    const control = form.elements[name];
    if (!control) continue;
    const probe = control.length && !control.tagName ? control[0] : control;
    const problem = check(probe.type === "checkbox" ? probe.checked : control.value, form);
    if (problem) {
      probe.setAttribute("aria-invalid", "true");
      probe.focus();
      return problem;
    }
  }
  return "";
}

/* Moves the message to sit just above the invalid field, so on a long form the problem is never off-screen. */
function placeNextTo(message, control) {
  let anchor = control && control.closest ? control.closest(".field, fieldset") : null;
  if (anchor && anchor.parentElement.classList.contains("row")) anchor = anchor.parentElement;
  if (anchor) anchor.before(message);
}

/* Runs `attempt` when `form` is submitted: checks the fields, shows the first problem next to the field
   (`message` is a .msg element that normally lives above the submit button) and keeps the submit button busy
   while the request runs. */
function onSubmit(form, message, specs, attempt) {
  const home = document.createComment("");
  message.before(home);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    home.after(message);
    say(message, "");
    const problem = validate(form, specs);
    if (problem) {
      placeNextTo(message, document.activeElement);
      say(message, problem, "error");
      return;
    }
    await busy($("button[type=submit]", form), async () => {
      try {
        return await attempt(form);
      } catch (error) {
        if (!(error instanceof ApiError)) console.error(error);
        say(message, error instanceof ApiError ? error.message : "Something went wrong. Please try again.", "error");
        return undefined;
      }
    });
  });
}

/* ---- form building ---- */

let uid = 0;
const nextId = (prefix = "f") => `${prefix}${++uid}`;

function describe(control, id) {
  control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), id].filter(Boolean).join(" "));
}

function field(label, control, hint) {
  const id = control.id || nextId();
  control.id = id;
  const wrap = h("div", { class: "field" }, h("label", { for: id }, label), control);
  if (hint) {
    wrap.append(h("p", { class: "hint", id: `${id}-hint`, text: hint }));
    describe(control, `${id}-hint`);
  }
  return wrap;
}

function optional(label) {
  return [label, " ", h("span", { class: "opt", text: "(optional)" })];
}

function select(name, options, value, attrs = {}) {
  return h("select", { name, ...attrs }, options.map(([optionValue, text]) => h("option", {
    value: optionValue, selected: optionValue === value, text,
  })));
}

function wireReveal(button, input) {
  button.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    button.replaceChildren(icon(show ? "eye-off" : "eye"));
  });
}

const COMMON_PASSWORDS = new Set([
  "password12", "password123", "password1234", "1234567890", "12345678910", "qwertyuiop", "qwerty12345",
  "iloveyou12", "welcome123", "letmein123", "admin12345", "myturkei123", "turkiye1923", "istanbul34",
  "turkbiztel123", "türkbiztel123",
]);

/* Mirrors the server's password rules so people see the problem while typing; the server stays the judge. */
function passwordScore(password, email = "") {
  if (!password) return { level: 0, text: "Use at least 10 characters. Longer is stronger." };
  if (password.length < 10) {
    const missing = 10 - password.length;
    return { level: 1, text: `Too short: add ${missing} more character${missing === 1 ? "" : "s"}.` };
  }
  const local = email.split("@")[0].toLowerCase();
  if (COMMON_PASSWORDS.has(password.toLowerCase()) || new Set(password).size < 5
      || (local.length >= 4 && password.toLowerCase().includes(local))) {
    return { level: 1, text: "Too easy to guess. Avoid common passwords and your email address." };
  }
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((rx) => rx.test(password)).length;
  if (password.length >= 16 || (password.length >= 12 && kinds >= 3)) return { level: 4, text: "Strong password." };
  if (password.length >= 12 || kinds >= 3) return { level: 3, text: "Good password." };
  return { level: 2, text: "Okay. A longer password with a mix of characters is stronger." };
}

function strengthMeter(input, emailOf = () => "") {
  const bars = h("div", { class: "meter-bars", "data-level": "0", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"));
  const label = h("span", { class: "meter-text", id: `${input.id}-meter-text` });
  const update = () => {
    const score = passwordScore(input.value, emailOf());
    bars.dataset.level = String(score.level);
    label.textContent = score.text;
  };
  input.addEventListener("input", update);
  describe(input, label.id);
  update();
  return h("div", { class: "meter" }, bars, label);
}

function passwordField({ label, name, autocomplete, hint, meter = false, emailOf }) {
  const input = h("input", {
    type: "password", name, autocomplete, maxlength: 128, spellcheck: "false", autocapitalize: "none", required: true,
  });
  input.id = nextId("pw");
  const toggle = h("button", {
    type: "button", class: "reveal", "aria-label": "Show password", "aria-pressed": "false",
  }, icon("eye"));
  wireReveal(toggle, input);
  const wrap = h("div", { class: "field" }, h("label", { for: input.id }, label),
    h("div", { class: "reveal-wrap" }, input, toggle));
  if (hint) {
    wrap.append(h("p", { class: "hint", id: `${input.id}-hint`, text: hint }));
    describe(input, `${input.id}-hint`);
  }
  if (meter) wrap.append(strengthMeter(input, emailOf));
  return wrap;
}

/* ---- values ---- */

function normalizePhone(value) {
  const text = value.replace(/[\s().-]/g, "");
  if (text.startsWith("00")) return `+${text.slice(2)}`;
  if (/^0[1-9]\d{9}$/.test(text)) return `+90${text.slice(1)}`;
  return text;
}

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const STAMP_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});

function fmtDate(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? DATE_FORMAT.format(date) : "";
}

function fmtStamp(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? STAMP_FORMAT.format(date) : "";
}

function ago(iso) {
  const time = new Date(iso).getTime();
  if (!iso || Number.isNaN(time)) return "";
  const minutes = Math.floor(Math.max(0, Date.now() - time) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : parts[0][0];
  return letters.toLocaleUpperCase("tr");
}

function firstName(name) {
  return String(name || "").trim().split(/\s+/)[0] || "there";
}
