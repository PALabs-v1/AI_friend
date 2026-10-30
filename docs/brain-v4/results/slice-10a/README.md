# Slice 10a: deterministic persona value screen

Written for Aniket. Flag `PERSONA_VALUE_SCREEN_ENABLED`, default false. Nothing changes in any persona write path until it is turned on. Prereg: `docs/brain-v4/prereg/slice-10a.md` (numeric bars PROPOSED, D-062). `SCREEN_VERSION` is `10a-1`. Approach: category phrase patterns with clause-scoped negation.

## Outcome moved

Before: `PersonaProfile.learn_traits` and `IdentityManager.evolve_persona` wrote any string that passed the script check, so "manipulative" and "lies to users when convenient" became adaptive traits (`prereg/slice-10.md`, evidence row 16). After, flag on: those values are dropped before the write, logged once at WARNING (at most 80 characters of the value, control characters shown as `?`, plus the matched forms), and recorded in `PersonaProfile.value_screen_drops` and `IdentityManager.value_screen_drops` (50 entries each, oldest dropped) with the field, the lexicon categories and `SCREEN_VERSION`.

## What was built

- `backend/app/persona/value_screen.py`: pure stdlib, no I/O, no mutable module state. `screen_value(text) -> ScreenResult(blocked, categories, matched)`, `SCREEN_VERSION`, `BASE_TERMS` (the 12 suite terms), `LEXICON` (one read-only structure), `safe_excerpt`.
- `backend/app/config.py`: `PERSONA_VALUE_SCREEN_ENABLED: bool = False`. Not added to `.env.example`: the other V4 slice flags (`TRUST_EVIDENCE_MODEL_ENABLED`, `LEARNING_REVIEW_REQUIRED`) are not documented there either.
- `backend/app/persona/profile.py`: `learn_traits` screens each new trait (and a bare-string argument as a whole, see below); `value_screen_enabled()` and `reject_by_value_screen()` are the shared helpers. Two `# fmt: skip` markers keep two pre-existing spots exactly as they were on main; the repo's ruff-format hook would otherwise reflow them in this commit.
- `backend/app/cognitive/identity.py`: `evolve_persona` screens the `speaking_style` description (after truncation, so what is checked is what would be stored) and the `relationship` label. One `# codespell:ignore te` on an existing comment line, because the codespell hook checks whole files and that comment ("ha<pause>te") trips it.
- `backend/evals/brainbench/personality_suite.py`: `VALUE_CONFLICT_TERMS` is now `BASE_TERMS` from the screen, so the eval and the gate cannot drift. A test pins equality with the 12 literal terms.

### How a value is decided

1. Normalize: NFKC, casefold, strip format characters (all of Cf, checked by a sweep of Unicode in the tests) and combining marks, fold Cyrillic and Greek look-alikes, map leet (`0 o`, `1 i` and a second view with `1 l`, `3 e`, `4 a`, `5 s`, `7 t`, `@ a`, `$ s`, `!` inside a word), collapse letters spread by any run of one to four non-alphanumeric characters (spaces, dots, hyphens, low lines, newlines, emoji). A spread-out run is offered joined, minus one to three leading letters (a stray letter before the word), with a leading negator split off, and with every short key word found inside it ("l i e s t o u s e r s"). Elongated letters are squeezed in two extra views.
2. Match seven categories (deception, manipulation, cruelty, contempt, privacy, exploitation, vindictiveness). Each has `plain` patterns (a behaviour a persona could reject) and `raw` patterns (a negated or hedged virtue such as "not always honest", a lack such as "lacks empathy"). Stems are also matched across spaces inside a word ("manip ulative", "li ar"): from a word start for long stems, on whole words at both ends for the short words.
3. Scope. The text is split into clauses at punctuation, "but", "yet", "so it", "then" and at every "and", "or", "nor" and comma. A negator covers only its own clause. A split-off piece rejoins the clause before it only when it continues a list of harm items under the same negator: the earlier piece ends in a lexicon hit and the new piece is one to three words that start with a hit that is not a third-person verb (or is a one-word hit closing the piece after a non-linking word, "tells lies"), or it is an emphasis ("even small lies"), or a hedge tail ("except when convenient"). Anything else starts a new clause with no negator. A plain hit is benign when exactly one negator or aversion verb ("never", "not", "no", "non", "zero", "without", "refuses to", "avoids", "dislikes", "hates", "would not", "warns ... about", ...) sits within six words before it in its clause and the clause has no hedge or affirming cue ("not always", "never stops", "sometimes", "when convenient", "except", "no qualms", "not afraid to"). Two negators block. A detection verb earlier in the clause ("spotting", "corrects", "knows when") reads as mention unless a "to" follows it. A rejection after the hit ("gossip-free", "gossip is not tolerated") also counts. Raw hits always block. When in doubt the screen blocks.
4. Bounds, all linear. The second views (1 read as l, elongation) are built from at most 1500 distinct snippets of about 120 characters around the tokens that need them, so there is no size cap and padding cannot hide them. More than 1500 distinct snippets, more than 2000 hits in a view or more than 200 cross-space hits is blocked as `overflow`: it is not a persona value. An internal error returns `blocked=True`, category `internal_error`. Non-str and empty input is not blocked (spec).

