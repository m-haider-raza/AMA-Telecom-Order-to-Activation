"""Render a preserved true-3D film using local WebGL and the existing energetic voice."""

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

from Render_Orchestration import STATIC, ROOT, digest, snapshot_videos, verify_media
from Render_Story import render

SOURCE = "solution-tower-v1"
ICONS = {
    "azure-local.svg": "dfce1fa962f0dfd242235471214d46ae9af425d249f206537fa08bb1b1cce1cf",
    "azure-arc.svg": "00f4a57d6e0391bec619a9298d114f783dbb7dd4165d44242202ca37d00d9bfa",
    "container-registry.svg": "c134b0a442f203a7eeeb6f99a1f02ed34e51373d7fca59f96c9f125874c9d7d4",
    "azure-monitor.svg": "1396f59a7d8fd7505a423842b987c9b343a092263d1ab5c7b0d94d58d8b17448",
}


def ensure_new(edition):
    if not re.fullmatch(r"solution-3d-v[1-9][0-9]*", edition):
        raise ValueError("Choose a separate versioned edition such as solution-3d-v1")
    if (STATIC / f"{edition}.mp4").exists():
        raise FileExistsError(f"Existing video is protected: {edition}.mp4")


def prepare(edition):
    ensure_new(edition)
    for file, sha in ICONS.items():
        if digest(STATIC / "film3d-assets" / file) != sha:
            raise ValueError(f"Official Microsoft product icon changed: {file}")
    story = json.loads((STATIC / f"{SOURCE}-data.json").read_text(encoding="utf-8"))
    story.update(
        edition=edition, presentation="true-3d-character-and-event-animation", visualTheme="webgl-3d",
        sourceVideo={"file": f"{SOURCE}.mp4", "sha256": digest(STATIC / f"{SOURCE}.mp4")},
        rendering={"engine": "Three.js 0.180.0", "projection": "perspective",
                   "resolution": [1920, 1080], "fps": 24, "shadows": True},
        humans=[
            {"role": "customer", "character": "Business_Male_01", "wardrobe": "business suit",
             "asset": "film3d-assets/customer/Business_Male_01.fbx"},
            {"role": "service manager", "character": "Business_Female_02", "wardrobe": "business jacket",
             "asset": "film3d-assets/manager/Business_Female_02.fbx"},
        ],
        humanAssetSource={
            "repository": "https://github.com/microsoft/Microsoft-Rocketbox",
            "revision": "0943055db6ec570bcef9f2c8b41c9e5467c808f9", "license": "MIT",
            "disclosure": "Textured, rigged rendered humans; not filmed live-action people.",
            "textures": "Original TGA textures re-encoded losslessly as PNG; maps and rig remain local.",
        },
        microsoftIcons={"source": "https://learn.microsoft.com/en-us/azure/architecture/icons/",
                        "archive": "https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip",
                        "unchangedSvgSHA256": ICONS,
                        "use": "Official product icons, upright and labelled with the actual product names."},
    )
    story["references"].extend([
        {"name": "Microsoft official Azure architecture icons",
         "url": "https://learn.microsoft.com/en-us/azure/architecture/icons/"},
        {"name": "Microsoft Rocketbox human avatars / MIT license",
         "url": "https://github.com/microsoft/Microsoft-Rocketbox/blob/master/LICENSE.md"},
    ])
    (STATIC / f"{edition}-data.json").write_text(json.dumps(story, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(STATIC / f"{SOURCE}.vtt", STATIC / f"{edition}.vtt")
    html = (STATIC / f"{SOURCE}.html").read_text(encoding="utf-8").replace(SOURCE, edition)
    html = html.replace("orchestration-theme.js", "film3d.js")
    html = html.replace("Control tower edition", "True 3D edition")
    html = html.replace("NEW EDITION / THE ORCHESTRATION CONTROL TOWER", "NEW EDITION / TRUE 3D")
    html = html.replace("<h1>Mission: Activate.</h1>", "<h1>Mission: Activate. In 3D.</h1>")
    html = html.replace(
        '<p class="intro">',
        '<p class="intro">A textured 3D human customer in a business suit and a human service '
        'manager join the giant orchestration tower and five distinct agents. Perspective cameras, '
        'lit meshes, shadows and moving event packets bring the complete journey into 3D. '
        'The energetic synthetic author voice is retained without another tempo change.</p><p class="intro">', 1)
    html = html.replace(
        '<p id="load-status"',
        f'<p><a href="/static/{SOURCE}.html">Previous control-tower film</a>. '
        'All earlier editions are preserved.</p><p>Humans are rendered Microsoft Rocketbox avatars '
        'under the MIT license, not live-action actors. Official Azure Local, Azure Arc, Azure '
        'Container Registry and Azure Monitor SVGs are used unchanged, upright and labelled. '
        'Microsoft and operator trademarks remain with their owners; no endorsement is implied. '
        '<a href="/static/film3d-assets/ATTRIBUTION.md">Asset attribution and licenses</a>.</p>'
        '<p id="load-status"', 1)
    (STATIC / f"{edition}.html").write_text(html, encoding="utf-8")
    return story, sum(scene["duration"] for scene in story["scenes"])


def verify_visuals(url, output, review):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_function("() => Boolean(window.amaStory)", timeout=120000)
            page.evaluate("document.fonts.ready")
            evidence = page.evaluate("""() => {
                const story=window.amaStory, context=document.getElementById('story').getContext('2d');
                return story.data.scenes.map(scene => {
                    const beat=scene.beats[0];
                    story.render(scene.start+beat.start+beat.duration*.2);
                    const a=context.getImageData(35,225,1345,390).data;
                    const state=story.render(scene.start+beat.start+beat.duration*.75);
                    const b=context.getImageData(35,225,1345,390).data;
                    let changed=0;
                    for(let i=0;i<a.length;i+=4)
                        if(Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2])>30)changed++;
                    return {...state, changedPixels:changed};
                });
            }""")
            for row in evidence:
                if row["changedPixels"] < 1000 or not row["perspectiveCamera"] or not row["shadows"]:
                    raise ValueError(f"Missing real 3D motion/lighting in {row['scene']}")
                if row["geometryTriangles"] < 10000 or row["avatarCount"] != 5 or row["towerHeight"] < 9:
                    raise ValueError(f"Missing 3D geometry/tower/avatars in {row['scene']}")
                if any(h["boneCount"] < 20 or h["texturedMeshes"] < 1 or h["skinnedMeshes"] < 1 for h in row["humans"]):
                    raise ValueError("The film must use genuinely textured, rigged human meshes")
            expected = {"Azure Local", "Azure Arc", "Azure Container Registry", "Azure Monitor"}
            if {icon["name"] for row in evidence for icon in row["officialIcons"]} != expected:
                raise ValueError("Required official Microsoft product icons are not displayed")
            if "customer" not in evidence[0]["visibleHumans"]:
                raise ValueError("The suited human customer is missing from the opening")
            human = next(row for row in evidence if row["scene"] == "human")
            if "manager" not in human["visibleHumans"]:
                raise ValueError("Human service manager is missing from the approval chapter")
            if review:
                review.mkdir(parents=True, exist_ok=True)
                for scene in page.evaluate("window.amaStory.data.scenes"):
                    image = page.evaluate("""time => {
                        window.amaStory.render(time);
                        return document.getElementById('story').toDataURL('image/png').split(',')[1];
                    }""", scene["start"] + scene["duration"] * .48)
                    (review / f"3d-{scene['id']}.png").write_bytes(base64.b64decode(image))
                    if scene["id"] in {"opening", "human"}:
                        close = page.evaluate("""time => {
                            window.amaStory.render(time);
                            return document.getElementById('story').toDataURL('image/png').split(',')[1];
                        }""", scene["start"] + 1.2)
                        (review / f"3d-{scene['id']}-close.png").write_bytes(base64.b64decode(close))
            output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return evidence
        finally:
            browser.close()


def build(edition, scratch, review, preview_only=False):
    ensure_new(edition)
    existing = snapshot_videos()
    story, duration = prepare(edition)
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "app"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/static/{edition}.html"
    try:
        motion = verify_visuals(url, STATIC / f"{edition}-motion.json", review)
        print(f"True 3D preview verified: {motion[0]['gpu']}", flush=True)
        if preview_only:
            return
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(STATIC / f"{SOURCE}.mp4"),
            "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(scratch / "voice.wav"),
        ], check=True)
        render(url, scratch / "rendered.mp4", scratch, duration, 24,
               STATIC / f"{edition}-poster.jpg", audio_path=scratch / "voice.wav")
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=10)
        if worker.is_alive():
            raise RuntimeError("The read-only local 3D preview server did not shut down")
    metadata = [";FFMETADATA1", "title=AMA - Mission Activate - True 3D",
                "comment=Rendered human characters; synthetic author voice; official Microsoft product icons; "
                "Azure Local proposed, not deployed"]
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
        "-i", str(STATIC / f"{SOURCE}.mp4"), "-i", str(STATIC / f"{edition}.vtt"),
        "-i", str(scratch / "chapters.txt"), "-map", "0:v", "-map", "1:a", "-map", "2:0",
        "-map_metadata", "3", "-map_chapters", "3", "-c:v", "copy", "-c:a", "copy",
        "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", str(final),
    ], check=True)
    actual = verify_media(final, story, motion)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(final), "-map", "0:v", "-map", "0:a",
                    "-f", "null", "-"], check=True)
    for filename, sha in existing.items():
        if digest(STATIC / filename) != sha:
            raise RuntimeError(f"Earlier video changed: {filename}")
    with (STATIC / f"{edition}.mp4").open("xb") as target, final.open("rb") as source:
        shutil.copyfileobj(source, target)
    (STATIC / f"{edition}-motion.json").write_text(json.dumps(motion, indent=2) + "\n", encoding="utf-8")
    evidence = {"edition": edition, "duration": actual, "existingVideosUnchanged": True,
                "preservedVideos": existing, "sourceVoiceVideo": story["sourceVideo"],
                "audioCopiedWithoutReencoding": True, "rendering": story["rendering"],
                "microsoftIcons": story["microsoftIcons"]}
    (STATIC / f"{edition}-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Created {edition}.mp4 ({actual:.3f}s). Previous films remain unchanged.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", default="solution-3d-v1")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--review-dir", type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        prepare(args.edition)
        return
    with tempfile.TemporaryDirectory(prefix="ama-film3d-") as directory:
        build(args.edition, Path(directory), args.review_dir, args.preview_only)


if __name__ == "__main__":
    main()
