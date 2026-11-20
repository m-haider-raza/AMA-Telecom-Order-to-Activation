"""Build the animated solution film with offline speech and verified workflow evidence.

Requires the optional playwright, pywin32, Edge and FFmpeg tools described in README.md.
Starts and stops a separate loopback demo using temporary synthetic state and random
credentials. Never reads saved credentials or changes an existing demo workspace.
"""

import argparse
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from Render_Story import assemble_narration, narrate, render

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def wait_order(client, order_id, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        response.raise_for_status()
        order = response.json()["orders"][order_id]
        if order["state"] == expected:
            return order
        time.sleep(.15)
    raise TimeoutError(f"Order {order_id} did not reach {expected}")


def capture_demo(url, manager, partner):
    evidence = {"syntheticOnly": True, "capture": "Isolated local instance; scenario waits edited out", "orders": {}}
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {manager}"}, trust_env=False) as client:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge")
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 850}, device_scale_factor=1)
                # Refresh explicitly so polling cannot detach a DOM node during its screenshot.
                page.add_init_script("window.setInterval = () => 0;")
                page.goto(url)
                page.wait_for_function("() => Boolean(snapshot)")
                page.evaluate("document.fonts.ready")

                def refresh():
                    page.evaluate("async () => { await refresh(); }")

                def screen(name, drawer=False):
                    page.screenshot(path=str(STATIC / f"solution-{name}.jpg"), type="jpeg", quality=94,
                                    clip={"x": 820, "y": 45, "width": 620, "height": 600}
                                    if drawer else None)

                def create(scenario, actor="manager"):
                    payload = {
                        "customer": "Demo Marmara Logistics", "city": "Istanbul", "product": "connectivity",
                        "scenario": scenario, "language": "tr", "idempotencyKey": secrets.token_hex(12),
                    }
                    if actor == "partner":
                        response = client.post("/mcp", headers={"Authorization": f"Bearer {partner}"}, json={
                            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "submit_partner_order", "arguments": payload},
                        })
                        response.raise_for_status()
                        body = response.json()
                        if body.get("error") or body["result"].get("isError"):
                            raise RuntimeError(f"MCP order failed: {body}")
                        return json.loads(body["result"]["content"][0]["text"])["id"]
                    response = client.post("/api/orders", json=payload)
                    response.raise_for_status()
                    return response.json()["id"]

                page.click("#new-order")
                page.locator("#order-dialog").screenshot(
                    path=str(STATIC / "solution-intake.jpg"), type="jpeg", quality=94)
                page.locator("#order-dialog .close-dialog").click()
                happy = create("happy")
                evidence["orders"]["happy"] = wait_order(client, happy, "completed")
                refresh()
                page.evaluate("id => openOrder(id)", happy)
                screen("active", drawer=True)
                page.evaluate("closeOrder()")

                recovered = create("auto-recovery")
                evidence["orders"]["auto-recovery"] = wait_order(client, recovered, "completed")
                if evidence["orders"]["auto-recovery"].get("recoveryAttempts") != 1:
                    raise AssertionError("Recovery must be bounded to one attempt")
                refresh()
                page.evaluate("id => openOrder(id)", recovered)
                page.evaluate("""() => {
                    const item = document.querySelectorAll('.timeline-item')[2];
                    document.getElementById('order-drawer').scrollTop += item.getBoundingClientRect().top - 100;
                }""")
                first = page.locator(".timeline-item").nth(2).bounding_box()
                last = page.locator(".timeline-item").nth(4).bounding_box()
                page.screenshot(path=str(STATIC / "solution-recovery.jpg"), type="jpeg", quality=94,
                                clip={"x": first["x"] - 14, "y": first["y"] - 14,
                                      "width": first["width"] + 28,
                                      "height": last["y"] + last["height"] - first["y"] + 28})
                page.locator(".message").screenshot(
                    path=str(STATIC / "solution-messages.jpg"), type="jpeg", quality=94)
                page.evaluate("closeOrder()")

                held = create("human-exception")
                held_order = wait_order(client, held, "pendingApproval")
                if held_order["reserved"]:
                    raise AssertionError("Route conflict allocated capacity before approval")
                evidence["orders"]["human-exception-before"] = held_order
                denied = client.post(f"/api/orders/{held}/decision",
                                     headers={"Authorization": f"Bearer {partner}"},
                                     json={"decision": "approve"})
                if denied.status_code != 403:
                    raise AssertionError("Partner was not denied approval")
                evidence["partnerApprovalStatus"] = denied.status_code
                refresh()
                page.evaluate("id => openOrder(id)", held)
                page.evaluate("document.getElementById('order-drawer').scrollTop = 0")
                screen("exception", drawer=True)
                response = client.post(f"/api/orders/{held}/decision", json={"decision": "approve"})
                response.raise_for_status()
                evidence["orders"]["human-exception-after"] = wait_order(client, held, "completed")
                page.evaluate("closeOrder()")

                window = create("change-window")
                evidence["orders"]["change-window-before"] = wait_order(client, window, "held")
                if evidence["orders"]["change-window-before"]["reserved"]:
                    raise AssertionError("Closed change window allocated capacity")
                response = client.post(f"/api/orders/{window}/decision", json={"decision": "approve"})
                response.raise_for_status()
                evidence["orders"]["change-window-after"] = wait_order(client, window, "completed")

                invalid = create("address-correction")
                evidence["orders"]["address-before"] = wait_order(client, invalid, "awaitingCustomer")
                response = client.post(f"/api/orders/{invalid}/address", json={"siteReference": "IST-001"})
                response.raise_for_status()
                evidence["orders"]["address-after"] = wait_order(client, invalid, "completed")

                partner_id = create("happy", actor="partner")
                evidence["orders"]["partner"] = wait_order(client, partner_id, "completed")
                page.evaluate("showPage('partners')")
                page.click("#access-button")
                page.fill("#access-form input[name=token]", partner)
                page.click("#access-form button[type=submit]")
                page.click("#capacity-button")
                page.wait_for_function("() => document.getElementById('partner-result').textContent.includes('capacity')")
                page.wait_for_timeout(300)
                screen("partners")
                response = client.get("/api/state")
                response.raise_for_status()
                evidence["auditChainValid"] = response.json()["metrics"]["auditChainValid"]
                if not evidence["auditChainValid"]:
                    raise AssertionError("Audit chain invalid")
                for name in ("happy", "auto-recovery", "partner"):
                    order = evidence["orders"][name]
                    if not order["notifications"] or not all(n["tr"] and n["en"] for n in order["notifications"]):
                        raise AssertionError(f"Bilingual updates missing for {name}")
            finally:
                browser.close()
    (STATIC / "solution-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def verify_animation(url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.wait_for_function("() => Boolean(window.amaStory)")
            evidence = page.evaluate("""() => {
                const story = window.amaStory, canvas = document.getElementById('story');
                const context = canvas.getContext('2d');
                return story.data.scenes.map(scene => {
                    const beat = scene.beats[0];
                    const first = scene.start + beat.start + beat.duration * .2;
                    const second = scene.start + beat.start + beat.duration * .75;
                    const state = story.render(first);
                    const a = context.getImageData(0, 170, 1280, 280).data;
                    story.render(second);
                    const b = context.getImageData(0, 170, 1280, 280).data;
                    let changed = 0;
                    for (let i = 0; i < a.length; i += 4) {
                        if (Math.abs(a[i]-b[i]) + Math.abs(a[i+1]-b[i+1]) + Math.abs(a[i+2]-b[i+2]) > 30) changed++;
                    }
                    return {scene: scene.id, mode: scene.mode, changedPixels: changed,
                            agentStates: state.agentStates, event: state.event};
                });
            }""")
            for scene in evidence:
                if scene["changedPixels"] < 500:
                    raise AssertionError(f"Scene has insufficient diagram motion: {scene}")
            (STATIC / "solution-motion.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        finally:
            browser.close()


def verify_encoded_motion(path, story):
    evidence_path = STATIC / "solution-motion.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    by_id = {row["scene"]: row for row in evidence}
    start = 0
    for scene in story["scenes"]:
        beat = scene["beats"][0]
        frames = []
        for progress in (.2, .75):
            position = start + beat["start"] + beat["duration"] * progress
            result = subprocess.run([
                "ffmpeg", "-loglevel", "error", "-ss", str(position), "-i", str(path),
                "-frames:v", "1", "-vf", "crop=1280:280:0:170", "-pix_fmt", "rgb24",
                "-f", "rawvideo", "pipe:1",
            ], check=True, capture_output=True)
            if len(result.stdout) != 1280 * 280 * 3:
                raise ValueError(f"Unable to decode a motion sample for {scene['id']}")
            frames.append(result.stdout)
        changed = sum(
            sum(abs(frames[0][i + c] - frames[1][i + c]) for c in range(3)) > 30
            for i in range(0, len(frames[0]), 3)
        )
        if changed < 500:
            raise ValueError(f"Encoded scene {scene['id']} has insufficient diagram motion: {changed}")
        by_id[scene["id"]]["encodedChangedPixels"] = changed
        start += scene["duration"]
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def prepare_narration(story, scratch, voice, narration_dir):
    options = {"data_path": STATIC / "solution-data.json", "captions_path": STATIC / "solution.vtt",
               "pauses": (.18, .12, .18), "max_duration": 179}
    if narration_dir is None:
        story["narration"] = {"kind": "system-synthetic", "disclosure": "Synthetic system narration"}
        return narrate(story, scratch, voice, **options)
    manifest = json.loads((narration_dir / "manifest.json").read_text(encoding="utf-8"))
    expected = [b["text"] for scene in story["scenes"] for b in scene["beats"]]
    if manifest.get("texts") != expected:
        raise ValueError("Private narration does not match the current script; regenerate it.")
    if manifest.get("kind") != "author-voice-synthetic":
        raise ValueError("Expected locally generated author-voice narration, not a system-voice fallback")
    story["narration"] = {
        "kind": manifest["kind"], "model": manifest["model"],
        "disclosure": "Synthetic narration generated locally from the author's supplied voice sample.",
    }
    return assemble_narration(story, narration_dir, scratch, **options)


def build(scratch, voice, fps, narration_dir=None):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    manager, partner = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = {**os.environ, "DEMO_ADMIN_TOKEN": manager, "DEMO_PARTNER_TOKEN": partner,
           "LOCAL_STATE_PATH": str(scratch / "state.json"), "DEMO_TICK_SECONDS": ".15",
           "AZURE_STORAGE_ACCOUNT_URL": "", "AZURE_OPENAI_ENDPOINT": "", "AZURE_OPENAI_DEPLOYMENT": "",
           "AZURE_REGION": "local"}
    previous_env = os.environ.copy()
    os.environ.update(env)
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port,
                                         workers=1, log_level="warning"))
    worker = threading.Thread(target=server.run, name="isolated-solution-demo", daemon=True)
    try:
        worker.start()
        try:
            deadline = time.monotonic() + 30
            with httpx.Client(base_url=url, timeout=1, trust_env=False) as client:
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass  # Expected only while the isolated server binds its loopback socket.
                    if not worker.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError("Isolated server did not start; inspect its logged startup error")
                    time.sleep(.1)
            print("Capturing real application scenarios in isolated temporary state.", flush=True)
            logging.getLogger("httpx").setLevel(logging.WARNING)
            capture_demo(url, manager, partner)
            story = json.loads((STATIC / "solution-data.json").read_text(encoding="utf-8"))
            duration = prepare_narration(story, scratch, voice, narration_dir)
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(scratch / "narration.wav"),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=9", "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", str(scratch / "normalized.wav"),
            ], check=True)
            (scratch / "normalized.wav").replace(scratch / "narration.wav")
            verify_animation(url + "/static/solution.html")
            print(f"Narration: {duration:.1f} seconds. Rendering {fps} fps.", flush=True)
            render(url + "/static/solution.html", scratch / "rendered.mp4", scratch, duration, fps,
                   STATIC / "solution-poster.jpg")
            metadata = [";FFMETADATA1", "title=AMA Telecom - From order to activation",
                        f"comment=Synthetic demonstration; targets are not measured results; "
                        f"{story['narration']['disclosure']}"]
            start = 0
            for scene in story["scenes"]:
                end = start + round(scene["duration"] * 1000)
                metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                                 f"title={scene['chapter']}"])
                start = end
            (scratch / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(scratch / "rendered.mp4"),
                "-i", str(STATIC / "solution.vtt"), "-i", str(scratch / "chapters.txt"),
                "-map", "0:v", "-map", "0:a", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
                "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
                "-movflags", "+faststart", str(scratch / "final.mp4"),
            ], check=True)
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(scratch / "final.mp4"),
            ], check=True, capture_output=True, text=True)
            if float(json.loads(probe.stdout)["format"]["duration"]) > 180:
                raise ValueError("Encoded solution film exceeds the three-minute limit")
            verify_encoded_motion(scratch / "final.mp4", story)
            (scratch / "final.mp4").replace(STATIC / "solution.mp4")
        finally:
            server.should_exit = True
            worker.join(timeout=15)
            if worker.is_alive():
                raise RuntimeError("Isolated server did not shut down")
    finally:
        os.environ.clear()
        os.environ.update(previous_env)
    print(f"Created {STATIC / 'solution.mp4'}", flush=True)


def main():
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", default="Microsoft Zira Desktop")
    parser.add_argument("--fps", type=int, default=24, choices=(12, 24, 30))
    parser.add_argument("--narration-dir", type=Path,
                        help="Private local voice segments and matching manifest from Synthesize_Solution.py")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ama-solution-") as directory:
        build(Path(directory), args.voice, args.fps, args.narration_dir)


if __name__ == "__main__":
    main()
