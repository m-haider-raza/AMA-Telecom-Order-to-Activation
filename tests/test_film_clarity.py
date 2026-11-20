import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.media_proof import assert_preserved

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


@pytest.fixture(params=["solution-clarity-v1", "solution-clarity-v2"])
def edition(request):
    return request.param


def load(suffix, edition):
    return json.loads((STATIC / f"{edition}-{suffix}.json").read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def test_every_agent_has_a_responsibility_output_exception_human_and_benefit(edition):
    data = load("data", edition)
    agents = [s for s in data["scenes"] if s["actor"] == "agent"]
    assert [s["agent"] for s in agents] == list(range(5))
    for scene in agents:
        assert len(scene["panels"]) == 5 and scene["minimumDuration"] >= 17
        assert [p["label"].split(" / ")[0] for p in scene["panels"]] == [
            "DO", "OUTPUT", "EXCEPTION", "HUMAN", "IMPACT",
        ]
    assert data["narration"]["kind"] == "author-voice-synthetic"
    assert data["voiceTreatment"]["pitchPreserved"] and data["voiceTreatment"]["tempo"] == 1.08
    assert 0 < sum(s["duration"] for s in data["scenes"]) < 180


def test_proposed_exceptions_targets_and_residency_are_not_claimed_as_demo_results(edition):
    scenes = {s["id"]: s for s in load("data", edition)["scenes"]}
    assert scenes["decomposition"]["panels"][3]["proposed"]
    assert all(p["proposed"] for p in scenes["recovery"]["panels"][2:4])
    assert all(p["proposed"] for p in scenes["communication"]["panels"][2:4])
    assert "intake" in scenes["decomposition"]["panels"][2]["text"]
    assert "one simulated retry" in scenes["recovery"]["panels"][0]["text"]
    assert "return to provisioning" in scenes["recovery"]["panels"][1]["text"]
    assert "no SMS or email" in scenes["communication"]["beats"][1]["text"]
    assert "saved stage" in scenes["human"]["panels"][3]["text"]
    assert scenes["impact"]["panels"][2]["label"] == "ASSIGNMENT TARGETS / NOT RESULTS"
    assert "35 days -> under 7" in scenes["impact"]["panels"][2]["text"]
    assert "30% -> under 8%" in scenes["impact"]["panels"][2]["text"]
    assert "first-time-right" in scenes["impact"]["panels"][3]["text"]
    assert scenes["residency"]["mode"] == "proposed"
    assert "Not full certification" in scenes["residency"]["panels"][0]["text"]
    assert "disconnected" in scenes["residency"]["panels"][3]["text"]


def test_focal_3d_scene_and_readable_panels_do_not_overlap(edition):
    data, rows = load("data", edition), load("motion", edition)
    assert len(rows) == len(data["scenes"]) == 10
    for row, scene in zip(rows, data["scenes"], strict=True):
        assert row["scene"] == scene["id"]
        assert row["visibleAgents"] == (1 if scene["actor"] == "agent" else 0)
        assert len(row["visibleHumans"]) <= 1
        assert row["perspectiveCamera"] and row["shadows"] and row["geometryTriangles"] > 100
        assert row["industryContextLogo"] and row["noEndorsementLabel"]
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        boxes = list(row["layout"].values())
        for i, a in enumerate(boxes):
            for b in boxes[i+1:]:
                assert (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or
                        a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])
        assert len(row["beatChecks"]) == 2
        for beat in row["beatChecks"]:
            assert beat["captionFontSize"] >= 28 and beat["captionLines"] <= 2
            for panel in beat["panelBounds"]:
                assert panel["fontSize"] >= 26 and panel["lines"] <= 2
                assert panel["measuredWidth"] <= panel["width"]
    renderer = (ROOT / "scripts" / "film3d" / "clarity.js").read_text()
    assert "MAIN: VALIDATE > PLAN > PROVISION > UPDATE" in renderer
    assert "TIMEOUT ONLY: RECOVER > PROVISION" in renderer


def test_official_icons_and_all_earlier_video_bytes_are_preserved(edition):
    data, proof = load("data", edition), load("evidence", edition)
    assert proof["existingVideosUnchanged"] and proof["layoutVerified"]
    assert {"current-scenario.mp4", "solution.mp4", "solution-tower-v1.mp4",
            "solution-3d-v1.mp4"} <= proof["preservedVideos"].keys()
    assert_preserved(proof["preservedVideos"])
    assert proof["scriptSHA256"] == digest(ROOT / "scripts" / "film3d" / "clarity-story.json")
    for name, sha in data["microsoftIcons"]["unchangedSvgSHA256"].items():
        assert digest(STATIC / "film3d-assets" / name) == sha
    assert next(r for r in load("motion", edition) if r["scene"] == "residency")["officialIcons"] == [
        "Azure Local", "Azure Arc", "Azure Container Registry", "Azure Monitor",
    ]
    html = (STATIC / f"{edition}.html").read_text(encoding="utf-8")
    assert f'src="/static/{edition}.mp4"' in html and "film-clarity.js" in html
    assert 'href="/static/solution-3d-v1.html"' in html
    assert "proposed production gates" in html


def test_existing_clarity_edition_is_refused_before_any_assets_change(edition):
    if not importlib.util.find_spec("playwright"):
        pytest.skip("Optional rendering guard requires Playwright")
    before = {s: digest(STATIC / f"{edition}{s}") for s in [".mp4", "-data.json", ".html"]}
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "Render_Clarity.py"), "--edition", edition, "--preview-only",
    ], capture_output=True, text=True)
    assert result.returncode != 0 and "Existing video is protected" in result.stderr
    assert before == {s: digest(STATIC / f"{edition}{s}") for s in before}


def test_encoded_clarity_film_has_ten_chapters_voice_captions_and_full_hd(edition):
    if not shutil.which("ffprobe"):
        pytest.skip("Optional encoded inspection requires ffprobe")
    media = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json",
        str(STATIC / f"{edition}.mp4"),
    ], check=True, capture_output=True, text=True).stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert any(s.get("width") == 1920 and s.get("height") == 1080 for s in media["streams"])
    assert {"audio", "video", "subtitle"} <= {s["codec_type"] for s in media["streams"]}
    assert len(media["chapters"]) == 10
    assert (STATIC / f"{edition}.vtt").read_text().count(" --> ") == 20
