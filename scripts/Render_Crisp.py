"""Rebuild the selected tower film with crisp messages and a distinct local narrator."""

import argparse
import base64
from copy import deepcopy
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
from Render_Orchestration import ROOT, STATIC, digest, ensure_new_edition, snapshot_videos, verify_media
from Render_Story import assemble_narration, render, soundscape

SCRIPT = ROOT / "scripts" / "film3d" / "tower-crisp-story.json"
SOURCE = "solution-tower-v1"
DELIVERY_TEMPO = 1.15


def prepare(edition, directory, scratch, script_path=SCRIPT):
    ensure_new_edition(edition)
    if directory.resolve().is_relative_to(ROOT):
        raise ValueError("Intermediate narration must remain outside the repository")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    script = json.loads(script_path.read_text(encoding="utf-8"))
    delivery_tempo = script.get("deliveryTempo", DELIVERY_TEMPO)
    if not .5 <= delivery_tempo <= 2:
        raise ValueError("Pitch-preserving delivery tempo must be between 0.5 and 2")
    if manifest.get("texts") != [b["text"] for s in script["scenes"] for b in s["beats"]]:
        raise ValueError("Preset narration does not match the crisp script")
    if manifest.get("kind") != "local-preset-neural" or manifest.get("referenceAudioUsed") is not False:
        raise ValueError("Use a distinct preset narrator, not a cloned author voice")
    source = json.loads((STATIC / f"{SOURCE}-data.json").read_text(encoding="utf-8"))
    old_scenes = {s["id"]: s for s in source["scenes"]}
    source.update(
        edition=edition, title=script["title"], subtitle=script["subtitle"],
        presentation="crisp-control-tower", visualTheme="orchestration-control-tower",
        editionLabel="CRISP STORY / NEW NARRATOR", voiceLabel="DISTINCT NEURAL NARRATOR / GENERATED LOCALLY",
        sourceVideo={"file": f"{SOURCE}.mp4", "sha256": digest(STATIC / f"{SOURCE}.mp4"),
                     "use": "Selected tower design and characters; no source audio reused"},
        narration={k: manifest[k] for k in [
            "kind", "model", "voice", "voiceDescription", "language", "speed", "modelSHA256", "source",
            "modelLicense", "runtimeLicense", "referenceAudioUsed",
        ]},
        voiceTreatment={"tempo": manifest["speed"], "deliveryTempo": delivery_tempo, "pitchPreserved": True,
                        "presenceEQ": "3000 Hz / +2 dB", "compressionRatio": 2,
                        "soundscape": "Original quiet pulse, normalized behind the narrator"},
    )
    source["narration"]["disclosure"] = (
        f"Locally generated neural narration: {manifest['voiceDescription']} "
        f"({manifest['model']} / {manifest['voice']}). "
        f"Brisk, pitch-preserving {delivery_tempo:.2f}x delivery treatment; "
        "no reference audio or script was uploaded."
    )
    source.update(script.get("presentationLabels", {}))
    if script.get("proposalEdition"):
        source["proposalEdition"] = True
        source["references"] = deepcopy(script["references"])
    source["scenes"] = []
    for revised in script["scenes"]:
        scene = old_scenes[revised["id"]]
        scene.update(deepcopy(revised))
        scene["chapter"] = re.sub(r"^\d{2} / ", "", scene["chapter"])
        scene["minimumDuration"] = 8 if scene["id"] not in {"residency", "hybrid", "impact"} else 12
        if scene["id"] == "intake":
            scene["events"][1] = "Invalid request -> submitter corrects before acceptance"
            scene["agentStates"][1] = ["waiting", "waiting", "waiting", "standby", "waiting"]
        elif scene["id"] == "validation":
            scene["events"][1] = "Site or capacity problem -> correction or manager review"
            scene["agentStates"][1] = ["held", "waiting", "waiting", "standby", "waiting"]
        elif scene["id"] == "recovery":
            scene["events"][1] = "Unknown fault -> proposed human triage / not a demo queue"
            scene["agentStates"][1] = ["done", "done", "held", "held", "waiting"]
        elif scene["id"] == "standards":
            scene["events"] = ["TMF-inspired product and service contracts", "Authenticated MCP / no privileged bypass"]
        elif scene["id"] == "residency":
            scene["events"] = ["Azure Local / operator datacenter in Turkey", "Customer workload remains local / proposed"]
        elif scene["id"] == "hybrid":
            scene["events"] = ["Images in / approved metadata out / customer data blocked",
                               "Stricter boundary -> assess eligible disconnected operations"]
        if "events" in revised:
            scene["events"] = revised["events"]
        source["scenes"].append(scene)
    for filename, sha in ICONS.items():
        if digest(STATIC / "film3d-assets" / filename) != sha:
            raise ValueError(f"Official Microsoft icon was modified: {filename}")
    source["microsoftIcons"] = {"unchangedSvgSHA256": ICONS}
    paced = scratch / "paced"
    paced.mkdir()
    for i, scene in enumerate(source["scenes"]):
        for j, _ in enumerate(scene["beats"]):
            subprocess.run([
                "ffmpeg", "-v", "error", "-i", str(directory / f"voice-{i}-{j}.wav"),
                "-af", f"atempo={delivery_tempo},highpass=f=75,equalizer=f=3000:t=q:w=1:g=2,"
                "acompressor=threshold=0.1:ratio=2:attack=8:release=90:makeup=1.2",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(paced / f"voice-{i}-{j}.wav"),
            ], check=True)
    duration = assemble_narration(
        source, paced, scratch, STATIC / f"{edition}-data.json", STATIC / f"{edition}.vtt",
        pauses=(.22, .18, .5), max_duration=179,
    )
    html = (STATIC / f"{SOURCE}.html").read_text(encoding="utf-8").replace(SOURCE, edition)
    html = html.replace(
        '<script src="/static/orchestration-theme.js" defer></script>',
        '<script src="/static/orchestration-theme.js" defer></script>\n'
        '  <script src="/static/tower-crisp-theme.js" defer></script>',
    ).replace("Control tower edition</title>", "Crisp tower edition</title>")
    html = html.replace("NEW EDITION / THE ORCHESTRATION CONTROL TOWER", "NEW EDITION / CRISP STORY AND A NEW VOICE")
    html = html.replace("<h1>Mission: Activate.</h1>", "<h1>One order. One accountable flow.</h1>")
    start, end = html.index('<p class="intro">'), html.index('<video id="film"')
    html = html[:start] + (
        '<p class="intro">The selected tower film, sharpened: one supervisor, five bounded specialists, '
        'clear handoffs and a distinct energetic neural narrator. Early checks, linked identifiers, '
        'allocate-once guards and bounded recovery explain how avoidable rework and waiting can fall.</p>\n'
        '<p>Human approval remains explicit. Generic fault triage, catalogue onboarding and external-message '
        'incidents are production gates, not implemented demo queues. Residency is proposed; '
        'business targets are not measured results.</p>\n'
    ) + html[end:]
    html = html.replace(
        '<p id="load-status"',
        f'<p><a href="/static/{SOURCE}.html">Original tower film</a> / '
        '<a href="/static/solution-clarity-v2.html">3D clarity film</a>. All earlier MP4s remain unchanged.</p>'
        '<p>Microsoft product SVGs are unchanged and labelled for their actual services. '
        'No endorsement or actual operator integration is implied.</p><p id="load-status"', 1)
    html = html.replace(
        "Order A follows timeout recovery; B is an alternate manager-review branch; C is a partner-originated request.",
        "Order A follows timeout recovery; B illustrates the alternate human-review branch. "
        "Authenticated partner tools are explained with the standards handoffs.",
    )
    if script.get("customerEdition"):
        html = customer_player(edition, script)
    (STATIC / f"{edition}.html").write_text(html, encoding="utf-8")
    return source, duration


