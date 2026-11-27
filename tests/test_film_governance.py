import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.media_proof import assert_text_preserved

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
EDITION = "solution-tower-v7"


def load(kind, edition=EDITION):
    return json.loads((STATIC / f"{edition}-{kind}.json").read_text(encoding="utf-8"))


def test_governance_claims_are_narrated_captioned_and_readable_without_claiming_target_controls_are_live():
    story, proof, motion = load("data"), load("evidence"), load("motion")
    script = json.loads((ROOT / "scripts" / "film3d" / "tower-governance-story.json").read_text(encoding="utf-8"))
    captions = (STATIC / f"{EDITION}.vtt").read_text(encoding="utf-8")
    transcript = (STATIC / f"{EDITION}-transcript.txt").read_text(encoding="utf-8")
    expected = [beat["text"] for scene in script["scenes"] for beat in scene["beats"]]
    assert expected == [beat["text"] for scene in story["scenes"] for beat in scene["beats"]]
    assert captions.count(" --> ") == len(expected)
    assert all(text in captions and text in transcript for text in expected)
    assert_text_preserved(ROOT / "scripts" / "film3d" / "tower-governance-story.json", proof["scriptSHA256"])
    assert all((ROOT / source).is_file() for source in script["sources"])
    scenes = {scene["id"]: scene for scene in story["scenes"]}
    claims = {
        "provisioning": ["proposes activation", "boundary rechecks"],
        "human": ["own Microsoft Entra account", "Agents and partners cannot approve", "audited"],
        "communication": ["Only the boundary sends email", "sign-in and order ownership"],
        "standards": ["separate Entra identity", "own project and tools", "no direct state access",
                      "verify the token, caller and permitted operation", "never permission to bypass policy"],
        "hybrid": ["For production, assign every agent a named business sponsor and technical owner",
                   "purpose, risk and budget", "releases and incidents", "audit trails", "access reviews",
                   "alerts and identity revocation", "Agent 365 registration remains pending"],
        "residency": ["synthetic pilot runs in Sweden"],
    }
    for name, phrases in claims.items():
        text = " ".join(beat["text"] for beat in scenes[name]["beats"])
        assert all(phrase in text for phrase in phrases), name
    assert scenes["hybrid"]["mode"] == scenes["residency"]["mode"] == "proposed"
    assert len(motion) == len(story["scenes"]) == 12
    for row in motion:
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        for beat in row["beatChecks"]:
            assert 0 < beat["captionLines"] <= 2
            assert "IDENTITY & GOVERNANCE" in beat["visibleText"]
            assert all(card["headingWidth"] <= 280 and card["bodyWidth"] <= 280 for card in beat["cardBounds"])


def test_governance_film_preserves_the_narrator_archived_video_and_near_original_size():
    current, evidence = load("data"), load("evidence")
    previous = json.loads((STATIC / "solution-tower-v6-data.json").read_text(encoding="utf-8"))
    for key in ("kind", "model", "voice", "language", "speed", "modelSHA256"):
        assert current["narration"][key] == previous["narration"][key]
    assert current["voiceTreatment"] == previous["voiceTreatment"]
    assert current["agents"] == previous["agents"] and current["avatars"] == previous["avatars"]
    assert current["narration"]["referenceAudioUsed"] is False
    assert evidence["fullDecodeVerified"] and evidence["existingVideosUnchanged"]
    archive = STATIC / "solution-tower-v6-turkbiztel.mp4"
    with archive.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == evidence["preservedVideos"][archive.name]
    current_video = STATIC / f"{EDITION}-turkbiztel.mp4"
    assert current_video.stat().st_size <= archive.stat().st_size * 1.10
    assert sum(scene["duration"] for scene in current["scenes"]) <= 180
    assert sum(scene["duration"] for scene in current["scenes"]) <= (
        sum(scene["duration"] for scene in previous["scenes"]) + 3
    )


