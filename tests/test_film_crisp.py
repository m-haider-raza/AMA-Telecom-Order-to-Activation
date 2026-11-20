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
EDITION = "solution-tower-v2"


def load(suffix):
    return json.loads((STATIC / f"{EDITION}-{suffix}.json").read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def test_selected_tower_style_has_a_crisper_story_and_a_different_non_cloned_voice():
    data = load("data")
    assert data["presentation"] == "crisp-control-tower"
    assert data["title"] == "One order. One accountable flow."
    assert data["sourceVideo"]["file"] == "solution-tower-v1.mp4"
    assert data["sourceVideo"]["sha256"] == digest(STATIC / "solution-tower-v1.mp4")
    assert data["narration"]["kind"] == "local-preset-neural"
    assert data["narration"]["voice"] == "af_heart"
    assert data["narration"]["referenceAudioUsed"] is False
    assert data["narration"]["speed"] > 1
    assert len(data["scenes"]) == 12
    assert sum(s["duration"] for s in data["scenes"]) < 180
    original = json.loads((STATIC / "solution-tower-v1-data.json").read_text())
    assert sum(s["duration"] for s in data["scenes"]) < sum(s["duration"] for s in original["scenes"])
    assert data["avatars"] == original["avatars"]
    assert [b["text"] for s in data["scenes"] for b in s["beats"]] != [
        b["text"] for s in original["scenes"] for b in s["beats"]
    ]


def test_five_responsibilities_exceptions_and_outcome_mechanisms_remain_explicit():
    scenes = {s["id"]: s for s in load("data")["scenes"]}
    for i, name in enumerate(["validation", "decomposition", "provisioning", "recovery", "communication"]):
        assert scenes[name]["agentStates"][0][i] == "active"
        assert len(scenes[name]["cards"]) == 3
    text = " ".join(b["text"] for s in scenes.values() for b in s["beats"])
    for phrase in ["site and capacity", "service and resource orders", "allocates resources once",
                   "retries once", "Turkish and English portal", "manager approves or rejects",
                   "Partners cannot approve", "No repeated rekeying", "targets, not results",
                   "proposed human triage", "not certified", "proposed, not deployed",
                   "eligible disconnected operations"]:
        assert phrase in text
    assert scenes["residency"]["mode"] == scenes["hybrid"]["mode"] == "proposed"


def test_exception_states_and_cloud_icons_match_the_message():
    scenes = {s["id"]: s for s in load("data")["scenes"]}
    assert scenes["intake"]["agentStates"][1][0] == "waiting"
    assert scenes["validation"]["agentStates"][1][:2] == ["held", "waiting"]
    assert "order.validated" not in scenes["validation"]["events"][1]
    assert scenes["recovery"]["agentStates"][1][2:4] == ["held", "held"]
    assert "proposed human triage" in scenes["recovery"]["events"][1]
    assert scenes["hybrid"]["parallelLanes"] is True
    metadata, blocked = scenes["hybrid"]["cards"][1:]
    assert "Azure Monitor aggregates" in metadata["body"]
    assert "azure-monitor.svg" in metadata["icons"]
    assert "icon" not in blocked and "icons" not in blocked
    assert all(not s["chapter"][:2].isdigit() for s in scenes.values())


def test_crisp_cards_tower_and_each_encoded_scene_are_readable_and_animated():
    rows = load("motion")
    assert len(rows) == 12
    for row in rows:
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        assert row["tower"]["height"] > 500 and len({a["signature"] for a in row["avatars"]}) == 5
        assert row["presetNarrator"] and row["distinctVoice"]
        for beat in row["beatChecks"]:
            assert 0 < beat["captionLines"] <= 2
            for card in beat["cardBounds"]:
                assert card["fontSize"] >= 22
                assert card["headingWidth"] <= 280 and card["bodyWidth"] <= 280
    assert set(next(r for r in rows if r["scene"] == "hybrid")["officialIcons"]) == {
        "container-registry.svg", "azure-arc.svg", "azure-monitor.svg",
    }


def test_previous_videos_and_official_icons_are_preserved():
    data, proof = load("data"), load("evidence")
    assert proof["distinctPresetNarrator"] and proof["referenceAudioUsed"] is False
    assert proof["existingVideosUnchanged"] and proof["fullDecodeVerified"]
    assert "solution-tower-v1.mp4" in proof["preservedVideos"]
    assert "solution-clarity-v2.mp4" in proof["preservedVideos"]
    assert_preserved(proof["preservedVideos"])
    for name, sha in data["microsoftIcons"]["unchangedSvgSHA256"].items():
        assert digest(STATIC / "film3d-assets" / name) == sha
    assert proof["scriptSHA256"] == digest(ROOT / "scripts" / "film3d" / "tower-crisp-story.json")
    html = (STATIC / f"{EDITION}.html").read_text(encoding="utf-8")
    assert "tower-crisp-theme.js" in html and f'{EDITION}.mp4' in html
    assert 'href="/static/solution-tower-v1.html"' in html
    assert (STATIC / f"{EDITION}.vtt").read_text().count(" --> ") == 24


def test_crisp_renderer_refuses_an_existing_edition_before_assets_change():
    if not importlib.util.find_spec("playwright"):
        pytest.skip("Optional renderer guard requires Playwright")
    before = {s: digest(STATIC / f"{EDITION}{s}") for s in [".mp4", "-data.json", ".html"]}
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "Render_Crisp.py"), "--edition", EDITION,
        "--narration-dir", str(ROOT / "missing-private-segments"), "--preview-only",
    ], capture_output=True, text=True)
    assert result.returncode != 0 and "Existing video is protected" in result.stderr
    assert before == {s: digest(STATIC / f"{EDITION}{s}") for s in before}


def test_crisp_mp4_is_full_hd_has_a_new_audio_track_and_all_chapters():
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        pytest.skip("Optional encoded checks require FFmpeg")
    media = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json",
        str(STATIC / f"{EDITION}.mp4"),
    ], check=True, capture_output=True, text=True).stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert any(s.get("width") == 1920 and s.get("height") == 1080 for s in media["streams"])
    assert {"audio", "video", "subtitle"} <= {s["codec_type"] for s in media["streams"]}
    assert len(media["chapters"]) == 12
    hashes = [subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(STATIC / name), "-map", "0:a", "-c:a", "copy",
        "-f", "hash", "-hash", "sha256", "pipe:1",
    ], check=True, capture_output=True).stdout for name in [f"{EDITION}.mp4", "solution-tower-v1.mp4"]]
    assert hashes[0] != hashes[1]
