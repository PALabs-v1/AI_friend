"""Slice 4b mood style line: unit tests of the model-backed measure harness
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
        noise=8,
        parity_skew=0,
        reject_fn=None,
        repeat_shift=0,
        repeat_skew=0,
    ):
        self.lift, self.base_effect, self.noise = lift, base_effect, noise
        self.parity_skew, self.reject_fn = parity_skew, reject_fn
        self.repeat_shift = repeat_shift
        self.repeat_skew = repeat_skew
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
            if clamp == 0.0 and repeat % 2 == 1:
                words += self.repeat_skew
        rejected = bool(
            self.reject_fn and self.reject_fn(flag_on, clamp, prompt, repeat)
        )
        return ms.Reply(" ".join(["w"] * words), rejected=rejected, tokens=words)


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
    result = ms.analyze(_run(FakeClient(lift=40, noise=8)))
    assert result["status"] == "pass", result["rules"]
    assert result["effect"] >= ms.DELTA_MARGIN
    assert all(result["rules"].values())
    assert result["delta_on"]["delta"] > result["delta_off"]["delta"]
    assert abs(result["placebo_repeat_split_on"]) < 0.2  # reported, not gating


def test_no_lift_fails_on_the_margin_not_on_noise():
    result = ms.analyze(_run(FakeClient(lift=0, noise=8)))
    assert result["status"] == "fail"
    assert not result["rules"]["effect_at_least_margin"]


def test_old_path_effect_alone_does_not_pass():
    """A big clamp effect present in both arms is the old mood paths. The
    difference of differences must be about zero, so the verdict is a fail."""
    result = ms.analyze(_run(FakeClient(lift=0, base_effect=40, noise=8)))
    assert result["delta_off"]["delta"] > 0.5
    assert abs(result["effect"]) < 0.1
    assert result["status"] == "fail"


def test_reversed_lift_is_a_fail_not_a_pass():
    result = ms.analyze(_run(FakeClient(lift=-40, noise=8)))
    assert result["effect"] < 0
    assert result["status"] == "fail"


def test_noise_above_the_void_band_reads_not_measurable():
    # A drift that differs by repeat moves the headline between repeats.
    client = FakeClient(lift=40, noise=8, repeat_shift=15)
    result = ms.analyze(_run(client))
    assert max(result["delta_on"]["noise_band"], result["delta_off"]["noise_band"]) > (
        ms.NOISE_VOID
    )
    assert result["status"] == "void"
    assert result["reading"] == "not measurable at this tier"


def test_prompt_halves_placebo_is_reported_and_does_not_gate():
    """Two different prompt sets differ in length for reasons that are not
    noise, so a halves skew is reported and cannot fail the run."""
    result = ms.analyze(_run(FakeClient(lift=40, noise=8, parity_skew=60)))
    assert abs(result["placebo_prompt_halves_on"]["delta"]) > 0.5
    assert abs(result["placebo_prompt_halves_off"]["delta"]) < 0.2
    assert result["rules"]["placebo_below_noise"]
    assert result["status"] == "pass"


def test_repeat_split_placebo_gates_and_fails_on_flag_on_drift():
    result = ms.analyze(_run(FakeClient(lift=40, noise=8, repeat_skew=60)))
    assert abs(result["placebo_repeat_split_on"]) > 0.5
    assert not result["rules"]["placebo_below_noise"]
    assert result["status"] == "fail"


def _rejecting(on=0, off=0, *, repeat=None):
    """A `reject_fn` rejecting the first `on` flag-on and `off` flag-off cells at
    clamp +-0.8 (400 per arm), numbered by (repeat, prompt, sign). With
    `repeat` set, the numbering stays inside that one repeat (80 cells)."""

    def cell(prompt, rep, clamp):
        base = prompt.index * 2 + (0 if clamp > 0 else 1)
        return base if repeat is not None else rep * 80 + base

    def fn(flag_on, clamp, prompt, rep):
        if clamp == 0.0 or (repeat is not None and rep != repeat):
            return False
        return cell(prompt, rep, clamp) < (on if flag_on else off)

    return fn


def _guard(on=0, off=0, **kw):
    client = FakeClient(lift=40, noise=8, reject_fn=_rejecting(on, off, **kw))
    return ms.analyze(_run(client))


def test_quality_guard_fails_on_a_five_point_rise():
    result = _guard(on=20)
    assert result["quality"]["rise"] == pytest.approx(0.05)
    assert result["quality"]["cap"] == 0.03
    assert not result["rules"]["quality_guard"]
    assert result["status"] == "fail"


def test_quality_guard_fails_on_a_five_point_rise_over_a_nonzero_baseline():
    result = _guard(on=28, off=8)  # 7% against 2%
    assert result["quality"]["rise"] == pytest.approx(0.05)
    assert not result["rules"]["quality_guard"]


def test_quality_guard_passes_a_two_point_rise():
    result = _guard(on=8)
    assert result["quality"]["rise"] == pytest.approx(0.02)
    assert result["rules"]["quality_guard"]


def test_quality_guard_passes_equal_rates_and_a_drop():
    assert _guard(on=20, off=20)["rules"]["quality_guard"]
    drop = _guard(on=4, off=20)
    assert drop["quality"]["rise"] < 0 and drop["rules"]["quality_guard"]


def test_quality_guard_passes_with_no_rejections():
    result = _guard()
    assert result["quality"]["rejected_share_on"] == 0.0
    assert result["quality"]["rejected_share_off"] == 0.0
    assert result["rules"]["quality_guard"]


def test_quality_guard_cap_is_inclusive_to_three_points_and_fails_past_it():
    assert _guard(on=12)["rules"]["quality_guard"]  # exactly 0.03
    assert _guard(on=17, off=5)["rules"]["quality_guard"]  # 0.0425 - 0.0125 = 0.03
    assert not _guard(on=13)["rules"]["quality_guard"]  # 0.0325


def test_quality_guard_is_judged_on_the_pooled_share_not_per_repeat():
    """Eight rejections, all in repeat 0, are 10% of that repeat and 2% pooled:
    pass. Twenty in repeat 0 are 25% of it and 5% pooled: fail."""
    clustered_small = _guard(on=8, repeat=0)
    assert clustered_small["quality"]["rise"] == pytest.approx(0.02)
    assert clustered_small["rules"]["quality_guard"]
    clustered_big = _guard(on=20, repeat=0)
    assert clustered_big["quality"]["rise"] == pytest.approx(0.05)
    assert not clustered_big["rules"]["quality_guard"]


def test_quality_guard_ignores_the_placebo_cells():
    def reject_placebo(flag_on, clamp, prompt, repeat):
        return flag_on and clamp == 0.0

    result = ms.analyze(_run(FakeClient(lift=40, noise=8, reject_fn=reject_placebo)))
    assert result["quality"]["rise"] == 0.0
    assert result["rules"]["quality_guard"]


def test_placebo_basis_marks_a_pass_as_conditional_on_the_corrected_placebo():
    result = ms.analyze(_run(FakeClient(lift=40, noise=8)))
    assert result["status"] == "pass"
    basis = result["placebo_basis"]
    assert "placebo_repeat_split_on" in basis["gating"]
    assert "does not gate" in basis["prereg"]
    assert "conditional" in basis["note"]


def test_baseline_first_rule_is_enforced_by_analyze():
    records = _run(FakeClient(lift=40, noise=8))
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
    records = _run(FakeClient(lift=40, noise=8))
    a = ms.permutation_control(records)
    assert a["sided"] == "one-sided, positive direction"
    b = ms.permutation_control(records)
    assert a == b
    assert a["blocks"] == ms.PROMPT_COUNT * ms.REPEATS
    assert a["permutations"] == 1000 and a["seed"] == 20261030
    assert a["outside"] and a["observed"] > a["null_cut"]


def test_permutation_cut_is_near_zero_when_there_is_no_effect():
    result = ms.permutation_control(_run(FakeClient(lift=0, noise=8)))
    assert abs(result["observed"]) < 0.15
    assert not result["outside"]


def test_permutation_never_moves_replies_across_prompts():
    """Each block holds the four replies of one (prompt, seed, repeat). A swap
    exchanges the on-pair and the off-pair, nothing else."""
    records = _run(FakeClient(lift=40, noise=8))
    blocks = ms._blocks(records)
    assert len(blocks) == ms.PROMPT_COUNT * ms.REPEATS
    assert ms._effect_of(blocks, [True] * len(blocks)) == pytest.approx(
        -ms._effect_of(blocks, [False] * len(blocks))
    )


def test_permutation_seed_changes_the_null():
    records = _run(FakeClient(lift=40, noise=8))
    a = ms.permutation_control(records, seed=1)
    b = ms.permutation_control(records, seed=2)
    assert a["null_cut"] != b["null_cut"]


# --- determinism and serialisation -----------------------------------------------


def test_analysis_is_a_pure_function_of_the_records():
    records = _run(FakeClient(lift=40, noise=8))
    assert ms.analyze(records) == ms.analyze(list(records))
    assert len(ms.records_to_json(records)) == len(records)


def test_at_cap_share_is_reported_only_when_tokens_are_known():
    records = _run(FakeClient(lift=40, noise=8))
    stripped = _without_tokens(records)
    assert ms.analyze(stripped)["at_cap_share"] == {"off": None, "on": None}
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


# --- missing token counts: status "incomplete" -----------------------------------


def _without_tokens(records, *, flag_on=None):
    """The records with `at_cap` unknown, for one arm or for both."""
    return [
        ms.Record(
            r.seed, r.index, r.flag_on, r.clamp, r.repeat, r.words, r.rejected, None
        )
        if flag_on is None or r.flag_on == flag_on
        else r
        for r in records
    ]


def test_a_run_that_would_pass_is_incomplete_without_token_counts():
    records = _run(FakeClient(lift=40, noise=8))
    complete = ms.analyze(records)
    assert complete["status"] == "pass" and complete["reason"] is None
    result = ms.analyze(_without_tokens(records))
    assert result["status"] == "incomplete"
    assert result["reason"] == ms.TOKENS_MISSING_REASON
    assert "250-token ceiling share" in result["reason"]
    assert "baseline-first rule 1" in result["reason"]
    assert result["reading"] == "incomplete run"
    # every other number is still reported, and equals the complete run's
    for key in ("effect", "delta_on", "delta_off", "permutation", "quality", "rules"):
        assert result[key] == complete[key]
    assert result["at_cap_share"] == {"off": None, "on": None}


def test_missing_tokens_in_either_arm_alone_is_incomplete():
    records = _run(FakeClient(lift=40, noise=8))
    for arm in (False, True):
        result = ms.analyze(_without_tokens(records, flag_on=arm))
        assert result["status"] == "incomplete", arm
        assert (result["at_cap_share"]["off"] is None) == (not arm)
        assert (result["at_cap_share"]["on"] is None) == arm


def test_precedence_is_void_then_incomplete_then_fail_then_pass():
    # void beats incomplete: noisy repeats and no tokens read void
    noisy = _run(FakeClient(lift=40, noise=8, repeat_shift=15))
    assert ms.analyze(noisy)["status"] == "void"
    assert ms.analyze(_without_tokens(noisy))["status"] == "void"
    # incomplete beats fail: no lift would fail on the margin, no tokens reads incomplete
    flat = _run(FakeClient(lift=0, noise=8))
    assert ms.analyze(flat)["status"] == "fail"
    assert ms.analyze(_without_tokens(flat))["status"] == "incomplete"
    # fail beats pass, and pass needs complete records
    assert ms.analyze(_run(FakeClient(lift=40, noise=8)))["status"] == "pass"


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


def _snapshot_at(prompt, clamp, repeat):
    return {"mood": clamp, "cortisol": 0.5, "dopamine": 0.0}


def test_tokens_fn_fills_reply_tokens_and_cap_flag():
    llm = ms.RecordingLLM(reply="one two three four")
    plain = ms.ActionServiceReplyClient(llm, _snapshot_at)
    counted = ms.ActionServiceReplyClient(
        llm, _snapshot_at, tokens_fn=lambda text: 250 if "four" in text else 10
    )
    prompt = _prompts()[0]
    assert (
        asyncio.run(plain.reply(prompt, flag_on=False, clamp=0.8, repeat=0)).tokens
        is None
    )
    reply = asyncio.run(counted.reply(prompt, flag_on=False, clamp=0.8, repeat=0))
    assert reply.tokens == 250
    assert ms._record(prompt, False, 0.8, 0, reply).at_cap is True
    short = ms.ActionServiceReplyClient(llm, _snapshot_at, tokens_fn=lambda t: 249)
    reply = asyncio.run(short.reply(prompt, flag_on=False, clamp=0.8, repeat=0))
    assert ms._record(prompt, False, 0.8, 0, reply).at_cap is False


def test_at_cap_share_is_produced_end_to_end_with_a_tokens_fn():
    """One repeat through the real ActionService path, tokens from the fake."""
    llm = ms.RecordingLLM(reply="alpha beta gamma")
    client = ms.ActionServiceReplyClient(
        llm,
        _snapshot_at,
        tokens_fn=lambda text: ms.TOKEN_CAP if len(text.split()) == 3 else 1,
    )
    records = asyncio.run(ms.run_design(client, _prompts(), repeats=1))
    result = ms.analyze(records, repeats=1, permutations=20)
    assert result["at_cap_share"] == {"off": 1.0, "on": 1.0}
    assert result["status"] != "incomplete"


def test_a_gating_run_without_tokens_fn_is_incomplete_end_to_end():
    llm = ms.RecordingLLM(reply="alpha beta gamma")
    client = ms.ActionServiceReplyClient(llm, _snapshot_at)
    records = asyncio.run(ms.run_design(client, _prompts(), repeats=1))
    result = ms.analyze(records, repeats=1, permutations=20)
    assert result["status"] == "incomplete"
    assert result["reason"] == ms.TOKENS_MISSING_REASON


def test_action_service_client_flags_a_self_correction_as_rejected():
    llm = ms.RecordingLLM(
        reply=lambda i, _p: "As an AI language model I cannot." if i == 0 else "Fine."
    )
    client = ms.ActionServiceReplyClient(
        llm, lambda p, c, r: {"mood": c, "cortisol": 0.5}
    )
    reply = asyncio.run(client.reply(_prompts()[0], flag_on=True, clamp=0.8, repeat=0))
    assert reply.rejected


# --- the seeded noise check ------------------------------------------------------


def test_noise_check_is_seeded_and_deterministic():
    from evals.brainbench import mood_style_noise_check as nc

    a = nc.simulate_run(10, 7)
    assert len(a) == 2 * ms.REPEATS * 3 * ms.PROMPT_COUNT
    assert a == nc.simulate_run(10, 7)
    assert a != nc.simulate_run(10, 8)
    first = nc.check(10, runs=2, permutations=20)
    assert first == nc.check(10, runs=2, permutations=20)
    assert first["pass"] + first["fail"] + first["void"] == 2


def test_noise_check_lift_moves_the_headline_the_right_way():
    from evals.brainbench import mood_style_noise_check as nc

    none = ms.analyze(nc.simulate_run(0, 3), permutations=20)
    lifted = ms.analyze(nc.simulate_run(40, 3), permutations=20)
    assert lifted["effect"] > none["effect"] + 0.2
