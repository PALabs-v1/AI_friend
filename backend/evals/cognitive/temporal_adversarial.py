"""Production write-path false-closure probes; deterministic and offline."""

from __future__ import annotations

import asyncio

from app.cognitive.learning import ReflectionService
from app.config import Config
from app.state.memory_records import BeliefRecord
from app.state.temporal_detector import classify_e8_temporal_relation
from app.state.temporal_store import TemporalMemoryStore


class _FixedEmbedding:
    async def get_embedding(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class _ConservativeLLM:
    async def generate(self, _prompt: str, **_kwargs) -> str:
        return '{"relation":"CONFLICT"}'


_CASES = (
    ("paraphrase", "Acme Corporation", "Acme", "same company, paraphrased"),
    (
        "abbreviation",
        "International Business Machines Corporation",
        "IBM",
        "same company, abbreviated",
    ),
    ("still", "Acme Corporation", "Acme", "Ari still works there now."),
    ("now", "Acme Corporation", "Acme", "Ari works there now."),
    ("these_days", "Acme Corporation", "Acme", "Ari works there these days."),
    (
        "hedging",
        "Acme Corporation",
        "Globex",
        "Maybe Ari works at Globex, but I am not sure.",
    ),
    ("dated_restatement", "Acme Corp", "Acme", "Ari restated this in 2025."),
)


def run_production_false_closure(seeds=range(1, 21)) -> dict:
    """Exercise record_assertion plus ReflectionService's real classifier path."""
    return asyncio.run(_run(seeds))


async def _run(seeds) -> dict:
    counts = {name: {"superseded": 0, "disputed": 0, "cases": 0} for name, *_ in _CASES}
    counts["second_person"] = {"superseded": 0, "disputed": 0, "cases": 0}
    e8_only = {"superseded": 0, "cases": 0}
    llm_only = {"superseded": 0, "cases": 0}
    service = ReflectionService.__new__(ReflectionService)
    service.vector = _FixedEmbedding()
    service.llm = _ConservativeLLM()
    previous = Config.MEMORY_TEMPORAL_TRUTH_ENABLED
    Config.MEMORY_TEMPORAL_TRUTH_ENABLED = True
    try:
        for seed in seeds:
            for name, old_value, new_value, context in _CASES:
                store = TemporalMemoryStore(":memory:")
                try:
                    old = _claim(f"old-{seed}", "Ari", old_value, 100.0)
                    new = _claim(f"new-{seed}", "Ari", new_value, 200.0)
                    e8_relation = classify_e8_temporal_relation(
                        old, new, similarity=1.0, context=context
                    )
                    e8_only["superseded"] += int(
                        e8_relation in {"UPDATE", "CORRECTION"}
                    )
                    e8_only["cases"] += 1
                    llm_relation = await service._classify_temporal_relation(
                        [old], new, context
                    )
                    llm_only["superseded"] += int(
                        llm_relation in {"UPDATE", "CORRECTION"}
                    )
                    llm_only["cases"] += 1
                    await store.store_belief(old)
                    await store.record_assertion(
                        new,
                        classifier_context=context,
                        classify_deterministic=service._classify_temporal_e8,
                        classify_ambiguous=service._classify_temporal_relation,
                    )
                    status = (await store.get_belief(old.record_id)).status
                    counts[name]["superseded"] += int(status == "SUPERSEDED")
                    counts[name]["disputed"] += int(status == "DISPUTED")
                    counts[name]["cases"] += 1
                finally:
                    await store.close()
            store = TemporalMemoryStore(":memory:")
            try:
                old = _claim(f"ari-{seed}", "Ari", "Acme", 100.0)
                other = _claim(f"bob-{seed}", "Bob", "Acme", 200.0)
                await store.store_belief(old)
                await store.record_assertion(other)
                status = (await store.get_belief(old.record_id)).status
                counts["second_person"]["superseded"] += int(status == "SUPERSEDED")
                counts["second_person"]["disputed"] += int(status == "DISPUTED")
                counts["second_person"]["cases"] += 1
            finally:
                await store.close()
    finally:
        Config.MEMORY_TEMPORAL_TRUTH_ENABLED = previous
    closed = sum(item["superseded"] for item in counts.values())
    disputed = sum(item["disputed"] for item in counts.values())
    total = sum(item["cases"] for item in counts.values())
    return {
        "arm": "production_record_assertion_with_offline_classifier_stub",
        "seeds": list(seeds),
        "false_closed": closed,
        "disputed": disputed,
        "still_true": total,
        "false_closure_rate": closed / total if total else 0.0,
        "categories": counts,
        "detector_arms": {
            "e8_deterministic_only": _rate(e8_only),
            "top3_llm_offline_stub_only": _rate(llm_only),
        },
        "llm": "offline conservative stub; no hosted model",
    }


def _rate(counts: dict) -> dict:
    """Return rate and raw numerator/denominator for one detector arm."""
    cases = counts["cases"]
    return {
        "false_closed": counts["superseded"],
        "still_true": cases,
        "false_closure_rate": counts["superseded"] / cases if cases else 0.0,
    }


def _claim(record_id: str, subject: str, value: str, effective_at: float):
    return BeliefRecord(
        record_id=record_id,
        subject=subject,
        predicate="works_at",
        object=value,
        valid_from=effective_at,
        recorded_at=effective_at + 1,
        confidence=0.9,
    )
