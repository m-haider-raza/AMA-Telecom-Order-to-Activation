"""Single sign-on for the service team with Microsoft Entra ID.

Each member of the service team signs in with their own work account (OpenID Connect, authorization-code flow with
PKCE), so the app never handles a staff password or a shared access token. Entra decides who may sign in and which
service role they hold (an app role in the ID token); the app refuses anyone who holds none, and, by default, the
enterprise application also requires assignment. The app turns that into a staff session that names the person, and
every action they take is written to the tamper-evident audit trail under their name (see app.domain.audit).

The page security policy allows no script to talk to Microsoft, so the sign-in is plain browser navigation: the
login link redirects to Microsoft, and Microsoft sends the browser back to /auth/sso/callback. The callback never
puts a session in a URL. It leaves a two-minute, single-use signed cookie and sends the browser to /login#sso,
whose script exchanges that cookie for a session through POST /api/auth/sso/handoff, the way every other sign-in
hands a session to its own browser tab.

The app proves who it is to Microsoft with its managed identity (a federated credential on the app registration),
so there is no client secret to store or rotate. AMA_SSO_CLIENT_SECRET exists for local development only.
"""

import logging
import os
import re
import secrets
import threading
import time
import warnings

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app import auth
from app.domain import audit
from app.schemas import transact
from app.security import STAFF_ROLES, Actor, role_label

log = logging.getLogger("ama.sso")
router = APIRouter()

AUTHORITY = "https://login.microsoftonline.com"
CALLBACK_PATH = "/auth/sso/callback"
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FLOW_COOKIE = "tbt_sso_flow"
HANDOFF_COOKIE = "tbt_sso_handoff"
FLOW_SECONDS = 600
HANDOFF_SECONDS = 120

_lock = threading.Lock()
_msal = None
_used = {}

# msal recommends the form_post response mode, which needs a SameSite=None flow cookie. The query mode stays: the code
# is useless without the PKCE verifier kept in the signed flow cookie, and the access log no longer prints it (below).
warnings.filterwarnings("ignore", message="response_mode='form_post' is recommended", category=UserWarning)


class CallbackLogScrubber(logging.Filter):
    """The server's access log prints each request's full URL. The callback's carries the authorization code and the
    state, and Microsoft's error text can name a person, so that log line keeps the path only."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and str(args[2]).startswith(CALLBACK_PATH + "?"):
            record.args = (*args[:2], CALLBACK_PATH, *args[3:])
        return True


_access_log = logging.getLogger("uvicorn.access")
if not any(isinstance(known, CallbackLogScrubber) for known in _access_log.filters):
    _access_log.addFilter(CallbackLogScrubber())


class Refused(Exception):
    """A sign-in Microsoft completed but the app will not accept; `code` is what the login page shows."""

    def __init__(self, code, who=None):
        super().__init__(code)
        self.code, self.who = code, who


def settings():
    return os.getenv("AMA_SSO_TENANT_ID", "").strip().lower(), os.getenv("AMA_SSO_CLIENT_ID", "").strip().lower()


def configuration_problems():
    """Everything that must be set before the service team can sign in; the app refuses to start without it."""
    problems = []
    tenant, client = settings()
    if not GUID.match(tenant):
        problems.append("AMA_SSO_TENANT_ID must be the Microsoft Entra tenant ID (a GUID)")
    if not GUID.match(client):
        problems.append("AMA_SSO_CLIENT_ID must be the application (client) ID of the app registration (a GUID)")
    if len(os.getenv("AMA_SESSION_SECRET", "")) < 32:
        problems.append("AMA_SESSION_SECRET must be a random value of at least 32 characters")
    if not os.getenv("AMA_SSO_CLIENT_SECRET") and not os.getenv("AZURE_CLIENT_ID"):
        problems.append("AZURE_CLIENT_ID (the managed identity the app registration trusts) is required")
    origin = os.getenv("AMA_PUBLIC_ORIGIN", "").strip()
    if not re.match(r"^(https://[^/\s]+|http://(localhost|127\.0\.0\.1)(:[0-9]+)?)/?$", origin):
        problems.append("AMA_PUBLIC_ORIGIN must be the https origin of the app, such as https://app.example.com, "
                        "so Microsoft returns the browser to the right address")
    return problems


def require_sso():
    if not auth.sso_enabled():
        raise HTTPException(404, "Not found")


def public_origin():
    return os.getenv("AMA_PUBLIC_ORIGIN", "").strip().rstrip("/")


def federated_assertion():
    """A token from the app's managed identity. The app registration trusts it through a federated credential."""
    from azure.identity import ManagedIdentityCredential

    credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    return lambda: credential.get_token("api://AzureADTokenExchange/.default").token


