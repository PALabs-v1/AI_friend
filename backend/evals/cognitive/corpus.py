"""Hand-written content for the memory scenarios.

Content is data, not code, but it lives in Python so each entry's shape is
checked at import time and the lexical-overlap invariants below can be tested
(`tests/test_cognitive_bench.py`).

Every `Fact` is one thing a user might tell a companion, in one topic and one
facet. It carries two queries:

* `para` -- a paraphrase that shares **no content word** with the statement.
  It can only be answered by meaning (the embedding), never by keyword
  overlap. This is the query shape that exposes a lexical-first ranker.
* `kw` -- a keyword query that shares at least one content word with the
  statement, the easy case.

`update` (optional) is a later statement that supersedes `text` on the same
facet: a changed preference or a corrected fact.

Emotional facts carry valence in [-1, 1] and emotional weight (arousal-like
intensity) in [0, 1]; neutral facts leave both at 0.

`FILLER` is small talk: low-importance, spread over topics so it competes on
similarity as well as on recency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    topic: str
    facet: str
    text: str
    para: str
    kw: str
    importance: float = 0.6
    valence: float = 0.0
    emotion: float = 0.0
    update: str | None = None


FACTS: tuple[Fact, ...] = (
    # family
    Fact(
        "family",
        "sister_birthday",
        "My sister Priya's birthday is on March 14.",
        "When does my sibling celebrate getting older each year?",
        "When is Priya's birthday?",
        0.75,
    ),
    Fact(
        "family",
        "brother_job",
        "My brother Rohan just started working as a nurse in Pune.",
        "What does my male sibling do for a living these days?",
        "What job did Rohan start?",
        0.6,
    ),
    Fact(
        "family",
        "grandfather_death",
        "My grandfather passed away last spring and I still miss him every day.",
        "Has anyone close to me died recently?",
        "What happened to my grandfather?",
        0.9,
        -0.85,
        0.9,
    ),
    Fact(
        "family",
        "mom_visit",
        "My mom is flying in from Kolkata to stay with me next month.",
        "Is a parent coming to visit soon?",
        "When is my mom flying in?",
        0.65,
        0.5,
        0.4,
    ),
    # health
    Fact(
        "health",
        "peanut_allergy",
        "I have a severe peanut allergy and carry an EpiPen everywhere.",
        "Is there any food that could send me to the hospital?",
        "Do I have an allergy?",
        0.9,
        -0.2,
        0.5,
    ),
    Fact(
        "health",
        "knee_surgery",
        "I had knee surgery in January and I'm still doing physiotherapy.",
        "Did I have an operation on a joint recently?",
        "How is my knee after surgery?",
        0.75,
        -0.4,
        0.5,
    ),
    Fact(
        "health",
        "insomnia",
        "I've been struggling with insomnia and only sleep four hours a night.",
        "Have I had trouble resting at bedtime?",
        "How many hours do I sleep?",
        0.6,
        -0.5,
        0.6,
    ),
    Fact(
        "health",
        "running_goal",
        "I'm training to run my first half marathon in November.",
        "What long-distance race am I preparing for?",
        "When is my half marathon?",
        0.7,
        0.6,
        0.6,
    ),
    # work
    Fact(
        "work",
        "promotion",
        "I got promoted to senior engineer at Infosys last week!",
        "Did anything good happen with my career lately?",
        "Was I promoted?",
        0.85,
        0.9,
        0.9,
    ),
    Fact(
        "work",
        "difficult_manager",
        "My manager Suresh keeps taking credit for my work in meetings.",
        "Is my boss treating me unfairly?",
        "What does Suresh do in meetings?",
        0.65,
        -0.7,
        0.7,
    ),
    Fact(
        "work",
        "commute",
        "I take the metro to the office, it's about forty minutes each way.",
        "How do I get to my workplace every day?",
        "How long is my metro commute?",
        0.4,
    ),
    Fact(
        "work",
        "layoff_fear",
        "There are rumours of layoffs at my company and I'm really anxious about it.",
        "Am I worried about losing my position?",
        "What rumours are going around my company?",
        0.7,
        -0.75,
        0.8,
    ),
    # pets
    Fact(
        "pets",
        "dog_name",
        "My dog is a golden retriever called Biscuit.",
        "What is my canine companion named?",
        "What breed is Biscuit?",
        0.7,
        0.6,
        0.4,
    ),
    Fact(
        "pets",
        "cat_vet",
        "My cat Mochi had to go to the vet for a kidney infection.",
        "Was one of my animals ill and needing treatment?",
        "Why did Mochi go to the vet?",
        0.65,
        -0.5,
        0.6,
    ),
    # food / preferences (with updates)
    Fact(
        "food",
        "morning_drink",
        "I drink two cups of strong coffee every morning.",
        "What beverage do I usually have after waking up?",
        "How much coffee do I drink?",
        0.5,
        update="I quit coffee completely and switched to green tea.",
    ),
    Fact(
        "food",
        "diet",
        "I eat chicken pretty much every day.",
        "What kind of meals do I usually prefer?",
        "Do I eat chicken?",
        0.5,
        update="I've gone fully vegetarian now, no meat at all.",
    ),
    Fact(
        "food",
        "favorite_cuisine",
        "My favourite cuisine is Thai, especially green curry.",
        "Which country's dishes do I enjoy most?",
        "What is my favourite cuisine?",
        0.45,
    ),
    # home
    Fact(
        "home",
        "city",
        "I live in a small apartment in Bangalore.",
        "Which city is my place in?",
        "Where is my apartment?",
        0.7,
        update="I moved to Hyderabad last month for the new job.",
    ),
    Fact(
        "home",
        "roommate",
        "My roommate Arjun plays guitar late at night.",
        "Who shares my flat and makes noise after dark?",
        "What does Arjun play?",
        0.45,
        -0.2,
        0.3,
    ),
    # hobbies
    Fact(
        "hobbies",
        "guitar_learning",
        "I'm learning to play the violin on weekends.",
        "Which musical instrument am I picking up?",
        "What instrument am I learning?",
        0.55,
        0.4,
        0.3,
    ),
    Fact(
        "hobbies",
        "reading",
        "I'm reading The Brothers Karamazov, slowly.",
        "Which novel is on my nightstand right now?",
        "What book am I reading?",
        0.4,
    ),
    Fact(
        "hobbies",
        "trek",
        "I'm planning a trek to Kedarkantha with college friends in December.",
        "Do I have a mountain hiking trip coming up?",
        "Where is my trek?",
        0.65,
        0.7,
        0.6,
    ),
    # relationships
    Fact(
        "relationships",
        "breakup",
        "My girlfriend Meera and I broke up after three years.",
        "Did a long romance of mine end?",
        "What happened with Meera?",
        0.9,
        -0.9,
        0.95,
    ),
    Fact(
        "relationships",
        "best_friend",
        "My best friend Kabir is moving to Canada for his master's.",
        "Is my closest buddy relocating abroad?",
        "Where is Kabir moving?",
        0.7,
        -0.3,
        0.5,
    ),
    # goals
    Fact(
        "goals",
        "save_house",
        "I'm saving up to buy a house in the next five years.",
        "What big purchase am I putting money aside for?",
        "What am I saving for?",
        0.65,
        0.3,
        0.3,
    ),
    Fact(
        "goals",
        "learn_spanish",
        "I want to learn Spanish before my trip to Madrid.",
        "Which foreign language do I hope to pick up?",
        "Why do I want to learn Spanish?",
        0.55,
        0.4,
        0.3,
    ),
)


# Small talk: low importance, spread over the same topics so it competes on
# similarity (same topic, different facet) as well as on recency.
FILLER: tuple[tuple[str, str], ...] = (
    ("weather", "It rained a little on the way home today."),
    ("weather", "It's really humid this afternoon."),
    ("weather", "The sky was beautiful at sunset."),
    ("weather", "It's supposed to be cloudy tomorrow."),
    ("food", "I had leftover pasta for lunch."),
    ("food", "Made some instant noodles tonight."),
    ("food", "Grabbed a sandwich from the corner shop."),
    ("food", "Ordered pizza because I was too tired to cook."),
    ("food", "Tried a new bakery near the station."),
    ("work", "Had back to back meetings all morning."),
    ("work", "Spent the day fixing a flaky test."),
    ("work", "The office air conditioning was broken again."),
    ("work", "Wrote some documentation this afternoon."),
    ("work", "Lunch break was short today."),
    ("hobbies", "Watched a couple of episodes of a sitcom."),
    ("hobbies", "Played a video game for an hour."),
    ("hobbies", "Scrolled through videos for way too long."),
    ("hobbies", "Listened to a podcast about history."),
    ("home", "Did my laundry this evening."),
    ("home", "Cleaned the kitchen finally."),
    ("home", "The neighbours were loud again."),
    ("home", "Watered the plants on the balcony."),
    ("health", "Went for a short walk after dinner."),
    ("health", "Felt a bit tired this morning."),
    ("health", "Drank more water today than usual."),
    ("family", "Talked to my cousin on the phone for a bit."),
    ("family", "Saw some old family photos online."),
    ("pets", "Saw a cute dog at the park."),
    ("pets", "A stray cat was sitting outside my building."),
    ("goals", "Thought about making a budget spreadsheet."),
)


# Topics whose filler must not share a facet with any fact.
FILLER_FACET = "smalltalk"


def facts_by_topic() -> dict[str, list[Fact]]:
    out: dict[str, list[Fact]] = {}
    for fact in FACTS:
        out.setdefault(fact.topic, []).append(fact)
    return out
