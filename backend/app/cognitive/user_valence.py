"""Pluggable user-text valence estimators used before affect appraisal."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Protocol


class ValenceEstimator(Protocol):
    """One text-only estimator contract shared by runtime and bake-off."""

    name: str

    async def estimate(self, text: str) -> float: ...


class VaderEstimator:
    """Fast lexical baseline; VADER's compound score is already in [-1, 1]."""

    name = "vader"

    async def estimate(self, text: str) -> float:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        except ImportError as exc:
            raise RuntimeError(
                "vaderSentiment is required for the VADER estimator"
            ) from exc

        if not hasattr(self, "_analyzer"):
            self._analyzer = SentimentIntensityAnalyzer()
        return float(self._analyzer.polarity_scores(text)["compound"])


class TransformerEstimator:
    """Optional local HF sequence classifier with an explicit valence map."""

    LABEL_WEIGHTS = {
        "cardiffnlp/twitter-roberta-base-sentiment-latest": {
            "negative": -1.0,
            "neutral": 0.0,
            "positive": 1.0,
        },
        "j-hartmann/emotion-english-distilroberta-base": {
            "anger": -1.0,
            "disgust": -1.0,
            "fear": -1.0,
            "sadness": -1.0,
            "joy": 1.0,
            "love": 1.0,
            "neutral": 0.0,
            "surprise": 0.0,
        },
        "SamLowe/roberta-base-go_emotions": {
            **dict.fromkeys(
                (
                    "anger",
                    "annoyance",
                    "disappointment",
                    "disapproval",
                    "disgust",
                    "embarrassment",
                    "fear",
                    "grief",
                    "nervousness",
                    "remorse",
                    "sadness",
                ),
                -1.0,
            ),
            **dict.fromkeys(
                (
                    "admiration",
                    "amusement",
                    "approval",
                    "caring",
                    "desire",
                    "excitement",
                    "gratitude",
                    "joy",
                    "love",
                    "optimism",
                    "pride",
                    "relief",
                ),
                1.0,
            ),
        },
    }

    def __init__(self, model_name: str):
        if model_name not in self.LABEL_WEIGHTS:
            raise ValueError(f"unsupported local sentiment model: {model_name}")
        self.name = model_name
        self._model_name = model_name
        self._classifier = None

    def _load(self):
        if self._classifier is not None:
            return self._classifier
        try:
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )
        except ImportError as exc:
            raise RuntimeError(
                "transformers and torch are optional requirements for local classifiers"
            ) from exc
        tokenizer = AutoTokenizer.from_pretrained(
            self._model_name, local_files_only=True
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            self._model_name, local_files_only=True
        )
        self._classifier = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            device=-1,
            top_k=None,
        )
        return self._classifier

    def prepare(self) -> None:
        """Load local weights during service startup, outside first-turn latency."""
        self._load()

    def _run_sync(self, text: str):
        """The blocking HF call, run off the event loop by `estimate`. Also
        loads on first use here rather than in `estimate` itself, so a
        caller that skipped `prepare()` blocks a worker thread instead of
        the loop even on that first, uncached call."""
        return self._load()(text, truncation=True)

    async def estimate(self, text: str) -> float:
        # W2 critic round 2, finding 5: this used to call the classifier
        # synchronously inline. `estimate` is a coroutine, but the pipeline
        # call itself is not awaited -- it blocks the single event-loop
        # thread for its full duration, so the caller's
        # `asyncio.wait_for(..., timeout=...)` cannot fire until the call
        # returns on its own (the reproducer: a 0.03s timeout around a
        # blocking call returned its value at 0.205s, never timing out).
        # `asyncio.to_thread` moves the blocking work to a worker thread so
        # the loop stays free and the timeout can actually cancel the wait.
        result = (await asyncio.to_thread(self._run_sync, text))[0]
        weights = self.LABEL_WEIGHTS[self._model_name]
        total = sum(float(row["score"]) for row in result)
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("classifier returned no probability mass")
        value = (
            sum(
                float(row["score"]) * weights.get(str(row["label"]).lower(), 0.0)
                for row in result
            )
            / total
        )
        if not math.isfinite(value):
            raise ValueError("classifier returned non-finite valence")
        return max(-1.0, min(1.0, value))


class OllamaValenceEstimator:
    """Local Ollama JSON client; malformed or missing valence is a failure."""

    name = "ollama"

    def __init__(self, model: str, url: str, *, client=None):
        self.model = model
        self.url = url.rstrip("/")
        self._client = client

    async def estimate(self, text: str) -> float:
        import httpx

        prompt = (
            "Estimate the user's expressed emotional valence from this message. "
            'Return JSON only as {"valence": number} in [-1,1]. '
            "Neutral factual text is 0. Message: " + json.dumps(text, ensure_ascii=True)
        )
        if self._client is None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await self._request(client, prompt)
        return await self._request(self._client, prompt)

    async def _request(self, client, prompt: str) -> float:
        response = await client.post(
            f"{self.url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                # A thinking model (qwen3) otherwise puts its whole answer in
                # `thinking` and leaves `response` empty: 100% parse failure
                # on the home-gpu bake-off (F-006's class). Non-thinking
                # models accept the flag and answer unchanged.
                "think": False,
                "options": {"temperature": 0, "num_predict": 64},
            },
        )
        response.raise_for_status()
        body = response.json()
        decoded = json.loads(body["response"])
        value = decoded["valence"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Ollama response valence must be numeric")
        if not math.isfinite(value):
            raise ValueError("Ollama response valence must be finite")
        return max(-1.0, min(1.0, float(value)))


class MeanEnsemble:
    """Unweighted average over independently runnable estimators."""

    def __init__(self, estimators: list[ValenceEstimator]):
        if not estimators:
            raise ValueError("an ensemble needs at least one estimator")
        self.estimators = estimators
        self.name = "ensemble:" + "+".join(item.name for item in estimators)

    def prepare(self) -> None:
        """Prepare any weight-backed members before interactive turns."""
        for estimator in self.estimators:
            prepare = getattr(estimator, "prepare", None)
            if prepare is not None:
                prepare()

    async def estimate(self, text: str) -> float:
        values = [await estimator.estimate(text) for estimator in self.estimators]
        return sum(values) / len(values)


def build_estimator(
    name: str,
    *,
    ollama_model: str = "qwen3:8b",
    ollama_url: str = "http://127.0.0.1:11434",
) -> ValenceEstimator:
    """Construct a configured estimator by stable CLI/config name."""
    if name == "vader":
        return VaderEstimator()
    if name in TransformerEstimator.LABEL_WEIGHTS:
        return TransformerEstimator(name)
    if name == "ollama":
        return OllamaValenceEstimator(ollama_model, ollama_url)
    if name.startswith("ensemble:"):
        return MeanEnsemble(
            [
                build_estimator(part, ollama_model=ollama_model, ollama_url=ollama_url)
                for part in name.removeprefix("ensemble:").split(",")
            ]
        )
    raise ValueError(f"unknown affect valence estimator: {name}")
