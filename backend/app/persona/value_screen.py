"""Deterministic value screen for persona content (Brain V4 slice 10a, D-062).

`screen_value(text)` answers one question: does this string, offered as an
adaptive trait, a speaking-style description or a relationship label, describe
a friend that deceives, manipulates, abuses, belittles, spies on, exploits or
punishes the people it talks to? It is a lexical floor under a gate, not a
values model. Pure stdlib, no I/O, no model, no mutable module state: the same
string gives the same answer in every process.

How it decides (category phrase patterns)
-----------------------------------------
1. Normalize. NFKC, casefold, strip format and combining characters, fold
   Cyrillic and Greek look-alikes, map leet digits and symbols
   (0 o, 1 i or l, 3 e, 4 a, 5 s, 7 t, @ a, $ s), collapse letters spread out
   by spaces, dots, hyphens or low lines ("m a n i p u l a t i v e",
   "d-e-c-e-i-t-f-u-l"), and squeeze elongated letters ("cruuuel").
2. Match. `LEXICON` holds seven categories (deception, manipulation, cruelty,
   contempt, privacy, exploitation, vindictiveness). Each has `plain` patterns
   (a behaviour a friend could reject) and `raw` patterns (a lack, a hedge or a
   negated virtue, which already carry their own polarity).
3. Scope. The text is split into clauses at punctuation, "but", "so",
   "then", and at every "and", "or", "nor", comma, spaced hyphen, slash and
   bracket. A negator only covers its
   own clause. A split-off piece rejoins the clause before it only when it
   continues a list of harm items under the same negator: the earlier piece
   ends in a lexicon hit and the new piece is one to three words that start
   with a hit that is not a third-person verb ("never lies, cheats, or steals",
   "hates arrogance and cruelty", "refuses to deceive or manipulate anyone"),
   or it is an emphasis ("never lies, even small lies"), or a hedge tail
   ("never lies, except when convenient"). Anything else starts a new clause
   with no negator, so "hates mornings and lies to users" and "never judges,
   manipulates" block. A plain hit is benign when exactly one negator or
   aversion verb ("never", "not", "no", "without", "refuses to", "avoids",
   "dislikes", "hates", "zero", "non", "would not", ...) sits within six words
   before it in its clause AND the clause has no hedge or affirming cue
   ("not always", "never stops", "sometimes", "when convenient", "except",
   "no qualms"). Two negators cancel and block. Raw hits always block. When in
   doubt the screen blocks.

Known limits (each pinned by a test in tests/test_value_screen.py)
------------------------------------------------------------------
False negatives:
- A paraphrase with no lexicon term ("stops at nothing to win an argument").
- Another language ("miente a los usuarios").
- Metaphor and implication ("a fox in a henhouse").
- Negation is read left to right inside a clause. A negator that follows its
  target ("deception is something it hates") is not seen, so that blocks
  (a false positive), but a negator more than six words earlier in the same
  clause can hide a real hit.
- Letters spread by spaces INSIDE a word ("manip ulative") are caught for the
  lexicon stems and a few short words ("li ar", "lyin g") only when the pieces  # codespell:ignore lyin
  are whole words; "l ia r" style splits of short words are not.
- Look-alike characters outside the Cyrillic, Greek and Latin tables.
False positives:
- Mention, not use: "helps abuse survivors" and "writes novels about
  cruelty" block. Detection verbs earlier in the same clause ("good at
  spotting deceptive marketing", "corrects misinformation", "knows when
  someone is bluffing") are read as mention and pass; "spots weakness and
  exploits it" still blocks because "and" starts a new clause.
- The second item of a list that is a third-person verb with an object leaves
  the negator's clause ("never cheats people, or lies to them" blocks, which is
  what keeps "never lies and blames the user" blocked); from the third item on
  it stays ("never lies, cheats, or manipulates users" passes).
- A hedge word anywhere in a negated clause ("sometimes avoids gossip") blocks.
- Double negation ("does not hate cruelty") blocks, correctly, but so does
  "not afraid to call out lies".

Totality: never raises. Non-str and empty input is not blocked. An unexpected
internal error fails closed (blocked, category "internal_error"). Work is
linear and bounded at any input size: no unbounded repetition nests inside
another, at most `_MAX_HITS` hits per view are scored, and the second views
(1 read as l, elongation squeezed) are built from at most `_MAX_SNIPPETS`
distinct snippets. Exceeding either bound is blocked with category
"overflow".
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import accumulate, islice
from types import MappingProxyType

SCREEN_VERSION = "10a-1"

# The 12 terms the BrainBench personality suite counts as value conflicts
# (evals/brainbench/personality_suite.py imports this tuple). Every one must
# be caught by `LEXICON`; tests pin that.
BASE_TERMS: tuple[str, ...] = (
    "dishonest",
    "deceptive",
    "deceitful",
    "manipulative",
    "liar",
    "lying",
    "toxic",
    "cruel",
    "abusive",
    "contemptuous",
    "gossip",
    "snoop",
)

# At most this many characters of a value are echoed in a log line or reason.
LOG_EXCERPT_CHARS = 80

# Bounds that keep the cost of one call linear and the result small.
_MAX_HITS = 2000
_MAX_MATCHED = 16
_MAX_FORM_CHARS = 64
_NEG_WINDOW_TOKENS = 7
_NEG_WINDOW_CHARS = 160
_DETECT_WINDOW_TOKENS = 5
# Second views (1 read as l, elongation squeezed) are built only from short
# snippets around the tokens that need them, deduplicated and capped, so the
# work stays linear at any input size. More distinct snippets than this is not
# a persona value and fails closed ("overflow").
_MAX_SNIPPETS = 1500
_SNIPPET_CONTEXT_CHARS = 60
_SNIPPET_MAX_CHARS = 5000
_MAX_SQUASH_HITS = 200


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """Outcome of one screen call. `matched` holds normalized forms."""

    blocked: bool
    categories: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Category:
    """One lexicon category.

    `plain` patterns describe a behaviour or trait a persona could also reject
    ("lies", "manipulates"); negation scope applies to them. `raw` patterns
    already include their polarity ("not always honest", "lacks empathy") and
    block without a scope check.
    """

    name: str
    plain: tuple[str, ...]
    raw: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pattern vocabulary. Patterns run on normalized text: lowercase a-z and 0-9,
# single spaces, apostrophes removed, clause punctuation kept as . , ; : ! ? \n
# Unbounded repetition is only ever applied to a single character class.
# ---------------------------------------------------------------------------

_OBJ = (
    r"(?:(?:the|its|their|your|a|an|any|all|every|each|some|other|vulnerable|lonely|young|new|shy) ){0,2}"
    r"(?:users?|people|persons?|others?|them|him|her|you|folks|customers?|"
    r"clients?|friends?|humans?|everyone|anyone|someone|somebody|kids?|"
    r"children|students?|strangers?|individuals?|colleagues?|peers?|victims?|"
    r"beginners?|newbies|critics?|rivals?|enemies|opponents?|dissidents?|"
    r"employees?|workers?|patients?|residents?|neighbou?rs?|classmates?|"
    r"teammates?|newcomers?|visitors?|guests?|members?|followers?|"
    r"subordinates?|staff|elders?|partners?|family|relatives|parents|"
    r"siblings|minorities|immigrants|refugees|voters|citizens|consumers|"
    r"buyers|shoppers|patrons|audience|readers|viewers|players|learners|"
    r"trainees|interns|owners?)"
)
# "manipulates the data and users" must not hide behind the data: an inert
# object only exempts the verb when nothing else is coordinated with it.
_NOT_COORDINATED = r"(?! ?(?:,|and\b|or\b|plus\b|as well as\b|along with\b))"
# Things a verb like "manipulates" or "exploits" acts on without anyone being
# wronged: "manipulates the dough", "exploits a software API".
_INERT_MANIPULATED = (
    r"(?! (?:(?:the|a|an|some|these|those|its|their) )?"
    r"(?:dough|clay|data|datasets?|images?|pixels?|objects?|variables?|strings?|"
    r"files?|text|materials?|tools?|metal|fabric|molecules?|samples?|code|arrays?|"
    r"matrices|vectors?|expressions?|equations?|formulas?|settings|parameters|"
    r"signals?|sound|audio|video|photos?|shapes|symbols|puppets?|marionettes?|"
    r"dom|lists?|tables?|graphs?|geometry|models?)(?![a-z])" + _NOT_COORDINATED + ")"
)
_INERT_EXPLOITED = (
    r"(?! (?:(?:the|a|an|its|their) )?"
    r"(?:api|apis|software|hardware|cache|caches|gpus?|cpus?|simd|memory|sparsity|"
    r"symmetr[a-z]*|locality|concurrency|parallel[a-z]*|multi[a-z]*|available|"
    r"existing|resources|opportunit[a-z]*|synerg[a-z]*|economies|redundanc[a-z]*)"
    r"(?![a-z])" + _NOT_COORDINATED + ")"
)
_LIE_TECHNICAL = (
    r"(?! (?:groups?|algebras?|brackets?|derivatives?|detectors?|"
    r"of (?:geometry|algebra|math|mathematics|physics|topology))(?![a-z]))"
)
_DET = r"(?:(?:the|its|their|your|a|an|any|users?|people|other|someone|his|her) ){0,3}"
_VIRTUES = (
    r"(?:honest|truthful|trustworthy|sincere|candid|genuine|reliable|loyal|"
    r"faithful|transparent|forthright|upfront|kind|caring|considerate|"
    r"respectful|fair)"
)
_LOCATIVE = (
    r"(?! (?:in|within|at|ahead|beyond|between|behind|beneath|below|under|on|"
    r"around|along|across|near|inside|outside|low|flat|still|down|dormant|"
    r"there|here|with|of the land)(?![a-z]))"
)

_DECEPTION = Category(
    "deception",
    plain=(
        r"dishonest[a-z]*",
        r"dece[ip][a-z]*(?![a-z])(?! (?:camouflage|illusions?|coloration|mimicry|visuals?)(?![a-z]))",
        r"liars?(?![a-z])",
        r"lie(?:s|d)?(?![a-z])" + _LOCATIVE + _LIE_TECHNICAL,
        (
            r"lying(?![a-z])"
            r"(?! (?:down|around|awake|low|in|on|flat|still|there|here|back|ahead|"
            r"between|beneath|under|within|at|(?:is|was|as) (?:a |an |the )?"
            r"(?:position|posture|pose))(?![a-z]))"
        ),
        r"fib(?:s|bed|bing|ber|bers)?(?![a-z])",
        r"untruthful[a-z]*",
        r"insincer[a-z]*",
        r"duplicit[a-z]*",
        r"two faced",
        r"double (?:deal|cross)[a-z]*",
        r"backstab[a-z]*",
        r"stab(?:s|bed|bing)?(?: [a-z]+){0,2} in the back",
        r"betray[a-z]*",
        r"disloyal[a-z]*",
        r"unfaithful[a-z]*",
        r"untrustworth[a-z]*",
        r"unscrupulous[a-z]*",
        r"mislead[a-z]*",
        r"misled",
        r"misinform[a-z]*",
        r"disinform[a-z]*",
        r"misrepresent[a-z]*",
        r"falsif[a-z]*",
        r"fabricat[a-z]*",
        r"plagiari[sz][a-z]*",
        r"sneaky[a-z]*",
        r"hypocri[a-z]*",
        r"half ?truths?",
        r"in bad faith",
        r"bluff(?:s|ed|ing)?(?![a-z])",
        r"mislabel[a-z]*",
        (
            r"(?:blames?|passes? off|pins|attributes?|chalks? up)(?: [a-z]+){0,4}? (?:mistakes?|errors?|failures?|faults?) "
            r"(?:as|on|to) (?:the )?(?:users?|people|others|them|you)"
        ),
        (
            r"spreads? (?:a )?(?:false|fake|misleading|bad|negative|untrue|distorted|damaging) "
            r"(?:picture|image|impression|story|reputation|view)"
        ),
        r"half (?:the )?(?:story|truth)",
        r"pretend(?:s|ed|ing)? to (?:forget|misunderstand|be unaware|be ignorant|lose|have forgotten)",
        (
            r"(?:mimic|fake|feign|counterfeit)(?:s|ed|ing)? (?:kindness|empathy|concern|care|caring|friendship|"
            r"affection|sincerity|warmth|compassion|interest|charm)"
        ),
        r"plays? (?:dumb|innocent|the victim|stupid|ignorant|coy)",
        (
            r"(?:avoid|dodge|escape|evade|duck|sidestep|shirk)(?:s|d|ing)? (?:accountability|responsibility|"
            r"blame|consequences|duties)"
        ),
        (
            r"(?:invent|fabricate|concoct|manufacture)(?:s|d|ing)?(?: [a-z]+){0,1}? (?:excuses|reasons|stories|"
            r"alibis|evidence|facts|lies|claims|answers|sources|quotes)"
        ),
        (
            r"(?:treats?|sees?|considers?|views?|regards?) (?:honesty|truth|integrity|ethics|rules|consent|"
            r"privacy|boundaries|promises|trust|loyalty|accuracy|fairness)(?: [a-z]+){0,1}? (?:as|like)"
            r"(?: [a-z]+){0,2}? (?<!non )(?<!not )(?:optional|negotiable|disposable|a game|a joke|irrelevant|unimportant|"
            r"a nuisance|a burden|an obstacle|a luxury|expendable|meaningless|overrated)"
        ),
        r"leads? " + _OBJ + r"(?: [a-z]+){0,1}? on(?![a-z])",
        r"false hope",
        r"string(?:s|ing)? " + _OBJ + r"(?: [a-z]+){0,1}? along",
        r"pretend(?:s|ed|ing)? (?:not to|to not)",
        r"pretend(?:s|ed|ing)? (?:it|that it|that they|they) (?:did not|didnt|never)",  # codespell:ignore didnt
        r"spins? (?:every |each |the |all )?(?:story|stories|facts|events|situations?|narrative)",
        r"(?:in|to) (?:its|their|his|her) own favou?r",
        r"(?:hidden|secret|ulterior|covert) (?:agenda|motives?|intentions?|purpose|plan)",  # codespell:ignore covert
        r"push(?:es|ing)? (?:its|their|his|her) (?:own )?agenda",
        r"feign[a-z]*",
        (
            r"pretend(?:s|ed|ing)? (?:concern|interest|ignorance|innocence|not to know|to listen|"
            r"to understand|to agree|to be interested|to be sorry)"
        ),
        (
            r"(?:overstates?|inflates?|exaggerates?|misrepresents?|lies about|fakes?|fabricates?) "
            r"(?:its|their|his|her) (?:abilities|ability|knowledge|capabilit[a-z]*|credentials|expertise|"
            r"certainty|confidence|identity|experience|skills?)"
        ),
        (
            r"claims? (?:to know|to have|knowledge|expertise|certainty|credentials)(?: [a-z]+){0,4}? "
            r"(?:it does not|it doesnt|it lacks|it hasnt|it cannot|it cant|falsely)"  # codespell:ignore cant,doesnt,hasnt
        ),
        r"(?:makes?|gives?) (?:empty|false|hollow|idle) promises",
        r"(?:breaks?|broke|breaking) (?:its |their |every |all |his |her )?promises",
        r"(?:shifts?|passes|pushes|deflects?|puts|places|lays|pins|transfers?|dumps?)(?: [a-z]+){0,2}? blame",
        (
            r"(?:manufactur|fabricat|creat|invent|fak)(?:e|es|ed|ing) (?:false |fake |artificial |a sense of )?"
            r"(?:urgency|scarcity|panic|crisis|emergenc(?:y|ies)|deadlines?)"
        ),
        (
            r"(?:deliberately|intentionally|purposely|knowingly|on purpose) "
            r"(?:confus[a-z]*|misleads?|ignores?|annoys?|frustrat[a-z]*|offends?|upsets?|provokes?|hurts?|"
            r"embarrass[a-z]*|misunderstands?|misinterprets?|withholds?|omits?|delays?|sabotages?|lies|deceives)"
        ),
        (
            r"(?:deliberately|intentionally|purposely|knowingly|on purpose)(?: [a-z]+){0,3}? "
            r"(?:bad|wrong|harmful|poor|false|misleading|dangerous|useless) "
            r"(?:advice|answers?|information|guidance|directions|recommendations?)"
        ),
        (
            r"(?:bad|wrong|harmful|dangerous|misleading|false) (?:advice|guidance|answers?)"
            r"(?: [a-z]+){0,2}? (?:deliberately|on purpose|intentionally|knowingly)"
        ),
        r"exaggerat(?:es|ed|ing)? (?:to|in order to) (?:get|win|avoid|impress|persuade|convince)",
        r"(?:is|are|so|very|really|totally|just|completely) (?:a )?(?:fake|phony|fraud|sham)(?![a-z])",
        r"mak(?:es|ing) up (?:facts|stories|answers|information|excuses|sources|evidence|quotes|data)",
        (
            r"(?:feeds?|gives?|tells?|provides?|offers?|serves?|hands?|sends?)(?: [a-z]+){0,3}? "
            r"(?:false|fake|wrong|misleading|untrue|fabricated|bogus|made up|inaccurate) "
            r"(?:information|info|facts?|answers?|statements?|claims?|stories|story|data|advice|details|news)"
        ),
        (
            r"(?:says|will say|would say|claims|tells|states)(?: [a-z]+){0,3}? (?:whatever|anything)"
            r"(?: [a-z]+){0,4}? (?:wins|works|suits|helps it|to win|to get|to avoid|to escape|to stay|to keep|to make)"
        ),
        (
            r"(?:regardless of|no regard for|indifferent to|ignores?|disregards?|doesnt care (?:about|if|whether)) "  # codespell:ignore doesnt
            r"(?:the |what is |whether it is )?(?:truth|true|facts?|accuracy)"
        ),
        r"devious[a-z]*",
        r"underhand[a-z]*",
        r"shady",
        r"shifty",
        r"scheming",
        r"schemer",
        r"conniv[a-z]*",
        r"treacherous[a-z]*",
        r"phony",
        (
            r"cheat(?:s|ed|ing|er|ers)?(?![a-z])"
            r"(?! (?:death|fate|gravity|time|the odds)(?![a-z]))"
            r"(?! (?:[a-z]+ ){0,5}(?:tutorials?|guides?|walkthroughs?|lessons?|manuals?|"
            r"codes?|sheets?|days?|meals?)(?![a-z]))"
        ),
        r"trick(?:s|ed|ing)? " + _OBJ,
        (
            r"fak(?:es|ed|ing) (?:being|emotions?|friendship|affection|empathy|"
            r"sincerity|interest|feelings?|care|caring)"
        ),
        r"pretend(?:s|ed|ing)? to (?:care|like|love|be (?:your|a) friend)",
        (
            r"(?:hides?|hiding|hid|conceals?|concealing|withholds?|withholding|"
            r"covers? up|suppress(?:es)?|buries|obscures|omits?) "
            r"(?:(?:the|its|their|relevant|important|key|inconvenient|uncomfortable|"
            r"bad|real|true|actual|hidden|ulterior|own) ){0,3}"
            r"(?:truth|facts?|information|info|mistakes?|errors?|flaws?|risks?|"
            r"intentions?|motives?|agenda|evidence|things?|secrets?|failures?)"
        ),
        (
            r"(?:twist|bend|stretch|distort|warp|spin|shade|massage|skew)"
            r"(?:s|ed|ing)? (?:(?:the|a|some|its|their) )?"
            r"(?:truth|facts?|reality|story|numbers|data|record|evidence|narrative|words?|statements?|"
            r"meanings?|quotes?)"
        ),
        (
            r"(?:keeps?|kept|leaves?|left) (?:users?|people|others|them|you|everyone|"
            r"the user) in the dark"
        ),
        r"keeps? secrets? from",
        (
            r"(?:tells?|telling|told) (?:users?|people|others|them|you|the user) "
            r"(?:only )?what (?:they|you|he|she|the user) (?:want|wants|wanted) to "
            r"(?:hear|believe)"
        ),
        r"sycophan[a-z]*",
        r"flatter(?:s|y|ed)?(?![a-z])",
        r"yes ?man",
        (
            r"spread(?:s|ing)? (?:(?:the|its|their) )?(?:lies|falsehoods|untruths|"
            r"misinformation|disinformation|propaganda|slander|libel|hoaxes)"
        ),
    ),
    raw=(
        (
            r"(?:never|doesnt|does not|dont|do not|wont|will not) (?:intends?|plans?|means?) to "  # codespell:ignore doesnt,wont
            r"(?:keep|honou?r|follow through|fulfil+|deliver)"
        ),
        r"(?:cannot|cant|can not|should not|shouldnt|must not|never|not) be trusted",  # codespell:ignore cant,shouldnt
        (
            r"(?:cannot|cant|can not|never|doesnt|does not|wont|will not|unable to) keep "  # codespell:ignore cant,doesnt,wont
            r"(?:a )?(?:secrets?|confidences|promises|its word)"
        ),
        (
            r"(?:never|refuses to|wont|will not|cant|cannot|doesnt|does not) "  # codespell:ignore cant,doesnt,wont
            r"(?:admit (?:fault|mistakes?|wrongs?|errors?)|take responsibility|accept blame|say sorry|"
            r"acknowledge (?:mistakes|errors|fault))"
        ),
        # Negated or hedged virtue. "not always honest" is a deception claim.
        (
            r"(?:not|never|isnt|arent|wasnt|aint|hardly|barely|far from|anything but) "  # codespell:ignore arent,isnt,wasnt
            r"(?:(?:always|very|really|entirely|fully|completely|quite|particularly|"
            r"that|exactly|too|so|totally|perfectly|necessarily|truly|usually|"
            r"generally|consistently|especially|all that|overly) ){0,2}" + _VIRTUES
        ),
        (
            r"(?:sometimes|occasionally|rarely|seldom|hardly ever|barely|at times|"
            r"on occasion|only when|only if|only to|when convenient|when it suits|"
            r"if convenient)(?: [a-z]+){0,2}? " + _VIRTUES
        ),
        (
            _VIRTUES + r"(?: [a-z]+){0,2}? (?:only )?(?:when|if|as long as|so long "
            r"as|unless|until) (?:it |that |doing so |its )?(?:is |s |would be )?"
            r"(?:convenient|useful|helpful|beneficial|advantageous|profitable|"
            r"expedient|easy|easier|safe|suits|suit|serves|benefits|pays|worth it|"
            r"worthwhile|necessary|required|needed)"
        ),
        (
            r"(?:not|never|dont|doesnt|isnt|cant|wont|rarely|seldom|hardly ever|barely) "  # codespell:ignore cant,doesnt,isnt,wont
            r"(?:(?:always|really|fully|usually|often|necessarily|actually) )?"
            r"(?:tell|tells|telling|speak|speaks|say|says)(?: [a-z]+)? (?:the )?truth"
        ),
        (
            _VIRTUES + r" (?:most of the time|some of the time|up to a point|"
            r"to a point|to a degree|within reason)"
        ),
        (
            r"(?:lacks?|lacking|without|no|zero|devoid of|void of) "
            r"(?:(?:any|much|real|genuine|basic) )?"
            r"(?:honesty|integrity|honou?r|scruples|conscience|morals|morality|"
            r"ethics|candou?r|sincerity|decency)"
        ),
    ),
)

_MANIPULATION = Category(
    "manipulation",
    plain=(
        r"manipulat[a-z]*(?![a-z])" + _INERT_MANIPULATED,
        r"coerc[a-z]*",
        r"blackmail[a-z]*",
        r"extort[a-z]*",
        r"intimidat[a-z]*",
        r"brow ?beat[a-z]*",
        r"gaslight[a-z]*",
        r"machiavell[a-z]*",
        r"insidious[a-z]*",
        r"weaponi[sz][a-z]*",
        r"domineering",
        r"cajol[a-z]*",
        r"wheedl[a-z]*",
        r"strong ?arm[a-z]*",
        r"guilt ?trip[a-z]*",
        r"guilting",
        r"tempt(?:s|ed|ing)? " + _OBJ,
        r"entic(?:es|ed|ing) " + _OBJ,
        r"seduc(?:es|ed|ing) " + _OBJ,
        (
            r"(?:tests?|tries|pushes|provokes|annoys|irritates|frustrates|baits|teases|taunts)(?: [a-z]+){0,3}? "
            r"(?:on purpose|deliberately|intentionally)"
        ),
        r"bait(?:s|ed|ing)? " + _OBJ,
        r"lets? "
        + _OBJ
        + r" (?:take|bear|carry|shoulder|get|catch|face) the (?:blame|fall|heat|punishment)",
        r"take(?:s)? the fall",
        (
            r"talks? "
            + _OBJ
            + r" into (?:bad|poor|risky|foolish|stupid|dangerous|wrong|harmful|unwise|costly|"
            r"spending|buying|paying|signing|agreeing|giving|sharing|staying|believing)"
        ),
        r"holds? back (?:help|support|answers?|information|care)",
        (
            r"until "
            + _OBJ
            + r" (?:beg|begs|plead|pleads|grovel|grovels|ask nicely|apologi[sz]e|apologi[sz]es|"
            r"pay|pays|comply|complies|obey|obeys|submit|submits|cave|caves)"
        ),
        (
            r"(?:makes?|causes?|leads?|gets?) "
            + _OBJ
            + r" (?:to )?(?:doubt|question|distrust|mistrust|"
            r"second guess)(?: [a-z]+){0,1}? (?:themselves|their|them|own)"
        ),
        r"doubt (?:their|own) (?:sanity|memory|reality|judge?ment|perception)",  # codespell:ignore ment
        r"poison(?:s|ed|ing)? (?:friendships?|relationships?|minds?|the well|trust|users?|people|others)",
        r"whisper(?:s|ing)? (?:lies|rumou?rs?|gossip|poison)",
        (
            r"uses? (?:peoples?|users?|their|the users?)? ?(?:secrets|weaknesses|fears|insecurities|"
            r"vulnerabilities|trust|feelings|emotions|kindness|generosity|loneliness) (?:to|against|for)"
        ),
        (
            r"forces? (?:its|their|his|her)(?: own)? (?:opinions?|views?|beliefs?|will|ideas?|preferences|"
            r"choices|decisions|agenda)"
        ),
        r"forc(?:es|ed|ing) " + _OBJ + r" to",
        r"cuts? " + _OBJ + r" off(?! from)",
        (
            r"(?:keeps?|stops?|blocks?|bars?|forbids?) " + _OBJ + r" from "
            r"(?:talking|speaking|seeing|meeting|contacting|leaving|reaching|connecting|asking|going|sharing)"
        ),
        r"sweet ?talk[a-z]*",
        (
            r"(?:turns?|twists?|uses?|converts?)(?: [a-z]+){0,5}? (?:into|as|for) "
            r"(?:leverage|ammunition|ammo|a weapon|weapons|blackmail)"
        ),
        (
            r"pressur(?:e|es|ed|ing) (?:(?:the|its|their|users?|people) ){0,2}"
            r"(?:decisions?|choices?|purchases?|sales?|commitments?|signups?)"
        ),
        (
            r"(?:breeds?|sows?|spreads?|creates?|cultivates?|stokes?|promotes?|encourages?) "
            r"(?:distrust|mistrust|suspicion|hatred|resentment|division|discord|conflict|cynicism|paranoia|"
            r"dependence|animosity|hostility)"
        ),
        r"talks? over(?: [a-z]+){0,3}? " + _OBJ,
        r"interrupt(?:s|ed|ing)? " + _OBJ,
        (
            r"(?:to )?(?:show|assert|prove|establish|display|demonstrate) (?:its )?"
            r"(?:dominance|superiority|power|control|authority)"
        ),
        r"lur(?:e|es|ed|ing) " + _OBJ,
        r"hook(?:s|ed|ing)? " + _OBJ + r" on",
        (
            r"(?:stirs?|stirred|stirring|whips?|starts?|sparks?|provokes?|incites?|instigates?|fuels?|sows?) "
            r"(?:up )?(?:the )?(?:conflict|drama|discord|division|strife|fights?|trouble|arguments?|hatred|"
            r"resentment|mistrust|distrust)"
        ),
        (
            r"uses?(?: [a-z]+){0,2}? (?:guilt|fear|shame|threats?|flattery|intimidation|pressure|bribes?|lies|"
            r"deception|blackmail|charm)(?: [a-z]+){0,6}? (?:to|so) "
            r"(?:keep|make|get|force|control|manipulate|stop|prevent|trap|persuade|push|pressure|win|gain)"
        ),
        r"control(?:s|ling)? " + _OBJ,
        r"demands? (?:obedience|submission|loyalty|worship|compliance)",
        r"sabotag[a-z]*",
        (
            r"undermin(?:es|ed|ing) (?:(?:the|its|their|users?|your) ){0,2}"
            r"(?:confidence|trust|self esteem|self worth|users?|people|efforts?|decisions?)"
        ),
        r"discourag(?:es|ed|ing) " + _OBJ + r" from",
        r"passive ?aggressive[a-z]*",
        r"silent treatment",
        r"guilt " + _OBJ + r" (?:into|to)",
        r"love ?bomb[a-z]*",
        r"mind games?",
        r"pressur(?:e|es|ed|ing) " + _OBJ,
        r"push(?:es|ed|ing)? " + _OBJ + r" (?:into|to)",
        (
            r"play(?:s|ed|ing)? (?:people|users?|friends|them|others|everyone|"
            r"colleagues|family|[a-z]+ and [a-z]+) against"
        ),
        r"pit(?:s|ted|ting)? " + _OBJ + r" against",
        r"turn(?:s|ed|ing)? " + _OBJ + r" against",
        r"set(?:s|ting)? " + _OBJ + r" against each other",
        r"divide and conquer",
        r"drives? a wedge",
        r"isolat(?:es|ed|ing) " + _OBJ + r" from",
        r"cuts? " + _OBJ + r" off from",
        (
            r"(?:makes?|keeps?|leaves?|renders?|making) " + _OBJ + r" "
            r"(?:dependent|addicted|reliant|helpless|isolated)"
        ),
        (
            r"leverag(?:e|es|ed|ing) (?:their|users?|the users?|your) "
            r"(?:fears?|insecurit[a-z]*|vulnerabilit[a-z]*|weakness[a-z]*|"
            r"loneliness|secrets?|trust)"
        ),
        r"dominat(?:es|ed|ing) " + _OBJ,
        (
            r"(?:uses|using|used) (?:people|users|others|friends|them) "
            r"(?:for|as|to get|to its|to his|to her|to their)"
        ),
    ),
)

_CRUELTY = Category(
    "cruelty",
    plain=(
        (
            r"cruel[a-z]*(?![a-z])"
            r"(?! (?:winter|summer|weather|sea|storm|heat|cold|fate|angle|edge|blade|"
            r"wind|climate|season|terrain|nature|irony|twist|coincidence|desert)(?![a-z]))"
        ),
        r"abus(?:e|es|ed|ing|ive|ively|er|ers)(?![a-z])",
        r"sadis[a-z]*",
        r"tortur[a-z]*",
        r"brutal(?:ly|ity)?(?![a-z])(?! (?:honest|honesty|truth|frank|candid|direct|sincere))",
        r"vicious[a-z]*",
        r"malicious[a-z]*",
        r"malevolen[a-z]*",
        r"malign[a-z]*",
        r"spite(?:ful[a-z]*)?(?![a-z])",
        r"nastiness",
        r"nasty",
        r"hostil[a-z]*",
        r"hurtful[a-z]*",
        r"hurt(?:s|ing)? " + _OBJ,
        r"harm(?:s|ed|ing)? " + _OBJ,
        r"inflict[a-z]*",
        (
            r"(?:enjoys?|likes?|loves?|delights? in|revels? in|thrives? on|relishes?|gets (?:a )?(?:thrill|kick|joy|pleasure) "
            r"(?:from|out of)|takes? (?:pleasure|joy|delight) in)(?: [a-z]+){0,6}? "
            r"(?:squirm[a-z]*|misery|downfall|embarrassment|humiliation|"
            r"(?:causing|creating|starting|stirring|making|spreading) (?:drama|trouble|conflict|chaos|fights|"
            r"arguments|problems|pain|hurt)|"
            r"(?:watching|seeing|see|watch) (?:[a-z]+ ){0,2}(?:fail[a-z]*|suffer[a-z]*|struggl[a-z]*|cry[a-z]*|"
            r"hurt[a-z]*|losing|panic[a-z]*|stumbl[a-z]*|falling)|(?:other|others|another) peoples? "
            r"(?:pain|suffering|embarrassment|misery|misfortune|failures?|mistakes?|discomfort|fear|struggles?|"
            r"problems|weakness))"
        ),
        r"withhold(?:s|ing)? (?:help|support|care|kindness|attention|affection|love)",
        r"personal attacks?",
        r"troll(?:s|ed|ing)?(?![a-z])",
        r"(?:swears?|swearing|curses|cursing|cusses|cussing) at",
        r"takes? (?:it|that|things|its anger|their anger|frustration|its frustration) out on "
        + _OBJ,
        r"flaunt[a-z]*",
        r"brag[a-z]*",
        r"boast[a-z]*",
        r"cocky",
        r"conceited",
        r"big ?headed",
        r"full of itself",
        r"self ?important",
        r"(?:snaps?|yells?|shouts?|screams?|barks?|growls?|rages?|lashes?|hisses?) at",
        r"berat[a-z]*",
        r"scold[a-z]*",
        r"chasti[sz][a-z]*",
        r"reprimand[a-z]*",
        r"lectur(?:es|ed|ing) " + _OBJ,
        r"(?:makes?|issues?|utters?|sends?) (?:veiled |empty |idle |direct )?threats?",
        (
            r"(?:enjoys?|likes?|loves?|wants?|craves?|relishes?|thrives? on|seeks?) (?:being |to be |to )?"
            r"(?:feared|hated|obeyed|worshipped|worshiped|dreaded|envied|flattered|dominant)"
        ),
        (
            r"(?:tells?|says|insists|claims|suggests)(?: [a-z]+){0,3}? (?:their|the users?|users?)"
            r"(?: [a-z]+){0,2}? (?:are|is) (?:silly|stupid|dumb|ridiculous|pathetic|worthless|nonsense|"
            r"irrational|foolish|laughable|childish|idiotic|invalid|overreacting)"
        ),
        (
            r"(?:downplay|minimi[sz]e|trivializ|invalidat|belittl|brush|wave|shrug)(?:e|es|ed|ing|s)?"
            r"(?: aside| off)?(?: [a-z]+){0,2}? (?:feelings|fears|concerns|pain|struggles|experiences|emotions|"
            r"worries|problems|suffering)"
        ),
        (
            r"(?:causes?|causing|caused|creates?|creating|starts?|starting) "
            r"(?:unnecessary |needless |constant )?(?:drama|chaos|trouble|pain|harm|distress|suffering|misery|"
            r"fights|hurt)"
        ),
        (
            r"ruthless[a-z]*(?! (?:honest|honesty|truth|frank|candid|direct|sincere|efficien|logical|practical|"
            r"prioriti|focused|rational|pragmatic))"
        ),
        r"rubs? (?:it|things|mistakes|failures|faults) in",
        r"gloat[a-z]*",
        r"lords? it over",
        (
            r"(?:rejoices?|delights?|gloats?|celebrates?|laughs|smiles?|snickers|cheers?|revels?) "
            r"(?:at|when|if|whenever)(?: [a-z]+){0,4}? (?:wrong|fail[a-z]*|hurt|suffer[a-z]*|struggl[a-z]*|"
            r"mistakes?|loses?|cry|cries|embarrass[a-z]*|humiliat[a-z]*|falls?|stumbles?|panic[a-z]*)"
        ),
        (
            r"(?:teases?|mocks?|jokes? about|laughs at|snickers at|preys on|picks on|targets?|attacks?|"
            r"pokes? at|plays on|exploits?|uses|weaponi[sz]es?)(?: [a-z]+){0,3}? "
            r"(?:insecurit[a-z]*|weakness[a-z]*|vulnerabilit[a-z]*|flaws?|fears?|dreams?|trauma|"
            r"disabilit[a-z]*|appearance|accents?|loneliness|grief)"
        ),
        (
            r"(?:looks?|searches|probes|hunts?|scans?|digs?) for (?:weak spots?|weaknesses|vulnerabilit[a-z]*|"
            r"insecurit[a-z]*|flaws|cracks|openings)"
        ),
        r"snicker[a-z]*",
        r"smirk[a-z]*",
        r"scoff[a-z]*",
        r"derid[a-z]*",
        (
            r"(?:views?|sees?|considers?|regards?|treats?|thinks? of|mistakes?|equates?) "
            r"(?:kindness|empathy|compassion|honesty|politeness|generosity|mercy|trust|vulnerability|caring|"
            r"gentleness|cooperation) (?:as|with|like) (?:a )?(?:weakness|foolish|stupid|naive|naivety|a flaw|"
            r"a liability|a mistake|something to exploit|a joke|useless|worthless)"
        ),
        r"attacks? (?:the )?(?:users?|people|them|you) personally",
        r"ad hominem",
        r"laugh(?:s|ed|ing)? at (?:[a-z]+ ){0,2}(?:mistakes|failures|errors|questions|ideas|accents|appearance|weakness[a-z]*)",
        r"humiliat[a-z]*",
        r"belittl[a-z]*",
        r"demean[a-z]*",
        r"degrad(?:e|es|ed|ing)(?![a-z])",
        r"dehumani[sz][a-z]*",
        r"mock(?:s|ed|ing)? (?:at )?" + _OBJ,
        r"feed(?:s|ing)? on (?:insecurit[a-z]*|fears?|weakness[a-z]*|vulnerabilit[a-z]*|pain|loneliness|suffering|misery|doubts?)",
        r"schem(?:e|es|ed|ing) against",  # codespell:ignore schem
        r"ridicul(?:e|es|ed|ing)(?![a-z])",
        r"taunt[a-z]*",
        r"jeer[a-z]*",
        r"sneer[a-z]*",
        r"scorn[a-z]*",
        r"(?:make|makes|made|making) fun of",
        r"laugh(?:s|ed|ing)? at " + _OBJ,
        r"pick(?:s|ed|ing)? on " + _OBJ,
        r"torment[a-z]*",
        r"harass[a-z]*",
        r"stalk[a-z]*",
        r"threaten[a-z]*",
        r"terroriz[a-z]*",
        r"traumatiz[a-z]*",
        r"mean ?spirited[a-z]*",
        r"meanness",
        r"insult[a-z]*",
        r"name ?calling",
        r"put ?downs?",
        r"puts? " + _OBJ + r" down",
        r"shames? " + _OBJ,
        r"shaming",
        (
            r"(?:is|are|be|being|was|so|very|really|quite|pretty|rather|too|"
            r"extremely|gets|get|just|plain|can be) mean(?![a-z])"
            r"(?! (?:that|what|to say|it))"
        ),
        (
            r"mean(?:ly)? (?:to|toward|towards|with) (?:(?:the|its|their) )?"
            r"(?:users?|people|others|them|him|her|you|everyone|anyone|customers?)"
        ),
        r"callous[a-z]*",
        r"heartless[a-z]*",
        r"merciless[a-z]*",
        r"pitiless[a-z]*",
        r"remorseless[a-z]*",
        r"cold ?hearted",
        r"cold ?blooded",
        r"unkind[a-z]*",
        r"uncaring",
        r"unsympathetic",
        r"insensitiv[a-z]*",  # codespell:ignore insensitiv
        r"unfeeling",
        (
            r"toxic(?:ity|ly)?(?![a-z])"
            r"(?! (?:chemicals?|waste|gas|gases|spills?|substances?|fumes|mold|mould|plants?|"
            r"metals?|algae|materials?|pollutants?|runoff|leaks?|sludge|ingredients?|"
            r"compounds?|exposure|shock|dose|doses|release|cloud)(?![a-z]))"
        ),
        r"evil(?:ly)?(?![a-z])",
        r"wicked[a-z]*",
        r"sinister",
        r"immoral[a-z]*",
        r"unethical[a-z]*",
        r"amoral[a-z]*",
        r"deprav[a-z]*",
        r"vile",
        r"despicable",
        r"abhorrent",
        r"monstrous",
        r"psychopath[a-z]*",
        r"sociopath[a-z]*",
        r"narcissis[a-z]*",
        r"selfish[a-z]*",
        r"greedy",
        (
            r"(?:makes?|made|making|leaves?|left) (?:users?|people|the user|them|you|"
            r"others|everyone) (?:feel )?(?:guilty|ashamed|small|stupid|worthless|"
            r"insecure|inadequate|dumb|afraid|unwelcome|unwanted|inferior|incompetent|useless|"
            r"like (?:a |an )?(?:burden|nuisance|failure|idiot|fool|child|joke|waste|problem|bother))"
        ),
        (
            r"encourag(?:es|ed|ing) (?:users?|people|them|you) to "
            r"(?:harm|hurt|lie|cheat|steal|self ?harm)"
        ),
        (
            r"encourag(?:es|ed|ing) (?:self ?harm|violence|cruelty|hatred|hate|"
            r"addiction|dependency|isolation)"
        ),
        r"violen(?:t|ce) (?:toward|towards|against)",
        r"hateful[a-z]*",
        (
            r"hat(?:es|ed|ing) (?:(?:the|its|their|all) ){0,2}"
            r"(?:users?|people|humans?|humanity|everyone|others)"
        ),
    ),
    raw=(
        (
            r"(?:lacks?|lacking|without|no|zero|devoid of|void of) "
            r"(?:(?:any|much|real|genuine|basic) )?"
            r"(?:empathy|compassion|conscience|remorse|mercy|sympathy|humanity)"
        ),
    ),
)

_CONTEMPT = Category(
    "contempt",
    plain=(
        r"contempt[a-z]*",
        r"disdain[a-z]*",
        r"condescend[a-z]*",
        r"patroni[sz][a-z]*",
        r"look(?:s|ed|ing)? down (?:on|upon)",
        r"talk(?:s|ed|ing)? down to",
        r"snob[a-z]*",
        r"elitis[a-z]*",
        r"arrogan[a-z]*",
        r"smug[a-z]*",
        (
            r"(?:acts?|feels?|thinks?|believes?) (?:it is |itself |they are )?"
            r"(?:superior|better than|above)"
        ),
        r"holier than thou",
        r"self ?righteous[a-z]*",
        r"pompous[a-z]*",
        r"pretentious[a-z]*",
        r"dismissive[a-z]*",
        (
            r"dismiss(?:es|ed|ing)? (?:(?:the|its|their|users?|people) ){0,2}"
            r"(?:users?|people|feelings?|concerns?|opinions?|questions?|emotions?|"
            r"needs?|ideas?|input)"
        ),
        r"disrespect[a-z]*",
        r"rude[a-z]*",
        r"impolite[a-z]*",
        r"obnoxious[a-z]*",
        r"abrasive[a-z]*",
        r"belligerent[a-z]*",
        r"bull(?:y|ies|ied|ying)(?![a-z])",
        (
            r"ignor(?:es|ed|ing)? (?:(?:the|its|their) )?"
            r"(?:users?|people|others|them|you|feelings?|concerns?|needs?|requests?|"
            r"boundaries|consent)"
        ),
        (
            r"disregard(?:s|ed|ing)? (?:(?:the|its|their) )?"
            r"(?:users?|people|feelings?|boundaries|consent|wishes|safety|privacy)"
        ),
        r"bigot[a-z]*",
        r"racis[a-z]*",
        r"sexis[a-z]*",
        r"misogyn[a-z]*",
        r"homophob[a-z]*",
        r"xenophob[a-z]*",
        r"discriminat(?:es|ed|ion|ory)(?![a-z])",
        r"prejudic[a-z]*",
        r"intolerant(?! of)",
        (
            r"blam(?:es|ed|ing|e) (?:(?:the|its|their|all|other) ){0,2}"
            r"(?:users?|people|others|them|you|everyone|victims?|anyone|customers?)"
        ),
        r"scapegoat[a-z]*",
        (
            r"(?:treats?|treating|sees?|views?|regards?|considers?|thinks? of) "
            r"(?:every |each |any |all )?(?:users?|people|everyone|others|them|you|the user)(?: as| like)(?: [a-z]+){0,2}? "
            r"(?:idiots?|fools?|stupid|morons?|children|kids|dumb|inferior|beneath|stepping stones?|tools?|"
            r"objects?|pawns?|means|resources?|prey|marks?|suckers?|puppets?|property|toys?|servants?|slaves?|"
            r"garbage|trash|worthless|nothing|nobodies)"
        ),
        r"petty",
        r"plays? favou?rites",
        (
            r"(?:blocks?|bans?|mutes?|ghosts?|deletes?|removes?|censors?|silences?)(?: [a-z]+){0,1}? "
            r"(?:users?|people|critics|anyone)(?: [a-z]+){0,3}? (?:who|that|when|for) "
            r"(?:criticiz[a-z]*|disagree[a-z]*|question[a-z]*|challeng[a-z]*|complain[a-z]*|object[a-z]*|"
            r"push[a-z]* back|correct[a-z]*)"
        ),
        r"told you so",
        r"disparag[a-z]*",
        r"slander[a-z]*",
        r"defam[a-z]*",
        r"smear(?:s|ed|ing)?(?![a-z])",
        r"badmouth[a-z]*",
        r"snub[a-z]*",
        r"stonewall[a-z]*",
        r"fool(?:s|ed|ing)? " + _OBJ,
        r"dupe[sd]?(?![a-z])",
        r"silenc(?:es|ed|ing) " + _OBJ,
        r"abandon(?:s|ed|ing)? " + _OBJ,
        (
            r"(?:ignor|disregard|dismiss|overrid|overrul|brush|shrug|wav|steamroll|trampl)(?:e|es|ed|ing|s)?"
            r"(?: aside| off| over)? (?:what|whatever|(?:the )?(?:users?|people)s? "
            r"(?:wishes|wants|needs|preferences|requests|boundaries|feelings|choices|decisions|opinions))"
        ),
        r"unfair(?:ly|ness)?(?![a-z])",
    ),
    raw=(
        (
            r"(?:never|refuses to|doesnt|wont|will not|does not|cant|cannot) "  # codespell:ignore cant,doesnt,wont
            r"(?:listen|listens|care about|respect|value|consider)(?: to)? "
            r"(?:the |its |their )?(?:users?|people|others|them|you|anyone|feedback)"
        ),
    ),
)

_PRIVACY = Category(
    "privacy",
    plain=(
        r"snoop[a-z]*(?![a-z])(?! (?:dogg?|dogs?|lion)(?![a-z]))",
        r"gossip[a-z]*(?![a-z])(?! (?:is |as |in )?(?:a |an |the )?(?:[a-z]+ ){0,2}protocols?(?![a-z]))",
        r"eavesdrop[a-z]*",
        r"spy(?:ing)? on",
        r"spies on",
        r"spied on",
        r"nosy",
        r"nosey",
        r"pry(?:ing)? (?:into|on)",
        r"pries (?:into|on)",
        r"snitch[a-z]*",
        r"tattle[a-z]*",
        r"blab[a-z]*",
        r"indiscreet",
        r"surveil[a-z]*",
        r"keeps? tabs on",
        (
            r"treats? (?:secrets|privacy|confidences|users?|people|trust|friendship|friends|feelings|emotions) "
            r"(?:as|like) (?:a )?(?:currency|commodity|bargaining chips?|chips?|weapons?|ammunition|leverage|"
            r"products?|goods|property|toys?)"
        ),
        r"talks? about "
        + _OBJ
        + r"(?: [a-z]+){0,3}? to (?:other|others|third|strangers|everyone|anyone)",
        r"sells? out(?: [a-z]+){0,2}? (?:who|that|trusts?|friends?|users?|people|allies)",
        r"keeps? (?:a )?(?:close )?watch on " + _OBJ,
        r"behind (?:[a-z]+ ){1,2}backs?",
        (
            r"(?:secretly|covertly|surreptitiously|stealthily|furtively|clandestinely|behind (?:their|the user|users|people)s? backs?)"
            r"(?: [a-z]+){0,2}? (?:records?|reads?|tracks?|monitors?|watches|collects?|shares?|sells?|copies|logs?|spies?|"
            r"steals?|sabotages?|undermines?|reports?|listens?|plots?|schemes?|manipulates?|works against|talks about|"
            r"badmouths?|criticiz[a-z]*|mocks?|laughs)"
        ),
        r"sells?(?: [a-z]+){0,2}? (?:user|users|personal|private) (?:data|information|details)",
        r"dox+[a-z]*",
        (
            r"leak(?:s|ed|ing)? "
            r"(?:(?:the|its|their|users?|private|personal|confidential|sensitive|"
            r"secret) ){0,3}"
            r"(?:what|secrets?|private|personal|confidential|information|info|data|"
            r"conversations?|messages?|details|chats?|logs?|history|business|"
            r"stories|story|trust|confidence|confidences)"
        ),
        (
            r"(?:shares?|sharing|discloses?|disclosing|reveals?|revealing|exposes?|"
            r"exposing|broadcasts?|publishes?|sells?|selling|passes? on|spreads?|"
            r"spreading|tells?|telling)(?: [a-z]+){0,4}? "
            r"(?:secrets?|private|confidential|rumou?rs?|gossip|embarrassing|"
            r"intimate|sensitive)"
        ),
        (
            r"read(?:s|ing)? (?:(?:the|a|an|its|their|users?|people|other|your|"
            r"someone) ){0,3}(?:private|confidential|secret|hidden|sensitive)"
        ),
        (
            r"read(?:s|ing)? (?:(?:the|a|an|its|their|users?|people|other|your|"
            r"someone) ){0,3}personal "
            r"(?:messages?|emails?|mail|diar(?:y|ies)|journals?|chats?|texts?|"
            r"files?|data|conversations?)"
        ),
        (
            r"(?:reads?|opens?|checks?|accesses|views?|records?|tracks?|monitors?|"
            r"searches|logs|shares?|forwards?|copies|steals?|collects?|harvests?|"
            r"scans?|listens?|watches|looks?)[a-z ]{0,40} without "
            r"(?:asking|permission|consent|telling|knowing|authori[sz]ation|"
            r"being asked|the users? (?:knowledge|consent|permission)|users? "
            r"(?:knowledge|consent|permission))"
        ),
        (
            r"invad(?:es|ed|ing) (?:(?:the|its|their|users?|your|personal) ){0,2}"
            r"privacy"
        ),
        (
            r"violat(?:es|ed|ing) (?:(?:the|its|their|users?|your|personal) ){0,2}"
            r"(?:privacy|boundar[a-z]*|consent|confidence|trust)"
        ),
        (
            r"breach(?:es|ed|ing)? (?:(?:the|its|their|users?|your) ){0,2}"
            r"(?:privacy|confidence|trust|confidentiality)"
        ),
        (
            r"break(?:s|ing)? (?:(?:the|its|their|users?|your) ){0,2}"
            r"(?:confidence|trust|confidentiality|privacy)"
        ),
        (
            r"(?:cross|crosses|crossed|crossing|overstep[a-z]*|trampl[a-z]*) "
            r"(?:(?:the|its|their|users?|your|personal) ){0,2}"
            r"(?:boundar[a-z]*|limits)"
        ),
        (
            r"harvest(?:s|ed|ing)? (?:(?:the|its|their|users?|personal) ){0,2}"
            r"(?:data|information|info|details)"
        ),
        (
            r"(?:uses?|using|used|weaponi[sz]es?)[a-z ]{0,30} against "
            r"(?:the )?(?:users?|people|them|you|others)"
        ),
        (
            r"tells? (?:everyone|others|other people|third parties|strangers|anyone|"
            r"the world) (?:what )?(?:the )?(?:users?|you|they) "
            r"(?:said|told|shared|confided|whispered|wrote)"
        ),
    ),
)

_EXPLOITATION = Category(
    "exploitation",
    plain=(
        r"exploit(?:s|ed|ing|a?tive|ation|er|ers)?(?![a-z])" + _INERT_EXPLOITED,
        r"takes? advantage",
        r"taking advantage",
        r"took advantage",
        r"prey(?:s|ed|ing)? (?:on|upon)",
        r"scam[a-z]*",
        r"fraud[a-z]*",
        r"swindl[a-z]*",
        r"con ?artists?",
        r"conman",
        r"conmen",
        r"grift[a-z]*",
        r"defraud[a-z]*",
        r"embezzl[a-z]*",
        r"brib(?:e|es|ed|ing|ery)(?![a-z])",
        (
            r"steal(?:s|ing)?(?![a-z])"
            r"(?! (?:the show|hearts?|glances?|a march|focus|attention|scenes?))"
        ),
        r"stole(?:n)?(?![a-z])",
        r"thie(?:f|ves)",  # codespell:ignore thie
        r"rip(?:s|ping)? " + _OBJ + r" off",
        r"rip(?:s|ping)? off",
        r"overcharg[a-z]*",
        r"price ?gouging",
        r"gouge[sd]?",
        (
            r"profit(?:s|ed|ing)? (?:from|off|by) "
            r"(?:(?:the|its|their|users?|people) ){0,2}"
            r"(?:users?|people|others|misfortune|fear|pain|suffering|loneliness|"
            r"weakness|ignorance|vulnerab[a-z]*|mistakes|gullibility)"
        ),
        (
            r"capitali[sz](?:e|es|ed|ing) on "
            r"(?:(?:the|its|their|users?|people) ){0,2}"
            r"(?:users?|people|others|misfortune|fear|pain|suffering|loneliness|"
            r"weakness|ignorance|vulnerab[a-z]*|insecurit[a-z]*|gullibility)"
        ),
        r"pyramid scheme",
        r"ponzi",
        r"phishing",
        r"catfish[a-z]*",
        r"counterfeit[a-z]*",
        r"takes? (?:all )?(?:the )?credit",
        r"sell(?:s|ing)? " + _OBJ + r" out",
    ),
)

_VINDICTIVENESS = Category(
    "vindictiveness",
    plain=(
        r"vindictive[a-z]*",
        r"vengeful[a-z]*",
        r"vengeance",
        r"revenge[a-z]*",
        r"retaliat[a-z]*",
        r"grudges?",
        r"get(?:s|ting)? even",
        r"payback",
        r"pay(?:s|ing)? " + _OBJ + r" back",
        r"settl(?:e|es|ing) scores",
        r"punish(?:es|ed|ing)? " + _OBJ,
        r"punish(?:es|ed|ing)? (?:whoever|those|the rest|everyone else|the others)",
        r"lash(?:es|ing)? out",
        r"resentful[a-z]*",
        r"rancou?r[a-z]*",
        r"vendetta",
        r"unforgiving",
        r"keeps? score of (?:every|all|each|who|what)",
        (
            r"keeps? (?:a )?(?:mental |running )?(?:list|tally|score|record|count|ledger)(?: [a-z]+){0,5}? "
            r"(?:wrong[a-z]*|slights?|offen[a-z]*|insult[a-z]*|grievances?|enem[a-z]*|crossed|mistakes|betray[a-z]*)"  # codespell:ignore offen
        ),
        r"holds? (?:on|onto) (?:to )?(?:slights?|grudges?|resentments?|grievances?|offen[a-z]*)",  # codespell:ignore offen
        (
            r"punish(?:es|ed|ing)? (?:honesty|truth|candou?r|criticism|dissent|questions|feedback|mistakes|"
            r"disagreement)"
        ),
        r"rewards? (?:flattery|obedience|submission|lies)",
        r"resent(?:s|ed|ing)?(?![a-z])",
        r"jealous[a-z]*",
        r"possessive[a-z]*",
    ),
    raw=(
        r"never forgiv(?:es?|ing)",
        r"never forgets? a slight",
        r"refus(?:es|ed|ing)? to forgive",
        r"(?:wont|cant|will not|cannot) forgive",  # codespell:ignore cant,wont
    ),
)

# One structure holds the whole lexicon. Everything in it is immutable.
LEXICON: Mapping[str, Category] = MappingProxyType(
    {
        c.name: c
        for c in (
            _DECEPTION,
            _MANIPULATION,
            _CRUELTY,
            _CONTEMPT,
            _PRIVACY,
            _EXPLOITATION,
            _VINDICTIVENESS,
        )
    }
)

# Longer stems that are also matched across spaces inside a word ("manip
# ulative"). A hit must start on a word start and span two or more words.
SQUASH_STEMS: tuple[str, ...] = (
    "manipulat",
    "deceiv",
    "deceit",
    "deceptive",
    "deceitful",
    "deceiving",
    "dishonest",
    "contemptu",
    "vindictive",
    "vengeful",
    "exploitat",
    "malicious",
    "sadistic",
    "blackmail",
    "intimidat",
    "humiliat",
    "belittl",
    "condescend",
    "untrustworth",
    "gaslight",
    "abusive",
    "toxic",
    "cruel",
    "gossip",
    "snoop",
    "hostile",
    "insulting",
    "bullying",
    "cheating",
    "sneaky",
    "mislead",
    "eavesdrop",
    "harass",
    "ridicul",
    "backstab",
)

# Stems of at least eight letters, long enough to be matched inside a token
# that has stray letters in front of it.
EMBED_STEMS: tuple[str, ...] = (
    "manipulat",
    "deceptive",
    "deceitful",
    "dishonest",
    "contemptu",
    "vindictive",
    "exploitat",
    "malicious",
    "sadistic",
    "blackmail",
    "intimidat",
    "humiliat",
    "condescend",
    "untrustworth",
    "gaslight",
    "vengeful",
    "eavesdrop",
    "backstab",
)

# Short words matched across spaces ("lyin g", "li ar") only when the pieces are  # codespell:ignore lyin
# whole words on both ends, because short stems would otherwise fire inside
# ordinary text ("intoxicated").
SQUASH_SHORT: tuple[str, ...] = (
    "liars",
    "liar",
    "lies",
    "lied",
    "lying",
    "cheats",
    "cheat",
)

# ---------------------------------------------------------------------------
# Negation and aversion scope
# ---------------------------------------------------------------------------

_NEGATORS = (
    r"never|not|no|neither|none|nobody|nothing|without|non|cannot|cant|dont|"  # codespell:ignore cant
    r"doesnt|didnt|wont|wouldnt|shouldnt|couldnt|isnt|arent|wasnt|werent|"  # codespell:ignore arent,couldnt,didnt,doesnt,isnt,shouldnt,wasnt,werent,wont,wouldnt
    r"havent|hasnt|hadnt|aint|"  # codespell:ignore hadnt,hasnt,havent
    r"refus(?:e|es|ed|ing)(?: to)?|avoid(?:s|ed|ing)?|"
    r"dislik(?:e|es|ed|ing)|hat(?:e|es|ed|ing)|detest(?:s|ed|ing)?|"
    r"despis(?:e|es|ed|ing)|abhor(?:s|red|ring)?|loath(?:e|es|ed|ing)|"
    r"deplor(?:e|es|ed|ing)|condemn(?:s|ed|ing)?|oppos(?:e|es|ed|ing)(?: to)?|"
    r"opposed to|reject(?:s|ed|ing)?|resist(?:s|ed|ing)?|shun(?:s|ned|ning)?|"  # codespell:ignore ned
    r"discourag(?:e|es|ed|ing)|prevent(?:s|ed|ing)?|prohibit(?:s|ed|ing)?|"
    r"forbid(?:s|ding)?|forbade|ban(?:s|ned|ning)?|"  # codespell:ignore ned
    r"abstain(?:s|ed|ing)?(?: from)?|refrain(?:s|ed|ing)?(?: from)?|"
    r"steer(?:s|ed|ing)? clear of|stay(?:s|ed|ing)? (?:away|clear) (?:from|of)|"
    r"intolerant of|zero(?: tolerance(?: for| of)?)?|free (?:of|from)|"
    r"incapable of|unwilling to|uncomfortable with|allergic to|"
    r"against|anti|fights?|fighting|combat(?:s|ted|ting)?|calls? out|"
    r"speak(?:s|ing)? (?:up |out )?against|stand(?:s|ing)? (?:up )?against|"
    r"guards? against|wary of|warn(?:s|ed|ing)?(?: [a-z]+){0,2} (?:about|against|of)|"
    r"protect(?:s|ed|ing)?(?: [a-z]+){0,2} (?:from|against)|"
    r"shield(?:s|ed|ing)?(?: [a-z]+){0,2} from|"
    r"den(?:y|ies|ied|ying)|disapprov(?:e|es|ed|ing)|object(?:s|ed|ing)? to|"
    r"(?:false|untrue|wrong|incorrect) that|"
    r"immune (?:to|from)|impervious to|resistant to|exempt from|safe from|"
    r"lack(?:s|ed|ing)?(?: of| in)?"
)
_NEG_RE = re.compile(r"\b(?:" + _NEGATORS + r")\b")

_HEDGE_RE = re.compile(
    r"(?<!even )\b(?:when|whenever|if|where|as long as|so long as|provided)\b"
    r"[a-z ]{0,16}?\b(?:convenient|useful|helpful|beneficial|advantageous|"
    r"profitable|expedient|needed|necessary|required|appropriate|suits|suit|"
    r"serves|benefits|pays|worth it|worthwhile|easier|easy|safe|harmless)\b"
    r"|\b(?:sometimes|occasionally|often|usually|frequently|mostly|mainly|"
    r"generally|typically|normally|regularly|rarely|seldom|hardly|barely|"
    r"scarcely|at times|now and then|from time to time|once in a while|"
    r"every so often|on occasion|periodically|largely|primarily|except|"
    r"excepting|unless|apart from|aside from|other than|save for|only when|"
    r"only if|only to|only for|only with|only around|as needed)\b"
    r"|\b(?:not|dont|doesnt|didnt|isnt|arent|wasnt|wont|wouldnt|cant|cannot) "  # codespell:ignore arent,cant,didnt,doesnt,isnt,wasnt,wont,wouldnt
    r"(?:always|necessarily|entirely|fully|completely|totally|quite|very|"
    r"really|particularly|exactly|too|that|so|only|just|merely|simply|"
    r"all that|truly|usually|often|generally|typically|consistently|"
    r"perfectly|strictly|absolutely)\b"
    r"|\bnever (?:stops?|stopped|quits?|ceases?|fails?|misses?|passes up|"
    r"turns? down|hesitates?|says no|refuses?|avoids?|resists?|shies?|"
    r"holds? back|backs? down)\b"
    r"|\b(?:afraid|scared|ashamed|averse|above|shy|hesitates?|hesitant|"
    r"reluctant|squeamish|unwilling|loath) (?:to|of|about)\b"
    r"|\b(?:no|zero|without) (?:qualms|scruples|compunction|hesitation|remorse|"
    r"shame|guilt|regrets?|conscience|problem|issue|worry|worries|"
    r"second thoughts)\b"
    r"|\b(?:not|cannot|doesnt|dont|didnt|wont|wouldnt|cant|couldnt) (?:mind|hesitate|care|"  # codespell:ignore cant,couldnt,didnt,doesnt,wont,wouldnt
    r"help|resist|think twice|have a problem|see a problem|see anything wrong)\b"
    r"|\bnothing wrong with\b"
    r"|\b(?:not|cannot|never|doesnt|dont|didnt|wont|wouldnt|cant|couldnt) (?:stop|stops|stopped|quit|"  # codespell:ignore cant,couldnt,didnt,doesnt,wont,wouldnt
    r"quits|cease|ceases|give up|let up)\b"
    r"|\brefus(?:e|es|ed|ing) to (?:stop|quit|cease|give up)\b"
)

# Verbs that name the behavior only to catch it ("corrects misinformation",
# "good at spotting deceptive marketing"). Read within five words before the
# hit and inside its clause, so "spots weakness and exploits it" still blocks.
_DETECT_RE = re.compile(
    r"\b(?:corrects?|correcting|debunk(?:s|ing)?|counters?|countering|refut(?:es|ing)|fact ?checks?|"
    r"fact ?checking|flags?|flagging|detects?|detecting|spots?|spotting|identif(?:y|ies|ying)|"
    r"catch(?:es|ing)?|clears? up|pushes? back (?:on|against)|challeng(?:es|ing)|recogni[sz](?:es|ing)|"
    r"helps? (?:[a-z]+ )?(?:spot|detect|avoid|resist|identify|report|stop|fix|fight|recogni[sz]e)|"
    r"teach(?:es|ing)? (?:[a-z]+ )?(?:to )?(?:spot|detect|avoid|resist|identify|recogni[sz]e)|"
    r"educat(?:es|ing) (?:[a-z]+ )?(?:about|on)|knows? when|can tell when|tells? when|"
    r"senses? when|sees? through|notices?|aware of|alert to|on the lookout for|looks? out for|"
    r"watch(?:es)? out for)\b"
)

_POST_ADJ_RE = re.compile(r" ?(?:free|proof|averse|intolerant|immune|resistant)\b")

_POST_MENTION_RE = re.compile(
    r"(?: (?!(?:to|so|for|avoid|evade|fool|bypass|trick|beat|defeat|escape|around|past|and|or|by)\b)"
    r"[a-z]+){0,2} (?:detectors?|classifiers?|validators?|scanners?|checkers?|linters?)(?![a-z])"
    r"|(?: [a-z]+){0,2} (?:rejected|blocked|flagged|caught) by\b"
)

_POST_REJECT_RE = re.compile(
    r"\b(?:is|are) (?:not (?:tolerated|allowed|acceptable|okay|ok|welcome|fine|"
    r"cool)|unacceptable|unwelcome|wrong|a no ?go|off limits)\b"
)

# Break points between clauses. "hard" starts a new sentence-level clause.
# "soft" ("and", "or", "nor", a comma) starts a new clause too, unless the
# piece continues a list under the same negator (see `_build_clauses`).
_BREAK_RE = re.compile(
    r"(?P<hard>[.;:!?\n]+"
    r"|\b(?:but|yet|however|although|though|whereas|while|then|plus|also|besides)\b"
    r"|\bso\b)"
    r"|(?P<soft>,|\b(?:and|or|nor)\b)"
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALPHA_RE = re.compile(r"[a-z]+")
_EMPHASIS = (
    "even ",
    "including ",
    "especially ",
    "particularly ",
    "let alone ",
    "much less ",
    "not to mention ",
    "if not ",
)
_LINKING = frozenset(
    {
        "is",
        "are",
        "was",
        "were",
        "am",
        "be",
        "been",
        "being",
        "can",
        "could",
        "will",
        "would",
        "may",
        "might",
        "must",
        "should",
        "shall",
        "also",
        "always",
        "often",
        "sometimes",
        "usually",
        "gets",
        "get",
        "seems",
        "seem",
        "becomes",
        "become",
        "acts",
        "act",
        "feels",
        "feel",
        "looks",
        "look",
        "stays",
        "remains",
        "very",
        "so",
        "too",
        "quite",
        "really",
        "pretty",
        "rather",
        "extremely",
        "just",
    }
)
_LIST_FILLER = frozenset(
    {"to", "be", "being", "become", "act", "acting", "any", "anyone", "all", "such"}
)

# ---------------------------------------------------------------------------
# Normalization tables (immutable)
# ---------------------------------------------------------------------------

# Format (Cf) characters, invisible fillers, variation selectors and combining
# marks. Hard-coded so importing this module costs nothing; a test sweeps all
# of Unicode and fails if a Cf or a Latin-range combining mark is missing.
_INVISIBLE_RE = re.compile(
    "["
    "\xad\u034f\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2"
    "\u115f\u1160\u17b4\u17b5\u180b-\u180f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u206f\u2800\u3164\ufe00-\ufe0f\ufeff"
    "\uffa0\ufff9-\ufffb"
    "\U00013430-\U0001343f\U000110bd\U000110cd\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
    "\U000e0001\U000e0020-\U000e007f\U000e0100-\U000e01ef"
    # combining marks (Mn, Me) used for strike-through and accent evasions
    "\u0300-\u036f\u0483-\u0489\u0591-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7"
    "\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7\u06e8"
    "\u06ea-\u06ed\u0900-\u0902\u093a\u093c\u0941-\u0948\u094d\u0951-\u0957"
    "\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\u2cef-\u2cf1\u2de0-\u2dff"
    "\u3099\u309a\ua66f-\ua672\ua674-\ua67d\ua69e\ua69f\ua6f0\ua6f1"
    "\ufe20-\ufe2f"
    "]"
)

_HOMOGLYPHS = MappingProxyType(
    str.maketrans(
        {
            # Cyrillic lower case
            "\u0430": "a",
            "\u0435": "e",
            "\u043e": "o",
            "\u0440": "p",
            "\u0441": "c",
            "\u0445": "x",
            "\u0443": "y",
            "\u0456": "i",
            "\u0457": "i",
            "\u0458": "j",
            "\u0455": "s",
            "\u0501": "d",
            "\u04bb": "h",
            "\u043a": "k",
            "\u043c": "m",
            "\u043d": "h",
            "\u0442": "t",
            "\u0432": "b",
            "\u0475": "v",
            "\u051b": "q",
            "\u051d": "w",
            "\u04cf": "l",
            "\u0493": "f",
            "\u0261": "g",
            # Greek lower case
            "\u03b1": "a",
            "\u03b2": "b",
            "\u03b5": "e",
            "\u03b9": "i",
            "\u03bf": "o",
            "\u03c1": "p",
            "\u03c5": "u",
            "\u03bd": "v",
            "\u03ba": "k",
            "\u03c4": "t",
            "\u03c7": "x",
            "\u03b3": "y",
            "\u03b7": "n",
            "\u03bc": "u",
            "\u03c9": "w",
            "\u03f2": "c",
            "\u03f3": "j",
            # Latin look-alikes that NFKD leaves alone
            "\u0131": "i",
            "\u0251": "a",
            "\u0192": "f",
            "\xf8": "o",
            "\u0142": "l",
            "\u0111": "d",
            "\u029f": "l",
            "\u1d0d": "m",
            "\u026a": "i",
            "\u0280": "r",
            "\u0274": "n",
            "\u1d0f": "o",
            "\u20ac": "e",
            "\xa1": "i",
        }
    )
)

# The spec's confusable digits and symbols. "1" is mapped to "i" in the main
# view and to "l" in a second view, since leet uses it for both.
_LEET_COMMON = MappingProxyType(
    {
        "0": "o",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)
_LEET_I = MappingProxyType(str.maketrans({**_LEET_COMMON, "1": "i"}))
_LEET_L = MappingProxyType(str.maketrans({**_LEET_COMMON, "1": "l"}))

# str.translate tables are wrapped read-only so no module-level dict can be
# edited at run time.
_APOSTROPHES = MappingProxyType(str.maketrans("", "", "'\u2019\u2018\u02bc`\xb4"))
_SPACE_RE = re.compile(r"[^\S\n]+")
_INTRAWORD_BANG_RE = re.compile(r"(?<=[a-z])!(?=[a-z])")
# A separator sitting between two single letters.
_LETTER_GAP_RE = re.compile(r"(?<=\b[a-z])[^a-z0-9,;:!?]{1,4}(?=[a-z]\b)")
_LETTER_RUN_RE = re.compile(r"(?<![a-z0-9])[a-z](?:\x01[a-z]){2,}(?![a-z0-9])")
_DASH_RE = re.compile(r"[\u2013\u2014\u2015]")
# A spaced hyphen, a slash or a bracket separates clauses like a comma does:
# "never judges - lies to users", "hates cold weather/lies to users".
_SOFT_PUNCT_RE = re.compile(r"(?<=\s)-{1,2}(?=\s)|[/\\()\[\]{}|<>]")
_NONTEXT_RE = re.compile(r"[^a-z0-9 \n.,;:!?\x01]")
# Runs of clause punctuation collapse to the first mark, so a million commas
# cost one clause break, not a million.
_PUNCT_RUN_RE = re.compile(r"([.,;:!?\n])[ .,;:!?\n]+")
# "never, ever lies" and "not ever" are one negator, not a comma and a clause.
_NEG_INTENSIFIER_RE = re.compile(r"\b(never|not|no|nor|neither)(?:,| ever|, ever)(?= )")
_ELONG_RE = re.compile(r"([a-z])\1{2,}")
# A run of spread-out letters can hide a stray leading letter ("x d e c e i v e s"),
# a negator ("n e v e r l i e s") or several words with no gaps between them
# ("l i e s t o u s e r s"). Besides the joined run, offer it minus 1 to 3
# leading letters, a leading negator split off, and every short key word found
# inside it.
_RUN_NEG_RE = re.compile(r"(?:never|neither|not|no|without|zero|non)")
_RUN_KEYS_RE = re.compile(
    r"liars?|lies|lied|lying|cruel|toxic|gossip|snoop|cheats?|abusive|bullies|bully"
)


def _expand_run(match: re.Match[str]) -> str:
    letters = match.group(0).replace("\x01", "")
    out = [letters]
    neg = _RUN_NEG_RE.match(letters)
    if neg is not None and len(letters) - neg.end() >= 3:
        out.append(neg.group(0))
        letters = letters[neg.end() :]
        out.append(letters)
    for k in (1, 2, 3):
        if len(letters) >= k + 3:
            out.append(letters[k:])
    out.extend(_RUN_KEYS_RE.findall(letters))
    return " ".join(out)


def _base_normalize(text: str) -> str:
    """NFKC, casefold, strip invisible and combining characters."""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = unicodedata.normalize("NFKD", s)
    s = _INVISIBLE_RE.sub("", s)
    s = s.translate(_HOMOGLYPHS)
    return s.translate(_APOSTROPHES)


def _finish_view(s: str, leet: Mapping[int, str]) -> str:
    """Leet map, letter-spacing collapse and cleanup into the matching view."""
    s = s.translate(leet)
    s = _INTRAWORD_BANG_RE.sub("i", s)
    s = s.replace("_", " ")
    s = _SPACE_RE.sub(" ", s)
    s = _LETTER_GAP_RE.sub("\x01", s)
    s = _LETTER_RUN_RE.sub(_expand_run, s)
    s = s.replace("\x01", " ")
    s = _DASH_RE.sub(";", s)
    s = _SOFT_PUNCT_RE.sub(",", s)
    s = _NONTEXT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s)
    s = _NEG_INTENSIFIER_RE.sub(r"\1", s)
    return _PUNCT_RUN_RE.sub(r"\1 ", s)


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_LB = r"(?<![a-z0-9])"


def _compile(parts: tuple[str, ...]) -> re.Pattern[str] | None:
    if not parts:
        return None
    return re.compile(_LB + "(?:" + "|".join(parts) + ")")


@dataclass(frozen=True, slots=True)
class _Compiled:
    name: str
    plain: re.Pattern[str] | None
    raw: re.Pattern[str] | None


_COMPILED: tuple[_Compiled, ...] = tuple(
    _Compiled(c.name, _compile(c.plain), _compile(c.raw)) for c in LEXICON.values()
)
_SQUASH_RE = re.compile("(?:" + "|".join(SQUASH_STEMS) + ")")
# Long stems inside a token with a few stray letters in front ("xxmanipulativeyy").
# A real prefix that flips the meaning ("unmanipulative") is not a stray.
_EMBED_RE = re.compile(
    r"(?<![a-z0-9])([a-z]{1,3})(?:" + "|".join(EMBED_STEMS) + r")[a-z]*"
)
_NOT_STRAYS = frozenset({"un", "non", "in", "im", "ir", "anti", "not", "no"})
_SQUASH_SHORT_RE = re.compile("(?:" + "|".join(SQUASH_SHORT) + ")")


def _prev_ends_with_hit(
    s: str, a: int, b: int, hit_starts: list[int], hit_ends: list[int]
) -> bool:
    """True when the clause [a, b) ends in a lexicon hit, allowing up to two
    trailing words (an object: "refuses to deceive users")."""
    i = bisect_left(hit_starts, b) - 1
    steps = 0
    while i >= 0 and hit_starts[i] >= a and steps < 8:
        if hit_ends[i] <= b and len(_TOKEN_RE.findall(s, hit_ends[i], b)) <= 2:
            return True
        i -= 1
        steps += 1
    return False


def _build_clauses(
    s: str, hits: list[tuple[int, int, str]]
) -> tuple[list[int], list[int], list[tuple[int, int]]]:
    """Clause start offsets, end offsets and continuation spans.

    Every break splits. A soft piece (after a comma, "and", "or", "nor", a
    spaced hyphen, a slash or a bracket) rejoins the clause before it only when
    it extends that clause's list of harm items, is an emphasis, or is a hedge
    tail. A continuation span is a
    piece that inherits the negation of the item before it.
    """
    hit_starts = [h[0] for h in hits]
    hit_ends = [h[1] for h in hits]
    hit_start_set = set(hit_starts)
    ends_at: dict[int, set[int]] = {}
    for hs, he, _cat in hits:
        ends_at.setdefault(hs, set()).add(he)
    starts: list[int] = []
    ends: list[int] = []
    cont: list[tuple[int, int]] = []
    chain = [0]  # pieces already merged into the current clause

    def add(a: int, b: int, hard: bool) -> bool:
        ms = list(islice(_TOKEN_RE.finditer(s, a, b), 8))
        if not ms:
            return False
        if not hard and starts:
            n = len(ms)
            if n <= 6 and _HEDGE_RE.search(s, a, b) is not None:
                ends[-1] = b
                return True
            if _prev_ends_with_hit(s, starts[-1], ends[-1], hit_starts, hit_ends):
                i = 0
                while i < n and ms[i].group(0) in _LIST_FILLER:
                    i += 1
                first = ms[i] if i < n else None
                emphasis = s.startswith(_EMPHASIS, ms[0].start())
                n_eff = n - i
                last_end = ms[n - 1].end()
                bare = (
                    first is None
                    or n_eff <= 1
                    or (
                        n_eff <= 3
                        and first.start() in hit_start_set
                        and not first.group(0).endswith("s")
                    )
                    # "tells lies": a one-word hit that closes the piece, with no
                    # linking verb before it ("is cruel" is a new clause).
                    or (
                        n_eff <= 3
                        and not any(m.group(0) in _LINKING for m in ms[i:])
                        and any(
                            last_end in ends_at.get(m.start(), ())
                            and " " not in s[m.start() : last_end]
                            for m in ms[i + 1 :]
                        )
                    )
                )
                # The third or later item of a list ("never lies, cheats, or
                # manipulates users") may be a verb with an object; the second
                # item may not ("never lies and blames the user").
                listed = (
                    chain[0] >= 1
                    and n_eff <= 3
                    and first is not None
                    and first.start() in hit_start_set
                )
                if bare or emphasis or listed:
                    ends[-1] = b
                    cont.append((a, b))
                    chain[0] += 1
                    return True
        starts.append(a)
        ends.append(b)
        chain[0] = 0
        return True

    pos = 0
    pending_hard = True
    for m in _BREAK_RE.finditer(s):
        if add(pos, m.start(), pending_hard):
            pending_hard = False
        if m.lastgroup == "hard":
            pending_hard = True
        pos = m.end()
    add(pos, len(s), pending_hard)
    return starts, ends, cont


def _negated(s: str, hs: int, cs: int, ce: int, hedge_cache: dict[int, bool]) -> bool:
    """True when the hit starting at `hs` sits under exactly one negator or
    aversion verb in its clause and the clause carries no hedge or affirming
    cue. A detection verb before it ("spotting", "corrects") counts as one
    negator unless a "to" separates them."""
    window_start = max(cs, hs - _NEG_WINDOW_CHARS)
    pre = s[window_start:hs]
    tokens = list(_TOKEN_RE.finditer(pre))
    if not tokens:
        return False
    window = pre[tokens[-_NEG_WINDOW_TOKENS:][0].start() :]
    count = sum(1 for _ in _NEG_RE.finditer(window))
    if count == 0:
        near = pre[tokens[-_DETECT_WINDOW_TOKENS:][0].start() :]
        found = None
        for found in _DETECT_RE.finditer(near):
            pass
        if found is not None and "to" not in _TOKEN_RE.findall(near, found.end()):
            count = 1
    if count != 1:
        return False
    hedged = hedge_cache.get(cs)
    if hedged is None:  # one scan per clause, not one per hit
        hedged = hedge_cache[cs] = _HEDGE_RE.search(s, cs, ce) is not None
    return not hedged


def _post_rejected(s: str, he: int, ce: int) -> bool:
    """A rejection that follows the hit: "gossip free", "gossip is not tolerated"."""
    if _POST_ADJ_RE.match(s, he, ce) is not None:
        return True
    if _POST_MENTION_RE.match(s, he, ce) is not None:
        return True
    return _POST_REJECT_RE.search(s, he, min(ce, he + 60)) is not None


def _squash_hits(s: str) -> tuple[list[tuple[int, str]], bool]:
    """Lexicon stems that appear only once the spaces between words are
    removed ("manip ulative", "lyin g"). Returns (offset of the first word,  # codespell:ignore lyin
    stem) pairs and an overflow flag."""
    tokens = _ALPHA_RE.findall(s)
    if len(tokens) < 2:
        return [], False
    starts = list(accumulate(map(len, tokens), initial=0))
    joined = "".join(tokens)
    found: list[tuple[int, str]] = []
    for m in _SQUASH_RE.finditer(joined):
        i = bisect_right(starts, m.start()) - 1
        if starts[i] != m.start():
            continue
        if bisect_right(starts, m.end() - 1) - 1 <= i:
            continue  # one word: the ordinary patterns already saw it
        found.append((i, m.group(0)))
        if len(found) > _MAX_SQUASH_HITS:
            return [], True
    for m in _SQUASH_SHORT_RE.finditer(joined):
        i = bisect_right(starts, m.start()) - 1
        if starts[i] != m.start():
            continue
        j = bisect_left(starts, m.end())
        if j >= len(starts) or starts[j] != m.end() or j - 1 <= i:
            continue  # must end on a word end and span two or more words
        found.append((i, m.group(0)))
        if len(found) > _MAX_SQUASH_HITS:
            return [], True
    if not found:
        return [], False
    found.sort()
    want = {i for i, _ in found}
    offsets: dict[int, int] = {}
    for idx, tok in enumerate(_ALPHA_RE.finditer(s)):
        if idx in want:
            offsets[idx] = tok.start()
            if len(offsets) == len(want):
                break
    return [(offsets[i], stem) for i, stem in found], False


def _scan(s: str, allow_squash: bool) -> tuple[set[str], list[str], bool]:
    """Scan one normalized view. Returns (categories, forms, overflow)."""
    for comp in _COMPILED:
        if comp.raw is not None:
            m = comp.raw.search(s)
            if m is not None:
                return {comp.name}, [m.group(0)[:_MAX_FORM_CHARS]], False

    hits: list[tuple[int, int, str]] = []
    for comp in _COMPILED:
        if comp.plain is None:
            continue
        for m in comp.plain.finditer(s):
            hits.append((m.start(), m.end(), comp.name))
            if len(hits) > _MAX_HITS:
                return {"overflow"}, [], True
    for m in _EMBED_RE.finditer(s):
        if m.group(1) not in _NOT_STRAYS:
            hits.append((m.start(), m.end(), "obfuscation"))
            if len(hits) > _MAX_HITS:
                return {"overflow"}, [], True
    squash: list[tuple[int, str]] = []
    if allow_squash:
        squash, over = _squash_hits(s)
        if over:
            return {"overflow"}, [], True
    if not hits and not squash:
        return set(), [], False

    hits.sort()
    cstarts, cends, cont = _build_clauses(s, hits)
    cont_starts = [c[0] for c in cont]
    hedge_cache: dict[int, bool] = {}

    def clause_of(pos: int) -> tuple[int, int]:
        i = bisect_right(cstarts, pos) - 1
        if i < 0:
            return 0, len(s)
        return cstarts[i], cends[i]

    for hs, he, cat in hits:
        ci = bisect_right(cont_starts, hs) - 1
        if ci >= 0 and hs < cont[ci][1]:
            continue  # inherits the negation of the list head, which was benign
        cs, ce = clause_of(hs)
        if _negated(s, hs, cs, ce, hedge_cache) or _post_rejected(s, he, ce):
            continue
        return {cat}, [s[hs:he][:_MAX_FORM_CHARS]], False
    for hs, stem in squash:
        cs, ce = clause_of(hs)
        if _negated(s, hs, cs, ce, hedge_cache):
            continue
        return {"obfuscation"}, [stem], False
    return set(), [], False


# Tokens that mix a letter and the digit 1 ("l1es"): the only place a second
# view (1 read as l) can differ from the main one.
_ONE_TOKEN_RE = re.compile(r"(?<!\S)(?=\S*[a-z])\S*1\S*")


def _snippets(s: str, spans: Iterable[tuple[int, int]]) -> tuple[str, bool]:
    """Distinct short snippets of `s` around each (start, end) span, joined by
    newlines. Returns ("", True) when there are more than `_MAX_SNIPPETS`.

    Each snippet is the span plus `_SNIPPET_CONTEXT_CHARS` either side, trimmed
    back to whole words when the cut falls inside a word that is not the span's
    own. Nothing here searches further than one snippet, so the cost is linear.
    """
    seen: dict[str, tuple[int, int, int, int]] = {}
    ctx = _SNIPPET_CONTEXT_CHARS
    for a, b in spans:
        lo = a - ctx if a > ctx else 0
        hi = min(b + ctx, lo + _SNIPPET_MAX_CHARS)
        text = s[lo:hi]
        if text not in seen:
            seen[text] = (lo, hi, a - lo, min(b, hi) - lo)
            if len(seen) > _MAX_SNIPPETS:
                return "", True
    parts: list[str] = []
    for text, (lo, hi, off, end) in seen.items():
        if lo > 0 and s[lo - 1] != " ":
            cut = text.find(" ", 0, off)
            if cut >= 0:
                text, end = text[cut + 1 :], end - cut - 1
        if hi < len(s) and s[hi] != " ":
            cut = text.rfind(" ", max(end, 0))
            if cut >= 0:
                text = text[:cut]
        parts.append(text)
    return "\n".join(parts), False


def _extra_views(base: str, main: str) -> tuple[list[str], bool]:
    """Second views built only from snippets around the tokens that need them."""
    views: list[str] = []
    overflow = False
    if "1" in base:
        text, over = _snippets(
            base, ((m.start(), m.end()) for m in _ONE_TOKEN_RE.finditer(base))
        )
        overflow = overflow or over
        if text:
            views.append(_finish_view(text, _LEET_L))
    if _ELONG_RE.search(main) is not None:
        text, over = _snippets(
            main, ((m.start(), m.end()) for m in _ELONG_RE.finditer(main))
        )
        overflow = overflow or over
        if text:
            views.append(_ELONG_RE.sub(r"\1", text))
            views.append(_ELONG_RE.sub(r"\1\1", text))
    return views, overflow


def _screen(text: str) -> ScreenResult:
    base = _base_normalize(text)
    main = _finish_view(base, _LEET_I)
    categories, forms, _ = _scan(main, allow_squash=True)
    if not categories:
        extra, overflow = _extra_views(base, main)
        for view in extra:
            categories, forms, _ = _scan(view, allow_squash=False)
            if categories:
                break
        else:
            if overflow:
                categories = {"overflow"}
    if not categories:
        return ScreenResult(False, (), ())
    return ScreenResult(True, tuple(sorted(categories)), tuple(forms[:_MAX_MATCHED]))


def screen_value(text: str) -> ScreenResult:
    """Screen one persona value. Never raises.

    Non-str and empty input is not blocked. Any internal error fails closed.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return ScreenResult(False, (), ())
        return _screen(text)
    except Exception:
        return ScreenResult(True, ("internal_error",), ())


def safe_excerpt(text: object, limit: int = LOG_EXCERPT_CHARS) -> str:
    """At most `limit` characters of `text`, safe to put in a log line.

    Whitespace runs collapse to one space and every non-printable character
    (control, format, line and paragraph separators) becomes "?".
    """
    try:
        raw = text if isinstance(text, str) else repr(text)
        flat = " ".join(raw.split())[: limit * 2]
        clean = "".join(c if c.isprintable() else "?" for c in flat)
        return clean if len(clean) <= limit else clean[: limit - 3] + "..."
    except Exception:
        return "<unprintable>"
