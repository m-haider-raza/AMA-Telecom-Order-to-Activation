"""Test-only local issuer/process harness. Never packaged in the deployed image."""

import argparse
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from app.isolated.contracts import propose
from app.isolated.identity import EntraVerifier


def run(args):
    if args.mode == "boundary":
        import uvicorn
        from app.isolated.api import app
        key = load_pem_public_key(Path(args.key).read_bytes())
        original = EntraVerifier.from_env.__func__

        def local_verifier(cls):
            verifier = original(cls)
            verifier.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key))
            return verifier
        EntraVerifier.from_env = classmethod(local_verifier)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        return
    headers = {"Authorization": "Bearer " + Path(args.token).read_text()}
    with httpx.Client(base_url=args.url, timeout=10) as client:
        identity = client.get("/internal/identity", headers=headers)
        identity.raise_for_status()
        assert identity.json()["agent"] == args.agent
        spoof = client.get("/internal/identity", headers={**headers, "X-Agent-Name": "provisioning"})
        assert spoof.status_code == 403
        for _ in range(180):
            if args.agent == "supervisor":
                response = client.post("/internal/dispatch", headers=headers)
            else:
                response = client.get("/internal/work", headers=headers)
                response.raise_for_status()
                job = response.json()
                if job:
                    response = client.post("/internal/submit", headers=headers, json={
                        "eventId": job["eventId"], "operation": job["operation"], "revision": job["revision"],
                        "proposal": propose(args.agent, job["facts"])})
            if response.status_code not in (200, 403):
                response.raise_for_status()
            time.sleep(.1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["boundary", "worker"])
    parser.add_argument("--key")
    parser.add_argument("--port", type=int)
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--agent")
    run(parser.parse_args())
