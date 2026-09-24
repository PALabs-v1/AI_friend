# ADR-002 — Appraisal-weight learning off by default; appraisal from user evidence proposed

**Status:** Part 1 accepted and shipped. Part 2 proposed, pending the GPU/LLM
estimator evaluation. **Date:** 2026-09-24. **Evidence:** `../04-affect-dynamics.md`.

## Problem

* **A-1.** Appraisal's goal congruence is the agent's own mood
  (`G = clamp(mood)`, `RI = 0.5·mood`). The user's words reach affect only
  through novelty and boundary-word checks, so in the simulation valence
  never moves for any script.
* **A-2.** `ReappraisalEngine` adapts `w1, w2` (appraisal→valence weights)
  from a prediction error computed on that same mood-derived signal. The
  per-turn loop gain `0.7 + 0.3·(w1 + 0.5·w2)` exceeds 1 inside the allowed
  clamp. Measured: from mood 0.6, valence saturates at +1.0 for 143 of 200
  turns. COMFORT turns drive `w1` to its floor (0.1), and the learned weights
  are persisted across restarts.

## Alternatives

| Option | Result |
|---|---|
| Keep learning on, tighten the clamp so the gain stays < 1 | Removes runaway, keeps the numbing drift, and keeps learning from a signal that is the agent's own state |
| Keep learning on, but learn from user evidence | Unmeasurable here (needs a real estimator); the oracle run still shows saturation (62 turns) and numbing (`w1`=0.1) — the rule itself drifts |
| **Turn weight learning off; keep prediction-error hormone bursts** | Bounded, recovers to baseline, no persisted drift; no measured downside because no benefit of learning was ever shown |
| Delete `ReappraisalEngine` | Loses the reward-prediction-error dopamine/cortisol signal that other code reads |

## Decision

1. `REAPPRAISAL_WEIGHT_LEARNING_ENABLED = False` (new config). The prediction
   error is still computed and still drives `pipeline._apply_reward_prediction_error`.
   `hydrate()` ignores previously persisted weights while learning is off, so
   deployments carrying a drifted `w1`/`w2` return to the authored defaults.
2. Proposed, not shipped: set `G` (and `RI`) from the user's expressed
   valence. The oracle run shows the mechanism works: mood tracks the user
   (r up to 0.98), stays bounded, and trust separates hostile from positive
   users. The obvious production estimator is the fast-LLM ToM
   `inferred_valence` that already runs every turn. Its accuracy is unknown,
   and it runs at stage 6, after the stage-5 affect update. Adopt only if
   `experiments/gpu/tom_valence_affect.py` meets the pre-registered rule
   (r ≥ 0.8, sign agreement ≥ 0.9, hostile-script trust ≤ 0.5). VADER was
   measured and is not good enough.

## Trade-offs

* With learning off and appraisal unchanged, V1's affect stays mostly static
  under conversation (A-1 remains). This change removes a harmful failure
  mode; it does not make affect responsive. Part 2 does.
* Deployments that relied on learned weights lose them. No evidence shows
  they helped.

## Reversal conditions

* A learning rule is proposed whose simulated loop gain stays < 1 for every
  reachable weight, with no persisted drift under the `negative`/`hostile`
  scripts, and a measured benefit on a benchmark. Then enable it behind the flag.
* Rollback: `REAPPRAISAL_WEIGHT_LEARNING_ENABLED=true`, restart the brain agent.
