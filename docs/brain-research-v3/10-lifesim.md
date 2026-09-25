# Lifesim: the synthetic longitudinal life simulator (Phase 5)

`backend/evals/lifesim/`. A pure generator (no `app.*` imports, enforced by a test) that produces
a seeded, byte-deterministic human life and renders it as text observations plus ground-truth-derived
probes, per the master prompt's Objective 4 and Fifth Stage, and `06-benchmark-plan.md`'s design.

## Pipeline

```
persona (personas.py) -> world (world.py, timeline.py) -> events (events.py, day-by-day)
  -> sessions (schedule.py) -> observe (observe.py: text + annotations)
  -> probes (probes.py: questions + answers derived from the timeline)
  -> files (schema.py: public/ + oracle/, sha256-hashed manifest)
```

`world.build_world` seeds a persona-driven human at `t0`: identity, family/friends (with deliberately
confusable names), a residence/employment/education history that predates `t0`, preferences, beliefs,
a pet, and the robot. `timeline.Timeline` is the event-sourced ground truth: every fact is an
`Assertion` on an `(entity, attribute)` slot with a validity window; a genuine change supersedes and
keeps history, nothing is ever deleted. `events.Life` walks the calendar one day at a time, 18
independent-RNG-stream processes (trivial chatter, preference drift, career, moves, the network,
trips, health, house guests, commitments/plans that get rescheduled or cancelled, social events,
robot-directed events, emotional events, goals, projects, interference-family episodes). `schedule.py`
turns the persona's interaction frequency, chronotype and weekly burstiness into actual session
timestamps; the robot lives at home, so silences during a trip are real, not random gaps.

`observe.render` turns events and timeline assertions into what the user actually said: sessions of
turns built from a frozen template bank (`banks/`), carrying hidden `Annotation`s (intent, tags,
claims with truthfulness, importance, valence) that only the scorer may read. `probes.build` asks
12 categories of question (current/historical/stale-trap/multi-hop/interference/temporal/trivia
recent-and-old/unanswerable/relationship-defining/commitment-due/contradiction-surface), each with an
`answer` plus a `derivation` that `probes.recompute` can re-derive from the timeline alone, without
reading any text.

## Leakage boundary (R9)

Public files (`public/turns.jsonl`, `public/probes.jsonl`) carry only `turn_id`/`session_id`/`t`/
`speaker`/`text` and `probe_id`/`t`/`text`. Everything else — the world, the timeline, the event log,
annotations, and answers — lives under `oracle/`, so a harness can mount `public/` alone. A gate test
greps every public turn/probe for internal ids, `_`-underscored attribute keys, and tag/category
words.

## Seed splits and bank freezing

