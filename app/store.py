"""Atomic local snapshots or Azure Blob optimistic-concurrency snapshots."""

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.domain import initial_state

T = TypeVar("T")
log = logging.getLogger(__name__)


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.path = Path(os.getenv("LOCAL_STATE_PATH", "data/state.json"))
        self.blob = None
        endpoint = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        if endpoint:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            self.credential = DefaultAzureCredential()
            self.client = BlobServiceClient(endpoint, credential=self.credential)
            self.blob = self.client.get_blob_client("orders", "state.json")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        if self.blob:
            from azure.core.exceptions import ResourceNotFoundError

            try:
                download = self.blob.download_blob()
                return json.loads(download.readall()), download.properties.etag
            except ResourceNotFoundError:
                return initial_state(), None
        if not self.path.exists():
            return initial_state(), None
        return json.loads(self.path.read_text(encoding="utf-8")), None

    def read(self):
        with self.lock:
            state, _ = self._read()
            return copy.deepcopy(state)

    def transact(self, fn: Callable[[dict], T]) -> T:
        with self.lock:
            for attempt in range(8):
                state, etag = self._read()
                before = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                result = fn(state)
                encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if encoded == before and (etag is not None or (not self.blob and self.path.exists())):
                    return copy.deepcopy(result)
                if self.blob:
                    from azure.core import MatchConditions
                    from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
                    from azure.storage.blob import ContentSettings

                    try:
                        options = {"overwrite": True, "etag": etag,
                                   "match_condition": MatchConditions.IfNotModified} if etag else {}
                        self.blob.upload_blob(
                            encoded, content_settings=ContentSettings(content_type="application/json"),
                            **options,
                        )
                    except (ResourceModifiedError, ResourceExistsError):
                        log.warning("Snapshot conflict; retrying transaction, attempt=%s", attempt + 1)
                        time.sleep(0.02 * (attempt + 1))
                        continue
                else:
                    temporary = self.path.with_suffix(".tmp")
                    with temporary.open("wb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    temporary.replace(self.path)
                return copy.deepcopy(result)
        raise RuntimeError("Snapshot contention exceeded retry limit; operation was not saved")

    def close(self):
        if self.blob:
            self.client.close()
            self.credential.close()


def legacy_state():
    """What the Blob or file store holds, used once to seed Azure SQL. A brand-new install starts empty."""
    local = Path(os.getenv("LOCAL_STATE_PATH", "data/state.json"))
    if not os.getenv("AZURE_STORAGE_ACCOUNT_URL") and not local.exists():
        return initial_state()
    store = Store()
    try:
        return store._read()[0]
    finally:
        store.close()


def open_store():
    """Azure SQL when it is configured, otherwise the Blob or file snapshot store."""
    server, database = os.getenv("AMA_SQL_SERVER"), os.getenv("AMA_SQL_DATABASE")
    if bool(server) != bool(database):
        raise RuntimeError("Set both AMA_SQL_SERVER and AMA_SQL_DATABASE to use Azure SQL, or neither")
    if server:
        from app.sqlstore import SqlStore

        return SqlStore.from_env(legacy=legacy_state)
    return Store()
