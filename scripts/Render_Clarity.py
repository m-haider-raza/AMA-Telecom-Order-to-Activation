"""Render a separate uncluttered 3D responsibility/exception film with local author voice."""

import argparse
import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from Render_3D import ICONS
from Render_Orchestration import ROOT, STATIC, digest, snapshot_videos, verify_media
from Render_Story import assemble_narration, load_private_narration, render, soundscape

SCRIPT = ROOT / "scripts" / "film3d" / "clarity-story.json"
TEMPO = 1.08
MINIMUMS = {"opening": 12, "intake": 12, "human": 16, "impact": 20, "residency": 20}


def ensure_new(edition):
    if not re.fullmatch(r"solution-clarity-v[1-9][0-9]*", edition):
        raise ValueError("Choose a separate edition such as solution-clarity-v1")
    if (STATIC / f"{edition}.mp4").exists():
        raise FileExistsError(f"Existing video is protected: {edition}.mp4")


def story():
    data = json.loads((STATIC / "solution-3d-v1-data.json").read_text(encoding="utf-8"))
    script = json.loads(SCRIPT.read_text(encoding="utf-8"))
    data.update(script)
    data["sourceVideo"] = {"file": "solution-3d-v1.mp4", "sha256": digest(STATIC / "solution-3d-v1.mp4"),
                           "use": "Visual asset reference only; revised narration is generated locally."}
    data.update(visualTheme="clear-ownership-3d", presentation="single-focal-3d-scene-with-responsibility-panel",
                voiceTreatment={"tempo": TEMPO, "pitchPreserved": True, "presenceEQ": "3000 Hz / +2 dB",
                                "compressionRatio": 2, "soundscape": "Original restrained quiet bed"},
                layoutContract={"oneAgentAtATime": True, "responsibilityFontMinimum": 26,
                                "separateWorldAndTextPanels": True})
    for scene in data["scenes"]:
        scene.setdefault("mode", "implemented")
        scene["minimumDuration"] = MINIMUMS.get(scene["id"], 17)
        scene["duration"] = scene["minimumDuration"]
        states = ["waiting", "waiting", "waiting", "standby", "waiting"]
        if scene["agent"] >= 0:
            states[scene["agent"]] = "active"
        scene["agentStates"] = [states.copy() for _ in scene["beats"]]
        scene["events"] = [scene["event"] for _ in scene["beats"]]
        for i, beat in enumerate(scene["beats"]):
            beat.update(start=.25 + i * scene["duration"] / 2, duration=scene["duration"] / 2 - .4)
    for file, sha in ICONS.items():
        if digest(STATIC / "film3d-assets" / file) != sha:
            raise ValueError(f"Official icon changed: {file}")
    return data


def prepare(data, edition):
    ensure_new(edition)
    data["edition"] = edition
    (STATIC / f"{edition}-data.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    html = (STATIC / "solution-3d-v1.html").read_text(encoding="utf-8").replace("solution-3d-v1", edition)
    html = html.replace("film3d.js", "film-clarity.js").replace("True 3D edition", "Clear ownership edition")
    html = html.replace("NEW EDITION / TRUE 3D", "NEW EDITION / CLEAR OWNERSHIP AND EXCEPTIONS")
    html = html.replace("<h1>Mission: Activate. In 3D.</h1>", "<h1>Clear ownership. Faster, safer delivery.</h1>")
    start, end = html.index('<p class="intro">'), html.index('<video id="film"')
    html = html[:start] + (
        '<p class="intro">One focal 3D scene at a time. Each agent has a separate, readable responsibility, '
        'output, exception path, human owner and benefit panel. Early validation, linked order identifiers, '
        'durable handoffs, allocate-once guards and bounded recovery explain how fallout and waiting can '
        'fall. Business targets remain ambitions requiring pilot evidence.</p>'
        '<p>Amber paths are explicitly proposed production gates, not implemented demo features. '
        'The only simulated recovery playbook is one partner-timeout retry; external notification delivery '
        'and its service-desk incident workflow are not implemented.</p>\n    '
    ) + html[end:]
    html = html.replace(
        '<p id="load-status"',
        '<p><a href="/static/solution-3d-v1.html">Previous 3D edition</a>. '
        'The earlier films are preserved, including their video bytes.</p><p id="load-status"', 1)
    start = html.index("<p>The character and event animations")
    end = html.index("</p>", start) + 4
    html = html[:start] + (
        "<p>The film explains one synthetic order through README.md, app/domain.py, app/main.py, "
        "app/store.py, app/advisor.py and docs/PRODUCTION.md. Each specialist's responsibility, "
        "output, exception and human owner are shown separately. Recovery is a timeout-only "
        "branch that returns to provisioning; proposed catalogue onboarding, general triage and "
        "external delivery incidents are not implemented demo queues. Animation and reading holds "
        "are illustrative, not runtime traces, a performance benchmark or global concurrency.</p>"
    ) + html[end:]
    (STATIC / f"{edition}.html").write_text(html, encoding="utf-8")


def paced_narration(data, directory, scratch, edition):
    if directory.resolve().is_relative_to(ROOT):
        raise ValueError("Private voice segments must remain outside the repository")
    data["narration"] = load_private_narration(data, directory)
    data["narration"]["disclosure"] += (
        " This clarity edition uses revised locally generated narration, pitch-preserving brisk pacing "
        "and explicit reading holds; the original sample remains private."
    )
    paced = scratch / "paced"; paced.mkdir()
    for i, scene in enumerate(data["scenes"]):
        for j, _ in enumerate(scene["beats"]):
            subprocess.run([
                "ffmpeg", "-v", "error", "-i", str(directory / f"voice-{i}-{j}.wav"),
                "-af", f"atempo={TEMPO},highpass=f=75,equalizer=f=3000:t=q:w=1:g=2,"
                "acompressor=threshold=0.1:ratio=2:attack=8:release=90:makeup=1.25",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(paced / f"voice-{i}-{j}.wav"),
            ], check=True)
    duration = assemble_narration(
        data, paced, scratch, STATIC / f"{edition}-data.json", STATIC / f"{edition}.vtt",
        pauses=(.25, .15, .35), max_duration=179,
    )
    return duration, soundscape(data, scratch, duration)


def intersect(a, b):
    return (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and
            a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])


