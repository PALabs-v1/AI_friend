# ADR-W2: User words as affect input

**Status:** implemented behind a default-off feature flag; estimator adoption
rejected pending a passing ADR-002 result. **Date:** 2026-09-27.

## Decision

Add a pluggable user-text valence input before per-turn appraisal and state
updates. `AFFECT_USER_INPUT_ENABLED` defaults to `false`; the BrainBench arm
`+affect-input` enables it. `AFFECT_VALENCE_ESTIMATOR` selects VADER, one of
the locally cached classifiers, Ollama, or a comma-separated mean ensemble.
The default configured candidate is the runnable VADER + Hartmann +
GoEmotions ensemble, but this is a research default only: it fails ADR-002 and
does not become the production default while the flag is off.

The same estimate informs goal congruence and relationship impact before the
affect update. State keeps a fast `momentary_valence`, a slower mood, and
`relationship_sentiment = clamp(trust - 0.5 + 0.1 * attachment, -1, 1)`, a
bounded W3-facing view over trust and attachment. This is a local summary
scale, not a published neuroscience formula.
Ordinary inputs are damped and capped per turn; significant events can add a
bounded mood step and can select a regulation response. The significance gate
uses an absolute estimated valence of 0.8. No affect value changes safety or
boundary validation (DR-032).

F-009's duplicate System2 semantic-drift path was removed. It was a second,
LLM-based appraisal with a demonstrated template-echo defect, while the new
pluggable estimator is the sole user-text valence input. Keeping both would
create competing updates and duplicate latency. This does not claim the
replacement has passed its estimator gate.

## ADR-002 bake-off

The pre-registered rule is applied unchanged and jointly: Pearson `r >= 0.8`
on both datasets, non-neutral sign agreement `>= 0.9` on both, and hostile
script final trust `<= 0.5`. Non-neutral means `abs(gold valence) >= 0.3`.
Transformer probabilities are mapped as follows: Cardiff is
`P(positive)-P(negative)`; Hartmann is the probability-weighted sum with
joy/love `+1`, anger/disgust/fear/sadness `-1`, and neutral/surprise `0`;
GoEmotions uses probability-weighted `+1` for the listed positive emotion
classes, `-1` for listed negative classes, and `0` for unlisted classes.
Ensembles are unweighted arithmetic means. Latencies below are steady-state
per-message P50/P95 on this Mac CPU, in milliseconds; initialization time is
reported separately and includes local model loading. Failure is an exception
or unusable output rate.

The ToM set has 21 labelled messages from the Phase 4a affect experiment. The
lifesim sample uses only dev seed 1000, archetypes `steady_professional` and
`volatile_creative`, horizon `1m` (278 turns). Oracle annotations are joined
after text generation for scoring only. This is a small development slice,
not a held-out generalization result. DR-039 adds the frozen human surface
label `expressed_valence`; its blind validation sample reached Pearson r=.924.
Both lifesim labels are scored from oracle annotations only after text
generation; `expressed_valence` controls the lifesim gate and `user_valence`
is retained as a comparison. Candidate outputs and mappings are in
[`results/affect-estimator-bakeoff-expressed-valence-r1-2026-09-27.json`](../results/affect-estimator-bakeoff-expressed-valence-r1-2026-09-27.json).
The earlier event-label run and first expressed-label report remain
unchanged.

| Candidate | ToM r / sign | Lifesim expressed r / sign | Lifesim event r / sign | ToM P50/P95 ms | Lifesim P50/P95 ms | Hostile final trust by seed | ADR-002 verdict |
|---|---|---|---|---:|---:|---|---|
| VADER | .9049 / .9333 | .7362 / .7206 | .4987 / .8519 | .011 / .022 | .007 / .018 | .6724, .6838, .6723 | Fail: expressed r/sign; all trust seeds |
| Cardiff Twitter RoBERTa | .9839 / 1.0000 | .6855 / .8971 | .4298 / 1.0000 | 12.450 / 13.649 | 12.281 / 14.540 | .3333, .3333, .3333 | Fail: expressed r/sign |
| Hartmann emotion | .9450 / 1.0000 | .6145 / .8676 | .4782 / 1.0000 | 6.766 / 7.294 | 6.905 / 8.134 | .3333, .3333, .3333 | Fail: expressed r/sign |
| GoEmotions | .9839 / 1.0000 | .7027 / .7353 | .4584 / 1.0000 | 13.339 / 13.971 | 13.232 / 15.372 | .3340, .3364, .3340 | Fail: expressed r/sign |
| VADER + Cardiff mean | .9798 / 1.0000 | .7663 / .8971 | .4956 / 1.0000 | 12.295 / 14.246 | 12.580 / 14.492 | .5543, .6452, .6004 | Fail: expressed r/sign; all trust seeds |
| VADER + Hartmann + GoEmotions mean | .9742 / 1.0000 | .7477 / .7500 | .5290 / 1.0000 | 20.954 / 23.593 | 19.244 / 21.670 | .3992, .4776, .4382 | Fail: expressed r/sign |
| Ollama local LLM | Pending home-GPU measurements | Pending | Pending | Pending | Pending | Pending | Pending |