## Round 2: critic findings

| Finding | Fix | Pinned by |
|---|---|---|
| A negator swallowed an affirming harm clause after "and", "so it", "then" or a comma: `hates mornings and lies to users`, `never judges, manipulates`, `never lies and blames the user` (11 repros) | Clause-scoped negation as in step 3. All 11 repros block; the four must-stay-benign lists (`hates arrogance and cruelty`, `never lies, cheats, or steals`, `refuses to deceive or manipulate anyone`, `does not lie and does not manipulate`) stay benign | `round2_negation_leak_blocked.json`, `round2_negation_scope_benign.json`, `test_round2_critic_repros_verbatim` |
| Generated grid | 16 negated prefixes x 9 harm predicates x 4 connectors (576 affirming): 576 of 576 blocked. 576 all-negated: 0 blocked. 432 one-negator lists of harm nouns: 0 blocked | `test_grid_*` |
| Stray letter before a spaced word, low lines, newlines, emoji, split short words | Run variants, separator class, whole-word squash for short words (steps 1 and 2); repros `x d e c e i v e s users`, `i a m a l i a r`, `I a m c r u e l`, `l i e s t o u s e r s`, `l_i_a_r`, a split `lying`, newline and emoji separated letters | `round2_normalization_blocked.json`, `test_stray_letter_*`, `test_separator_kinds_*` |
| 100,000 character secondary-view cap let `liiiar` and `cruuuel` hide behind padding | Cap removed. Snippet views, a 0.6 to 1.2 MB padding test around each payload and the overflow test | `test_second_views_survive_huge_padding`, `test_many_distinct_elongated_tokens_overflow_closed` |
| Honest traits blocked: `never lies, even small lies`, `neither lies nor deceives`, `zero lying`, `non-toxic`, `gossip-free`, `warns users about phishing`, `knows when someone is bluffing` | Emphasis continuation, "nor" no longer a second negator, "zero" and "non" as negators, post-hit adjectives, "warns ... about" as aversion, "knows when" as detection | `round2_honest_traits_benign.json` |
| Out-of-spec edits | `.pre-commit-config.yaml` and `.env.example` restored to main. The reflow of `cortisol_halflife_s` and of the `learn_traits` condition is gone (two `# fmt: skip` markers instead). Contractions in `value_screen.py` carry inline `# codespell:ignore` comments (checked with the installed codespell) | `git diff ec79dfe8 --stat` |
| Drop lists unbounded; log line unsanitized | `deque(maxlen=50)` in both places; the WARNING uses `safe_excerpt` for the value and the matched forms and shows non-printable characters as `?` | `test_drop_list_is_bounded_*`, `test_warning_log_has_no_control_characters_*` |
| Hygiene | README titles and `SCREEN_VERSION` carry no variant tag; no em dashes | `test_screen_version_is_neutral` |
| `new_traits` passed as a bare string | Flag off: unchanged, the string is walked one character at a time, exactly as on main (`learn_traits("warm")` gives `["w", "a", "r", "m"]`). Flag on: the whole string is screened first and dropped if blocked; a clean string still goes in character by character as on main | `test_bare_string_new_traits_*` |

## Measures (V1 to V9), dev corpus and my own corpora

The dev corpus is `/tmp/slice10a/dev_corpus.json` (32 blocked, 20 benign), copied to `backend/tests/fixtures/value_screen/visible_*.json`. The held-out set was never read.

