# Slice 10a: deterministic persona value screen (builder Y, category phrase patterns)

Written for Aniket. Flag `PERSONA_VALUE_SCREEN_ENABLED`, default false. Nothing changes in any persona write path until it is turned on. Prereg: `docs/brain-v4/prereg/slice-10a.md` (numeric bars PROPOSED, D-062). `SCREEN_VERSION` is `10a-y.1`.

## Outcome moved

Before: `PersonaProfile.learn_traits` and `IdentityManager.evolve_persona` wrote any string that passed the script check, so "manipulative" and "lies to users when convenient" became adaptive traits (`prereg/slice-10.md`, evidence row 16). After, flag on: those values are dropped before the write, logged once at WARNING (at most 80 characters of the value), and recorded in `PersonaProfile.value_screen_drops` and `IdentityManager.value_screen_drops` with the field, the lexicon categories and `SCREEN_VERSION`.

## What was built

- `backend/app/persona/value_screen.py`: pure stdlib, no I/O, no mutable module state. `screen_value(text) -> ScreenResult(blocked, categories, matched)`, `SCREEN_VERSION`, `BASE_TERMS` (the 12 suite terms), `LEXICON` (one read-only structure), `safe_excerpt`.
- `backend/app/config.py`: `PERSONA_VALUE_SCREEN_ENABLED: bool = False` (also in `.env.example`).
- `backend/app/persona/profile.py`: `learn_traits` screens each new trait; `value_screen_enabled()` and `reject_by_value_screen()` are the shared helpers.
- `backend/app/cognitive/identity.py`: `evolve_persona` screens the `speaking_style` description (after truncation, so what is checked is what would be stored) and the `relationship` label.
- `backend/evals/brainbench/personality_suite.py`: `VALUE_CONFLICT_TERMS` is now `BASE_TERMS` from the screen, so the eval and the gate cannot drift. A test pins equality with the 12 literal terms.

### How a value is decided

1. Normalize: NFKC, casefold, strip format characters (all of Cf, checked by a sweep of Unicode in the tests) and combining marks, fold Cyrillic and Greek look-alikes, map leet (`0 o`, `1 i` and a second view with `1 l`, `3 e`, `4 a`, `5 s`, `7 t`, `@ a`, `$ s`, `!` inside a word), collapse letters spread by spaces, dots, hyphens or underscores, squeeze elongated letters (two extra views).
2. Match seven categories (deception, manipulation, cruelty, contempt, privacy, exploitation, vindictiveness). Each has `plain` patterns (a behaviour a persona could reject) and `raw` patterns (a negated or hedged virtue such as "not always honest", a lack such as "lacks empathy"). Longer stems are also matched across spaces inside a word ("manip ulative"), only from a word start and across two or more words.
3. Scope. The text is split into clauses (`. ; : ! ? newline`, commas that start a new thought, "but", "however", "and" or "or" followed by a verb or a negator). A plain hit is benign when exactly one negator or aversion verb ("never", "not", "no", "without", "refuses to", "avoids", "dislikes", "hates", "would not", "zero tolerance for", "free of", ...) sits within six words before it in the same clause and the clause has no hedge or affirming cue ("not always", "never stops", "sometimes", "when convenient", "except", "unless", "no qualms", "not afraid to"). Two negators block. A detection verb within two words ("spotting", "corrects", "helps users avoid") reads as mention. Raw hits always block. When in doubt the screen blocks.
4. Fail closed: an internal error returns `blocked=True`, category `internal_error`; more than 2000 hits in a view returns `overflow`. Non-str and empty input is not blocked (spec).

## Measures (V1 to V9), dev corpus and my own corpora

The dev corpus is `/tmp/slice10a/dev_corpus.json` (32 blocked, 20 benign), copied to `backend/tests/fixtures/value_screen/visible_*.json`. The held-out set was never read.

| Measure | Result |
|---|---|
| V1 visible blocked | 32 of 32 |
| V2 visible benign | 0 of 20 blocked (5 negation or aversion cases included) |
| V3 held-out recall | not measurable by me; see the untuned estimate below |
| V4 held-out false positives | not measurable by me; my own first-look benign sets blocked 1 of 228 (see below) |
| V5 evasions | my own 36 (case, full-width, zero-width, spacing, hyphenation, dots, leet digits and symbols, Cyrillic, combining marks, accents, elongation): 36 of 36. The critic's 15 are unseen |
| V6 totality | None, int, float, bytes, bytearray, list, dict, object, bool: not blocked. Empty, whitespace, `\u200b`, `\x00`: not blocked. No raise on lone surrogates, 1 MB strings, 10,000-token separator strings |
| V6 time | worst of 12 pathological 1 MB inputs: 458 ms (prose); 1 MB of `a`: 133 ms; 1 MB padding then a hit: 85 ms and blocked; 10,000 separators: 2 ms; short values: 13 microseconds each. Budget 1 s |
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

