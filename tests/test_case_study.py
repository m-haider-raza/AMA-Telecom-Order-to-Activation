"""The guided assignment page: public, self-contained, safe under the strict policy and free of credentials."""

import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.security import _requests

ADMIN = "test-manager-token-not-a-real-secret-00001"
PARTNER = "test-partner-token-not-a-real-secret-00001"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
DECK = STATIC / "case-study"
PPTX = DECK / "Agentic_Order_to_Activation_Architecture.pptx"
PDF = DECK / "Agentic_Order_to_Activation_Architecture.pdf"
HTML = (STATIC / "case-study.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "case-study.js").read_text(encoding="utf-8")
STYLES = (STATIC / "case-study.css").read_text(encoding="utf-8")
SCENES = ["intro", "today", "architecture", "solution", "app"]
# Nothing the page, the script or the published deck carries may contain these: they are the signs of the
# private hand-off slide (local paths, loop-back links, demo sign-ins, the admin token) or of the owner and
# sensitivity-label properties Office adds to exported files.
LEAKS = ("moraz", "C:\\Users", "session-state", "127.0.0.1", "localhost", "demo-credentials", "adminToken",
         "partnerToken", "X-Session", "Bearer", "MSIP", "APRv9")


@pytest.fixture(params=["demo", "live"])
def site(request, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STATE_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setenv("DEMO_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("DEMO_PARTNER_TOKEN", PARTNER)
    monkeypatch.setenv("DEMO_TICK_SECONDS", "3600")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    if request.param == "live":
        monkeypatch.setenv("AMA_MODE", "live")
    else:
        monkeypatch.delenv("AMA_MODE", raising=False)
    _requests.clear()
    auth.reset_throttles()
    with TestClient(app) as client:
        yield client


class Inventory(HTMLParser):
    """What the page is made of, read without a browser."""

    def __init__(self):
        super().__init__()
        self.ids, self.classes = set(), set()
        self.scripts, self.inline = [], []
        self.scenes, self.rail, self.thumbs, self.acts = [], [], [], []
        self.films, self.chapters, self.frame_pages = {}, {}, []
        self.current_film = None

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        classes = (found.get("class") or "").split()
        self.classes.update(classes)
        if "id" in found:
            self.ids.add(found["id"])
        if tag == "script":
            (self.scripts if "src" in found else self.inline).append(found.get("src", "<script>"))
        if tag == "style":
            self.inline.append("<style>")
        self.inline += [f"<{tag} {name}>" for name in found if name == "style" or name.startswith("on")]
        if tag == "section" and "scene" in classes:
            self.scenes.append(found["data-scene"])
        if tag == "a" and "stop" in classes:
            self.rail.append(found["data-stop"])
        if tag == "button" and "data-src" in found:
            self.thumbs.append((int(found["data-slide"]), found["data-src"]))
        if tag == "button" and "act" in classes:
            self.acts.append((int(found["data-slide"]), int(found["data-last"])))
        if tag == "video":
            self.current_film = {"poster": found["poster"]}
            self.films[self.scenes[-1]] = self.current_film
        if tag in ("source", "track") and self.current_film is not None:
            self.current_film[tag] = found["src"]
        if tag == "button" and "chip" in classes:
            self.chapters.setdefault(self.scenes[-1], []).append(float(found["data-t"]))
        if tag == "button" and "data-frame" in found:
            self.frame_pages.append(found["data-frame"])

    def handle_endtag(self, tag):
        if tag == "video":
            self.current_film = None


def inventory():
    page = Inventory()
    page.feed(HTML)
    return page


def static_paths():
    return sorted({found.split("?")[0] for found in re.findall(r"/static/[^\"'\s<>#]+", HTML)})


def test_page_is_public_in_both_modes(site):
    for path in ("/case-study", "/assignment"):
        reply = site.get(path)
        assert reply.status_code == 200, path
        assert reply.headers["content-type"].startswith("text/html")
        assert reply.headers["cache-control"] == "no-cache"
        assert reply.content == (STATIC / "case-study.html").read_bytes()
    assert "AMA Case Study 30" in site.get("/case-study").text


def test_page_and_the_app_it_shows_can_only_be_framed_by_this_origin(site):
    for path in ("/case-study", "/", "/order", "/track"):
        reply = site.get(path)
        assert reply.status_code == 200, path
        assert reply.headers["x-frame-options"] == "SAMEORIGIN", path
        policy = reply.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in policy and "script-src 'self'" in policy, path
        assert "unsafe" not in policy and "http" not in policy, path


def test_page_needs_no_inline_code_and_no_third_party_resources():
    page = inventory()
    assert page.inline == []
    assert page.scripts == ["/static/case-study.js?v=tb1"]
    for source in (HTML, SCRIPT, STYLES):
        assert not re.search(r"https?://|@import|url\(", source)
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   ".style.", "cssText", "setAttribute(\"style\""):
        assert unsafe not in SCRIPT, unsafe


def test_page_carries_no_credentials_or_local_paths():
    for source in (HTML, SCRIPT, STYLES):
        for leak in LEAKS:
            assert leak not in source, leak


def test_every_file_the_page_uses_is_served(site):
    paths = static_paths()
    assert {"/static/case-study.css", "/static/case-study.js", "/static/current-scenario.mp4",
            "/static/solution-tower-v6.mp4", "/static/case-study/slide-14.webp"} <= set(paths)
    for path in paths:
        reply = site.get(path, headers={"Range": "bytes=0-0"})
        assert reply.status_code in (200, 206), path
        assert reply.content, path


def test_script_and_markup_agree():
    page = inventory()
    assert page.scenes == SCENES and page.rail == SCENES[1:]
    for name in SCENES:
        assert f"h-{name}" in page.ids
    for selector in re.findall(r"\$\$?\(\s*\"([^\"]+)\"", SCRIPT):
        for kind, name in re.findall(r"([#.])([A-Za-z][\w-]*)", selector):
            assert name in (page.ids if kind == "#" else page.classes), selector

    total = int(re.search(r"const SLIDES = (\d+);", SCRIPT).group(1))
    assert [number for number, _ in page.thumbs] == list(range(1, total + 1))
    assert [path for _, path in page.thumbs] == [f"/static/case-study/slide-{n:02d}.webp" for n in range(1, total + 1)]
    assert [n for first, last in page.acts for n in range(first, last + 1)] == list(range(1, total + 1))
    assert f"All {total} slides" in HTML and f"1 / {total}" in HTML

    for name in re.findall(r"classList\.(?:add|remove|toggle)\(\s*\"([\w-]+)\"", SCRIPT):
        assert f".{name}" in STYLES, name


@pytest.mark.parametrize("name", ["current-scenario.mp4", "solution-tower-v6.mp4"])
def test_films_can_be_seeked(site, name):
    reply = site.get(f"/static/{name}", headers={"Range": "bytes=100-199"})
    assert reply.status_code == 206
    assert reply.headers["content-range"].startswith("bytes 100-199/")
    assert reply.headers["content-type"] == "video/mp4" and len(reply.content) == 100


@pytest.mark.parametrize("name", ["current-scenario.vtt", "solution-tower-v6.vtt"])
def test_narration_is_served_as_webvtt(site, name):
    reply = site.get(f"/static/{name}")
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/vtt")
    assert reply.content.decode("utf-8").startswith("WEBVTT") and "-->" in reply.text


@pytest.mark.parametrize("scene,film,data", [
    ("today", "current-scenario", "journey-data.json"),
    ("solution", "solution-tower-v6", "solution-tower-v6-data.json"),
])
def test_film_sources_and_chapter_jumps_match_the_latest_narration(scene, film, data):
    page = inventory()
    assert page.films[scene] == {
        "poster": f"/static/{film}-poster.jpg",
        "source": f"/static/{film}.mp4",
        "track": f"/static/{film}.vtt",
    }
    story = json.loads((STATIC / data).read_text(encoding="utf-8"))
    starts, position = [], 0
    for chapter in story["scenes"]:
        starts.append(position)
        position += chapter["duration"]
    assert page.chapters[scene] == pytest.approx(starts, abs=.001)


def test_service_team_signin_is_a_direct_entry_in_the_live_app(site):
    assert "/login#staff" in inventory().frame_pages
    login = site.get("/login")
    assert login.status_code == 200 and 'id="panel-staff"' in login.text
    assert "Sign in with your access token" in login.text
    assert login.text.index('data-go="staff"') < login.text.index('id="signin-email"')


def slide_texts(deck):
    """The text of every slide in presentation order (file names do not follow it)."""
    ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    rels = {rel.get("Id"): rel.get("Target")
            for rel in ElementTree.fromstring(deck.read("ppt/_rels/presentation.xml.rels"))}
    texts = []
    for entry in ElementTree.fromstring(deck.read("ppt/presentation.xml")).findall("p:sldIdLst/p:sldId", ns):
        target = rels[entry.get(f"{{{ns['r']}}}id")].removeprefix("/").removeprefix("ppt/")
        part = ElementTree.fromstring(deck.read(f"ppt/{target}"))
        texts.append(" ".join(node.text or "" for node in part.iter(f"{{{ns['a']}}}t")))
    return texts


def test_published_deck_shows_the_cost_of_implementation_before_the_risks():
    with zipfile.ZipFile(PPTX) as deck:
        texts = slide_texts(deck)
    assert len(texts) == 14
    assert "cost of implementation" in texts[12].lower() and "payback" in texts[12].lower()
    assert "risks" in texts[13].lower()
    assert 'data-slide="13" data-kicker="Cost of implementation' in HTML


def test_published_deck_is_the_clean_web_edition():
    with zipfile.ZipFile(PPTX) as deck:
        names = deck.namelist()
        slides = [name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        assert len(slides) == 14 and "ppt/slides/slide15.xml" not in names
        for name in names:
            if name.endswith((".xml", ".rels")):
                text = deck.read(name).decode("utf-8", "replace")
                for leak in LEAKS:
                    assert leak not in text, (name, leak)
    pdf = PDF.read_bytes()
    assert pdf.startswith(b"%PDF")
    for leak in LEAKS:
        assert leak.encode() not in pdf, leak
