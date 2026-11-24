"""Owner-run, one-time snapshot copy; never exposed as a web endpoint."""

import argparse
import copy
import hashlib
import json
import os
import zlib
from pathlib import Path

from app.domain import audit, verify_audit
from app.isolated.boundary import eligible
from app.isolated.store import BlobStore


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def validate(snapshot):
    if not verify_audit(snapshot):
        raise ValueError("Source audit chain does not verify")
    for key in ("orders", "catalogue", "inventory", "events", "idempotency", "isolatedJobs"):
        if key not in snapshot:
            raise ValueError("A complete isolated snapshot is required")
    if snapshot.get("benchmark", {}).get("run", {}).get("status") == "running":
        raise ValueError("Wait for the source benchmark to finish before copying")
    if any(event["status"] == "queued" and eligible(snapshot["orders"][event["orderId"]])
           for event in snapshot["events"]):
        raise ValueError("Wait for in-flight source orders to settle before copying")


def import_snapshot(store, snapshot, source, expected_target):
    validate(snapshot)
    source_digest = digest(snapshot)

    def replace(current):
        existing = current.get("isolatedCopy")
        if existing and existing["sourceDigest"] == source_digest and existing["source"] == source:
            return existing
        if digest(current) != expected_target:
            raise ValueError("Destination changed; refusing to overwrite its records")
        current.clear()
        current.update(copy.deepcopy(snapshot))
        current["isolatedCopy"] = {"source": source, "sourceDigest": source_digest,
                                   "sourceAuditHead": snapshot["audit"][-1]["hash"] if snapshot["audit"] else None,
                                   "orders": len(snapshot["orders"]),
                                   "customers": len(snapshot.get("customers", {})),
                                   "partners": len(snapshot.get("partners", {}))}
        audit(current, "STATE-COPY", "deployment-owner", "state.copied",
              json.dumps(current["isolatedCopy"], sort_keys=True))
        return current["isolatedCopy"]

    return store.transact(replace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-target", required=True)
    args = parser.parse_args()
    encoded = args.file.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != args.sha256:
        raise ValueError("Transferred snapshot checksum mismatch")
    snapshot = json.loads(zlib.decompress(encoded))
    store = BlobStore(os.environ["ISOLATED_STORAGE_URL"], "boundary", os.environ["AZURE_CLIENT_ID"])
    try:
        result = import_snapshot(store, snapshot, args.source, args.expected_target)
        if not verify_audit(store.read()):
            raise RuntimeError("Imported audit chain does not verify")
        args.file.unlink()
        print("RESULT " + json.dumps(result))
    finally:
        store.close()


if __name__ == "__main__":
    main()
