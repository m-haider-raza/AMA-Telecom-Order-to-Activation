import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.media_proof import assert_preserved

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
EDITION = "solution-tower-v1"


def load(suffix):
    return json.loads((STATIC / f"{EDITION}-{suffix}.json").read_text(encoding="utf-8"))


def test_tower_edition_preserves_the_story_with_energetic_author_voice():
    story = load("data")
    original = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
    assert story["visualTheme"] == "orchestration-control-tower"
    assert story["narration"]["kind"] == "author-voice-synthetic"
    assert story["voiceTreatment"]["tempo"] == 1.10
    assert story["voiceTreatment"]["pitchPreserved"]
    assert sum(s["duration"] for s in story["scenes"]) < 180
    assert len(story["scenes"]) == len(original["scenes"]) == 14
    for scene, source in zip(story["scenes"], original["scenes"], strict=True):
        assert scene["id"] == source["id"]
        assert scene["mode"] == source["mode"]
        assert scene["agentStates"] == source["agentStates"]
        assert [b["text"] for b in scene["beats"]] == [b["text"] for b in source["beats"]]
        assert scene["duration"] == pytest.approx(source["duration"] / 1.10, abs=.00001)
        for beat, original_beat in zip(scene["beats"], source["beats"], strict=True):
            assert beat["start"] == pytest.approx(original_beat["start"] / 1.10, abs=.00001)
            assert beat["duration"] == pytest.approx(original_beat["duration"] / 1.10, abs=.00001)


def test_five_agents_have_distinct_characters_and_the_tower_overlooks_them():
    story = load("data")
    assert [a["role"] for a in story["avatars"]] == story["agents"]
    assert len({a["character"] for a in story["avatars"]}) == 5
    assert len({a["signature"] for a in story["avatars"]}) == 5
    motion = load("motion")
    assert {row["scene"] for row in motion} == {s["id"] for s in story["scenes"]}
    for row in motion:
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        assert row["tower"]["height"] > 500 and row["tower"]["y"] < 300
        assert len(set(row["avatarSignatures"])) == 5
    validation = next(row for row in motion if row["scene"] == "validation")
    recovery = next(row for row in motion if row["scene"] == "recovery")
    assert validation["activeAgent"] == 0 and recovery["activeAgent"] == 3


def test_previous_videos_are_preserved_byte_for_byte():
    evidence = load("evidence")
    assert evidence["existingVideosUnchanged"]
    assert {"solution.mp4", "current-scenario.mp4"} <= evidence["preservedVideos"].keys()
    assert f"{EDITION}.mp4" not in evidence["preservedVideos"]
    assert_preserved(evidence["preservedVideos"])


def test_separate_player_links_earlier_editions_and_has_aligned_captions():
    html = (STATIC / f"{EDITION}.html").read_text(encoding="utf-8")
    assert f'src="/static/{EDITION}.mp4"' in html
    assert f'data-story="/static/{EDITION}-data.json"' in html
    assert "orchestration-theme.js" in html and "solution.js" in html
    assert 'width="1920" height="1080"' in html
    assert 'href="/static/solution.html"' in html
    assert "not an unrestricted" in html
    captions = (STATIC / f"{EDITION}.vtt").read_text(encoding="utf-8")
    assert captions.startswith("WEBVTT\n") and captions.count(" --> ") == 28


def test_encoded_tower_film_has_full_hd_video_voice_captions_and_chapters():
    if not shutil.which("ffprobe"):
        pytest.skip("Optional film inspection requires FFmpeg / ffprobe")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters",
        "-of", "json", str(STATIC / f"{EDITION}.mp4"),
    ], check=True, capture_output=True, text=True)
    media = json.loads(result.stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert {s["codec_type"] for s in media["streams"]} >= {"video", "audio", "subtitle"}
    assert any(s.get("width") == 1920 and s.get("height") == 1080 for s in media["streams"])
    assert len(media["chapters"]) == 14
