# Preregistration: slice 4 (fix the mood self-feed, NF-12)

**Status: PROPOSED. Not approved. No model-backed run happens until Aniket approves or changes the numbers below (D-047).** Written for Aniket. Committed before any slice 4 code or run.

## Claim under test

Today, on the heuristic path, the user's text does not move mood: the appraisal returns the stored mood as goal congruence, so mood only decays (`results/slice-02a/README.md`, scoped to System 1, text-only input). After the fix, user input moves mood in the direction of the user's valence, and the previous mood alone does not.

## Arms and controls

| Arm | What it is |
|---|---|
| current | main as of `a40f9edc`, flag off |
| variant A | congruence from the semantic (System 2) appraisal output with a lexical fallback |
| variant B | congruence from a signed event valence computed outside the mood path |
| shuffled control | each arm run on the same lifesim turns with user text shuffled across turns (seeded) |
| clamp control | mood clamped to +0.8 and to -0.8 on an offline copy (slice 1 operators), same turns |

The flag is the Config field `AFFECT_USER_INPUT_ENABLED` (the name BrainBench's switchboard already reserves for W2), default false. The winning variant is chosen by the cold critic; the loser is deleted, not kept behind another flag.

## Measures and proposed pass rules

The headline measure is Cliff's delta between mood change on positive-oracle turns and on negative-oracle turns, `user_valence_reaches_mood` in `backend/evals/brainbench/affect_suite.py`, on a fixed seed set, frontier tier through local Claude Code. Delta runs from -1 to 1; 0.147 and 0.33 are the usual small and medium cutoffs.

1. **Baseline first.** The current arm and its shuffled control are run and recorded before any code change. Noise band per D-048 (5 repeats, largest pairwise gap).
2. **Pass (proposed):** the winning arm's delta minus its own shuffled-control delta is at least **0.33**, and that gap exceeds twice the larger arm's noise band. If the noise band alone exceeds 0.165, the pass rule is void and the result is reported as "not measurable at this tier", not as a pass.
3. **Clamp (finding, not a pass rule):** the sampler mapping in `_compute_endocrine_options` in `backend/app/cognitive/action.py`, reads cortisol, dopamine and fatigue, not mood. A mood clamp therefore has no declared effect through that function today (other style paths were not checked for this proposal). The clamp control is run and its downstream effect on the measures above is reported; whether mood should drive any sampler or style term is an architecture decision for Aniket and is not made here.
4. **No-regression (proposed):** the System 1 no-model baseline (`results/slice-02a`) digest changes only in the mood section; integrity, behavioral and static sections are byte-identical.
5. **Parity:** the Rust and Python appraisal paths agree on every input in the existing parity tests plus the new cases.

## Not claimed

No claim about the local 4B-9B tier (no GPU, D-036). No claim about felt realism. A pass on the frontier tier is a test-tier result (D-003, D-024).

## Open for Aniket

The 0.33 gap, the 2x-noise condition, the seed set (proposed: 5 persona seeds, listed in this file before the first run; `v2_gate_bands.json` does not carry a seed list to reuse), and whether mood should drive any sampler term (item 3).