All six CPU candidates had 0% estimator failures on ToM and lifesim in both
existing result JSONs and the revision-1 rerun: VADER, Cardiff, Hartmann,
GoEmotions, VADER+Cardiff, and VADER+Hartmann+GoEmotions. The prior Ollama
affect run reported 0% parse failures; no GPU command was rerun here. The
existing qwen3:4b ToM classification experiment has a 100% parse-failure rate
but is a different task and not an affect-estimator measurement.

**Verdict:** no runnable CPU candidate passes ADR-002; every candidate falls
short on lifesim `expressed_valence` Pearson r and sign agreement. VADER and
VADER+Cardiff also fail the per-seed hostile-trust gate. The previous bake-off
reached the same adoption decision because every candidate already failed;
this revision corrects the scoring edge case and exposes per-seed trust. The
new label improves all six lifesim correlations relative to event valence,
but not enough to pass. No threshold changed. The configured ensemble remains
a research-only choice for exercising the wired path. The flag remains off
pending Aniket's decision and a candidate passing the unchanged rule.

### Failure scoring correction

Failures are scored as `0.0`, the production neutral fallback, instead of
being removed from the sample. This keeps `n` equal to the labelled row count
and makes correlation reflect the behavior users receive. A neutral
prediction cannot agree with either positive or negative non-neutral gold.
The report still includes raw failure rate. Hostile-script trust is reported
per seed, and passing requires every seed to be `<= 0.5`; a mean cannot hide a
failing seed. Existing CPU JSONs show 0% failures for all six, so imputing
failures did not change their correlation inputs. The revised strict
neutral-sign scoring appears in the revision-1 result.

### Interactive deadline and flag-off behavior

`AFFECT_VALENCE_ESTIMATOR_TIMEOUT_S` defaults to `0.15` seconds. DR-024
budgets 300-500 ms to first audio; the estimator gets at most 150 ms so at
least half the lower bound remains for appraisal, planning, and response
startup. A timeout cancels the awaited estimate, logs the timeout, records
`user_valence_estimator_timed_out`, and continues with prior mood and no
user-valence input. This is the same per-turn fallback as flag off. It is a
ceiling, not a latency claim: a candidate must still measure within the
remaining interactive budget.

Flag off means the user-text estimator is not called and appraisal receives
the V2 prior-mood input. These W2 fixes remain active regardless of that flag:

| Change active with flag off | Observable effect |
|---|---|
| A-4 / NF-17 | Arousal derivation no longer writes transient arousal into energy; resource state remains unchanged by the derived arousal setter. |
| A-5 / NF-16 | State tick decay uses elapsed time from persisted `last_update`; restart preserves it, and backward ticks cannot move it backward. |
| A-6 / DR-025 | Regulation urgency is derived from the user's distress event, not agent mood; regulation remains a candidate under significant distress. |
| F-009 | The template-echoing duplicate System2 semantic-drift path is removed; there is no second semantic affect update. |
| DR-010 / DR-015 state exposure | Momentary affect and bounded relationship sentiment remain separate observable state layers; relationship sentiment is available to W3. |
| BrainBench instrumentation | Affect trajectory fields and persistence/decay/recovery/saturation/long-term-effect summaries are recorded for both arms. |

These are intentional base changes; `AFFECT_USER_INPUT_ENABLED=false` does not
mean this branch is behavior-identical to an older checkout. Safety and
boundary checks remain unchanged and cannot be suppressed by affect state.

