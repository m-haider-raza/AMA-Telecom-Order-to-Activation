const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const {test} = require("node:test");
const {runInNewContext} = require("node:vm");

const source = readFileSync(join(__dirname, "..", "app", "static", "app.js"), "utf8");
const helpers = source.slice(source.indexOf("function node("), source.indexOf("function toast("));
const capacity = source.slice(source.indexOf("function renderPartnerCapacity("), source.indexOf('$("#audit-download")'));

class Element {
  constructor(tagName) { this.tagName = tagName; this.children = []; this.disabled = false; this.value = ""; }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(child => child.textContent).join(" "); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.value = ""; this.children = children; }
}

function setup(response) {
  const output = new Element("div"), button = new Element("button"), notices = [];
  const context = {
    document: {createElement: tag => new Element(tag)},
    $: selector => selector === "#partner-result" ? output : button,
    requireToken: () => true,
    productNames: {connectivity: "Dedicated Internet", sdwan: "Managed SD-WAN", cloud: "Cloud Connect"},
    toast: (...args) => notices.push(args),
    api: async () => response,
  };
  runInNewContext(helpers + capacity, context);
  return {context, output, button, notices};
}

function success(entries = [
  {product: "connectivity", availableUnits: 20, changeWindow: "22:00-06:00 Europe/Istanbul (simulation)"},
  {product: "sdwan", availableUnits: 0, changeWindow: "22:00-06:00 Europe/Istanbul (simulation)"},
]) {
  return {result: {isError: false, content: [{type: "text", text: JSON.stringify({
    city: "Istanbul", simulated: true, partner: "Demo Balkan Wholesale", capacity: entries,
  })}]}};
}

test("successful MCP capacity becomes readable rows, including zero availability", async () => {
  const {button, output, notices} = setup(success());
  const pending = button.onclick();
  assert.equal(button.disabled, true);
  assert.match(output.textContent, /Checking Istanbul/);
  await pending;
  assert.equal(button.disabled, false);
  assert.match(output.textContent, /Istanbul capacity/);
  assert.match(output.textContent, /Simulated availability, not a reservation/);
  assert.match(output.textContent, /Dedicated Internet 20/);
  assert.match(output.textContent, /Managed SD-WAN 0/);
  assert.doesNotMatch(output.textContent, /isError|content|\\u/);
  assert.equal(notices.length, 0);
});

for (const [name, response, expected] of [
  ["JSON-RPC error", {error: {message: "Permission denied"}}, /Permission denied/],
  ["MCP tool failure", {result: {isError: true, content: [{text: "Capacity unavailable"}]}}, /Capacity unavailable/],
  ["invalid JSON", {result: {content: [{text: "not JSON"}]}}, /unreadable response/],
  ["missing data", {result: {content: [{text: "{}"}]}}, /incomplete results/],
]) {
  test(`${name} clears previous results and reports failure`, async () => {
    const {button, output, notices} = setup(response);
    output.textContent = "Old capacity";
    await button.onclick();
    assert.match(output.textContent, /^Capacity check failed:/);
    assert.match(output.textContent, expected);
    assert.doesNotMatch(output.textContent, /Old capacity/);
    assert.equal(button.disabled, false);
    assert.equal(notices[0][1], true);
  });
}

test("network failure remains visible and the button can be retried", async () => {
  const {context, button, output} = setup();
  context.api = async () => { throw new Error("Request failed (503)"); };
  await button.onclick();
  assert.match(output.textContent, /503/);
  assert.equal(button.disabled, false);
});

