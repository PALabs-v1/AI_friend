"""Deterministic memory and recall-probe extraction from lifesim dev data."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from evals.lifesim.generate import build
from evals.lifesim.personas import PANEL
from evals.lifesim.splits import SPLITS, authorize


@dataclass(frozen=True)
class MemoryRow:
    memory_id: str
    content: str
    seed: int
    archetype: str
    turn: int
    timestamp: str


@dataclass(frozen=True)
class ProbeRow:
    probe_id: str
    text: str
    support_memory_ids: tuple[str, ...]


@dataclass(frozen=True)
class GraphEdge:
    seed: int
    source: str
    relation: str
    target: str


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_corpus(
    size: int,
    seeds: list[int] | tuple[int, ...],
    *,
    horizon: str = "10y",
    allow_partial: bool = False,
) -> tuple[list[MemoryRow], list[ProbeRow], list[GraphEdge]]:
    """Build the first `size` distinct turn texts in stable seed/turn order."""
    if size <= 0:
        raise ValueError("size must be positive")
    if not seeds:
        raise ValueError("at least one dev seed is required")
    for seed in seeds:
        if seed not in SPLITS["dev"]:
            raise ValueError(f"seed {seed} is outside the dev split 1000-1099")
        authorize(seed)

    memories: list[MemoryRow] = []
    probes: list[ProbeRow] = []
    graph_edges: list[GraphEdge] = []
    seen: dict[str, str] = {}
    seed_count = min(len(seeds), max(12, (size + 9_999) // 10_000))
    active_seeds = seeds[:seed_count]
    per_seed_quota = (size + seed_count - 1) // seed_count
    for seed_index, seed in enumerate(active_seeds):
        archetype = PANEL[seed_index % len(PANEL)]
        simulation, turns, _, probe_records, answers = build(seed, archetype, horizon)
        world = simulation.world
        graph_edges.extend(
            GraphEdge(seed, world.user_name, person.relation.upper(), person.name)
            for person in world.people.values()
        )
        graph_edges.extend(
            GraphEdge(seed, world.user_name, "OWNS", pet.name)
            for pet in world.pets.values()
        )
        graph_edges.extend(
            GraphEdge(
                seed, person.name, "PARTNER_OF", world.people[person.partner_of].name
            )
            for person in world.people.values()
            if person.partner_of in world.people
        )
        turn_by_id = {turn.turn_id: turn for turn in turns}
        seed_memories: list[MemoryRow] = []
        for index, turn in enumerate(turns):
            content_key = _key(turn.text)
            if (
                not content_key
                or content_key in seen
                or len(seed_memories) >= per_seed_quota
            ):
                continue
            memory_id = f"ls-{seed}-{index:07d}"
            seen[content_key] = memory_id
            seed_memories.append(
                MemoryRow(
                    memory_id=memory_id,
                    content=turn.text,
                    seed=seed,
                    archetype=archetype,
                    turn=index,
                    timestamp=turn.t.isoformat(),
                )
            )
        support_by_probe = {
            answer.probe_id: tuple(
                seen[_key(turn_by_id[turn_id].text)]
                for turn_id in answer.support_turn_ids
                if turn_id in turn_by_id and _key(turn_by_id[turn_id].text) in seen
            )
            for answer in answers
            if answer.expected == "answer"
        }
        for probe in probe_records:
            support = support_by_probe.get(probe.probe_id, ())
            if support:
                probes.append(ProbeRow(probe.probe_id, probe.text, support))
        memories.extend(seed_memories)
        if len(memories) >= size:
            memories = memories[:size]
            break
    if len(memories) < size and not allow_partial:
        raise ValueError(
            f"requested {size} unique memories, got {len(memories)}; add dev seeds or increase horizon"
        )
    used_ids = {row.memory_id for row in memories}
    probes = [
        probe
        for probe in probes
        if any(memory_id in used_ids for memory_id in probe.support_memory_ids)
    ]
    return memories, probes, graph_edges


def write_jsonl(path: Path, records) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(asdict(row), sort_keys=True, separators=(",", ":")) + "\n"
        for row in records
    ).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()
