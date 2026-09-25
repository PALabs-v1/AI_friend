"""Deterministic event generation and real BrainAgent lifecycle measurements."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Config
from evals.brainbench.bargein_suite import (
    FAMILIES,
    Event,
    ScenarioEvidence,
    _attribute_assistant_rows,
    _run_scenario,
    _scenario,
    check_invariants,
    generate_scenarios,
    invariant_violation_rates,
    outcome_accounting,
    run_bargein_suite,
    run_bargein_suite_for_seed,
)
from evals.brainbench.stats import SuiteOutcome


def _family(scenarios, family):
    return next(scenario for scenario in scenarios if scenario.family == family)


def _clean_evidence() -> ScenarioEvidence:
    return ScenarioEvidence(
        started_replies=("A",),
        terminal_counts={"A": 1},
        history_expected={"A": "one two three"},
        history_actual={"A": "one two three"},
        history_claimed={"A": True},
        terminal_eligible=("A",),
    )


@pytest.fixture(scope="module")
def real_seed_1000_outcomes():
    return run_bargein_suite(1000)


def test_generator_is_reproducible_and_weighted_mix_is_seeded():
    first = generate_scenarios(1000, n_scenarios_per_family=2, n_events=12)
    second = generate_scenarios(1000, n_scenarios_per_family=2, n_events=12)
    assert first == second
    assert [scenario.family for scenario in first] == [
        family for _ in range(2) for family in (*FAMILIES[:-1], "random")
    ]
    only_unknown = generate_scenarios(
        1001, n_scenarios_per_family=1, n_events=5, mix_weights={"unknown": 1.0}
    )
    random_case = _family(only_unknown, "random")
    assert len(random_case.events) == 5
    assert all(
        event.target == "unknown"
        for event in random_case.events
        if event.type in ("progress", "stop")
    )
    for scenario in first:
        scenario_replies = list(scenario.reply_texts.values())
        assert len(scenario_replies) == len(set(scenario_replies))
        assert all(text.startswith("[reply:") for text in scenario_replies)


def test_stale_stop_events_do_not_claim_confirmed_command_authority():
    scenarios = generate_scenarios(1000, n_scenarios_per_family=50)
    stale_stops = [
        event
        for scenario in scenarios
        for event in scenario.events
        if event.type == "stop" and event.target == "stale"
    ]

    assert stale_stops
    assert all(event.reason == "facial_reflex_startle" for event in stale_stops)


@pytest.mark.parametrize(
    ("family", "predicate"),
    [
        (
            "clean",
            lambda events: (
                sum(e.type == "user_utterance" for e in events) == 1
                and any(
                    e.type == "progress" and e.word_offset >= 10_000 for e in events
                )
                and not any(e.type == "stop" for e in events)
            ),
        ),
        (
            "confirmed_barge_in",
            lambda events: any(
                e.type == "stop" and e.reason == "confirmed_user_speech" for e in events
            ),
        ),
        (
            "superseded_stop",
            lambda events: any(
                e.type == "stop" and e.target == "superseded" for e in events
            ),
        ),
        (
            "stale_stop",
            lambda events: any(
                e.type == "stop" and e.target == "stale" for e in events
            ),
        ),
        (
            "unknown_stop",
            lambda events: any(
                e.type == "stop" and e.target == "unknown" for e in events
            ),
        ),
        (
            "rapid_fire",
            lambda events: (
                sum(e.type == "user_utterance" for e in events) >= 3
                and not any(e.type == "progress" for e in events)
            ),
        ),
        ("random", lambda events: len(events) >= 2 and events[-1].type == "idle"),
    ],
)
def test_each_family_has_its_defining_event_shape(family, predicate):
    scenario = _family(generate_scenarios(1000), family)
    assert predicate(scenario.events)


def test_unresolved_count_covers_every_started_reply_not_only_eligible_ones():
    # B played to the end and never got COMPLETED (V2 has no producer, F-002):
    # not eligible, so the eligible-only count cannot see it.
    evidence = replace(
        _clean_evidence(),
        started_replies=("A", "B", "B"),
        terminal_counts={"A": 1},
        terminal_eligible=("A",),
    )
    metrics = check_invariants(evidence)
    assert metrics["replies_with_zero_terminal_outcomes"] == 0
    assert metrics["started_reply_count"] == 2
    assert metrics["started_replies_without_terminal"] == 1


def test_only_playing_or_superseded_stops_carry_the_voice_command_reason():
    # ADR-003: Stage 2 addresses its confirmed command to the playing or the
    # superseded reply; stops for older or unknown turns come from the reflex.
    stops = [
        event
        for scenario in generate_scenarios(1000, n_scenarios_per_family=20)
        for event in scenario.events
        if event.type == "stop"
    ]
    assert {e.target for e in stops} >= {"playing", "superseded", "stale", "unknown"}
    for event in stops:
        if event.target in ("stale", "unknown"):
            assert event.reason == "facial_reflex_startle", event
        elif event.reason != "confirmed_user_speech":
            assert event.reason == "confirmed_command", event


@pytest.mark.asyncio
async def test_a_stale_stop_that_lands_on_the_superseded_reply_is_not_applied(
    monkeypatch,
):
    # Seed 1000 random scenario 29: "stale" resolves to the first reply, which
    # is also the superseded one. As a voice command BrainAgent would rightly
    # accept it (ADR-003), and the suite used to score that as a stale stop
    # that applied; a reflex stop for a superseded turn must be ignored.
    monkeypatch.setattr(Config, "BARGE_IN_ONSET_GRACE_S", 0.0)  # as the suite runs
    scenario = next(
        s
        for s in generate_scenarios(1000, n_scenarios_per_family=50)
        if s.family == "random" and s.index == 29
    )
    assert any(e.type == "stop" and e.target == "stale" for e in scenario.events)
    reflex = await _run_scenario(scenario, 1000, scenario.index, 1.0)
    assert reflex.metrics["stale_stop_applied_violation"] == 0
    as_command = replace(
        scenario,
        events=tuple(
            replace(e, reason="confirmed_command") if e.type == "stop" else e
            for e in scenario.events
        ),
    )
    command = await _run_scenario(as_command, 1000, scenario.index, 1.0)
    assert command.metrics["stale_stop_applied_violation"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda clean: replace(
            clean, terminal_counts={"A": 0}, terminal_eligible=("A",)
        ),
        lambda clean: replace(clean, history_actual={"A": "one two"}),
        lambda clean: replace(clean, stale_stop_changed=True),
        lambda clean: replace(clean, superseded_stop_harmed_current=True),
        lambda clean: replace(clean, completed_without_producer=True),
        lambda clean: replace(clean, hung=True),
    ],
    ids=(
        "terminal-outcome",
        "history",
        "stale-stop",
        "current-turn",
        "completion",
        "hung",
    ),
)
def test_each_invariant_checker_detects_a_hand_built_violation(change):
    baseline = check_invariants(_clean_evidence())
    violated = check_invariants(change(_clean_evidence()))
    names = (
        "terminal_outcomes_per_reply",
        "history_matches_heard",
        "stale_stop_applied",
        "current_turn_harmed",
        "completed_outcome_seen",
        "hung",
    )
    changed = [
        name
        for name in names
        if violated[f"{name}_violation"] > baseline[f"{name}_violation"]
    ]
    assert len(changed) == 1


def test_invariant_checker_passes_a_hand_built_clean_case():
    metrics = check_invariants(_clean_evidence())
    assert all(
        metrics[f"{name}_violation"] == 0.0
        for name in (
            "terminal_outcomes_per_reply",
            "history_matches_heard",
            "stale_stop_applied",
            "current_turn_harmed",
            "completed_outcome_seen",
            "hung",
        )
    )
    assert metrics["history_mismatch_char_length"] == 0


def test_wrongly_truncated_history_row_is_attributed_and_compared():
    scenario = _family(generate_scenarios(1000), "clean")
    turn_id, reply = next(iter(scenario.reply_texts.items()))
    expected = reply[:20]
    wrong_row = reply[:19]
    attributed, unattributed = _attribute_assistant_rows(
        [["row-id", "assistant", wrong_row]], scenario.reply_texts
    )

    metrics = check_invariants(
        ScenarioEvidence(
            history_expected={turn_id: expected},
            history_rows=tuple(attributed),
            unattributed_assistant_rows=unattributed,
        )
    )

    assert metrics["history_rows_compared"] == 1
    assert metrics["history_mismatch_count"] == 1
    assert metrics["history_matches_heard_violation"] == 1.0
    assert metrics["replies_without_history_row"] == 0


def test_assistant_history_row_matching_no_reply_is_unattributed():
    scenario = _family(generate_scenarios(1000), "clean")
    turn_id, reply = next(iter(scenario.reply_texts.items()))
    attributed, unattributed = _attribute_assistant_rows(
        [["row-id", "assistant", "unrelated assistant content"]],
        scenario.reply_texts,
    )

    metrics = check_invariants(
        ScenarioEvidence(
            history_expected={turn_id: reply},
            history_rows=tuple(attributed),
            history_row_claimed={turn_id: False},
            unattributed_assistant_rows=unattributed,
        )
    )

    assert not attributed
    assert metrics["unattributed_assistant_rows"] == 1
    assert metrics["unattributed_assistant_rows_violation"] == 1.0
    assert metrics["unattributed_assistant_rows_claimed_violation"] == 1.0
    assert metrics["replies_without_history_row"] == 1
    assert metrics["replies_without_history_row_claimed"] == 0
    assert metrics["replies_without_history_row_unclaimed"] == 1


@pytest.mark.parametrize("terminal_count", [0, 2], ids=("missing", "duplicate"))
def test_confirmed_speech_mid_playback_terminal_violation_is_unclaimed(
    terminal_count,
):
    metrics = check_invariants(
        ScenarioEvidence(
            started_replies=("A",),
            terminal_counts={"A": terminal_count},
            terminal_eligible=("A",),
            terminal_claimed={"A": False},
        )
    )

    assert metrics["terminal_outcomes_per_reply_violation"] == 1.0
    assert metrics["terminal_outcomes_per_reply_claimed_violation"] == 0.0
    assert metrics["terminal_outcomes_per_reply_unclaimed_violation"] == 1.0
    assert metrics["replies_with_zero_terminal_outcomes"] == int(terminal_count == 0)
    assert metrics["replies_with_multiple_terminal_outcomes"] == int(
        terminal_count == 2
    )


def test_clean_scenario_has_no_real_brainagent_violations(real_seed_1000_outcomes):
    outcome = next(
        row for row in real_seed_1000_outcomes if row.categories == ("clean",)
    )
    assert outcome.suite == "bargein"
    assert outcome.mode == "architecture_only"
    assert outcome.persona_seed == 1000
    assert outcome.probe_key == "1000:clean:0"
    for name in (
        "terminal_outcomes_per_reply",
        "history_matches_heard",
        "stale_stop_applied",
        "current_turn_harmed",
        "completed_outcome_seen",
        "hung",
    ):
        assert outcome.metrics[f"{name}_violation"] == 0.0
    assert outcome.metrics["replies_unresolved_without_completion"] == 0


def test_adr003_superseded_stop_truncates_old_reply_and_preserves_current_turn(
    real_seed_1000_outcomes,
):
    outcome = next(
        row for row in real_seed_1000_outcomes if row.categories == ("superseded_stop",)
    )
    assert outcome.metrics["history_matches_heard_violation"] == 0.0
    assert outcome.metrics["terminal_outcomes_per_reply_violation"] == 0.0
    assert outcome.metrics["current_turn_harmed_violation"] == 0.0
    assert outcome.metrics["replies_with_zero_terminal_outcomes"] == 0


@pytest.mark.asyncio
async def test_earlier_legitimate_stop_of_current_turn_is_not_counted_as_harm():
    """Reviewer regression: the harm check used to scan the playing turn's whole
    outcome history, so a stop aimed at the current reply followed by a stop for
    the superseded reply flagged ADR-003's guarantee as broken (24% of the
    random family at n=50). Only what the superseded stop itself changes counts.
    """
    events = [
        Event("user_utterance", turn_id="t-old"),
        Event("stream_chunk_delay", chunks=2),
        Event("progress", target="playing", word_offset=2),
        Event("user_utterance", turn_id="t-new"),
        Event("stream_chunk_delay", chunks=2),
        Event("progress", target="playing", word_offset=2),
        Event("stop", target="playing"),
        Event("stop", target="superseded"),
        Event("idle"),
    ]
    scenario = _scenario("random", 0, events, ("one two three four five six seven",))
    outcome = await _run_scenario(scenario, 1000, 0, 2.0)
    assert outcome.metrics["current_turn_harmed_violation"] == 0.0


@pytest.mark.asyncio
async def test_superseded_stop_without_a_superseded_reply_is_judged_as_unknown():
    events = [
        Event("user_utterance", turn_id="t-only"),
        Event("stream_chunk_delay", chunks=2),
        Event("stop", target="superseded"),
        Event("idle"),
    ]
    scenario = _scenario("random", 0, events, ("one two three four five six seven",))
    outcome = await _run_scenario(scenario, 1000, 0, 2.0)
    assert outcome.metrics["current_turn_harmed_violation"] == 0.0


@pytest.mark.asyncio
async def test_progress_cannot_report_words_that_were_never_streamed():
    """Reviewer regression: an uncapped progress offset before any word was
    streamed made the following stop look mid-playback and produced claimed
    terminal violations (4% of the random family at n=50) on audio that
    could not exist."""
    events = [
        Event("user_utterance", turn_id="t-early"),
        Event("progress", target="playing", word_offset=3),
        Event("stop", target="playing"),
        Event("idle"),
    ]
    scenario = _scenario("random", 0, events, ("one two three four five six seven",))
    outcome = await _run_scenario(scenario, 1000, 0, 2.0)
    assert outcome.metrics["terminal_outcome_replies_eligible"] == 0
    assert outcome.metrics["terminal_outcomes_per_reply_claimed_violation"] == 0.0


def test_ordinary_barge_in_history_matches_the_heard_prefix(
    real_seed_1000_outcomes,
):
    outcome = next(
        row
        for row in real_seed_1000_outcomes
        if row.categories == ("confirmed_barge_in",)
    )
    assert outcome.metrics["history_matches_heard_violation"] == 0.0
    assert outcome.metrics["history_matches_heard_claimed_violation"] == 0.0
    assert outcome.metrics["history_matches_heard_unclaimed_violation"] == 0.0


def test_scoring_functions_report_rates_and_reply_accounting():
    outcomes = [
        SuiteOutcome(
            probe_key="p1",
            persona_seed=1000,
            suite="bargein",
            categories=("clean",),
            metrics={
                "hung_violation": 0.0,
                "hung_claimed_violation": 0.0,
                "hung_unclaimed_violation": 0.0,
                "replies_with_zero_terminal_outcomes": 0.0,
                "replies_with_multiple_terminal_outcomes": 0.0,
                "replies_unresolved_without_completion": 1.0,
                "completed_outcome_count": 0.0,
            },
        ),
        SuiteOutcome(
            probe_key="p2",
            persona_seed=1000,
            suite="bargein",
            categories=("clean",),
            metrics={
                "hung_violation": 1.0,
                "hung_claimed_violation": 1.0,
                "hung_unclaimed_violation": 0.0,
                "replies_with_zero_terminal_outcomes": 1.0,
                "replies_with_multiple_terminal_outcomes": 0.0,
                "replies_unresolved_without_completion": 0.0,
                "completed_outcome_count": 1.0,
            },
        ),
    ]
    rates = invariant_violation_rates(outcomes)
    assert rates["hung:clean"] == {
        "n": 2,
        "rate": 0.5,
        "claimed_rate": 0.5,
        "unclaimed_rate": 0.0,
    }
    assert outcome_accounting(outcomes)["clean"] == {
        "scenarios": 2,
        "zero_terminal_replies": 1,
        "multiple_terminal_replies": 0,
        "unresolved_without_completion": 1,
        "completed_outcomes": 1,
    }


def test_events_and_outcomes_validate_dev_seed_boundary():
    with pytest.raises(ValueError, match="dev seeds"):
        generate_scenarios(2000)
    with pytest.raises(ValueError, match="dev seeds"):
        run_bargein_suite(9000)


def test_lifesim_entry_builds_without_cognitive_service(monkeypatch):
    calls = []

    def fake_build(seed, archetype, horizon):
        calls.append((seed, archetype, horizon))
        return (
            object(),
            [type("Turn", (), {"text": "A realistic reply text"})()],
            [],
            [],
            [],
        )

    monkeypatch.setattr("evals.brainbench.bargein_suite.build", fake_build)
    outcomes = run_bargein_suite_for_seed(1000, "steady_friend", "1w")
    assert calls == [(1000, "steady_friend", "1w")]
    assert len(outcomes) == len(FAMILIES)
    assert all(row.persona_seed == 1000 for row in outcomes)
