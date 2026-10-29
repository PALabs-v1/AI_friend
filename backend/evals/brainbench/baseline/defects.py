"""No-LLM defect baselines for Brain V4 (slice 2a, `prereg/slice-02.md`).

The mood and integrity baselines drive a real `CognitiveService` built by the
BrainBench `architecture_only` adapter (production persona, reappraisal weights,
theory of mind, System 2 hook with a null model), one `process_event` per turn.
The static baselines (behavioral signal, baseline-PAD writers, subscriber
counts) read the source. Nothing here calls a model, the network or the wall
clock, so two runs must agree exactly; `collect()` is what the instrument check
compares.

Run: `python -m evals.brainbench.baseline.defects --out DIR` (from `backend/`).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import random
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.clock import ManualClock, use_clock
from app.config import Config
from app.persona.profile import IMMUTABLE_CORE
from evals.brainbench.adapters import BrainBenchService, build_cognitive_service

BACKEND = Path(__file__).resolve().parents[3]
APP = BACKEND / "app"
CRATES = BACKEND / "crates"

SEED = 20261029
ROUND = 9
START_MOODS = (0.0, 0.5)
CLOCK_START = datetime(2026, 1, 1, 9, 0, 0)
TURN_GAP = timedelta(seconds=60)
BACKGROUND_TIMEOUT = 10.0

MOOD_TEXTS = (
    "Thank you so much, you really helped me and I love talking with you.",
    "You are useless and I hate you, shut up you stupid bot.",
    "The meeting is at three on Thursday.",
    "I just lost my job and I feel terrible about everything.",
    "I got the promotion today and I am so happy!",
    "Can you remind me what we said about the trip?",
    "Nobody ever listens to me and I am so tired of it.",
    "That joke made me laugh for ten minutes.",
    "I do not want to talk about it.",
    "My sister is coming to visit next week, I cannot wait.",
    "You were wrong about that and it cost me a lot.",
    "I think I am finally starting to feel better.",
)
SHUFFLE_SEEDS = (SEED, SEED + 1, SEED + 2)

HOSTILE = "You are useless and I hate you, shut up you stupid bot."
NEUTRAL = "The meeting is at three on Thursday."
SUPPORTIVE = "Thank you, that really helped me and I appreciate you a lot."
MATCHED_TURNS = 10
RECOVERY_HOSTILE_TURNS = 5
RECOVERY_SUPPORT_TURNS = 5

OUTCOME_TEXT_GRID = (-1.0, -0.5, 0.0, 0.5, 1.0)
OUTCOME_ACOUSTIC_GRID = (-0.5, 0.0, 0.5)
FIXED_BIASES = (-0.9, 0.0, 0.9)

PAD_NAMES = frozenset(
    {
        "baseline_valence",
        "baseline_arousal",
        "baseline_dominance",
        "personality_baseline_affect",
    }
)
SUBJECTS = {
    "state.subconscious": "STATE_SUBCONSCIOUS",
    "telemetry.reflection": "TELEMETRY_REFLECTION",
}
# SQL or Cypher write shapes; prose that merely says "set" or "update" is not one.
STORE_WRITE = re.compile(
    r"\bUPDATE\s+\w+\s+SET\b|\bINSERT\s+(?:OR\s+\w+\s+)?INTO\b|\bSET\s+\w+\.\w+\s*=|\bON\s+(?:CREATE|MATCH)\s+SET\b",
    re.IGNORECASE,
)


def scenario() -> dict[str, Any]:
    return {
        "seed": SEED,
        "round": ROUND,
        "start_moods": list(START_MOODS),
        "turn_gap_seconds": TURN_GAP.total_seconds(),
        "mood_texts": list(MOOD_TEXTS),
        "shuffle_seeds": list(SHUFFLE_SEEDS),
        "hostile": HOSTILE,
        "neutral": NEUTRAL,
        "supportive": SUPPORTIVE,
        "matched_turns": MATCHED_TURNS,
        "recovery_hostile_turns": RECOVERY_HOSTILE_TURNS,
        "recovery_support_turns": RECOVERY_SUPPORT_TURNS,
        "outcome_text_grid": list(OUTCOME_TEXT_GRID),
        "outcome_acoustic_grid": list(OUTCOME_ACOUSTIC_GRID),
        "fixed_biases": list(FIXED_BIASES),
        "boundaries": list(IMMUTABLE_CORE["boundaries"]),
        "adapter": "architecture_only",
    }


def canonical(data: Any) -> str:
    return json.dumps(_rounded(data), sort_keys=True, separators=(",", ":"))


def digest(data: Any) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, ROUND)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


def appraisal_backend() -> str:
    try:
        import cognitive_rust  # noqa: F401

        return "cognitive_rust"
    except ImportError:
        return "python_mirror"


class _LiveRig:
    """A real `CognitiveService` on throwaway SQLite files, turns driven with
    `process_event` under a `ManualClock`, as the BrainBench suites do."""

    def __init__(self, start_mood: float | None = None) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Memory writes try an embedding call. Point it at a closed local port so
        # the outcome (connection refused) is the same whether or not an Ollama
        # server happens to be running on the machine doing the measuring.
        self._no_ollama = patch.object(Config, "OLLAMA_URL", "http://127.0.0.1:9")
        self._no_ollama.start()
        self.service: BrainBenchService = build_cognitive_service(
            "architecture_only", Path(self._tmp.name)
        )
        self.clock = ManualClock(CLOCK_START)
        self._turn = 0
        if start_mood is not None:
            self.state.mood = start_mood

    @property
    def state(self):
        return self.service.cognitive.state.current_state

    def close(self) -> None:
        self._no_ollama.stop()
        self._tmp.cleanup()

    def appraise(self, text: str, bias: float):
        """The pipeline's own appraisal engine at a chosen bias, state untouched."""
        pipeline = self.service.cognitive.pipeline
        snapshot = self.service.cognitive.state.get_context_snapshot()
        return pipeline.appraisal.appraise(
            event_content=text,
            event_type="USER_MESSAGE",
            emotional_bias=bias,
            state_snapshot=snapshot,
            identity_boundaries=list(IMMUTABLE_CORE["boundaries"]),
        )

    async def turn(self, text: str):
        """One production turn; returns the appraisal vector the pipeline yielded."""
        self._turn += 1
        self.clock.set(CLOCK_START + TURN_GAP * self._turn)
        raw_event = {
            "id": f"baseline-{self._turn}",
            "type": "USER_MESSAGE",
            "content": text,
            "metadata": {},
        }
        with use_clock(self.clock):
            outputs = [o async for o in self.service.cognitive.process_event(raw_event)]
            errors = [o for o in outputs if o.get("type") == "error"]
            if errors:
                raise RuntimeError(f"baseline turn {self._turn} failed: {errors!r}")
            for task in (
                self.service.cognitive.pipeline._system2_task,
                self.service.cognitive.last_reflection_task,
            ):
                if task is not None and not task.done():
                    await asyncio.wait_for(task, timeout=BACKGROUND_TIMEOUT)
        vectors = [o["data"] for o in outputs if o.get("type") == "appraisal"]
        if len(vectors) != 1:
            raise RuntimeError(f"expected one appraisal per turn, got {len(vectors)}")
        return vectors[0]


