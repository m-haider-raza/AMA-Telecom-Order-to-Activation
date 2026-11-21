"""Public rebranding must not break old links, narration, sessions or stored conversations."""

import base64
import copy
import hashlib
import hmac
import json
import re
import time
import zipfile
from pathlib import Path

import pytest

from app import auth, delivery, partners
from app.branded_static import FILMS
from tests.test_accounts import ADMIN
from tests.test_accounts import live as accounts_live  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


@pytest.fixture
def brand_site(accounts_live):  # noqa: F811
    return accounts_live


def test_public_pages_and_api_metadata_use_the_new_brand(brand_site):
    for path in ("/", "/order", "/track", "/login", "/account", "/partner", "/case-study", "/openapi.json",
                 "/static/journey.html", "/static/solution-tower-v4.html", "/static/solution-tower-v5.html",
                 "/static/solution-tower-v6.html"):
        reply = brand_site.get(path)
        assert reply.status_code == 200, path
        assert "TürkBizTel" in reply.text, path
        assert not re.search(r"MyTurk(?:ei|ey)", reply.text, re.I), path
    for name in ("mark", "icons"):
        old = brand_site.get(f"/static/myturkei-{name}.svg")
        new = brand_site.get(f"/static/turkbiztel-{name}.svg")
        assert old.status_code == new.status_code == 200 and old.content == new.content


def test_existing_film_links_serve_seekable_branded_editions_and_preserve_sources(brand_site):
    evidence = json.loads((STATIC / "turkbiztel-media.json").read_text(encoding="utf-8"))
    assert {row["source"] for row in evidence["films"]} == {name + ".mp4" for name in FILMS}
    for row in evidence["films"]:
        original, branded = STATIC / row["source"], STATIC / row["file"]
        with original.open("rb") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == row["sourceSHA256"]
        reply = brand_site.get(f"/static/{original.name}", headers={"Range": "bytes=100-199"})
        with branded.open("rb") as source:
            source.seek(100)
            assert reply.content == source.read(100)
        assert reply.status_code == 206 and reply.headers["content-type"] == "video/mp4"
        assert reply.headers["content-range"] == f"bytes 100-199/{branded.stat().st_size}"


def test_downloadable_presentation_carries_the_brand_on_every_public_slide():
    folder = STATIC / "case-study"
    with zipfile.ZipFile(folder / "Agentic_Order_to_Activation_Architecture.pptx") as deck:
        slides = [name for name in deck.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        assert len(slides) == 16
        assert all("TürkBizTel" in deck.read(name).decode("utf-8") for name in slides)


def test_generated_mail_uses_new_brand_without_rewriting_customer_text(brand_site):
    messages = [delivery.account_message(purpose, "Deniz", "123456", language)
                for purpose in ("verify", "reset") for language in ("en", "tr")]
    messages += [delivery.invite_message("Deniz", "Aegean", "/login#invite=test", "tr"),
                 delivery.concern_message("Deniz", "Invoice", "A customer note", "/partner", "en")]
    assert all("TürkBizTel" in subject and "MyTurkei" not in subject for subject, _ in messages)
    assert all("TürkBizTel" in body for _, body in messages[:5])
    subject, body, _ = delivery.compose({"id": "AMA-123", "language": "en"}, {
        "en": "The customer wrote MyTurkei in their note.", "tr": "Müşteri notu",
    })
    assert subject == "TürkBizTel order AMA-123" and "MyTurkei" in body


def test_pre_rebrand_cookie_and_tab_sessions_remain_valid(brand_site):
    payload = base64.urlsafe_b64encode(json.dumps({
        "r": "admin", "s": "staff", "v": auth.admin_version(), "e": int(time.time()) + 600,
    }, separators=(",", ":")).encode()).rstrip(b"=").decode()
    key = hmac.new(ADMIN.encode(), b"myturkei.session.v1", hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()).rstrip(b"=")
    token = payload + "." + signature.decode()
    brand_site.cookies.set("myturkei_session", token)
    assert brand_site.get("/api/state").status_code == 200
    brand_site.cookies.clear()
    assert brand_site.get("/api/state", headers={"X-Session-Mode": "tab", "X-Session": token}).status_code == 200


def test_historical_staff_labels_change_only_in_the_view():
    concern = {key: "example" for key in ("id", "partnerId", "orderId", "category", "subject", "status",
                                         "createdAt", "updatedAt", "createdByName")}
    concern["messages"] = [
        {"from": "staff", "name": "MyTurkei service team", "text": "MyTurkei is in this historical note."},
        {"from": "staff", "name": "Deniz Kaya", "text": "A signed reply."},
        {"from": "partner", "name": "MyTurkei service team", "text": "A partner's chosen name."},
    ]
    before = copy.deepcopy(concern)
    view = partners.concern_view(concern)
    assert view["messages"][0]["name"] == "TürkBizTel service team"
    assert view["messages"][0]["text"] == before["messages"][0]["text"]
    assert view["messages"][1:] == before["messages"][1:]
    view["messages"][1]["name"] = "A changed view"
    assert concern == before
