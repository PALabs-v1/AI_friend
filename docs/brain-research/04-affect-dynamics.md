# 04 — Affect dynamics: experiments and decision

**Outcome:** in V1 the user's words never move the agent's valence, trust rises
even under sustained hostility, and the appraisal-weight learning rule can
drive valence into permanent saturation or permanent numbness. Weight learning
is now off by default (shipped, ADR-002). Feeding appraisal from the user's
expressed valence is validated as a mechanism but not shipped, pending an
estimator evaluation that needs a real LLM (GPU package).

## Method

`backend/evals/cognitive/affect_sim.py` replays scripted conversations through
the production objects, in production order: `AppraisalEngine.appraise`
(stage 4) → `ReappraisalEngine.evaluate_outcome` and
`StateService.update_from_appraisal` (stage 5) →
`ReappraisalEngine.record_expected_outcome` (stage 6/8) → `handle_system_tick`
every simulated minute, then 24 hours of idle ticks. Substitutions: no
persistence, the 2 s reappraisal rate limit reset per simulated turn (turns
are 30 s apart), goal = COMFORT for negative user turns else ENGAGE (instead
of the LLM classifier + MAUT). 21 hand-labelled messages; 7 scripts
(positive, negative, neutral, alternating, venting→recovery, hostile,
rupture→repair); 200 turns; seeds 0–2; default persona (baseline valence 0).

The variable under test is **what feeds appraisal's goal congruence G**:

* `agent_mood` — V1: `G = clamp(state.mood)`, `RI = 0.5·mood` (`pipeline.py`, `appraisal.py`);
* `oracle` — the hand label of the user's message (upper bound; isolates the mechanism from estimator quality);
* `vader` — VADER compound score, a deterministic lexicon estimator (comparison only).

## Results — learning off (the shipped default), starting at baseline

| appraisal input | script | r(user valence, mood) | final mood | max abs mood | turns saturated | final trust | w1 | loop gain |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| agent_mood | positive | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | negative | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | neutral | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | alternating | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | venting_then_recovery | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | hostile | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| agent_mood | rupture_repair | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| oracle | positive | +0.49 | +0.58 | 0.69 | 0 | 1.00 | 0.60 | 0.940 |
| oracle | negative | +0.36 | -0.57 | 0.63 | 0 | 0.38 | 0.60 | 0.940 |
| oracle | neutral | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| oracle | alternating | +0.97 | -0.08 | 0.20 | 0 | 0.87 | 0.60 | 0.940 |
| oracle | venting_then_recovery | +0.98 | +0.58 | 0.68 | 0 | 1.00 | 0.60 | 0.940 |
| oracle | hostile | +0.51 | -0.57 | 0.69 | 0 | 0.34 | 0.60 | 0.940 |
| oracle | rupture_repair | +0.96 | +0.58 | 0.69 | 0 | 1.00 | 0.60 | 0.940 |
| vader | positive | -0.06 | +0.51 | 0.58 | 0 | 1.00 | 0.60 | 0.940 |
| vader | negative | +0.41 | -0.35 | 0.40 | 0 | 0.67 | 0.60 | 0.940 |
| vader | neutral | — | +0.00 | 0.00 | 0 | 0.83 | 0.60 | 0.940 |
| vader | alternating | +0.90 | +0.02 | 0.25 | 0 | 0.99 | 0.60 | 0.940 |
| vader | venting_then_recovery | +0.97 | +0.51 | 0.57 | 0 | 1.00 | 0.60 | 0.940 |
| vader | hostile | +0.31 | -0.09 | 0.56 | 0 | 0.68 | 0.60 | 0.940 |
| vader | rupture_repair | +0.93 | +0.51 | 0.57 | 0 | 1.00 | 0.60 | 0.940 |

* **V1 (`agent_mood`)**: mood is exactly constant for every script, so the
  correlation with the user is undefined. Trust ends at 0.83 for *every*
  script, hostile included.
* **User evidence (`oracle`)**: mood tracks the user (r up to 0.98 on
  venting→recovery and rupture→repair), stays bounded (max |mood| 0.69),
  and trust separates hostile (0.34) from positive (1.00).
