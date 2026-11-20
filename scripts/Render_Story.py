"""Build the current-state film locally using Windows speech, Edge and FFmpeg.

Requires optional presentation tooling: playwright, pywin32, Microsoft Edge and
ffmpeg on PATH. The application must already be serving the storyboard URL.
No PDF content or narration is sent to a remote service.
"""

import argparse
import array
import base64
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def vtt_timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02}.{milliseconds:03}"


def narrate(story, scratch, voice, data_path=None, captions_path=None, pauses=(.65, .35, .65),
            max_duration=None):
    from win32com.client import Dispatch

    speaker = Dispatch("SAPI.SpVoice")
    voices = speaker.GetVoices()
    matching = [item for item in voices if item.GetDescription().startswith(voice)]
    if not matching:
        raise ValueError(f"Local speech voice is not installed: {voice}")
    speaker.Voice = matching[0]
    speaker.Rate = 1
    for i, scene in enumerate(story["scenes"]):
        for j, beat in enumerate(scene["beats"]):
            stream = Dispatch("SAPI.SpFileStream")
            stream.Format.Type = 26  # SAPI: 24 kHz, 16-bit mono PCM.
            stream.Open(str(scratch / f"voice-{i}-{j}.wav"), 3, False)
            try:
                speaker.AudioOutputStream = stream
                speaker.Speak(beat["text"], 0)
            finally:
                stream.Close()
    return assemble_narration(story, scratch, scratch, data_path, captions_path, pauses, max_duration)


