"""Rebuild the public case-study bundle and checksums from current repository assets."""

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
DEST = STATIC / "case-study"
RELEASE = "turkbiztel-case-study-2026-11-27-proposal"
BUNDLE = "TurkBizTel-Case-Study-2026-11-27-Proposal.zip"


def main():
    manifest_path = DEST / "artefacts.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = {}
    for row in manifest["files"]:
        name = row["file"].replace("solution-tower-v6", "solution-tower-v8").replace(
            "solution-tower-v7", "solution-tower-v8")
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
                 "journey-data.json", "solution-tower-v8-turkbiztel-poster.jpg", "solution-tower-v8-data.json",
                 "solution-tower-v8.html", "solution-tower-v8-transcript.txt"):
        sources[name] = STATIC / name
    sources["guides/FOUNDRY-SSO-APP.md"] = ROOT / "docs" / "FOUNDRY-SSO-APP.md"
    sources["guides/AGENT365-STATUS.md"] = ROOT / "docs" / "AGENT365-STATUS.md"
    for extension in ("pptx", "pdf"):
        name = f"TurkBizTel-Foundry-Model-Economics.{extension}"
        content = (ROOT / "docs" / "presentations" / name).read_bytes()
        (DEST / name).write_bytes(content)
        sources[name] = DEST / name
    manifest.update(
        release=RELEASE,
        bundle=BUNDLE,
        architectureRevision="2026-11-24-governed-production-target-19-slides",
        solutionFilmRevision="2026-11-27-customer-proposal",
        solutionFilmScope="Customer proposal; runbooks separately document the running application.",
        solutionFilm=manifest["ssoApp"] + "/static/solution-tower-v8.mp4",
    )
    manifest.pop("githubRelease", None)
    manifest["files"] = []
    for name, source in sources.items():
        content = source.read_bytes()
        if source.suffix in {".md", ".html", ".css", ".js", ".json", ".vtt", ".txt"}:
            content = content.replace(b"\r\n", b"\n")
            source.write_bytes(content)
        manifest["files"].append({
            "file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="\n")
    sums = "".join(f"{row['sha256']}  {row['file']}\n" for row in manifest["files"])
    (DEST / "SHA256SUMS.txt").write_text(sums, encoding="utf-8", newline="\n")
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
