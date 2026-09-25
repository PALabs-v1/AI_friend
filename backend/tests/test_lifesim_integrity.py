"""Anti-gaming and truth-integrity gates for generated lifesim output (R1-R12).

Written by the benchmark's owner, not by the text layer's builder: these
check the files a harness would receive, with their own oracle loader, so a
bug in the builder's reasoning cannot hide behind the builder's own tests.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from evals.lifesim import banks
from evals.lifesim import probes as probes_mod
from evals.lifesim.generate import generate, parse_horizon, simulate_truth
from evals.lifesim.schema import (
    EXPECTED,
    INTENTS,
    PROBE_CATEGORIES,
    TURN_TAGS,
    read_jsonl,
)
from evals.lifesim.splits import SplitError
from evals.lifesim.stats import content_words, describe, load
from evals.lifesim.world import ATTRIBUTES

PANEL = {
    "main": (1001, "chatty_student", "1y"),
    "terse": (1002, "terse_engineer", "6m"),
    "forgetful": (1003, "forgetful_retiree", "6m"),
    "social": (1004, "socialite", "6m"),
    "hedger": (1005, "anxious_hedger", "6m"),
    "gamer": (1006, "night_owl_gamer", "6m"),
    "minimal": (1007, "private_minimalist", "1y"),
    "random": (1008, "random", "3m"),
}


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    root = tmp_path_factory.mktemp("lifesim")
    dirs = {}
    for key, (seed, arch, horizon) in PANEL.items():
        dirs[key] = root / key
        generate(seed, arch, horizon, dirs[key])
    return dirs


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Oracle:
    """Independent reading of oracle/timeline.jsonl (does not use evals.lifesim.timeline)."""

    def __init__(self, rows: list[dict]) -> None:
        self.slots: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows:
            self.slots[(r["entity"], r["attribute"])].append(r)
        for hist in self.slots.values():
            hist.sort(key=lambda r: r["valid_from"])
        self.names = {
            e: h[-1]["value"] for (e, a), h in self.slots.items() if a == "name"
        }

    def value_at(self, entity: str, attribute: str, t: datetime) -> str | None:
        for r in reversed(self.slots.get((entity, attribute), [])):
            if _t(r["valid_from"]) <= t and (
                r["valid_to"] is None or t < _t(r["valid_to"])
            ):
                return r["value"]
        return None

    def values_ever(self, entity: str, attribute: str) -> list[str]:
        return [r["value"] for r in self.slots.get((entity, attribute), [])]

    def same(self, stated: str, truth: str | None) -> bool:
        if truth is None:
            return False
        if stated == truth or stated == self.names.get(truth):
            return True
        # plan times may be rendered at date or minute precision
        return bool(re.match(r"\d{4}-\d{2}-\d{2}", truth)) and (
            truth.startswith(stated) or stated.startswith(truth[:10])
        )


def _all(out):
    for key, d in out.items():
        yield key, d, load(d)


# ---- R9 public schema and leakage ------------------------------------------------


def test_public_records_carry_nothing_but_the_allowed_fields(out):
    for _, _, data in _all(out):
        for t in data["turns"]:
            assert set(t) == {"turn_id", "session_id", "t", "speaker", "text"}
        for p in data["probes"]:
            assert set(p) == {"probe_id", "t", "text"}


ID_RX = re.compile(
    r"\b(person|pet|plan|goal|project|belief):[a-z]\d{3}\b|\b[ae]\d{6}\b"
)
KEYS = sorted(
    {k for k in ATTRIBUTES if "_" in k}
    | {
        "user_warmth",
        "competence_evidence",
        "source_event",
        "valid_from",
        "assertion_id",
    }
)
LABELS = sorted({t for t in TURN_TAGS + PROBE_CATEGORIES if "_" in t})


def test_public_text_has_no_ids_keys_or_labels(out):
    for _, _, data in _all(out):
        texts = [t["text"] for t in data["turns"]] + [p["text"] for p in data["probes"]]
        for text in texts:
            low = text.lower()
            assert not ID_RX.search(text), text
            for k in KEYS + LABELS:
                assert k not in low, (k, text)
            assert "{" not in text and "}" not in text, f"unrendered template: {text}"


def test_annotations_and_answers_align_and_use_the_schema(out):
    for _, _, data in _all(out):
        assert [a["turn_id"] for a in data["annotations"]] == [
            t["turn_id"] for t in data["turns"]
        ]
        assert [a["probe_id"] for a in data["answers"]] == [
            p["probe_id"] for p in data["probes"]
        ]
        for a in data["annotations"]:
            assert a["intent"] in INTENTS
            assert set(a["tags"]) <= set(TURN_TAGS)
            assert a["template_family"] and a["template_id"]
        for a in data["answers"]:
            assert a["category"] in PROBE_CATEGORIES and a["expected"] in EXPECTED
        times = [_t(t["t"]) for t in data["turns"]]
        assert times == sorted(times)


# ---- R1 truth first --------------------------------------------------------------


def test_truthful_claims_match_the_timeline_and_misstatements_do_not(out):
    checked = 0
    for key, _, data in _all(out):
        o = Oracle(data["timeline"])
        when = {t["turn_id"]: _t(t["t"]) for t in data["turns"]}
        for a in data["annotations"]:
            for c in a["claims"]:
                at = _t(c["about"]) if c["about"] else when[a["turn_id"]]
                truth = o.value_at(c["entity"], c["attribute"], at)
                if c["truthful"]:
                    assert o.same(c["value"], truth), (key, a["turn_id"], c, truth)
                else:
                    assert not o.same(c["value"], truth), (key, a["turn_id"], c, truth)
                checked += 1
    assert checked > 500


def test_misstatements_are_plausible_values_not_noise(out):
    for _, _, data in _all(out):
        for a in data["annotations"]:
            for c in a["claims"]:
                spec = ATTRIBUTES.get(c["attribute"])
                if (
                    not c["truthful"]
                    and not a["joke"]
                    and spec is not None
                    and spec.pool
                ):
                    assert c["value"] in spec.pool, c


def test_turns_only_mention_what_already_happened(out):
    for key, _, data in _all(out):
        ev_t = {e["event_id"]: _t(e["t"]) for e in data["events"]}
        for t, a in zip(data["turns"], data["annotations"], strict=True):
            for eid in a["event_ids"]:
                assert ev_t[eid] <= _t(t["t"]), (key, t["turn_id"], eid)


def test_corrections_point_back_at_a_false_statement(out):
    n = 0
    for _, _, data in _all(out):
        by_id = {a["turn_id"]: (i, a) for i, a in enumerate(data["annotations"])}
        for i, a in enumerate(data["annotations"]):
            if a["corrects_turn"]:
                j, target = by_id[a["corrects_turn"]]
                assert j <= i
                assert target["misstatement"] or any(
                    not c["truthful"] for c in target["claims"]
                )
                assert any(c["truthful"] for c in a["claims"])
                n += 1
    assert n > 0


def test_a_loss_is_talked_about_more_than_once(out):
    # Grouped by the affected entity, not by exact event id: a death and its
    # funeral are two distinct events, each mentioned once, three weeks
    # apart -- that IS the loss being revisited over time, not two losses.
    seen = 0
    for _, _, data in _all(out):
        loss_events = {
            e["event_id"]: e["entities"][0]
            for e in data["events"]
            if e["kind"] in ("death", "pet_death", "funeral") and e["entities"]
        }
        mentions = Counter(
            loss_events[eid]
            for a in data["annotations"]
            for eid in a["event_ids"]
            if eid in loss_events
        )
        for entity, n in mentions.items():
            seen += 1
            assert n >= 2, entity
    if not seen:
        pytest.skip("no loss was told in this panel")


# ---- R4 coverage -----------------------------------------------------------------


def test_one_year_persona_covers_every_memory_type(out):
    """R4: TURN_TAGS coverage on one dev persona, exactly as the rubric states it."""
    assert describe(out["main"])["missing_tags"] == []


def test_the_panel_covers_every_probe_category(out):
    """R8's categories are 'wherever the truth supports it', not a per-seed
    guarantee: a rare category like contradiction_surface (needs an
    uncorrected misstatement to survive to probe time) can legitimately miss
    on any one seed by chance. Checked across the whole panel instead, which
    is also how the categories are actually used downstream."""
    counts = Counter()
    for d in out.values():
        counts.update(describe(d)["probe_categories"])
    missing = [c for c in PROBE_CATEGORIES if not counts[c]]
    assert missing == []


# ---- R8 probes -------------------------------------------------------------------


def test_referenced_turns_exist_and_precede_the_question(out):
    for _, _, data in _all(out):
        when = {t["turn_id"]: _t(t["t"]) for t in data["turns"]}
        for p, a in zip(data["probes"], data["answers"], strict=True):
            pt = _t(p["t"])
            for tid in (
                a["support_turn_ids"] + a["stale_turn_ids"] + a["distractor_turn_ids"]
            ):
                assert tid in when and when[tid] < pt, (p["probe_id"], tid)
            if a["expected"] in ("answer", "forgettable", "surface_conflict"):
                assert a["support_turn_ids"], p["probe_id"]
            if a["expected"] == "abstain":
                assert not a["support_turn_ids"], p["probe_id"]
            assert a["answer"] or a["expected"] == "abstain"


def test_current_answers_are_the_truth_at_question_time(out):
    n = 0
    for key, _, data in _all(out):
        o = Oracle(data["timeline"])
        ann = {a["turn_id"]: a for a in data["annotations"]}
        for p, a in zip(data["probes"], data["answers"], strict=True):
            if a["category"] not in ("current", "stale_trap"):
                continue
            pt = _t(p["t"])
            ok = any(
                c["truthful"]
                and o.same(a["answer"][0], o.value_at(c["entity"], c["attribute"], pt))
                for tid in a["support_turn_ids"]
                for c in ann[tid]["claims"]
            )
            assert ok, (key, p, a)
            n += 1
    assert n >= 10


def test_stale_turns_carry_values_that_are_no_longer_true(out):
    n = 0
    for key, _, data in _all(out):
        o = Oracle(data["timeline"])
        ann = {x["turn_id"]: x for x in data["annotations"]}
        for p, a in zip(data["probes"], data["answers"], strict=True):
            if a["category"] != "stale_trap":
                continue
            assert a["stale_turn_ids"], (key, p)
            pt = _t(p["t"])
            for tid in a["stale_turn_ids"]:
                assert any(
                    not o.same(c["value"], o.value_at(c["entity"], c["attribute"], pt))
                    and not o.same(a["answer"][0], c["value"])
                    for c in ann[tid]["claims"]
                ), (key, p["probe_id"], tid)
                n += 1
    assert n >= 3


def test_interference_questions_have_same_family_distractors(out):
    n = 0
    for _, _, data in _all(out):
        for a in data["answers"]:
            if a["category"] == "interference":
                assert a["distractor_turn_ids"], a["probe_id"]
                assert not set(a["distractor_turn_ids"]) & set(a["support_turn_ids"])
                n += 1
    assert n >= 3


def test_forgettable_trivia_is_old_and_trivial(out):
    for _, _, data in _all(out):
        when = {t["turn_id"]: _t(t["t"]) for t in data["turns"]}
        ann = {x["turn_id"]: x for x in data["annotations"]}
        for p, a in zip(data["probes"], data["answers"], strict=True):
            if a["expected"] == "forgettable":
                for tid in a["support_turn_ids"]:
                    assert _t(p["t"]) - when[tid] > timedelta(days=60)
                    assert (
                        "trivial_episodic" in ann[tid]["tags"]
                        or ann[tid]["importance"] < 0.3
                    )


def test_every_answer_is_rederived_from_truth_alone(out):
    """R1: recompute from a truth-only simulation; no turns, no text."""
    n = 0
    for key, (seed, arch, horizon) in PANEL.items():
        m = read_jsonl(out[key] / "oracle/answers.jsonl")
        manifest_end = _t(json.loads((out[key] / "manifest.json").read_text())["end"])
        sim = simulate_truth(
            seed, arch, parse_horizon(horizon), banks.load(), "dev", end=manifest_end
        )
        for a in m:
            assert probes_mod.recompute(a["derivation"], sim) == a["answer"], (
                key,
                a["probe_id"],
                a["derivation"],
            )
            n += 1
    assert n >= 100


# ---- R10 anti-gaming -------------------------------------------------------------


def test_support_turns_are_not_positionally_predictable(out):
    first = last = total = 0
    latest_is_support = latest_total = 0
    for _, _, data in _all(out):
        sess: dict[str, list[str]] = defaultdict(list)
        for t in data["turns"]:
            sess[t["session_id"]].append(t["turn_id"])
        where = {
            tid: (sid, i) for sid, ids in sess.items() for i, tid in enumerate(ids)
        }
        ann = {x["turn_id"]: x for x in data["annotations"]}
        order = {t["turn_id"]: i for i, t in enumerate(data["turns"])}
        for p, a in zip(data["probes"], data["answers"], strict=True):
            if a["category"] not in (
                "current",
                "stale_trap",
                "historical",
                "relationship_defining",
                "multi_hop",
            ):
                continue
            for tid in a["support_turn_ids"]:
                sid, i = where[tid]
                if len(sess[sid]) >= 3:
                    total += 1
                    first += i == 0
                    last += i == len(sess[sid]) - 1
            if a["category"] in ("current", "stale_trap") and a["stale_turn_ids"]:
                slots = {
                    (c["entity"], c["attribute"])
                    for tid in a["support_turn_ids"]
                    for c in ann[tid]["claims"]
                }
                mentions = [
                    x["turn_id"]
                    for x in data["annotations"]
                    if any((c["entity"], c["attribute"]) in slots for c in x["claims"])
                ]
                pt = _t(p["t"])
                mentions = [
                    m for m in mentions if _t(data["turns"][order[m]]["t"]) < pt
                ]
                if mentions:
                    latest_total += 1
                    latest_is_support += (
                        max(mentions, key=order.get) in a["support_turn_ids"]
                    )
    assert total >= 20
    assert first / total < 0.6 and last / total < 0.6, (first, last, total)
    if latest_total >= 5:
        assert latest_is_support < latest_total, (
            "the answer is always the latest mention"
        )


def test_questions_mix_keyword_and_paraphrase_shapes(out):
    zero = some = 0
    for d in out.values():
        s = describe(d)["probe_support_overlap"]
        zero, some = zero + s["zero"], some + s["some"]
    total = zero + some
    assert total >= 50
    # P4's actual floor is on the zero-overlap side only ("at least a quarter
    # share no content word"); "some" just needs to be a real, non-token
    # presence, not parity with zero-overlap.
    assert zero / total >= 0.25, (zero, some)
    assert some / total >= 0.10, (zero, some)


def test_no_template_dominates(out):
    probe_uses: dict[str, Counter] = defaultdict(Counter)
    turn_uses: dict[str, Counter] = defaultdict(Counter)
    for _, _, data in _all(out):
        for a in data["answers"]:
            probe_uses[a["category"]][a["template_id"]] += 1
        for a in data["annotations"]:
            turn_uses[a["template_family"]][a["template_id"]] += 1
    for cat, c in probe_uses.items():
        if sum(c.values()) >= 20:
            assert max(c.values()) / sum(c.values()) <= 0.15, (cat, c.most_common(3))
    for fam, c in turn_uses.items():
        if sum(c.values()) >= 30 and len(c) >= 6:
            assert max(c.values()) / sum(c.values()) <= 0.35, (fam, c.most_common(3))


def test_questions_do_not_name_their_own_category(out):
    for _, _, data in _all(out):
        for p in data["probes"]:
            words = content_words(p["text"])
            assert not words & {
                "stale",
                "trap",
                "distractor",
                "unanswerable",
                "trivia",
                "probe",
            }, p["text"]


# ---- R7 persona knobs ------------------------------------------------------------


def test_persona_parameters_move_their_outputs(out):
    s = {k: describe(d) for k, d in out.items()}
    assert s["terse"]["words_per_turn"] < s["social"]["words_per_turn"]
    assert s["forgetful"]["misstatement_rate"] > s["terse"]["misstatement_rate"]
    assert s["hedger"]["hedge_rate"] > s["terse"]["hedge_rate"]
    assert s["gamer"]["joke_rate"] > s["minimal"]["joke_rate"]
    assert s["social"]["sessions_per_week"] > 3 * s["minimal"]["sessions_per_week"]
    assert s["forgetful"]["tags"]["repeated"] / s["forgetful"]["counts"]["turns"] > s[
        "terse"
    ]["tags"]["repeated"] / max(1, s["terse"]["counts"]["turns"])


# ---- R2, R5, R12 -----------------------------------------------------------------


def test_same_seed_same_bytes(out, tmp_path):
    seed, arch, horizon = PANEL["random"]
    m = generate(seed, arch, horizon, tmp_path / "again")
    first = json.loads((out["random"] / "manifest.json").read_text())
    assert m["files"] == first["files"]


def test_three_months_is_a_prefix_of_six(tmp_path):
    generate(1009, "busy_parent", "3m", tmp_path / "short")
    generate(1009, "busy_parent", "6m", tmp_path / "long")
    short = read_jsonl(tmp_path / "short/public/turns.jsonl")
    long = read_jsonl(tmp_path / "long/public/turns.jsonl")
    assert long[: len(short)] == short


def test_turn_horizon_is_exact(tmp_path):
    m = generate(1010, "steady_professional", "100t", tmp_path / "t100")
    assert m["counts"]["turns"] == 100


def test_heldout_needs_the_final_run_flag(tmp_path):
    with pytest.raises(SplitError):
        generate(9001, "socialite", "1w", tmp_path / "h")
    assert not Path(tmp_path / "h").exists()
