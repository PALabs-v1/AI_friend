# Slice 4 build: mood no longer fed its own value (NF-12), flag off

Written for Aniket. Status: built and merged behind `AFFECT_USER_INPUT_ENABLED` (default false); no model-backed run has happened, because the margins in `prereg/slice-04.md` are still PROPOSED (D-047). Nothing changes in production until the flag is turned on.

## What the flag does

With the flag on and `event.event_type == "USER_MESSAGE"`, `pipeline.py` passes `event_valence(event.raw_content)` as the appraisal's `emotional_bias` instead of the stored mood (`backend/app/cognitive/event_valence.py`: a small signed lexicon with negation, intensifiers and clause boundaries; stateless; no model). Other event types keep the mood bias. The Rust and Python appraisal functions are unchanged, so their parity tests are untouched. With the flag off the code path is the old one, and the slice 2a baseline reproduces exactly (`test_flag_off_matches_recorded_slice_02a_baseline`, which runs on a clean checkout).

## Tournament

Two builders worked in separate worktrees, then a blind critic compared them against a rubric fixed before it read either diff.

| Variant | Source of the bias | Outcome |
|---|---|---|
| A | The user valence inferred on the previous turn (`user_mental_model.inferred_valence`), lexical fallback when unset | Rejected. That field is written after the appraisal runs (`decision.py:760`, then `_apply_post_decision_tom`), so with a real model mood tracks the previous message: in the critic's repro (true valence written into state after each turn, as a model would) mood went 0.24, then 0.408 on "I hate this, you are useless", then 0.046 on a thanks message: both moves the wrong way. Every test of A ran on a null model where the stored value stays 0.0, so none caught it. A raised on int, list and bytes input |
| B (merged) | Signed valence of the current message only | Passed. Mutations (ignore the flag, flip the sign, pass the old mood, constant bias, zero scorer) each fail 3 to 9 tests. Returns 0.0 for None, int, list and bytes |

Critic round 1 corrections applied before merge: tick and other non-user events keep the mood bias (tested); a test that a stored user valence does not move the bias; "sick" no longer scores as a negative word on its own (slang), while "feel sick" and "sick of" do; the flag-off baseline test no longer skips; the `execute` docstring that called `MEMORY_TRUTH_ENABLED` and `AFFECT_CONTROL_ENABLED` false by default now says true (it matches `config.py`; found by the Codex review).

## Side effects with the flag on (read this before approving)

- **Trust moves with the user's words.** `update_from_appraisal` feeds goal congruence and relationship impact into `trust_benevolence` (+delta x RI) and `trust_competence` (+delta x 0.6 G). With the flag on those follow the text sign, not just mood. Slice 5 handles integrity separately; benevolence and competence are not covered by any slice 4 measure yet.
- **Acoustic emotion is not in the bias.** `apply_sensory_perception` already blends it into mood; adding it here would count it twice.
- **System 2 can still disagree.** With a live model, `apply_semantic_appraisal` also writes valence from the same text. The two paths combine; the combined effect is what the model-backed eval will measure.

## Known limits of the scorer

English only. Sarcasm scores as praise ("Oh great, another meeting"). Mixed cues cancel to near zero ("I love the idea but hate the execution" scores 0). Texts with no cue word score 0.0 ("I do not want to talk about it"). "Nothing is good anymore" scores positive. These are the cost of a model-free source; the model-backed eval decides whether they matter.

## Not done (needs your approval first)

The model-backed baseline of the current arm and its shuffled control, the winning arm's run, and the pass decision. All wait for the margins in `prereg/slice-04.md`. The clamp control has no declared downstream effect to test (mood does not feed `_compute_endocrine_options`).
