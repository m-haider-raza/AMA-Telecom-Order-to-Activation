"use strict";

const PANELS = ["signin", "register", "forgot", "reset", "invite", "staff"];
const TITLES = {
  signin: "Sign in", register: "Create your customer account", forgot: "Reset your password",
  reset: "Enter your code", invite: "Set up your partner account", staff: "Service team sign-in",
};
const INVITE_HASH = /^#invite=([\w-]{20,200})$/;
// Where Microsoft sends the browser back to: "#sso" after a good sign-in, "#sso-error=<code>" after a refused one.
const SSO_HASH = /^#sso(?:-error=([a-z-]{1,20}))?$/;
const SSO_PROBLEMS = {
  expired: "That sign-in took too long, or it was started in a different browser. Please sign in again.",
  denied: "The Microsoft sign-in was cancelled. Sign in again when you are ready.",
  consent: "Your organisation has not allowed this app yet. Ask your Microsoft Entra administrator to approve it, then sign in again.",
  "not-assigned": "Your account has not been given access to the TürkBizTel service console. Ask a service manager to assign you a role.",
  "no-role": "You signed in, but your account has no TürkBizTel service role. Ask a service manager to assign you one, then sign in again.",
  tenant: "That account belongs to a different organisation. Sign in with your TürkBizTel work account.",
  unavailable: "Microsoft sign-in is not available right now. Please try again in a few minutes.",
  failed: "We couldn't complete the Microsoft sign-in. Please try again, and contact the service desk if it keeps failing.",
  throttled: "There have been too many sign-in attempts. Wait a few minutes and try again.",
};
const forms = Object.fromEntries(PANELS.map((name) => [name, $(`#panel-${name}`)]));
const tabs = $$('[role="tab"]');
let resetEmail = "";
let inviteToken = "";
let inviteEmail = "";
let inviteRun = 0;
let simulatedDelivery = false;

/* Leaves the button busy until the browser has actually moved on, so it never flashes back to normal. */
function navigate(url, { replace = false } = {}) {
  if (replace) location.replace(url);
  else location.assign(url);
  return new Promise(() => {});
}

function showNotice(text) {
  $("#notice-text").textContent = text;
  $("#notice").hidden = false;
}

const hideNotice = () => { $("#notice").hidden = true; };

const fromHash = () => {
  const invite = INVITE_HASH.exec(location.hash);
  if (invite) inviteToken = invite[1];
  const name = location.hash.slice(1);
  if (invite || (name === "invite" && inviteToken)) return "invite";
  if (SSO_HASH.test(location.hash)) return "staff";
  if (name === "reset") return "forgot";
  return PANELS.includes(name) && name !== "invite" ? name : "signin";
};

/* The service team signs in with Microsoft or with an access token, and the server says which. The link
   keeps its space but stays invisible until then, so the wrong wording never flashes. */
function applyStaffMode(mode) {
  const sso = mode === "sso" || mode === "sso+token";
  $("#staff-token").hidden = mode !== "token" && mode !== "sso+token";
  $("#staff-sso").hidden = !sso;
  $("#staff-fallback-hint").hidden = mode !== "sso+token";
  if (sso) $("#staff-link").textContent = mode === "sso" ? "Sign in with Microsoft" : "Staff sign in";
  $("#staff-row").removeAttribute("data-pending");
}

/* Microsoft has sent the browser back. The server holds the verified person in a short-lived cookie that works
   once, and this call turns it into the session for this tab. */
async function finishSso() {
  const start = $("#sso-start");
  const wait = $("#sso-wait");
  start.hidden = true;
  wait.hidden = false;
  try {
    const result = await api("/api/auth/sso/handoff", { method: "POST" });
    // Replace this page in history so the back button does not return to a finished sign-in.
    return navigate(loginDestination(result.role), { replace: true });
  } catch (error) {
    wait.hidden = true;
    start.hidden = false;
    say($("#staff-msg"), error instanceof ApiError ? error.message : SSO_PROBLEMS.failed, "error");
    return undefined;
  }
}

/* Asks the server whether the invitation in the link is still good, and shows who it is for. */
async function checkInvite() {
  const run = ++inviteRun;
  const problem = $("#invite-msg");
  $("#invite-body").hidden = true;
  say(problem, "");
  $("#invite-sub").textContent = "Checking your invitation…";
  try {
    const info = await api("/api/auth/invite/check", { method: "POST", body: { token: inviteToken } });
    if (run !== inviteRun) return;
    inviteEmail = info.email;
    $("#invite-partner").textContent = info.partner;
    $("#invite-email").textContent = info.email;
    $("#invite-sub").textContent = `Welcome, ${firstName(info.name)}. Choose a password to open your partner portal.`;
    $("#invite-body").hidden = false;
  } catch (error) {
    if (run !== inviteRun) return;
    $("#invite-sub").textContent = "";
    say(problem, error instanceof ApiError ? error.message : "We couldn't check your invitation. Please try again.", "error");
  }
}