def build_msal_app():
    import msal

    class Uncached(msal.TokenCache):
        """Signing in only needs the claims of the ID token, so no token is kept in memory."""

        def add(self, event, now=None):
            return None

    tenant, client = settings()
    secret = os.getenv("AMA_SSO_CLIENT_SECRET")
    return msal.ConfidentialClientApplication(
        client, client_credential=secret or {"client_assertion": federated_assertion()},
        authority=f"{AUTHORITY}/{tenant}", token_cache=Uncached())


def msal_app():
    """One client per process: building it reads the tenant's sign-in endpoints from Microsoft."""
    global _msal
    with _lock:
        if _msal is None:
            _msal = build_msal_app()
        return _msal


def begin(redirect_uri):
    """Start an authorization-code flow with PKCE; msal adds the openid, profile and offline_access scopes itself.
    Returns the Microsoft URL and what the callback needs to finish the flow (the URL stays out of the cookie)."""
    flow = msal_app().initiate_auth_code_flow([], redirect_uri=redirect_uri, prompt="select_account")
    return flow["auth_uri"], {key: value for key, value in flow.items() if key != "auth_uri"}


def exchange(flow, response):
    """Swap the authorization code for the ID token. msal checks the state, the PKCE verifier and the nonce."""
    return msal_app().acquire_token_by_auth_code_flow(dict(flow), response, scopes=None)


def clean(value, limit):
    return "".join(char for char in str(value or "") if char.isprintable()).strip()[:limit]


def staff_from_claims(claims):
    """The signed-in person from ID token claims. msal decodes the token received straight from Microsoft's token
    endpoint over TLS, so the app still checks that it was issued by this tenant, for this application, and that
    the person holds a service role."""
    tenant, client = settings()
    if str(claims.get("tid", "")).lower() != tenant:
        raise Refused("tenant")
    if claims.get("aud") != client or claims.get("iss") != f"{AUTHORITY}/{tenant}/v2.0":
        raise Refused("failed")
    try:
        if float(claims.get("exp", 0)) < time.time():
            raise Refused("failed")
    except (TypeError, ValueError):
        raise Refused("failed") from None
    oid = str(claims.get("oid", "")).lower()
    if not GUID.match(oid):
        raise Refused("failed")
    upn = clean(claims.get("preferred_username") or claims.get("upn") or claims.get("email"), 120)
    name = clean(claims.get("name"), 80) or upn or "Service team member"
    who = {"oid": oid, "upn": upn or oid, "name": name}
    roles = claims.get("roles")
    held = [role for role in roles if role in STAFF_ROLES] if isinstance(roles, list) else []
    if not held:
        raise Refused("no-role", who)
    return {**who, "role": max(held, key=STAFF_ROLES.get)}


def failure_code(error, description=""):
    """A fixed code for what Microsoft reported, never its free text, so nothing untrusted reaches the page."""
    text = f"{error} {description}"
    if "AADSTS50105" in text:
        return "not-assigned"
    if "AADSTS65001" in text or "AADSTS65004" in text or "AADSTS90094" in text:
        return "consent"
    return "denied" if error == "access_denied" else "failed"


def staff_subject(person):
    """What a sign-in or sign-out is filed under on the audit trail: the person, since no order is involved."""
    return "staff-" + person["oid"].replace("-", "")


def public_staff(person):
    return {"name": person["name"], "upn": person["upn"], "role": person["role"],
            "roleLabel": role_label(person["role"])}


def record_staff_event(app, person, action, detail):
    actor = Actor("service-manager", person)
    transact(app, lambda state: audit(state, staff_subject(person), actor, action, detail))


def record_refusal(app, who):
    """The one refusal worth keeping: somebody of this tenant proved who they are but holds no service role."""
    actor = Actor("sso-refused")
    transact(app, lambda state: audit(
        state, staff_subject(who), actor, "staff.sso-denied",
        f"{who['upn']} signed in with Microsoft but holds no TürkBizTel service role"))


