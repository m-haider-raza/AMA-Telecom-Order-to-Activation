"""Rebuild the public case-study bundle and checksums from current repository assets."""

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
DEST = STATIC / "case-study"
RELEASE = "turkbiztel-case-study-2026-11-24"
BUNDLE = "TurkBizTel-Case-Study-2026-11-24.zip"


def main():
    manifest_path = DEST / "artefacts.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = {}
    for row in manifest["files"]:
        name = row["file"]
        if name == "README.md":
            source = ROOT / name
        elif name.startswith("guides/"):
            source = ROOT / "docs" / name.removeprefix("guides/")
        else:
            source = DEST / name
            if not source.is_file():
                source = STATIC / name
        sources[name] = source
    for pattern in ("slide-*.webp", "thumb-*.webp"):
        sources.update((path.name, path) for path in sorted(DEST.glob(pattern)))
    for name in ("case-study.html", "case-study.css", "case-study.js", "current-scenario-turkbiztel-poster.jpg",
                 "journey-data.json", "solution-tower-v6-turkbiztel-poster.jpg", "solution-tower-v6-data.json"):
        sources[name] = STATIC / name
    manifest.update(
        release=RELEASE,
        githubRelease=manifest["github"] + "/releases/tag/" + RELEASE,
        bundle=BUNDLE,
        architectureRevision="2026-11-24-governed-production-target-19-slides",
    )
    manifest["files"] = []
    for name, source in sources.items():
        content = source.read_bytes()
        manifest["files"].append({
            "file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sums = "".join(f"{row['sha256']}  {row['file']}\n" for row in manifest["files"])
    (DEST / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    with zipfile.ZipFile(DEST / BUNDLE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, source in sources.items():
            archive.write(source, name)
        archive.write(manifest_path, "artefacts.json")
        archive.writestr("SHA256SUMS.txt", sums)
    with zipfile.ZipFile(DEST / BUNDLE) as archive:
        assert archive.testzip() is None
        for row in manifest["files"]:
            content = archive.read(row["file"])
            assert len(content) == row["bytes"]
            assert hashlib.sha256(content).hexdigest() == row["sha256"]
    print(f"Published {len(sources)} current assets in {BUNDLE}; every archive checksum verified.")


if __name__ == "__main__":
    main()
