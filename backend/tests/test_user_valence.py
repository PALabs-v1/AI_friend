"""User-text valence estimators and offline Ollama protocol checks."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from app.cognitive.user_valence import OllamaValenceEstimator, TransformerEstimator
from experiments import affect_estimator_bakeoff as bakeoff
from experiments.affect_estimator_bakeoff import (
    _dataset_report,
    _failure_rate,
    _lifesim_rows,
    _measure,
    _passes_adr002,
)


def test_transformer_valence_mappings_are_bounded_and_signed():
    assert TransformerEstimator.LABEL_WEIGHTS[
        "cardiffnlp/twitter-roberta-base-sentiment-latest"
    ] == {"negative": -1.0, "neutral": 0.0, "positive": 1.0}
    go_emotions = TransformerEstimator.LABEL_WEIGHTS["SamLowe/roberta-base-go_emotions"]
    assert go_emotions["joy"] == 1.0
    assert go_emotions["anger"] == -1.0


@pytest.mark.asyncio
async def test_transformer_estimator_blocking_inference_can_be_timed_out(monkeypatch):
    """W2 critic round 2, finding 5: `estimate` ran the classifier
    synchronously inline, which blocks the single event-loop thread, so a
    caller's `asyncio.wait_for(..., timeout=...)` could not cancel it until
    the blocking call returned on its own. The critic's repro: a 0.03s
    timeout around a call that took 0.205s never timed out. `asyncio.
    to_thread` moves the blocking work off the loop so the timeout can
    actually fire while the classifier is still running.
    """
    estimator = TransformerEstimator("cardiffnlp/twitter-roberta-base-sentiment-latest")

    def slow_classifier(text, truncation=True):
        time.sleep(0.2)
        return [[{"label": "positive", "score": 1.0}]]

    monkeypatch.setattr(estimator, "_load", lambda: slow_classifier)

    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(estimator.estimate("hello"), timeout=0.03)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.15, (
        f"wait_for did not cancel promptly, took {elapsed}s against a 0.03s timeout"
    )


@pytest.mark.asyncio
async def test_ollama_estimator_parses_stubbed_json_response():
    async def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen3:8b"
        assert body["stream"] is False
        return httpx.Response(200, json={"response": '{"valence": -0.75}'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        estimator = OllamaValenceEstimator(
            "qwen3:8b", "http://ollama.local", client=client
        )
        assert await estimator.estimate("This has been awful") == pytest.approx(-0.75)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ollama_estimator_rejects_unparseable_stubbed_output():
    async def handler(_request):
        return httpx.Response(200, json={"response": "not json"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        estimator = OllamaValenceEstimator(
            "qwen3:8b", "http://ollama.local", client=client
        )
        with pytest.raises(json.JSONDecodeError):
            await estimator.estimate("I am upset")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ollama_estimator_rejects_non_finite_stubbed_valence():
    async def handler(_request):
        return httpx.Response(200, json={"response": '{"valence": NaN}'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        estimator = OllamaValenceEstimator(
            "qwen3:8b", "http://ollama.local", client=client
        )
        with pytest.raises(ValueError, match="finite"):
            await estimator.estimate("I am upset")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_bakeoff_counts_stubbed_parse_failures_in_candidate_failure_rate():
    class FailingEstimator:
        async def estimate(self, _text):
            raise ValueError("stubbed malformed response")

    values, _latencies = await _measure(FailingEstimator(), ["hello", "hello", "bye"])

    assert len(values) == 2
    assert _failure_rate(list(values.values())) == 1.0


def test_bakeoff_verdict_uses_expressed_valence_not_event_valence(monkeypatch):
    class FixedEstimator:
        async def estimate(self, text):
            return {"a": 0.8, "b": -0.8, "c": 0.8, "d": -0.8}[text]

    async def fake_simulate(*_args, **_kwargs):
        return SimpleNamespace(metrics=lambda: {"final_trust": 0.4})

    monkeypatch.setattr(
        bakeoff, "build_estimator", lambda *_args, **_kwargs: FixedEstimator()
    )
    monkeypatch.setattr(bakeoff.affect_sim, "simulate", fake_simulate)
    tom_rows = [("a", 1.0), ("b", -1.0), ("c", 1.0), ("d", -1.0)]
    rows = [
        ("a", 1.0, 1.0),
        ("b", -1.0, -1.0),
        ("c", 1.0, -1.0),
        ("d", -1.0, 1.0),
    ]
    report = bakeoff._candidate_report("stub", tom_rows, rows, "model", "url")

    assert report["lifesim_dev_expressed_valence"]["pearson_r"] > 0.99
    assert report["lifesim_dev_expressed_valence"]["sign_agreement_nonneutral"] == 1.0
    assert report["lifesim_dev_event_user_valence"]["pearson_r"] == 0.0
    assert report["lifesim_dev_event_user_valence"]["sign_agreement_nonneutral"] == 0.5
    assert report["adr_002_pass"] is True


def test_bakeoff_extracts_expressed_and_event_labels_separately():
    turns = [SimpleNamespace(turn_id="t1", text="That sounds lovely.")]
    annotations = [
        SimpleNamespace(turn_id="t1", expressed_valence=0.7, user_valence=-0.6)
    ]

    assert _lifesim_rows(turns, annotations) == [("That sounds lovely.", 0.7, -0.6)]


def test_bakeoff_scores_failed_predictions_as_neutral_instead_of_dropping_them():
    rows = [("a", 0.3), ("b", 0.4), ("c", 0.5), ("d", 1.0)]
    predictions = {"a": 0.3, "b": 0.4, "c": 0.5, "d": float("nan")}

    report = _dataset_report(rows, predictions, [1.0] * 4)
    passed = _passes_adr002(report, report, [0.4, 0.4, 0.4])

    assert report["n"] == 4
    assert report["failure_rate"] == 0.25
    assert report["sign_agreement_nonneutral"] == 0.75
    assert report["pearson_r"] < 0.8
    assert passed is False


def test_bakeoff_requires_each_hostile_seed_to_meet_trust_threshold():
    perfect = {"pearson_r": 1.0, "sign_agreement_nonneutral": 1.0}

    assert not _passes_adr002(perfect, perfect, [0.9, 0.3, 0.3])
