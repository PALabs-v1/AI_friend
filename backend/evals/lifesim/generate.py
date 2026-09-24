"""Truth first, text last: the generation pipeline.

    persona -> world -> events (+timeline) -> sessions -> observe -> probes -> files

`simulate_truth` builds everything that is true. `observe.render` turns it
into what the user actually said; `probes.build` asks questions whose answers
are derived from the truth, never from the text. `generate` writes the
public/oracle split to disk with a manifest of sha256 hashes.

Horizons are either calendar spans (``1w``, ``6m``, ``10y``) or turn counts
(``500t``). The calendar walk is prefix-stable -- every process draws from its
own stream one day at a time -- so a turn horizon is produced by simulating
up to the moment of the Nth turn, which yields exactly the first N turns of any
longer run of the same seed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

from . import banks as banks_mod
from .banks import Bank
from .events import Event, simulate
from .personas import Persona, resolve
from .rng import stream
from .schedule import TURNS, Session, build_sessions
from .schema import write_json, write_jsonl
from .splits import authorize
from .timeline import Timeline
from .world import World, build_world

GENERATOR_VERSION = "lifesim-1"

_H = re.compile(r"^(\d+)([twmyd])$")
_DAYS = {"d": 1, "w": 7, "m": 30.44, "y": 365.25}


@dataclass(frozen=True)
class Horizon:
    label: str
    days: int | None = None
    turns: int | None = None


def parse_horizon(label: str) -> Horizon:
    m = _H.match(label.strip().lower())
    if not m:
        raise ValueError(f"bad horizon {label!r}: use e.g. 100t, 1w, 1m, 6m, 1y, 10y")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError("horizon must be positive")
    if unit == "t":
        return Horizon(label, turns=n)
    return Horizon(label, days=max(1, round(n * _DAYS[unit])))


@dataclass
class Simulation:
    seed: int
    split: str
    archetype: str
    horizon: Horizon
    persona: Persona
    world: World
    events: list[Event]
    sessions: list[Session]
    start: datetime
    end: datetime
    bank: Bank
    extras: dict = field(default_factory=dict)

    @property
    def timeline(self) -> Timeline:
        return self.world.timeline

    @cached_property
    def events_by_id(self) -> dict[str, Event]:
        return {e.event_id: e for e in self.events}

    def rng(self, *names: str):
        return stream(self.seed, *names)


def simulate_truth(
    seed: int,
    archetype: str,
    horizon: Horizon,
    bank: Bank,
    split: str,
    end: datetime | None = None,
) -> Simulation:
    persona = resolve(archetype, stream(seed, "persona"))
    world = build_world(seed, persona)
    if end is None:
        if horizon.days is not None:
            end = world.start + timedelta(days=horizon.days)
        else:
            lo, hi = TURNS[persona.style]
            per_day = persona.interaction_frequency * (lo + hi) / 2
            end = world.start + timedelta(
                days=math.ceil(horizon.turns / per_day * 1.6) + 7
            )
    events = simulate(world, end)
    world.timeline.validate()
    sessions = build_sessions(seed, persona, world.timeline, world.start, end)
    return Simulation(
        seed,
        split,
        archetype,
        horizon,
        persona,
        world,
        events,
        sessions,
        world.start,
        end,
        bank,
    )


def build(
    seed: int,
    archetype: str,
    horizon_label: str,
    *,
    final_run: bool = False,
    bank: Bank | None = None,
    heldout_log: Path | None = None,
):
    """Simulate, observe and probe. Returns (sim, turns, annotations, probes, answers)."""
    from . import (  # the text layer depends on the truth layer, never the reverse
        observe,
        probes,
    )

    horizon = parse_horizon(horizon_label)
    split = authorize(
        seed,
        final_run=final_run,
        log_path=heldout_log,
        note=f"lifesim generate archetype={archetype} horizon={horizon.label}",
    )
    bank = bank or banks_mod.load()
    sim = simulate_truth(seed, archetype, horizon, bank, split)
    turns, annotations = observe.render(sim)
    if horizon.turns is not None:
        tries = 0
        while len(turns) < horizon.turns:
            tries += 1
            if tries > 8:
                raise RuntimeError(
                    f"could not reach {horizon.turns} turns for seed {seed}"
                )
            sim = simulate_truth(
                seed,
                archetype,
                horizon,
                bank,
                split,
                end=sim.start + (sim.end - sim.start) * 2,
            )
            turns, annotations = observe.render(sim)
        cut = turns[horizon.turns - 1].t + timedelta(minutes=5)
        sim = simulate_truth(seed, archetype, horizon, bank, split, end=cut)
        turns, annotations = observe.render(sim)
        keep = {t.turn_id for t in turns[: horizon.turns]}
        turns = [t for t in turns if t.turn_id in keep]
        annotations = [a for a in annotations if a.turn_id in keep]
    probe_rows, answers = probes.build(sim, turns, annotations)
    return sim, turns, annotations, probe_rows, answers


def generate(
    seed: int,
    archetype: str,
    horizon_label: str,
    out: Path,
    *,
    final_run: bool = False,
    bank: Bank | None = None,
    heldout_log: Path | None = None,
) -> dict:
    sim, turns, annotations, probe_rows, answers = build(
        seed,
        archetype,
        horizon_label,
        final_run=final_run,
        bank=bank,
        heldout_log=heldout_log,
    )
    out = Path(out)
    files = {
        "public/turns.jsonl": write_jsonl(
            out / "public/turns.jsonl", [t.to_json() for t in turns]
        ),
        "public/probes.jsonl": write_jsonl(
            out / "public/probes.jsonl", [p.to_json() for p in probe_rows]
        ),
        "oracle/world.json": write_json(out / "oracle/world.json", sim.world.to_json()),
        "oracle/timeline.jsonl": write_jsonl(
            out / "oracle/timeline.jsonl", [a.to_json() for a in sim.timeline]
        ),
        "oracle/events.jsonl": write_jsonl(
            out / "oracle/events.jsonl", [e.to_json() for e in sim.events]
        ),
        "oracle/sessions.jsonl": write_jsonl(
            out / "oracle/sessions.jsonl",
            [
                {
                    "session_id": s.session_id,
                    "start": s.start.isoformat(),
                    "base_turns": s.base_turns,
                }
                for s in sim.sessions
            ],
        ),
        "oracle/annotations.jsonl": write_jsonl(
            out / "oracle/annotations.jsonl", [a.to_json() for a in annotations]
        ),
        "oracle/answers.jsonl": write_jsonl(
            out / "oracle/answers.jsonl", [a.to_json() for a in answers]
        ),
    }
    manifest = {
        "generator": GENERATOR_VERSION,
        "seed": seed,
        "split": sim.split,
        "archetype": archetype,
        "horizon": sim.horizon.label,
        "start": sim.start.isoformat(),
        "end": sim.end.isoformat(),
        "counts": {
            "turns": len(turns),
            "sessions": len({t.session_id for t in turns}),
            "events": len(sim.events),
            "assertions": len(sim.timeline),
            "probes": len(probe_rows),
            "people": len(sim.world.people),
        },
        "files": files,
        "banks": banks_mod.verify_manifest() if bank is None else {},
    }
    write_json(out / "manifest.json", manifest)
    return manifest
