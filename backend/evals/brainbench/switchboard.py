"""Validated Config overrides for BrainBench ablation arms."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from app.config import AppSettings, Config


@dataclass(frozen=True)
class Arm:
    name: str
    overrides: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    workstream: str | None = None


class ArmUnavailable(ValueError):
    pass


ARMS: dict[str, Arm] = {
    "baseline": Arm("baseline", {}, "V2 as shipped"),
    "v1-ranker": Arm(
        "v1-ranker", {"MEMORY_RANKING_POLICY": "actr_v1"}, "Rollback ranker"
    ),
    "-memory-truth": Arm(
        "-memory-truth", {"MEMORY_TRUTH_ENABLED": False}, "Disable memory truth"
    ),
    "-affect-control": Arm(
        "-affect-control", {"AFFECT_CONTROL_ENABLED": False}, "Disable affect control"
    ),
    "-reappraisal": Arm(
        "-reappraisal", {"REAPPRAISAL_ENABLED": False}, "Disable reappraisal"
    ),
    "+reappraisal-weight-learning": Arm(
        "+reappraisal-weight-learning",
        {"REAPPRAISAL_WEIGHT_LEARNING_ENABLED": True},
        "Enable V1 weight learning",
    ),
    "+temporal": Arm(
        "+temporal",
        {"MEMORY_TEMPORAL_TRUTH_ENABLED": True},
        "Enable temporal truth",
        "W1",
    ),
    "+affect-input": Arm(
        "+affect-input",
        {"AFFECT_USER_INPUT_ENABLED": True},
        "Enable user affect input",
        "W2",
    ),
    "+trust-model": Arm(
        "+trust-model",
        {"TRUST_EVIDENCE_MODEL_ENABLED": True},
        "Enable trust evidence model",
        "W3",
    ),
    "+graph": Arm(
        "+graph", {"MEMORY_GRAPH_PPR_ENABLED": True}, "Enable graph retrieval", "W6"
    ),
    "+consolidation": Arm(
        "+consolidation",
        {"MEMORY_SEMANTIC_CONSOLIDATION_ENABLED": True},
        "Enable semantic consolidation",
        "W7",
    ),
    "all": Arm("all", {}, "All planned V3 mechanisms"),
}


def _field_info():
    return AppSettings.model_fields


def _unavailable(arm: Arm) -> list[str]:
    missing: list[str] = []
    fields = _field_info()
    for key, value in arm.overrides.items():
        info = fields.get(key)
        if info is None:
            missing.append(key)
            continue
        try:
            TypeAdapter(info.annotation).validate_python(value, strict=True)
        except ValidationError:
            missing.append(f"{key} (invalid value {value!r})")
    return missing


def is_runnable(arm: Arm) -> bool:
    if arm.name == "all":
        arm = _derived_all()
    return not _unavailable(arm)


def _derived_all() -> Arm:
    merged: dict[str, Any] = {}
    for name, candidate in ARMS.items():
        if not name.startswith("+") or name == "+reappraisal-weight-learning":
            continue
        for key, value in candidate.overrides.items():
            if key in merged and merged[key] != value:
                raise ValueError(
                    f"conflicting overrides for {key}: {merged[key]!r} and {value!r}"
                )
            merged[key] = value
    return Arm("all", merged, "All planned V3 mechanisms")


def resolve_arm(name: str) -> Arm:
    if name == "all":
        arm = _derived_all()
    else:
        try:
            arm = ARMS[name]
        except KeyError as exc:
            raise ValueError(
                f"unknown arm {name!r}; known arms: {', '.join(ARMS)}"
            ) from exc
    missing = _unavailable(arm)
    if missing:
        detail = ", ".join(missing)
        workstreams = sorted(
            {
                candidate.workstream
                for candidate in ARMS.values()
                if candidate.workstream
                and any(key in item for item in missing for key in candidate.overrides)
            }
        )
        stream = (
            f" (workstream {arm.workstream})"
            if arm.workstream
            else f" (workstreams {', '.join(workstreams)})"
            if workstreams
            else ""
        )
        raise ArmUnavailable(
            f"arm {arm.name!r} unavailable{stream}; missing/invalid Config fields: {detail}"
        )
    return arm


@contextmanager
def apply_arm(arm: Arm) -> Iterator[None]:
    missing = _unavailable(arm)
    if missing:
        workstreams = sorted(
            {
                candidate.workstream
                for candidate in ARMS.values()
                if candidate.workstream
                and any(key in item for item in missing for key in candidate.overrides)
            }
        )
        stream = (
            f" (workstream {arm.workstream})"
            if arm.workstream
            else f" (workstreams {', '.join(workstreams)})"
            if workstreams
            else ""
        )
        raise ArmUnavailable(
            f"arm {arm.name!r} unavailable{stream}; missing/invalid Config fields: {', '.join(missing)}"
        )
    with patch.multiple(Config, **arm.overrides) if arm.overrides else nullcontext():
        yield


def arm_status() -> list[dict[str, Any]]:
    rows = []
    for arm in ARMS.values():
        if arm.name == "all":
            continue
        missing = _unavailable(arm)
        rows.append(
            {
                "name": arm.name,
                "overrides": arm.overrides,
                "description": arm.description,
                "workstream": arm.workstream,
                "runnable": not missing,
                "missing": missing,
            }
        )
    all_arm = _derived_all()
    missing = _unavailable(all_arm)
    rows.append(
        {
            "name": all_arm.name,
            "overrides": all_arm.overrides,
            "description": all_arm.description,
            "workstream": None,
            "runnable": not missing,
            "missing": missing,
        }
    )
    return rows


ARMS["all"] = _derived_all()
