import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_solution_narrative_covers_workflow_and_boundaries():
    story = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
    assert len(story["scenes"]) == 14
    assert story["presentation"] == "character-and-event-animation"
    assert {s["id"] for s in story["scenes"]} >= {
        "validation", "decomposition", "provisioning", "recovery", "human", "communication",
        "partners", "standards", "residency", "hybrid", "strict", "impact",
    }
    narration = " ".join(b["text"] for s in story["scenes"] for b in s["beats"]).lower()
    for boundary in ("not measured results", "synthetic", "not certified", "once", "partner token is denied",
                     "proposed, not deployed", "azure local", "representative pilot",
                     "system metadata", "disconnected operations", "tmf622", "tmf641"):
        assert boundary in narration
    assert "no affiliation" in story["branding"]["disclaimer"].lower()
    assert story["branding"]["source"].startswith("https://commons.wikimedia.org/")

    for scene in story["scenes"]:
        assert len(scene["agentStates"]) == len(scene["beats"]) == len(scene["events"])
        assert all(len(states) == 5 for states in scene["agentStates"])
    scenes = {s["id"]: s for s in story["scenes"]}
    for scene_id, index in (("validation", 0), ("decomposition", 1), ("provisioning", 2),
                            ("recovery", 3), ("communication", 4)):
        assert scenes[scene_id]["agentStates"][0][index] == "active"
        assert scenes[scene_id]["agentStates"][0].count("active") == 1
    assert scenes["human"]["agentStates"][0][2] == "held"
    assert all(scenes[name]["mode"] == "proposed" for name in ("residency", "hybrid", "strict"))
    assert all("image" not in s for s in story["scenes"])


def test_solution_timing_media_and_captures():
    story = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
    assert 0 < sum(s["duration"] for s in story["scenes"]) <= 179
    assert sum(len(b["text"].split()) for s in story["scenes"] for b in s["beats"]) <= 350
    assert story["narration"]["kind"] == "author-voice-synthetic"
    assert "locally" in story["narration"]["disclosure"]
    for scene in story["scenes"]:
        end = 0
        for beat in scene["beats"]:
            assert beat["start"] >= end
            assert beat["duration"] > 0
            end = beat["start"] + beat["duration"]
        assert end <= scene["duration"] + .0001
        if "image" in scene:
            assert (STATIC / scene["image"]).stat().st_size > 10_000
    assert (STATIC / "solution.mp4").stat().st_size > 100_000
    assert (STATIC / "solution-poster.jpg").stat().st_size > 10_000
    captions = (STATIC / "solution.vtt").read_text(encoding="utf-8")
    assert captions.startswith("WEBVTT\n")
    assert captions.count(" --> ") == sum(len(s["beats"]) for s in story["scenes"])


def test_solution_capture_evidence():
    evidence = json.loads((STATIC / "solution-evidence.json").read_text(encoding="utf-8"))
    assert evidence["syntheticOnly"] and evidence["auditChainValid"]
    assert evidence["partnerApprovalStatus"] == 403
    orders = evidence["orders"]
    assert orders["auto-recovery"]["recoveryAttempts"] == 1
    for name in ("human-exception-before", "change-window-before", "address-before"):
        assert not orders[name]["reserved"]
    for name in ("happy", "auto-recovery", "human-exception-after", "change-window-after",
                 "address-after", "partner"):
        assert orders[name]["state"] == "completed"
        assert orders[name]["reserved"]
        assert all(n["tr"] and n["en"] and not n["deliveredExternally"] for n in orders[name]["notifications"])
    assert orders["partner"]["actor"] == "wholesale-partner"


def test_encoded_film_is_under_three_minutes_with_audio_captions_and_chapters():
    if not shutil.which("ffprobe"):
        pytest.skip("Optional film inspection requires FFmpeg / ffprobe")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters",
        "-of", "json", str(STATIC / "solution.mp4"),
    ], check=True, capture_output=True, text=True)
    media = json.loads(result.stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert {s["codec_type"] for s in media["streams"]} >= {"video", "audio", "subtitle"}
    assert len(media["chapters"]) == 14
    assert "author" in media["format"]["tags"]["comment"].lower()


def test_every_scene_has_measured_diagram_motion():
    motion = json.loads((STATIC / "solution-motion.json").read_text(encoding="utf-8"))
    story = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
    assert {s["scene"] for s in motion} == {s["id"] for s in story["scenes"]}
    assert all(s["changedPixels"] >= 500 for s in motion)
    assert all(s["encodedChangedPixels"] >= 500 for s in motion)
    modes = {s["scene"]: s["mode"] for s in motion}
    assert all(modes[name] == "proposed" for name in ("residency", "hybrid", "strict"))


def test_solution_player_is_linked_and_accessible():
    html = (STATIC / "solution.html").read_text(encoding="utf-8")
    assert 'id="chapters"' in html and 'id="transcript"' in html
    assert 'kind="captions"' in html and "solution.vtt" in html
    assert 'src="/static/solution.mp4"' in html
    assert "/static/solution.html" in (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="animation-play"' in html and 'id="references"' in html
    assert "proposed" in html.lower() and "platform metadata" in html.lower()