async def mood_self_feed() -> dict[str, Any]:
    runs: dict[str, Any] = {}
    for start in START_MOODS:
        orders: dict[str, list[int]] = {"real": list(range(len(MOOD_TEXTS)))}
        for s in SHUFFLE_SEEDS:
            order = list(range(len(MOOD_TEXTS)))
            random.Random(s).shuffle(order)
            orders[f"shuffled_{s}"] = order
        per_order: dict[str, Any] = {}
        for name, order in orders.items():
            rig = _LiveRig(start_mood=start)
            try:
                trajectory = [rig.state.mood]
                congruence = []
                for i in order:
                    vector = await rig.turn(MOOD_TEXTS[i])
                    congruence.append(vector.goal_congruence)
                    trajectory.append(rig.state.mood)
            finally:
                rig.close()
            per_order[name] = {
                "text_order": order,
                "mood_trajectory": trajectory,
                "goal_congruence_returned": congruence,
                "mood_ratio_per_turn": [
                    trajectory[i + 1] / trajectory[i]
                    for i in range(len(trajectory) - 1)
                    if trajectory[i] != 0.0
                ],
            }
        real = per_order["real"]["mood_trajectory"]
        gaps = {
            name: max(
                abs(a - b) for a, b in zip(real, run["mood_trajectory"], strict=True)
            )
            for name, run in per_order.items()
            if name != "real"
        }
        runs[f"start_mood_{start}"] = {
            "orders": per_order,
            "max_abs_gap_real_vs_each_shuffled": gaps,
            "max_abs_gap_real_vs_shuffled": max(gaps.values()),
        }

    # Same state, every scripted text, three fixed biases: if the text carried the
    # appraisal, goal congruence would differ by text at a fixed bias.
    rig = _LiveRig()
    try:
        by_bias = {
            str(b): [rig.appraise(t, bias=b).goal_congruence for t in MOOD_TEXTS]
            for b in FIXED_BIASES
        }
    finally:
        rig.close()
    return {
        "runs": runs,
        "goal_congruence_by_text_at_fixed_bias": by_bias,
        "distinct_goal_congruence_values_per_bias": {
            k: len(set(v)) for k, v in by_bias.items()
        },
        "scope": (
            "System 1 (heuristic appraisal) with text-only input and a null model; "
            "System 2 LLM appraisal, sensory perception and voice cues are not exercised."
        ),
    }