def verify_visuals(url, path, review):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url); page.wait_for_function("() => Boolean(window.amaStory)", timeout=120000)
            page.evaluate("document.fonts.ready")
            rows = page.evaluate("""() => {
                const s=window.amaStory,c=document.getElementById('story').getContext('2d');
                return s.data.scenes.map(scene=>{
                    const beat=scene.beats[0];
                    s.render(scene.start+beat.start+beat.duration*.2);
                    const a=c.getImageData(35,225,1030,560).data;
                    const state=s.render(scene.start+beat.start+beat.duration*.8);
                    const b=c.getImageData(35,225,1030,560).data;
                    let changed=0;
                    for(let i=0;i<a.length;i+=4)
                      if(Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2])>30)changed++;
                    const beatChecks=scene.beats.map(b=>s.render(scene.start+b.start+.7));
                    return {...state,changedPixels:changed,beatChecks};
                });
            }""")
            for row in rows:
                if row["visibleAgents"] > 1 or row["changedPixels"] < 1000:
                    raise ValueError(f"Overcrowded or static scene: {row['scene']}")
                if not row["perspectiveCamera"] or not row["shadows"] or row["geometryTriangles"] < 100:
                    raise ValueError("True 3D geometry/lighting is missing")
                boxes = list(row["layout"].values())
                if any(intersect(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:]):
                    raise ValueError("The focal world, responsibility, handoff or caption areas overlap")
                for beat in row["beatChecks"]:
                    if beat["captionLines"] > 2 or beat["captionFontSize"] < 28:
                        raise ValueError("Narration captions are overcrowded")
                    if any(b["fontSize"] < 26 or b["lines"] > 2 or b["measuredWidth"] > b["width"]
                           for b in beat["panelBounds"]):
                        raise ValueError("Responsibilities are too small or overcrowded")
            if review:
                review.mkdir(parents=True, exist_ok=True)
                for scene in page.evaluate("window.amaStory.data.scenes"):
                    for beat in range(2):
                        time = scene["start"] + scene["beats"][beat]["start"] + .7
                        frame = page.evaluate("""t=>{
                            window.amaStory.render(t);
                            return document.getElementById('story').toDataURL('image/png').split(',')[1];
                        }""", time)
                        (review / f"clarity-{scene['id']}-{beat}.png").write_bytes(base64.b64decode(frame))
            path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
            return rows
        finally:
            browser.close()


def build(edition, directory, review, preview_only):
    ensure_new(edition)
    existing = snapshot_videos()
    data = story(); prepare(data, edition)
    with tempfile.TemporaryDirectory(prefix="ama-clarity-") as folder:
        scratch = Path(folder)
        if preview_only:
            duration = sum(s["duration"] for s in data["scenes"]); audio = None
        else:
            duration, audio = paced_narration(data, directory, scratch, edition)
        handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "app"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/static/{edition}.html"
            rows = verify_visuals(url, STATIC / f"{edition}-motion.json", review)
            if preview_only:
                print("Uncluttered 3D preview and non-overlapping layout verified.", flush=True)
                return
            print(f"Rendering clarity film: {duration:.3f}s; all earlier films protected.", flush=True)
            render(url, scratch / "rendered.mp4", scratch, duration, 24,
                   STATIC / f"{edition}-poster.jpg", audio_path=audio)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Local render server did not shut down")
        metadata = [";FFMETADATA1", "title=AMA - Clear ownership, fewer failures, faster delivery",
                    "comment=Locally generated author voice; simulation; production gates explicitly labelled"]
        start = 0
        for scene in data["scenes"]:
            end = start + round(scene["duration"] * 1000)
            metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                             f"title={scene['chapter']}"])
            start = end
        (scratch / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")
        final = scratch / "final.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(scratch / "rendered.mp4"),
            "-i", str(STATIC / f"{edition}.vtt"), "-i", str(scratch / "chapters.txt"),
            "-map", "0:v", "-map", "0:a", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
            "-movflags", "+faststart", str(final),
        ], check=True)
        actual = verify_media(final, data, rows)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(final), "-f", "null", "-"], check=True)
        if any(digest(STATIC / name) != sha for name, sha in existing.items()):
            raise RuntimeError("An existing film changed during the clarity render")
        with (STATIC / f"{edition}.mp4").open("xb") as destination, final.open("rb") as source:
            shutil.copyfileobj(source, destination)
        (STATIC / f"{edition}-motion.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        proof = {"edition": edition, "duration": actual, "preservedVideos": existing,
                 "existingVideosUnchanged": True, "voiceTreatment": data["voiceTreatment"],
                 "layoutVerified": True, "singleFocalAgentVerified": True,
                 "scriptSHA256": digest(SCRIPT)}
        (STATIC / f"{edition}-evidence.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(f"Created {edition}.mp4: {actual:.3f}s. All earlier films unchanged.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", default="solution-clarity-v1")
    parser.add_argument("--narration-dir", type=Path)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--review-dir", type=Path)
    args = parser.parse_args()
    if not args.preview_only and args.narration_dir is None:
        parser.error("Supply private revised --narration-dir, or use --preview-only")
    build(args.edition, args.narration_dir, args.review_dir, args.preview_only)


if __name__ == "__main__":
    main()
