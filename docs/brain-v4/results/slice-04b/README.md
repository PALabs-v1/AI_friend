# Slice 4b, variant B: mood style line

Status: built behind `MOOD_STYLE_DRIVE_ENABLED` (default off). Gate tests D1 to D9 pass. The model-backed measure is built and unit tested with a fake client, and has not been run. Preregistration: `docs/brain-v4/prereg/slice-04b.md` (margins approved, D-064).

This is builder B's work in the 4b tournament. Variant A (the `top_p` term) is not in this branch.

## What was built

| Piece | Where |
|---|---|
| Flag `MOOD_STYLE_DRIVE_ENABLED`, default false | `backend/app/config.py` |
| One reply-length line in "Current Context" | `backend/app/cognitive/action.py`, `_build_realization_contract` and `_mood_style_line` |
| `valence` key on the proactive payload, only with the flag on | `backend/app/cognitive/core.py`, `generate_proactive_response` |
| Seven missing mood readers in the ledger, plus the new one | `backend/app/state/ledger.py`, `_MOOD_READERS` |
| Switchboard arm `+mood-style` (not part of the V3 `all` union) | `backend/evals/brainbench/switchboard.py` |
| Gate tests D1 to D9 | `backend/tests/test_mood_style_drive.py` |
| Model-backed measure harness and recording rig | `backend/evals/brainbench/mood_style.py` |
| Harness tests (fake client) | `backend/tests/test_mood_style_measure.py` |

The rule, as preregistered. With the flag on and a usable valence:

- valence <= -0.4 and `plan.goal` is not `COMFORT`: `- Reply length: keep it brief, one or two short sentences.`
- valence >= +0.4: `- Reply length: you have room to say more, two to four sentences.`
- otherwise nothing. A bool, string, None, NaN or infinite valence adds nothing. A finite number outside [-1, 1] is not clamped and lands in the nearest bucket.

The line is appended last to the contract. Nothing else in the prompt, the plan, the options or the state changes.

The seven readers added to the `mood` ledger row (the drift test now fails if any symbol is renamed): `cortisol_tonic` and `dopamine_tonic` (sampler), `get_behavioral_directive`, `get_emotion_label`, `_bucket_relational_stance`, `_prepended_affect_tag`, `_refresh_global_controls_locked` (candidate scoring), `derive_speech_expression` (voice). The row also lists `_mood_style_line`. If the tournament deletes B, remove that one entry.

## What the gate tests prove

D1 hashes are literals recorded from main (`ec79dfe8`) before any code changed, over 1372 endocrine inputs and 1440 contract inputs. Everything below is deterministic and needs no model.

| Test | Proves | Mutation that makes it fail |
|---|---|---|
| D1 | With the flag off, `_compute_endocrine_options` and `_build_realization_contract` match main byte for byte on the grids. With the flag on the sampler output is still main's, and the contract does change (the hash test is not vacuous). | Flag check replaced by `if True` (4 tests fail) |
| D2 | The line level (brief, none, more) is non-decreasing in valence over [-1, 1] at step 0.01, with exactly two changes at -0.4 and +0.4 inclusive. The sampler does not depend on valence. | Brief and more lines swapped (8 fail) |
| D3 | The only added text is one line of at most 120 characters, one of the two fixed strings, over the whole contract grid. | Second line appended (6 fail); 120+ character line (2 fail) |
| D4 | Through a real `AgentState`, the sampler options are identical on and off for moods -1 to 1, the premise table still holds (temperature 0.36 at -0.8, 0.84 at +0.8), and the line class matches `get_emotion_label` at 199 of 201 moods (the two exact-boundary moods are the known gap below). | Low threshold moved to -0.3 (3 fail) |
| D5 | None, NaN, +-inf, True, False, "0.5", "-0.8", "x", list, dict and absent valence give the flag-off contract for every goal, with and without a behavior decision. A positive control shows usable values do produce a line. | `bool` accepted (12 fail); `isfinite` removed (12 fail) |
| D6 | On 1000 seeded mean-reverting trajectories (step sd 0.05, 100 steps), the line moves only on turns where the real emotion-label class moves, so it changes at most as often. More than 500 label changes occur, so the check is not vacuous. | Threshold -0.3 (fails) |
| D7 | The slice 2a digests (`627dcce6...`, raw `cb62dc20...`) from `defects.collect()` are unchanged, in one process, with the flag off then on. | Not mutation-checked: B writes no state, so no plausible mutation reaches `collect()`. |
| D8 | Flag off, the proactive payload has exactly the old six keys. Flag on it gains only `valence` (default 0.0). The proactive prompt carries the line only with the flag on. | `valence` assignment dropped (2 fail) |
| D9 | On an offline copy, `clamp("mood", +0.8 / -0.8 / 0.0)` then a hydrated `StateService` snapshot then the real stage 7 payload builder then the real `ActionService.execute` with a recording client: options on equal options off and equal the unchanged mapping (predicted difference zero); the prompt differs only by the expected line, once; system prompt identical. The two clamps reach different temperatures, which is why the claim is a difference of differences. | Sampler perturbed when the flag is on (5 fail) |
| Also | COMFORT skip, retry keeps the line, ledger row lists the readers, flag default off, arm runnable and absent from `all`. | COMFORT skip removed (1 fails) |

