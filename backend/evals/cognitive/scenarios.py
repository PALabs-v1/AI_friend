"""Seeded, multi-week memory histories with ground-truth probes.

One `Scenario` is one simulated user's life with the companion: weeks of
small talk, a set of facts that matter (some emotional, some later updated),
facts that get mentioned again, and keyword "traps" (small talk that happens
to contain a word from a later question). Probes are asked at the end, against
the whole accumulated store, so every category competes with every other one
the way it would in production.

Everything is derived from `seed`; the same seed produces the same scenario
byte for byte, so a result can be reproduced and two retrieval policies can
be compared on identical input.

Probe categories (a probe can carry several):

* ``paraphrase`` / ``keyword`` -- query shape (see `corpus.Fact`).
* ``important`` -- target importance >= 0.75.
* ``emotional`` -- target |valence| >= 0.6.
* ``updated`` -- the fact changed; the old version is ``obsolete`` and must
  not outrank the new one.
* ``old`` -- target last mentioned more than 21 days before the probe.
* ``mood_negative`` -- same question asked while the agent is in a negative,
  high-arousal, high-cortisol state (tests mood-congruent intrusion and the
  stress-narrowed candidate pool).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from .corpus import FACTS, FILLER_FACET, Fact

# Neutral agent affect as production actually computes it: tonic cortisol is
# 0.5 - V/2 + 0.3*fatigue (agent_state.py), so a calm agent at V=0 already
# carries cortisol 0.5, not the search_memories default of 0.0.
NEUTRAL_AFFECT = (0.0, 0.5, 0.5)
NEGATIVE_AFFECT = (-0.7, 0.8, 0.85)

REGIMES = ("verbatim", "unique", "summary")
FLAT_IMPORTANCE = 0.6

_FILLER_TEMPLATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "food",
        "Had {x} for lunch today.",
        (
            "leftover pasta",
            "a sandwich",
            "dal and rice",
            "instant noodles",
            "a salad",
            "biryani",
            "idli",
            "a wrap",
            "soup",
        ),
    ),
    (
        "food",
        "Ordered {x} for dinner because I was tired.",
        ("pizza", "burgers", "dosa", "sushi", "shawarma", "fried rice"),
    ),
    (
        "weather",
        "It was {x} on the way home.",
        (
            "raining a little",
            "really humid",
            "windy",
            "surprisingly cold",
            "sunny",
            "foggy",
        ),
    ),
    (
        "work",
        "Spent the afternoon {x}.",
        (
            "in back to back meetings",
            "fixing a flaky test",
            "writing documentation",
            "reviewing pull requests",
            "answering emails",
            "debugging a deploy",
        ),
    ),
    (
        "hobbies",
        "Watched {x} last night.",
        (
            "a couple of sitcom episodes",
            "a cricket match",
            "an old movie",
            "a cooking show",
            "some stand-up comedy",
            "a documentary on space",
        ),
    ),
    (
        "hobbies",
        "Played {x} for an hour.",
        ("a video game", "chess online", "some badminton", "cards with neighbours"),
    ),
    (
        "home",
        "Finally {x} this evening.",
        (
            "did my laundry",
            "cleaned the kitchen",
            "watered the plants",
            "fixed the leaky tap",
            "reorganised my desk",
        ),
    ),
    (
        "health",
        "Went for a {x} after dinner.",
        ("short walk", "slow stroll", "quick jog", "walk around the block"),
    ),
    (
        "family",
        "Talked to my {x} on the phone for a bit.",
        ("cousin", "aunt", "uncle", "old neighbour"),
    ),
    (
        "pets",
        "Saw {x} at the park.",
        (
            "a cute dog",
            "a couple of stray cats",
            "a parrot",
            "someone walking three dogs",
        ),
    ),
)

# Consolidation writes a fresh narrative summary each time, never a verbatim
# repeat, so in the ``summary`` regime every stored text is wrapped in one of
# these frames. Repeated mentions of one fact therefore become several
# distinct, near-duplicate memories -- which is what production accumulates.
_SUMMARY_FRAMES = (
    "We chatted for a while and they told me: {s}",
    "They brought this up again today: {s}",
    "Catching up this evening, the conversation touched on this: {s}",
    "In passing they mentioned: {s}",
    "One thing from today's talk: {s}",
    "They shared something with me: {s}",
)

_TRAP_TEMPLATES = (
    "Read a news article that mentioned {w} in passing.",
    "Someone at the cafe was going on about {w} for ages.",
    "Saw a meme about {w} this morning.",
)

# Words too generic to make a believable trap (and mostly production stop
# words anyway).
_TRAP_SKIP = {
    "what",
    "when",
    "which",
    "does",
    "have",
    "with",
    "that",
    "this",
    "there",
    "been",
    "anyone",
    "anything",
    "these",
    "days",
    "each",
    "year",
    "usually",
    "right",
    "coming",
    "soon",
    "recently",
    "lately",
    "every",
    "much",
    "many",
}


@dataclass
class MemoryEvent:
    key: str
    text: str
    t_hours: float
    topic: str
    facet: str
    importance: float
    valence: float = 0.0
    emotion: float = 0.0
    tags: frozenset[str] = frozenset()


@dataclass
class Probe:
    key: str
    t_hours: float
    query: str
    topic: str
    facet: str
    relevant: frozenset[str]
    obsolete: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    affect: tuple[float, float, float] = NEUTRAL_AFFECT


@dataclass
class Scenario:
    name: str
    seed: int
    days: int
    events: list[MemoryEvent] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)

    def memory_keys(self) -> dict[str, str]:
        """Stored text -> memory key (repeated mentions share one key)."""
        return {e.text: e.key for e in self.events}

    def distinct_memories(self) -> int:
        return len({e.text for e in self.events})


def content_words(text: str) -> set[str]:
    """Lowercased words of 3+ letters, the unit production's cue boost uses."""
    return set(re.findall(r"\b\w{3,}\b", text.lower()))


