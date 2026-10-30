# Preregistration: slice 5 (fix the integrity update, NF-23)

**Status: PROPOSED. Not approved. No model-backed run happens until Aniket approves or changes the numbers below (D-047).** Written for Aniket. Committed before any slice 5 code or run.

## Claim under test

Today hostile, neutral and supportive input produce identical trust rows, with integrity rising 0.1 per turn from 0.5 to 1.0 in 5 turns, because norm alignment is 1.0 whenever no boundary keyword matches and the update adds `delta * NA` (`results/slice-02a/README.md`, System 1, text-only). After the fix, hostile input does not raise integrity, and support and rupture-repair move trust in the right direction.

## Arms

current (flag off), variant A (neutral norm term when there is no evidence), variant B (signed norm term fed by the semantic appraisal; the System 2 reading only drifts mood today and never reaches trust, so B adds that path). Flag: `TRUST_EVIDENCE_MODEL_ENABLED` (the name the BrainBench switchboard reserves for W3), default false. The cold critic picks one; the other is deleted.

## Measures and proposed pass rules

Matched hostile, neutral and supportive inputs from slice 2a (`defects.py` constants), same start state, 10 turns each, plus the 5 hostile then 5 supportive recovery run. Then `backend/evals/brainbench/trust_suite.py` on the frontier tier for the semantic variant.

1. **Baseline** is the slice 2a recorded data (no new run needed for the System 1 path).
2. **Hostile (proposed):** integrity after 10 hostile turns is at most integrity before (change at most 0.0).
3. **Support (proposed):** integrity after 10 supportive turns is at least 0.05 above the start, and strictly above the hostile run at every turn from turn 2.
4. **Rupture and repair (proposed):** in the 5 hostile then 5 supportive run, integrity at turn 5 is below the all-supportive run at turn 5, and rises on each of turns 6 to 10.
5. **Neutral (proposed):** neutral input moves integrity by no more than 0.02 over 10 turns.
6. **Trust suite (model-backed):** reported with the D-048 noise band; no numeric pass margin is proposed until slice 4's run shows the frontier tier's noise.
7. **No-regression:** the slice 2a digest changes only in the integrity section.

Rules 2 to 5 are deterministic and use no model. They are proposals; the 0.05 and 0.02 are my judgment, not derived from data, and Aniket may replace them.

## Not claimed

No claim that the semantic variant is better in conversation, only that it passes the matched-input rules. No local-tier claim.

## Revisions before any build

- The first draft said integrity rose 0.17 per turn. That figure came from the discarded slice 2a collection that read the owner's gitignored persona file. The corrected default-persona step is 0.1 (`results/slice-02a/README.md`). Rule text is unchanged.
- Deterministic rules 2 to 5 cannot be run on variant B with the null model, because B moves integrity only on a model reading. B's tests use a scripted fake reading per input text. That checks the plumbing and the neutral band, not any real model. The real-model check is the trust suite (rule 6), after approval.