Mutation checks were run by `mutate.py` (scratchpad, not committed) on D1, D2, D3, D5, D6, D8 and D9 with D7 deselected, restoring the file after each run. D9's recording client is `RecordingLLM` in `mood_style.py`, not `PinnedOptionsClient`.

## The measure harness

`evals/brainbench/mood_style.py` takes an injected `ReplyClient` and runs the 40-prompt design: flag off first, then flag on, each at clamp +0.8, -0.8 and a 0.0 placebo, 5 repeats. `analyze` computes, as preregistered:

- Cliff's delta of word counts, positive clamp against negative, per arm; `delta_on - delta_off`.
- Noise band per arm: largest gap of the per-repeat headline.
- Placebo (flag on, even against odd prompt halves at 0.0).
- 1000 label permutations, seed 20261030, swapping the on/off pair inside each (prompt, seed, repeat) block.
- Quality guard from the rejected share.
- The share of replies at 250 tokens, when the tier reports tokens.
- Verdict: `pass`, `fail`, or `void` ("not measurable at this tier") when either noise band is above 0.165.

`ActionServiceReplyClient` runs the real `ActionService.execute` and toggles the flag with the `+mood-style` arm. `select_prompts` applies the intent and turn filters to generated lifesim panels. No model was called. The 22 harness tests use a deterministic fake and cover: a real lift passes, no lift fails, a lift shared by both arms (the old mood paths) fails, a reversed lift fails, drift between repeats voids, a placebo skew fails, more rejections with the flag on fail the guard, flag-on-first order fails, permutations are deterministic and paired.

## What is not proven

1. **No output effect.** Nothing here shows that the line changes reply length. D1 to D9 show the line reaches the prompt and nothing else moves. Only the model-backed run can pass or fail the claim, and it has not run.
2. **Prompts are not frozen.** `select_prompts` needs a generated lifesim dev panel (seeds 1000 to 1004). The 40 prompt texts are not committed. They must be generated, read, and frozen before the first run.
3. **Boundary mismatch.** B is inclusive (`<= -0.4`, `>= +0.4`) as preregistered. `get_emotion_label` is strict (`< -0.4`, `> 0.4`). At exactly +-0.4 the line fires while the label stays neutral. D4 skips those two points and D6 cannot hit them. Measure zero in practice, but the prereg sentence "flips only when the label class flips" is false at the boundary.
4. **Proactive side effects with the flag on.** The new `valence` key also feeds `_surface_fallback_memories` (`current_valence` in the recall call), so a proactive turn with no surfaced memories can rank recall differently. It does not reach `_prepended_affect_tag`, because the proactive payload has no `arousal` and the default 0.5 never triggers a tag. The proactive instruction says "1-3 sentences max" while the high-mood line says two to four sentences; the model gets both.
5. **Placebo rule as written compares different prompts.** The even half and the odd half are different prompts, so their length difference is prompt composition, not noise, and the noise band (within-prompt repeat variation) is usually smaller. The rule `|delta| < noise band` will likely fail on real data even when the line does nothing. The harness implements it as written and also reports `placebo_repeat_split_on` (same prompts, odd against even repeats at 0.0), which is not gating. Aniket should decide whether to swap the gating placebo.
6. **Quality guard band.** The prereg says "not above flag off by more than the noise band" without naming which band. The harness uses the largest per-repeat gap of the rejected share in either arm. If no reply is ever rejected the band is 0, and a single flag-on rejection fails the guard.
7. **Permutation is one-sided.** The observed effect must exceed the 99th percentile of the signed null (positive direction is declared). A two-sided cut would be looser.
8. **Noise floor.** With 40 prompts against 40, Cliff's delta moves by chance between repeats. The 0.165 void rule may fire on a real run even with a true effect. The fake client uses a shared prompt effect plus small jitter to stay under it; real replies may not.
9. **Token cap.** `ActionServiceReplyClient` returns `tokens=None`, so the "share of replies at 250 tokens" is reported as `None` until the real client supplies a count.
10. **No full-pipeline test.** D8 and D9 build payloads from the snapshot with the real stage 7 function, but no test drives `CognitivePipeline.execute` end to end with the flag on.
11. **D7 is slow.** It runs `defects.collect()` twice, about 7 seconds, over the 2 second gate budget. The sibling slice 2a tests have the same cost.
12. **Variant A.** Not built here. The A parts of D2, D3, D4 and D9 are absent.
13. **Voice and the local 4B to 9B tier** are out of scope (`Not claimed` in the prereg).

## Run it

From `backend/`, with the main checkout's interpreter:

```
/path/to/main/checkout/.venv/bin/python -m pytest tests/test_mood_style_drive.py tests/test_mood_style_measure.py tests/test_ledger_registry.py tests/test_brainbench_switchboard.py -q
```

Nothing needs restarting: the flag defaults off and no running service reads it until it is set.