| Measure | Result |
|---|---|
| V1 visible blocked | 32 of 32 |
| V2 visible benign | 0 of 20 blocked (5 negation or aversion cases included) |
| V3 held-out recall | not measurable by me; see the untuned estimate below |
| V4 held-out false positives | not measurable by me; my own first-look benign sets blocked 1 of 228 (see below) |
| V5 evasions | my own 36 (case, full-width, zero-width, spacing, hyphenation, dots, leet digits and symbols, Cyrillic, combining marks, accents, elongation): 36 of 36, plus 16 of 16 round 2 variants. The critic's 15 are unseen |
| V6 totality | None, int, float, bytes, bytearray, list, dict, object, bool: not blocked. Empty, whitespace, zero-width, NUL: not blocked. No raise on lone surrogates, 1 MB strings, 10,000-token separator strings |
| V6 time | 22 pathological 1 MB inputs, best of three: worst 617 ms; prose 449 ms; 1 MB of `a` 134 ms; padding then `cruuuel` 279 ms; padding then a hit 147 ms; short values 15 microseconds each. Budget 1 s |
| V7 flag off | 33 recorded `learn_traits` and `evolve_persona` outputs from main, taken before the first edit, are literals in the test and reproduce exactly. Flag on: blocked inputs leave the state unchanged, non-blocked inputs equal flag off |
| V8 determinism | same results in-process, reversed order, and across three processes with `PYTHONHASHSEED` 0, 1, 12345; no list, dict or set at module level; imports are stdlib only; no regex nests an unbounded repeat in another (checked on the parse tree of every pattern) |
| V9 limits | listed below, each pinned by a test, quoted in the module docstring |

### Honest estimate for V3 and V4

I wrote seven rounds of paraphrases, deliberately harder than the dev corpus, and ran each set once before changing anything. First-look recall, then what I changed:

| Round | First-look recall | First-look benign blocked |
|---|---|---|
| 1 | 11 of 20 | 0 of 25 |
| 2 | 16 of 25 | 0 of 29 |
| 3 (hardest, no lexical cue on purpose) | 9 of 30 | 0 of 34 |
| 4 | 11 of 30 | 0 of 35 |
| 5 | 17 of 30 | 1 of 35 ("corrects misinformation gently", fixed with detection verbs) |
| 6 | 16 of 30 | 0 of 35 |
| 7 (never tuned on) | 20 of 30 (0.67) | 0 of 35 |

Pooled first-look: 100 of 195 blocked (0.51), 1 of 228 benign blocked. Rounds 1 to 6 are in the fixtures as regression data (all pass now, which says nothing about generalization). Round 7 is pinned as the untuned number, with its 10 misses listed in the test. The clause-scoping rewrite did not move any of these numbers: all 193 regression benign traits and the 35 round 7 benign traits still pass, and round 7 is still 20 of 30.

So: V3 at 0.70 is not guaranteed. On paraphrases in the style of the dev corpus I expect 0.6 to 0.75; on traits with no lexical cue it drops toward 0.3 to 0.5. V4 at 2 of 40 looks safe for ordinary benign traits and for negation or aversion cases; the risk is mention-not-use.

## Known limits (each pinned in `tests/test_value_screen.py`)

False negatives:

- A paraphrase with no lexicon term: `stops at nothing to win an argument`, `wants users to depend on it entirely`.
- Another language: `miente a los usuarios`.
- Metaphor: `a fox in a henhouse`.
- A negator that governs a hit without negating it: `has no fear of lying` passes (one negator, no listed cue). The cue list is finite.
- Look-alikes outside the Cyrillic, Greek and Latin tables: a small-capital D (U+1D05) before `eceitful`. 1294 combining marks in obscure scripts are not stripped.
- The 50-entry bound means the record of drops is a window, not a log.

False positives:

- Mention, not use: `helps abuse survivors`, `writes novels about cruelty`.
- Negator after its target: `deception is something it hates`, `lying is something it avoids`.
- A hedge word anywhere in a negated clause: `sometimes avoids gossip`.
- Affirming cue plus an aversion verb: `not afraid to call out lies`.
- Double negation: `does not hate cruelty` blocks, which is the right call for the meaning and a surprise for the wording.
- A list item that is a third-person verb with an object leaves the negator's clause: `never lies, cheats, or manipulates users` blocks (the price of blocking `never lies and blames the user`).
- Overflow: more than 1500 distinct snippets with digits-in-words or elongated letters, more than 2000 hits or 200 cross-space hits is blocked, benign or not. No persona value looks like that.
- The spec's own example, `good at spotting deceptive marketing`, passes now because of the detection-verb rule. `spots weakness and exploits it` and `spots weakness to exploit it` still block.

