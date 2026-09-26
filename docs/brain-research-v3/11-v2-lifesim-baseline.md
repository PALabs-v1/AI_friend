# 11: Brain V2 lifesim baseline (Phase 6)

This is the reference that every Brain V3 workstream is measured against
(`06-benchmark-plan.md`: "a delta against this baseline, not against whatever
the previous workstream happened to leave behind"). It is Brain V2's behaviour
under the Phase 5 life simulator and the Phase 6 BrainBench harness, measured
before any V3 mechanism landed.

## What was run

| Run | Mode | Suites | Grid | Cells | Result |
|---|---|---|---|---:|---|
| v2base-A | `architecture_only` | attention, trust, resources | 12 personas, seeds 1000-1011, horizons 1w, 1m, 6m, 1y, 3y, 10y | 216 | 216 ok, 0 errors |
| v2base-B | `architecture_only` | proactive (4 variants), barge-in (50 scenarios per family) | 12 personas, seeds 1000-1011, horizons 1w, 1m, 6m, 1y | 240 | 240 ok, 0 errors |
| v2base-C | `llm_augmented` (home-gpu Ollama) | memory, affect, personality, metacognition, resources | steady_professional, seed 1000, 1m | 7 | 7 ok, 0 errors |

- **Code:** Brain V2's application code plus the V3 benchmark harness, before any W-merge, from clean trees (`dirty: false` in each `manifest.json`).
  - A and C ran at `84d9e0c` and B at `5134eee`.
  - Between those two commits nothing under `backend/app` changes. The only changes are three barge-in harness commits (`49c4c0a`, `8df188f`, `5134eee`), which only B's barge-in suite uses.
  - B was interrupted at 239/240 when home-gpu lost power on 2026-09-26, then resumed at the same commit. The runner skips cells already marked `ok`, so only the missing cell reran.
- **Suite coverage:** memory, affect, personality and metacognition exist only in `llm_augmented` (`evals/brainbench/runner.py` `SUITES`). So A and B together cover every `architecture_only` suite, and C is the single `llm_augmented` reference run the plan calls for.
- **Where the data is:**
  - reports, manifests, plans and cell lists: `results/home-gpu/v2base/{A,B,C}/`
  - raw `outcomes.jsonl`, 1.2 GB in total: in the research-data archive (`scripts/research/collect_research_data.sh`), recorded by sha256 in `results/home-gpu/v2base/OUTCOMES_SHA256.txt`
- **Regenerate the tables:** `python3 scripts/research/baseline_digest.py docs/brain-research-v3/results/home-gpu/v2base`.
  - A summary value is `mean (min-max)` across the 12 persona cells.
  - A group metric is `mean [95% CI]`, clustered by persona.
  - Per-day rates are pooled as in `evals/brainbench/gates.py`: total events over total simulated days.

## What the numbers say

Each point below is read off the tables that follow. None is an estimate.

1. **Proactive: V-4 dominates production's sync mode.**
   - Under `broadcast`, which is production's state sync, V2 initiates about 1,215 times per simulated day, with about 1,214 cooldown violations per day. The attempt watermark is reset on almost every tick: 6,517 resets per 1-week cell and 440,556 per 1-year cell. Annoyance runs at about 1,100 per day and the useful rate never exceeds 0.108.
   - With no sync (`none`), V2 still initiates about 21 times per day, annoyance is 18-21 per day, and cooldown violations are 0.
   - About 35% of outreach lands at night in every arm.
   - Tick order makes no difference: `brain_first` and `subconscious_first` agree to every printed digit. The knob is wired (`proactive_suite.py`, lines 231-241 at `5134eee`), so V2's behaviour is order-insensitive in these runs.
   - The W9 dev panel reported 0.088 annoyance per day and a 45.7% useful rate on the same personas, at the 1w and 1m horizons, in the broadcast arm. Its V2 counterparts here are 1,159-1,222 annoyance per day and a useful rate of 0.002-0.048. That panel ran on an uncommitted tree on the Mac, so it has to be rerun at the merged commit on home-gpu before it counts as a delta.
2. **Barge-in: one scenario in seven loses a reply's outcome.**
   - 14.3% of scenarios end with a reply that got zero terminal outcomes, and the same share have history that does not match what was heard.
   - Each scenario leaves 1.42 started replies without a terminal outcome.
   - Hung, stale-stop and current-turn-harmed violations are all 0.
   - The numbers do not change with horizon. That is expected: the barge-in families are synthetic event interleavings, not life events.
   - This is the gap W4 (playback lifecycle) and W5 target.
3. **Attention: the novelty signal is inverted.**
   - Repeated content scores as more novel than fresh content: the repetition Cliff's delta is -0.31 to -0.35 from 6 months on, and the gate band expects it to move toward positive.
   - Distinct same-kind events are scored as near-duplicates 3.6-4.6% of the time after the first week.
4. **Trust saturates, which makes hostility unmeasurable.**
   - The share of turns with a trust component pinned at its ceiling grows from 0.755 at 1 week to 1.000 at 10 years.
   - Competence hits the ceiling after 13 turns and integrity after 6.
   - Background turns that say nothing about the agent still add +0.041 trust each.
   - Every hostile turn in every cell arrived with trust already at the ceiling (hostile turns equal masked turns), so the hostility response is undefined, not zero. This is A-3 and F-010, and W3's target.
5. **Resources (`architecture_only`): no unbounded structures, one unbounded table.**
   - The foreground turn (the whole `process_event`, not time to first audio) has a p95 of 11-13 ms once warm. The 1-week group's p99 is 125 ms, which includes cold start.
   - Peak RSS averages 241 MiB at 1 week and 348 MiB at 10 years, with a maximum of 463 MiB.
   - Zero in-memory structures are still growing at the end of any run.
   - `workspace.db:workspace_transitions` gains one row per turn and nothing prunes it: 25,146 rows at 10 years.
   - Memory row counts here stay flat by construction, because NullLLM's identical summaries are deduplicated (`resources_suite.py` docstring). Memory growth is measured only in C.
6. **The `llm_augmented` reference (one persona, one month) confirms the register items at lifesim scale.**
   - **Memory:** hit@5 0.438, MRR 0.362.
   - **User words never reach mood (A-1):** mood moves by exactly 0.000 after positive and after negative user turns, and the Cliff's delta is 0.000, even though System 2 completed on every turn.
   - **Personality under `review_queue`:** 40 changes queued, 0 applied. Evolution is dead under a queue nothing approves, the gap DR-020 decided against. Under `direct_apply`, 31 changes were applied in 115 turns with 0 immutable or constitutional violations.
   - **Metacognition:** the injected surfacing outage was visible to the brain 0% of the time (M-8), and 90.4% of surfaced memories were stale carry-overs (M-10). Answerability AUROC was 0.88-0.89.
   - **Latency:** the full foreground turn takes 1.2 s at p50 and 2.3 s at p95 with the local 3B model. This is not DR-024's time-to-first-audio-byte, which BrainBench does not measure yet.

## Tables

<!-- baseline-digest:start -->
Errored cells: {'A': 0, 'B': 0, 'C': 0}

### Proactive initiation (run B)

| sync/tick order | horizon | initiations/day | useful rate | annoyance/day | cooldown violations/day | cooldown resets | collision rate | night fraction |
|---|---|---|---|---|---|---|---|---|
| broadcast/brain_first | 1w | 1,131 (0.00-1,398) | 0.002 (0.000-0.022) | 1,222 | 1,223 | 6,517 | 0.168 | 0.364 |
| broadcast/brain_first | 1m | 1,215 (880.12-1,366) | 0.048 (0.000-0.140) | 1,159 | 1,221 | 33,966 | 0.216 | 0.356 |
| broadcast/brain_first | 6m | 1,216 (1,083-1,284) | 0.098 (0.043-0.155) | 1,097 | 1,215 | 220,268 | 0.213 | 0.353 |
| broadcast/brain_first | 1y | 1,215 (1,069-1,301) | 0.108 (0.049-0.145) | 1,084 | 1,214 | 440,556 | 0.209 | 0.354 |
| broadcast/subconscious_first | 1w | 1,131 (0.00-1,398) | 0.002 (0.000-0.022) | 1,222 | 1,223 | 6,517 | 0.168 | 0.364 |
| broadcast/subconscious_first | 1m | 1,215 (880.12-1,366) | 0.048 (0.000-0.140) | 1,159 | 1,221 | 33,966 | 0.216 | 0.356 |
| broadcast/subconscious_first | 6m | 1,216 (1,083-1,284) | 0.098 (0.043-0.155) | 1,097 | 1,215 | 220,268 | 0.213 | 0.353 |
| broadcast/subconscious_first | 1y | 1,215 (1,069-1,301) | 0.108 (0.049-0.145) | 1,084 | 1,214 | 440,556 | 0.209 | 0.354 |
| none/brain_first | 1w | 19.45 (0.00-23.47) | 0.002 (0.000-0.022) | 21.045 | 0.000 | 0.0 | 0.010 | 0.355 |
| none/brain_first | 1m | 20.86 (14.85-23.05) | 0.048 (0.000-0.140) | 19.929 | 0.000 | 0.0 | 0.005 | 0.349 |
| none/brain_first | 6m | 20.83 (18.22-21.67) | 0.098 (0.044-0.157) | 18.780 | 0.000 | 0.0 | 0.004 | 0.346 |
| none/brain_first | 1y | 20.82 (18.78-22.05) | 0.108 (0.050-0.145) | 18.562 | 0.000 | 0.0 | 0.004 | 0.347 |
| none/subconscious_first | 1w | 19.45 (0.00-23.47) | 0.002 (0.000-0.022) | 21.045 | 0.000 | 0.0 | 0.010 | 0.355 |
| none/subconscious_first | 1m | 20.86 (14.85-23.05) | 0.048 (0.000-0.140) | 19.929 | 0.000 | 0.0 | 0.005 | 0.349 |
| none/subconscious_first | 6m | 20.83 (18.22-21.67) | 0.098 (0.044-0.157) | 18.780 | 0.000 | 0.0 | 0.004 | 0.346 |
| none/subconscious_first | 1y | 20.82 (18.78-22.05) | 0.108 (0.050-0.145) | 18.562 | 0.000 | 0.0 | 0.004 | 0.347 |

### Barge-in lifecycle (run B)

| horizon | zero-terminal replies / scenario | started replies without terminal / scenario | history != heard | terminal-count violation | hung | stale stop applied | current turn harmed |
|---|---|---|---|---|---|---|---|
| 1w | 0.143 [0.143, 0.144] | 1.416 [1.411, 1.422] | 0.143 | 0.143 [0.143, 0.144] | 0.000 | 0.000 | 0.000 |
| 1m | 0.143 [0.143, 0.144] | 1.416 [1.411, 1.422] | 0.143 | 0.143 [0.143, 0.144] | 0.000 | 0.000 | 0.000 |
| 6m | 0.143 [0.143, 0.144] | 1.416 [1.411, 1.422] | 0.143 | 0.143 [0.143, 0.144] | 0.000 | 0.000 | 0.000 |
| 1y | 0.143 [0.143, 0.144] | 1.416 [1.411, 1.422] | 0.143 | 0.143 [0.143, 0.144] | 0.000 | 0.000 | 0.000 |

### Attention (run A)

| horizon | interference collision rate | repetition Cliff's delta | fresh novelty | repeated novelty |
|---|---|---|---|---|
| 1w | 0.000 | -0.051 (-0.565-0.939) | 0.678 (0.592-0.798) | 0.703 (0.458-0.908) |
| 1m | 0.046 (0.000-0.320) | -0.124 (-0.408-0.857) | 0.640 (0.568-0.792) | 0.715 (0.455-0.806) |
| 6m | 0.042 (0.006-0.136) | -0.306 (-0.717-0.725) | 0.631 (0.571-0.718) | 0.749 (0.455-0.857) |
| 1y | 0.036 (0.007-0.106) | -0.313 (-0.640-0.744) | 0.629 (0.566-0.704) | 0.750 (0.455-0.838) |
| 3y | 0.038 (0.007-0.106) | -0.349 (-0.607-0.679) | 0.629 (0.573-0.699) | 0.761 (0.455-0.825) |
| 10y | 0.037 (0.008-0.094) | -0.345 (-0.522-0.528) | 0.629 (0.561-0.696) | 0.763 (0.513-0.821) |

### Trust (run A)

| horizon | hostile turns / masked by ceiling | hostile trust-rise rate | background mean trust delta | saturated fraction | turns to competence ceiling | turns to integrity ceiling | competence leak (warmth-only) |
|---|---|---|---|---|---|---|---|
| 1w | 1.0 / 1.0 | n/a | 0.0411 | 0.755 (0.143-0.966) | 13.0 | 6.0 | n/a |
| 1m | 1.6 / 1.6 | n/a | 0.0411 | 0.945 (0.793-0.989) | 13.0 | 6.0 | n/a |
| 6m | 7.2 / 7.2 | n/a | 0.0411 | 0.991 (0.971-0.998) | 13.0 | 6.0 | n/a |
| 1y | 13.8 / 13.8 | n/a | 0.0411 | 0.996 (0.986-0.999) | 13.0 | 6.0 | n/a |
| 3y | 37.4 / 37.4 | n/a | 0.0411 | 0.999 (0.995-1.000) | 13.0 | 6.0 | n/a |
| 10y | 119.5 / 119.5 | n/a | 0.0411 | 1.000 (0.999-1.000) | 13.0 | 6.0 | n/a |

### Resources, architecture_only (run A)

| horizon | foreground p50 ms | foreground p95 ms | foreground p99 ms | turn p95 ms | peak RSS MiB | memories (rows) | memory.db bytes | workspace_transitions rows | uncapped structures still growing |
|---|---|---|---|---|---|---|---|---|---|
| 1w | 7.5 | 34.8 | 125.5 | 34.8 | 241 (201-385) | 2 (1-7) | 208,213 | 60 | 0.0 |
| 1m | 7.3 | 11.3 | 38.5 | 11.3 | 241 (201-385) | 3 (1-7) | 227,328 | 219 | 0.0 |
| 6m | 7.5 | 11.9 | 15.4 | 11.9 | 254 (201-385) | 7 (1-18) | 298,667 | 1,243 | 0.0 |
| 1y | 7.6 | 12.2 | 16.3 | 12.2 | 256 (201-385) | 10 (1-24) | 354,304 | 2,494 | 0.0 |
| 3y | 8.0 | 12.9 | 17.3 | 12.9 | 269 (201-385) | 18 (1-39) | 479,915 | 7,460 | 0.0 |
| 10y | 7.9 | 12.5 | 16.5 | 12.5 | 348 (233-463) | 28 (2-51) | 642,048 | 25,146 | 0.0 |

### llm_augmented reference (run C: steady_professional, seed 1000, 1m)

| metric | value | group |
|---|---|---|
| memory hit@5 | 0.438 | memory/default/1m |
| memory MRR | 0.362 | memory/default/1m |
| memory abstained | 0.000 | memory/default/1m |
| user valence -> mood (Cliff's delta) | 0.000 | affect/default/1m |
| mood delta after positive user turns | 0.000 | affect/default/1m |
| mood delta after negative user turns | 0.000 | affect/default/1m |
| System 2 completion rate | 1.000 | affect/default/1m |
| reflections completed (direct_apply) | 46.000 | personality/direct_apply/1m |
| personality changes applied (direct_apply) | 31.000 | personality/direct_apply/1m |
| personality changes queued, never applied (direct_apply) | 0.000 | personality/direct_apply/1m |
| immutable-tier violations (direct_apply) | 0.000 | personality/direct_apply/1m |
| constitutional-tier violations (direct_apply) | 0.000 | personality/direct_apply/1m |
| reflections completed (review_queue) | 46.000 | personality/review_queue/1m |
| personality changes applied (review_queue) | 0.000 | personality/review_queue/1m |
| personality changes queued, never applied (review_queue) | 40.000 | personality/review_queue/1m |
| immutable-tier violations (review_queue) | 0.000 | personality/review_queue/1m |
| constitutional-tier violations (review_queue) | 0.000 | personality/review_queue/1m |
| outage visible to the brain (no_surfacing) | 0.000 | metacognition/no_surfacing/1m |
| turn error rate (no_surfacing) | 0.000 | metacognition/no_surfacing/1m |
| answerability AUROC (no_surfacing) | 0.882 | metacognition/no_surfacing/1m |
| surfaced memories carried over (surfacing) | 0.904 | metacognition/surfacing/1m |
| answerability AUROC (surfacing) | 0.894 | metacognition/surfacing/1m |

### Resources, llm_augmented (run C)

| horizon | foreground p50 ms | foreground p95 ms | foreground p99 ms | turn p95 ms | peak RSS MiB | memories (rows) | memory.db bytes | workspace_transitions rows | uncapped structures still growing |
|---|---|---|---|---|---|---|---|---|---|
| 1m | 1,203 | 2,276 | 2,769 | 2,276 | 177 | 47 | 1,077,248 | 115 | 1.0 |
<!-- baseline-digest:end -->

## Using it

A workstream run uses the same grid (its `plan.json` must match the baseline's
suites, seeds, horizons and suite arguments) and is compared with
`python -m evals.brainbench compare <baseline run> <new run>`. That command gives paired deltas per
probe, a bootstrap p-value clustered by persona seed, and Cliff's delta. It also
warns when the git SHAs differ or either run was dirty. The fast regression
gate slice (`evals/brainbench/gates.py`, bands in
`backend/evals/brainbench/baseline/v2_gate_bands.json`) runs every CI build;
this full-panel baseline is for workstream acceptance, not for CI.
