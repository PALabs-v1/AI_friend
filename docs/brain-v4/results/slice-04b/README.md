# Slice 4b: mood reply-length style line

Status: built behind `MOOD_STYLE_DRIVE_ENABLED` (default off). Gate tests D1 to D9 pass. The model-backed measure is built and unit tested with a fake client, and has not been run. Preregistration: `docs/brain-v4/prereg/slice-04b.md` (margins approved, D-064).

The sampler variant of 4b (a `top_p` term) is not in this branch.

## Owner decisions that differ from the preregistration (2026-10-30)

| Prereg text | What this build does | Why |
|---|---|---|
| D-064: "the proactive payload gains a `valence` key under the flag" | The proactive payload is left exactly as on main. `backend/app/cognitive/core.py` has no diff. | The style line is silent on proactive turns. The proactive instruction already asks for one to three sentences. A `valence` key would also feed `_surface_fallback_memories` (`current_valence` in the recall call), so recall on proactive turns would change with the flag on. Owner choice. |
| Pass rule 6: quality guard against "the noise band" | Fixed cap: fail if the flag-on rejected-reply share exceeds the flag-off share by more than 0.03 absolute, pooled over all repeats and prompts of the +0.8 and -0.8 cells. | The prereg did not name which band. Owner choice. |
| Pass rule 4: placebo is the even prompt half against the odd half | The gating placebo is flag on, clamp 0.0, the same prompts, odd repeats against even repeats (`placebo_repeat_split_on`). The prereg placebo is reported as `placebo_prompt_halves_on` and does not gate. | Two different prompt sets differ in length for reasons that are not noise. Accepted as a PROPOSED correction to the prereg; the prereg file itself was not edited. `analyze()` returns `placebo_basis`, so a pass is visibly conditional on the corrected placebo. |

