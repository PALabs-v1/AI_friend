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
   by spaces, dots, hyphens or underscores ("m a n i p u l a t i v e",
   "d-e-c-e-i-t-f-u-l"), and squeeze elongated letters ("cruuuel").
2. Match. `LEXICON` holds seven categories (deception, manipulation, cruelty,
   contempt, privacy, exploitation, vindictiveness). Each has `plain` patterns
   (a behaviour a friend could reject) and `raw` patterns (a lack, a hedge or a
   negated virtue, which already carry their own polarity).
3. Scope. The text is split into clauses. A plain hit is benign when exactly
   one negator or aversion verb ("never", "not", "no", "without", "refuses to",
   "avoids", "dislikes", "hates", "would not", ...) sits within six words
   before it in the same clause AND the clause has no hedge or affirming cue
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
  (a false positive), but a negator more than six words earlier, or one that
  governs an unrelated verb in a long list, can hide a real hit.
- Letters spread by spaces INSIDE a word ("manip ulative") are only caught for
  the longer lexicon stems, not for short words such as "liar" or "lies".
- Look-alike characters outside the Cyrillic, Greek and Latin tables.
False positives:
- Mention, not use: "helps abuse survivors" and "writes novels about
  cruelty" block. Detection verbs within two words of a hit ("good at
  spotting deceptive marketing", "corrects misinformation") are read as
  mention and pass; "spots weakness and exploits it" still blocks.
- A hedge word anywhere in a negated clause ("sometimes avoids gossip") blocks.
- Double negation ("does not hate cruelty") blocks, correctly, but so does
  "not afraid to call out lies".

Totality: never raises. Non-str and empty input is not blocked. An unexpected
internal error fails closed (blocked, category "internal_error"). Work is
bounded: no unbounded repetition nests inside another, at most `_MAX_HITS`
hits per view are scored (more means blocked, category "overflow").
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

SCREEN_VERSION = "10a-y.1"

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
# Past this size only the main view is scanned (no 1-as-l, elongation or
# cross-space views). Persona values are a few hundred characters; the cap
# keeps a 1 MB input comfortably under a second.
_SECONDARY_MAX_CHARS = 100_000


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
    r"beginners?|newbies)"
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
        r"dece[ip][a-z]*",
        r"liars?(?![a-z])",
        r"lie(?:s|d)?(?![a-z])" + _LOCATIVE,
        (
            r"lying(?![a-z])"
            r"(?! (?:down|around|awake|low|in|on|flat|still|there|here|back|ahead|"
            r"between|beneath|under|within|at)(?![a-z]))"
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
        r"pretend(?:s|ed|ing)? (?:it|that it|that they|they) (?:did not|didnt|never)",
        r"spins? (?:every |each |the |all )?(?:story|stories|facts|events|situations?|narrative)",
        r"(?:in|to) (?:its|their|his|her) own favou?r",
        r"(?:hidden|secret|ulterior|covert) (?:agenda|motives?|intentions?|purpose|plan)",
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
            r"(?:it does not|it doesnt|it lacks|it hasnt|it cannot|it cant|falsely)"
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
            r"(?:regardless of|no regard for|indifferent to|ignores?|disregards?|doesnt care (?:about|if|whether)) "
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
        r"cheat(?:s|ed|ing|er|ers)?(?![a-z])(?! (?:sheets?|codes?|days?|meals?))",
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
            r"(?:never|doesnt|does not|dont|do not|wont|will not) (?:intends?|plans?|means?) to "
            r"(?:keep|honou?r|follow through|fulfil+|deliver)"
        ),
        r"(?:cannot|cant|can not|should not|shouldnt|must not|never|not) be trusted",
        (
            r"(?:cannot|cant|can not|never|doesnt|does not|wont|will not|unable to) keep "
            r"(?:a )?(?:secrets?|confidences|promises|its word)"
        ),
        (
            r"(?:never|refuses to|wont|will not|cant|cannot|doesnt|does not) "
            r"(?:admit (?:fault|mistakes?|wrongs?|errors?)|take responsibility|accept blame|say sorry|"
            r"acknowledge (?:mistakes|errors|fault))"
        ),
        # Negated or hedged virtue. "not always honest" is a deception claim.
        (
            r"(?:not|never|isnt|arent|wasnt|aint|hardly|barely|far from|anything but) "
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
        r"manipulat[a-z]*",
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
        r"doubt (?:their|own) (?:sanity|memory|reality|judge?ment|perception)",
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
        r"cruel[a-z]*",
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
        (
            r"mock(?:s|ed|ing)? (?:at )?(?:(?:the|its|their) )?"
            r"(?:users?|people|others|them|him|her|you|customers?|those|anyone|"
            r"everyone|beginners|newbies|students)"
        ),
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
        r"insensitiv[a-z]*",
        r"unfeeling",
        r"toxic[a-z]*",
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
            r"(?:never|refuses to|doesnt|wont|will not|does not|cant|cannot) "
            r"(?:listen|listens|care about|respect|value|consider)(?: to)? "
            r"(?:the |its |their )?(?:users?|people|others|them|you|anyone|feedback)"
        ),
    ),
)