* **VADER** shows the same shape with a worse estimator: hostile trust ends
  at 0.68, and it mislabels plain phrasings ("Not bad, pretty good day
  actually" scores −0.29).

## Results — learning ON (V1's always-on rule), starting mood 0.6

| appraisal input | script | r(user valence, mood) | final mood | max abs mood | turns saturated | final trust | w1 | loop gain |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| agent_mood | positive | -0.02 | +1.00 | 1.00 | 143 | 1.00 | 0.90 | 1.105 |
| agent_mood | negative | -0.02 | +0.00 | 0.56 | 0 | 0.90 | 0.10 | 0.749 |
| agent_mood | neutral | — | +1.00 | 1.00 | 143 | 1.00 | 0.90 | 1.105 |
| agent_mood | alternating | +0.02 | +0.00 | 0.56 | 0 | 0.92 | 0.41 | 0.869 |
| agent_mood | venting_then_recovery | +0.02 | +1.00 | 1.00 | 143 | 1.00 | 0.90 | 1.105 |
| agent_mood | hostile | -0.03 | +0.00 | 0.56 | 0 | 0.90 | 0.10 | 0.749 |
| agent_mood | rupture_repair | -0.28 | +1.00 | 1.00 | 143 | 1.00 | 0.90 | 1.105 |
| oracle | positive | +0.41 | +0.96 | 1.00 | 62 | 1.00 | 0.90 | 1.105 |
| oracle | negative | +0.03 | -0.11 | 0.38 | 0 | 0.38 | 0.10 | 0.745 |
| oracle | neutral | — | +0.00 | 0.42 | 0 | 0.83 | 0.60 | 0.940 |
| oracle | alternating | +0.61 | -0.01 | 0.60 | 0 | 0.87 | 0.10 | 0.745 |
| oracle | venting_then_recovery | +0.84 | +0.90 | 0.98 | 1 | 1.00 | 0.90 | 1.078 |
| oracle | hostile | +0.07 | -0.11 | 0.39 | 0 | 0.34 | 0.10 | 0.745 |
| oracle | rupture_repair | +0.89 | +0.96 | 1.00 | 22 | 1.00 | 0.90 | 1.105 |
| vader | positive | -0.04 | +0.86 | 0.97 | 0 | 1.00 | 0.90 | 1.105 |
| vader | negative | +0.03 | -0.06 | 0.30 | 0 | 0.67 | 0.10 | 0.745 |
| vader | neutral | — | +0.00 | 0.42 | 0 | 0.83 | 0.60 | 0.940 |
| vader | alternating | +0.74 | +0.01 | 0.57 | 0 | 0.99 | 0.30 | 0.829 |
| vader | venting_then_recovery | +0.82 | +0.77 | 0.85 | 0 | 1.00 | 0.90 | 1.065 |
| vader | hostile | +0.18 | -0.02 | 0.34 | 0 | 0.68 | 0.10 | 0.745 |
| vader | rupture_repair | +0.88 | +0.86 | 0.96 | 0 | 1.00 | 0.90 | 1.105 |

Loop gain per turn is `0.7 + 0.3·(w1 + 0.5·w2)`. The clamp `[0.1, 0.9]` allows
gain up to 1.105. From mood 0.6, positive/neutral/venting/rupture scripts
drive `w1` to 0.9 and valence saturates at +1.0 for 143 of 200 turns. It is
still 0.30 from baseline after 24 idle hours. Every COMFORT turn pushes `w1`
toward 0.1 (actual outcome below the 0.3 expectation), and the weights are
persisted, so one sad conversation numbs the agent across restarts.

The same starting state with learning **off**:

| appraisal input | script | r(user valence, mood) | final mood | max abs mood | turns saturated | final trust | w1 | loop gain |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| agent_mood | positive | +0.04 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | negative | -0.02 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | neutral | — | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | alternating | +0.02 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | venting_then_recovery | -0.03 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | hostile | -0.03 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| agent_mood | rupture_repair | +0.21 | +0.00 | 0.56 | 0 | 0.90 | 0.60 | 0.940 |
| oracle | positive | +0.72 | +0.58 | 0.69 | 0 | 1.00 | 0.60 | 0.940 |
| oracle | negative | +0.17 | -0.57 | 0.63 | 0 | 0.38 | 0.60 | 0.940 |
| oracle | neutral | — | +0.00 | 0.42 | 0 | 0.83 | 0.60 | 0.940 |
| oracle | alternating | +0.89 | -0.08 | 0.60 | 0 | 0.87 | 0.60 | 0.940 |
| oracle | venting_then_recovery | +0.97 | +0.58 | 0.68 | 0 | 1.00 | 0.60 | 0.940 |
| oracle | hostile | +0.30 | -0.57 | 0.69 | 0 | 0.34 | 0.60 | 0.940 |
| oracle | rupture_repair | +0.96 | +0.58 | 0.69 | 0 | 1.00 | 0.60 | 0.940 |
| vader | positive | +0.00 | +0.51 | 0.58 | 0 | 1.00 | 0.60 | 0.940 |
| vader | negative | +0.22 | -0.35 | 0.40 | 0 | 0.67 | 0.60 | 0.940 |
| vader | neutral | — | +0.00 | 0.42 | 0 | 0.83 | 0.60 | 0.940 |
| vader | alternating | +0.83 | +0.02 | 0.57 | 0 | 0.99 | 0.60 | 0.940 |
| vader | venting_then_recovery | +0.96 | +0.51 | 0.57 | 0 | 1.00 | 0.60 | 0.940 |
| vader | hostile | +0.28 | -0.09 | 0.56 | 0 | 0.68 | 0.60 | 0.940 |
| vader | rupture_repair | +0.94 | +0.51 | 0.58 | 0 | 1.00 | 0.60 | 0.940 |

## Decision

* **Shipped** (ADR-002): `REAPPRAISAL_WEIGHT_LEARNING_ENABLED=False`. Prediction
  errors still fire the dopamine/cortisol bursts; only the `w1, w2` update and
  hydration of previously learned weights are gated. Gate test:
  `test_gate_affect_weight_learning_runaway_is_off_by_default`.
* **Proposed** (ADR-002 §Next): set G/RI from the user's expressed valence.
  The fast-LLM Theory-of-Mind classifier already produces `inferred_valence`
  on every turn, but its accuracy is unmeasured and it runs after stage 5.
  `backend/experiments/gpu/tom_valence_affect.py` measures it against the
  labels and replays this matrix on it. Pre-registered adoption rule: r ≥ 0.8,
  sign agreement ≥ 0.9 on non-neutral messages, hostile-script trust ≤ 0.5.
* **Proposed**: route trust through `PersonModel`'s asymmetric evidence update
  (already implemented, never called) instead of `+0.1·NA` per turn (07 §4).

## Reproduce

```bash
cd backend
PYTHONPATH=. python -m evals.cognitive affect --turns 200 --out /tmp/affect.json   # ~1 min
PYTHONPATH=. python -m evals.cognitive.report affect /tmp/affect.json False None
PYTHONPATH=. python -m evals.cognitive.report affect /tmp/affect.json True 0.6
```

## Limits

Scripted labels, a fixed goal rule, no System-2 LLM appraisal, no sensory or
facial-reflex inputs, one persona. The simulation isolates the per-turn
update path; it does not claim the lived dynamics of a full deployment.
