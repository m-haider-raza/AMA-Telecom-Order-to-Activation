"""Copy a complete, settled Foundry snapshot to a paused new app; print counts, never records."""

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from . import Deploy_Isolated as deployment
except ImportError:
    import Deploy_Isolated as deployment

READ = (
    "import os,json,zlib,base64;"
    "from app.isolated.store import BlobStore;"
    "s=BlobStore(os.environ['ISOLATED_STORAGE_URL'],'boundary',os.environ['AZURE_CLIENT_ID']);"
    "b=zlib.compress(json.dumps(s.read(),sort_keys=True).encode(),9);s.close();"
    "print('BEGIN-SNAPSHOT\\n'+base64.b64encode(b).decode()+'\\nEND-SNAPSHOT')"
)


def execute(stack, command, marker):
    azure = Path(shutil.which("az"))
    python = azure.parent.parent / "python.exe"
    if not python.is_file():
        raise RuntimeError("Use the Azure CLI Python on this Windows deployment host")
    for attempt in range(4):
        response = subprocess.run(
            [str(python), "-IBm", "azure.cli", "containerapp", "exec",
             "--name", stack["boundaryName"], "--resource-group", stack["resourceGroup"],
             "--subscription", stack["subscriptionId"], "--command", command],
            capture_output=True, text=True, encoding="utf-8", timeout=240)
        body = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", response.stdout).replace("\r", "")
        if marker in body:
            return body
        if attempt == 3:
            raise RuntimeError("Container exec did not return its expected receipt; no data was printed")
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("Container exec retries exhausted")


def remote_python(stack, program, marker):
    encoded = base64.b64encode(program.encode()).decode()
    return execute(stack, f"python -c exec(__import__('base64').b64decode('{encoded}'))", marker)


def snapshot(stack):
    body = remote_python(stack, READ, "END-SNAPSHOT")
    match = re.search(r"BEGIN-SNAPSHOT\n([A-Za-z0-9+/=\n]+)\nEND-SNAPSHOT", body)
    if not match:
        raise RuntimeError("Snapshot transfer framing is incomplete")
    encoded = base64.b64decode(match[1])
    return json.loads(zlib.decompress(encoded)), encoded


def source_summary(state, encoded):
    from app.isolated.migration import digest, validate
    validate(state)
    return {"digest": digest(state), "compressedBytes": len(encoded), "orders": len(state["orders"]),
            "customers": len(state.get("customers", {})), "partners": len(state.get("partners", {})),
            "auditEntries": len(state["audit"]), "auditIntact": True}


def read_runtime(path):
    stack = json.loads(path.read_text(encoding="utf-8"))
    host = urlparse(stack["url"]).hostname
    if not host or not host.endswith(".azurecontainerapps.io"):
        raise ValueError("A deployed Container Apps origin is required")
    stack.setdefault("boundaryName", host.split(".")[0])
    if (not re.fullmatch(r"amai-boundary-[a-z0-9]+", stack["boundaryName"])
            or not re.fullmatch(r"rg-amai-foundry-runtime-[a-z0-9-]+", stack["resourceGroup"])):
        raise ValueError("Only scoped Foundry runtime receipts are permitted")
    return stack


def run(args):
    source = read_runtime(args.source_runtime)
    state, encoded = snapshot(source)
    summary = source_summary(state, encoded)
    if args.inspect_source:
        print(json.dumps(summary))
        return
    if not args.target_runtime or not args.output:
        raise ValueError("A new target receipt and private output path are required")
    target = read_runtime(args.target_runtime)
    if (source["resourceGroup"] == target["resourceGroup"] or source["storageUrl"] == target["storageUrl"]
            or any(source[key] != target[key] for key in ("tenantId", "subscriptionId"))
            or not target["resourceGroup"].startswith("rg-amai-foundry-runtime-")
            or target["status"] != "workers-paused-for-state-copy"):
        raise ValueError("Copy requires a distinct new paused Foundry app in the same tenant/subscription")
    apps = deployment.az("containerapp", "list", "--resource-group", target["resourceGroup"],
                         "--subscription", target["subscriptionId"], "-o", "json", json_result=True)
    workers = [app for app in apps if app["name"] != target["boundaryName"]]
    if len(workers) != 6 or not all(any(item["name"] == "ISOLATED_WORKERS_PAUSED" and item.get("value") == "1"
                                      for item in app["properties"]["template"]["containers"][0]["env"])
                                   for app in workers):
        raise ValueError("All six new workers must remain paused throughout data import")
    destination, _ = snapshot(target)
    from app.isolated.migration import digest
    target_digest = digest(destination)
    temporary = "/tmp/amai-foundry-snapshot.bin"
    chunk_size = 720
    try:
        for offset in range(0, len(encoded), chunk_size):
            part = base64.b64encode(encoded[offset:offset + chunk_size]).decode()
            flags = 578 if offset == 0 else 66
            count = len(encoded[offset:offset + chunk_size])
            command = (
                f"python -c print('WROTE-'+str(__import__('os').pwrite(__import__('os').open("
                f"'{temporary}',{flags},384),__import__('base64').b64decode('{part}'),{offset})))"
            )
            execute(target, command, f"WROTE-{count}")
        sha = hashlib.sha256(encoded).hexdigest()
        command = (f"python -m app.isolated.migration --file {temporary} --sha256 {sha} "
                   f"--source {source['boundaryName']} --expected-target {target_digest}")
        result = execute(target, command, "RESULT ")
    finally:
        remote_python(target, f"from pathlib import Path;Path('{temporary}').unlink(missing_ok=True);"
                      "print('CLEANED-SNAPSHOT')", "CLEANED-SNAPSHOT")
    copied = json.loads(re.search(r"RESULT (\{[^\n]+\})", result)[1])
    after, _ = snapshot(target)
    if after["isolatedCopy"] != copied or copied["sourceDigest"] != summary["digest"]:
        raise RuntimeError("Destination did not preserve the exact source snapshot digest")
    expected = {**state, "audit": after["audit"], "isolatedCopy": copied}
    if after != expected or after["audit"][:-1] != state["audit"]:
        raise RuntimeError("Destination differs from the source beyond the explicit copy provenance entry")
    report = {"status": "copied-and-verified", "source": source["resourceGroup"],
              "target": target["resourceGroup"], "sourceReadOnly": True, **summary,
              "allSourceFieldsPreserved": True, "extraAuditEntries": 1}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--target-runtime", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inspect-source", action="store_true")
    run(parser.parse_args())
