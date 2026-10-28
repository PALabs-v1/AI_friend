"""Intervention operators on offline copies (V4 slice 1, D-020).

Four operators change one ledger row so a later run can show whether the row
drives behavior:

- `clamp`: set the row to a fixed value.
- `swap`: replace the row with the same row from another copy (transplant).
- `shuffle`: permute a list-valued row, keeping its values.
- `replay`: write a recorded sequence of values into the row one step at a time,
  with a callback between steps so a harness can run a turn on each.

A target is either an `OfflineCopy` (persisted columns, via the row's `sqlite`
binding) or an offline `StateService` / `AgentState` (in-process attributes, via
the row's `attr` binding). Every call returns an `InterventionRecord` and, for
copies, appends it to `interventions.jsonl` so an outcome can be linked to the
exact change. Nothing here touches live state: a `StateService` is accepted only
if its database sits inside an offline copy.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .ledger import LedgerRow, SqliteBinding, get_row
from .offline_copy import OfflineCopy, is_offline_copy

LOG_FILENAME = "interventions.jsonl"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InterventionError(RuntimeError):
    pass


@dataclass(frozen=True)
class InterventionRecord:
    op: str
    row_id: str
    target_kind: str  # "sqlite" or "attr"
    copy_id: str
    where: dict[str, Any]
    before: Any
    after: Any
    seed: int | None
    t: float


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------


def _state_of(target: Any) -> Any:
    return getattr(target, "current_state", target)


def _copy_root_of_service(target: Any) -> Path | None:
    db_path = getattr(target, "db_path", None)
    if db_path is None:
        return None
    parent = Path(db_path).parent
    return parent if is_offline_copy(parent) else None


def _kind_and_root(target: Any) -> tuple[str, Path | None, str]:
    """('sqlite' | 'attr', copy root or None, copy id)."""
    if isinstance(target, OfflineCopy):
        return "sqlite", target.root, target.copy_id
    if hasattr(target, "current_state"):  # StateService
        root = _copy_root_of_service(target)
        if root is None:
            raise InterventionError(
                "refusing to intervene on a StateService whose database is not "
                "inside an offline copy"
            )
        marker = json.loads((root / "OFFLINE_COPY.json").read_text())
        return "attr", root, marker.get("copy_id", "")
    return "attr", None, ""  # bare AgentState (unit tests, in-memory harness)


# --------------------------------------------------------------------------
# sqlite adapter
# --------------------------------------------------------------------------


def _encode(binding: SqliteBinding, value: Any) -> Any:
    if binding.encoding == "json":
        return json.dumps(value)
    if binding.encoding == "real":
        return float(value)
    if binding.encoding == "int":
        return int(value)
    return str(value)


def _decode(binding: SqliteBinding, raw: Any) -> Any:
    if raw is None:
        return None
    if binding.encoding == "json":
        return json.loads(raw)
    if binding.encoding == "real":
        return float(raw)
    if binding.encoding == "int":
        return int(raw)
    return str(raw)


def _where_clause(binding: SqliteBinding, where: Mapping[str, Any] | None):
    where = dict(where or {})
    missing = [c for c in binding.key_columns if c not in where]
    if missing:
        raise InterventionError(
            f"row needs `where` for key columns {list(binding.key_columns)}; "
            f"missing {missing}"
        )
    allowed = (*binding.key_columns, *binding.optional_key_columns)
    extra = [c for c in where if c not in allowed]
    if extra:
        raise InterventionError(f"`where` has columns not in the key: {extra}")
    for name in (binding.table, binding.column, *where):
        if not _IDENT.match(name):
            raise InterventionError(f"bad identifier {name!r}")
    if not where:
        return "", (), {}
    clause = " WHERE " + " AND ".join(f"{c} = ?" for c in where)
    return clause, tuple(where.values()), where


def _sqlite_conn(copy: OfflineCopy, binding: SqliteBinding) -> sqlite3.Connection:
    return sqlite3.connect(copy.path(binding.store))


def _sqlite_read(copy: OfflineCopy, row: LedgerRow, where) -> Any:
    b = row.sqlite
    assert b is not None
    clause, params, _ = _where_clause(b, where)
    conn = _sqlite_conn(copy, b)
    try:
        rows = conn.execute(
            f"SELECT {b.column} FROM {b.table}{clause}", params
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise InterventionError(f"{row.id}: no row matched in {b.table}")
    if len(rows) > 1:
        raise InterventionError(
            f"{row.id}: {len(rows)} rows matched in {b.table}; narrow `where`"
        )
    return _decode(b, rows[0][0])


def _sqlite_write(copy: OfflineCopy, row: LedgerRow, value: Any, where) -> None:
    b = row.sqlite
    assert b is not None
    clause, params, _ = _where_clause(b, where)
    conn = _sqlite_conn(copy, b)
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE {b.table} SET {b.column} = ?{clause}",
                (_encode(b, value), *params),
            )
            if cur.rowcount == 0:
                raise InterventionError(f"{row.id}: no row matched in {b.table}")
            if cur.rowcount > 1:
                raise InterventionError(
                    f"{row.id}: {cur.rowcount} rows matched; narrow `where`"
                )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# attr adapter
# --------------------------------------------------------------------------


def _attr_parent(state: Any, path: str):
    *head, leaf = path.split(".")
    obj = state
    for part in head:
        obj = getattr(obj, part)
    return obj, leaf


def _attr_read(target: Any, row: LedgerRow) -> Any:
    assert row.attr is not None
    obj, leaf = _attr_parent(_state_of(target), row.attr.path)
    return getattr(obj, leaf)


def _attr_write(target: Any, row: LedgerRow, value: Any) -> None:
    assert row.attr is not None
    obj, leaf = _attr_parent(_state_of(target), row.attr.path)
    setattr(obj, leaf, value)


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def read_value(target: Any, row_id: str, *, where: Mapping[str, Any] | None = None):
    row = get_row(row_id)
    kind, _, _ = _kind_and_root(target)
    if kind == "sqlite":
        if row.sqlite is None:
            raise InterventionError(f"{row_id} has no sqlite binding")
        return _sqlite_read(target, row, where)
    if row.attr is None:
        raise InterventionError(f"{row_id} has no in-process binding")
    return _attr_read(target, row)


def _write_value(target: Any, row: LedgerRow, value: Any, where) -> None:
    kind, _, _ = _kind_and_root(target)
    if kind == "sqlite":
        if row.sqlite is None:
            raise InterventionError(f"{row.id} has no sqlite binding")
        _sqlite_write(target, row, value, where)
    else:
        if row.attr is None:
            raise InterventionError(f"{row.id} has no in-process binding")
        _attr_write(target, row, value)


def _record(
    op: str,
    target: Any,
    row: LedgerRow,
    before: Any,
    after: Any,
    where,
    seed: int | None = None,
) -> InterventionRecord:
    kind, root, copy_id = _kind_and_root(target)
    rec = InterventionRecord(
        op=op,
        row_id=row.id,
        target_kind=kind,
        copy_id=copy_id,
        where=dict(where or {}),
        before=before,
        after=after,
        seed=seed,
        t=time.time(),
    )
    if root is not None:
        with (root / LOG_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), sort_keys=True, default=str) + "\n")
    return rec


# --------------------------------------------------------------------------
# operators
# --------------------------------------------------------------------------


def clamp(
    target: Any,
    row_id: str,
    value: Any,
    *,
    where: Mapping[str, Any] | None = None,
) -> InterventionRecord:
    row = get_row(row_id)
    before = read_value(target, row_id, where=where)
    _write_value(target, row, value, where)
    after = read_value(target, row_id, where=where)
    return _record("clamp", target, row, before, after, where)


def swap(
    dst: Any,
    src: Any,
    row_id: str,
    *,
    where: Mapping[str, Any] | None = None,
) -> InterventionRecord:
    """Copy the row's value from `src` into `dst` (a transplant)."""
    row = get_row(row_id)
    if _kind_and_root(dst)[0] != _kind_and_root(src)[0]:
        raise InterventionError("swap needs both targets of the same kind")
    value = read_value(src, row_id, where=where)
    before = read_value(dst, row_id, where=where)
    _write_value(dst, row, value, where)
    after = read_value(dst, row_id, where=where)
    return _record("swap", dst, row, before, after, where)


