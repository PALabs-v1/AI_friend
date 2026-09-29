"""Offline preloaded-fact slice for the temporal switchboard arm.

This isolates retrieval/versioning from LLM-produced consolidation. It is not
the BrainBench memory suite, which remains `llm_augmented` per DR-037.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.cognitive.pipeline import CognitivePipeline
from app.state.memory_records import BeliefRecord
from app.state.temporal_store import TemporalMemoryStore
from evals.brainbench.switchboard import apply_arm, resolve_arm


def _belief(record_id: str, subject: str, city: str, valid_from: float):
    return BeliefRecord(
        record_id=record_id,
        subject=subject,
        predicate="lives_in",
        object=city,
        valid_from=valid_from,
        recorded_at=valid_from,
    )


async def run_slice(size: int = 20) -> dict:
    """Compare flag-off and flag-on current/history behavior on fixed claims."""
    store = TemporalMemoryStore(":memory:")
    pipeline = CognitivePipeline.__new__(CognitivePipeline)
    pipeline.temporal_memory_store = store
    cases = []
    try:
        for index in range(size):
            subject = f"User{index}"
            old_value, new_value = f"OldCity{index}", f"NewCity{index}"
            old = _belief(f"old-{index}", subject, old_value, 10.0)
            new_start = 10.0 if index % 2 else 20.0
            new = _belief(f"new-{index}", subject, new_value, new_start).model_copy(
                update={"recorded_at": new_start + 1.0}
            )
            await store.store_belief(old)
            relation = await store.record_assertion(
                new, explicit_correction=index % 2 == 1
            )
            cases.append((subject, old_value, new_value, relation))

        output = {}
        for arm_name in ("-temporal", "+temporal"):
            current_hits = history_hits = correction_leaks = 0
            with apply_arm(resolve_arm(arm_name)):
                for index, (subject, old, new, relation) in enumerate(cases):
                    (
                        current,
                        _obsolete,
                        _ambiguous,
                    ) = await pipeline._temporal_belief_memories(
                        f"Where does {subject} live now?"
                    )
                    (
                        history,
                        _past_obsolete,
                        _past_ambiguous,
                    ) = await pipeline._temporal_belief_memories(
                        f"Where did {subject} used to live?"
                    )
                    current_text = " ".join(row["content"] for row in current)
                    history_text = " ".join(row["content"] for row in history)
                    current_hits += int(new in current_text and old not in current_text)
                    if relation == "UPDATE":
                        history_hits += int(old in history_text)
                    else:
                        correction_leaks += int(old in history_text)
            output[arm_name] = {
                "current_accuracy": current_hits / size,
                "historical_update_hit_rate": history_hits / (size // 2),
                "correction_history_leak_rate": correction_leaks / (size // 2),
                "cases": size,
            }
        return {
            "suite": "preloaded_temporal_truth_dev",
            "mode": "architecture_only",
            "size": size,
            "arms": output,
            "scope": "fixed typed claims; no LLM encoding or consolidation",
        }
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run_slice(args.size))
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
