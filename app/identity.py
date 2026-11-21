"""Who is signed in right now: the request's session (a tab's own token, or the cookie for script clients) checked
against the stored accounts.

One answer feeds /api/auth/me and the role APIs, so they can never disagree. A session whose account was removed,
disabled or had its password changed counts as signed out.
"""

from app import auth


def resolve(request, snapshot=None):
    """The signed-in actor as {"role": ..., ...account records}, or None when nobody valid is signed in."""
    session = auth.request_session(request)
    if session is None:
        return None
    if session["r"] == "admin":
        return {"role": "admin", "staff": auth.staff_person(session)}
    snapshot = snapshot if snapshot is not None else request.app.state.store.read()
    if session["r"] == "customer":
        customer = snapshot.get("customers", {}).get(session["s"])
        if customer and customer["sessionVersion"] == session["v"]:
            return {"role": "customer", "customer": customer}
        return None
    user = snapshot.get("partnerUsers", {}).get(session["s"])
    partner = snapshot.get("partners", {}).get(user["partnerId"]) if user else None
    if user and partner and partner["status"] == "active" and user["sessionVersion"] == session["v"]:
        return {"role": "partner", "user": user, "partner": partner}
    return None
