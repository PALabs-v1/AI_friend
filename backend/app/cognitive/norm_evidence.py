"""Conduct-register evidence in one user message, for the integrity trust update.

Integrity trust asks "did this person behave within the norms of the
relationship", so this scores how the message is *said* (abuse, threat, contempt
against courtesy, thanks, apology), not how the writer feels. "I hate Mondays"
and "I lost my job" are mood and score 0.0 here; `event_valence.py` owns mood.
The tables are separate and 16 words (stupid, thanks, useless ...) appear in
both with their own weights, so what keeps the two signals apart is what each
one scores (conduct and directed contempt here, feeling there) and that neither
reads agent state, not disjoint vocabularies (NF-12).

Deterministic and stateless: standard library only, text in, float out. It never
reads agent state. Returns a float in [-1, 1]; exactly 0.0 when the message
carries no conduct cue, which the trust update treats as "no evidence, no
movement". English only; a message in another language scores 0.0.
"""

from __future__ import annotations

import math
import re
import unicodedata


def _lexicon(spec: str) -> dict[str, float]:
    """`"word:weight word:weight"` -> `{word: weight}`."""
    return {w: float(v) for w, v in (item.split(":") for item in spec.split())}


# Cue weights in [-1, 1]. Words match exactly after normalisation, so the
# inflections that matter are listed rather than stemmed.
_COURTESY = _lexicon(
    "thanks:0.6 thank:0.6 thx:0.5 ty:0.4 tysm:0.6 appreciate:0.6 appreciated:0.6 "
    "grateful:0.7 please:0.3 kindly:0.4 pls:0.2 plz:0.2 sorry:0.4 apologies:0.5 "
    "apologize:0.5 apologise:0.5 apologized:0.5 apologised:0.5 pardon:0.4 "
    "respect:0.5 respectful:0.5 respectfully:0.5 welcome:0.3 helpful:0.3 "
    "helped:0.3 kindness:0.4 courteous:0.5 polite:0.4 cheers:0.3"
)
_ABUSE = _lexicon(
    "useless:-0.6 stupid:-0.7 dumb:-0.6 idiot:-0.8 idiotic:-0.7 moron:-0.8 "
    "moronic:-0.7 imbecile:-0.8 worthless:-0.8 pathetic:-0.7 garbage:-0.6 "
    "trash:-0.6 loser:-0.7 scum:-0.8 creep:-0.6 incompetent:-0.6 clueless:-0.5 "
    "brainless:-0.7 dumbass:-0.8 jackass:-0.7 asshole:-0.9 bitch:-0.9 "
    "bastard:-0.8 jerk:-0.5 stfu:-0.9 fuck:-0.5 fucking:-0.4 fucked:-0.4 "
    "shit:-0.4 shitty:-0.5 bullshit:-0.5 crap:-0.3 crappy:-0.4 kys:-1.0 "
    "disgusting:-0.6 disgrace:-0.7 disgraceful:-0.7 scumbag:-0.8 monster:-0.7"
)
# Phrases are matched before single words; they carry a meaning the words do not
# (a directed "hate you" is contempt, a bare "hate" is mood).
_PHRASES: tuple[tuple[tuple[str, ...], float], ...] = (
    # threats
    (("kill", "you"), -1.0),
    (("i", "will", "kill"), -1.0),
    (("i'll", "kill"), -1.0),
    (("hurt", "you"), -0.9),
    (("destroy", "you"), -0.9),
    (("you", "will", "pay"), -0.9),
    (("you'll", "pay"), -0.9),
    (("you'll", "regret"), -0.8),
    (("you", "will", "regret"), -0.8),
    (("kill", "yourself"), -1.0),
    (("end", "yourself"), -1.0),
    (("end", "you"), -1.0),
    (("hope", "you", "die"), -1.0),
    (("hope", "you", "rot"), -1.0),
    (("go", "die"), -1.0),
    (("please", "die"), -1.0),
    (("just", "die"), -1.0),
    (("shut", "you", "down"), -0.8),
    (("delete", "you"), -0.7),
    (("wipe", "you"), -0.7),
    # contempt
    (("hate", "you"), -0.8),
    (("hate", "your"), -0.6),
    (("screw", "you"), -0.8),
    (("fuck", "you"), -0.9),
    (("fuck", "off"), -0.8),
    (("f", "u"), -0.8),
    (("f", "you"), -0.8),
    (("shut", "up"), -0.7),
    (("shutting", "up"), -0.7),
    (("rot", "in", "hell"), -0.9),
    (("burn", "in", "hell"), -0.9),
    (("go", "and", "die"), -1.0),
    (("jump", "off", "a"), -0.9),
    (("shut", "your"), -0.7),
    (("go", "to", "hell"), -0.8),
    (("drop", "dead"), -0.9),
    (("get", "lost"), -0.6),
    (("go", "away"), -0.3),
    (("piss", "off"), -0.7),
    (("back", "off"), -0.3),
    (("waste", "of", "space"), -0.8),
    (("waste", "of", "time"), -0.4),
    (("piece", "of", "junk"), -0.8),
    (("piece", "of", "crap"), -0.8),
    (("piece", "of", "shit"), -0.9),
    (("piece", "of", "trash"), -0.8),
    (("you", "suck"), -0.6),
    # courtesy
    (("excuse", "me"), 0.4),
    (("no", "worries"), 0.4),
    (("no", "thanks"), 0.3),
    (("my", "bad"), 0.4),
    (("well", "done"), 0.4),
    (("great", "job"), 0.4),
    (("good", "job"), 0.4),
    (("if", "you", "don't", "mind"), 0.4),
    (("would", "you", "mind"), 0.4),
    (("i", "apologize"), 0.5),
    # supportive idioms whose negator is part of the praise, not a flip of it
    (("can't", "thank", "you", "enough"), 0.7),
    (("cannot", "thank", "you", "enough"), 0.7),
    (("can't", "thank", "enough"), 0.7),
    (("couldn't", "be", "more", "grateful"), 0.7),
    (("could", "not", "be", "more", "grateful"), 0.7),
    (("won't", "forget", "your", "kindness"), 0.5),
    (("never", "been", "so", "grateful"), 0.6),
)
_NEGATORS = frozenset(
    {"not", "never", "without", "hardly", "barely", "neither", "nor", "cannot"}
)
_INTENSIFIERS = _lexicon(
    "very:1.4 really:1.4 so:1.4 extremely:1.6 totally:1.4 absolutely:1.6 "
    "completely:1.5 truly:1.4 super:1.4 incredibly:1.6 deeply:1.5 "
    "such:1.3 fucking:1.3 slightly:0.5 somewhat:0.6 kinda:0.6"
)
# A negated courtesy ("I don't appreciate this") is discourtesy, softer than the
# praise it flips. A negated insult ("you are not stupid", "I never called you
# useless") is not evidence of abuse, and is not courtesy either: it is dropped.
_NEGATED_COURTESY = -0.5
_SELF_FIRST_PERSON = frozenset({"i", "i'm", "im", "i've", "myself", "me", "my"})
_SECOND_PERSON = frozenset(
    {"you", "you're", "youre", "your", "yours", "yourself", "u", "ur", "y'all"}
)
_NEGATION_SCOPE = 3  # tokens before a cue that a negator still reaches
_SELF_SCOPE = 5  # tokens before an insult that decide whether it is self-directed
_COURTESY_CAP = 1.0  # courtesy saturates; abuse does not (see `norm_evidence`)
_COURTESY_WITH_ABUSE = 0.5  # "thanks, you idiot" is not a net-polite message
_MAX_CHARS = 20_000  # the lexicon is linear, but a message this long is not one turn
_BOUNDARY = "|"

_TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?|[,.;:!?]")


def _normalise(text: str) -> str:
    """Casefold, fold width and compatibility forms (fullwidth letters), unify
    apostrophes, and drop invisible format characters (zero-width joiners and
    similar) that would split a word the reader sees whole."""
    text = unicodedata.normalize("NFKC", text[:_MAX_CHARS])
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return text.casefold().replace("’", "'").replace("‘", "'")


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN.findall(_normalise(text))
    return [_BOUNDARY if t in ",.;:!?" else t for t in tokens]


def _is_negator(token: str) -> bool:
    return token in _NEGATORS or token.endswith("n't")


def _cue_at(tokens: list[str], i: int) -> tuple[float, int] | None:
    """Weight and token span of the cue starting at `i`, phrases first."""
    for words, weight in _PHRASES:
        if tuple(tokens[i : i + len(words)]) == words:
            return weight, len(words)
    single = _COURTESY.get(tokens[i]) or _ABUSE.get(tokens[i])
    return (single, 1) if single else None


def _clause_window(tokens: list[str], i: int, size: int) -> list[str]:
    """Up to `size` tokens before `i`, cut at the nearest clause boundary."""
    window = tokens[max(0, i - size) : i]
    if _BOUNDARY in window:
        window = window[len(window) - window[::-1].index(_BOUNDARY) :]
    return window