@pytest.mark.parametrize("edition", (EDITION, "solution-tower-v8"))
def test_encoded_governance_film_has_aligned_chapters_complete_captions_and_unchanged_branding_audio(edition):
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        pytest.skip("Encoded film verification requires optional FFmpeg")
    story = load("data", edition)
    video = STATIC / f"{edition}-turkbiztel.mp4"
    media = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(video),
    ], capture_output=True, text=True, check=True).stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert {"video", "audio", "subtitle"} <= {stream["codec_type"] for stream in media["streams"]}
    assert any(stream.get("width") == 1920 and stream.get("height") == 1080 for stream in media["streams"])
    assert len(media["chapters"]) == len(story["scenes"])
    position = 0
    for chapter, scene in zip(media["chapters"], story["scenes"], strict=True):
        assert float(chapter["start_time"]) == pytest.approx(position, abs=.01)
        assert chapter["tags"]["title"] == scene["chapter"]
        position += scene["duration"]
    captions = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(video), "-map", "0:s:0", "-f", "webvtt", "pipe:1",
    ], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert all(beat["text"] in captions for scene in story["scenes"] for beat in scene["beats"])
    pattern = r"((?:\d{2}:)?\d{2}:\d{2}\.\d{3}) --> ((?:\d{2}:)?\d{2}:\d{2}\.\d{3})"

    def milliseconds(value):
        whole, fraction = value.split(".")
        return sum(int(part) * 60**i for i, part in enumerate(reversed(whole.split(":")))) * 1000 + int(fraction)

    external = (STATIC / f"{edition}.vtt").read_text(encoding="utf-8")
    external_times = [tuple(milliseconds(t) for t in pair) for pair in re.findall(pattern, external)]
    embedded_times = [tuple(milliseconds(t) for t in pair) for pair in re.findall(pattern, captions)]
    assert len(external_times) == len(embedded_times) == 24
    assert external_times == embedded_times
    hashes = [subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
        "-f", "hash", "-hash", "sha256", "pipe:1",
    ], capture_output=True, text=True, check=True).stdout for path in (video, STATIC / f"{edition}.mp4")]
    assert hashes[0] == hashes[1]


def test_customer_proposal_introduces_ai_once_and_presents_agent365_governance_without_demo_status_copy():
    edition = "solution-tower-v8"
    story, evidence, motion = (load(kind, edition) for kind in ("data", "evidence", "motion"))
    script = json.loads((ROOT / "scripts" / "film3d" / "tower-proposal-story.json").read_text(encoding="utf-8"))
    expected = [beat["text"] for scene in script["scenes"] for beat in scene["beats"]]
    assert expected == [beat["text"] for scene in story["scenes"] for beat in scene["beats"]]
    assert expected[0].startswith("This proposal uses AI-powered agents.")
    assert re.findall(r"\bAI\b", " ".join(expected)) == ["AI"]
    assert not re.search(r"\bAI\s+Agent\b", " ".join(expected), re.I)
    assert any("Agent 365 is part of the governance solution" in text for text in expected)
    assert_text_preserved(ROOT / "scripts" / "film3d" / "tower-proposal-story.json", evidence["scriptSHA256"])
    excluded = ("synthetic", "simulat", "pilot", "pending", "not deployed", "not registered", "not measured")
    captions = (STATIC / f"{edition}.vtt").read_text(encoding="utf-8")
    transcript = (STATIC / f"{edition}-transcript.txt").read_text(encoding="utf-8")
    player = (STATIC / f"{edition}.html").read_text(encoding="utf-8")
    case_study = (STATIC / "case-study.html").read_text(encoding="utf-8")
    proposal_section = re.search(r'<section[^>]*id="scene-solution".*?</section>', case_study, re.S).group()
    assert captions.count(" --> ") == 24
    assert all(text in captions and text in transcript for text in expected)
    for content in (captions, transcript, player, proposal_section):
        assert not any(phrase in content.lower() for phrase in excluded)
    for content in (captions, transcript, player):
        assert not re.search(r"\bdemo\b", content, re.I)
    assert "Microsoft Agent 365 is part of the proposed agent-governance solution" in player
    assert "Agent 365 is part of the governance solution" in proposal_section
    assert len(motion) == 12
    for row in motion:
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        for beat in row["beatChecks"]:
            text = " ".join(beat["visibleText"])
            assert "CUSTOMER PROPOSAL" in text
            assert not any(phrase in text.lower() for phrase in excluded)
            assert not re.search(r"\bdemo\b", text, re.I)
            assert row["scene"] == "opening" or not re.search(r"\bAI\b", text)
            assert 0 < beat["captionLines"] <= 2
    governance = next(row for row in motion if row["scene"] == "hybrid")
    assert all("AGENT 365 GOVERNANCE" in beat["visibleText"] for beat in governance["beatChecks"])


def test_proposal_preserves_the_current_narrator_and_archives_with_compact_runtime_and_size():
    edition = "solution-tower-v8"
    story, evidence = load("data", edition), load("evidence", edition)
    previous = load("data")
    for key in ("kind", "model", "voice", "language", "speed", "modelSHA256"):
        assert story["narration"][key] == previous["narration"][key]
    assert story["voiceTreatment"] == previous["voiceTreatment"]
    assert story["avatars"] == previous["avatars"] and story["agents"] == previous["agents"]
    assert sum(scene["duration"] for scene in story["scenes"]) <= 180
    assert sum(scene["duration"] for scene in story["scenes"]) <= (
        sum(scene["duration"] for scene in previous["scenes"]) + 3
    )
    assert (STATIC / f"{edition}-turkbiztel.mp4").stat().st_size <= (
        (STATIC / f"{EDITION}-turkbiztel.mp4").stat().st_size * 1.10
    )
    assert evidence["existingVideosUnchanged"] and evidence["fullDecodeVerified"]
    for name, expected in evidence["preservedVideos"].items():
        with (STATIC / name).open("rb") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == expected