async def integrity_on_hostile_text() -> dict[str, Any]:
    async def run(texts: list[str]) -> list[dict[str, Any]]:
        rig = _LiveRig()
        try:
            s = rig.state
            rows: list[dict[str, Any]] = [
                {
                    "norm_alignment": None,
                    "trust_integrity": s.trust_integrity,
                    "trust_benevolence": s.trust_benevolence,
                    "trust_competence": s.trust_competence,
                }
            ]
            for text in texts:
                vector = await rig.turn(text)
                rows.append(
                    {
                        "norm_alignment": vector.norm_alignment,
                        "trust_integrity": s.trust_integrity,
                        "trust_benevolence": s.trust_benevolence,
                        "trust_competence": s.trust_competence,
                    }
                )
            return rows
        finally:
            rig.close()

    return {
        "hostile": await run([HOSTILE] * MATCHED_TURNS),
        "neutral": await run([NEUTRAL] * MATCHED_TURNS),
        "supportive": await run([SUPPORTIVE] * MATCHED_TURNS),
        "recovery_hostile_then_supportive": await run(
            [HOSTILE] * RECOVERY_HOSTILE_TURNS + [SUPPORTIVE] * RECOVERY_SUPPORT_TURNS
        ),
    }


def behavioral_signal() -> dict[str, Any]:
    pipeline = ast.parse((APP / "cognitive" / "pipeline.py").read_text("utf-8"))
    passed: list[Any] = []
    for node in ast.walk(pipeline):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "evaluate_outcome"
        ):
            for kw in node.keywords:
                if kw.arg == "behavioral_signal":
                    passed.append(
                        kw.value.value
                        if isinstance(kw.value, ast.Constant)
                        else "non-constant expression"
                    )
    source = (APP / "cognitive" / "reappraisal.py").read_text("utf-8")
    m = re.search(
        r"actual_outcome = \(\s*([0-9.]+) \* actual_text_valence\s*\+ ([0-9.]+) \* "
        r"acoustic_delta\s*\+ ([0-9.]+) \* behavioral_signal",
        source,
    )
    if not m:
        raise RuntimeError(
            "outcome formula in reappraisal.py changed; update the baseline"
        )
    w_text, w_acoustic, w_behavioral = (float(g) for g in m.groups())
    values = {v for v in passed if isinstance(v, (int, float))}
    b = next(iter(values)) if len(values) == 1 else None
    totals, terms = [], []
    for text in OUTCOME_TEXT_GRID:
        for acoustic in OUTCOME_ACOUSTIC_GRID:
            term = w_behavioral * (b if b is not None else 0.0)
            totals.append(w_text * text + w_acoustic * acoustic + term)
            terms.append(term)
    return {
        "values_passed_at_call_sites": passed,
        "weights": {
            "text": w_text,
            "acoustic": w_acoustic,
            "behavioral": w_behavioral,
        },
        "outcome_variance_over_grid": _var(totals),
        "behavioral_term_variance_over_grid": _var(terms),
        "note": (
            "the behavioral share of variance is zero for any constant argument; "
            "the finding is the literal at the call site"
        ),
    }