`splits.py` enforces dev (1000-1099) / tune (2000-2199) / validation (3000-3099) / held-out
(9000-9099) in code: `generate(..., final_run=True)` is required for a held-out seed, and every
held-out generation appends a line to `docs/brain-research-v3/results/HELDOUT_LOG.md`.
`banks.verify_manifest` refuses to load a bank whose sha256 no longer matches
`banks/MANIFEST.json`, so nobody accidentally regenerates (and re-randomizes) the benchmark's own
fixtures. `bank_expand.py` is the one deliberately latent step (asks local Claude Code, per
CLAUDE.md's LLM-access rule, for new paraphrase variants per template family, validates each
candidate's placeholders before accepting it, re-freezes); it never runs during generation.

## Tournament: two independent builders, one survivor

Per the fan-out rules, the text layer (`observe.py` + `probes.py`) was built as a variant tournament
against a frozen rubric (`/tmp/lifesim/critique/rubric.md`, not committed — a scratch artifact per
CLAUDE.md's "critique stays under /tmp" rule): Codex C3 in its own worktree, and a Claude sub-agent
("variant B") in an isolated worktree, both given the identical spec, neither able to see the other's
work.

**Variant B stalled**: 17 minutes with no file writes after its own setup step, and no response to a
direct status query. Stopped via `TaskStop`. Its worktree (`claude/lifesim-text-b`) contributed
nothing to the final code.

**Codex C3 crashed near completion** — an internal `codex_models_manager` network timeout killed the
process — but had already written `observe.py`, `probes.py`, both bank files and its own
`test_lifesim_text.py` (all syntactically complete, its own 8 tests passing) before the crash, with
nothing committed. Its output was salvaged and is the actual code in this repo today.

## What the harsh-critic pass found (and fixed)

Codex's own 8 tests, and this project's 24 independent, blind gate tests (`test_lifesim_integrity.py`,
written before Codex's output was read), both passed on the first real run — after two bugs in my
own gate tests were fixed (a microsecond-precision loss in `Claim.about`'s serialization that made a
truthful claim look false at an exact-instant boundary; two test assertions that were stricter than
the spec actually required). That is real, but it is not the same as "done": reading the generated
text cold, at scale, surfaced defects no schema-shaped test could catch.

| Defect | Found by | Fix |
|---|---|---|
| Every turn for a "chatty" persona got a generic opener AND trailer glued on unconditionally (100% of 39,439 turns), producing incoherent text ("The light looks lovely today.; I have a little more to say about it.") | Reading sampled output | Gated both behind a verbosity-scaled probability, capped well under 1.0 |
| The probability fix let a tail chain (e.g. the same trailer twice in a row) | Reading the fixed output | Capped at one tail, never a chain |
| A bank template combined a period-terminated lead-in with a lowercase continuation ("I miss them. my tortoise Olive died") | Reading sampled output | One template edit (period → colon, matching its five siblings) |
| A style-wrap opener could coincidentally repeat a discourse marker already at the start of the line ("Oh, and Oh, and I had tacos...") | Reading sampled output | A generic stutter-detector; discard the wrap if it fires |
| A joke template assumed a predicate continuation, not a full clause ("I'm absolutely I'm quitting to become a lighthouse keeper") | Reading sampled output | One template edit |
| Two probe families rendered `{person}`/`{topic}` from a value that could be empty or a self-referential fallback string ("involving ?", "about city?", "the earlier earlier detail") | Reading sampled output + a systematic sweep | Skip the templates that need a companion when none exists; fix the self-mapped labels; fix the fallback string |
| `person_move`/`person_job_change` events had no `name` in their payload at all — every one of them rendered with an empty subject | A systematic multi-persona sweep for empty placeholders | Resolve the subject from the event's own entity instead |
| The user's own partner (both the world-build-time and the mid-simulation creation paths) was never seeded a `city`, so their first move had nothing to move from ("moved from None to Seville") | The same sweep | Seed `city` in both creation paths, matching every other person in the network |
| `_slot_line`'s `home_city` special case set the subject to "home" *and* the label was already "home" ("home home is Melbourne") | The same sweep | Removed the redundant special case |
| A goal/project created and rolling its achieved/abandoned dice on the same day could draw an earlier random hour for the second event than its own creation, asserting backwards in time on the same slot — a hard crash, not just bad text | A 150-run, 10-year stress sweep (this class of bug needs enough goal-turnover cycles to surface) | A goal must survive its creation day before it can be marked achieved/abandoned |

None of this is a knock on Codex's contribution: the truth-integrity, leakage-boundary, determinism
and coverage machinery were all correct on the first pass. What it demonstrates is exactly what the
"harsh critic" step in CLAUDE.md's fan-out protocol is for — a spec and a deterministic test suite
describe the *shape* of correct output; they do not read it. After every fix: 80/80 tests pass, and a
150-seed, 10-year-horizon stress sweep (the full persona panel plus random draws, ~566k generated
turns+probes in the broader correctness sweep) found zero crashes and zero remaining instances of the
specific defect patterns above.

## Numbers

- 10 simulated years for the busiest archetype (`socialite`): ~1.7-2.5s, ~39,000 turns, ~900-1000
  probes, well under the 30s/10y budget implied by the spec.
- 150 generations (full panel + random draws, 10y horizon each): 0 crashes, 4m15s total.
- 22 generations swept for text-quality artifacts (empty placeholders, bad capitalization, stutters,
  literal `None` leaks): 0 remaining, 565,880 rows checked.
- Template banks: 402 templates across 39 families (6 per family for most utterance families, per
  spec's "expand later" plan; 20 per family for the probe-question families, exceeding the 6-minimum).
- Determinism: same seed is byte-identical (sha256-checked); a shorter horizon is an exact byte-prefix
  of a longer one at the same seed (turns and timeline both).

## What is not built yet

- The bank expansion itself (`bank_expand.py` exists and is tested, but has not been run) — the 6
  templates per utterance family are the seed set, not the final 20+.
- BrainBench (Phase 6): the harness that drives real brain components against this output does not
  exist yet. Nothing here has been run against `app.*` code.
- The held-out log is empty by construction: no held-out seed has been generated yet, because tuning
  hasn't started.