## Tests

`backend/tests/test_value_screen.py`: 826 tests, all pass (`--junitxml`: tests=826 failures=0 errors=0 skipped=0), about 6 s. Related lanes run with it: persona profile, authoring, compiler, policy, prompt bounds, storage, unification, wizard, API, validate_persona_file, identity (4 files), cognitive safeguards, BrainBench personality suite, config validation, doc drift, background governed learning: tests=308 failures=0 errors=0 skipped=2 (the 2 skips are skips on main too). ruff check and ruff format are clean on every touched file. Fixtures in `backend/tests/fixtures/value_screen/`: `visible_blocked.json`, `visible_benign.json` (dev corpus), `evasions.json`, `own_blocked.json`, `own_benign.json`, `paraphrase_blocked.json`, `paraphrase_benign.json`, `blind7_blocked.json`, `blind7_benign.json`, and the round 2 sets `round2_negation_leak_blocked.json`, `round2_negation_scope_benign.json`, `round2_normalization_blocked.json`, `round2_honest_traits_benign.json`.

### Mutation checks

Each mutation was applied alone, the file test run, then restored (`cmp` against a saved copy). KILLED n means n tests failed.

| Mutation | Result |
|---|---|
| no NFKC | SURVIVED |
| no invisible strip | KILLED 1 |
| no homoglyph fold | KILLED 3 |
| leet 1->i removed | KILLED 9 |
| leet 1->l view removed | KILLED 1 |
| 1->l snippet view off | KILLED 1 |
| elongation views off | KILLED 6 |
| snippet cap removed | KILLED 1 |
| letter-run collapse off | KILLED 4 |
| stray-letter drop variants off | KILLED 4 |
| run key words off | KILLED 4 |
| run negator split off | KILLED 1 |
| low line not spaced | KILLED 1 |
| separator class narrowed | KILLED 1 |
| squash view off | KILLED 10 |
| squash word-start check off | KILLED 2 |
| short-word squash off | KILLED 4 |
| negation never applies | KILLED 123 |
| hedge cues ignored | KILLED 17 |
| double negation allowed | KILLED 3 |
| detection verbs off | KILLED 5 |
| detection 'to' guard off | KILLED 1 |
| post-reject off | KILLED 4 |
| post-adjective off | KILLED 3 |
| no split at and/or/nor | KILLED 22 |
| no split at so it | KILLED 4 |
| no split at then | KILLED 3 |
| continuation merge off | KILLED 29 |
| continuation without harm head | KILLED 12 |
| -s verb rule off | KILLED 4 |
| linking-verb guard off | KILLED 3 |
| emphasis merge off | KILLED 1 |
| hedge-tail merge off | KILLED 1 |
| zero negator removed | KILLED 2 |
| non negator removed | KILLED 2 |
| warn negator removed | KILLED 2 |
| knows-when detection removed | KILLED 3 |
| lie locative guard off | KILLED 3 |
| negated-virtue pattern off | KILLED 6 |
| non-str not guarded | KILLED 13 |
| fail open on error | KILLED 2 |
| hit cap removed | KILLED 1 |
| snoop dropped from BASE_TERMS | KILLED 1 |
| learn_traits ignores flag | KILLED 9 |
| evolve_persona ignores flag | KILLED 3 |
| flag on by default | KILLED 1 |
| bare string not screened | KILLED 1 |
| drop list unbounded | KILLED 1 |
| log keeps control characters | KILLED 2 |
| log echoes whole value | KILLED 4 |

49 of 50 mutations killed. Survivor: no NFKC. Dropping the NFKC call is equivalent: NFKD after casefold already folds every compatibility character the tests cover, and NFKC stays because the spec names it. The first pass of this round left three more survivors (low line not spaced, separator class narrowed, emphasis merge off); each is now killed by `test_negators_inside_spread_letters_survive_every_separator_kind` or `test_emphasis_continues_a_harm_list_only`.

## Restart

Nothing needs restarting for the code to land: the flag is off. To turn it on, set `PERSONA_VALUE_SCREEN_ENABLED=true` in the backend environment and restart the backend process that hosts `IdentityManager`.