def assemble_narration(story, segments, scratch, data_path=None, captions_path=None,
                       pauses=(.65, .35, .65), max_duration=None):
    captions = ["WEBVTT", ""]
    samples = 0
    sample_rate = 24000
    with wave.open(str(scratch / "narration.wav"), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))

        def silence(seconds):
            nonlocal samples
            length = round(seconds * sample_rate)
            output.writeframes(b"\0\0" * length)
            samples += length

        for i, scene in enumerate(story["scenes"]):
            scene_start = samples / sample_rate
            silence(pauses[0])
            for j, beat in enumerate(scene["beats"]):
                with wave.open(str(segments / f"voice-{i}-{j}.wav"), "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, sample_rate):
                        raise ValueError("Offline narration returned an unexpected audio format")
                    start = samples / sample_rate
                    beat["start"] = round(start - scene_start, 5)
                    beat["duration"] = round(source.getnframes() / sample_rate, 5)
                    output.writeframes(source.readframes(source.getnframes()))
                    samples += source.getnframes()
                    captions.extend([
                        f"{vtt_timestamp(start)} --> {vtt_timestamp(samples / sample_rate)}",
                        beat["text"], "",
                    ])
                silence(pauses[1])
            elapsed = samples / sample_rate - scene_start
            silence(max(pauses[2], scene.get("minimumDuration", 0) - elapsed))
            scene["duration"] = round(samples / sample_rate - scene_start, 5)
    if max_duration is not None and samples / sample_rate > max_duration:
        raise ValueError(f"Narration is {samples / sample_rate:.2f}s; limit is {max_duration}s. "
                         "Shorten the script or regenerate speech, rather than truncating it.")
    (data_path or STATIC / "journey-data.json").write_text(
        json.dumps(story, indent=2) + "\n", encoding="utf-8")
    (captions_path or STATIC / "current-scenario.vtt").write_text("\n".join(captions), encoding="utf-8")
    return samples / sample_rate


def render(url, output_path, scratch, duration, fps, poster_path=None, audio_path=None):
    from playwright.sync_api import sync_playwright

    frames = math.ceil(duration * fps)
    intermediate = scratch / "current-scenario.mp4"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
            page.goto(url)
            page.wait_for_function("() => Boolean(window.amaStory)")
            page.evaluate("document.fonts.ready")
            browser_duration = page.evaluate("window.amaStory.duration")
            if abs(browser_duration - duration) > .01:
                raise ValueError("Browser storyboard timing differs from narration timing")
            # Capture a readable first frame, rather than a black transition frame.
            poster = page.evaluate("""() => {
                window.amaStory.render(1.2);
                return document.getElementById('story').toDataURL('image/jpeg', .94).split(',')[1];
            }""")
            (poster_path or STATIC / "current-scenario-poster.jpg").write_bytes(base64.b64decode(poster))
            command = [
                "ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "mjpeg",
                "-framerate", str(fps), "-i", "pipe:0", "-i", str(audio_path or scratch / "narration.wav"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(intermediate),
            ]
            with (scratch / "ffmpeg.log").open("w+b") as log:
                encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
                try:
                    for frame in range(frames):
                        image = page.evaluate("""time => {
                            window.amaStory.render(time);
                            return document.getElementById('story').toDataURL('image/jpeg', .9).split(',')[1];
                        }""", frame / fps)
                        encoder.stdin.write(base64.b64decode(image))
                        if frame % (fps * 10) == 0:
                            print(f"Rendered {frame / fps:.0f} / {duration:.1f} seconds", flush=True)
                finally:
                    encoder.stdin.close()
                    code = encoder.wait()
                if code:
                    log.seek(0)
                    raise RuntimeError(f"FFmpeg failed: {log.read().decode('utf-8', errors='replace')}")
        finally:
            browser.close()
    output_path.write_bytes(intermediate.read_bytes())


def load_private_narration(story, directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    expected = [beat["text"] for scene in story["scenes"] for beat in scene["beats"]]
    if manifest.get("texts") != expected:
        raise ValueError("Private narration does not match the current script; regenerate it.")
    kind = manifest.get("kind")
    if kind not in {"author-voice-synthetic", "local-preset-neural"}:
        raise ValueError("Narration must identify its synthetic author-voice or local preset provenance.")
    if kind == "local-preset-neural" and manifest.get("referenceAudioUsed") is not False:
        raise ValueError("A local preset narrator must not use reference audio.")
    for i, scene in enumerate(story["scenes"]):
        for j, _ in enumerate(scene["beats"]):
            if not (directory / f"voice-{i}-{j}.wav").is_file():
                raise ValueError(f"Missing private narration segment {i}-{j}")
    narration = {
        "kind": kind,
        "model": manifest.get("model"),
        "disclosure": "Synthetic narration modeled locally on the author-provided voice sample. "
                      "Not an original recording of this script. Reference audio was not uploaded.",
    }
    if kind == "local-preset-neural":
        narration.update({key: manifest[key] for key in (
            "voice", "voiceDescription", "language", "speed", "referenceAudioUsed",
            "modelSHA256", "source", "modelLicense", "runtimeLicense",
        ) if key in manifest})
        narration["disclosure"] = (
            f"Locally generated neural narration: {manifest['voiceDescription']} "
            f"({manifest['model']} / {manifest['voice']}). "
            "Brisk delivery with presence EQ and compression. No voice sample or script was uploaded."
        )
    return narration


def soundscape(story, scratch, duration):
    """Original, quiet pulse and transition accents, synthesized without licensed music."""
    rate = 24000
    transitions = []
    position = 0
    for scene in story["scenes"]:
        transitions.append(position)
        position += scene["duration"]
    samples = array.array("h")
    scene_index = 0
    for i in range(math.ceil(duration * rate)):
        t = i / rate
        if scene_index + 1 < len(transitions) and t >= transitions[scene_index + 1]:
            scene_index += 1
        beat = t % (60 / 84)
        pulse = math.sin(2 * math.pi * 66 * beat) * math.exp(-beat * 19)
        pad = (math.sin(2 * math.pi * 130.81 * t) + .4 * math.sin(2 * math.pi * 196 * t)
               + .25 * math.sin(2 * math.pi * 261.63 * t)) * .2
        since_cut = t - transitions[scene_index]
        accent = math.sin(2 * math.pi * (620 * since_cut - 170 * since_cut ** 2))
        accent *= math.exp(-since_cut * 10)
        fade = min(1, t / 1.5, max(0, duration - t) / 2)
        samples.append(round((pulse * .45 + pad + accent * .13) * fade * 1800))
    bed = scratch / "soundscape.wav"
    with wave.open(str(bed), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    mixed = scratch / "film-audio.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(scratch / "narration.wav"),
        "-i", str(bed), "-filter_complex",
        "[0:a]loudnorm=I=-16:TP=-2:LRA=7[voice];"
        "[voice][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
        "-map", "[a]", "-ar", "24000", "-ac", "1", str(mixed),
    ], check=True)
    return mixed


def verify_video(path, maximum=120):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_name,codec_type,width,height", "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    duration = float(info["format"]["duration"])
    if not 0 < duration < maximum:
        raise ValueError(f"Encoded film is {duration:.3f}s; it must be under {maximum}s.")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        raise ValueError("Encoded film has no audio stream.")
    if not any(s.get("width") == 1280 and s.get("height") == 720 for s in info["streams"]):
        raise ValueError("Encoded film is not 1280x720.")
    return duration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/static/journey.html")
    parser.add_argument("--output", type=Path, default=STATIC / "current-scenario.mp4")
    parser.add_argument("--voice", default="Microsoft Zira Desktop")
    parser.add_argument("--fps", type=int, default=24, choices=(12, 24, 30))
    parser.add_argument("--narration-dir", type=Path, help="Private author-voice or local preset speech segments")
    args = parser.parse_args()
    story = json.loads((STATIC / "journey-data.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="ama-film-") as directory:
        scratch = Path(directory)
        if args.narration_dir:
            story["narration"] = load_private_narration(story, args.narration_dir)
            segments = args.narration_dir
            if story["narration"]["kind"] == "local-preset-neural":
                segments = scratch / "presence"
                segments.mkdir()
                for i, scene in enumerate(story["scenes"]):
                    for j, _ in enumerate(scene["beats"]):
                        subprocess.run([
                            "ffmpeg", "-v", "error", "-i", str(args.narration_dir / f"voice-{i}-{j}.wav"),
                            "-af", "highpass=f=75,equalizer=f=3000:t=q:w=1:g=2,"
                            "acompressor=threshold=0.1:ratio=2:attack=8:release=90:makeup=1.2",
                            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                            str(segments / f"voice-{i}-{j}.wav"),
                        ], check=True)
            duration = assemble_narration(story, segments, scratch,
                                          pauses=(.3, .16, .3), max_duration=119)
        else:
            story["narration"] = {
                "kind": "system-voice",
                "disclosure": f"Locally synthesized Windows narration ({args.voice}), not the author's voice.",
            }
            duration = narrate(story, scratch, args.voice, pauses=(.3, .16, .3), max_duration=119)
        print(f"Narration: {duration:.1f} seconds. Rendering at {args.fps} fps.", flush=True)
        audio = soundscape(story, scratch, duration)
        staged = scratch / "verified-film.mp4"
        render(args.url, staged, scratch, duration, args.fps, audio_path=audio)
        encoded_duration = verify_video(staged)
        args.output.write_bytes(staged.read_bytes())
        print(f"Verified encoded film: {encoded_duration:.3f} seconds.", flush=True)
    print(f"Created {args.output}", flush=True)


if __name__ == "__main__":
    main()