function show(name, { focus = true } = {}) {
  for (const key of PANELS) forms[key].hidden = key !== name;
  const isTab = name === "signin" || name === "register";
  $("#tabs").hidden = !isTab;
  for (const tab of tabs) {
    const selected = tab.dataset.panel === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  document.title = `${TITLES[name]} · TürkBizTel`;
  // The invitation link keeps its code in the address, so reloading the page still works.
  if (name !== "invite") history.replaceState(null, "", name === "signin" ? location.pathname + location.search : `#${name}`);
  else checkInvite();
  window.dispatchEvent(new Event("turkbiztel:navigate"));
  if (focus) $(`#h-${name}`).focus();
}

function weakPassword(value, email) {
  const score = passwordScore(value, email);
  return score.level < 2 && score.text;
}

function mountFields() {
  const signin = passwordField({ label: "Password", name: "password", autocomplete: "current-password" });
  const label = $("label", signin);
  const row = h("div", { class: "label-row" });
  label.replaceWith(row);
  row.append(label, h("button", { class: "btn link", type: "button", "data-go": "forgot", text: "Forgot password?" }));
  $("[data-slot=password]", forms.signin).replaceWith(signin);

  $("[data-slot=password]", forms.register).replaceWith(passwordField({
    label: "Password", name: "password", autocomplete: "new-password", meter: true,
    emailOf: () => forms.register.elements.email.value,
  }));
  $("[data-slot=password]", forms.reset).replaceWith(passwordField({
    label: "New password", name: "password", autocomplete: "new-password", meter: true, emailOf: () => resetEmail,
  }));
  $("[data-slot=password]", forms.invite).replaceWith(passwordField({
    label: "Choose a password", name: "password", autocomplete: "new-password", meter: true,
    emailOf: () => inviteEmail,
  }));
  $("[data-slot=token]", forms.staff).replaceWith(passwordField({
    label: "Access token", name: "token", autocomplete: "off",
  }));
}

for (const form of Object.values(forms)) form.addEventListener("submit", hideNotice);

onSubmit(forms.signin, $("#signin-msg"), [
  ["email", (value) => !EMAIL_RX.test(value.trim()) && "Enter the email address you signed up with."],
  ["password", (value) => !value && "Enter your password."],
], async (form) => {
  const result = await api("/api/auth/login", {
    method: "POST", body: { email: form.elements.email.value.trim(), password: form.elements.password.value },
  });
  return navigate(loginDestination(result.role));
});

onSubmit(forms.register, $("#register-msg"), [
  ["name", (value) => value.trim().length < 2 && "Enter your full name."],
  ["company", (value) => value.trim().length < 2 && "Enter your company name."],
  ["email", (value) => !EMAIL_RX.test(value.trim()) && EMAIL_PROBLEM],
  ["phone", (value) => value.trim() && !PHONE_RX.test(normalizePhone(value.trim())) && PHONE_PROBLEM],
  ["password", (value, form) => weakPassword(value, form.elements.email.value)],
  ["consent", (checked) => !checked && "Please tick the box to agree before you continue."],
], async (form) => {
  const body = {
    name: form.elements.name.value.trim(),
    company: form.elements.company.value.trim(),
    email: form.elements.email.value.trim(),
    language: form.elements.language.value,
    password: form.elements.password.value,
    consent: true,
  };
  const phone = normalizePhone(form.elements.phone.value.trim());
  if (phone) body.phone = phone;
  const result = await api("/api/auth/register", { method: "POST", body });
  sessionStorage.removeItem(DEMO_CODE_KEY);
  sessionStorage.removeItem(MAIL_PROBLEM_KEY);
  if (result.simulatedCode) sessionStorage.setItem(DEMO_CODE_KEY, result.simulatedCode);
  if (result.delivery === "unavailable") sessionStorage.setItem(MAIL_PROBLEM_KEY, "1");
  return navigate(loginDestination("customer"));
});

async function requestCode(email) {
  const result = await api("/api/auth/forgot", { method: "POST", body: { email } });
  resetEmail = email;
  const code = result.simulatedCode || "";
  $("#reset-demo").hidden = !code;
  $("#reset-demo-code").textContent = code;
  $("#reset-sub").textContent =
    code || simulatedDelivery ? "No email was sent. If a test account exists, its simulated code is shown below and works for 15 minutes."
      : `If an account exists for ${email}, we have sent it a 6-digit code. The code works for 15 minutes.`;
}

onSubmit(forms.forgot, $("#forgot-msg"), [
  ["email", (value) => !EMAIL_RX.test(value.trim()) && "Enter the email address you signed up with."],
], async (form) => {
  await requestCode(form.elements.email.value.trim());
  show("reset");
});

onSubmit(forms.reset, $("#reset-msg"), [
  ["code", (value) => !/^\d{6}$/.test(value.trim()) && "Enter the 6-digit code from your email."],
  ["password", (value) => weakPassword(value, resetEmail)],
], async (form) => {
  await api("/api/auth/reset", {
    method: "POST",
    body: { email: resetEmail, code: form.elements.code.value.trim(), password: form.elements.password.value },
  });
  form.elements.code.value = "";
  form.elements.password.value = "";
  form.elements.password.dispatchEvent(new Event("input"));
  forms.signin.elements.email.value = resetEmail;
  show("signin", { focus: false });
  showNotice("Your password has been changed. Sign in with your new password.");
  forms.signin.elements.password.focus();
});

$("#reset-resend").addEventListener("click", (event) => busy(event.currentTarget, async () => {
  const message = $("#reset-msg");
  say(message, "");
  try {
    await requestCode(resetEmail);
    say(message, "We have sent you a new code.", "ok");
  } catch (error) {
    say(message, error.message, "error");
  }
}));

onSubmit(forms.invite, $("#invite-msg"), [
  ["password", (value) => weakPassword(value, inviteEmail)],
], async (form) => {
  const result = await api("/api/auth/invite/accept", {
    method: "POST", body: { token: inviteToken, password: form.elements.password.value },
  });
  // Replace this page in history so the used invitation link is not one tap away on the back button.
  return navigate(loginDestination(result.role), { replace: true });
});

onSubmit(forms.staff, $("#staff-msg"), [
  ["token", (value) => !value.trim() && "Enter the access token you were given."],
], async (form) => {
  await api("/api/auth/staff", { method: "POST", body: { token: form.elements.token.value.trim() } });
  return navigate(HOME.admin);
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-go]");
  if (!link) return;
  show(link.dataset.go);
});

