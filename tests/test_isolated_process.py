import json
import os
import socket
import subprocess
import sys
import time

import httpx
from cryptography.hazmat.primitives import serialization

from app.domain import create_order, verify_audit
from app.isolated.store import LocalStore
from tests.test_domain import payload
from tests.test_isolated import AUDIENCE, BINDINGS, KEY, TENANT, token


def test_six_independent_http_processes_drive_durable_boundary(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key_path = tmp_path / "synthetic-public.pem"
    key_path.write_bytes(KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo))
    path = tmp_path / "process.sqlite"
    store = LocalStore(path)
    store.transact(lambda state: state.update(orders={}, events=[], audit=[], idempotency={}))
    order = store.transact(lambda state: create_order(state, payload("auto-recovery"), "service-manager"))
    environment = {**os.environ, "ISOLATED_TENANT_ID": TENANT, "ISOLATED_AUDIENCE": AUDIENCE,
                   "ISOLATED_PRINCIPALS": json.dumps(BINDINGS), "ISOLATED_LOCAL": "1",
                   "ISOLATED_LOCAL_PATH": str(path), "DEMO_ADMIN_TOKEN": "new-local-admin-" + "a" * 30,
                   "DEMO_PARTNER_TOKEN": "new-local-partner-" + "p" * 30,
                   "AMA_MODE": "demo", "AMA_DELIVERY": "simulated", "AMA_STAFF_AUTH": "token"}
    for forbidden in ("AMA_SQL_SERVER", "AMA_SQL_DATABASE", "AZURE_STORAGE_ACCOUNT_URL", "AMA_SQL_ADMIN_TOKEN",
                      "ISOLATED_STORAGE_URL", "APPLICATIONINSIGHTS_CONNECTION_STRING"):
        environment.pop(forbidden, None)
    processes = []
    logs = []
    try:
        output = (tmp_path / "boundary.log").open("w")
        logs.append(output)
        processes.append(subprocess.Popen([sys.executable, "-m", "tests.isolated_process", "boundary",
                                            "--key", str(key_path), "--port", str(port)],
                                           env=environment, stdout=output, stderr=output))
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(url + "/isolated-health").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            assert processes[0].poll() is None, "Local boundary exited; inspect boundary.log"
            assert time.monotonic() < deadline, "Local boundary failed to start; inspect boundary.log"
            time.sleep(.1)
        for agent in BINDINGS:
            credential = tmp_path / f"synthetic-{agent}.token"
            credential.write_text(token(agent))
            output = (tmp_path / f"{agent}.log").open("w")
            logs.append(output)
            processes.append(subprocess.Popen([
                sys.executable, "-m", "tests.isolated_process", "worker", "--agent", agent,
                "--url", url, "--token", str(credential)], env=environment, stdout=output, stderr=output))
        deadline = time.monotonic() + 60
        while store.read()["orders"][order["id"]]["state"] != "completed":
            assert time.monotonic() < deadline, "Independent workers did not complete the order"
            assert all(process.poll() is None for process in processes), "A runtime exited unexpectedly"
            time.sleep(.1)
        result = store.read()
        assert result["orders"][order["id"]]["recoveryAttempts"] == 1
        assert verify_audit(result)
        commits = [json.loads(row["detail"]) for row in result["audit"] if row["action"] == "isolated.commit"]
        assert {row["principalId"] for row in commits} == {BINDINGS[a]["oid"] for a in BINDINGS if a != "supervisor"}
        assert httpx.get(url + "/health").status_code == 200
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        for output in logs:
            output.close()
