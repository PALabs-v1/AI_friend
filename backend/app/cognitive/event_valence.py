"""Signed valence of one user message, for the appraisal's goal congruence.

Deterministic and stateless: a small lexicon with negation and intensifier
handling. It never reads agent state, so the mood path cannot feed itself
through it (NF-12). Returns a float in [-1, 1]; 0.0 when no cue word is found.
"""

from __future__ import annotations

import math
import re


def _lexicon(spec: str) -> dict[str, float]:
    """`"word:weight word:weight"` -> `{word: weight}`."""
    return {w: float(v) for w, v in (item.split(":") for item in spec.split())}


# Cue weights in [-1, 1]. Words are matched exactly after lowercasing, so the
# inflections that matter are listed rather than stemmed.
_POSITIVE = _lexicon(
    "thanks:0.6 thank:0.6 appreciate:0.6 grateful:0.7 love:0.8 loved:0.8 "
    "loves:0.8 adore:0.8 happy:0.8 glad:0.6 joy:0.7 excited:0.7 thrilled:0.8 "
    "great:0.7 good:0.5 wonderful:0.8 amazing:0.8 awesome:0.8 excellent:0.8 "
    "fantastic:0.8 perfect:0.7 nice:0.5 lovely:0.7 brilliant:0.7 fun:0.5 "
    "enjoy:0.6 enjoyed:0.6 helped:0.5 helpful:0.5 helps:0.5 better:0.4 "
    "best:0.7 proud:0.6 relieved:0.5 laugh:0.6 laughed:0.6 laughing:0.6 "
    "smile:0.5 promotion:0.5 won:0.6 win:0.5 congratulations:0.7 sweet:0.5 "
    "kind:0.4 beautiful:0.7 hope:0.3 hopeful:0.5 calm:0.4 comfortable:0.4 "
    "safe:0.4 yay:0.7 cheers:0.5"
)
_NEGATIVE = _lexicon(
    "hate:-0.8 hated:-0.8 hates:-0.8 despise:-0.8 terrible:-0.8 awful:-0.8 "
    "horrible:-0.8 worst:-0.8 bad:-0.5 sad:-0.6 angry:-0.7 furious:-0.8 "
    "mad:-0.5 upset:-0.6 annoyed:-0.5 annoying:-0.5 frustrated:-0.6 "
    "frustrating:-0.6 useless:-0.7 stupid:-0.7 dumb:-0.6 idiot:-0.8 "
    "worthless:-0.8 pathetic:-0.7 garbage:-0.6 wrong:-0.4 lost:-0.5 lose:-0.4 "
    "fired:-0.6 failed:-0.6 fail:-0.5 failure:-0.6 hurt:-0.6 hurts:-0.6 "
    "cry:-0.5 crying:-0.5 lonely:-0.6 alone:-0.3 tired:-0.4 exhausted:-0.5 "
    "worried:-0.5 anxious:-0.5 afraid:-0.5 scared:-0.5 stressed:-0.5 "
    "miserable:-0.8 depressed:-0.7 disappointed:-0.6 disappointing:-0.6 "
    "sorry:-0.2 ugly:-0.6 disgusting:-0.8 died:-0.7 dead:-0.5 "
    "pain:-0.5 ignored:-0.5 unfair:-0.5 cost:-0.2"
)
# Phrases are matched before single words; they carry a meaning the words do not.
_PHRASES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("cannot", "wait"), 0.6),
    (("can't", "wait"), 0.6),
    (("shut", "up"), -0.6),
    (("fed", "up"), -0.6),
    (("sick", "of"), -0.6),
    (("feel", "sick"), -0.5),
    (("feeling", "sick"), -0.5),
    (("felt", "sick"), -0.5),
    (("tired", "of"), -0.6),
)
_NEGATORS = frozenset(
    {"not", "never", "without", "hardly", "barely", "neither", "nor", "cannot"}
)
_INTENSIFIERS = _lexicon(
    "very:1.4 really:1.4 so:1.4 extremely:1.6 totally:1.4 absolutely:1.6 "
    "completely:1.5 truly:1.4 super:1.4 incredibly:1.6 deeply:1.5 "
    "slightly:0.5 somewhat:0.6 kinda:0.6"
)
_NEGATION_SCOPE = 3  # tokens before a cue that a negator still reaches
_NEGATION_FLIP = -0.75  # "not happy" is mildly negative, not the mirror of "happy"
_BOUNDARY = "|"

_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?|[,.;:!?]")


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN.findall(text.lower().replace("’", "'"))
    return [_BOUNDARY if t in ",.;:!?" else t for t in tokens]


def _is_negator(token: str) -> bool:
    return token in _NEGATORS or token.endswith("n't")


def _cue_at(tokens: list[str], i: int) -> tuple[float, int] | None:
    """Weight and token span of the cue starting at `i`, phrases first."""
    for words, weight in _PHRASES:
        if tuple(tokens[i : i + len(words)]) == words:
            return weight, len(words)
    weight = _POSITIVE.get(tokens[i]) or _NEGATIVE.get(tokens[i])
    return (weight, 1) if weight else None


def event_valence(text: str) -> float:
    """Signed valence of `text` in [-1, 1]; 0.0 when no cue word is present."""
    if not isinstance(text, str) or not text.strip():
        return 0.0
    tokens = _tokenize(text)
    total = 0.0
    i = 0
    while i < len(tokens):
        cue = _cue_at(tokens, i)
        if cue is None:
            i += 1
            continue
        weight, span = cue
        # Modifiers count only inside the cue's own clause.
        window = tokens[max(0, i - _NEGATION_SCOPE) : i]
        if _BOUNDARY in window:
            window = window[len(window) - window[::-1].index(_BOUNDARY) :]
        for token in window[-2:]:
            weight *= _INTENSIFIERS.get(token, 1.0)
        if any(_is_negator(token) for token in window):
            weight *= _NEGATION_FLIP
        total += weight
        i += span
    return math.tanh(total)
