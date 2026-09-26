"""When the human talks to the robot.

A turn is never a fixed slice of time. Sessions follow the persona's
interaction frequency, modulated by:

* chronotype -- session hours cluster where this person is awake and home;
* weekday/weekend -- weekends shift the rhythm;
* bursty weeks -- a per-week multiplier drawn from a Gamma(6, 1/6)
  (mean 1) clamped to [0.5, 2.5], so some weeks are busy and some are
  quiet, but a quiet week at home is never a silent one: long silences must
  have a cause in the life (a trip), not in the random numbers;
* a house guest -- attention goes to the visitor, fewer sessions;
* absences -- the robot lives at home, so while the user is on a trip there
  are no sessions at all. Silences are caused by modelled trips, not random
  holes (R6);
* illness -- more time at home, more sessions.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from .personas import Persona
from .rng import stream
from .timeline import Timeline

# (hour mean, hour sd, weight) mixtures per chronotype, local time
CHRONO: dict[str, tuple[tuple[float, float, float], ...]] = {
    "morning": ((7.5, 1.0, 0.45), (13.0, 1.5, 0.15), (19.5, 1.5, 0.40)),
    "evening": ((8.5, 1.0, 0.15), (13.0, 1.5, 0.15), (20.5, 1.7, 0.70)),
    "night": ((11.0, 1.5, 0.15), (17.0, 2.0, 0.25), (23.0, 1.8, 0.60)),
    "flat": ((10.0, 2.5, 0.5), (18.5, 2.5, 0.5)),
}
TURNS: dict[str, tuple[int, int]] = {
    "terse": (1, 3),
    "plain": (2, 5),
    "formal": (2, 5),
    "playful": (2, 7),
    "chatty": (3, 10),
    "rambling": (3, 12),
}


@dataclass(frozen=True)
class Session:
    session_id: str
    start: datetime
    base_turns: int


def _poisson(r: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= limit:
            return k
        k += 1


def _hour(r: random.Random, chronotype: str) -> float:
    mix = CHRONO[chronotype]
    pick = r.random() * sum(w for _, _, w in mix)
    for mean, sd, w in mix:
        pick -= w
        if pick <= 0:
            break
    return r.gauss(mean, sd) % 24


def build_sessions(
    seed: int, persona: Persona, timeline: Timeline, start: datetime, end: datetime
) -> list[Session]:
    r = stream(seed, "schedule")
    sessions: list[Session] = []
    lo, hi = TURNS[persona.style]
    day = start.date()
    week_mult: dict[tuple[int, int], float] = {}
    n = 0
    while datetime(day.year, day.month, day.day) < end:
        week = day.isocalendar()[:2]
        if week not in week_mult:
            week_mult[week] = min(2.5, max(0.5, r.gammavariate(6.0, 1 / 6)))
        lam = (
            persona.interaction_frequency
            * week_mult[week]
            * (1.15 if day.weekday() >= 5 else 1.0)
        )
        noon = datetime(day.year, day.month, day.day, 12)
        if timeline.current("user", "health", noon) is not None:
            lam *= 1.5
        if timeline.current("user", "house_guest", noon) is not None:
            lam *= 0.6
        # Habit: a person who lives with the robot has a usual moment most days
        # (a morning chat, an evening debrief), so a day at home is rarely
        # silent. The anchor is a Bernoulli share of the daily rate, the rest
        # is Poisson, and the mean stays exactly `lam`.
        anchor = min(0.9, 0.7 * lam)
        count = (1 if r.random() < anchor else 0) + _poisson(r, lam - anchor)
        hours = sorted(_hour(r, persona.chronotype) for _ in range(count))
        last: datetime | None = None
        for h in hours:
            t = datetime(day.year, day.month, day.day) + timedelta(minutes=int(h * 60))
            if last is not None and t - last < timedelta(minutes=30):
                t = last + timedelta(minutes=30 + r.randint(0, 40))
            if t < start or t >= end or t.date() != day:
                continue
            if timeline.current("user", "away_city", t) is not None:
                continue
            last = t
            n += 1
            # Geometric-ish session length: most sessions short, a few long.
            k = lo + min(hi - lo, int(r.expovariate(1.0 / max(1.0, (hi - lo) / 2.5))))
            sessions.append(Session(f"s{n:06d}", t, k))
        day += timedelta(days=1)
    return sessions
