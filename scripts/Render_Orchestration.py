"""Render a new, non-overwriting control-tower edition with energetic author voice.

Reuses the existing solution film's locally synthesized voice, original diagrams and
original quiet soundscape helper. No reference voice or repository content is uploaded.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from Render_Story import render, soundscape, vtt_timestamp

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPO = 1.10
AVATARS = [
    {"role": "Validation", "character": "Inspector", "signature": "owl ears, checklist and magnifying glass"},
    {"role": "Decomposition", "character": "Architect", "signature": "hard hat, blueprint and orbiting blocks"},
    {"role": "Provisioning", "character": "Cable crew", "signature": "tall cyclops, wrench and connector arms"},
    {"role": "Fallout recovery", "character": "Rescue mechanic", "signature": "round rescue ring and retry toolbox"},
    {"role": "Customer updates", "character": "Messenger", "signature": "headset, paper plane and bilingual bubbles"},
]


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def snapshot_videos():
    return {path.name: digest(path) for path in STATIC.glob("*.mp4")}


def ensure_new_edition(edition):
    if not re.fullmatch(r"solution-tower-v[1-9][0-9]*", edition):
        raise ValueError("Choose a versioned edition such as solution-tower-v1")
    if (STATIC / f"{edition}.mp4").exists():
        raise FileExistsError(f"Existing video is protected: {edition}.mp4. Choose a new edition.")


def prepare_assets(edition):
    ensure_new_edition(edition)
    story = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
    if story.get("narration", {}).get("kind") != "author-voice-synthetic":
        raise ValueError("The source film must contain the existing synthetic author-voice narration")
    story.update(edition=edition, visualTheme="orchestration-control-tower", avatars=AVATARS,
                 sourceVideo={"file": "solution.mp4", "sha256": digest(STATIC / "solution.mp4")},
                 voiceTreatment={"tempo": TEMPO, "pitchPreserved": True,
                                 "presenceEQ": "3000 Hz / +2 dB", "compressionRatio": 2,
                                 "soundscape": "Original quiet pulse and transition accents"})
    story["narration"]["disclosure"] += (
        " This edition uses pitch-preserving 10% brisker pacing, presence EQ, gentle compression "
        "and an original quiet soundscape."
    )
    captions = ["WEBVTT", ""]
    position = 0
    for scene in story["scenes"]:
        scene["duration"] = round(scene["duration"] / TEMPO, 5)
        for beat in scene["beats"]:
            beat["start"] = round(beat["start"] / TEMPO, 5)
            beat["duration"] = round(beat["duration"] / TEMPO, 5)
            start = position + beat["start"]
            captions.extend([f"{vtt_timestamp(start)} --> {vtt_timestamp(start + beat['duration'])}",
                             beat["text"], ""])
        position += scene["duration"]
    (STATIC / f"{edition}-data.json").write_text(json.dumps(story, indent=2) + "\n", encoding="utf-8")
    (STATIC / f"{edition}.vtt").write_text("\n".join(captions), encoding="utf-8")

    html = (STATIC / "solution.html").read_text(encoding="utf-8")
    for source, target in [
        ("solution.mp4", f"{edition}.mp4"), ("solution.vtt", f"{edition}.vtt"),
        ("solution-poster.jpg", f"{edition}-poster.jpg"),
        ("<title>TürkBizTel | End-to-end solution film</title>",
         "<title>TürkBizTel | Mission: Activate - Control tower edition</title>"),
        ('<script src="/static/solution.js" defer></script>',
         '<script src="/static/orchestration-theme.js" defer></script>\n'
         '  <script src="/static/solution.js" defer></script>'),
        ('width="1280" height="720"', f'width="1920" height="1080" data-story="/static/{edition}-data.json"'),
        ("<h1>From order to activation.</h1>", "<h1>Mission: Activate.</h1>"),
        ("THE NEW SOLUTION / END TO END", "NEW EDITION / THE ORCHESTRATION CONTROL TOWER"),
    ]:
        html = html.replace(source, target)
    html = html.replace(
        '<p class="intro">',
        '<p class="intro">A giant orchestration tower overlooks five distinct specialist characters: '
        'the inspector, architect, cable crew, rescue mechanic and messenger. Brisker author-voice narration, '
        'animated event handoffs and a quiet original soundscape carry the journey.</p><p class="intro">',
        1,
    )
    html = html.replace(
        '<p id="load-status"',
        '<p><a href="/static/solution.html">Previous solution edition</a> / '
        '<a href="/static/journey.html">Current-state film</a>. Earlier video files remain unchanged.</p>'
        '<p>The tower personifies the deterministic, policy-guarded supervisor; it is not an '
        'unrestricted autonomous AI agent. Dispatch animation is illustrative, not a live runtime trace.</p>'
        '<p id="load-status"', 1,
    )
    (STATIC / f"{edition}.html").write_text(html, encoding="utf-8")
    return story, position


def verify_visuals(url, story, output):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_function("() => Boolean(window.amaStory)")
            evidence = page.evaluate("""() => {
                const story=window.amaStory,context=document.getElementById('story').getContext('2d');
                return story.data.scenes.map(scene => {
                    const beat=scene.beats[0];
                    const state=story.render(scene.start+beat.start+beat.duration*.2);
                    const a=context.getImageData(35,225,1345,390).data;
                    story.render(scene.start+beat.start+beat.duration*.75);
                    const b=context.getImageData(35,225,1345,390).data;
                    let changed=0;
                    for(let i=0;i<a.length;i+=4)
                        if(Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2])>30)changed++;
                    return {scene:scene.id,changedPixels:changed,activeAgent:state.activeAgent,
                            tower:state.tower,avatarSignatures:state.avatars.map(a=>a.signature)};
                });
            }""")
            for scene in evidence:
                if scene["changedPixels"] < 1000 or len(set(scene["avatarSignatures"])) != 5:
                    raise ValueError(f"Insufficient scene motion or character distinction: {scene['scene']}")
                if scene["tower"]["height"] < 500:
                    raise ValueError("The orchestration tower is not prominent")
            output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return evidence
        finally:
            browser.close()


def verify_media(path, story, motion):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    media = json.loads(result.stdout)
    duration = float(media["format"]["duration"])
    if not 0 < duration <= 180:
        raise ValueError(f"Video must be under three minutes, got {duration}")
    if not any(s.get("width") == 1920 and s.get("height") == 1080 for s in media["streams"]):
        raise ValueError("The tower edition must be full HD")
    if not {"audio", "video", "subtitle"} <= {s["codec_type"] for s in media["streams"]}:
        raise ValueError("Audio, video or captions are missing")
    if len(media["chapters"]) != len(story["scenes"]):
        raise ValueError("Chapter count does not match the story")
    position = 0
    for scene, row in zip(story["scenes"], motion, strict=True):
        beat = scene["beats"][0]
        frames = []
        for progress in (.2, .75):
            sample = position + beat["start"] + beat["duration"] * progress
            frames.append(subprocess.run([
                "ffmpeg", "-loglevel", "error", "-ss", str(sample), "-i", str(path),
                "-frames:v", "1", "-vf", "format=rgb24,crop=1345:390:35:225", "-pix_fmt", "rgb24",
                "-f", "rawvideo", "pipe:1",
            ], check=True, capture_output=True).stdout)
        if any(len(frame) != 1345 * 390 * 3 for frame in frames):
            raise ValueError("Unable to decode the scene motion samples")
        changed = sum(sum(abs(frames[0][i+c]-frames[1][i+c]) for c in range(3)) > 30
                      for i in range(0, len(frames[0]), 3))
        if changed < 1000:
            raise ValueError(f"Insufficient encoded animation in {scene['id']}")
        row["encodedChangedPixels"] = changed
        position += scene["duration"]
    return duration


def build(edition, scratch):
    ensure_new_edition(edition)
    existing = snapshot_videos()
    story, duration = prepare_assets(edition)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(STATIC / "solution.mp4"), "-vn",
        "-af", f"atempo={TEMPO},highpass=f=75,equalizer=f=3000:t=q:w=1:g=2,"
        f"acompressor=threshold=0.1:ratio=2:attack=8:release=90:makeup=1.3,"
        f"apad=whole_dur={duration},atrim=duration={duration}",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(scratch / "narration.wav"),
    ], check=True)
    audio = soundscape(story, scratch, duration)
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "app"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/static/{edition}.html"
    try:
        with httpx.Client(trust_env=False) as client:
            client.get(url).raise_for_status()
        motion = verify_visuals(url, story, STATIC / f"{edition}-motion.json")
        print(f"Rendering new tower edition: {duration:.2f}s / 1920x1080 / 24 fps.", flush=True)
        render(url, scratch / "rendered.mp4", scratch, duration, 24,
               STATIC / f"{edition}-poster.jpg", audio_path=audio)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=10)
        if worker.is_alive():
            raise RuntimeError("The read-only rendering server did not shut down")
    metadata = [";FFMETADATA1", "title=AMA - Mission Activate - Orchestration tower",
                "comment=Synthetic author-voice with energetic pitch-preserving pacing; "
                "illustrative guarded supervisor; Azure Local proposed, not deployed"]
    start = 0
    for scene in story["scenes"]:
        end = start + round(scene["duration"] * 1000)
        metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                         f"title={scene['chapter']}"])
        start = end
    (scratch / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")
    final = scratch / "final.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(scratch / "rendered.mp4"),
        "-i", str(STATIC / f"{edition}.vtt"), "-i", str(scratch / "chapters.txt"),
        "-map", "0:v", "-map", "0:a", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
        "-movflags", "+faststart", str(final),
    ], check=True)
    actual_duration = verify_media(final, story, motion)
    for filename, sha in existing.items():
        if digest(STATIC / filename) != sha:
            raise RuntimeError(f"An earlier video changed during rendering: {filename}")
    with (STATIC / f"{edition}.mp4").open("xb") as target, final.open("rb") as source:
        shutil.copyfileobj(source, target)
    (STATIC / f"{edition}-motion.json").write_text(json.dumps(motion, indent=2) + "\n", encoding="utf-8")
    proof = {"edition": edition, "duration": actual_duration, "existingVideosUnchanged": True,
             "preservedVideos": existing, "sourceVoiceVideo": story["sourceVideo"],
             "voiceTreatment": story["voiceTreatment"]}
    (STATIC / f"{edition}-evidence.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(f"Created {edition}.mp4 ({actual_duration:.3f}s). Earlier videos unchanged.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", default="solution-tower-v1")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the new player for visual review")
    args = parser.parse_args()
    if args.prepare_only:
        prepare_assets(args.edition)
        return
    with tempfile.TemporaryDirectory(prefix="ama-tower-") as directory:
        build(args.edition, Path(directory))


if __name__ == "__main__":
    main()