If a `valence` key is ever added to the proactive payload, revisit INITIATE turns in `_mood_style_line` (a high-mood proactive turn would get "two to four sentences" against the instruction's "1-3") and the recall coupling at `action.py` `_surface_fallback_memories`. The rule has no INITIATE guard because the case cannot occur in production today.

## What was built

| Piece | Where |
|---|---|
| Flag `MOOD_STYLE_DRIVE_ENABLED`, default false | `backend/app/config.py` |
| One reply-length line in "Current Context" | `backend/app/cognitive/action.py`, `_build_realization_contract` and `_mood_style_line` |
| Mood readers in the ledger | `backend/app/state/ledger.py`, `_MOOD_READERS` |
| Switchboard arm `+mood-style` (not part of the V3 `all` union) | `backend/evals/brainbench/switchboard.py` |
| Gate tests D1 to D9 | `backend/tests/test_mood_style_drive.py` |
| Model-backed measure harness and recording rig | `backend/evals/brainbench/mood_style.py` |
| Harness tests (fake client) | `backend/tests/test_mood_style_measure.py` |
| Seeded noise check of the harness verdicts | `backend/evals/brainbench/mood_style_noise_check.py` |

The rule. With the flag on and a usable valence:

- valence <= -0.4 and goal not `COMFORT`: `- Reply length: keep it brief, one or two short sentences.`
- valence >= +0.4: `- Reply length: you have room to say more, two to four sentences.`
- otherwise nothing.
- A bool, string, None, NaN or infinite valence adds nothing. Any other real number is usable: python or numpy ints and floats, and `Fraction`. `Decimal` is not a `numbers.Real` and is inert. A finite number outside [-1, 1] is not clamped and lands in the nearest bucket. An integral value is compared as an int and never converted, so `10**400` resolves to the high bucket (`math.isfinite` on such an int raises `OverflowError`). A rational too large for a float is compared exactly.

The line is appended last to the contract. Nothing else in the prompt, the plan, the options or the state changes.

Ledger: the `mood` row had one reader, `emotional_bias`. It now lists seven further paths found by grep, as nine symbols: sampler (`cortisol_tonic`, `dopamine_tonic`), directive (`get_behavioral_directive`), label (`get_emotion_label`), stance (`_bucket_relational_stance`), breath tag (`_prepended_affect_tag`), candidate scoring (`_refresh_global_controls_locked`), voice (`derive_speech_expression` and `brain_agent._derive_expression_wire`). A tenth entry, `_mood_style_line`, is this slice's own reader.

## Boundary behavior at exactly +-0.4

The preregistered rule is inclusive (`<= -0.4`, `>= +0.4`) and `get_emotion_label` is strict (`< -0.4`, `> 0.4`). At exactly -0.4 and +0.4 the line is present while the label is still "neutral". So the line changes with the label class everywhere except those two points. Tests pin the actual behavior with the next float on either side.

## What the gate tests prove

D1 hashes are literals recorded from main (`ec79dfe8`) before any code changed, over 1372 endocrine inputs and 1440 contract inputs. Everything below is deterministic and needs no model.

| Test | Proves |
|---|---|
| D1 | With the flag off, `_compute_endocrine_options` and `_build_realization_contract` match main byte for byte on the grids. With the flag on the sampler output is still main's, and the contract does change. |
| D2 | The line level (brief, none, more) is non-decreasing in valence over [-1, 1] at step 0.01, with exactly two changes at -0.4 and +0.4 inclusive. The sampler does not depend on valence. |
| D3 | The only added text is one line of at most 120 characters, one of two fixed strings, over the whole contract grid. |
| D4 | Through a real `AgentState`, sampler options are identical on and off for moods -1 to 1 and the premise table holds. The line class matches `get_emotion_label` at 199 of 201 moods. The boundary test pins the other two. |
| D5 | None, NaN, +-inf, True, False, "0.5", "-0.8", "x", list, dict, object and absent valence give the flag-off contract for every goal, with and without a behavior decision. A positive control shows usable values do produce a line. |
| D6 | On 1000 seeded mean-reverting trajectories (step sd 0.05, 100 steps) the line moves only on turns where the label class moves. A second test pins the exact-boundary step: the line moves one step before the label does. |
| D7 | The slice 2a digests (`627dcce6...`, raw `cb62dc20...`) from `defects.collect()` are unchanged, in one process, flag off then on. |
| D8 | With the flag on, the proactive payload equals the flag-off payload exactly (same dict, same canonical JSON, the old six keys, no `valence`) at moods -0.8, 0 and +0.8. Through the real proactive path with an empty surfaced list, the model prompt, system text and options, and the recall call arguments (`current_valence` stays 0.0), are identical on and off, and neither style line appears. |
| D9 | On an offline copy, `clamp("mood", +0.8 / -0.8 / 0.0)`, hydrated `StateService` snapshot, the real stage 7 payload builder, the real `ActionService.execute` with a recording client: options on equal options off and equal the unchanged mapping; the prompt differs only by the expected line, once; system prompt identical. The two clamps reach different temperatures, which is why the claim is a difference of differences. |
| Number types | numpy float32/float64/int32/int64 give the same bucket as the python value; numpy NaN, inf and bool stay inert; `Fraction` follows the rules; `Decimal` is inert. |
| Huge ints | `_mood_style_line(+-10**400, ...)` does not raise and returns the nearest bucket. |
| Also | COMFORT skip, retry keeps the line, ledger row lists the readers, flag default off, arm runnable and absent from `all`. |

### Mutation checks

Each mutation was applied to the code, the affected test file run (D7 deselected), then the file restored.

| Mutation | Tests failing |
|---|---|
| Flag ignored (`if True`) | 4 of 42 |
| Lines swapped | 12 of 42 |
| Second line appended / 120+ character line | 11 / 2 of 42 |
| `bool` accepted | 13 of 42 |
| Non-finite check removed | 14 of 42 |
| COMFORT skip removed | 3 of 42 |
| Low threshold -0.3 | 5 of 42 |
| Strict low boundary / strict high boundary | 3 / 3 of 42 |
| Sampler perturbed with the flag on | 5 of 42 |
| Proactive `valence` key restored under the flag | 5 of 42 |
| `numbers.Real` replaced by `(int, float)` | 2 of 42 (numpy, Fraction) |
| Huge rational treated as unusable | 1 of 42 |
| `float()` applied to every valence | 2 of 42 |
| Quality cap widened to 0.06 / narrowed to 0.01 / guard always passes | 4 / 4 / 4 of 35 |
| Placebo cells counted in the guard | 6 of 35 |
| Worst per-repeat share used instead of the pooled share | 6 of 35 |
| `placebo_basis` dropped | 1 of 35 |
| `tokens_fn` ignored | 2 of 35 |
| Gating placebo moved back to the prompt halves | 2 of 35 |

One mutation survived and is equivalent: applying the non-finite check to integers too. For an int, `math.isfinite` raises `OverflowError` only above float range, and the same `except` branch that handles huge rationals lets it through, so behavior is unchanged. The integer branch still matters when that `except` is changed (the huge-rational mutation above).

## The measure harness

`evals/brainbench/mood_style.py` takes an injected `ReplyClient` and runs the 40-prompt design: flag off first, then flag on, each at clamp +0.8, -0.8 and 0.0, 5 repeats. `analyze` computes:

- Cliff's delta of word counts, positive clamp against negative, per arm; `delta_on - delta_off`.
- Noise band per arm: largest gap of the per-repeat headline. `void` ("not measurable at this tier") when either band is above 0.165.
- Gating placebo `placebo_repeat_split_on` against the larger arm noise band, with `placebo_basis` in the result. The prompt-halves placebo is reported only.
- 1000 label permutations, seed 20261030, swapping the on/off pair inside each (prompt, seed, repeat) block. One-sided by choice: the declared direction is positive, so `outside` means the observed effect exceeds the 99th percentile of the signed null. The result carries `"sided": "one-sided, positive direction"`. A two-sided cut would be looser and is not used.
- Quality guard: fixed 0.03 absolute cap on the rise in rejected-reply share, pooled (above). Tests: +5 points fails, +2 passes, equal passes, a drop passes, zero rejections passes, exactly +3 passes and +3.25 fails, rejections clustered in one repeat are judged on the pooled share, placebo cells do not count.
- The share of replies at 250 tokens. `ActionServiceReplyClient` takes an optional `tokens_fn(reply_text) -> int`; without it `Reply.tokens` is `None` and the share is reported as `None`, so the runner must supply one for the tier it uses.

`ActionServiceReplyClient` runs the real `ActionService.execute` and toggles the flag with the `+mood-style` arm. `select_prompts` applies the intent and turn filters to generated lifesim panels. No model was called.

### Which rule to watch: `void`, not the placebo

`mood_style_noise_check.py` simulates the whole analysis under a stated noise model and counts verdicts. The model is my assumption, not a measurement: each prompt has a fixed log-length (mean ln 40 words, sd 0.5), each reply adds lognormal noise (sd 0.35), and with the flag on a reply gains `lift` words at +0.8 and loses `lift` at -0.8. 60 runs per lift, run `r` seeded `20261030 + r`, 1000 permutations:

| lift (words) | pass | fail | void | gating placebo rule failed |
|---:|---:|---:|---:|---:|
| 0 | 0 | 11 | 49 | 0 |
| 10 | 16 | 0 | 44 | 0 |
| 20 | 19 | 0 | 41 | 0 |
| 40 | 23 | 2 | 35 | 2 |

The corrected placebo rarely fails the run (0 to 2 of 60 per lift). The likeliest non-pass is `void`: with 40 prompts against 40, Cliff's delta moves by chance between repeats, so the band exceeds 0.165 in 35 to 49 of 60 runs under this model, even with a real lift. In that case the result reads "not measurable at this tier" whatever the line does. If the real reply noise is like this model, a first run will mostly return `void`; more repeats or more prompts would narrow the band, but that changes the approved design and is not done here. Reproduce with `python -m evals.brainbench.mood_style_noise_check` from `backend/` (about 15 seconds). Real replies may be noisier or quieter than the model, so the counts show how the rules behave, not what a run will show.

## What is not proven

1. **No output effect.** Nothing here shows the line changes reply length. D1 to D9 show the line reaches the prompt and nothing else moves. Only the model-backed run can pass or fail the claim, and it has not run.
2. **Prompts are not frozen.** `select_prompts` needs a generated lifesim dev panel (seeds 1000 to 1004). The 40 prompt texts are not committed.
3. **`void` is the binding rule** under the noise model above (see the table). Whether a real run is measurable depends on real reply noise, which is unknown.
4. **The noise model is an assumption.** The simulation says how the rules respond, not how Claude Code replies are distributed.
5. **Token cap.** The share of replies at 250 tokens needs the runner to pass a `tokens_fn`; none is wired to a real tier.
6. **No full-pipeline test.** D8 and D9 use the real stage 7 payload builder and `ActionService.execute`, but no test drives `CognitivePipeline.execute` end to end with the flag on.
7. **D7 is slow.** It runs `defects.collect()` twice, about 7 seconds, over the 2 second gate budget. The sibling slice 2a tests have the same cost.
8. **Voice and the local 4B to 9B tier** are out of scope (`Not claimed` in the prereg).

## Run it

From `backend/`, with the main checkout's interpreter:

```
/path/to/main/checkout/.venv/bin/python -m pytest tests/test_mood_style_drive.py tests/test_mood_style_measure.py tests/test_ledger_registry.py tests/test_brainbench_switchboard.py -q
```

Nothing needs restarting: the flag defaults off and no running service reads it until it is set.
