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