def customer_player(edition, script):
    governance = script.get("governanceEdition", False)
    proposal = script.get("proposalEdition", False)
    channel_scope = (
        "The trusted boundary sends real Azure Communication Services email. "
        "Customer status links require sign-in and order ownership; partner access is organisation-scoped."
        if governance else
        "The customer portal is the demonstrated message channel; external delivery needs integrated channel services."
    )
    governance_scope = (
        "<p>Hosted Entra agent identities and runtime managed identities are distinct. "
        "Receiving APIs enforce caller identity, roles, operation allowlists and current policy; "
        "agents cannot write state or grant approvals directly. Staff token fallback has operator permissions, "
        "not a personal service-manager identity.</p>"
        "<p>Named business sponsor and technical-owner assignments, access reviews and incident alerts "
        "are production operating-model requirements, not verified live assignments. "
        "Agent 365 catalog registration remains pending tenant approval; "
        "it does not replace the authorization controls already enforced.</p>"
        if governance else ""
    )
    previous = (
        '<a href="/static/solution-tower-v6-turkbiztel.mp4">Archived first-mention film</a>'
        if governance else
        '<a href="/static/solution-tower-v2.html">Previous crisp tower edition</a>'
    )
    scope = (
        "This customer walkthrough describes the solution design and illustrative workflow. "
        "The running Azure reference implementation uses synthetic data and simulated carrier adapters. "
        "Türkiye residency requires supported Azure Local infrastructure at an approved Turkish site, "
        "local application services, inference, keys, logs and backups, and approved egress controls. "
        "Connected Arc platform metadata requires review; disconnected operation requires eligibility and capacity."
    )
    delivery = (
        "Business objectives describe the intended value, rather than a quantified customer outcome. "
        "TMF622/TMF641-inspired interfaces require customer-specific contracts and conformance acceptance. "
        f"{channel_scope} "
        "Unknown-fault escalation requires a service-desk integration and accountable operational owner."
    )
    if proposal:
        scope = (
            "This film presents a customer solution proposal. Azure Local at an approved Turkish operator site "
            "hosts customer workloads and inference, with local keys, logs and backups. "
            "Outbound metadata flows are reviewed and controlled."
        )
        delivery = (
            "TM Forum-inspired product, service and resource contracts keep the order journey linked. "
            "The trusted boundary controls state changes and Azure Communication Services email. "
            "Customers sign in to see their own orders; partner access is organisation-scoped. "
            "Bounded recovery hands other faults to an accountable service desk."
        )
        governance_scope = (
            "<p>Each agent has a distinct Entra identity and only the projects, tools and operations it needs. "
            "Receiving APIs enforce caller identity, role and current policy. "
            "Service managers use personal Entra sign-in; agents and partners cannot grant approvals.</p>"
            "<p>Microsoft Agent 365 is part of the proposed agent-governance solution, providing inventory, "
            "ownership visibility and oversight. Named business sponsors approve purpose, risk and budget; "
            "technical owners control releases and incidents. Audit trails, access reviews, alerts and "
            "identity revocation complement server-enforced permissions.</p>"
        )
        previous = '<a href="/static/solution-tower-v7-turkbiztel.mp4">Previous governance film</a>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TürkBizTel | Customer solution</title><link rel="stylesheet" href="/static/solution.css">
