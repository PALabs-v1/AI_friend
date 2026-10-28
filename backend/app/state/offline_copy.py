"""Offline intervention copies (V4 slice 1, D-020, D-029).

A copy is a directory of SQLite snapshots plus an `OFFLINE_COPY.json` marker.
The intervention operators in `interventions.py` act on a copy, never on live
state. Three rules keep it from going live:

- `runtime_state_db` refuses a deployment path that holds the marker.
- `OfflineCopy.open_state_service` is the sanctioned way to run code against a
  copy, and it switches Redis off so nothing is mirrored or published.
- `delete` removes the directory and appends an audit record, so "the copy was
  deleted" is a fact in a log and not a promise.

Stores are named by the caller (`{"state_cache": path, ...}`); the registry in
`ledger.py` refers to the same names.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from ..config import Config
from .runtime_paths import OFFLINE_MARKER

if TYPE_CHECKING:
    from .agent_state import StateService

AUDIT_FILENAME = "offline_copies_audit.jsonl"


def _audit(audit_path: Path, event: dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def is_offline_copy(path: str | Path) -> bool:
    return (Path(path) / OFFLINE_MARKER).is_file()


@dataclass
class OfflineCopy:
    root: Path
    copy_id: str
    audit_path: Path
    stores: dict[str, Path] = field(default_factory=dict)
    deleted: bool = False

    @classmethod
    def create(
        cls,
        sources: Mapping[str, str | Path],
        dest_parent: str | Path,
        *,
        label: str = "",
    ) -> OfflineCopy:
        """Snapshot each source SQLite file into a new copy directory.

        Uses SQLite's online backup API, which is consistent even for a
        database in WAL mode. A source that does not exist is an error: a copy
        that silently lacks a store would make an intervention look like a
        no-op.
        """
        dest_parent = Path(dest_parent)
        copy_id = uuid.uuid4().hex[:12]
        root = dest_parent / f"copy-{copy_id}"
        root.mkdir(parents=True, exist_ok=False)
        stores: dict[str, Path] = {}
        try:
            for name, src in sources.items():
                src_path = Path(src)
                if not src_path.is_file():
                    raise FileNotFoundError(f"offline copy source {name}: {src_path}")
                dst_path = root / f"{name}.db"
                src_conn, dst_conn = (
                    sqlite3.connect(src_path),
                    sqlite3.connect(dst_path),
                )
                try:
                    src_conn.backup(dst_conn)
                finally:
                    src_conn.close()
                    dst_conn.close()
                stores[name] = dst_path
            marker = {
                "copy_id": copy_id,
                "created_at": time.time(),
                "label": label,
                "stores": sorted(stores),
            }
            (root / OFFLINE_MARKER).write_text(json.dumps(marker, indent=2))
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        audit_path = dest_parent / AUDIT_FILENAME
        _audit(
            audit_path,
            {"event": "create", "copy_id": copy_id, "label": label, "t": time.time()},
        )
        return cls(root=root, copy_id=copy_id, audit_path=audit_path, stores=stores)

    def path(self, store: str) -> Path:
        self._assert_live_object()
        try:
            return self.stores[store]
        except KeyError:
            raise KeyError(f"offline copy has no store {store!r}") from None

    def open_state_service(self, **kwargs: Any) -> StateService:
        """A `StateService` bound to this copy's `state_cache`, with Redis off."""
        from .agent_state import StateService

        self._assert_live_object()
        with patch.object(Config, "REDIS_URL", ""):
            return StateService(
                graph_store=None, db_path=str(self.path("state_cache")), **kwargs
            )

    def delete(self, *, reason: str = "") -> None:
        self._assert_live_object()
        shutil.rmtree(self.root)
        self.deleted = True
        _audit(
            self.audit_path,
            {
                "event": "delete",
                "copy_id": self.copy_id,
                "reason": reason,
                "t": time.time(),
            },
        )

    def _assert_live_object(self) -> None:
        if self.deleted:
            raise RuntimeError(f"offline copy {self.copy_id} was deleted")
