"""Byte-for-byte preservation check shared by the film edition tests."""

import hashlib
import json
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

# The author replaced current-scenario.mp4 on 12 November 2026, after the earliest editions had
# recorded its hash. Editions from solution-tower-v3 on pin the replacement, and their tests keep
# guarding it. The older evidence files stay exactly as they were written.
REPLACED_AFTER_RECORDING = "current-scenario.mp4"
CURRENT_PIN = "solution-tower-v6-evidence.json"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def assert_text_preserved(path, recorded):
    """Story scripts were hashed with Windows line endings; accept the same text under any checkout."""
    raw = path.read_bytes()
    unix = raw.replace(b"\r\n", b"\n")
    windows = unix.replace(b"\n", b"\r\n")
    accepted = {hashlib.sha256(variant).hexdigest() for variant in (raw, unix, windows)}
    assert recorded in accepted, f"{path.name} no longer matches its recorded content"


def assert_preserved(preserved):
    current = json.loads((STATIC / CURRENT_PIN).read_text(encoding="utf-8"))["preservedVideos"]
    for name, recorded in preserved.items():
        accepted = {recorded}
        if name == REPLACED_AFTER_RECORDING:
            accepted.add(current[name])
        assert digest(STATIC / name) in accepted, f"{name} no longer matches its recorded bytes"
