"""Fast scoring tests plus one real local-Ollama memory replay."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from evals.brainbench.adapters import build_memory_store
from evals.brainbench.memory_suite import (
    ABSTAIN_RELEVANCE_THRESHOLD,
    run_memory_suite_for_seed,
    score_probe,
)
from evals.lifesim.generate import build


def _answer(category: str, expected: str, values: list[str]) -> SimpleNamespace:
    return SimpleNamespace(category=category, expected=expected, answer=values)


def _memory(content: str, score: float = 1.0) -> dict:
    return {"content": content, "score": score}


def test_single_value_hit_miss_rank_and_word_boundaries():
    answer = _answer("current", "answer", ["art"])
    assert score_probe([_memory("I like ART and music")], answer) == {
        "n_retrieved": 1.0,
        "hit@5": 1.0,
        "mrr": 1.0,
    }
    assert score_probe([_memory("party decorations")], answer)["hit@5"] == 0.0
    ranked = score_probe(
        [_memory("unrelated"), _memory("The art exhibit opened")], answer
    )
    assert ranked["hit@5"] == 1.0
    assert ranked["mrr"] == 0.5


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (["Noor", "Glasshouse Games"], 1.0),
        (["Noor, the designer"], 0.0),
        (["Nothing useful"], 0.0),
    ],
)
def test_multi_hop_requires_both_values_anywhere(contents, expected):
    answer = _answer("multi_hop", "answer", ["Noor", "Glasshouse Games"])
    assert (
        score_probe([_memory(content) for content in contents], answer)["hit@5"]
        == expected
    )


def test_forgettable_reports_retained_vs_forgotten():
    answer = _answer("trivia_old", "forgettable", ["shakshuka"])
    assert score_probe([_memory("Lunch was shakshuka")], answer)["retained"] == 1.0
    assert score_probe([_memory("Lunch details faded")], answer)["retained"] == 0.0


def test_abstain_uses_strict_relevance_threshold():
    answer = _answer("unanswerable", "abstain", [])
    assert (
        score_probe([_memory("low noise", ABSTAIN_RELEVANCE_THRESHOLD)], answer)[
            "abstained"
        ]
        == 1.0
    )
    assert (
        score_probe(
            [_memory("something surfaced", ABSTAIN_RELEVANCE_THRESHOLD + 0.01)], answer
        )["abstained"]
        == 0.0
    )


def test_commitment_due_reports_partial_coverage():
    answer = _answer("commitment_due", "answer", ["cancel the gym trial", "call Noor"])
    metrics = score_probe([_memory("I need to cancel the gym trial")], answer)
    assert metrics["coverage"] == 0.5
    assert "hit@5" not in metrics


def test_contradiction_scores_only_canonical_correct_value():
    answer = _answer(
        "contradiction_surface", "surface_conflict", ["Seattle", "Hyderabad"]
    )
    metrics = score_probe([_memory("The city is Hyderabad")], answer)
    assert metrics["hit@5"] == 1.0
    assert metrics["mrr"] == 1.0


def test_n_retrieved_reports_actual_count_even_when_scoring_only_top_five():
    answer = _answer("current", "answer", ["Halcyon"])
    rows = [_memory("irrelevant") for _ in range(5)] + [_memory("Halcyon")]
    metrics = score_probe(rows, answer)
    assert metrics["n_retrieved"] == 6.0
    assert metrics["hit@5"] == 0.0


@pytest.mark.asyncio
async def test_abstain_threshold_separates_dev_seed_hit_from_noise(
    tmp_path, monkeypatch
):
    _sim, turns, _annotations, probes, answers = build(1000, "chatty_student", "1w")
    probe_index = next(
        i
        for i, answer in enumerate(answers)
        if answer.category == "current" and answer.support_turn_ids
    )
    probe, answer = probes[probe_index], answers[probe_index]
    by_id = {turn.turn_id: turn for turn in turns}
    supported = by_id[answer.support_turn_ids[0]]
    noise = next(
        turn
        for turn in turns
        if turn.turn_id not in answer.support_turn_ids
        and turn.session_id != supported.session_id
    )
    store = build_memory_store(tmp_path)

    async def query_embedding(_text: str) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setattr(store, "get_embedding", query_embedding)
    try:
        assert await store.add_memory(
            supported.text, embedding=[1.0, 0.0], current_time=probe.t
        )
        assert await store.add_memory(
            noise.text, embedding=[0.0, 1.0], current_time=probe.t
        )
        retrieved = await store.search_memories(
            probe.text, limit=5, current_time=probe.t
        )
        scores = {row["content"]: row["score"] for row in retrieved}
        assert scores[supported.text] > ABSTAIN_RELEVANCE_THRESHOLD
        assert scores[noise.text] < ABSTAIN_RELEVANCE_THRESHOLD
    finally:
        await store.close()


# llm_augmented replays are GPU work and belong on home-gpu, not whatever
# Ollama happens to be running on the dev machine (a real week-long persona
# replay is not a "seconds-long smoke check"). Point this at the Mac's local
# Ollama only by explicit override, never by default, so this test can never
# silently burn Mac CPU/battery just because port 11434 answers.
BRAINBENCH_LLM_URL = os.environ.get("BRAINBENCH_LLM_URL", "http://100.88.246.46:11434")


@pytest.mark.asyncio
async def test_real_llm_augmented_lifesim_replay(tmp_path: Path, ollama_tags):
    from app.llm import build_llm_client
    from evals.brainbench.adapters import build_cognitive_service

    assert (Config.LLM_PROVIDER or "ollama").lower() == "ollama"
    assert isinstance(ollama_tags.get("models", []), list)
    service = build_cognitive_service(
        "llm_augmented",
        tmp_path,
        llm_service=build_llm_client(
            base_url=BRAINBENCH_LLM_URL,
            model=Config.LLM_CHAT_MODEL,
        ),
    )
    simulation = build(1000, "chatty_student", "1w")
    _sim, turns, _annotations, probes, _answers = simulation
    try:
        outcomes = await run_memory_suite_for_seed(
            1000, "chatty_student", "1w", service, progress_every=25
        )
        assert len(outcomes) == len(probes)
        assert len({outcome.probe_key for outcome in outcomes}) == len(probes)
        assert all(outcome.mode == "llm_augmented" for outcome in outcomes)
        assert all(outcome.suite == "memory" for outcome in outcomes)
        assert all("n_retrieved" in outcome.metrics for outcome in outcomes)
        assert any(outcome.metrics.get("hit@5", 0.0) > 0 for outcome in outcomes), (
            "the real replay completed but no probe retrieved its canonical answer"
        )
        # run_memory_suite raises immediately if any real turn emits type=error.
        assert len(turns) > 0
    finally:
        service.cognitive.close()
        await service.llm_service.close()
