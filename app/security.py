import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app import auth

_requests = defaultdict(deque)


def authorize(request: Request, *, partner_allowed=False):
    """Staff and partner-token access: a bearer token, or a signed-in staff session (a tab's token or the cookie).

    Customer and partner portal sessions never qualify; each has its own APIs.
    """
    supplied = request.headers.get("authorization", "")
    if supplied.startswith("Bearer "):
        token = supplied[7:]
        admin = os.getenv("DEMO_ADMIN_TOKEN", "")
        partner = os.getenv("DEMO_PARTNER_TOKEN", "")
        if admin and hmac.compare_digest(token, admin):
            return "service-manager"
        if partner_allowed and partner and hmac.compare_digest(token, partner):
            return "wholesale-partner"
        raise HTTPException(403, "This token is not authorized for this operation")
    session = auth.request_session(request)
    if session and session["r"] == "admin":
        auth.require_csrf(request)
        return "service-manager"
    if session:
        raise HTTPException(403, "Customer and partner accounts cannot use the staff console or its APIs")
    raise HTTPException(401, "Sign in as staff or supply an access token", headers={"WWW-Authenticate": "Bearer"})


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
