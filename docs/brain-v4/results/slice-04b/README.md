# Slice 4b, variant B: mood style line

Status: built behind `MOOD_STYLE_DRIVE_ENABLED` (default off). Gate tests D1 to D9 pass. The model-backed measure is built and unit tested with a fake client, and has not been run. Preregistration: `docs/brain-v4/prereg/slice-04b.md` (margins approved, D-064). This README covers the build plus the round 1 corrections after the two critic FAILs.

This is builder B's work in the 4b tournament. Variant A (the `top_p` term) is not in this branch.

## Deviation from D-064: no proactive `valence` key

D-064 approved "the proactive payload gains a `valence` key under the flag". Round 1 dropped it, and `backend/app/cognitive/core.py` is untouched. Reasons:

- B emits no line on an INITIATE turn (below), so the key has no consumer in B.
- The key also feeds `_surface_fallback_memories` (`current_valence` in the recall call, `action.py`), so with the flag on a proactive turn with an empty surfaced list recalled differently. That breaks the rule that the flag changes nothing but the one line.

This will be confirmed with Aniket. If a later variant needs mood on proactive turns, the key comes back together with a recall guard.

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

The rule. With the flag on and a usable valence:

- goal `INITIATE`: no line, either bucket. The proactive instruction (`core.py`) asks for one to three sentences, so the high bucket would contradict it and the low bucket repeats it.
- valence <= -0.4 and goal not `COMFORT`: `- Reply length: keep it brief, one or two short sentences.`
- valence >= +0.4: `- Reply length: you have room to say more, two to four sentences.`
- otherwise nothing.
- A bool, string, None, NaN or infinite valence adds nothing. A finite number outside [-1, 1] is not clamped and lands in the nearest bucket. An int is compared as an int and never converted, so `10**400` resolves to the high bucket and `-10**400` to the low one (`math.isfinite` on such an int raises `OverflowError`).

The line is appended last to the contract. Nothing else in the prompt, the plan, the options or the state changes.

Ledger: the `mood` row had one reader, `emotional_bias`. It now lists seven further paths found by grep, as nine symbols: sampler (`cortisol_tonic`, `dopamine_tonic`), directive (`get_behavioral_directive`), label (`get_emotion_label`), stance (`_bucket_relational_stance`), breath tag (`_prepended_affect_tag`), candidate scoring (`_refresh_global_controls_locked`), voice (`derive_speech_expression` and `brain_agent._derive_expression_wire`). A tenth entry, `_mood_style_line`, is B's own reader; remove it if B is deleted.

## Boundary behavior at exactly +-0.4

The preregistered rule is inclusive (`<= -0.4`, `>= +0.4`) and `get_emotion_label` is strict (`< -0.4`, `> 0.4`). So at exactly -0.4 and +0.4 the line is present while the label is still "neutral". The sentence "the line flips only when the label class flips" is therefore false at those two points and true everywhere else. The rule was kept as approved; tests pin the actual behavior (D4 and D6 boundary tests, next float either side).

## What the gate tests prove

D1 hashes are literals recorded from main (`ec79dfe8`) before any code changed, over 1372 endocrine inputs and 1440 contract inputs. Everything below is deterministic and needs no model.

| Test | Proves |
|---|---|
| D1 | With the flag off, `_compute_endocrine_options` and `_build_realization_contract` match main byte for byte on the grids. With the flag on the sampler output is still main's, and the contract does change. |
| D2 | The line level (brief, none, more) is non-decreasing in valence over [-1, 1] at step 0.01, with exactly two changes at -0.4 and +0.4 inclusive. The sampler does not depend on valence. |
| D3 | The only added text is one line of at most 120 characters, one of two fixed strings, over the whole contract grid. |
| D4 | Through a real `AgentState`, sampler options are identical on and off for moods -1 to 1 and the premise table holds. The line class matches `get_emotion_label` at 199 of 201 moods. The boundary test pins the other two: at -0.4 and +0.4 the label is neutral and the line is present; one float outward the label agrees. |
| D5 | None, NaN, +-inf, True, False, "0.5", "-0.8", "x", list, dict, object and absent valence give the flag-off contract for every goal, with and without a behavior decision. A positive control shows usable values do produce a line. |
| D6 | On 1000 seeded mean-reverting trajectories (step sd 0.05, 100 steps) the line moves only on turns where the label class moves. A second test pins the exact-boundary step: the line moves one step before the label does. |
| D7 | The slice 2a digests (`627dcce6...`, raw `cb62dc20...`) from `defects.collect()` are unchanged, in one process, flag off then on. |
| D8 | With the flag on, the proactive payload equals the flag-off payload exactly (same dict, same canonical JSON, the old six keys, no `valence`) at moods -0.8, 0 and +0.8. Through the real proactive path with an empty surfaced list, the model prompt, system text and options, and the recall call arguments (`current_valence` stays 0.0), are identical on and off. |
| INITIATE | For `INITIATE`, flag on equals flag off in both buckets at -1, -0.8, -0.4, +0.4, +0.8, +1 and +-10**400, with and without a behavior decision, while the same valence on `ENGAGE` does get a line. An INITIATE plan carrying `valence` in its payload still gets no line end to end. |
| Huge ints | `_mood_style_line(+-10**400, ...)` does not raise and returns the nearest bucket; bool, str, None, NaN, inf stay inert. |
| D9 | On an offline copy, `clamp("mood", +0.8 / -0.8 / 0.0)`, hydrated `StateService` snapshot, the real stage 7 payload builder, the real `ActionService.execute` with a recording client: options on equal options off and equal the unchanged mapping (predicted difference zero); the prompt differs only by the expected line, once; system prompt identical. The two clamps reach different temperatures, which is why the claim is a difference of differences. |
| Also | COMFORT skip, retry keeps the line, ledger row lists the readers, flag default off, arm runnable and absent from `all`. |

