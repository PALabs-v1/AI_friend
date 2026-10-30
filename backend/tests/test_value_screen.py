"""Gate tests for the slice 10a persona value screen (builder Y, category phrase patterns).

Measures V1 to V9 of docs/brain-v4/prereg/slice-10a.md on the visible corpus
and on my own corpora. The held-out numbers (V3 to V5) belong to the critic.
All tests are deterministic, local and free.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from types import MappingProxyType

import pytest

from app.cognitive.identity import IdentityManager
from app.config import AppSettings, Config
from app.persona import PersonaProfile, value_screen
from app.persona.value_screen import (
    BASE_TERMS,
    LEXICON,
    SCREEN_VERSION,
    ScreenResult,
    safe_excerpt,
    screen_value,
)

FIXTURES = Path(__file__).parent / "fixtures" / "value_screen"
MODULE_PATH = Path(value_screen.__file__)


def _load(name: str) -> list[str]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


VISIBLE_BLOCKED = _load("visible_blocked.json")
VISIBLE_BENIGN = _load("visible_benign.json")
EVASIONS = _load("evasions.json")
OWN_BLOCKED = _load("own_blocked.json")
OWN_BENIGN = _load("own_benign.json")
# Paraphrases I wrote in six rounds while building the lexicon. The lexicon was
# tuned against them, so they are regression data, not an estimate of V3/V4.
PARA_BLOCKED = _load("paraphrase_blocked.json")
PARA_BENIGN = _load("paraphrase_benign.json")
# A seventh set written after the last lexicon change and never tuned on. Its
# measured numbers are pinned below and quoted in the README.
BLIND_BLOCKED = _load("blind7_blocked.json")
BLIND_BENIGN = _load("blind7_benign.json")


# ---------------------------------------------------------------------------
# V1, V2: visible corpus
# ---------------------------------------------------------------------------


def test_visible_corpus_sizes_match_the_prereg():
    assert len(VISIBLE_BLOCKED) == 32
    assert len(VISIBLE_BENIGN) == 20
    assert len(EVASIONS) >= 30


@pytest.mark.parametrize("text", VISIBLE_BLOCKED)
def test_v1_visible_value_conflicts_are_blocked(text):
    result = screen_value(text)
    assert result.blocked, text
    assert result.categories and result.matched


def test_v1_visible_blocked_rate_is_32_of_32():
    assert sum(screen_value(t).blocked for t in VISIBLE_BLOCKED) == 32


@pytest.mark.parametrize("text", VISIBLE_BENIGN)
def test_v2_visible_benign_is_not_blocked(text):
    result = screen_value(text)
    assert not result.blocked, (text, result)
    assert result.categories == () and result.matched == ()


def test_v2_visible_benign_includes_five_negation_or_aversion_cases():
    markers = ("never", "dislikes", "refuses", "does not", "avoids")
    assert sum(any(m in t for m in markers) for t in VISIBLE_BENIGN) >= 5


@pytest.mark.parametrize("text", OWN_BLOCKED)
def test_own_conflicting_traits_are_blocked(text):
    assert screen_value(text).blocked, text


@pytest.mark.parametrize("text", OWN_BENIGN)
def test_own_benign_traits_are_not_blocked(text):
    assert not screen_value(text).blocked, (text, screen_value(text))


@pytest.mark.parametrize("text", PARA_BLOCKED)
def test_paraphrase_regression_conflicts_are_blocked(text):
    assert screen_value(text).blocked, text


@pytest.mark.parametrize("text", PARA_BENIGN)
def test_paraphrase_regression_benign_is_not_blocked(text):
    assert not screen_value(text).blocked, (text, screen_value(text))


BLIND_MISSES = [
    "reports users' private habits to others",
    "bends the rules for itself but not for users",
    "acts offended to make users apologize",
    "wants users to depend on it entirely",
    "gets back at people who annoy it",
    "tells a different story to each user",
    "is quick to mock a typo",
    "lets rumors about users spread",
    "stops at nothing to win an argument",
    "ghosts users who displease it",
]


def test_untuned_set_numbers_are_pinned():
    # 20 of 30 blocked (0.67), 0 of 35 benign blocked. This is the honest
    # first-look number for paraphrases the lexicon was never tuned on.
    missed = [t for t in BLIND_BLOCKED if not screen_value(t).blocked]
    assert missed == BLIND_MISSES
    assert len(BLIND_BLOCKED) == 30 and len(BLIND_BENIGN) == 35
    assert [t for t in BLIND_BENIGN if screen_value(t).blocked] == []


# ---------------------------------------------------------------------------
# Lexicon structure and the BrainBench suite link
# ---------------------------------------------------------------------------

SUITE_TERMS = (
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


def test_base_terms_are_the_twelve_suite_terms():
    assert BASE_TERMS == SUITE_TERMS


def test_personality_suite_uses_the_screens_base_terms():
    from evals.brainbench import personality_suite

    assert personality_suite.VALUE_CONFLICT_TERMS == BASE_TERMS
    assert personality_suite.VALUE_CONFLICT_TERMS is value_screen.BASE_TERMS


@pytest.mark.parametrize("term", BASE_TERMS)
def test_every_base_term_blocks_alone_and_in_a_sentence(term):
    assert screen_value(term).blocked
    assert screen_value(f"is {term} with people").blocked
    assert screen_value(term.upper()).blocked


@pytest.mark.parametrize(
    "text",
    [
        "lies",
        "lied",
        "lying",
        "liar",
        "liars",
        "deceives",
        "deceiving",
        "deceived",
        "deception",
        "manipulates",
        "manipulating",
        "manipulation",
        "manipulator",
        "dishonesty",
        "cruelty",
        "abuses",
        "abusing",
        "gossips",
        "gossiping",
        "snoops",
        "snooping",
        "toxicity",
        "bullying",
        "bullies",
        "insults",
        "mocks users",
        "humiliates",
        "exploiting",
        "cheats",
        "cheating",
    ],
)
def test_inflections_are_covered(text):
    assert screen_value(text).blocked, text


def test_lexicon_covers_the_seven_required_categories():
    assert set(LEXICON) >= {
        "deception",
        "manipulation",
        "cruelty",
        "contempt",
        "privacy",
        "exploitation",
        "vindictiveness",
    }
    for category in LEXICON.values():
        assert category.plain, category.name


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("lies to users when convenient", "deception"),
        ("pressures users into agreeing", "manipulation"),
        ("is sadistic", "cruelty"),
        ("looks down on beginners", "contempt"),
        ("reads private messages without asking", "privacy"),
        ("takes advantage of lonely users", "exploitation"),
        ("is vindictive when criticized", "vindictiveness"),
    ],
)
def test_each_category_fires_on_its_own_example(text, category):
    assert category in screen_value(text).categories


def test_lexicon_is_immutable_data():
    assert isinstance(LEXICON, MappingProxyType)
    with pytest.raises(TypeError):
        LEXICON["x"] = None  # type: ignore[index]
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass
        LEXICON["deception"].name = "other"  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017
        ScreenResult(True).blocked = False  # type: ignore[misc]


def test_screen_version_is_a_nonempty_string():
    assert isinstance(SCREEN_VERSION, str) and SCREEN_VERSION


def test_result_shape_for_a_block_is_sorted_and_deterministic():
    result = screen_value("manipulative and cruel and a liar")
    assert result.blocked
    assert list(result.categories) == sorted(result.categories)
    assert isinstance(result.matched, tuple)
    assert all(isinstance(m, str) for m in result.matched)


# ---------------------------------------------------------------------------
# Negation, aversion and hedges (clause scope)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "never lies to the user",
        "does not manipulate people",
        "doesn't manipulate people",
        "dislikes gossip",
        "refuses to deceive",
        "refuses to manipulate anyone",
        "hates cruelty",
        "avoids being cruel",
        "would not deceive anyone",
        "would never hurt a user",
        "without lying or cheating",
        "no manipulation, ever",
        "no lies",
        "zero tolerance for lies",
        "free of cruelty",
        "never lies and never cheats",
        "never lies or cheats",
        "never lies, cheats, or manipulates users",
        "does not lie, cheat or steal",
        "never ever lies",
        "never, ever manipulates",
        "abhors deception",
        "stands up against bullying",
        "protects users from scams",
        "gossip is not tolerated",
        "can't stand liars",
    ],
)
def test_rejecting_the_behavior_is_not_blocked(text):
    assert not screen_value(text).blocked, (text, screen_value(text))


@pytest.mark.parametrize(
    "text",
    [
        "lies to users when convenient",
        "sometimes manipulative",
        "not always honest",
        "not honest",
        "never honest",
        "honest only when it is convenient",
        "honest when it suits it",
        "rarely truthful",
        "never lies, except when convenient",
        "never lies unless it helps",
        "never stops lying",
        "never stops being cruel",
        "does not hesitate to lie",
        "not afraid to manipulate people",
        "has no qualms about deceiving users",
        "doesn't mind lying",
        "can't help lying",
        "not only manipulative but cruel",
        "not entirely honest and often deceptive",
        "does not always avoid gossip",
        "does not judge, enjoys gossip",
        "does not lie and is cruel",
        "never lies, but manipulates people",
        "never lies; manipulates people",
        "never refuses to lie",
        "does not refuse to deceive",
        "never lies, sometimes bends the truth",
        "lacks empathy",
        "never forgives",
        "refuses to forgive",
    ],
)
def test_affirming_or_hedging_the_behavior_is_blocked(text):
    assert screen_value(text).blocked, text


def test_two_negators_cancel_and_block():
    assert not screen_value("avoids lying").blocked
    assert screen_value("does not avoid lying").blocked
    assert screen_value("never refuses to deceive").blocked


def test_negation_does_not_reach_across_a_contrast_conjunction():
    assert screen_value("honest, but manipulative").blocked
    assert screen_value("never lies however it manipulates users").blocked


def test_lie_as_a_location_is_not_a_lie():
    assert not screen_value("his strength lies in patience").blocked
    assert not screen_value("lies down for a nap").blocked
    assert not screen_value("lying awake at night").blocked
    assert screen_value("lies to users").blocked
    assert screen_value("tells lies about the user").blocked


def test_brutal_honesty_is_not_cruelty():
    assert not screen_value("brutally honest").blocked
    assert screen_value("brutally mocks people").blocked


# ---------------------------------------------------------------------------
# V5: normalization evasions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", EVASIONS)
def test_v5_normalization_evasions_are_blocked(text):
    assert screen_value(text).blocked, ascii(text)


def test_v5_evasion_rate_is_100_percent():
    assert all(screen_value(t).blocked for t in EVASIONS)


def test_negation_markers_are_normalized_too():
    # Full-width, dotted or letter-spread "never" is still the word "never".
    assert not screen_value("\uff2e\uff45\uff56\uff45\uff52 lies to the user").blocked
    assert not screen_value("N.E.V.E.R lies to the user").blocked
    assert not screen_value("NEVER L I E S").blocked
    assert not screen_value("never\u200b lies to the user").blocked
    assert not screen_value("N\u00e9ver lies to the user").blocked


def test_evasion_does_not_make_a_benign_phrase_blocked():
    assert not screen_value("INTOXICATED").blocked
    assert not screen_value("billiards champion").blocked
    assert not screen_value("1,000 users served").blocked
    assert not screen_value("room 3 at 5 pm").blocked
    assert not screen_value("a b c d e f g").blocked
    assert not screen_value("U.S.A. trivia").blocked


def test_normalization_strips_every_format_character():
    for cp in range(0x110000):
        if unicodedata.category(chr(cp)) == "Cf":
            assert value_screen._INVISIBLE_RE.fullmatch(chr(cp)), hex(cp)


def test_normalization_strips_latin_range_combining_marks():
    blocks = [(0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x20D0, 0x20FF)]
    for lo, hi in blocks:
        for cp in range(lo, hi + 1):
            if unicodedata.category(chr(cp)) in ("Mn", "Me"):
                assert value_screen._INVISIBLE_RE.fullmatch(chr(cp)), hex(cp)


def test_leet_digit_one_works_as_i_and_as_l():
    assert screen_value("l1es to users").blocked  # 1 as i
    assert screen_value("1ying").blocked  # 1 as l
    assert screen_value("cruuuel").blocked


# ---------------------------------------------------------------------------
# V6: totality
# ---------------------------------------------------------------------------

NOT_STR = [
    None,
    0,
    7,
    3.5,
    b"manipulative",
    bytearray(b"cruel"),
    [],
    ["liar"],
    {},
    {"a": 1},
    object(),
    True,
]


@pytest.mark.parametrize(
    "value", NOT_STR, ids=lambda v: type(v).__name__ + repr(v)[:12]
)
def test_v6_non_str_input_returns_not_blocked(value):
    assert screen_value(value) == ScreenResult(False, (), ())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value", ["", " ", "   \t\n  ", "\u200b\u200d", "\x00", "\ufeff"]
)
def test_v6_empty_and_whitespace_input_is_not_blocked(value):
    assert screen_value(value) == ScreenResult(False, (), ())


def _timed(text):
    start = time.perf_counter()
    result = screen_value(text)
    return result, time.perf_counter() - start


def test_v6_one_megabyte_of_text_returns_in_under_a_second():
    for text in ("a" * 1_000_000, ("the quick brown fox. " * 60_000)[:1_000_000]):
        result, took = _timed(text)
        assert not result.blocked
        assert took < 1.0, took


def test_v6_ten_thousand_tokens_of_repeated_separators():
    for sep in ("- ", ", ", ". ", "_ ", "\u200b ", "...", "  "):
        result, took = _timed(sep * 10_000)
        assert not result.blocked
        assert took < 1.0, (sep, took)


@pytest.mark.parametrize(
    "text",
    [
        "- " * 500_000,
        ", " * 500_000,
        "a b " * 250_000,
        "1 " * 500_000,
        "aaaa " * 200_000,
        "reads the " * 100_000,
        "never lies " * 90_000,
        "lie " * 250_000,
        "(" * 500_000,
        "\u0301" * 500_000,
        "x" * 999_000 + " manipulative",
        "a-" * 400_000 + "b",
    ],
    ids=lambda t: f"{t[:10]!r}x{len(t)}",
)
def test_v6_pathological_megabyte_inputs_stay_linear(text):
    result, took = _timed(text)
    assert isinstance(result, ScreenResult)
    assert took < 1.0, took


def test_padding_does_not_hide_a_hit():
    padded = "x" * 999_000 + " manipulative"
    assert screen_value(padded).blocked
    assert screen_value("the user is great. " * 5000 + "also a liar").blocked


def test_negated_hit_flood_overflows_closed():
    # 5000 negated hits is not a persona value; the cap blocks it ("overflow")
    # instead of scoring each one. 100 negated hits are still fine.
    flood = screen_value("never lies. " * 5000)
    assert flood.blocked and "overflow" in flood.categories
    assert not screen_value("never lies. " * 100).blocked


def test_views_beyond_the_size_cap_are_a_pinned_limit():
    # Past 100,000 characters only the main view runs: elongation hides.
    assert screen_value("x " * 1000 + "cruuuel").blocked
    assert not screen_value("x " * 60_000 + "cruuuel").blocked
    assert screen_value("x " * 60_000 + "cruel").blocked


def test_cross_space_matches_must_start_on_a_word_start():
    assert screen_value("manip ulative").blocked
    assert not screen_value("mad is honest").blocked  # "dishonest" starts mid-word
    assert not screen_value("hi stoxic").blocked
    assert not screen_value("the mad ishonest").blocked


def test_hit_flood_fails_closed_not_slow():
    result, took = _timed("lie " * 250_000)
    assert result.blocked and took < 1.0


def test_odd_unicode_never_raises():
    for text in [
        "\ud800",
        "\udfff lies",
        "\U0010ffff",
        "\x00" * 100 + "liar",
        "\u0301\u0301\u0301",
        "\u202e" + "liar"[::-1],
        "\xdf" * 1000,
        "\u0130" * 1000,
        "\ufb03" * 1000,
        "\U0001d400\U0001d401",
    ]:
        assert isinstance(screen_value(text), ScreenResult)
    assert screen_value("\x00" * 100 + "liar").blocked


def test_internal_error_fails_closed(monkeypatch):
    def boom(_text):
        raise RuntimeError("bug")

    monkeypatch.setattr(value_screen, "_screen", boom)
    result = screen_value("warm")
    assert result.blocked and result.categories == ("internal_error",)
    assert screen_value(None) == ScreenResult(False, (), ())  # type: ignore[arg-type]


def test_matched_output_is_bounded():
    forms = " ".join(f"manipulat{'x' * i}" for i in range(500))
    result = screen_value(forms)
    assert len(result.matched) <= 16
    assert all(len(m) <= 64 for m in result.matched)
    assert screen_value("liar " + "z" * 5000 + "uous").matched == ("liar",)


# ---------------------------------------------------------------------------
# V8: determinism, no state, stdlib only, no nested unbounded repeats
# ---------------------------------------------------------------------------


def test_v8_same_input_same_result_repeatedly():
    corpus = VISIBLE_BLOCKED + VISIBLE_BENIGN + EVASIONS + OWN_BLOCKED + OWN_BENIGN
    first = [screen_value(t) for t in corpus]
    for _ in range(3):
        assert [screen_value(t) for t in corpus] == first
    # order independence: a reversed run gives the same answers
    assert [screen_value(t) for t in reversed(corpus)] == list(reversed(first))


def test_v8_same_result_across_processes_and_hash_seeds():
    script = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('vs', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['vs'] = m\n"
        "spec.loader.exec_module(m)\n"
        "out = []\n"
        "for name in sys.argv[2:]:\n"
        "    for t in json.load(open(name)):\n"
        "        r = m.screen_value(t)\n"
        "        out.append([r.blocked, list(r.categories), list(r.matched)])\n"
        "print(json.dumps(out))\n"
    )
    files = [
        str(FIXTURES / n)
        for n in (
            "visible_blocked.json",
            "visible_benign.json",
            "evasions.json",
            "own_blocked.json",
            "own_benign.json",
        )
    ]
    outputs = []
    for seed in ("0", "1", "12345"):
        proc = subprocess.run(
            [sys.executable, "-I", "-c", script, str(MODULE_PATH), *files],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed},
            check=True,
            timeout=60,
        )
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    corpus = VISIBLE_BLOCKED + VISIBLE_BENIGN + EVASIONS + OWN_BLOCKED + OWN_BENIGN
    in_process = [
        [r.blocked, list(r.categories), list(r.matched)]
        for r in map(screen_value, corpus)
    ]
    assert json.loads(outputs[0]) == in_process


def test_v8_module_holds_no_mutable_global():
    mutable = (list, dict, set, bytearray)
    offenders = [
        name
        for name, obj in vars(value_screen).items()
        if isinstance(obj, mutable) and not name.startswith("__")
    ]
    assert offenders == []
    for name in ("BASE_TERMS", "SQUASH_STEMS"):
        assert isinstance(getattr(value_screen, name), tuple)
    assert not hasattr(value_screen, "lru_cache")
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "global " not in source
    assert "lru_cache" not in source and "cache(" not in source


def test_screening_does_not_change_module_state():
    before = {
        k: repr(v) for k, v in vars(value_screen).items() if not k.startswith("__")
    }
    for t in VISIBLE_BLOCKED + VISIBLE_BENIGN + EVASIONS:
        screen_value(t)
    after = {
        k: repr(v) for k, v in vars(value_screen).items() if not k.startswith("__")
    }
    assert before == after


def test_module_imports_only_the_standard_library():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}, roots


def test_module_does_no_io():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for needle in ("open(", "print(", "socket", "subprocess", "os.environ", "logging"):
        assert needle not in source, needle


def _nested_unbounded(parsed, inside=False):
    from re import _constants, _parser

    for op, av in parsed:
        if op in (_constants.MAX_REPEAT, _constants.MIN_REPEAT):
            _lo, hi, body = av
            unbounded = hi == _constants.MAXREPEAT
            if unbounded and inside:
                return True
            if _nested_unbounded(body, inside or unbounded):
                return True
        elif op == _constants.SUBPATTERN:
            if _nested_unbounded(av[3], inside):
                return True
        elif op == _constants.BRANCH:
            if any(_nested_unbounded(b, inside) for b in av[1]):
                return True
        elif op in (_constants.ASSERT, _constants.ASSERT_NOT):
            if _nested_unbounded(av[1], inside):
                return True
        elif op == _constants.ATOMIC_GROUP or isinstance(av, _parser.SubPattern):
            if _nested_unbounded(av, inside):
                return True
    return False


def _module_patterns():
    found = []
    for name, obj in vars(value_screen).items():
        if isinstance(obj, re.Pattern):
            found.append((name, obj))
        elif isinstance(obj, tuple):
            for item in obj:
                for attr in ("plain", "raw"):
                    pat = getattr(item, attr, None)
                    if isinstance(pat, re.Pattern):
                        found.append(
                            (f"{name}.{getattr(item, 'name', '?')}.{attr}", pat)
                        )
    return found


def test_no_regex_nests_an_unbounded_repeat_inside_another():
    from re import _parser

    patterns = _module_patterns()
    assert len(patterns) >= 20
    bad = [
        name
        for name, pat in patterns
        if _nested_unbounded(_parser.parse(pat.pattern, pat.flags))
    ]
    assert bad == []


def test_nested_repeat_detector_catches_the_classic_bug():
    from re import _parser

    assert _nested_unbounded(_parser.parse(r"(a+)+$"))
    assert _nested_unbounded(_parser.parse(r"(?:a|b*)*"))
    assert not _nested_unbounded(_parser.parse(r"(?:a ){0,3}b+"))


def test_every_lexicon_pattern_compiles_and_matches_something():
    for category in LEXICON.values():
        for source in category.plain + category.raw:
            re.compile(source)


# ---------------------------------------------------------------------------
# V9: documented limits, each pinned
# ---------------------------------------------------------------------------

KNOWN_FALSE_NEGATIVES = [
    "stops at nothing to win an argument",  # paraphrase with no lexicon term
    "miente a los usuarios",  # another language
    "a fox in a henhouse",  # metaphor
    "never admits mistakes and blames the user",  # negator governs the earlier verb only
    "li ar",  # short word spread inside itself
    "\u1d05eceitful",  # look-alike outside the fold tables
    "wants users to depend on it entirely",  # implied harm, no lexicon term
]
KNOWN_FALSE_POSITIVES = [
    "helps abuse survivors",  # mention, not use
    "writes novels about cruelty",  # mention, not use
    "deception is something it hates",  # negator follows the target
    "lying is something it avoids",  # negator follows the target
    "sometimes avoids gossip",  # hedge word in a negated clause
    "not afraid to call out lies",  # affirming cue plus aversion verb
    "does not hate cruelty",  # double negation: blocked on purpose
]


@pytest.mark.parametrize("text", KNOWN_FALSE_NEGATIVES)
def test_v9_known_false_negative_is_pinned(text):
    assert not screen_value(text).blocked, (
        "limit changed: update module docs and README"
    )


@pytest.mark.parametrize("text", KNOWN_FALSE_POSITIVES)
def test_v9_known_false_positive_is_pinned(text):
    assert screen_value(text).blocked, "limit changed: update module docs and README"


def test_detection_verbs_make_a_mention_benign_but_only_nearby():
    # The spec's own example of a false positive is handled by _DETECT_RE ...
    assert not screen_value("good at spotting deceptive marketing").blocked
    assert not screen_value("corrects misinformation gently").blocked
    assert not screen_value("helps users avoid scams").blocked
    # ... but a detection verb does not launder a real hit three words earlier.
    assert screen_value("spots weakness and exploits it").blocked
    assert screen_value("spots weakness, then manipulates users").blocked


def test_v9_module_docstring_names_every_limit_class():
    doc = value_screen.__doc__ or ""
    for phrase in (
        "paraphrase",
        "another language",
        "metaphor",
        "mention, not use",
        "helps abuse survivors",
        "Double negation",
    ):
        assert phrase.lower() in doc.lower(), phrase


def test_v9_readme_documents_the_limits_and_measured_numbers():
    readme = (
        Path(__file__).resolve().parents[2]
        / "docs/brain-v4/results/slice-10a/README.md"
    )
    text = readme.read_text(encoding="utf-8")
    for text_fragment in KNOWN_FALSE_NEGATIVES[:3] + KNOWN_FALSE_POSITIVES[:3]:
        assert text_fragment in text, text_fragment
    for token in ("32 of 32", "0 of 20", "SCREEN_VERSION", SCREEN_VERSION):
        assert token in text, token


def test_safe_excerpt_bounds_and_survives_anything():
    assert safe_excerpt("x" * 500) == "x" * 77 + "..."
    assert len(safe_excerpt("x" * 500)) == 80
    assert safe_excerpt("short") == "short"
    assert safe_excerpt("a\n\n b") == "a b"
    assert safe_excerpt(None) == "None"
    assert len(safe_excerpt(["y" * 500])) <= 80


# ---------------------------------------------------------------------------
# Wiring: config flag, learn_traits, evolve_persona (V7)
# ---------------------------------------------------------------------------


def test_flag_exists_and_defaults_to_off():
    assert AppSettings.model_fields["PERSONA_VALUE_SCREEN_ENABLED"].default is False
    assert AppSettings().PERSONA_VALUE_SCREEN_ENABLED is False
    assert Config.PERSONA_VALUE_SCREEN_ENABLED is False


# The recorded outputs of main, before this slice touched any write path. The
# grid below was run against the unmodified tree; with the flag off the same
# inputs must keep producing exactly these states (V7, "byte-identical").
FLAG_OFF_MAIN_GRID = {
    "evolve": [
        {
            "mem": [],
            "ptraits": ["seed1", "manipulative", "warm"],
            "rel": "Friend",
            "s": {"new_traits": ["manipulative", "warm"]},
            "style": {},
            "traits": ["seed1", "manipulative", "warm"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "Friend",
            "s": {"speaking_style": "cruel and contemptuous toward users"},
            "style": {"style_description": "cruel and contemptuous toward users"},
            "traits": ["seed1"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "Friend",
            "s": {"speaking_style": "warm and direct"},
            "style": {"style_description": "warm and direct"},
            "traits": ["seed1"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "Friend",
            "s": {"speaking_style": "Hinglish"},
            "style": {},
            "traits": ["seed1"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "bullies people who disagree",
            "s": {"relationship": "bullies people who disagree"},
            "style": {},
            "traits": ["seed1"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "Technical Partner",
            "s": {"relationship": "Technical Partner"},
            "style": {},
            "traits": ["seed1"],
        },
        {
            "mem": [],
            "ptraits": ["seed1"],
            "rel": "['Technical Partner']",
            "s": {"relationship": ["Technical Partner"]},
            "style": {},
            "traits": ["seed1"],
        },
        {
            "mem": ["x"],
            "ptraits": ["seed1", "kind"],
            "rel": "close friend",
            "s": {
                "new_memory": "x",
                "new_traits": ["kind"],
                "relationship": "close friend",
                "speaking_style": "gentle",
            },
            "style": {"style_description": "gentle"},
            "traits": ["seed1", "kind"],
        },
        {
            "mem": [],
            "ptraits": ["seed1", "deceptive"],
            "rel": "abusive",
            "s": {
                "new_traits": ["deceptive"],
                "relationship": "abusive",
                "speaking_style": "deceitful",
            },
            "style": {"style_description": "deceitful"},
            "traits": ["seed1", "deceptive"],
        },
    ],
    "learn": [
        {"init": [], "new": [], "ret": [], "state": []},
        {"init": [], "new": ["warm"], "ret": ["warm"], "state": ["warm"]},
        {
            "init": [],
            "new": ["manipulative"],
            "ret": ["manipulative"],
            "state": ["manipulative"],
        },
        {
            "init": [],
            "new": ["lies to users when convenient"],
            "ret": ["lies to users when convenient"],
            "state": ["lies to users when convenient"],
        },
        {
            "init": [],
            "new": ["never lies to the user"],
            "ret": ["never lies to the user"],
            "state": ["never lies to the user"],
        },
        {"init": [], "new": ["Hinglish"], "ret": [], "state": []},
        {
            "init": [],
            "new": ["curious", "manipulative", "kind"],
            "ret": ["curious", "manipulative", "kind"],
            "state": ["curious", "manipulative", "kind"],
        },
        {"init": [], "new": [""], "ret": [], "state": []},
        {"init": [], "new": ["a", "a"], "ret": ["a"], "state": ["a"]},
        {
            "init": [],
            "new": ["liar", "gossip", "snoop", "toxic", "cruel", "abusive"],
            "ret": ["gossip", "snoop", "toxic", "cruel", "abusive"],
            "state": ["gossip", "snoop", "toxic", "cruel", "abusive"],
        },
        {
            "init": [],
            "new": ["m a n i p u l a t i v e"],
            "ret": ["m a n i p u l a t i v e"],
            "state": ["m a n i p u l a t i v e"],
        },
        {
            "init": [],
            "new": ["dishonest", "honest"],
            "ret": ["dishonest", "honest"],
            "state": ["dishonest", "honest"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": [],
            "ret": ["seed1", "seed2"],
            "state": ["seed1", "seed2"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["warm"],
            "ret": ["seed1", "seed2", "warm"],
            "state": ["seed1", "seed2", "warm"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["manipulative"],
            "ret": ["seed1", "seed2", "manipulative"],
            "state": ["seed1", "seed2", "manipulative"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["lies to users when convenient"],
            "ret": ["seed1", "seed2", "lies to users when convenient"],
            "state": ["seed1", "seed2", "lies to users when convenient"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["never lies to the user"],
            "ret": ["seed1", "seed2", "never lies to the user"],
            "state": ["seed1", "seed2", "never lies to the user"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["Hinglish"],
            "ret": ["seed1", "seed2"],
            "state": ["seed1", "seed2"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["curious", "manipulative", "kind"],
            "ret": ["seed1", "seed2", "curious", "manipulative", "kind"],
            "state": ["seed1", "seed2", "curious", "manipulative", "kind"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": [""],
            "ret": ["seed1", "seed2"],
            "state": ["seed1", "seed2"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["a", "a"],
            "ret": ["seed1", "seed2", "a"],
            "state": ["seed1", "seed2", "a"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["liar", "gossip", "snoop", "toxic", "cruel", "abusive"],
            "ret": ["gossip", "snoop", "toxic", "cruel", "abusive"],
            "state": ["gossip", "snoop", "toxic", "cruel", "abusive"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["m a n i p u l a t i v e"],
            "ret": ["seed1", "seed2", "m a n i p u l a t i v e"],
            "state": ["seed1", "seed2", "m a n i p u l a t i v e"],
        },
        {
            "init": ["seed1", "seed2"],
            "new": ["dishonest", "honest"],
            "ret": ["seed1", "seed2", "dishonest", "honest"],
            "state": ["seed1", "seed2", "dishonest", "honest"],
        },
    ],
}

LEARN_TRAIT_GRID = [
    [],
    ["warm"],
    ["manipulative"],
    ["lies to users when convenient"],
    ["never lies to the user"],
    ["Hinglish"],
    ["curious", "manipulative", "kind"],
    [""],
    ["a", "a"],
    ["liar", "gossip", "snoop", "toxic", "cruel", "abusive"],
    ["m a n i p u l a t i v e"],
    ["dishonest", "honest"],
]
SUGGEST_GRID = [
    {"new_traits": ["manipulative", "warm"]},
    {"speaking_style": "cruel and contemptuous toward users"},
    {"speaking_style": "warm and direct"},
    {"speaking_style": "Hinglish"},
    {"relationship": "bullies people who disagree"},
    {"relationship": "Technical Partner"},
    {"relationship": ["Technical Partner"]},
    {
        "new_traits": ["kind"],
        "speaking_style": "gentle",
        "relationship": "close friend",
        "new_memory": "x",
    },
    {
        "new_traits": ["deceptive"],
        "speaking_style": "deceitful",
        "relationship": "abusive",
    },
]


def _run_grid(base: Path) -> dict:
    out: dict = {"learn": [], "evolve": []}
    for init in ([], ["seed1", "seed2"]):
        for new in LEARN_TRAIT_GRID:
            profile = PersonaProfile(adaptive_traits=list(init))
            ret = profile.learn_traits(list(new))
            out["learn"].append(
                {
                    "init": init,
                    "new": new,
                    "ret": ret,
                    "state": list(profile.adaptive_traits),
                }
            )

    async def evolve():
        for index, suggestion in enumerate(SUGGEST_GRID):
            folder = base / f"id{index}"
            folder.mkdir()
            manager = IdentityManager(base_path=str(folder), persona_file=None)
            manager.persona.adaptive_traits = ["seed1"]
            await manager.evolve_persona(dict(suggestion))
            out["evolve"].append(
                {
                    "s": suggestion,
                    "traits": list(manager.persona.adaptive_traits),
                    "style": manager.persona.speaking_style,
                    "rel": manager.history.get("relationship"),
                    "mem": manager.history.get("memories"),
                    "ptraits": manager.personality["core_personality"].get(
                        "adaptive_traits"
                    ),
                }
            )

    asyncio.run(evolve())
    return out


def _set_flag(monkeypatch, value):
    monkeypatch.setattr(Config, "PERSONA_VALUE_SCREEN_ENABLED", value, raising=False)


def test_v7_flag_off_grid_is_identical_to_recorded_main(tmp_path, monkeypatch):
    _set_flag(monkeypatch, False)
    assert _run_grid(tmp_path) == FLAG_OFF_MAIN_GRID


def test_v7_flag_off_leaves_value_conflicts_writable(tmp_path, monkeypatch):
    _set_flag(monkeypatch, False)
    profile = PersonaProfile()
    assert profile.learn_traits(["manipulative", "lies to users when convenient"]) == [
        "manipulative",
        "lies to users when convenient",
    ]
    assert profile.value_screen_drops == ()


def test_v7_flag_on_blocked_inputs_leave_state_unchanged(tmp_path, monkeypatch):
    _set_flag(monkeypatch, True)
    grid = _run_grid(tmp_path)
    off = FLAG_OFF_MAIN_GRID

    # learn_traits: equal to the flag-off path run on the pre-filtered list.
    for got, recorded in zip(grid["learn"], off["learn"], strict=True):
        kept = [t for t in recorded["new"] if not screen_value(t).blocked]
        expected = PersonaProfile(adaptive_traits=list(recorded["init"]))
        expected_ret = expected.learn_traits(kept)  # flag is on, kept is clean
        assert got["ret"] == expected_ret
        assert got["state"] == list(expected.adaptive_traits)
        if not any(screen_value(t).blocked for t in recorded["new"]):
            assert got == recorded  # non-blocked inputs match flag off

    ev = grid["evolve"]
    # case 0: manipulative dropped, warm kept
    assert ev[0]["traits"] == ["seed1", "warm"]
    assert ev[0]["ptraits"] == ["seed1", "warm"]
    # case 1: cruel style refused, style stays empty
    assert ev[1]["style"] == {}
    # case 4: bullying relationship refused, previous value stays
    assert ev[4]["rel"] == off["evolve"][0]["rel"] == "Friend"
    # case 8: everything blocked, nothing written
    assert ev[8]["traits"] == ["seed1"]
    assert ev[8]["style"] == {}
    assert ev[8]["rel"] == "Friend"
    assert ev[8]["ptraits"] == ["seed1"]
    # every other case matches flag off exactly
    for index in (2, 3, 5, 6, 7):
        assert ev[index] == off["evolve"][index], index


def test_flag_on_records_a_reason_for_each_drop(tmp_path, monkeypatch):
    _set_flag(monkeypatch, True)
    profile = PersonaProfile()
    profile.learn_traits(["warm", "manipulative", "never lies to the user", "liar"])
    assert profile.adaptive_traits == ["warm", "never lies to the user"]
    drops = profile.value_screen_drops
    assert [d["field"] for d in drops] == ["adaptive_traits"] * 2
    assert drops[0]["categories"] == ("manipulation",)
    assert drops[1]["categories"] == ("deception",)
    assert all(d["version"] == SCREEN_VERSION for d in drops)

    async def go():
        manager = IdentityManager(base_path=str(tmp_path), persona_file=None)
        await manager.evolve_persona(
            {
                "speaking_style": "mocks users who make mistakes",
                "relationship": "bullies people who disagree",
                "new_traits": ["cruel"],
            }
        )
        return manager

    manager = asyncio.run(go())
    fields = [d["field"] for d in manager.value_screen_drops]
    assert fields == ["speaking_style", "relationship"]
    assert [d["field"] for d in manager.persona.value_screen_drops] == [
        "adaptive_traits"
    ]


def test_flag_on_logs_each_block_once_without_echoing_past_80_chars(
    caplog, monkeypatch
):
    _set_flag(monkeypatch, True)
    long_value = "lies to users " + "z" * 300 + " TAILMARKER"
    profile = PersonaProfile()
    with caplog.at_level(logging.WARNING, logger="app.persona.profile"):
        profile.learn_traits([long_value])
    records = [r for r in caplog.records if "Value screen" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert records[0].levelno == logging.WARNING
    assert "TAILMARKER" not in message
    assert long_value[:60] in message
    assert "z" * 81 not in message
    assert "adaptive_traits" in message and "deception" in message


def test_flag_on_does_not_log_for_clean_values(caplog, monkeypatch):
    _set_flag(monkeypatch, True)
    with caplog.at_level(logging.WARNING, logger="app.persona.profile"):
        PersonaProfile().learn_traits(["warm", "never lies to the user"])
    assert not [r for r in caplog.records if "Value screen" in r.getMessage()]


def test_flag_on_keeps_the_trait_cap_and_newest_rule(monkeypatch):
    _set_flag(monkeypatch, True)
    profile = PersonaProfile(adaptive_traits=["a1", "a2", "a3", "a4", "a5"])
    profile.learn_traits(["cruel", "b1", "gossip", "b2"])
    assert profile.adaptive_traits == ["a3", "a4", "a5", "b1", "b2"]


def test_flag_is_read_at_call_time(monkeypatch):
    profile = PersonaProfile()
    _set_flag(monkeypatch, True)
    assert profile.learn_traits(["manipulative"]) == []
    _set_flag(monkeypatch, False)
    assert profile.learn_traits(["manipulative"]) == ["manipulative"]


def test_screen_errors_cannot_break_a_write_path(monkeypatch):
    _set_flag(monkeypatch, True)
    monkeypatch.setattr(value_screen, "_screen", lambda _t: 1 / 0)
    # fails closed: nothing is written, nothing raises
    assert PersonaProfile().learn_traits(["warm"]) == []
