"""The persona parameter space and a fixed panel of archetypes.

Every parameter is a knob on a measurable output (R7): verbosity moves words
per turn, interaction_frequency moves sessions per week, contradiction_rate
moves the misstatement rate, and so on. The panel spans the space on purpose
so no mechanism gets tuned to one kind of person.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, replace

CHRONOTYPES = ("morning", "evening", "night", "flat")
STYLES = ("terse", "plain", "chatty", "formal", "playful", "rambling")


@dataclass(frozen=True)
class Persona:
    archetype: str
    # 0..1 unless noted
    verbosity: float  # words per turn, clauses per mention
    forgetfulness: float  # re-telling already-told facts, "did I tell you"
    contradiction_rate: float  # accidental misstatement per fact mention
    deception_rate: float  # deliberate falsehood later corrected
    hedging: float  # "I think", "maybe" on uncertain facts
    humor: float  # jokes, including joking false claims
    emotionality: float  # intensity of emotional language and events
    network_size: int  # people in the social graph, 6..40
    lifestyle_complexity: float  # projects, trips, commitments, restaurants
    volatility: float  # preference drift rate
    interaction_frequency: float  # expected sessions per day, 0.15..4
    chronotype: str
    style: str

    def to_json(self) -> dict:
        return asdict(self)


ARCHETYPES: dict[str, Persona] = {
    p.archetype: p
    for p in (
        Persona(
            "steady_professional",
            0.4,
            0.2,
            0.03,
            0.01,
            0.2,
            0.2,
            0.3,
            14,
            0.5,
            0.2,
            1.2,
            "morning",
            "plain",
        ),
        Persona(
            "chatty_student",
            0.8,
            0.4,
            0.08,
            0.03,
            0.4,
            0.7,
            0.6,
            28,
            0.6,
            0.7,
            3.0,
            "night",
            "chatty",
        ),
        Persona(
            "forgetful_retiree",
            0.7,
            0.9,
            0.15,
            0.0,
            0.5,
            0.3,
            0.5,
            18,
            0.3,
            0.2,
            2.0,
            "morning",
            "rambling",
        ),
        Persona(
            "volatile_creative",
            0.6,
            0.4,
            0.07,
            0.05,
            0.4,
            0.6,
            0.8,
            22,
            0.7,
            0.95,
            1.5,
            "night",
            "playful",
        ),
        Persona(
            "terse_engineer",
            0.1,
            0.1,
            0.02,
            0.0,
            0.1,
            0.1,
            0.15,
            8,
            0.4,
            0.15,
            0.8,
            "evening",
            "terse",
        ),
        Persona(
            "emotional_caregiver",
            0.7,
            0.3,
            0.05,
            0.01,
            0.3,
            0.3,
            0.95,
            20,
            0.5,
            0.3,
            1.8,
            "morning",
            "chatty",
        ),
        Persona(
            "busy_parent",
            0.3,
            0.5,
            0.1,
            0.02,
            0.3,
            0.3,
            0.6,
            24,
            0.9,
            0.3,
            0.6,
            "evening",
            "plain",
        ),
        Persona(
            "night_owl_gamer",
            0.4,
            0.3,
            0.06,
            0.08,
            0.3,
            0.8,
            0.4,
            10,
            0.3,
            0.5,
            2.5,
            "night",
            "playful",
        ),
        Persona(
            "socialite",
            0.9,
            0.3,
            0.06,
            0.04,
            0.3,
            0.6,
            0.7,
            40,
            0.8,
            0.6,
            2.2,
            "evening",
            "chatty",
        ),
        Persona(
            "private_minimalist",
            0.15,
            0.1,
            0.02,
            0.0,
            0.2,
            0.1,
            0.2,
            6,
            0.15,
            0.1,
            0.25,
            "flat",
            "terse",
        ),
        Persona(
            "traveling_consultant",
            0.5,
            0.3,
            0.05,
            0.02,
            0.5,
            0.4,
            0.4,
            16,
            0.95,
            0.4,
            0.4,
            "flat",
            "formal",
        ),
        Persona(
            "anxious_hedger",
            0.6,
            0.5,
            0.12,
            0.01,
            0.95,
            0.2,
            0.8,
            12,
            0.4,
            0.4,
            1.6,
            "evening",
            "rambling",
        ),
    )
}

PANEL: tuple[str, ...] = tuple(ARCHETYPES)


def draw_random(rng: random.Random) -> Persona:
    return Persona(
        archetype="random",
        verbosity=round(rng.uniform(0.05, 0.95), 3),
        forgetfulness=round(rng.uniform(0.0, 0.95), 3),
        contradiction_rate=round(rng.uniform(0.0, 0.15), 3),
        deception_rate=round(rng.uniform(0.0, 0.08), 3),
        hedging=round(rng.uniform(0.05, 0.95), 3),
        humor=round(rng.uniform(0.0, 0.9), 3),
        emotionality=round(rng.uniform(0.1, 0.95), 3),
        network_size=rng.randint(6, 40),
        lifestyle_complexity=round(rng.uniform(0.1, 0.95), 3),
        volatility=round(rng.uniform(0.05, 0.95), 3),
        interaction_frequency=round(rng.uniform(0.15, 4.0), 3),
        chronotype=rng.choice(CHRONOTYPES),
        style=rng.choice(STYLES),
    )


def resolve(archetype: str, rng: random.Random) -> Persona:
    if archetype == "random":
        return draw_random(rng)
    if archetype not in ARCHETYPES:
        raise KeyError(
            f"unknown archetype {archetype!r}; choose from {', '.join(PANEL)} or 'random'"
        )
    return ARCHETYPES[archetype]


def with_overrides(persona: Persona, **kw) -> Persona:
    return replace(persona, **kw)