<script src="/static/orchestration-theme.js" defer></script>
<script src="/static/tower-crisp-theme.js" defer></script><script src="/static/solution.js" defer></script></head>
<body><header><a href="/">TürkBizTel / CUSTOMER SOLUTION</a><span>ORDER TO ACTIVATION</span></header><main>
<p class="eyebrow">CUSTOMER SOLUTION WALKTHROUGH</p><h1>{script['title']}</h1>
<p class="intro">{script['subtitle']}</p>
<video id="film" controls playsinline preload="metadata" poster="/static/{edition}-poster.jpg">
<source src="/static/{edition}.mp4" type="video/mp4">
<track kind="captions" src="/static/{edition}.vtt" srclang="en" label="English"></video>
<div class="downloads"><a href="/static/{edition}.mp4" download>Download MP4</a>
<a href="/static/{edition}.vtt" download>English captions</a><span id="runtime"></span></div>
<p id="voice-disclosure"></p><p id="load-status" role="status">Loading chapters...</p>
<nav id="chapters" aria-label="Video chapters"></nav>
<details><summary>Full narration transcript</summary><div id="transcript"></div></details>
<details><summary>Solution scope and delivery requirements</summary>
<p>{scope}</p>
<p>{delivery}</p>
{governance_scope}
<p>Microsoft product icons identify their services. Operator branding supplies industry context;
it implies no endorsement, relationship or actual carrier integration.</p>
<div id="references"></div></details>
<p>{previous}.
All earlier videos are preserved.</p>
<details id="animation-panel"><summary>Interactive solution walkthrough</summary>
<button id="animation-play" type="button">Play animation</button>
<canvas id="story" width="1920" height="1080" data-story="/static/{edition}-data.json"></canvas>
<label>Position <input id="scrub" type="range" min="0" max="1" step="0.05" value="0"></label></details>
</main><footer>TürkBizTel / ONE ORDER. ONE ACCOUNTABLE FLOW.</footer></body></html>"""


def verify_visuals(url, story, review):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_function("() => Boolean(window.amaStory)", timeout=120000)
            page.evaluate("document.fonts.ready")
            rows = page.evaluate("""() => {
                const s=window.amaStory,c=document.getElementById('story').getContext('2d');
                const originalFillText=c.fillText;
                let drawnText=[];
                c.fillText=function(...values) {
                    drawnText.push(String(values[0]));
                    return originalFillText.apply(this,values);
                };
                const inspect=time=>{
                    drawnText=[];
                    const state=s.render(time);
                    return {...state,visibleText:[...drawnText]};
                };
                return s.data.scenes.map(scene=>{
                    const beat=scene.beats[0];
                    s.render(scene.start+beat.start+beat.duration*.2);
                    const a=c.getImageData(35,225,1345,390).data;
                    const state=s.render(scene.start+beat.start+beat.duration*.75);
                    const b=c.getImageData(35,225,1345,390).data;
                    let changed=0;
                    for(let i=0;i<a.length;i+=4)
                      if(Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2])>30)changed++;
                    const beatChecks=scene.beats.map(b=>inspect(scene.start+b.start+.3));
                    return {...state,changedPixels:changed,beatChecks};
                });
            }""")
            for scene, row in zip(story["scenes"], rows, strict=True):
                if row["changedPixels"] < 1000 or row["tower"]["height"] < 500 or len(row["avatars"]) != 5:
                    raise ValueError(f"Tower identity or animation missing: {scene['id']}")
                for check in row["beatChecks"]:
                    if story.get("proposalEdition"):
                        text = " ".join(check["visibleText"]).lower()
                        if any(phrase in text for phrase in (
                            "synthetic", "simulat", "pilot", "pending", "not deployed", "not registered",
                            "not measured", "registration blocked", "no live network changes",
                        )) or re.search(r"\bdemo\b", text):
                            raise ValueError(f"Proposal contains demonstrator status wording: {scene['id']}")
                        if scene["id"] != "opening" and re.search(r"\bai\b", text):
                            raise ValueError(f"Proposal repeats AI after the introduction: {scene['id']}")
                    if story.get("editionLabel") == "CUSTOMER SOLUTION / ORDER TO ACTIVATION":
                        text = " ".join(check["visibleText"]).lower()
                        if any(phrase in text for phrase in ["proposed", "not deployed", "not measured",
                                                            "targets, not results", "35 days", "30%"]):
                            raise ValueError(f"Customer edition contains outdated status wording: {scene['id']}")
                    if check["captionLines"] > 2:
                        raise ValueError("Narration captions overflow")
                    for box in check["cardBounds"]:
                        if box["headingWidth"] > 280 or box["bodyWidth"] > 280:
                            raise ValueError("Crisp message cards overflow")
            if review:
                review.mkdir(parents=True, exist_ok=True)
                for scene in page.evaluate("window.amaStory.data.scenes"):
                    for j, beat in enumerate(scene["beats"]):
                        frame = page.evaluate("""t=>{
                            window.amaStory.render(t);
                            return document.getElementById('story').toDataURL('image/png').split(',')[1];
                        }""", scene["start"] + beat["start"] + .3)
                        (review / f"crisp-{scene['id']}-{j}.png").write_bytes(base64.b64decode(frame))
            return rows
        finally:
            browser.close()


def build(edition, directory, review, preview, script_path=SCRIPT):
    ensure_new_edition(edition)
    existing = snapshot_videos()
    with tempfile.TemporaryDirectory(prefix="ama-crisp-") as folder:
        scratch = Path(folder)
        story, duration = prepare(edition, directory, scratch, script_path)
        audio = None if preview else soundscape(story, scratch, duration)
        handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "app"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/static/{edition}.html"
            rows = verify_visuals(url, story, review)
            if preview:
                print(f"Crisp preview verified: {duration:.3f}s; {len(story['scenes'])} chapters.", flush=True)
                return
            print(f"Rendering crisp tower edition: {duration:.3f}s.", flush=True)
            render(url, scratch / "rendered.mp4", scratch, duration, 24,
                   STATIC / f"{edition}-poster.jpg", audio_path=audio)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("The render server did not stop")
        comment = (
            "Customer proposal: secure order activation and Agent 365 governance; locally generated preset narration"
            if story.get("proposalEdition") else
            "Distinct local neural preset voice; no author voice cloning; illustrative simulation"
        )
        metadata = [";FFMETADATA1", f"title=TürkBizTel - {story['title']}", f"comment={comment}"]
        position = 0
        for scene in story["scenes"]:
            end = position + round(scene["duration"] * 1000)
            metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={position}", f"END={end}",
                             f"title={scene['chapter']}"])
            position = end
        (scratch / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")
        final = scratch / "final.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(scratch / "rendered.mp4"),
            "-i", str(STATIC / f"{edition}.vtt"), "-i", str(scratch / "chapters.txt"),
            "-map", "0:v", "-map", "0:a", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
            "-movflags", "+faststart", str(final),
        ], check=True)
        actual = verify_media(final, story, rows)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(final), "-f", "null", "-"], check=True)
        if any(digest(STATIC / name) != sha for name, sha in existing.items()):
            raise RuntimeError("An existing video changed during rendering")
        with (STATIC / f"{edition}.mp4").open("xb") as target, final.open("rb") as source:
            shutil.copyfileobj(source, target)
        (STATIC / f"{edition}-motion.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        evidence = {
            "edition": edition, "duration": actual, "preservedVideos": existing, "existingVideosUnchanged": True,
            "sourceVideo": story["sourceVideo"], "distinctPresetNarrator": True, "referenceAudioUsed": False,
            "scriptSHA256": digest(script_path), "narration": story["narration"],
            "fullDecodeVerified": True, "captionCount": sum(len(s["beats"]) for s in story["scenes"]),
        }
        (STATIC / f"{edition}-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if story.get("editionLabel") == "IDENTITY & GOVERNANCE" or story.get("proposalEdition"):
            transcript = [story["title"], story["subtitle"], ""]
            for scene in story["scenes"]:
                transcript.extend([scene["chapter"], *(beat["text"] for beat in scene["beats"]), ""])
            (STATIC / f"{edition}-transcript.txt").write_text("\n".join(transcript), encoding="utf-8")
        print(f"Created {edition}.mp4: {actual:.3f}s; all previous MP4s unchanged.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", default="solution-tower-v2")
    parser.add_argument("--narration-dir", required=True, type=Path)
    parser.add_argument("--review-dir", type=Path)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--story", type=Path, default=SCRIPT)
    args = parser.parse_args()
    build(args.edition, args.narration_dir, args.review_dir, args.preview_only, args.story)


if __name__ == "__main__":
    main()
