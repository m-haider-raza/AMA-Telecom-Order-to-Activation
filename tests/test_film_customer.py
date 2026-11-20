import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
EDITIONS = ("solution-tower-v3", "solution-tower-v4", "solution-tower-v5", "solution-tower-v6")


def load(edition, kind):
    return json.loads((STATIC / f"{edition}-{kind}.json").read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@pytest.mark.parametrize("edition", EDITIONS)
def test_customer_copy_is_present_in_every_drawn_beat_and_matching_caption(edition):
    data, rows = load(edition, "data"), load(edition, "motion")
    assert len(rows) == len(data["scenes"]) == 12
    banned = ["proposed", "not deployed", "not measured", "targets, not results", "35 days", "30%"]
    captions = (STATIC / f"{edition}.vtt").read_text(encoding="utf-8")
    assert captions.count(" --> ") == 24
    for row in rows:
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        assert len(row["beatChecks"]) == 2
        for beat in row["beatChecks"]:
            text = " ".join(beat["visibleText"]).lower()
            assert "customer solution walkthrough" in text
            assert not any(word in text for word in banned)
            assert 0 < beat["captionLines"] <= 2
    for scene in data["scenes"]:
        for beat in scene["beats"]:
            assert beat["text"] in captions
            assert not any(word in beat["text"].lower() for word in banned)


@pytest.mark.parametrize("edition", EDITIONS)
def test_customer_story_preserves_human_authority_and_architecture_requirements(edition):
    scenes = {scene["id"]: scene for scene in load(edition, "data")["scenes"]}
    assert scenes["validation"]["agentStates"][1][:2] == ["held", "waiting"]
    assert scenes["recovery"]["agentStates"][1][2:4] == ["held", "held"]
    assert "service-desk integration" in scenes["recovery"]["beats"][1]["text"]
    assert "Partners cannot approve" in scenes["human"]["beats"][1]["text"]
    assert "Delivery requires supported local infrastructure" in scenes["residency"]["beats"][1]["text"]
    assert "eligible disconnected operations" in scenes["hybrid"]["beats"][1]["text"]
    assert "objective" in scenes["impact"]["beats"][1]["text"]
    assert all("%" not in card["body"] for card in scenes["impact"]["cards"])


@pytest.mark.parametrize("edition", EDITIONS)
def test_new_customer_film_preserves_every_previous_video_and_official_icon(edition):
    data, proof = load(edition, "data"), load(edition, "evidence")
    assert proof["fullDecodeVerified"] and proof["existingVideosUnchanged"]
    assert "solution-tower-v2.mp4" in proof["preservedVideos"]
    for name, sha in proof["preservedVideos"].items():
        assert digest(STATIC / name) == sha
    assert digest(STATIC / f"{edition}.mp4") != digest(STATIC / "solution-tower-v2.mp4")
    script = {"solution-tower-v5": "tower-ai-agent-story.json",
              "solution-tower-v6": "tower-first-mention-story.json"}.get(edition, "tower-customer-story.json")
    assert proof["scriptSHA256"] == digest(ROOT / "scripts" / "film3d" / script)
    for name, sha in data["microsoftIcons"]["unchangedSvgSHA256"].items():
        assert digest(STATIC / "film3d-assets" / name) == sha
    assert proof["referenceAudioUsed"] is False


@pytest.mark.parametrize("edition", EDITIONS)
def test_customer_mp4_and_overwrite_guard(edition):
    before = {suffix: digest(STATIC / f"{edition}{suffix}") for suffix in [".mp4", "-data.json", ".html"]}
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "Render_Crisp.py"), "--edition", edition,
        "--story", str(ROOT / "scripts" / "film3d" / "tower-customer-story.json"),
        "--narration-dir", str(ROOT / "missing-narration"),
    ], capture_output=True, text=True)
    assert result.returncode != 0 and "Existing video is protected" in result.stderr
    assert before == {suffix: digest(STATIC / f"{edition}{suffix}") for suffix in before}
    if not shutil.which("ffprobe"):
        pytest.skip("Encoded metadata check requires optional FFmpeg")
    media = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json",
        str(STATIC / f"{edition}.mp4"),
    ], check=True, capture_output=True, text=True).stdout)
    assert 0 < float(media["format"]["duration"]) < 180
    assert any(stream.get("width") == 1920 and stream.get("height") == 1080 for stream in media["streams"])
    assert {"video", "audio", "subtitle"} <= {stream["codec_type"] for stream in media["streams"]}
    assert len(media["chapters"]) == 12


