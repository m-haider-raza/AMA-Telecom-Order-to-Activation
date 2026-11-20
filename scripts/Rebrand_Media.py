"""Create non-overwriting TürkBizTel film editions, preserving narration, captions and chapters."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.branded_static import FILMS, MEDIA_ALIASES, SCREENSHOTS  # noqa: E402

STATIC = ROOT / "app" / "static"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def probe(path):
    return json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, encoding="utf-8").stdout)


def audio_digest(path):
    return subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
        "-f", "hash", "-hash", "sha256", "pipe:1",
    ], check=True, capture_output=True, text=True).stdout.strip()


def brand_film(name, font):
    source = STATIC / f"{name}.mp4"
    target = STATIC / MEDIA_ALIASES[source.name]
    poster = STATIC / MEDIA_ALIASES[f"{name}-poster.jpg"]
    if target.exists() or poster.exists():
        raise FileExistsError(f"Existing branded film is protected: {target.name}")
    before = digest(source)
    original = probe(source)
    video = next(stream for stream in original["streams"] if stream["codec_type"] == "video")
    width, height = video["width"], video["height"]
    with tempfile.TemporaryDirectory(prefix="turkbiztel-film-") as directory:
        scratch = Path(directory)
        shutil.copyfile(font, scratch / "font.ttf")
        label = "TürkBizTel / CURRENT STATE" if name == "current-scenario" else "TürkBizTel / ORDER TO ACTIVATION"
        (scratch / "brand.txt").write_text(label, encoding="utf-8")
        (scratch / "operator.txt").write_text("TürkBizTel", encoding="utf-8")
        if width == 1920:
            filters = [
                "drawbox=x=25:y=10:w=740:h=48:color=0x0b1725:t=fill",
                "drawtext=fontfile=font.ttf:textfile=brand.txt:fontcolor=0x8ee4c7:fontsize=24:x=48:y=20",
            ]
        elif name == "current-scenario":
            filters = [
                "drawbox=x=45:y=15:w=520:h=40:color=0x080e20:t=fill",
                "drawtext=fontfile=font.ttf:textfile=brand.txt:fontcolor=0xf4f7ff:fontsize=19:x=64:y=24",
            ]
        else:
            filters = [
                "drawbox=x=25:y=6:w=600:h=38:color=0x0b1725:t=fill",
                "drawtext=fontfile=font.ttf:textfile=brand.txt:fontcolor=0x8ee4c7:fontsize=18:x=40:y=13",
            ]
        if name == "solution" or name.startswith("solution-tower"):
            story = json.loads((STATIC / f"{name}-data.json").read_text(encoding="utf-8"))
            opening = story["scenes"][0]["duration"]
            x, y, w, h, size = (1070, 431, 205, 32, 20) if width == 1920 else (1023, 348, 168, 32, 18)
            filters.extend([
                f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x0c2232:t=fill:enable='lt(t,{opening})'",
                "drawtext=fontfile=font.ttf:textfile=operator.txt:fontcolor=0xedf5fa:"
                f"fontsize={size}:x={x}+({w}-text_w)/2:y={y}+6:enable='lt(t,{opening})'",
            ])
        staged = scratch / "branded.mp4"
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0", "-map", "0:s?", "-map_metadata", "0", "-map_chapters", "0",
            "-filter_threads", "2", "-vf", ",".join(filters), "-c:v", "libx264", "-threads", "2",
            "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "copy", "-c:s", "copy",
            "-metadata", f"title={label}", "-movflags", "+faststart", str(staged),
        ], cwd=scratch, check=True)
        media = probe(staged)
        seconds = float(media["format"]["duration"])
        if abs(seconds - float(original["format"]["duration"])) > .1 or seconds >= 180:
            raise ValueError(f"Branding changed the film duration: {name}")
        if len(media["chapters"]) != len(original["chapters"]) or audio_digest(source) != audio_digest(staged):
            raise ValueError(f"Branding changed chapters or narration: {name}")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(staged), "-f", "null", "-"], check=True)
        if digest(source) != before:
            raise RuntimeError(f"The original film changed: {name}")
        with target.open("xb") as output, staged.open("rb") as rendered:
            shutil.copyfileobj(rendered, output)
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", "1.2", "-i", str(target),
            "-frames:v", "1", "-q:v", "2", str(poster),
        ], check=True)
    print(f"Branded {name}: {seconds:.3f}s; original soundtrack preserved.", flush=True)
    return {
        "source": source.name, "sourceSHA256": before, "file": target.name, "sha256": digest(target),
        "poster": poster.name, "brand": "TürkBizTel", "duration": seconds, "width": width, "height": height,
        "narrationCopied": True, "fullDecodeVerified": True, "chapterCount": len(media["chapters"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path(r"C:\Windows\Fonts\segoeuib.ttf"))
    parser.add_argument("--edition", choices=FILMS, action="append",
                        help="Append a new film edition without touching earlier branded media")
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error("Provide an installed bold font with Turkish glyphs")
    evidence = STATIC / "turkbiztel-media.json"
    previous = {"brand": "TürkBizTel", "films": []}
    if evidence.exists():
        if not args.edition:
            raise FileExistsError("Existing branding evidence is protected; select a new --edition")
        previous = json.loads(evidence.read_text(encoding="utf-8"))
    editions = args.edition or FILMS
    existing = {row["source"] for row in previous["films"]}
    if len(set(editions)) != len(editions) or any(f"{name}.mp4" in existing for name in editions):
        raise FileExistsError("Existing branded editions are protected; select only new films")
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda name: brand_film(name, args.font), editions))
    for name in (() if args.edition else SCREENSHOTS):
        source = STATIC / f"{name}.jpg"
        target = STATIC / MEDIA_ALIASES[source.name]
        if target.exists():
            raise FileExistsError(f"Existing branded screenshot is protected: {target.name}")
        with Image.open(source) as image:
            branded = Image.new("RGB", (image.width, image.height + 38), "#0b1725")
            branded.paste(image, (0, 38))
            draw = ImageDraw.Draw(branded)
            draw.text((14, 6), "TürkBizTel", font=ImageFont.truetype(str(args.font), 21), fill="#8ee4c7")
            branded.save(target, quality=95)
    previous["films"].extend(rows)
    evidence.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
