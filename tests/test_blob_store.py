import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

from app.domain import initial_state
from app.store import Store


class FakeBlob:
    def __init__(self, state=None, conflict=False):
        self.state = state
        self.etag = "version-1" if state is not None else None
        self.uploads = []
        self.conflict = conflict

    def upload_blob(self, data, **options):
        self.uploads.append(options)
        if self.conflict:
            self.conflict = False
            self.state["concurrent"] = "preserved"
            self.etag = "version-2"
            raise ResourceModifiedError("Concurrent write")
        if self.etag and options.get("etag") != self.etag:
            raise ResourceExistsError("Already initialized")
        self.state = json.loads(data)
        self.etag = "version-3"


def store_with_blob(blob):
    store = Store.__new__(Store)
    store.lock = threading.RLock()
    store.path = Path("unused-local-path")
    store.blob = blob
    store._read = lambda: (copy.deepcopy(blob.state) if blob.state else initial_state(), blob.etag)
    return store


def test_blob_conflict_reloads_and_preserves_concurrent_change():
    blob = FakeBlob(initial_state(), conflict=True)
    store = store_with_blob(blob)
    store.transact(lambda state: state.update(own="saved"))
    assert blob.state["own"] == "saved"
    assert blob.state["concurrent"] == "preserved"
    assert len(blob.uploads) == 2
    assert blob.uploads[-1]["etag"] == "version-2"
    assert blob.uploads[-1]["match_condition"] == MatchConditions.IfNotModified


def test_blob_noop_does_not_generate_versions_or_writes():
    blob = FakeBlob(initial_state())
    store_with_blob(blob).transact(lambda state: None)
    assert blob.uploads == []


def test_first_snapshot_is_create_only():
    blob = FakeBlob()
    store_with_blob(blob).transact(lambda state: None)
    assert "overwrite" not in blob.uploads[0]


def test_blob_download_keeps_exact_etag():
    store = Store.__new__(Store)
    store.lock = threading.RLock()
    store.blob = SimpleNamespace(download_blob=lambda: SimpleNamespace(
        readall=lambda: b'{"schemaVersion":1}', properties=SimpleNamespace(etag="downloaded-etag"),
    ))
    state, etag = store._read()
    assert state["schemaVersion"] == 1
    assert etag == "downloaded-etag"
