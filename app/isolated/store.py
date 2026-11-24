"""Only the boundary identity may access its new container; no SQL/bootstrap/live-data path."""

import copy
import json
import logging
import sqlite3
from pathlib import Path

from app.domain import initial_state

log = logging.getLogger(__name__)


def fresh_state():
    # initial_state is exclusively project-authored synthetic fixtures.
    state = initial_state()
    state["isolatedJobs"] = {}
    return state


class LocalStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")

    def transact(self, fn):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM snapshot WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else fresh_state()
            before = json.dumps(state)
            result = fn(state)
            encoded = json.dumps(state)
            if not row or encoded != before:
                db.execute("INSERT OR REPLACE INTO snapshot VALUES (1,?)", (encoded,))
            return copy.deepcopy(result)

    def read(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute("SELECT body FROM snapshot WHERE id=1").fetchone()
        if row is None:
            return self.transact(lambda state: state)
        return json.loads(row[0])

    def close(self):
        pass


class BlobStore:
    def __init__(self, endpoint, container, client_id):
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import BlobServiceClient
        self.credential = ManagedIdentityCredential(client_id=client_id)
        self.client = BlobServiceClient(endpoint, credential=self.credential)
        self.blob = self.client.get_blob_client(container, "isolated.json")

    def transact(self, fn):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
        for attempt in range(12):
            try:
                download = self.blob.download_blob()
                state, etag = json.loads(download.readall()), download.properties.etag
            except ResourceNotFoundError:
                state, etag = fresh_state(), None
            before = json.dumps(state)
            result = fn(state)
            encoded = json.dumps(state)
            if etag is not None and before == encoded:
                return copy.deepcopy(result)
            options = {"overwrite": True, "etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {}
            try:
                self.blob.upload_blob(encoded.encode(), **options)
                return copy.deepcopy(result)
            except (ResourceExistsError, ResourceModifiedError):
                log.warning("Boundary snapshot contention attempt=%s", attempt + 1)
        raise RuntimeError("Boundary commit contention exhausted; request must be retried")

    def read(self):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return json.loads(self.blob.download_blob().readall())
        except ResourceNotFoundError:
            return self.transact(lambda state: state)

    def close(self):
        self.client.close()
        self.credential.close()