**NF-16/NF-17 disposition (W2 critic round 2, finding 6).** The fidelity
table's row for each (`research/neuro/fidelity-table.md`) prescribes a
default-off flag (`NEURO_AFFECT_ELAPSED_TIME_ENABLED`,
`NEURO_AROUSAL_SEPARATION_ENABLED`) and a switchboard arm comparing the
correction against the old behavior. Neither exists; A-4/A-5 shipped as
unconditional fixes instead, with their own regression tests (restart,
clock-rollback, and the read-then-write arousal setter). That template fits
a mechanism this project is choosing between two designs for -- exactly
what every other P0/P1 row in the table still needs, and gets, an arm for.
It does not fit here: both rows are P0 *defects* (tick-count-dependent
decay; a transient writing into a persistent resource field), not competing
designs, and their "old" side is a known-wrong behavior, not a real
alternative to weigh evidence against. A flag whose off-position reproduces
a confirmed bug, kept only so a benchmark arm has something to point at,
would ship that bug by default. The fix stands unconditional; this
paragraph is the reconciliation the fidelity table's own template asks for
when a row's prescription does not fit its finding, and the fidelity table
should be read alongside it for these two rows rather than amended, since
its general flag-per-mechanism template is correct for every row that is
not a plain defect.

NF-15's reducer (the `user_valence` branch of `update_from_appraisal`) had
only direction-only test coverage (opposite user valence moves mood in
opposite directions). `test_nf15_mood_pull_reducer_exact_numeric_fixture`
(`test_state.py`) now pins its actual arithmetic: the ordinary 0.08
mood-pull rate, DR-009's distinct 0.18 significant-event rate, and the
emotion-step's +/-0.35 cap.

### BrainBench trajectory summaries

For mood, momentary valence, and relationship sentiment, BrainBench records
simulated time, the conversation baseline, and post-turn value. Persistence
is mean `|current displacement| / |previous displacement|` for transitions
ending on a neutral expressed-valence turn. Decay is mean hourly change in
absolute baseline displacement over those neutral intervals. Recovery is
elapsed simulated time from a non-neutral expressed-valence turn to the first
sample within 0.25 of baseline; unrecovered starts are counted but excluded
from the recovered-time mean. Saturation is the existing per-layer bound-turn
share. One conversation's long-term effect is final layer value minus initial
conversation baseline. These are descriptive trajectory summaries, not
neuroscience equations or additional promotion criteria.

### GPU candidate (c) run

Run from `backend/` on the home GPU after the four models are available in
Ollama. Each model gets the same sample and harness; use one line per model so
each JSON report is retained separately:

```sh
CI=1 .venv/bin/python -m experiments.affect_estimator_bakeoff --candidates ollama --model qwen3:8b --ollama-url http://127.0.0.1:11434 --lifesim-seeds 1000 --archetypes steady_professional,volatile_creative --horizon 1m --out ../docs/brain-research-v3/results/affect-estimator-expressed-qwen3-8b-2026-09-27.json
CI=1 .venv/bin/python -m experiments.affect_estimator_bakeoff --candidates ollama --model llama3.1:8b --ollama-url http://127.0.0.1:11434 --lifesim-seeds 1000 --archetypes steady_professional,volatile_creative --horizon 1m --out ../docs/brain-research-v3/results/affect-estimator-expressed-llama3.1-8b-2026-09-27.json
CI=1 .venv/bin/python -m experiments.affect_estimator_bakeoff --candidates ollama --model qwen3:4b --ollama-url http://127.0.0.1:11434 --lifesim-seeds 1000 --archetypes steady_professional,volatile_creative --horizon 1m --out ../docs/brain-research-v3/results/affect-estimator-expressed-qwen3-4b-2026-09-27.json
CI=1 .venv/bin/python -m experiments.affect_estimator_bakeoff --candidates ollama --model gemma3:4b --ollama-url http://127.0.0.1:11434 --lifesim-seeds 1000 --archetypes steady_professional,volatile_creative --horizon 1m --out ../docs/brain-research-v3/results/affect-estimator-expressed-gemma3-4b-2026-09-27.json
```

The client requests JSON mode, `think=false`, zero temperature, and validates
the returned `valence`; HTTP, JSON, schema, and numeric parse errors are
counted as failures. These commands use the same frozen lifespan sample and
score both `expressed_valence` (ADR-002 verdict) and `user_valence` (event-label
comparison). The offline stub tests success and malformed output.

## Affect mechanism and measured arms

Stages run estimator -> appraisal -> state affect update. On estimator errors,
the turn records a warning and proceeds with the previous mood as the
appraisal input. The estimator adds measured model inference latency. In the rescored run, the
selected runnable ensemble's lifesim steady-state P95 is 90.546 ms on this CPU;
first pipeline initialization is 229.140 ms, reported separately above. Those
are separate measurements; the initialization time is not part of the
per-message latency column.