def shuffle(
    target: Any,
    row_id: str,
    *,
    seed: int,
    where: Mapping[str, Any] | None = None,
) -> InterventionRecord:
    """Permute a list-valued row with a seeded RNG; the multiset is preserved."""
    row = get_row(row_id)
    before = read_value(target, row_id, where=where)
    if not isinstance(before, list):
        raise InterventionError(f"{row_id} is not list-valued; cannot shuffle")
    after = list(before)
    random.Random(seed).shuffle(after)
    _write_value(target, row, after, where)
    return _record("shuffle", target, row, before, after, where, seed=seed)


def replay(
    target: Any,
    row_id: str,
    trajectory: Sequence[Any],
    *,
    on_step: Callable[[int, Any], None] | None = None,
    where: Mapping[str, Any] | None = None,
) -> list[InterventionRecord]:
    """Write `trajectory` into the row one value at a time (open-loop replay).

    `on_step(i, value)` runs after each write so a harness can drive a turn
    while the row follows the recorded path regardless of user input. The
    trajectory is recorded elsewhere (a state trace); this operator only plays it.
    """
    if not trajectory:
        raise InterventionError("replay needs a non-empty trajectory")
    row = get_row(row_id)
    records = []
    for i, value in enumerate(trajectory):
        before = read_value(target, row_id, where=where)
        _write_value(target, row, value, where)
        after = read_value(target, row_id, where=where)
        records.append(_record("replay", target, row, before, after, where))
        if on_step is not None:
            on_step(i, value)
    return records