// The skip link must not change the hash, which this page reads as "which panel to show".
$(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  $("#auth-card").focus();
});

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => show(tab.dataset.panel, { focus: false }));
  tab.addEventListener("keydown", (event) => {
    const target = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    const next = tabs[(target + tabs.length) % tabs.length];
    show(next.dataset.panel, { focus: false });
    next.focus();
  });
}

window.addEventListener("hashchange", () => show(fromHash(), { focus: false }));
window.addEventListener("pageshow", (event) => {
  if (event.persisted) location.reload();
});

async function boot() {
  mountFields();
  const bounced = sessionStorage.getItem(NOTICE_KEY);
  if (bounced) {
    sessionStorage.removeItem(NOTICE_KEY);
    showNotice(bounced);
  }
  // Read before show() tidies the address: the hash says Microsoft has just sent the browser back.
  const back = SSO_HASH.exec(location.hash);
  show(fromHash(), { focus: false });
  if (back) {
    applyStaffMode("sso");
    if (back[1]) say($("#staff-msg"), SSO_PROBLEMS[back[1]] || SSO_PROBLEMS.failed, "error");
    else await finishSso();
  }
  try {
    const me = await api("/api/auth/me");
    simulatedDelivery = Boolean(me.isolatedArchitecture && !me.delivery);
    applyStaffMode(me.staffAuth);
    if (simulatedDelivery) {
      $("#partner-invitation-note").textContent = "Partner? Your service-team contact shares a test invitation link. No invitation email is sent in this isolated application.";
      $("#panel-forgot .sub").textContent = "Enter your test email address to generate a simulated 6-digit code. No email is sent.";
    }
    // A visitor who was just sent here because a session ended must not be sent straight back, and
    // someone opening an invitation link must get to set up their account, whoever is signed in.
    if (me.authenticated && !bounced && !back && !INVITE_HASH.test(location.hash)) {
      location.replace(loginDestination(me.role));
      return;
    }
    if (me.mode !== "live") {
      $("#reg-company-hint").hidden = false;
      forms.register.elements.company.setAttribute("aria-describedby", "reg-company-hint");
    }
  } catch {
    // The forms still work without this check; the server explains any rule they break.
    applyStaffMode(back ? "sso" : "token");
  }
}

boot();
