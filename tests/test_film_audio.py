import json
import subprocess
import wave

import pytest

from scripts.Render_Story import assemble_narration, load_private_narration, verify_video


def segment(path, frames=24000, rate=24000):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"\1\0" * frames)


def test_private_segments_drive_timings_and_captions(tmp_path):
    segment(tmp_path / "voice-0-0.wav")
    segment(tmp_path / "voice-0-1.wav", 12000)
    story = {"scenes": [{"beats": [{"text": "First beat."}, {"text": "Second beat."}]}]}
    data, captions = tmp_path / "data.json", tmp_path / "captions.vtt"
    duration = assemble_narration(story, tmp_path, tmp_path, data, captions,
                                  pauses=(.18, .12, .18), max_duration=179)
    assert duration == pytest.approx(2.1)
    assert story["scenes"][0]["beats"][1]["start"] == pytest.approx(1.3)
    assert story["scenes"][0]["beats"][1]["duration"] == .5
    assert json.loads(data.read_text()) == story
    assert "00:00:01.300 --> 00:00:01.800" in captions.read_text()


def test_duration_limit_rejects_audio_without_publishing_metadata(tmp_path):
    segment(tmp_path / "voice-0-0.wav")
    data, captions = tmp_path / "data.json", tmp_path / "captions.vtt"
    with pytest.raises(ValueError, match="Shorten the script"):
        assemble_narration({"scenes": [{"beats": [{"text": "Too long."}]}]},
                           tmp_path, tmp_path, data, captions, max_duration=.5)
    assert not data.exists() and not captions.exists()


def test_reading_hold_extends_scene_without_truncating_voice_or_caption(tmp_path):
    segment(tmp_path / "voice-0-0.wav")
    story = {"scenes": [{"minimumDuration": 4, "beats": [{"text": "Read the responsibility."}]}]}
    duration = assemble_narration(story, tmp_path, tmp_path, tmp_path / "data.json",
                                  tmp_path / "captions.vtt", pauses=(.25, .15, .35), max_duration=179)
    assert duration == 4
    assert story["scenes"][0]["duration"] == 4
    assert story["scenes"][0]["beats"][0] == {
        "text": "Read the responsibility.", "start": .25, "duration": 1,
    }
    assert "00:00:00.250 --> 00:00:01.250" in (tmp_path / "captions.vtt").read_text()
    with wave.open(str(tmp_path / "narration.wav")) as audio:
        assert audio.getnframes() == 4 * 24000
        audio.setpos(round(1.25 * 24000))
        assert audio.readframes(24000) == b"\0\0" * 24000


def test_unexpected_private_audio_format_is_rejected(tmp_path):
    segment(tmp_path / "voice-0-0.wav", rate=48000)
    with pytest.raises(ValueError, match="unexpected audio format"):
        assemble_narration({"scenes": [{"beats": [{"text": "Wrong format."}]}]},
                           tmp_path, tmp_path, tmp_path / "data.json", tmp_path / "captions.vtt")


def test_current_film_requires_matching_private_voice_manifest(tmp_path):
    story = {"scenes": [{"beats": [{"text": "Current state only."}]}]}
    manifest = {"kind": "author-voice-synthetic", "texts": ["Current state only."]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    segment(tmp_path / "voice-0-0.wav")
    assert load_private_narration(story, tmp_path)["kind"] == "author-voice-synthetic"
    manifest["texts"] = ["A different film."]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_private_narration(story, tmp_path)


def test_current_film_rejects_missing_voice_beat(tmp_path):
    story = {"scenes": [{"beats": [{"text": "Current state only."}]}]}
    (tmp_path / "manifest.json").write_text(json.dumps({
        "kind": "author-voice-synthetic", "texts": ["Current state only."],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing private narration"):
        load_private_narration(story, tmp_path)


def test_current_film_accepts_british_male_preset_without_claiming_author_voice(tmp_path):
    story = {"scenes": [{"beats": [{"text": "Current state only."}]}]}
    manifest = {
        "kind": "local-preset-neural", "model": "Kokoro-82M v1.0", "voice": "bm_george",
        "voiceDescription": "Distinct British-English male preset; not the author's voice",
        "language": "en-gb", "referenceAudioUsed": False, "texts": ["Current state only."],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    segment(tmp_path / "voice-0-0.wav")
    narration = load_private_narration(story, tmp_path)
    assert narration["voice"] == "bm_george"
    assert narration["language"] == "en-gb"
    assert "British-English male" in narration["disclosure"]
    assert "not the author's voice" in narration["disclosure"]
    manifest["referenceAudioUsed"] = True
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="must not use reference audio"):
        load_private_narration(story, tmp_path)


@pytest.mark.parametrize("duration,valid", [(119.99, True), (120, False), (148.176, False)])
def test_encoded_duration_limit_is_strict(monkeypatch, tmp_path, duration, valid):
    info = {"format": {"duration": str(duration)}, "streams": [
        {"codec_type": "audio"}, {"codec_type": "video", "width": 1280, "height": 720},
    ]}
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args, 0, stdout=json.dumps(info)))
    if valid:
        assert verify_video(tmp_path / "film.mp4") == duration
    else:
        with pytest.raises(ValueError, match="must be under 120"):
            verify_video(tmp_path / "film.mp4")
