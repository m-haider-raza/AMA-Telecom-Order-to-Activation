"use strict";
const $ = (selector) => document.querySelector(selector);

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
    $("#confirm-code").textContent = order.trackingToken;
    $("#confirm-link").href = order.trackPath;
    show("confirm");
  } catch (error) { $("#order-error").textContent = error.message; }
  finally { $("#submit-order").disabled = false; }
};
if (location.pathname === "/track") {
  show("track");
  const orderId = new URLSearchParams(location.hash.slice(1)).get("order") || "";
  location.replace(orderId.length === 14 && /^AMA-[A-Z0-9]{10}$/.test(orderId)
    ? `/account#order/${orderId}` : "/account");
} else show("order");