def test_latest_customer_film_matches_current_state_voice_without_changing_the_story():
    current = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    previous, latest = (load(edition, "data") for edition in EDITIONS[:2])
    for key in ["kind", "model", "voice", "voiceDescription", "language", "speed", "modelSHA256"]:
        assert latest["narration"][key] == current["narration"][key]
    assert current["narration"]["voice"] in latest["narration"]["disclosure"]
    assert current["narration"]["voiceDescription"] in latest["narration"]["disclosure"]
    assert latest["narration"]["referenceAudioUsed"] is False
    assert latest["narration"]["voice"] != previous["narration"]["voice"]
    assert latest["avatars"] == previous["avatars"]
    assert [
        beat["text"] for scene in latest["scenes"] for beat in scene["beats"]
    ] == [
        beat["text"] for scene in previous["scenes"] for beat in scene["beats"]
    ]


def test_full_ai_agent_names_are_narrated_without_renaming_visual_labels():
    previous, latest = (load(edition, "data") for edition in EDITIONS[1:3])
    for key in ("voice", "language", "speed", "modelSHA256"):
        assert latest["narration"][key] == previous["narration"][key]
    assert latest["avatars"] == previous["avatars"] and latest["agents"] == previous["agents"]
    for before, after in zip(previous["scenes"], latest["scenes"], strict=True):
        assert {k: v for k, v in before.items() if k not in ("beats", "duration")} == {
            k: v for k, v in after.items() if k not in ("beats", "duration")
        }
    scenes = {scene["id"]: scene for scene in latest["scenes"]}
    names = {
        "opening": ("Orchestration AI Agent",),
        "validation": ("Validation AI Agent",),
        "decomposition": ("Decomposition AI Agent",),
        "provisioning": ("Provisioning AI Agent", "Fallout Recovery AI Agent"),
        "recovery": ("Fallout Recovery AI Agent", "Provisioning AI Agent"),
        "communication": ("Customer Updates AI Agent",),
    }
    for scene, expected in names.items():
        spoken = " ".join(beat["text"] for beat in scenes[scene]["beats"])
        assert all(name in spoken for name in expected), scene
    if shutil.which("ffmpeg"):
        captions = subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(STATIC / "solution-tower-v5.mp4"),
            "-map", "0:s:0", "-f", "webvtt", "pipe:1",
        ], check=True, capture_output=True, text=True, encoding="utf-8").stdout
        assert all(name in captions for expected in names.values() for name in expected)


def test_agents_are_introduced_once_then_referenced_by_shorter_names():
    previous, latest = (load(edition, "data") for edition in EDITIONS[2:])
    assert latest["narration"] == previous["narration"]
    assert latest["voiceTreatment"] == previous["voiceTreatment"]
    assert latest["avatars"] == previous["avatars"] and latest["agents"] == previous["agents"]
    changes = []
    for before, after in zip(previous["scenes"], latest["scenes"], strict=True):
        assert {k: v for k, v in before.items() if k not in ("beats", "duration")} == {
            k: v for k, v in after.items() if k not in ("beats", "duration")
        }
        for index, (old, new) in enumerate(zip(before["beats"], after["beats"], strict=True)):
            if old["text"] != new["text"]:
                changes.append((after["id"], index))
    assert changes == [("recovery", 0)]
    transcript = " ".join(beat["text"] for scene in latest["scenes"] for beat in scene["beats"])
    pattern = r"\b(Orchestration|Validation|Decomposition|Provisioning|Fallout Recovery|Customer Updates) (AI )?Agent\b"
    captions = (STATIC / "solution-tower-v6.vtt").read_text(encoding="utf-8")
    transcripts = [transcript, captions]
    if shutil.which("ffmpeg"):
        transcripts.append(subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(STATIC / "solution-tower-v6.mp4"),
            "-map", "0:s:0", "-f", "webvtt", "pipe:1",
        ], check=True, capture_output=True, text=True, encoding="utf-8").stdout)
    for text in transcripts:
        seen, full, shortened = set(), 0, 0
        for match in re.finditer(pattern, text, re.I):
            name = match[1].casefold()
            assert bool(match[2]) == (name not in seen), match[0]
            full += bool(match[2])
            shortened += not bool(match[2])
            seen.add(name)
        assert len(seen) == full == 6 and shortened == 2