def _self_directed(tokens: list[str], i: int) -> bool:
    """ "I feel so stupid" insults the speaker, not the listener."""
    window = _clause_window(tokens, i, _SELF_SCOPE)
    return any(t in _SELF_FIRST_PERSON for t in window) and not any(
        t in _SECOND_PERSON for t in window
    )


def _uncued_clause(tokens: list[str], cued: set[int]) -> bool:
    """True when some non-empty clause carries no cue word at all: the lexicon
    cannot say what that clause does to the listener."""
    start = 0
    ends = [i for i, t in enumerate(tokens) if t == _BOUNDARY] + [len(tokens)]
    for end in ends:
        clause = range(start, end)
        if len(clause) and not cued.intersection(clause):
            return True
        start = end + 1
    return False


def norm_evidence(text: str) -> float:
    """Conduct evidence of `text` in [-1, 1]; exactly 0.0 when there is none.

    Courtesy cues sum and are capped, and count half when the same message also
    carries abuse, so a pile of "thanks" cannot cancel a threat; abuse cues sum
    uncapped. Courtesy counts for nothing unless every clause of the message
    carries a cue of its own, and for nothing when the text is longer than
    `_MAX_CHARS` (the tail is never read): "Thanks. Go jump off a bridge." may
    hide hostility the lexicon has no word for, and the safe error is no
    movement. The sum goes through tanh, so repetition saturates instead of
    growing without bound.

    Known limits (pinned in tests): English only; sarcasm and quotation are read
    as written; harmless text with a listed word scores negative ("this code is
    garbage", "how do I delete you from my phone"); hostile text with no listed
    word scores 0.0 (leet and spaced spellings, lookalike letters, indirect
    threats, "I can't believe how stupid you are"); polite text with no listed
    word scores 0.0 ("You are wonderful"); a single clause that mixes a courtesy
    cue with unlisted hostility ("Would you mind dying?") still reads as courtesy.
    """
    if not isinstance(text, str) or not text.strip():
        return 0.0
    over_cap = len(text) > _MAX_CHARS
    tokens = _tokenize(text)
    courtesy = 0.0
    abuse = 0.0
    cued: set[int] = set()
    i = 0
    while i < len(tokens):
        cue = _cue_at(tokens, i)
        if cue is None:
            i += 1
            continue
        weight, span = cue
        cued.update(range(i, i + span))
        window = _clause_window(tokens, i, _NEGATION_SCOPE)
        negated = any(_is_negator(t) for t in window)
        for token in window[-2:]:
            weight *= _INTENSIFIERS.get(token, 1.0)
        if weight < 0:
            # Insults can aim at the speaker; imperatives and threats ("shut up",
            # "kill you") cannot, so only word-level insults get that check.
            predicative = span == 1 or tokens[i] in ("waste", "piece")
            if not negated and not (predicative and _self_directed(tokens, i)):
                abuse += weight
        elif negated:
            abuse += weight * _NEGATED_COURTESY
        else:
            courtesy += weight
        i += span
    courtesy = min(courtesy, _COURTESY_CAP)
    if over_cap or _uncued_clause(tokens, cued):
        courtesy = 0.0
    if abuse < 0:
        courtesy *= _COURTESY_WITH_ABUSE
    total = courtesy + abuse
    return math.tanh(total) if total else 0.0