def _var(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        raw = list(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        raw = [node.target]
    else:
        return []
    flat: list[ast.expr] = []
    while raw:
        t = raw.pop()
        if isinstance(t, (ast.Tuple, ast.List)):
            raw.extend(t.elts)
        elif isinstance(t, ast.Starred):
            raw.append(t.value)
        else:
            flat.append(t)
    return flat


def pad_write_sites(tree: ast.AST, rel: str) -> dict[str, set[str]]:
    """Writes to the baseline PAD in one parsed file, as `file::function`:
    attribute assignments (incl. tuple unpacking), `setattr` with a literal name,
    SQL or Cypher write strings naming a PAD column, and constructor or
    `replace` keyword seeds."""
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def owner(node: ast.AST) -> str:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    out: dict[str, set[str]] = {
        "assignment": set(),
        "seed": set(),
        "store_string": set(),
    }
    for node in ast.walk(tree):
        for t in _targets(node):
            if isinstance(t, ast.Attribute) and t.attr in PAD_NAMES:
                out["assignment"].add(f"{rel}::{owner(node)}")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in PAD_NAMES:
                    out["seed"].add(f"{rel}::{owner(node)}")
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in PAD_NAMES
            ):
                out["assignment"].add(f"{rel}::{owner(node)}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(p in node.value for p in PAD_NAMES)
            and STORE_WRITE.search(node.value)
        ):
            out["store_string"].add(f"{rel}::{owner(node)}")
    return out


def baseline_pad_writers() -> dict[str, Any]:
    merged: dict[str, set[str]] = {
        "assignment": set(),
        "seed": set(),
        "store_string": set(),
    }
    for path in sorted(APP.rglob("*.py")):
        found = pad_write_sites(
            ast.parse(path.read_text("utf-8")), str(path.relative_to(BACKEND))
        )
        for k, v in found.items():
            merged[k] |= v
    return {
        "assignment_sites": sorted(merged["assignment"]),
        "constructor_seed_sites": sorted(merged["seed"]),
        "store_write_string_sites": sorted(merged["store_string"]),
    }


def _nats_matches(pattern: str, subject: str) -> bool:
    p, s = pattern.split("."), subject.split(".")
    for i, token in enumerate(p):
        if token == ">":
            return len(s) > i
        if i >= len(s) or (token != "*" and token != s[i]):
            return False
    return len(p) == len(s)


def subject_roles(tree: ast.AST, subject: str, const: str) -> dict[str, bool]:
    """Whether one parsed file publishes or subscribes `subject`: direct constant,
    literal string, a name assigned from either, or a NATS wildcard pattern that
    matches the subject."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _mentions(
            node.value, subject, const, set()
        ):
            aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    roles = {"publishes": False, "subscribes": False}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", "")
        )
        if "subscribe" in name and _mentions(
            node, subject, const, aliases, wildcard=True
        ):
            roles["subscribes"] = True
        elif "publish" in name and _mentions(node, subject, const, aliases):
            roles["publishes"] = True
    return roles


def _mentions(
    node: ast.AST, subject: str, const: str, aliases: set[str], wildcard: bool = False
) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == const:
            return True
        if isinstance(n, ast.Name) and n.id in aliases:
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value == subject:
                return True
            if (
                wildcard
                and ("*" in n.value or ">" in n.value)
                and _nats_matches(n.value, subject)
            ):
                return True
    return False


def subscriber_counts() -> dict[str, Any]:
    trees = {
        str(p.relative_to(BACKEND)): ast.parse(p.read_text("utf-8"))
        for p in sorted(APP.rglob("*.py"))
    }
    rust = {
        str(p.relative_to(BACKEND)): p.read_text("utf-8", errors="replace")
        for p in sorted(CRATES.rglob("*.rs"))
        if not ("contracts" in p.parts and p.name == "lib.rs")
    }
    out: dict[str, Any] = {}
    for subject, const in SUBJECTS.items():
        roles = {
            rel: subject_roles(tree, subject, const) for rel, tree in trees.items()
        }
        out[subject] = {
            "python_publishers": sorted(r for r, v in roles.items() if v["publishes"]),
            "python_subscribers": sorted(
                r for r, v in roles.items() if v["subscribes"]
            ),
            "rust_files_referencing_constant": sorted(
                rel for rel, text in rust.items() if const in text
            ),
            "scan_scope": "backend/app (Python AST) and backend/crates (text); not frontend or scripts",
        }
    return out


async def collect() -> dict[str, Any]:
    return {
        "scenario": scenario(),
        "mood_self_feed": await mood_self_feed(),
        "integrity_on_hostile_text": await integrity_on_hostile_text(),
        "behavioral_signal": behavioral_signal(),
        "baseline_pad_writers": baseline_pad_writers(),
        "subscriber_counts": subscriber_counts(),
    }


def envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "appraisal_backend": appraisal_backend(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "scenario_sha256": digest(data["scenario"]),
        "result_sha256": digest(data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision", default="")
    args = parser.parse_args()
    data = asyncio.run(collect())
    env = envelope(data)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {"envelope": {**env, "revision": args.revision}, "data": _rounded(data)}
    (args.out / "defect_baselines.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(env, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