def finish(request, error=None, handoff=None):
    """Return the browser to the login page, with the hand-off cookie when the sign-in succeeded."""
    response = RedirectResponse("/login#sso" if error is None else f"/login#sso-error={error}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    secure = auth.secure_request(request)
    response.delete_cookie(FLOW_COOKIE, path="/auth/sso", httponly=True, samesite="lax", secure=secure)
    if handoff:
        response.set_cookie(HANDOFF_COOKIE, handoff, max_age=HANDOFF_SECONDS, httponly=True, samesite="lax",
                            secure=secure, path="/api/auth/sso")
    return response


def claim_once(handoff_id, expires):
    """A hand-off can be exchanged for a session one time only."""
    now = time.time()
    with _lock:
        for stale in [key for key, until in _used.items() if until < now]:
            del _used[stale]
        if not isinstance(handoff_id, str) or handoff_id in _used:
            return False
        _used[handoff_id] = expires
        return True


@router.get("/auth/sso/login", include_in_schema=False)
def sso_login(request: Request):
    require_sso()
    address = auth.client_ip(request)
    try:
        auth.guard(auth.LOGIN_IP, address)
    except HTTPException:
        return finish(request, "throttled")
    auth.LOGIN_IP.add(address)
    try:
        url, flow = begin(public_origin() + CALLBACK_PATH)
    except Exception:
        log.exception("Could not start the Microsoft sign-in")
        return finish(request, "unavailable")
    response = RedirectResponse(url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(FLOW_COOKIE, auth.sign_blob("sso-flow", {"f": flow, "e": int(time.time()) + FLOW_SECONDS}),
                        max_age=FLOW_SECONDS, httponly=True, samesite="lax", secure=auth.secure_request(request),
                        path="/auth/sso")
    return response


@router.get(CALLBACK_PATH, include_in_schema=False)
def sso_callback(request: Request):
    require_sso()
    flow = auth.read_blob("sso-flow", request.cookies.get(FLOW_COOKIE))
    if flow is None or not isinstance(flow.get("f"), dict):
        return finish(request, "expired")
    params = dict(request.query_params)
    if "code" not in params:
        code = failure_code(params.get("error", ""), params.get("error_description", ""))
        log.warning("Microsoft sign-in was not completed: %s", code)
        return finish(request, code)
    try:
        result = exchange(flow["f"], params)
    except ValueError:
        return finish(request, "expired")
    except RuntimeError:
        return finish(request, "failed")
    except Exception:
        log.exception("Microsoft sign-in could not be completed")
        return finish(request, "unavailable")
    if "error" in result:
        code = failure_code(result.get("error", ""), result.get("error_description", ""))
        log.warning("Microsoft refused the sign-in: %s", code)
        return finish(request, code)
    try:
        person = staff_from_claims(result.get("id_token_claims") or {})
    except Refused as refusal:
        log.warning("Sign-in refused: %s", refusal.code)
        if refusal.who:
            try:
                record_refusal(request.app, refusal.who)
            except HTTPException:
                log.warning("The refused sign-in could not be written to the audit trail")
        return finish(request, refusal.code)
    handoff = {"j": secrets.token_urlsafe(16), "p": person, "e": int(time.time()) + HANDOFF_SECONDS}
    return finish(request, None, auth.sign_blob("sso-handoff", handoff))


@router.post("/api/auth/sso/handoff")
def sso_handoff(request: Request, response: Response):
    """Turn the callback's hand-off cookie into a staff session for this browser tab, once."""
    require_sso()
    auth.require_csrf(request)
    address = auth.client_ip(request)
    auth.guard(auth.LOGIN_IP, address)
    blob = auth.read_blob("sso-handoff", request.cookies.get(HANDOFF_COOKIE))
    person = blob.get("p") if blob else None
    valid = (isinstance(person, dict) and GUID.match(str(person.get("oid", ""))) and person.get("role") in STAFF_ROLES
             and all(isinstance(person.get(key), str) for key in ("upn", "name")))
    if not valid or not claim_once(blob.get("j"), blob["e"]):
        auth.LOGIN_IP.add(address)
        raise HTTPException(401, "That sign-in has expired or was already used. Please sign in again.")
    person = {key: person[key] for key in ("oid", "upn", "name", "role")}
    session = auth.issue_session("admin", person["oid"], auth.staff_version(),
                                 seconds=auth.staff_session_seconds(), person=person)
    record_staff_event(request.app, person, "staff.signed-in", f"Signed in with Microsoft from {address}")
    response.delete_cookie(HANDOFF_COOKIE, path="/api/auth/sso", httponly=True, samesite="lax",
                           secure=auth.secure_request(request))
    return {"role": "admin", "staff": public_staff(person), **auth.grant(request, response, session)}