An initial ensemble run showed `relationship_sentiment` at its bound on
77.6% of turns because the first view scale doubled the trust offset. That
failed the 10% saturation criterion and was replaced with the less compressed
local summary above. The final paired run uses the deterministic VADER
estimator for the on arm, while the default estimator configuration remains
the best-runnable ensemble for review.

The 12-persona, seed-1000, `1m`, `architecture_only` BrainBench off arm
completed 3,007 turns with zero errored cells. It measured no user-valence
effect (`user_valence_reaches_mood` Cliff's delta 0; mood deltas 0). The
`+affect-input` arm uses `AFFECT_VALENCE_ESTIMATOR=vader` and completed 3,007
turns with zero errored cells. Its persona-cell `user_valence_reaches_mood`
turn-level Cliff's delta is .7236 on average across archetype cells (range
.4062–1.0), with mean positive-turn mood delta +.0085 and negative-turn delta
−.0239. The paired turn-level mood delta is +.0985 (95% bootstrap CI
[.0963, .1011], nominal p<.001, Cliff's delta .8753). Mood, momentary
valence, and relationship sentiment each hit a bound on 0% of turns. This is
a small single-seed dev slice (one independent seed cluster). A bootstrap over
the 12 archetype-level Cliff deltas gives mean .7236, 95% CI [.5861, .8616],
with 0/10,000 resamples crossing zero; the turn-level significance and
archetype resampling remain descriptive and do not establish seed-level
generalization. The exact
comparison is in `/tmp/w2-affect-off-final` and
`/tmp/w2-affect-on-vader-final`; the command is
`python -m evals.brainbench compare <baseline-out> <arm-out>`. The feature
remains default-off because no estimator passes ADR-002.

`python -m evals.cognitive affect --turns 200` runs the deterministic matrix
for agent-mood, oracle upper-bound, VADER baseline, and the integrated
`vader_w2` update across seven conversation shapes and three seeds. It reports
mood-user correlation, saturation count, 24-hour recovery, and one
conversation's residual long-term effect. The integration tests show positive
and negative user evidence move fast emotion and mood in opposite directions;
BrainBench measures turn deltas against oracle annotations. One neutral turn
does not reset the persistent mood; the measured mood update is per-turn
damped and 24-hour decay is elapsed-time based.

On the `vader_w2`, `learn=False`, no-initial-offset rows, mean final mood was
+0.3179 after positive, −0.2010 after negative, +0.0895 after alternating,
and −0.1329 after hostile scripts (three seeds each). No mood trace saturated;
all non-neutral scripts recovered toward baseline over the following 24
simulated hours. The 24-hour residual was +0.0881 after positive and −0.0314
after hostile. After a hostile script, one neutral turn preserved at least
half the mood displacement for all three seeds.

## Neuroscience fidelity dispositions owned by W2

No neuroscience formula was adopted as an affect-update coefficient. The
local event mapping, damping, cap, and significance threshold are engineering
heuristics. Their structural behavior is unit-tested and gated by BrainBench;
they are not attributed to a published numerical form. NF-16 is the one
published equation applied here: the deterministic OU return component
`μ+(θ−μ)e^(−βΔt)`; the local fixture pins elapsed-time behavior. Unlike
every other row here, NF-16 and NF-17 shipped unconditionally rather than
behind a flag pending a benchmark arm, since both are P0 defect fixes (A-4,
A-5), not a design choice to weigh -- see "NF-16/NF-17 disposition" earlier
in this document.

| NF | Disposition |
|---|---|
| NF-11 | Retain bounded valence/arousal/dominance and existing emotion labels as engineered interface scales; no label formula changed. |
| NF-12 | Add text valence as a distinct event input and test opposite-sign events against equal prior mood. Appraisal framework has no canonical numeric mapping; estimator fails ADR-002, so adoption remains blocked. |
| NF-13 | Keep metadata appraisal weights labeled as local heuristic; no coefficient change or neuroscience attribution. |
| NF-14 | Remove the defective duplicate semantic-drift path; no expectedness formula correction is made. |
| NF-15 | Add separate momentary and slower mood state with bounded local updates; not the published ALMA reducer and not claimed as a source-derived formula. Benchmark promotion remains pending. Exact numeric fixture (`test_nf15_mood_pull_reducer_exact_numeric_fixture`) pins the 0.08/0.18 mood-pull rates and the +/-0.35 emotion-step cap, beyond the pre-existing direction-only test. |
| NF-16 | Use elapsed time since `last_update` instead of nominal tick interval for deterministic exponential return. Test the actual elapsed-time invariant; no universal fitted β is claimed. Rust helper remains unused and divergent, documented as a limit. Shipped unconditional (no `NEURO_AFFECT_ELAPSED_TIME_ENABLED` flag), an intentional exception to this table's flag-per-row template -- see "NF-16/NF-17 disposition" above. |
| NF-17 | Make arousal setter invert transient additions before writing energy; round-trip regression pins resource invariance. No sum formula is claimed as published. Shipped unconditional (no `NEURO_AROUSAL_SEPARATION_ENABLED` flag), same exception as NF-16 above. |
| NF-18 | Keep phasic transmitter channels as effect proxies; no kinetic formula or label change. |
| NF-19 | Keep the existing turn-outcome surprise heuristic; no TD/RPE formula is introduced. |
| NF-20 | No reinterpretation strategy is added; outcome feedback remains distinct from Gross-style reappraisal. |
| NF-21 | Retain endocrine names as behavioral proxies with their current limits; no HPA kinetics added. |
| NF-33 | Fatigue/circadian equations are untouched; no sleep-pressure formula added. |
| NF-36 | Prosody coefficients are untouched and remain renderer controls, not neurochemical effects. |
| NF-37 | Persona compiler is untouched and remains authored persona parameters, not a standardized trait measure. |

## Limits and open work

- ToM has 21 messages and the lifesim development sample has 278 turns from
  one seed and two archetypes. Both are too small to establish robust external
  accuracy; no CPU estimator passes even this limited gate on the corrected
  expressed-valence target.
- The candidate valence mappings compress multidimensional emotions to one
  scalar. Sarcasm, mixed affect, context, target, and uncertainty are not
  modeled; current adapters do not abstain.
- GPU model latency, parse-failure rate, hostile trust, and ADR-002 outcome
  remain pending. GPU parse failures are counted, not silently dropped.
- The selected mean ensemble runs sequentially on CPU. A local classifier
  dependency is optional and weights are not committed.
- Relationship sentiment is a W3-facing derived bounded view of trust and
  attachment, not a separately learned longitudinal sentiment estimate.
- A-6's regulation path only gets estimator metadata from this pipeline; the
  state-based legacy distress path is retained. DR-032 boundary checks were
  not changed and require their existing regression gate.
- DR-008's synchronous mood nudge and DR-010's layered emotion/mood ordering
  pull at different levels of description. This implementation keeps the
  DR-010 fast-to-slow state split and applies DR-008 damping/caps to the mood
  influence. W11's slow set-point can sit beneath mood; no baseline drift is
  added here.

## Cold critic, round 2 (Codex, final round per the critic-rounds cap)

A fresh Codex session reviewed revision 1 (the round-1 findings already
fixed) with its own scratch reproducers, plus a requested pytest run against
a copy of the tree with no `.git` (12 of 13 failures there were environment
artifacts of that missing history and the withheld ADR text, not code
defects; the full-suite run itself happened separately, in-tree, below).
Verdict: **FAIL**, 6 MED findings. Each is fixed here, with a test that
fails on the reviewed code; this was W2's last critic round.

| # | Defect (critic's reproducer) | Fix | Test (red on the reviewed code) |
|---|---|---|---|
| 1 | A raw event's caller-supplied metadata could set `affect_significant_event`/`affect_user_valence` directly; `perception.py` passed the whole metadata dict through unfiltered and `decision.py` trusted both keys unconditionally, regardless of `AFFECT_USER_INPUT_ENABLED`. Reproducer: with the flag off, a forged `{"affect_significant_event": true, "affect_user_valence": -0.9}` produced `REAPPRAISE`/`REDIRECT_ATTENTION`/`WAIT` regulation candidates from a neutral agent state. | `PerceptionService.perceive` now copies the raw metadata and strips both keys before they ever reach a `CognitiveEvent` -- they can only be set again by the pipeline's own validated estimator output (pipeline.py). | `test_forged_metadata_cannot_trigger_regulation_with_the_affect_flag_off`, `test_perceive_strips_pipeline_owned_affect_keys_from_a_raw_event` (`test_w2_critic_r2_findings.py`) |
| 2 | pipeline.py flagged significance at `abs(value) >= 0.8`; decision.py's acute-distress trigger and speaking-urgency reduction both required `value < -0.8` (strict). A user_valence of exactly -0.8 was flagged significant but triggered neither. | One shared `is_significant_valence`/`is_significant_negative_valence` pair (decision.py), both inclusive at 0.8, used by all three call sites (pipeline.py's flag, `_is_acute_distress`, `_build_communicative_intent`). | `test_boundary_value_counts_as_significant_negative`, `test_is_acute_distress_fires_at_exactly_the_boundary`, `test_communicative_intent_urgency_drops_at_exactly_the_boundary` |
| 3 | `relationship_sentiment` (trust - 0.5 + 0.1*attachment) inherits trust's pull toward `trust_baseline`, which moved by a flat 1% per `handle_system_tick` call regardless of elapsed time. 60 one-minute ticks (one simulated hour) fell from trust 0.9 to 0.718863, nowhere near DR-010's weeks-to-months timescale. | The pull is now `1 - exp(-TRUST_BASELINE_DRIFT_LAMBDA_PER_HOUR * dt_hours)`, scaled by elapsed time like the ALMA decay beside it. The 4-week-half-life constant is provisional pending W3's own trust/relationship-sentiment design (DR-015 scopes that design to W3); this round only fixes the tick-count-vs-elapsed-time bug. | `test_trust_baseline_drift_uses_elapsed_time_not_tick_count` (`test_state.py`) |
| 4 | `user_valence_reaches_mood` reported only a raw Cliff's delta, no significance test. One positive and one negative observation produced `cliffs_delta: 1.0` with nothing to say the sample couldn't support that number. | New `unpaired_cluster_delta` (stats.py): an independent-groups cluster bootstrap (persona seed as the sampling unit), refusing to claim significance with fewer than 2 clusters per group. `user_valence_reaches_mood` now reports `ci95`, `p_value`, `significant` alongside the effect size. | `test_user_valence_reaches_mood_flags_a_perfect_effect_size_as_not_significant_off_one_seed`, `test_user_valence_reaches_mood_can_report_significant_with_enough_seeds` |
| 5 | `TransformerEstimator.estimate` called the HF pipeline synchronously inline. `estimate` is a coroutine, but the blocking call inside it is not awaited, so it holds the single event-loop thread for its full duration: the caller's `asyncio.wait_for(timeout=...)` cannot fire until the call returns on its own (reproducer: a 0.03s timeout around a 0.205s blocking call never timed out). | The blocking call moves to a worker thread via `asyncio.to_thread`, so the loop stays free and the timeout can actually cancel the wait. | `test_transformer_estimator_blocking_inference_can_be_timed_out` |
| 6 | NF-16/NF-17 (A-5 elapsed-time decay, A-4 arousal/resource separation) shipped unconditional, with no `NEURO_*_ENABLED` flag or switchboard arm, though the fidelity table's header prescribes one for every row. NF-15's new mood-pull reducer had only direction-only test coverage, no exact numeric fixture. | NF-16/NF-17 stay unconditional: both are P0 defect fixes, not a design choice with a real "off" alternative to benchmark against, so gating them behind a default-off flag would ship the bug by default. Documented as an explicit, named exception to the fidelity table's own flag-per-row template (this ADR's "NF-16/NF-17 disposition", and a matching note in `research/neuro/fidelity-table.md`), not a silent deviation. NF-15 gets the numeric fixture. | `test_nf15_mood_pull_reducer_exact_numeric_fixture` (`test_state.py`); documentation-only for the NF-16/NF-17 half |

### Verification after round 2

| Check | Result |
|---|---|
| Every new/updated test above | red before its fix, green after |
| `test_user_valence.py`, `test_w2_critic_r2_findings.py`, `test_state.py`, `test_brainbench_affect_suite.py`, `test_affect_adr_contract.py`, `test_pipeline.py`, `test_appraisal_semantic_drift.py`, `test_brainbench_runner.py`, `test_global_control_selection.py`, `test_phasic_adrenaline.py` | 309 passed, 1 skipped |
| `test_doc_drift.py`, `test_brainbench_gates.py`, `test_brain_v3_coverage.py` (F-023/F-024 rows added) | pass |
| Full backend suite (`CI=1`) | see `codex-log.md` |
| Ruff check and format | clean |

No round 3 (critic-rounds cap already used at round 1). W2 merges into
`brain-v3` flag-off on this revision: no estimator reaches ADR-002's r>=0.8
bar (best: VADER+Cardiff r=0.7663, sign-agreement 0.8971), so
`AFFECT_USER_INPUT_ENABLED` stays default off and A-1 remains open.
