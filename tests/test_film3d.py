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
EDITION = "solution-3d-v1"


def load(suffix):
    return json.loads((STATIC / f"{EDITION}-{suffix}.json").read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def test_3d_edition_preserves_the_script_workflow_and_energetic_voice_timing():
    story = load("data")
    source = json.loads((STATIC / "solution-tower-v1-data.json").read_text(encoding="utf-8"))
    assert story["visualTheme"] == "webgl-3d"
    assert story["rendering"]["projection"] == "perspective"
    assert story["rendering"]["shadows"]
    assert story["rendering"]["resolution"] == [1920, 1080]
    assert story["scenes"] == source["scenes"]
    assert story["voiceTreatment"] == source["voiceTreatment"]
    assert story["narration"] == source["narration"]
    assert story["sourceVideo"]["sha256"] == digest(STATIC / "solution-tower-v1.mp4")
    assert 0 < sum(scene["duration"] for scene in story["scenes"]) < 180


def test_real_rigged_humans_cameras_tower_and_five_mesh_agents_are_rendered():
    story, motion = load("data"), load("motion")
    assert [h["character"] for h in story["humans"]] == ["Business_Male_01", "Business_Female_02"]
    assert story["humans"][0]["wardrobe"] == "business suit"
    assert story["humanAssetSource"]["license"] == "MIT"
    assert {row["scene"] for row in motion} == {scene["id"] for scene in story["scenes"]}
    for row in motion:
        assert row["perspectiveCamera"] and row["shadows"]
        assert row["geometryTriangles"] > 10000
        assert row["towerHeight"] >= 9 and row["avatarCount"] == 5
        assert row["changedPixels"] >= 1000 and row["encodedChangedPixels"] >= 1000
        assert len(row["humans"]) == 2
        for human in row["humans"]:
            assert human["boneCount"] >= 20 and human["skinnedMeshes"] >= 1
            assert human["texturedMeshes"] >= 1
    assert "customer" in motion[0]["visibleHumans"]
    assert "manager" in next(row for row in motion if row["scene"] == "human")["visibleHumans"]
    for scene, agent in [("validation", 0), ("decomposition", 1), ("provisioning", 2), ("recovery", 3),
                         ("communication", 4)]:
        assert next(row for row in motion if row["scene"] == scene)["activeAgent"] == agent


def test_official_microsoft_icons_are_unmodified_and_have_retained_sources_and_licenses():
    story = load("data")
    for name, sha in story["microsoftIcons"]["unchangedSvgSHA256"].items():
        assert digest(STATIC / "film3d-assets" / name) == sha
    icons = {icon["name"] for row in load("motion") for icon in row["officialIcons"]}
    assert icons == {"Azure Local", "Azure Arc", "Azure Container Registry", "Azure Monitor"}
    license_text = (STATIC / "film3d-assets" / "ROCKETBOX-LICENSE.md").read_text()
    assert "MIT License" in license_text and "Copyright (c) 2020 Microsoft" in license_text
    assert (STATIC / "film3d-assets" / "THREE-LICENSE.txt").is_file()
    assert "Copyright (c) 2023 Arjun Barrett" in (
        STATIC / "film3d-assets" / "FFLATE-LICENSE.txt").read_text()
    textures = json.loads((STATIC / "film3d-assets" / "texture-evidence.json").read_text())
    assert len(textures) == 17 and all(texture["lossless"] for texture in textures)
    for texture in textures:
        assert digest(STATIC / "film3d-assets" / texture["role"] / texture["file"]) == texture["pngSHA256"]
    assert "https://learn.microsoft.com/en-us/azure/architecture/icons/" in (
        STATIC / "film3d-assets" / "ATTRIBUTION.md").read_text()


def test_previous_video_editions_are_unchanged_and_player_remains_separate():
    proof = load("evidence")
    assert proof["existingVideosUnchanged"] and proof["audioCopiedWithoutReencoding"]
    assert {"solution.mp4", "solution-tower-v1.mp4", "current-scenario.mp4"} <= proof["preservedVideos"].keys()
    assert f"{EDITION}.mp4" not in proof["preservedVideos"]
    assert_preserved(proof["preservedVideos"])
    html = (STATIC / f"{EDITION}.html").read_text(encoding="utf-8")
    assert f'src="/static/{EDITION}.mp4"' in html
    assert "film3d.js" in html and "solution.js" in html and "orchestration-theme.js" not in html
    assert "not live-action actors" in html
    assert 'href="/static/solution-tower-v1.html"' in html
    assert 'href="/static/solution.html"' in html
    assert (STATIC / f"{EDITION}.vtt").read_bytes() == (STATIC / "solution-tower-v1.vtt").read_bytes()


def test_3d_renderer_refuses_an_existing_edition_before_changing_files():
    if not importlib.util.find_spec("playwright"):
        pytest.skip("Optional render guard check requires Playwright")
    before = {suffix: digest(STATIC / f"{EDITION}{suffix}") for suffix in [".mp4", "-data.json", ".html"]}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "Render_3D.py"), "--edition", EDITION, "--prepare-only"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and "Existing video is protected" in result.stderr
    assert before == {suffix: digest(STATIC / f"{EDITION}{suffix}") for suffix in before}


def test_encoded_3d_video_is_full_hd_under_three_minutes_and_copies_the_original_audio():
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        pytest.skip("Optional encoded video inspection requires FFmpeg / ffprobe")
    media = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json",
        str(STATIC / f"{EDITION}.mp4"),
    ], check=True, capture_output=True, text=True).stdout)
    assert 0 < float(media["format"]["duration"]) <= 180
    assert any(s.get("width") == 1920 and s.get("height") == 1080 for s in media["streams"])
    assert {"audio", "video", "subtitle"} <= {s["codec_type"] for s in media["streams"]}
    assert len(media["chapters"]) == 14
    hashes = []
    for name in [f"{EDITION}.mp4", "solution-tower-v1.mp4"]:
        hashes.append(subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(STATIC / name), "-map", "0:a", "-c:a", "copy",
            "-f", "hash", "-hash", "sha256", "pipe:1",
        ], check=True, capture_output=True).stdout)
    assert hashes[0] == hashes[1]