def _filler_line(rng: random.Random) -> tuple[str, str]:
    topic, template, options = rng.choice(_FILLER_TEMPLATES)
    return topic, template.format(x=rng.choice(options))


def _frame(rng: random.Random, text: str, framed: bool) -> str:
    return rng.choice(_SUMMARY_FRAMES).format(s=text) if framed else text


def _trap_word(fact: Fact) -> str | None:
    words = sorted(content_words(fact.para) - _TRAP_SKIP)
    words = [w for w in words if len(w) >= 4]
    return words[0] if words else None


def build_history(
    seed: int,
    days: int = 60,
    n_facts: int = 18,
    filler_per_day: tuple[int, int] = (3, 7),
    traps: bool = True,
    mood_probes: bool = True,
    regime: str = "verbatim",
    distress_share: float = 0.0,
) -> Scenario:
    """Build one simulated history.

    ``regime`` controls the two properties that most change how the ACT-R
    frequency and importance terms behave, because production's write paths
    differ on exactly these:

    * ``verbatim`` -- small talk recurs word for word (exact duplicates are
      reinforced by `add_memory`, inflating recall_count); facts carry their
      authored importance. Upper bound on frequency effects.
    * ``unique`` -- every small-talk memory is a distinct two-sentence text
      (no duplicate reinforcement); authored importance kept.
    * ``summary`` -- unique texts *and* flat importance 0.6 for every
      conversation-derived memory, which is what consolidation actually
      writes (`learning.py:_consolidate_episodic_memory`). Closest to a
      production store populated by the subconscious agent.

    ``distress_share`` tags that fraction of small-talk memories with strong
    negative affect (valence -0.7, emotional weight 0.8), as consolidation
    does when a rough week colours every summary. It is the rumination
    stress test for any retrieval term that favours emotional memories
    regardless of the query.
    """
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}; expected one of {REGIMES}")
    rng = random.Random(seed)
    scenario = Scenario(name=f"history-{regime}-s{seed}-d{days}", seed=seed, days=days)
    flat = regime == "summary"
    probe_t = days * 24.0 + 12.0

    facts = rng.sample(list(FACTS), k=min(n_facts, len(FACTS)))
    # Every fact with an update is always included; updates are the category
    # most likely to expose a frequency-driven ranker.
    for fact in FACTS:
        if fact.update and fact not in facts:
            facts.append(fact)

    # Windows for preferences that later change: stated early, repeated for a
    # while, then updated. For the default 60-day history these are exactly
    # days 0-12 / up to 30 / 35-45 (so published results stay reproducible);
    # shorter histories scale them so every event precedes the probe.
    first_end = min(12.0, days * 0.2)
    repeat_end = min(30.0, days * 0.5)
    update_lo, update_hi = (
        (35.0, min(45.0, days - 2)) if days >= 47 else (days * 0.55, days * 0.75)
    )

    for fact in facts:
        key = f"fact:{fact.facet}"
        if fact.update:
            first_t = rng.uniform(0, first_end) * 24
            # People repeat a stable preference several times before it changes.
            mentions = [first_t] + sorted(
                rng.uniform(first_t / 24 + 1, repeat_end) * 24
                for _ in range(rng.randint(2, 4))
            )
        else:
            first_t = rng.uniform(0, days - 5) * 24
            extra = rng.choice((0, 0, 1, 2))
            mentions = [first_t] + sorted(
                rng.uniform(first_t / 24, days - 1) * 24 for _ in range(extra)
            )
        for t in mentions:
            text = _frame(rng, fact.text, flat)
            scenario.events.append(
                MemoryEvent(
                    key,
                    text,
                    t,
                    fact.topic,
                    fact.facet,
                    FLAT_IMPORTANCE if flat else fact.importance,
                    fact.valence,
                    fact.emotion,
                    frozenset({"fact"}),
                )
            )

        relevant_key = key
        obsolete: frozenset[str] = frozenset()
        last_mention = mentions[-1]
        if fact.update:
            new_key = f"fact:{fact.facet}:v2"
            t_update = rng.uniform(update_lo, update_hi) * 24
            scenario.events.append(
                MemoryEvent(
                    new_key,
                    _frame(rng, fact.update, flat),
                    t_update,
                    fact.topic,
                    fact.facet,
                    FLAT_IMPORTANCE if flat else fact.importance,
                    0.0,
                    0.0,
                    frozenset({"fact", "update"}),
                )
            )
            relevant_key = new_key
            obsolete = frozenset({key})
            last_mention = t_update

        cats = set()
        if fact.importance >= 0.75:
            cats.add("important")
        if abs(fact.valence) >= 0.6:
            cats.add("emotional")
        if fact.update:
            cats.add("updated")
        if probe_t - last_mention > 21 * 24:
            cats.add("old")

        for shape, query in (("paraphrase", fact.para), ("keyword", fact.kw)):
            scenario.probes.append(
                Probe(
                    f"{shape}:{fact.facet}",
                    probe_t,
                    query,
                    fact.topic,
                    fact.facet,
                    frozenset({relevant_key}),
                    obsolete,
                    frozenset(cats | {shape}),
                )
            )
            if mood_probes and shape == "paraphrase":
                scenario.probes.append(
                    Probe(
                        f"mood:{fact.facet}",
                        probe_t,
                        query,
                        fact.topic,
                        fact.facet,
                        frozenset({relevant_key}),
                        obsolete,
                        frozenset(cats | {shape, "mood_negative"}),
                        NEGATIVE_AFFECT,
                    )
                )

        if traps:
            word = _trap_word(fact)
            if word:
                text = rng.choice(_TRAP_TEMPLATES).format(w=word)
                scenario.events.append(
                    MemoryEvent(
                        f"trap:{fact.facet}",
                        text,
                        rng.uniform(days - 10, days - 0.5) * 24,
                        "misc",
                        FILLER_FACET,
                        FLAT_IMPORTANCE if flat else 0.25,
                        0.0,
                        0.0,
                        frozenset({"trap", "trivial"}),
                    )
                )

    seen: set[str] = set()
    for day in range(days):
        for _ in range(rng.randint(*filler_per_day)):
            if regime == "verbatim":
                topic, text = _filler_line(rng)
            else:
                for _attempt in range(50):
                    topic, first = _filler_line(rng)
                    _, second = _filler_line(rng)
                    text = _frame(rng, f"{first} {second}", flat)
                    if text not in seen and first != second:
                        break
                seen.add(text)
            importance = FLAT_IMPORTANCE if flat else round(rng.uniform(0.2, 0.35), 2)
            distressed = distress_share > 0 and rng.random() < distress_share
            scenario.events.append(
                MemoryEvent(
                    f"filler:{text}",
                    text,
                    (day + rng.random()) * 24,
                    topic,
                    FILLER_FACET,
                    importance,
                    -0.7 if distressed else 0.0,
                    0.8 if distressed else 0.0,
                    frozenset({"trivial", "distressed"} if distressed else {"trivial"}),
                )
            )

    scenario.events.sort(key=lambda e: e.t_hours)
    late = [e.key for e in scenario.events if e.t_hours >= probe_t]
    if late:
        raise AssertionError(f"events after probe time in {scenario.name}: {late[:3]}")
    return scenario


def register_embeddings(scenario: Scenario, embedder) -> None:
    """Teach the embedder every text the scenario will embed.

    Filler and traps share a per-text facet so they are topically related to
    facts in their topic but never share a fact's facet.
    """
    for event in scenario.events:
        facet = event.facet if "fact" in event.tags else f"{FILLER_FACET}:{event.text}"
        embedder.register(event.text, event.topic, facet)
    for probe in scenario.probes:
        embedder.register(probe.query, probe.topic, probe.facet)
