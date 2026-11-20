import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_story_is_grounded_in_current_state_only():
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    assert story["source"]["file"] == "AMA_Assignment30_HaiderRaza.pdf"
    assert story["source"]["page"] == 1
    assert story["baseline"] == {"activationDays": 35, "falloutPercent": 30}
    assert len(story["scenes"]) == 9
    narration = " ".join(beat["text"] for scene in story["scenes"] for beat in scene["beats"]).lower()
    for excluded in ("autonomous", "retry-once", "under seven", "under eight", "mcp", "supervisor"):
        assert excluded not in narration
    assert "imagine" in narration
    assert "not a recorded incident" in story["scenes"][2]["label"].lower()
    assert "not a measured wait for any individual team" in narration
    assert "fictional operator, not those companies" in narration


def test_story_timing_and_deliverables():
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    assert 60 < sum(scene["duration"] for scene in story["scenes"]) < 120
    for scene in story["scenes"]:
        previous_end = 0
        for beat in scene["beats"]:
            assert beat["start"] >= previous_end
            assert beat["duration"] > 0
            previous_end = beat["start"] + beat["duration"]
        assert previous_end <= scene["duration"]
    assert (STATIC / "current-scenario.mp4").stat().st_size > 100_000
    assert (STATIC / "current-scenario-poster.jpg").stat().st_size > 10_000
    assert (STATIC / "current-scenario.vtt").read_text(encoding="utf-8").startswith("WEBVTT\n")


def test_business_film_has_no_unexplained_technical_jargon():
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    visible = " ".join(
        " ".join([scene["chapter"], scene["title"], scene["label"],
                  *[beat["text"] for beat in scene["beats"]]])
        for scene in story["scenes"]
    )
    renderer = re.sub(r'case "[a-z]+":', "", (STATIC / "journey.js").read_text(encoding="utf-8"))
    captions = (STATIC / "current-scenario.vtt").read_text(encoding="utf-8")
    forbidden = r"\b(?:APIs?|CRM|SD-WAN|provisioning|fallout|churn|system domains|interface)\b"
    for surface in (visible, renderer, captions):
        assert not re.search(forbidden, surface, re.IGNORECASE)
    assert "cannot exchange order details directly" in visible
    assert "thirty percent of orders get stuck" in visible


def test_turkish_operator_context_has_local_assets_and_provenance():
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    assert [op["name"] for op in story["operators"]] == ["Turkcell", "Türk Telekom", "Vodafone Türkiye"]
    for operator in story["operators"]:
        assert (STATIC / operator["asset"]).is_file()
        assert operator["assetSource"].startswith("https://")
    player = (STATIC / "journey.html").read_text(encoding="utf-8")
    assert "not the subject of these operational claims" in player
    assert "no affiliation, endorsement" in player


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="FFprobe is optional local presentation tooling")
def test_encoded_current_film_is_under_two_minutes_with_audio():
    from scripts.Render_Story import verify_video

    encoded = verify_video(STATIC / "current-scenario.mp4")
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    assert encoded == pytest.approx(sum(scene["duration"] for scene in story["scenes"]), abs=.15)
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=r_frame_rate", "-of", "json", str(STATIC / "current-scenario.mp4"),
    ], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["streams"][0]["r_frame_rate"] in ("24/1", "30/1")