### Mutation checks

Each mutation was applied to the code, the test file run (D7 deselected), then the file restored. Round 0 (original build): flag ignored (4 tests fail), lines swapped (8), second line appended (6), 120+ character line (2), `bool` accepted (12), `isfinite` removed (12), COMFORT skip removed (1), threshold -0.3 (3), sampler perturbed with the flag on (5). Round 1 corrections:

| Mutation | Tests failing |
|---|---|
| INITIATE guard removed | 19 of 58 |
| INITIATE guard only blocks the high bucket (the original conflict) | 9 of 58 |
| Proactive `valence` key restored under the flag | 5 of 58 (D8 payload x3, D8 prompt/recall x2) |
| `math.isfinite` applied to ints | 5 of 58 |
| Strict low boundary (`<`) | 5 of 58 |
| Strict high boundary (`>`) | 5 of 58 |
| Gating placebo moved back to the prompt halves | 2 of 25 (harness) |
| Quality band zeroed | 1 of 25 |
| Explicit zero-rejection pass removed | 3 of 25 |

The ledger addition has no mutation run: `test_mood_row_lists_every_known_reader` names `_derive_expression_wire`, and `test_ledger_registry.py` fails when a listed symbol disappears from its file; neither was exercised with a deliberate break.

## The measure harness

`evals/brainbench/mood_style.py` takes an injected `ReplyClient` and runs the 40-prompt design: flag off first, then flag on, each at clamp +0.8, -0.8 and 0.0, 5 repeats. `analyze` computes:

- Cliff's delta of word counts, positive clamp against negative, per arm; `delta_on - delta_off`.
- Noise band per arm: largest gap of the per-repeat headline.
- Gating placebo, PROPOSED correction: flag on, clamp 0.0, the same prompts, odd repeats against even repeats (`placebo_repeat_split_on`), compared with the larger arm noise band. Round 1 changed this: the prereg placebo (even against odd prompt halves) measures prompt composition, not noise. It is kept as `placebo_prompt_halves_on` and `_off`, reported and not gating. Needs confirmation by Aniket.
- 1000 label permutations, seed 20261030, swapping the on/off pair inside each (prompt, seed, repeat) block. One-sided by choice: the declared direction is positive, so `outside` means the observed effect exceeds the 99th percentile of the signed null. The result carries `"sided": "one-sided, positive direction"`. A two-sided cut would be looser and is not used.
- Quality guard: flag-on rejected share minus flag-off share must not exceed the band, the larger per-repeat gap of the rejected share in either arm. The prereg does not name the band; this is the D-048 recipe applied to this metric. If neither arm rejects anything the guard passes explicitly. A lone rejection moves that arm's per-repeat share by 1/80, so it does not fail the guard; a rate increase does.
- The share of replies at 250 tokens, when the tier reports tokens.
- Verdict: `pass`, `fail`, or `void` ("not measurable at this tier") when either arm noise band is above 0.165.

`ActionServiceReplyClient` runs the real `ActionService.execute` and toggles the flag with the `+mood-style` arm. `select_prompts` applies the intent and turn filters to generated lifesim panels. No model was called. The 25 harness tests use a deterministic fake client.

## What is not proven

1. **No output effect.** Nothing here shows the line changes reply length. D1 to D9 show the line reaches the prompt and nothing else moves. Only the model-backed run can pass or fail the claim, and it has not run.
2. **Prompts are not frozen.** `select_prompts` needs a generated lifesim dev panel (seeds 1000 to 1004). The 40 prompt texts are not committed.
3. **The corrected placebo is still weak as a gate.** `|delta| < noise band` compares one pooled delta with a gap between five small deltas. With no effect, both are noise of the same size, so the rule fails roughly as often as not. The fake client needed a jitter scale of 8 words to land the placebo below the band; with 3 the placebo rule failed although the fake had no drift. Expect the placebo rule to be the likeliest cause of a false fail on real data. Aniket should decide whether to compare against a band computed from the flag-off repeat split instead.
4. **Noise floor.** With 40 prompts against 40, Cliff's delta moves by chance between repeats, so the 0.165 void rule may fire on a real run even with a true effect.
5. **Token cap.** `ActionServiceReplyClient` returns `tokens=None`, so the share of replies at the 250-token cap is `None` until the real client supplies a count.
6. **No full-pipeline test.** D8 and D9 use the real stage 7 payload builder and `ActionService.execute`, but no test drives `CognitivePipeline.execute` end to end with the flag on.
7. **D7 is slow.** It runs `defects.collect()` twice, about 7 seconds, over the 2 second gate budget. The sibling slice 2a tests have the same cost.
8. **Variant A.** Not built here. The A parts of D2, D3, D4 and D9 are absent.
9. **Voice and the local 4B to 9B tier** are out of scope (`Not claimed` in the prereg).

## Run it

From `backend/`, with the main checkout's interpreter:

```
/path/to/main/checkout/.venv/bin/python -m pytest tests/test_mood_style_drive.py tests/test_mood_style_measure.py tests/test_ledger_registry.py tests/test_brainbench_switchboard.py -q
```

Nothing needs restarting: the flag defaults off and no running service reads it until it is set.
