"use strict";
const $ = (selector) => document.querySelector(selector);
const states = {acknowledged: "Received", inProgress: "In progress", pendingApproval: "Under review", held: "On hold",
  awaitingCustomer: "Action needed from you", completed: "Processed", rejected: "Rejected", failed: "Failed"};
let current = null;

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", ...options.headers}});
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail
        : (body.detail || []).map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ") || detail;
    } catch { /* keep the HTTP status as the message */ }
    throw new Error(detail);
  }
  return response.json();
}
function contactFrom(form) {
  const data = new FormData(form), contact = {
    contactName: data.get("contactName").trim(), preferredChannel: data.get("preferredChannel"),
    updateFrequency: data.get("updateFrequency")};
  if (data.get("email")) contact.email = data.get("email").trim();
  if (data.get("phone")) contact.phone = data.get("phone").trim();
  return contact;
}
function show(section) {
  ["order", "confirm", "track"].forEach(name => $(`#${name}-section`).classList.toggle("hidden", name !== section));
}
function render(order) {
  current = order;
  $("#order-view").classList.remove("hidden");
  $("#view-title").textContent = `${order.id} · ${order.customer}`;
  $("#view-status").textContent = states[order.state] || order.state;
  const box = $("#action-box");
  box.classList.toggle("hidden", !(order.state === "awaitingCustomer" && order.action));
  if (order.action) {
    $("#action-text").textContent = `The installation address you gave does not appear to be in ${order.city} (${order.action.remindersSent} reminder(s) sent). `
      + `If the address and city are correct, confirm below; otherwise place a new order with the right address.`;
    $("#confirm-site").textContent = `The address is in ${order.city}`;
  }
  const list = $("#updates"); list.replaceChildren();
  order.updates.slice().reverse().forEach(update => {
    const item = document.createElement("li"), when = document.createElement("small");
    item.textContent = order.language === "tr" ? update.tr : update.en;
    when.textContent = ` ${new Date(update.at).toLocaleString()}`; item.append(when); list.append(item);
  });
  const form = $("#prefs-form");
  form.contactName.value = order.contact?.contactName || "";
  form.preferredChannel.value = order.contact?.preferredChannel || "email";
  form.updateFrequency.value = order.contact?.updateFrequency || "every-step";
  form.email.value = ""; form.phone.value = "";
}
async function lookup(orderId, token) {
  $("#track-error").textContent = "";
  try {
    const order = await api(`/api/portal/orders/${encodeURIComponent(orderId)}`, {headers: {"X-Tracking-Token": token}});
    sessionStorage.setItem("ama-tracking", JSON.stringify({orderId, token}));
    render(order);
  } catch (error) { $("#track-error").textContent = error.message; }
}
$("#order-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.target; $("#order-error").textContent = "";
  if (!form.reportValidity()) return;
  $("#submit-order").disabled = true;
  try {
    const data = new FormData(form), payload = {
      customer: data.get("customer").trim(), city: data.get("city"), product: data.get("product"),
      language: data.get("language"), siteAddress: data.get("siteAddress").trim(),
      idempotencyKey: crypto.randomUUID(), contact: contactFrom(form)};
    const order = await api("/api/portal/orders", {method: "POST", body: JSON.stringify(payload)});
    sessionStorage.setItem("ama-tracking", JSON.stringify({orderId: order.id, token: order.trackingToken}));
    $("#confirm-id").textContent = order.id;
    $("#confirm-link").href = order.trackPath;
    show("confirm");
  } catch (error) { $("#order-error").textContent = error.message; }
  finally { $("#submit-order").disabled = false; }
};
$("#lookup-form").onsubmit = event => {
  event.preventDefault();
  const data = new FormData(event.target); lookup(data.get("order").trim(), data.get("token").trim());
};
$("#confirm-site").onclick = async () => {
  try {
    const saved = JSON.parse(sessionStorage.getItem("ama-tracking") || "{}");
    render(await api(`/api/portal/orders/${encodeURIComponent(current.id)}/address`, {
      method: "POST", headers: {"X-Tracking-Token": saved.token},
      body: JSON.stringify({siteReference: current.action.expectedSiteReference})}));
  } catch (error) { $("#track-error").textContent = error.message; }
};
$("#prefs-form").onsubmit = async event => {
  event.preventDefault();
  const saved = JSON.parse(sessionStorage.getItem("ama-tracking") || "{}");
  try {
    render(await api(`/api/portal/orders/${encodeURIComponent(current.id)}/contact`, {
      method: "PUT", headers: {"X-Tracking-Token": saved.token}, body: JSON.stringify(contactFrom(event.target))}));
    $("#prefs-msg").textContent = "Preferences saved.";
  } catch (error) { $("#prefs-msg").textContent = error.message; }
};
if (location.pathname === "/track") {
  show("track");
  const hash = new URLSearchParams(location.hash.slice(1)), saved = JSON.parse(sessionStorage.getItem("ama-tracking") || "{}");
  const orderId = hash.get("order") || saved.orderId, token = hash.get("token") || saved.token;
  if (orderId && token) {
    $("#lookup-form").order.value = orderId; $("#lookup-form").token.value = token;
    history.replaceState(null, "", "/track"); lookup(orderId, token);
  }
} else show("order");
