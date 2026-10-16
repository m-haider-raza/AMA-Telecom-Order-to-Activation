import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_requests = defaultdict(deque)


def authorize(request: Request, *, partner_allowed=False):
    supplied = request.headers.get("authorization", "")
    if not supplied.startswith("Bearer "):
        raise HTTPException(401, "A demo access token is required", headers={"WWW-Authenticate": "Bearer"})
    token = supplied[7:]
    admin = os.getenv("DEMO_ADMIN_TOKEN", "")
    partner = os.getenv("DEMO_PARTNER_TOKEN", "")
    if admin and hmac.compare_digest(token, admin):
        return "service-manager"
    if partner_allowed and partner and hmac.compare_digest(token, partner):
        return "wholesale-partner"
    raise HTTPException(403, "This token is not authorized for this operation")


def limit(request: Request):
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
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
