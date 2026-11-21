"""Sign-in primitives for customers, partners and staff: password hashing, signed sessions, throttles, e-mail
codes and partner invitations.

Sessions are stateless HMAC-signed tokens, so they survive restarts and need no session table. A per-account
version number revokes every older session when a password changes.

A token travels in one of two ways. The TürkBizTel pages keep it in their own browser tab and send it as an
`X-Session` header (marked by `X-Session-Mode: tab`), so every tab or window signs in separately: a customer, a
partner and the service team can be open side by side in one browser. Scripts and API clients that send neither
header use the `myturkei_session` cookie, which a browser would share between all of its tabs.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
import unicodedata
from collections import defaultdict, deque

from fastapi import HTTPException, Request

COOKIE = "myturkei_session"
# Keep the wire cookie and signing namespace stable so a visual rebrand does not revoke sessions.
SESSION_SECONDS = 8 * 3600
STAFF_SESSION_HOURS = 4
CODE_SECONDS = 15 * 60
CODE_ATTEMPTS = 5
RESEND_SECONDS = 60
INVITE_SECONDS = 7 * 24 * 3600
ROLES = ("customer", "partner", "admin")
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COMMON_PASSWORDS = {
    "password12", "password123", "password1234", "1234567890", "12345678910", "qwertyuiop", "qwerty12345",
    "iloveyou12", "welcome123", "letmein123", "admin12345", "myturkei123", "turkiye1923", "istanbul34",
    "turkbiztel123", "türkbiztel123",
}


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key():
    base = os.getenv("AMA_SESSION_SECRET") or os.getenv("AMA_TRACKING_SECRET")
    if not base:
        if sso_enabled():
            # The shared access token is not a safe signing key once it is meant to be out of use.
            raise RuntimeError("Set AMA_SESSION_SECRET: staff sessions cannot be signed with an access token")
        base = os.getenv("DEMO_ADMIN_TOKEN", "")
    return hmac.new(base.encode(), b"myturkei.session.v1", hashlib.sha256).digest()


def _sign(text):
    return _b64(hmac.new(_key(), text.encode(), hashlib.sha256).digest())


def check_password(password, email=""):
    """Raise ValueError with a customer-friendly message when a new password is too weak."""
    if len(password) < 10:
        raise ValueError("Use at least 10 characters for your password.")
    local = email.split("@", 1)[0].lower()
    if (password.lower() in COMMON_PASSWORDS or len(set(password)) < 5
            or (len(local) >= 4 and local in password.lower())):
        raise ValueError("Choose a password that is harder to guess, and keep your email address out of it.")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(unicodedata.normalize("NFKC", password).encode(), salt=salt, dklen=32, **SCRYPT)
    return "$".join(["scrypt", str(SCRYPT["n"]), str(SCRYPT["r"]), str(SCRYPT["p"]), _b64(salt), _b64(digest)])


_DUMMY = hash_password("not-a-real-password")


def verify_password(password, stored):
    """Constant-effort check: an unknown account is verified against a dummy hash so timing does not leak it."""
    real = bool(stored)
    try:
        _, n, r, p, salt, digest = (stored or _DUMMY).split("$")
        candidate = hashlib.scrypt(unicodedata.normalize("NFKC", password).encode(), salt=_unb64(salt),
                                   n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(candidate, _unb64(digest)) and real
    except (ValueError, TypeError):
        return False


def staff_auth_mode():
    """How the service team signs in. "token" (the default) is the shared access token; "sso" requires each person
    to sign in with Microsoft Entra ID, so every action can be tied to the person who took it."""
    return "sso" if os.getenv("AMA_STAFF_AUTH", "token").strip().lower() == "sso" else "token"


def sso_enabled():
    return staff_auth_mode() == "sso"


def staff_session_seconds():
    try:
        hours = float(os.getenv("AMA_STAFF_SESSION_HOURS", STAFF_SESSION_HOURS))
    except ValueError:
        hours = STAFF_SESSION_HOURS
    return int(min(max(hours, 0.25), 12) * 3600)


def admin_version():
    return hashlib.sha256(os.getenv("DEMO_ADMIN_TOKEN", "").encode()).hexdigest()[:12]


def staff_version():
    """Ties staff sessions to the sign-in method that issued them, so changing the method ends them all."""
    if sso_enabled():
        scope = f"sso|{os.getenv('AMA_SSO_TENANT_ID', '')}|{os.getenv('AMA_SSO_CLIENT_ID', '')}"
        return hashlib.sha256(scope.encode()).hexdigest()[:12]
    return admin_version()


def issue_session(role, subject, version, seconds=SESSION_SECONDS, person=None):
    """`person` is the signed-in staff member (oid, upn, name, role) of a single sign-on session."""
    body = {"r": role, "s": subject, "v": version, "e": int(time.time()) + seconds}
    if person:
        body["p"] = {"o": person["oid"], "u": person["upn"], "n": person["name"], "r": person["role"]}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def staff_person(session):
    """The staff member behind a single sign-on session, or None for any other kind of session."""
    person = session.get("p") if isinstance(session, dict) else None
    if not isinstance(person, dict) or not person.get("o"):
        return None
    return {"oid": str(person["o"]), "upn": str(person.get("u", "")), "name": str(person.get("n", "")),
            "role": str(person.get("r", ""))}


def sign_blob(purpose, body):
    """A short-lived signed value (an SSO flow or hand-off) that is bound to one purpose, so it cannot be replayed
    as a session or as any other kind of blob."""
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    signature = hmac.new(_key(), f"{purpose}|{payload}".encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(signature)}"


def read_blob(purpose, token):
    if not token or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    expected = _b64(hmac.new(_key(), f"{purpose}|{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("e", 0) < time.time():
        return None
    return data


def read_session(token):
    if not token or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    if not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("r") not in ROLES or data.get("e", 0) < time.time():
        return None
    return data


def per_tab(request: Request):
    """True when the caller is a TürkBizTel page that keeps its session in its own tab instead of the shared cookie."""
    return request.headers.get("x-session-mode", "").strip().lower() == "tab"


def request_session(request: Request):
    """The verified session the request carries: the tab's own token in per-tab mode (the cookie is then ignored,
    so another tab's sign-in can never leak in), otherwise the cookie. Customer and partner sessions still need
    their version checked against the stored account."""
    data = read_session(request.headers.get("x-session") if per_tab(request) else request.cookies.get(COOKIE))
    if data and data["r"] == "admin":
        if not hmac.compare_digest(str(data.get("v")), staff_version()):
            return None
        if sso_enabled() and staff_person(data) is None:
            return None
    return data


def secure_request(request: Request):
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https" or os.getenv(
        "DEMO_PUBLIC_ORIGIN", "").startswith("https://")


def set_session_cookie(response, request, token):
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=secure_request(request), path="/")


def clear_session_cookie(response, request):
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=secure_request(request))


def grant(request, response, token):
    """Hand a new session to the caller. A TürkBizTel page keeps its session in its own tab, so the token goes back in
    the reply and any older shared cookie is dropped; every other client still receives the cookie."""
    if per_tab(request):
        clear_session_cookie(response, request)
        return {"session": token}
    set_session_cookie(response, request, token)
    return {}


def require_csrf(request: Request):
    """Cookie-authenticated writes must carry a header a cross-site form or image request cannot set."""
    if request.method not in SAFE_METHODS and not request.headers.get("x-requested-with"):
        raise HTTPException(403, "Missing X-Requested-With header")


def client_ip(request: Request):
    """The address the platform proxy saw: the last X-Forwarded-For hop, which the client cannot forge."""
    last = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if last.count(":") == 1:
        last = last.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(last.strip("[]")))
    except ValueError:
        return request.client.host if request.client else "unknown"


class Window:
    """Sliding-window counter used to throttle sign-in attempts and e-mail sends."""

    def __init__(self, limit, seconds):
        self.limit, self.seconds, self.hits = limit, seconds, defaultdict(deque)

    def _recent(self, key, moment):
        queue = self.hits[key]
        while queue and queue[0] <= moment - self.seconds:
            queue.popleft()
        return queue

    def retry_after(self, key):
        moment = time.monotonic()
        queue = self._recent(key, moment)
        return 0 if len(queue) < self.limit else int(queue[0] + self.seconds - moment) + 1

    def add(self, key):
        moment = time.monotonic()
        self._recent(key, moment).append(moment)
        if len(self.hits) > 4096:
            for stale in [name for name in self.hits if not self._recent(name, moment)]:
                del self.hits[stale]

    def clear(self, key):
        self.hits.pop(key, None)


LOGIN_PAIR = Window(5, 900)
LOGIN_IP = Window(30, 900)
LOGIN_ACCOUNT = Window(40, 900)
MAIL_IP = Window(10, 3600)
APPROVAL_IP = Window(30, 900)
THROTTLES = (LOGIN_PAIR, LOGIN_IP, LOGIN_ACCOUNT, MAIL_IP, APPROVAL_IP)


def reset_throttles():
    for window in THROTTLES:
        window.hits.clear()


def guard(window, key):
    wait = window.retry_after(key)
    if wait:
        raise HTTPException(429, f"Too many attempts. Please try again in {max(1, round(wait / 60))} minute(s).",
                            headers={"Retry-After": str(wait)})


def code_hash(purpose, email, code):
    return hmac.new(_key(), f"{purpose}|{email}|{code}".encode(), hashlib.sha256).hexdigest()


def new_code(purpose, email):
    """A six-digit one-time code plus the record to store; only the keyed hash is ever persisted."""
    code = f"{secrets.randbelow(10**6):06d}"
    moment = int(time.time())
    return code, {"hash": code_hash(purpose, email, code), "expires": moment + CODE_SECONDS, "sent": moment,
                  "attempts": 0}


def check_code(record, purpose, email, code):
    """Return ok / missing / expired / locked / wrong. A wrong guess is counted on the record."""
    if not record:
        return "missing"
    if time.time() > record["expires"]:
        return "expired"
    if record["attempts"] >= CODE_ATTEMPTS:
        return "locked"
    if hmac.compare_digest(record["hash"], code_hash(purpose, email, code.strip())):
        return "ok"
    record["attempts"] += 1
    return "wrong"


def cooling_down(record):
    return bool(record) and time.time() - record["sent"] < RESEND_SECONDS


def invite_hash(token):
    return hmac.new(_key(), f"invite|{token.strip()}".encode(), hashlib.sha256).hexdigest()


def new_invite():
    """A single-use invitation token plus the record to store; only the keyed hash is ever persisted."""
    token = secrets.token_urlsafe(32)
    moment = int(time.time())
    return token, {"hash": invite_hash(token), "expires": moment + INVITE_SECONDS, "sent": moment}
