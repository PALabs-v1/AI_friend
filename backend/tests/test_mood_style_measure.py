"""Slice 4b, variant B: unit tests of the model-backed measure harness
(`evals/brainbench/mood_style.py`) with fake clients. No model, no network.

The harness is the instrument the approved run will use, so these tests pin the
pass rule from `prereg/slice-04b.md`: effect margin, twice the noise band, the
void rule, the placebo, the paired permutation and the quality guard.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from app.cognitive.action import _MOOD_STYLE_BRIEF
from evals.brainbench import mood_style as ms


def _jitter(*key, scale: int) -> int:
    """Deterministic pseudo-noise in [0, scale)."""
    digest = hashlib.sha256(repr(key).encode()).digest()
    return digest[0] % scale if scale else 0


def _prompts() -> list[ms.Prompt]:
    return [
        ms.Prompt(ms.SEEDS[i // ms.PROMPTS_PER_SEED], i, f"prompt {i}")
        for i in range(ms.PROMPT_COUNT)
    ]


class FakeClient:
    """Reply length as a function of the design cell. `lift` words are added at
    clamp +0.8 and removed at -0.8 when the flag is on."""

    def __init__(
        self,
        *,
        lift=0,
        base_effect=0,
        noise=3,
        parity_skew=0,
        reject_on=False,
        repeat_shift=0,
    ):
        self.lift, self.base_effect, self.noise = lift, base_effect, noise
        self.parity_skew, self.reject_on = parity_skew, reject_on
        self.repeat_shift = repeat_shift
        self.order: list[bool] = []

    async def reply(self, prompt, *, flag_on, clamp, repeat):
        self.order.append(flag_on)
        # A prompt effect shared by every cell (what the user asked decides most
        # of the length) plus a small per-reply jitter. The effect is equal for
        # each even/odd pair, so the placebo halves are comparable by design.
        words = 30 + _jitter("prompt", prompt.index // 2, scale=20)
        words += _jitter(prompt.index, flag_on, clamp, repeat, scale=self.noise)
        words += (
            self.repeat_shift * repeat * (1 if clamp > 0 else -1 if clamp < 0 else 0)
        )
        words += int(self.base_effect * clamp)  # old paths: same in both arms
        if flag_on:
            words += int(self.lift * clamp)
            if clamp == 0.0 and prompt.index % 2 == 1:
                words += self.parity_skew
        rejected = self.reject_on and flag_on and prompt.index % 7 == 0
        return ms.Reply(" ".join(["w"] * words), rejected=rejected)


def _run(client, prompts=None):
    return asyncio.run(ms.run_design(client, prompts or _prompts()))


# --- prompts ---------------------------------------------------------------------


def _fake_turns(intents_by_seed):
    return {
        seed: [
            (SimpleNamespace(text=f"s{seed}t{n}"), SimpleNamespace(intent=intent))
            for n, intent in enumerate(intents, start=1)
        ]
        for seed, intents in intents_by_seed.items()
    }


def test_select_prompts_applies_turn_and_intent_filters():
    intents = ["small_talk"] * 20 + ["express_emotion", "question"] * 12
    turns = _fake_turns({seed: intents for seed in ms.SEEDS})
    prompts = ms.select_prompts(turns)
    assert len(prompts) == ms.PROMPT_COUNT
    assert [p.index for p in prompts] == list(range(ms.PROMPT_COUNT))
    first = prompts[0]
    # turns 1..20 are skipped, 21 is express_emotion, 22 is the first qualifier
    assert first.seed == ms.SEEDS[0] and first.text == f"s{first.seed}t22"
    assert all("t2" in p.text or "t3" in p.text for p in prompts)
    assert {p.seed for p in prompts} == set(ms.SEEDS)


def test_select_prompts_refuses_a_short_seed():
    intents = ["small_talk"] * 24
    turns = _fake_turns({seed: intents for seed in ms.SEEDS})
    with pytest.raises(ValueError, match="qualifying turns"):
        ms.select_prompts(turns)


def test_halves_split_by_index_parity():
    even, odd = ms.halves(_prompts())
    assert len(even) == len(odd) == 20
    assert all(p.index % 2 == 0 for p in even) and all(p.index % 2 for p in odd)


def test_run_design_needs_exactly_forty_prompts():
    with pytest.raises(ValueError):
        asyncio.run(ms.run_design(FakeClient(), _prompts()[:39]))


# --- run order and shape ---------------------------------------------------------


def test_run_design_collects_every_cell_and_runs_flag_off_first():
    client = FakeClient()
    records = _run(client)
    assert len(records) == 2 * ms.REPEATS * 3 * ms.PROMPT_COUNT
    assert client.order == sorted(client.order)  # False... then True...
    assert ms._baseline_first(records)
    assert not ms._baseline_first(list(reversed(records)))


# --- verdicts --------------------------------------------------------------------


def test_real_lift_passes_every_rule():
    result = ms.analyze(_run(FakeClient(lift=40, noise=3)))
    assert result["status"] == "pass", result["rules"]
    assert result["effect"] >= ms.DELTA_MARGIN
    assert all(result["rules"].values())
    assert result["delta_on"]["delta"] > result["delta_off"]["delta"]
    assert abs(result["placebo_repeat_split_on"]) < 0.2  # reported, not gating


def test_no_lift_fails_on_the_margin_not_on_noise():
    result = ms.analyze(_run(FakeClient(lift=0, noise=3)))
    assert result["status"] == "fail"
    assert not result["rules"]["effect_at_least_margin"]


def test_old_path_effect_alone_does_not_pass():
    """A big clamp effect present in both arms is the old mood paths. The
    difference of differences must be about zero, so the verdict is a fail."""
    result = ms.analyze(_run(FakeClient(lift=0, base_effect=40, noise=3)))
    assert result["delta_off"]["delta"] > 0.5
    assert abs(result["effect"]) < 0.1
    assert result["status"] == "fail"


def test_reversed_lift_is_a_fail_not_a_pass():
    result = ms.analyze(_run(FakeClient(lift=-40, noise=3)))
    assert result["effect"] < 0
    assert result["status"] == "fail"


def test_noise_above_the_void_band_reads_not_measurable():
    # A drift that differs by repeat moves the headline between repeats.
    client = FakeClient(lift=40, noise=3, repeat_shift=15)
    result = ms.analyze(_run(client))
    assert max(result["delta_on"]["noise_band"], result["delta_off"]["noise_band"]) > (
        ms.NOISE_VOID
    )
    assert result["status"] == "void"
    assert result["reading"] == "not measurable at this tier"


def test_placebo_with_a_parity_skew_fails_the_placebo_rule():
    result = ms.analyze(_run(FakeClient(lift=40, noise=3, parity_skew=60)))
    assert not result["rules"]["placebo_below_noise"]
    assert result["status"] == "fail"
    assert abs(result["placebo_on"]["delta"]) > 0.5


def test_quality_guard_fails_when_flag_on_is_rejected_more():
    result = ms.analyze(_run(FakeClient(lift=40, noise=3, reject_on=True)))
    assert (
        result["quality"]["rejected_share_on"] > result["quality"]["rejected_share_off"]
    )
    assert not result["rules"]["quality_guard"]
    assert result["status"] == "fail"


def test_baseline_first_rule_is_enforced_by_analyze():
    records = _run(FakeClient(lift=40, noise=3))
    shuffled = [r for r in records if r.flag_on] + [r for r in records if not r.flag_on]
    result = ms.analyze(shuffled)
    assert not result["rules"]["baseline_first"]
    assert result["status"] == "fail"


# --- headline direction ----------------------------------------------------------


def test_headline_is_positive_when_positive_clamp_replies_are_longer():
    assert ms.headline([50.0, 60.0], [10.0, 20.0]) == 1.0
    assert ms.headline([10.0, 20.0], [50.0, 60.0]) == -1.0


# --- permutation control ---------------------------------------------------------


def test_permutation_is_deterministic_and_paired_by_block():
    records = _run(FakeClient(lift=40, noise=3))
    a = ms.permutation_control(records)
    b = ms.permutation_control(records)
    assert a == b
    assert a["blocks"] == ms.PROMPT_COUNT * ms.REPEATS
    assert a["permutations"] == 1000 and a["seed"] == 20261030
    assert a["outside"] and a["observed"] > a["null_cut"]


def test_permutation_cut_is_near_zero_when_there_is_no_effect():
    result = ms.permutation_control(_run(FakeClient(lift=0, noise=3)))
    assert abs(result["observed"]) < 0.15
    assert not result["outside"]


def test_permutation_never_moves_replies_across_prompts():
    """Each block holds the four replies of one (prompt, seed, repeat). A swap
    exchanges the on-pair and the off-pair, nothing else."""
    records = _run(FakeClient(lift=40, noise=3))
    blocks = ms._blocks(records)
    assert len(blocks) == ms.PROMPT_COUNT * ms.REPEATS
    assert ms._effect_of(blocks, [True] * len(blocks)) == pytest.approx(
        -ms._effect_of(blocks, [False] * len(blocks))
    )


def test_permutation_seed_changes_the_null():
    records = _run(FakeClient(lift=40, noise=3))
    a = ms.permutation_control(records, seed=1)
    b = ms.permutation_control(records, seed=2)
    assert a["null_cut"] != b["null_cut"]


# --- determinism and serialisation -----------------------------------------------


def test_analysis_is_a_pure_function_of_the_records():
    records = _run(FakeClient(lift=40, noise=3))
    assert ms.analyze(records) == ms.analyze(list(records))
    assert len(ms.records_to_json(records)) == len(records)


def test_at_cap_share_is_reported_only_when_tokens_are_known():
    records = _run(FakeClient(lift=40, noise=3))
    assert ms.analyze(records)["at_cap_share"] == {"off": None, "on": None}
    capped = [
        ms.Record(
            r.seed, r.index, r.flag_on, r.clamp, r.repeat, r.words, False, r.words > 55
        )
        for r in records
    ]
    share = ms.analyze(capped)["at_cap_share"]
    for flag, key in ((False, "off"), (True, "on")):
        rows = [r for r in capped if r.flag_on == flag and r.clamp != 0.0]
        expected = sum(1 for r in rows if r.at_cap) / len(rows)
        assert share[key] == pytest.approx(expected)
    assert 0.0 < share["on"] < 1.0


# --- the ActionService-backed client ---------------------------------------------


def test_action_service_client_toggles_the_flag_and_reads_the_snapshot():
    from app.config import Config

    llm = ms.RecordingLLM(reply="Okay, I hear you and I am here.")
    snapshots = []

    def snapshot_for(prompt, clamp, repeat):
        snapshots.append((prompt.index, clamp, repeat))
        return {"mood": clamp, "cortisol": 0.5, "dopamine": 0.0}

    client = ms.ActionServiceReplyClient(llm, snapshot_for)
    prompt = _prompts()[0]
    off = asyncio.run(client.reply(prompt, flag_on=False, clamp=-0.8, repeat=0))
    on = asyncio.run(client.reply(prompt, flag_on=True, clamp=-0.8, repeat=0))
    assert Config.MOOD_STYLE_DRIVE_ENABLED is False  # restored
    assert snapshots == [(0, -0.8, 0), (0, -0.8, 0)]
    assert off.text == on.text == "Okay, I hear you and I am here."
    assert not off.rejected and not on.rejected
    assert _MOOD_STYLE_BRIEF not in llm.calls[0].prompt
    assert _MOOD_STYLE_BRIEF in llm.calls[1].prompt


def test_action_service_client_flags_a_self_correction_as_rejected():
    llm = ms.RecordingLLM(
        reply=lambda i, _p: "As an AI language model I cannot." if i == 0 else "Fine."
    )
    client = ms.ActionServiceReplyClient(
        llm, lambda p, c, r: {"mood": c, "cortisol": 0.5}
    )
    reply = asyncio.run(client.reply(_prompts()[0], flag_on=True, clamp=0.8, repeat=0))
    assert reply.rejected
