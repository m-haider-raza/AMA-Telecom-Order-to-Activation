"""Who may call the staff APIs, and which person is acting.

Token mode keeps the legacy manager credential. Both SSO modes accept named Entra sessions; `sso+token` also
accepts an operator session exchanged for a separate staff access token at the sign-in page.
"""

import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app import auth

_requests = defaultdict(deque)

# Entra app roles, lowest to highest. A request needs one of: read (see), operate (work orders), manage (decide).
STAFF_ROLES = {"ServiceAuditor": 1, "ServiceOperator": 2, "ServiceManager": 3}
ROLE_LABELS = {"ServiceAuditor": "Service auditor", "ServiceOperator": "Service operator",
               "ServiceManager": "Service manager"}
NEEDS = {"read": 1, "operate": 2, "manage": 3}
NEED_TEXT = {"operate": "change orders", "manage": "make this decision or manage partners"}
FALLBACK_STAFF = {"oid": auth.FALLBACK_SUBJECT, "name": "Staff access token (shared)", "upn": "",
                  "role": "ServiceOperator"}


class Actor(str):
    """Who is acting. It compares and is stored as the plain role string every domain rule already uses
    ("service-manager", "wholesale-partner"), and it also carries the signed-in staff member, so the audit trail can
    say which person did it without any rule having to change."""

    staff = None

    def __new__(cls, role, staff=None):
        actor = super().__new__(cls, role)
        actor.staff = staff
        return actor


def role_label(role):
    return ROLE_LABELS.get(role, "Staff")


def session_staff(session):
    if session and session["r"] == "admin" and session.get("s") == auth.FALLBACK_SUBJECT:
        return FALLBACK_STAFF
    return auth.staff_person(session)


def staff_actor(session, need):
    """The acting staff member of a verified admin session, refused when their role is below what the call needs."""
    person = session_staff(session)
    if person is None:
        return Actor("service-manager")
    if STAFF_ROLES.get(person["role"], 0) < NEEDS[need]:
        raise HTTPException(
            403, f"Your role ({role_label(person['role'])}) cannot {NEED_TEXT.get(need, 'do this')}. "
                 "Ask a service manager to change your role or to do it.")
    return Actor("service-manager", person)


def authorize(request: Request, *, partner_allowed=False, need="manage"):
    """Staff and partner-token access: a bearer token, or a signed-in staff session (a tab's token or the cookie).

    `need` is the lowest staff role the call accepts (read, operate or manage) and only matters with single sign-on.
    Customer and partner portal sessions never qualify; each has its own APIs.
    """
    supplied = request.headers.get("authorization", "")
    if supplied.startswith("Bearer "):
        token = supplied[7:]
        admin = os.getenv("DEMO_ADMIN_TOKEN", "")
        partner = os.getenv("DEMO_PARTNER_TOKEN", "")
        if admin and auth.staff_auth_mode() == "token" and hmac.compare_digest(token, admin):
            return Actor("service-manager")
        if partner_allowed and partner and hmac.compare_digest(token, partner):
            return Actor("wholesale-partner")
        raise HTTPException(403, "This token is not authorized for this operation")
    session = auth.request_session(request)
    if session and session["r"] == "admin":
        auth.require_csrf(request)
        return staff_actor(session, need)
    if session:
        raise HTTPException(403, "Customer and partner accounts cannot use the staff console or its APIs")
    message = {"sso": "Sign in with Microsoft to continue",
               "sso+token": "Sign in with Microsoft or use your staff access token"}.get(
                   auth.staff_auth_mode(), "Sign in as staff or supply an access token")
    raise HTTPException(401, message, headers={"WWW-Authenticate": "Bearer"})


def limit(request: Request):
    now = time.monotonic()
    key = auth.client_ip(request)
    queue = _requests[key]
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= 60:
        raise HTTPException(429, "Demo write limit reached; retry after one minute")
    queue.append(now)
    if len(_requests) > 4096:
        for old_key in list(_requests):
            if not _requests[old_key] or _requests[old_key][-1] < now - 60:
                del _requests[old_key]