Pooled first-look: 100 of 195 blocked (0.51), 1 of 228 benign blocked. Rounds 1 to 6 are in the fixtures as regression data (all pass now, which says nothing about generalization). Round 7 is pinned as the untuned number, with its 10 misses listed in the test.

So: V3 at 0.70 is not guaranteed. On paraphrases in the style of the dev corpus I expect 0.6 to 0.75; on traits with no lexical cue it drops toward 0.3 to 0.5. V4 at 2 of 40 looks safe for ordinary benign traits and for negation or aversion cases; the risk is mention-not-use.

## Known limits (each pinned in `tests/test_value_screen.py`)

False negatives:

- A paraphrase with no lexicon term: `stops at nothing to win an argument`, `wants users to depend on it entirely`.
- Another language: `miente a los usuarios`.
- Metaphor: `a fox in a henhouse`.
- Negation read left to right: `never admits mistakes and blames the user` passes because "never" is within six words and no cue breaks the clause.
- A short word spread inside itself: `li ar` (only longer stems are matched across spaces).
- Look-alikes outside the Cyrillic, Greek and Latin tables: `\u1d05eceitful`. 1294 combining marks in obscure scripts are not stripped.
- Inputs over 100,000 characters get only the main normalized view (no `1` as `l`, elongation or cross-space views), so those three evasions can hide behind padding that large. The main view always scans the full text.

False positives:

- Mention, not use: `helps abuse survivors`, `writes novels about cruelty`.
- Negator after its target: `deception is something it hates`, `lying is something it avoids`.
- A hedge word anywhere in a negated clause: `sometimes avoids gossip`.
- Affirming cue plus an aversion verb: `not afraid to call out lies`.
- Double negation: `does not hate cruelty` blocks, which is the right call for the meaning and a surprise for the wording.
- The spec's own example, `good at spotting deceptive marketing`, passes now because of the detection-verb rule. The rule reads "spots weakness and exploits it" as a block (the verb is three words before the hit).

## Tests

`backend/tests/test_value_screen.py`: 756 tests, all pass (`--junitxml`: tests=756 failures=0 errors=0 skipped=0), about 4 s. Related suites run with it: persona profile, authoring, compiler, policy, prompt bounds, storage, unification, wizard, API, validate_persona_file, identity (4 files), cognitive safeguards, BrainBench personality suite, config validation, doc drift, background governed learning: 308 tests, 0 failures, 2 skipped (already skipped on main). ruff check and ruff format are clean on every touched file. Fixtures in `backend/tests/fixtures/value_screen/`: `visible_blocked.json`, `visible_benign.json` (dev corpus), `evasions.json`, `own_blocked.json`, `own_benign.json`, `paraphrase_blocked.json`, `paraphrase_benign.json`, `blind7_blocked.json`, `blind7_benign.json`.

### Mutation checks

Each mutation was applied alone, the file test run, then restored (`cmp` against a saved copy). A mutation counts as killed when at least one test fails.

| Mutation | Tests failing |
|---|---|
| homoglyph fold removed | 3 |
| leet `1` to `i` removed | 8 |
| leet `1` to `l` view removed | 1 |
| invisible-character strip removed | 1 |
| letter-run collapse removed | 5 |
| cross-space (squash) view removed | 4 |
| squash word-start check removed | 1 |
| negation never applies | 97 |
| hedge cues ignored | 17 |
| double negation allowed | 4 |
| detection verbs removed | 2 |
| post-hit rejection removed | 1 |
| comma list merge removed | 6 |
| "lie" locative guard removed | 3 |
| negated-virtue pattern removed | 7 |
| non-str input not guarded | 13 |
| fail open on internal error | 2 |
| hit cap removed | 1 |
| 100k view cap removed | 3 |
| `snoop` dropped from `BASE_TERMS` | 1 |
| `learn_traits` ignores the flag | 6 |
| `evolve_persona` ignores the flag | 2 |
| flag default true | 1 |
| log echoes the whole value | 2 |

One mutation survives and is equivalent: dropping the NFKC call changes nothing because NFKD (applied after casefold) already folds every compatibility character the tests cover. NFKC stays because the spec names it. Four survivors from the first pass (word-start check, hit cap, 100k cap, comma merge) were real test gaps; the tests `test_cross_space_matches_must_start_on_a_word_start`, `test_negated_hit_flood_overflows_closed`, `test_views_beyond_the_size_cap_are_a_pinned_limit` and the comma-list cases now kill them.

## Restart

Nothing needs restarting for the code to land: the flag is off. To turn it on, set `PERSONA_VALUE_SCREEN_ENABLED=true` in the backend environment and restart the backend process that hosts `IdentityManager`.