test("empty inventory is explicit and partner text is not parsed as HTML", async () => {
  const {context, button, output} = setup(success([]));
  await button.onclick();
  assert.match(output.textContent, /No capacity entries/);
  const response = success();
  const data = JSON.parse(response.result.content[0].text);
  data.partner = "<img src=x onerror=alert(1)>";
  response.result.content[0].text = JSON.stringify(data);
  context.api = async () => response;
  await button.onclick();
  assert.match(output.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(output.children[1].children.length, 0);
});

for (const [name, me, destination] of [
  ["anonymous isolated", {authenticated: false, mode: "demo", isolatedArchitecture: true}, "signin"],
  ["anonymous live", {authenticated: false, mode: "live"}, "signin"],
  ["legacy public demo", {authenticated: false, mode: "demo"}, "console"],
  ["isolated staff", {authenticated: true, role: "admin", isolatedArchitecture: true}, "console"],
  ["customer", {authenticated: true, role: "customer"}, "/account"],
]) {
  test(`${name} boot uses the correct destination without a false expiry warning`, async () => {
    const destinations = [];
    const context = {
      api: async () => me, session: {}, HOME: {customer: "/account"},
      leaveConsole: message => { assert.equal(message, undefined); destinations.push("signin"); },
      showSession: () => {}, document: {body: {classList: {remove: () => {}}}},
      refresh: () => destinations.push("console"), setInterval: () => 1,
      location: {replace: path => destinations.push(path)},
    };
    const boot = source.slice(source.indexOf("async function boot()"), source.indexOf("buildAgentPages(); boot();"));
    await runInNewContext(boot + ";boot()", context);
    assert.deepEqual(destinations, [destination]);
  });
}

const partnersSource = readFileSync(join(__dirname, "..", "app", "static", "partners-admin.js"), "utf8");
const showInvite = partnersSource.slice(partnersSource.indexOf("  function showInvite("), partnersSource.indexOf("  async function copyInvite("));

for (const delivery of ["email", "failed", "manual"]) {
  test(`partner invitation dialog reports ${delivery} without claiming inbox delivery`, () => {
    const elements = new Map(), attributes = new Map();
    const element = selector => {
      if (!elements.has(selector)) elements.set(selector, {
        setAttribute: (name, value) => attributes.set(`${selector}:${name}`, value),
        showModal: () => { elements.get(selector).opened = true; },
      });
      return elements.get(selector);
    };
    const invite = {
      path: "/login#invite=single-use-link", email: "contact@example.invalid",
      delivery, expiresAt: "expiry", ...(delivery === "failed" ? {deliveryError: "Recipient is suppressed."} : {}),
    };
    runInNewContext(showInvite + ";showInvite(person, invite, 'Anatolia Wholesale')", {
      $: element, location: {origin: "https://turkbiztel.example"}, stamp: value => value,
      person: {name: "Test Contact", email: invite.email}, invite,
    });
    const note = element("#invite-note");
    assert.equal(element("#invite-link").value, "https://turkbiztel.example" + invite.path);
    assert.equal(element("#invite-dialog").opened, true);
    assert.match(note.textContent, /works once and expires expiry/);
    assert.doesNotMatch(note.textContent, /We also emailed/);
    if (delivery === "email") {
      assert.match(note.textContent, /email service accepted/);
      assert.match(note.textContent, /does not confirm inbox delivery/);
    } else {
      assert.match(note.textContent, /Copy and share the link securely/);
      assert.doesNotMatch(note.textContent, /email service accepted/);
    }
    if (delivery === "failed") {
      assert.match(note.textContent, /not confirmed.*Recipient is suppressed/);
      assert.equal(note.className, "form-error");
      assert.equal(attributes.get("#invite-note:role"), "alert");
    }
  });
}

const uiSource = readFileSync(join(__dirname, "..", "app", "static", "ui.js"), "utf8");
const orderNavigation = uiSource.slice(uiSource.indexOf("function orderDestination("), uiSource.indexOf("async function api("));
const customerSource = readFileSync(join(__dirname, "..", "app", "static", "account.js"), "utf8");
const customerBoot = customerSource.slice(customerSource.indexOf("async function boot()"), customerSource.indexOf("function wire()"));
const loginSource = readFileSync(join(__dirname, "..", "app", "static", "login.js"), "utf8");
const customerSignin = loginSource.slice(loginSource.indexOf("onSubmit(forms.signin,"), loginSource.indexOf("onSubmit(forms.register,"));

test("an anonymous customer email link asks for sign-in before loading order data and keeps its destination", async () => {
  const destinations = [], dataReads = [];
  const context = {
    URLSearchParams, HOME: {customer: "/account", partner: "/partner", admin: "/console"},
    location: {pathname: "/account", hash: "#order/AMA-0123456789", replace: path => destinations.push(path)},
    api: async path => { assert.equal(path, "/api/auth/me"); return {authenticated: false}; },
    load: () => dataReads.push("orders"),
  };
  await runInNewContext(orderNavigation + customerBoot + ";boot()", context);
  assert.deepEqual(destinations, ["/login?next=%2Faccount%23order%2FAMA-0123456789"]);
  assert.deepEqual(dataReads, []);
});

for (const [role, next, expected] of [
  ["customer", "/account#order/AMA-0123456789", "/account#order/AMA-0123456789"],
  ["partner", "/partner#deliveries/AMA-0123456789", "/partner#deliveries/AMA-0123456789"],
  ["partner", "/account#order/AMA-0123456789", "/partner"],
  ["customer", "https://other.example/account#order/AMA-0123456789", "/account"],
  ["customer", "//other.example/account#order/AMA-0123456789", "/account"],
  ["customer", "/account#order/AMA-0123456789\n", "/account"],
]) {
  test(`sign-in returns ${role} only to a valid same-role order destination: ${JSON.stringify(next)}`, async () => {
    const destinations = [];
    let submit;
    const context = {
      URLSearchParams, HOME: {customer: "/account", partner: "/partner", admin: "/console"},
      location: {search: "?next=" + encodeURIComponent(next)},
      forms: {signin: {elements: {email: {value: "contact@example.invalid"}, password: {value: "test-password"}}}},
      $: () => ({}), onSubmit: (form, message, fields, attempt) => { submit = attempt; },
      api: async path => { assert.equal(path, "/api/auth/login"); return {role}; },
      navigate: path => destinations.push(path),
    };
    runInNewContext(orderNavigation + customerSignin, context);
    await submit(context.forms.signin);
    assert.deepEqual(destinations, [expected]);
  });
}

test("a legacy tracking link redirects to the authenticated order route without sending its token", () => {
  const portalSource = readFileSync(join(__dirname, "..", "app", "static", "portal.js"), "utf8");
  const trackingBoot = portalSource.slice(portalSource.indexOf('if (location.pathname === "/track")'));
  const destinations = [];
  runInNewContext(trackingBoot, {
    URLSearchParams, show: () => {},
    location: {pathname: "/track", hash: "#order=AMA-0123456789&token=old-private-token", replace: path => destinations.push(path)},
  });
  assert.deepEqual(destinations, ["/account#order/AMA-0123456789"]);
});

test("an expired customer session returns to sign-in with the order destination and forgets the old session", async () => {
  const saved = new Map([["myturkei-session", "expired-session"]]), destinations = [];
  const context = {
    URLSearchParams,
    sessionStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)},
    document: {body: {classList: {contains: () => false}}},
    location: {pathname: "/account", hash: "#order/AMA-0123456789", replace: path => destinations.push(path)},
    fetch: async () => ({ok: false, status: 401, json: async () => ({detail: "Please sign in to continue."})}),
  };
  runInNewContext(uiSource.slice(0, uiSource.indexOf("/* ---- feedback ---- */")), context);
  context.api("/api/me/orders/AMA-0123456789");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(destinations, ["/login?next=%2Faccount%23order%2FAMA-0123456789"]);
  assert.equal(saved.has("myturkei-session"), false);
  assert.equal(saved.get("myturkei-notice"), "Please sign in to continue.");
});
