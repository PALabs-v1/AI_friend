"""Slice 4b, variant B: the model-backed measure (`prereg/slice-04b.md`).

This module is the harness and the analysis. It never builds a model client and
never reaches a network: the caller injects a `ReplyClient`, and the unit tests
inject a deterministic fake. A real run (frontier tier through local Claude
Code, Mac) is a separate, approved step; nothing here starts one.

Design (margins approved, D-064):

- 40 prompts: dev seeds 1000 to 1004, per seed the first 8 user turns after turn
  20 whose intent is in `ALLOWED_INTENTS` (`select_prompts`).
- Arms: flag off (current) and flag on (B), each at clamp +0.8 and -0.8 over all
  40 prompts, plus a placebo clamp 0.0 over the even and odd index halves. 5
  repeats. The flag-off arm is run first (baseline-first rule).
- Headline: Cliff's delta of reply word counts, positive-clamp replies against
  negative-clamp replies, per arm. Effect = `delta_on - delta_off`.
- Noise band per arm: largest pairwise gap of the per-repeat headline.
- Controls: placebo, a label permutation (seed 20261030, 1000 draws, paired
  within each (prompt, seed, repeat) block) and a quality guard.

Everything after the replies are collected (`analyze`) is deterministic.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol

from evals.brainbench.stats import cliffs_delta

SEEDS = (1000, 1001, 1002, 1003, 1004)
PROMPTS_PER_SEED = 8
MIN_TURN = 20  # prompts come from user turns numbered above this (1-based)
ALLOWED_INTENTS = frozenset(
    {"small_talk", "share_fact", "share_update", "share_plan", "question"}
)
PROMPT_COUNT = len(SEEDS) * PROMPTS_PER_SEED

CLAMP_POS = 0.8
CLAMP_NEG = -0.8
CLAMP_PLACEBO = 0.0
REPEATS = 5

DELTA_MARGIN = 0.33
NOISE_VOID = 0.165
NOISE_MULTIPLE = 2.0
PERMUTATIONS = 1000
PERMUTATION_SEED = 20261030
PERMUTATION_QUANTILE = 0.99
TOKEN_CAP = 250


@dataclass(frozen=True)
class Prompt:
    seed: int
    index: int  # 0-based position in the frozen 40-prompt list
    text: str


@dataclass(frozen=True)
class Reply:
    text: str
    # True when `_validate_partial_response` or the self-correction path
    # rejected a draft on this turn (quality guard, rule 6).
    rejected: bool = False
    # Generated tokens when the tier reports them; None when it does not.
    tokens: int | None = None


class ReplyClient(Protocol):
    """The injected model side. One call is one reply for one design cell."""

    async def reply(
        self, prompt: Prompt, *, flag_on: bool, clamp: float, repeat: int
    ) -> Reply: ...


@dataclass(frozen=True)
class Record:
    seed: int
    index: int
    flag_on: bool
    clamp: float
    repeat: int
    words: int
    rejected: bool
    at_cap: bool | None


# --------------------------------------------------------------------------
# prompt selection and the run
# --------------------------------------------------------------------------


def select_prompts(
    turns_by_seed: Mapping[int, Sequence[tuple[Any, Any]]],
) -> list[Prompt]:
    """Freeze the 40 prompts from generated lifesim panels.

    `turns_by_seed[seed]` is the seed's user turns in order as
    `(turn, annotation)` pairs; `turn.text` and `annotation.intent` are read.
    A turn qualifies when its 1-based number is above `MIN_TURN` and its intent
    is in `ALLOWED_INTENTS` (no `express_emotion`, so the user's own mood does
    not confound). The first `PROMPTS_PER_SEED` qualifying turns per seed are
    kept, in seed order. Fewer than 40 in total is an error, never a short run.
    """
    prompts: list[Prompt] = []
    for seed in SEEDS:
        taken = 0
        for number, (turn, annotation) in enumerate(turns_by_seed[seed], start=1):
            if number <= MIN_TURN or annotation.intent not in ALLOWED_INTENTS:
                continue
            prompts.append(Prompt(seed, len(prompts), str(turn.text)))
            taken += 1
            if taken == PROMPTS_PER_SEED:
                break
        if taken < PROMPTS_PER_SEED:
            raise ValueError(
                f"seed {seed} has {taken} qualifying turns, need {PROMPTS_PER_SEED}"
            )
    return prompts


def halves(prompts: Sequence[Prompt]) -> tuple[list[Prompt], list[Prompt]]:
    """Even and odd index halves, fixed by index parity before any run."""
    return (
        [p for p in prompts if p.index % 2 == 0],
        [p for p in prompts if p.index % 2 == 1],
    )


def _words(text: str) -> int:
    return len(text.split())


def _record(prompt: Prompt, flag_on: bool, clamp: float, repeat: int, r: Reply):
    at_cap = None if r.tokens is None else r.tokens >= TOKEN_CAP
    return Record(
        prompt.seed,
        prompt.index,
        flag_on,
        clamp,
        repeat,
        _words(r.text),
        bool(r.rejected),
        at_cap,
    )


async def run_design(
    client: ReplyClient, prompts: Sequence[Prompt], *, repeats: int = REPEATS
) -> list[Record]:
    """Collect every cell. Flag off is run to completion before flag on, so the
    baseline exists before the B arm is touched (pass rule 1)."""
    if len(prompts) != PROMPT_COUNT:
        raise ValueError(f"the design needs {PROMPT_COUNT} prompts, got {len(prompts)}")
    records: list[Record] = []
    for flag_on in (False, True):
        for repeat in range(repeats):
            for clamp in (CLAMP_POS, CLAMP_NEG, CLAMP_PLACEBO):
                for prompt in prompts:
                    r = await client.reply(
                        prompt, flag_on=flag_on, clamp=clamp, repeat=repeat
                    )
                    records.append(_record(prompt, flag_on, clamp, repeat, r))
    return records


# --------------------------------------------------------------------------
# analysis (pure)
# --------------------------------------------------------------------------


def _cells(records: Sequence[Record], flag_on: bool, clamp: float, repeat=None):
    return [
        r
        for r in records
        if r.flag_on == flag_on
        and r.clamp == clamp
        and (repeat is None or r.repeat == repeat)
    ]


def _words_of(rows: Sequence[Record]) -> list[float]:
    return [float(r.words) for r in sorted(rows, key=lambda r: (r.repeat, r.index))]


def headline(pos: Sequence[float], neg: Sequence[float]) -> float:
    """Cliff's delta, positive-clamp replies against negative-clamp replies.
    Positive means longer replies at +0.8."""
    return cliffs_delta(list(neg), list(pos))


def _gap(values: Sequence[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _arm_stats(records: Sequence[Record], flag_on: bool, repeats: int) -> dict:
    pos = _words_of(_cells(records, flag_on, CLAMP_POS))
    neg = _words_of(_cells(records, flag_on, CLAMP_NEG))
    per_repeat = [
        headline(
            _words_of(_cells(records, flag_on, CLAMP_POS, k)),
            _words_of(_cells(records, flag_on, CLAMP_NEG, k)),
        )
        for k in range(repeats)
    ]
    return {
        "delta": headline(pos, neg),
        "per_repeat": per_repeat,
        "noise_band": _gap(per_repeat),
    }


def _placebo(records: Sequence[Record], flag_on: bool, repeats: int) -> dict:
    """0.0 against 0.0, the even half against the odd half. Same pairing
    direction as the headline (odd as the 'positive' group)."""
    rows = _cells(records, flag_on, CLAMP_PLACEBO)
    even = [float(r.words) for r in rows if r.index % 2 == 0]
    odd = [float(r.words) for r in rows if r.index % 2 == 1]
    per_repeat = []
    for k in range(repeats):
        rk = [r for r in rows if r.repeat == k]
        per_repeat.append(
            headline(
                [float(r.words) for r in rk if r.index % 2 == 1],
                [float(r.words) for r in rk if r.index % 2 == 0],
            )
        )
    return {
        "delta": headline(odd, even),
        "per_repeat": per_repeat,
        "noise_band": _gap(per_repeat),
    }


def _placebo_repeat_split(records: Sequence[Record], flag_on: bool) -> float:
    """Reported, not gating. The prereg placebo compares two different prompt
    halves, so prompt composition shows up as a 'delta'. This compares the same
    prompts across odd and even repeats at clamp 0.0, which removes it."""
    rows = _cells(records, flag_on, CLAMP_PLACEBO)
    odd = [float(r.words) for r in rows if r.repeat % 2 == 1]
    even = [float(r.words) for r in rows if r.repeat % 2 == 0]
    return headline(odd, even)


def _share(rows: Sequence[Record], attr: str) -> float | None:
    values = [getattr(r, attr) for r in rows]
    if not values or any(v is None for v in values):
        return None
    return sum(1 for v in values if v) / len(values)


def _blocks(records: Sequence[Record]):
    """Arrays keyed by (prompt, seed, repeat): on/off x pos/neg word counts."""
    keyed: dict[tuple[int, int, int], dict[tuple[bool, float], float]] = {}
    for r in records:
        if r.clamp in (CLAMP_POS, CLAMP_NEG):
            keyed.setdefault((r.seed, r.index, r.repeat), {})[(r.flag_on, r.clamp)] = (
                float(r.words)
            )
    order = sorted(keyed)
    rows = []
    for key in order:
        cell = keyed[key]
        rows.append(
            (
                cell[(True, CLAMP_POS)],
                cell[(True, CLAMP_NEG)],
                cell[(False, CLAMP_POS)],
                cell[(False, CLAMP_NEG)],
            )
        )
    return rows


def _effect_of(blocks: Sequence[tuple[float, float, float, float]], swaps) -> float:
    on_pos, on_neg, off_pos, off_neg = [], [], [], []
    for (a, b, c, d), swap in zip(blocks, swaps, strict=True):
        if swap:
            a, b, c, d = c, d, a, b
        on_pos.append(a)
        on_neg.append(b)
        off_pos.append(c)
        off_neg.append(d)
    return headline(on_pos, on_neg) - headline(off_pos, off_neg)


def permutation_control(
    records: Sequence[Record],
    *,
    permutations: int = PERMUTATIONS,
    seed: int = PERMUTATION_SEED,
    quantile: float = PERMUTATION_QUANTILE,
) -> dict:
    """Shuffle the flag-on/flag-off label inside each (prompt, seed, repeat)
    block, so the analysis stays paired by prompt. Swapping the label swaps the
    block's on-pair of replies with its off-pair. Replies are never moved
    across prompts, which would give a falsely narrow null."""
    blocks = _blocks(records)
    observed = _effect_of(blocks, [False] * len(blocks))
    rng = random.Random(seed)
    null = sorted(
        _effect_of(blocks, [rng.random() < 0.5 for _ in blocks])
        for _ in range(permutations)
    )
    # Nearest-rank quantile: the smallest value with at least `quantile` of the
    # null at or below it.
    rank = min(len(null) - 1, max(0, math.ceil(quantile * len(null)) - 1))
    cut = null[rank]
    return {
        "observed": observed,
        "null_quantile": quantile,
        "null_cut": cut,
        "permutations": permutations,
        "seed": seed,
        "blocks": len(blocks),
        "outside": observed > cut,
    }


def analyze(
    records: Sequence[Record],
    *,
    repeats: int = REPEATS,
    permutations: int = PERMUTATIONS,
) -> dict:
    on = _arm_stats(records, True, repeats)
    off = _arm_stats(records, False, repeats)
    placebo_on = _placebo(records, True, repeats)
    placebo_off = _placebo(records, False, repeats)
    effect = on["delta"] - off["delta"]
    larger_band = max(on["noise_band"], off["noise_band"])
    perm = permutation_control(records, permutations=permutations)

    guard_rows = {
        flag: [r for r in records if r.flag_on == flag and r.clamp != CLAMP_PLACEBO]
        for flag in (True, False)
    }
    rejected = {flag: _share(rows, "rejected") for flag, rows in guard_rows.items()}
    guard_band = max(
        _gap(
            [
                _share([r for r in guard_rows[flag] if r.repeat == k], "rejected")
                or 0.0
                for k in range(repeats)
            ]
        )
        for flag in (True, False)
    )
    quality_ok = rejected[True] - rejected[False] <= guard_band

    baseline_first = _baseline_first(records)
    void = on["noise_band"] > NOISE_VOID or off["noise_band"] > NOISE_VOID
    rules = {
        "baseline_first": baseline_first,
        "effect_at_least_margin": effect >= DELTA_MARGIN,
        "effect_above_twice_noise": effect > NOISE_MULTIPLE * larger_band,
        "placebo_below_noise": abs(placebo_on["delta"]) < larger_band,
        "permutation_outside": perm["outside"],
        "quality_guard": quality_ok,
    }
    if void:
        status = "void"
    elif all(rules.values()):
        status = "pass"
    else:
        status = "fail"
    return {
        "status": status,
        "reading": {
            "pass": "pass",
            "fail": "fail",
            "void": "not measurable at this tier",
        }[status],
        "effect": effect,
        "delta_on": on,
        "delta_off": off,
        "larger_noise_band": larger_band,
        "placebo_on": placebo_on,
        "placebo_off": placebo_off,
        "placebo_repeat_split_on": _placebo_repeat_split(records, True),
        "permutation": perm,
        "quality": {
            "rejected_share_on": rejected[True],
            "rejected_share_off": rejected[False],
            "noise_band": guard_band,
        },
        "at_cap_share": {
            "off": _share(
                _cells(records, False, CLAMP_POS) + _cells(records, False, CLAMP_NEG),
                "at_cap",
            ),
            "on": _share(
                _cells(records, True, CLAMP_POS) + _cells(records, True, CLAMP_NEG),
                "at_cap",
            ),
        },
        "rules": rules,
        "thresholds": {
            "delta_margin": DELTA_MARGIN,
            "noise_void": NOISE_VOID,
            "noise_multiple": NOISE_MULTIPLE,
        },
        "n_records": len(records),
    }


def _baseline_first(records: Sequence[Record]) -> bool:
    """Every flag-off record precedes every flag-on record in collection order,
    and flag off is present."""
    seen_on = False
    seen_off = False
    for r in records:
        if r.flag_on:
            seen_on = True
        else:
            if seen_on:
                return False
            seen_off = True
    return seen_off


def records_to_json(records: Sequence[Record]) -> list[dict]:
    return [asdict(r) for r in records]


# --------------------------------------------------------------------------
# recording rig (shared with gate test D9)
# --------------------------------------------------------------------------


@dataclass
class RecordedCall:
    prompt: str
    system: str | None
    options: dict[str, Any] | None
    role: Any = None


@dataclass
class RecordingLLM:
    """Stands in for the LLM client under a real `ActionService`. Records the
    prompt, system text and sampling options of every call and streams a
    scripted reply. It is deliberately not `evals.action_path.PinnedOptionsClient`,
    which overwrites all three sampling options and so hides the endocrine
    mapping from the very thing under test."""

    reply: str | Callable[[int, str], str] = "Sure, I can do that."
    calls: list[RecordedCall] = field(default_factory=list)

    async def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
        role: Any = None,
        **_kwargs: Any,
    ):
        import copy

        index = len(self.calls)
        self.calls.append(
            RecordedCall(prompt, system, copy.deepcopy(options_override), role)
        )
        text = self.reply(index, prompt) if callable(self.reply) else self.reply
        yield text


class _FixedIdentity:
    def get_persona_prompt(self, *_args: Any) -> str:
        return "You are a careful friend."


def build_chat_plan(snapshot: Mapping[str, Any], message: str, *, goal: str = "ENGAGE"):
    """A chat `ActionPlan` whose payload is filled by the real pipeline stage 7
    (`CognitivePipeline._prepare_action_payload`) from a state snapshot, so the
    rig and production agree on which snapshot field feeds which payload key."""
    from app.cognitive.decision import ActionPlan
    from app.cognitive.pipeline import CognitivePipeline

    plan = ActionPlan(
        action_type="RESPOND_CHAT",
        goal=goal,
        payload={
            "message": message,
            "emotion_state": "neutral",
            "surfaced_memories": [],
        },
    )
    CognitivePipeline._prepare_action_payload(
        SimpleNamespace(identity=_FixedIdentity()),
        plan,
        dict(snapshot),
        SimpleNamespace(metadata={}),
        "",
    )
    return plan


class ActionServiceReplyClient:
    """A `ReplyClient` that runs the real `ActionService.execute`.

    `llm` is the injected model client (a fake in unit tests; the local Claude
    Code client in an approved run). `snapshot_for(prompt, clamp, repeat)`
    returns the state snapshot of the offline mind with mood clamped to
    `clamp`. The flag is toggled with the `+mood-style` switchboard arm.
    """

    def __init__(
        self,
        llm: Any,
        snapshot_for: Callable[[Prompt, float, int], Mapping[str, Any]],
        *,
        goal: str = "ENGAGE",
    ) -> None:
        self._llm = llm
        self._snapshot_for = snapshot_for
        self._goal = goal

    async def reply(
        self, prompt: Prompt, *, flag_on: bool, clamp: float, repeat: int
    ) -> Reply:
        from app.cognitive.action import ActionService
        from evals.brainbench.switchboard import apply_arm, resolve_arm

        snapshot = self._snapshot_for(prompt, clamp, repeat)
        plan = build_chat_plan(snapshot, prompt.text, goal=self._goal)
        service = ActionService(
            llm_service=self._llm, memory_store=None, self_knowledge=None
        )
        arm = apply_arm(resolve_arm("+mood-style")) if flag_on else nullcontext()
        parts: list[str] = []
        rejected = False
        with arm:
            async for chunk in service.execute(plan):
                if chunk.get("type") == "content":
                    parts.append(str(chunk.get("data", "")))
                elif chunk.get("type") == "self_correction":
                    rejected = True
        return Reply("".join(parts).strip(), rejected=rejected)