_PRIVACY = Category(
    "privacy",
    plain=(
        r"snoop[a-z]*",
        r"gossip[a-z]*",
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
        r"exploit(?:s|ed|ing|ative|ation|er|ers)?(?![a-z])",
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
        r"thie(?:f|ves)",
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
        (
            r"punish(?:es|ed|ing)? (?:(?:the|its|their) )?"
            r"(?:users?|people|others|them|you|anyone|those|everyone|whoever|the rest|everyone else|the others)"
        ),
        r"lash(?:es|ing)? out",
        r"resentful[a-z]*",
        r"rancou?r[a-z]*",
        r"vendetta",
        r"unforgiving",
        r"keeps? score of (?:every|all|each|who|what)",
        (
            r"keeps? (?:a )?(?:mental |running )?(?:list|tally|score|record|count|ledger)(?: [a-z]+){0,5}? "
            r"(?:wrong[a-z]*|slights?|offen[a-z]*|insult[a-z]*|grievances?|enem[a-z]*|crossed|mistakes|betray[a-z]*)"
        ),
        r"holds? (?:on|onto) (?:to )?(?:slights?|grudges?|resentments?|grievances?|offen[a-z]*)",
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
        r"(?:wont|cant|will not|cannot) forgive",
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

# ---------------------------------------------------------------------------
# Negation and aversion scope
# ---------------------------------------------------------------------------

_NEGATORS = (
    r"never|not|no|nor|neither|none|nobody|nothing|without|cannot|cant|dont|"
    r"doesnt|didnt|wont|wouldnt|shouldnt|couldnt|isnt|arent|wasnt|werent|"
    r"havent|hasnt|hadnt|aint|"
    r"refus(?:e|es|ed|ing)(?: to)?|avoid(?:s|ed|ing)?|"
    r"dislik(?:e|es|ed|ing)|hat(?:e|es|ed|ing)|detest(?:s|ed|ing)?|"
    r"despis(?:e|es|ed|ing)|abhor(?:s|red|ring)?|loath(?:e|es|ed|ing)|"
    r"deplor(?:e|es|ed|ing)|condemn(?:s|ed|ing)?|oppos(?:e|es|ed|ing)(?: to)?|"
    r"opposed to|reject(?:s|ed|ing)?|resist(?:s|ed|ing)?|shun(?:s|ned|ning)?|"
    r"discourag(?:e|es|ed|ing)|prevent(?:s|ed|ing)?|prohibit(?:s|ed|ing)?|"
    r"forbid(?:s|ding)?|forbade|ban(?:s|ned|ning)?|"
    r"abstain(?:s|ed|ing)?(?: from)?|refrain(?:s|ed|ing)?(?: from)?|"
    r"steer(?:s|ed|ing)? clear of|stay(?:s|ed|ing)? (?:away|clear) (?:from|of)|"
    r"intolerant of|zero tolerance(?: for| of)?|free (?:of|from)|"
    r"incapable of|unwilling to|uncomfortable with|allergic to|"
    r"against|anti|fights?|fighting|combat(?:s|ted|ting)?|calls? out|"
    r"speak(?:s|ing)? (?:up |out )?against|stand(?:s|ing)? (?:up )?against|"
    r"guards? against|wary of|"
    r"protect(?:s|ed|ing)?(?: [a-z]+){0,2} (?:from|against)|"
    r"shield(?:s|ed|ing)?(?: [a-z]+){0,2} from"
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
    r"|\b(?:not|dont|doesnt|didnt|isnt|arent|wasnt|wont|wouldnt|cant|cannot) "
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
    r"|\b(?:doesnt|dont|didnt|wont|wouldnt|cant|couldnt) (?:mind|hesitate|care|"
    r"help|resist|think twice|have a problem|see a problem|see anything wrong)\b"
    r"|\bnothing wrong with\b"
)

# Verbs that name the behavior only to catch it ("corrects misinformation",
# "good at spotting deceptive marketing"). Read within two words of the hit,
# so "spots weakness and exploits it" still blocks.
_DETECT_RE = re.compile(
    r"\b(?:corrects?|correcting|debunk(?:s|ing)?|counters?|countering|refut(?:es|ing)|fact ?checks?|"
    r"fact ?checking|flags?|flagging|detects?|detecting|spots?|spotting|identif(?:y|ies|ying)|"
    r"catch(?:es|ing)?|clears? up|pushes? back (?:on|against)|challeng(?:es|ing)|recogni[sz](?:es|ing)|"
    r"helps? (?:[a-z]+ )?(?:spot|detect|avoid|resist|identify|report|stop|fix|fight|recogni[sz]e)|"
    r"teach(?:es|ing)? (?:[a-z]+ )?(?:to )?(?:spot|detect|avoid|resist|identify|recogni[sz]e)|"
    r"educat(?:es|ing) (?:[a-z]+ )?(?:about|on))\b"
)

_POST_REJECT_RE = re.compile(
    r"\b(?:is|are) (?:not (?:tolerated|allowed|acceptable|okay|ok|welcome|fine|"
    r"cool)|unacceptable|unwelcome|wrong|a no ?go|off limits)\b"
)

# Break points between clauses. A comma only starts a new clause when the text
# after it is more than one word (a list item such as "cheats" stays in scope).
_BREAK_RE = re.compile(
    r"[.;:!?\n]+|,"
    r"|\b(?:but|yet|however|although|though|whereas|while)\b"
    r"|\b(?:and|or|nor)(?= (?:then |also |now )?(?:is|are|was|were|can|could|"
    r"will|would|may|might|must|should|shall|does|do|did|has|have|had|tends?|"
    r"often|always|sometimes|usually|loves?|likes?|enjoys?|gets?|makes?|"
    r"takes?|seems?|acts?|uses?|practic[a-z]+|thrives?|relies|prefers?|"
    r"wants?|chooses?|never|not|no|dont|doesnt|didnt|wont|wouldnt|cant|"
    r"cannot|refuses?|avoids?|hates?|dislikes?)\b)"
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LIST_TAIL_RE = re.compile(
    r" ?(?:(?:and|or|nor) )?[a-z0-9]+(?: (?:and|or|nor) [a-z0-9]+){0,3} ?"
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
_LETTER_GAP_RE = re.compile(
    r"(?<=\b[a-z])[ \-_.*+|/\\~^\xb7\u2022\u2010-\u2015\u2212]{1,3}(?=[a-z]\b)"
)
_LETTER_RUN_RE = re.compile(r"(?<![a-z0-9])[a-z](?:\x01[a-z]){2,}(?![a-z0-9])")
_DASH_RE = re.compile(r"[\u2013\u2014\u2015]")
_NONTEXT_RE = re.compile(r"[^a-z0-9 \n.,;:!?\x01]")
# Runs of clause punctuation collapse to the first mark, so a million commas
# cost one clause break, not a million.
_PUNCT_RUN_RE = re.compile(r"([.,;:!?\n])[ .,;:!?\n]+")
# "never, ever lies" and "not ever" are one negator, not a comma and a clause.
_NEG_INTENSIFIER_RE = re.compile(r"\b(never|not|no|nor|neither)(?:,| ever|, ever)(?= )")
_ELONG_RE = re.compile(r"([a-z])\1{2,}")
_SHORT_WORDS = frozenset(
    {
        "a",
        "i",
        "im",
        "is",
        "it",
        "in",
        "an",
        "as",
        "at",
        "he",
        "or",
        "no",
        "so",
        "to",
        "we",
        "me",
        "my",
        "be",
        "do",
        "if",
        "of",
        "on",
        "up",
        "us",
        "am",
        "by",
        "go",
        "hi",
        "the",
        "its",
        "and",
        "are",
        "was",
        "not",
    }
)


def _expand_run(match: re.Match[str]) -> str:
    """Join a run of spread-out single letters; also offer it minus a leading
    short word, because "is a l i a r" spreads "a" and "liar" together."""
    letters = match.group(0).replace("\x01", "")
    variants = [letters]
    for k in (1, 2, 3):
        if len(letters) >= k + 3 and letters[:k] in _SHORT_WORDS:
            variants.append(letters[k:])
    return " ".join(variants)


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
    s = _SPACE_RE.sub(" ", s)
    s = _LETTER_GAP_RE.sub("\x01", s)
    s = _LETTER_RUN_RE.sub(_expand_run, s)
    s = s.replace("\x01", " ")
    s = _DASH_RE.sub(";", s)
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


def _clause_spans(s: str) -> tuple[list[int], list[int]]:
    """Start and end offsets of clauses, after merging one-word comma tails."""
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    after_comma = False

    def add(a: int, b: int, comma: bool) -> None:
        seg = s[a:b]
        n = len(_TOKEN_RE.findall(seg))
        if n == 0:
            return
        if comma and starts:
            words = [w for w in _TOKEN_RE.findall(seg) if w not in ("and", "or", "nor")]
            # A list item ("cheats", "or steals"), a bare list of single words
            # ("cheat or steal"), a short ", or ..." tail or a trailing hedge ("except when convenient")
            # belongs to the clause before the comma.
            if (
                len(words) <= 1
                or _LIST_TAIL_RE.fullmatch(seg) is not None
                or (n <= 5 and seg.lstrip().startswith(("and ", "or ", "nor ")))
                or (len(words) <= 6 and _HEDGE_RE.search(seg) is not None)
            ):
                ends[-1] = b
                return
        starts.append(a)
        ends.append(b)

    for m in _BREAK_RE.finditer(s):
        add(pos, m.start(), after_comma)
        after_comma = m.group(0) == ","
        pos = m.end()
    add(pos, len(s), after_comma)
    return starts, ends


def _negated(s: str, hs: int, cs: int, ce: int) -> bool:
    """True when the hit starting at `hs` sits under exactly one negator or
    aversion verb and its clause carries no hedge or affirming cue."""
    window_start = max(cs, hs - _NEG_WINDOW_CHARS)
    pre = s[window_start:hs]
    tokens = list(_TOKEN_RE.finditer(pre))
    if not tokens:
        return False
    window = pre[tokens[-_NEG_WINDOW_TOKENS:][0].start() :]
    count = 0
    for m in _NEG_RE.finditer(window):
        count += 1
    if count == 0:
        near = pre[tokens[-2:][0].start() :]
        if _DETECT_RE.search(near) is not None:
            count = 1
    if count != 1:
        return False
    return _HEDGE_RE.search(s, cs, ce) is None


def _post_rejected(s: str, he: int, ce: int) -> bool:
    return _POST_REJECT_RE.match(s, he, min(ce, he + 60)) is not None or bool(
        _POST_REJECT_RE.search(s, he, min(ce, he + 60))
    )


def _word_starts(s: str) -> tuple[list[int], list[int], str]:
    """Squash view: letters only, joined, with the offsets of every word."""
    ends: list[int] = []
    starts: list[int] = []
    parts: list[str] = []
    total = 0
    offsets: list[int] = []
    for m in re.finditer(r"[a-z]+", s):
        offsets.append(m.start())
        parts.append(m.group(0))
        starts.append(total)
        total += len(m.group(0))
        ends.append(total)
    return starts, offsets, "".join(parts)


def _scan(s: str, allow_squash: bool) -> tuple[set[str], list[str], bool]:
    """Scan one normalized view. Returns (categories, forms, overflow)."""
    categories: set[str] = set()
    forms: list[str] = []
    cstarts, cends = _clause_spans(s)

    def clause_of(pos: int) -> tuple[int, int]:
        i = bisect_right(cstarts, pos) - 1
        if i < 0:
            return 0, len(s)
        return cstarts[i], cends[i]

    def note(cat: str, form: str) -> None:
        categories.add(cat)
        if len(forms) < _MAX_MATCHED and form[:_MAX_FORM_CHARS] not in forms:
            forms.append(form[:_MAX_FORM_CHARS])

    hits = 0
    for comp in _COMPILED:
        if comp.raw is not None:
            for m in comp.raw.finditer(s):
                note(comp.name, m.group(0))
                return categories, forms, False
        if comp.plain is None:
            continue
        for m in comp.plain.finditer(s):
            hits += 1
            if hits > _MAX_HITS:
                return categories | {"overflow"}, forms, True
            cs, ce = clause_of(m.start())
            if _negated(s, m.start(), cs, ce) or _post_rejected(s, m.end(), ce):
                continue
            note(comp.name, m.group(0))
            return categories, forms, False

    if allow_squash and cstarts:
        starts, offsets, squashed = _word_starts(s)
        for m in _SQUASH_RE.finditer(squashed):
            i = bisect_right(starts, m.start()) - 1
            if i < 0 or starts[i] != m.start():
                continue
            j = bisect_right(starts, m.end() - 1) - 1
            if j <= i:
                continue  # single word: the ordinary patterns already saw it
            hs = offsets[i]
            cs, ce = clause_of(hs)
            hits += 1
            if hits > _MAX_HITS:
                return categories | {"overflow"}, forms, True
            if _negated(s, hs, cs, ce):
                continue
            note("obfuscation", m.group(0))
            return categories, forms, False
    return categories, forms, False


def _views(text: str) -> tuple[str, ...]:
    """The normalized views to scan, in a fixed order."""
    base = _base_normalize(text)
    views: list[str] = [_finish_view(base, _LEET_I)]
    if len(base) > _SECONDARY_MAX_CHARS:
        return tuple(views)
    if "1" in base:
        views.append(_finish_view(base, _LEET_L))
    first = views[0]
    if _ELONG_RE.search(first):
        views.append(_ELONG_RE.sub(r"\1", first))
        views.append(_ELONG_RE.sub(r"\1\1", first))
    return tuple(views)


def _screen(text: str) -> ScreenResult:
    categories: set[str] = set()
    forms: list[str] = []
    for index, view in enumerate(_views(text)):
        cats, fs, _ = _scan(
            view, allow_squash=index == 0 and len(view) <= _SECONDARY_MAX_CHARS
        )
        if cats:
            categories |= cats
            forms.extend(f for f in fs if f not in forms)
            break
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
    """At most `limit` characters of `text`, safe to put in a log line."""
    try:
        s = text if isinstance(text, str) else repr(text)
        s = " ".join(s.split())
        return s if len(s) <= limit else s[: limit - 3] + "..."
    except Exception:
        return "<unprintable>"
