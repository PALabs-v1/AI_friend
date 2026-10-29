# Preregistration: slice 2a (baseline harness and no-LLM defect baselines)

Written before the first run of the harness (file times in the working tree show it first; the commit holds both). Written for Aniket and for whoever fills the slice 2b table.

## What is measured in 2a

Five defect baselines from `phases/02-architecture-draft.md` section 9 that need no model, plus the bare-model gate arm (D-021) as code with a gate test. No model-backed arm is run in 2a. The mood and integrity baselines drive a real `CognitiveService` built by the BrainBench `architecture_only` adapter (null model); the others read the source.

These baselines are descriptive records. They carry no pass or fail target: the slices that fix each defect (4, 5 and later) set theirs, preregistered in their own files, and compare against these results.

| Baseline | What it records |
|---|---|
| mood self-feed (System 1, text-only) | mood trajectory under real and shuffled user-input orders, from two start moods; per-turn ratio of mood to previous mood; goal congruence the appraisal returns for every scripted text |
| integrity on hostile text (System 1, text-only) | norm alignment and each trust dimension over hostile, neutral and supportive matched inputs, then a hostile-to-supportive recovery run |
| constant behavioral signal | the literal passed as `behavioral_signal` at each `evaluate_outcome` call in `pipeline.py`, the weights in `reappraisal.py`, and the share of outcome variance the behavioral term supplies over a fixed grid |
| baseline-PAD writers | every assignment to `baseline_valence`, `baseline_arousal`, `baseline_dominance` or `personality_baseline_affect` in `backend/app`, as `file::function` |
| subscriber counts | publishers and subscribers of `state.subconscious` and `telemetry.reflection` in Python, and Rust references |

## Instrument check (the measure that can fail)

Two runs of the frozen baselines, each in its own Python process, must produce byte-identical canonical JSON (sorted keys, floats rounded to 9 decimals). The harness stores the SHA-256 of that JSON. If two runs differ at all, the harness is not a measuring instrument, no baseline from it is used, and slice 2a is not done.

Noise band for these baselines: **zero**. They are deterministic functions of frozen inputs (no model, no network, no wall clock: a `ManualClock` drives time, the text appraisal runs the same code path every time). A nonzero band would hide a real nondeterminism, so none is allowed.

Conditions that make a digest comparison valid:

- same `appraisal_backend` (the harness records whether `cognitive_rust` or the Python mirror ran; a digest from one backend is never compared with the other);
- same `scenario_sha256` (the frozen scenario texts, orders and seeds; editing a text changes it and starts a new baseline);
- same Python minor version, recorded in the output;
- the same checkout layout (the output names `file::function` sites).

The harness points the embedding call that memory writes make at a closed local port, so a running Ollama server on the measuring machine cannot change a result.

## Frozen inputs

All texts, seeds (`20261029`), start moods (`0.0`, `0.5`), turn counts and the persona default (`PersonaProfile()`) live in `backend/evals/brainbench/baseline/defects.py` as constants. They are not changed after the first run. A change is a new preregistration.

## Not set here (open, owner decision)

The noise band for **model-backed** arms (bare model, V3, current brain) cannot be zero because sampling is stochastic, and no number is chosen here. Proposed procedure for slice 2b, for Aniket to accept or change before the first model run: run each frozen arm 5 times per tier at the lowest temperature the client allows; the band is the largest pairwise difference of the headline measure across those runs, written to `prereg/slice-02b.md` before any comparison; two arms are called different only if their gap exceeds the larger of the two arms' bands. GPU tiers wait for the hand-back (D-036).

## Source of the baselines

Baselines are collected at a recorded git revision and stored under `docs/brain-v4/results/slice-02a/`. A later slice that fixes a defect repeats the same arm and reports against these files, not against `sota_comparisons.md` or any document with unreconciled numbers (`ROADMAP_RECONCILIATION.md`, blocking issue 1).

## Revisions after the first run (none changes a threshold)

- 2026-10-29, after cold critic round 1: the mood and integrity baselines were moved from a reduced rig (`StateService` plus `AppraisalEngine`) to the real `CognitiveService` through the `architecture_only` adapter, because the critic showed the rig's numbers (mood factor 0.94, integrity start 0.5) differed from the production persona (0.944, 0.38). All results were recollected; the first-draft digest `fec9167a...` is discarded. The noise band stayed zero, the instrument check stayed exact equality, and no reading was chosen after seeing it: the readings in the result README follow from the recollected data and are scoped to System 1, text-only input. Also added: the closed-port embedding note, the checkout-layout validity condition, and the statement that these baselines carry no pass or fail target.
