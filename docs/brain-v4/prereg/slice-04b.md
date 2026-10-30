# Preregistration: slice 4b (mood drives a style or sampler term, D-052)

**Status: PROPOSED. Every numeric margin below is a proposal for D-047. No model-backed run of slice 4b happens before Aniket approves or changes the numbers. The deterministic gate tests and the build behind the flag are not blocked.** Written for Aniket. To be committed before any slice 4b code or run.

## Premise check (read this first)

D-052 and `prereg/slice-04.md` item 3 say mood drives no sampler term today. That is true of the names in `_compute_endocrine_options` and false of the effect. The function reads `cortisol`, `dopamine` and `fatigue` (`backend/app/cognitive/action.py:949-951`), and two of those are functions of mood:

- `cortisol_tonic = 0.5 - valence/2 + 0.3*fatigue` (`backend/app/state/agent_state.py:407-411`), and `cortisol` is tonic plus a decaying burst (`:431-441`). Temperature is `0.9 - 0.6*cortisol` (`action.py:958-964`).
- `dopamine_tonic = max(0, valence) * arousal` (`agent_state.py:467-476`). `top_p` is `0.70 + 0.25*dopamine` (`action.py:967-975`).
- `valence` is the `mood` field (`agent_state.py:307-313`), and the snapshot reads `cortisol` and `dopamine` live from those properties (`agent_state.py:2778-2780`).

Read-only probe with `.venv/bin/python` (default `AgentState`, energy 0.5, fatigue 0, no hormone burst), calling the real `_compute_endocrine_options`:

| mood | cortisol | dopamine | temperature | top_p | num_predict |
|---:|---:|---:|---:|---:|---:|
| -0.8 | 0.90 | 0.00 | 0.36 | 0.70 | 250 |
| -0.4 | 0.70 | 0.00 | 0.48 | 0.70 | 250 |
| 0.0 | 0.50 | 0.00 | 0.60 | 0.70 | 250 |
| +0.4 | 0.30 | 0.20 | 0.72 | 0.75 | 250 |
| +0.8 | 0.10 | 0.40 | 0.84 | 0.80 | 250 |

So a mood clamp at +0.8 versus -0.8 already moves temperature by 0.48 and top_p by 0.10. The slice 4 sentence "a mood clamp has no declared effect through that function" (`prereg/slice-04.md` item 3, `results/slice-04/README.md`, last section) is wrong as written. It needs a one-line correction (open item 1).

Mood also reaches the reply through six other paths (the table lists six), none of them measured by an eval I found (a grep of `backend/evals/`; absence is not proven):

| Path | Evidence | What it does |
|---|---|---|
| Directive text | `agent_state.py:2789-2799`, injected at `identity.py:774` (`VOLATILE INTERNAL STATE`) | "deeply melancholic and reserved" below -0.6, "optimistic" above +0.6 |
| Emotion label | `agent_state.py:2823-2835`, rendered at `action.py:884` | "Current Emotion: sad/happy/..." with thresholds at +-0.4 |
| Relational stance | `decision.py:250-262`, rendered at `action.py:891` | mood is one third of the stance score |
| Breath or sigh tag | `action.py:1003-1009`, emitted at `:1504-1506` | `<breath_fast>` or `<sigh_soft>` opener |
| Candidate scoring | `global_controls.py:75-97` via `agent_state.py:885-896`, used at `decision.py:1119` (`AFFECT_CONTROL_ENABLED` defaults true, `config.py:227`) | negative valence raises urgency gain, positive raises exploration |
| Voice trajectory (not text) | `expression.py:98-120`, `brain_agent.py:1389-1411` | valence feeds the Rust APRA trajectory; `style` stays the constant `"natural"` |

The ledger row for mood lists one reader, `emotional_bias` (`backend/app/state/ledger.py:107-112`). Seven more exist. The drift test cannot see them.

**What is still missing** (these are the gaps the build targets, each verified):

1. `top_p` is flat at 0.70 for every mood at or below 0. `dopamine_tonic` clamps negative valence to zero (table rows -0.8, -0.4, 0.0). The positive half of the mapping moves, the negative half is dead.
2. Reply length depends on fatigue only (`action.py:984-989`). Mood never touches it. The length text hint ("keep replies brief") is keyed to energy, not mood (`agent_state.py:2801-2802`).
3. Temperature carries mood and stress in one number. A mood of -0.8 and an acute cortisol burst give the same temperature, so a clamp control cannot separate them.
4. The proactive plan payload carries `cortisol` and `dopamine` only, no `valence`, no `fatigue` (`core.py:632-641`), so any mood term that reads `payload["valence"]` is inert on proactive turns.
5. The graded effect of mood on text is a three-way switch at +-0.6 (directive) and a five-way label. There is no continuous register term.

**Consequence for the design.** Because the old paths already move with mood, a clamp that "changes output in the declared direction" proves nothing about a new term: the flag-off arm passes it too. Every measure below is therefore a difference of differences, flag-on minus flag-off, never the clamp effect alone.

## Claim under test

With the flag on, a mood clamp at +0.8 versus -0.8 changes the measured output in the declared direction by more than it does with the flag off, by a margin larger than the noise band. A placebo (flag on, both groups clamped at 0.0) and a label-shuffled analysis show no such change. With the flag off, `_compute_endocrine_options` and `_build_realization_contract` return byte-identical output to main on the D1 input grid, and the slice 2a digests are unchanged (the implementation must gate the new term on the flag; D1 tests exactly that).

## Where mood could legitimately act (grep result) and what is proposed

| Place | Verdict |
|---|---|
| `top_p` negative branch (`action.py:967-975`) | **Variant A.** Fills gap 1. Sampler. |
| Reply length / pacing line in the realization contract (`action.py:875-900`) | **Variant B.** Fills gaps 2 and 5. Style channel to the realizer. |
| Temperature coefficient (`action.py:958-964`) | Rejected. Already follows mood with slope 0.3 per unit; changing the slope changes flag-off behavior and mixes mood with stress further. |
| `num_predict` (`action.py:984-989`) | Rejected as the length mechanism. It is a ceiling. If typical replies are far under 100 tokens it never binds and the effect is zero. The baseline step records the share of replies at the cap; B uses text because that is measurable on the Mac tier. |
| `relational_stance` (`decision.py:250-262`) | Rejected. Mood already a third of it; a new term double counts. |
| `GlobalControls` (`global_controls.py`) | Rejected. Affects candidate ranking, not the realized text; slice 4b is about the realizer. |
| `SpeechExpression.style` (`expression.py:34`, `"natural"` constant) | Rejected for 4b. Voice side, and the field is part of the wire contract (`backend/app/contracts.py`, `backend/crates/contracts/src/lib.rs`), so changing it is a contract change. Named as a candidate for a later voice slice. |
| Directive thresholds (`agent_state.py:2794-2799`) | Rejected. Changing existing text changes flag-off bytes. |

## Variants (tournament, D-052)

One flag for both: Config field **`MOOD_STYLE_DRIVE_ENABLED`**, default false. `backend/evals/brainbench/switchboard.py:28-76` reserves names only for W1, W2, W3, W6 and W7, none for this, so the name is new; an arm `+mood-style` is added to `ARMS`. The cold critic picks one variant, the loser is deleted, not kept behind another flag. Max 2 critic rounds (standing cap). The critic's rubric is the pass rules in this file, frozen when it is committed.

### Variant A: breadth (sampler)

- **Mechanism.** Negative mood narrows the sampling nucleus, positive mood already widens it; A adds a negative-side response of the same sign as the existing positive-side one (the magnitudes differ: 0.10 against 0.25 times dopamine). Rationale from the broaden-and-build account: low mood narrows the range of what is considered, high mood widens it. This is the mechanism claim, not a measured result.
- **Formula (PROPOSED).** `top_p = clamp(0.70 + 0.25*dopamine + 0.10*min(0, valence), 0.60, 0.95)`. Positive valence adds nothing, because `dopamine_tonic` already carries it, so there is no double count on the positive side. Temperature and `num_predict` are untouched.
- **Bounds.** At most -0.10 versus flag off (reached at valence -1.0), exactly 0 for valence >= 0. Absolute floor 0.60.
- **Input.** `payload["valence"]` (set from `state_snapshot["mood"]`, `pipeline.py:412`). Non-finite, boolean, string or absent valence is treated as 0.0 (no effect). The proactive payload gains a `valence` key only when the flag is on (gap 4).
- **Sampler honored only by:** Ollama (`ollama_client.py:196-204`) and Anthropic (`anthropic_client.py:28-43`). The local Claude Code client ignores `temperature`, `top_p` and `num_predict` (`claude_code_client.py:72-82`). See Measures: A's output effect cannot be measured on the Mac today.

### Variant B: register (style channel to the realizer)

- **Mechanism.** Low mood means less talk, high mood more talk, a long-standing finding on depressed versus elevated speech output. B adds one line to "Current Context" and nothing else.
- **Rule (PROPOSED).** In `_build_realization_contract`, flag on and valence available:
  - valence <= -0.4 and `plan.goal` is not `COMFORT`: `- Reply length: keep it brief, one or two short sentences.`
  - valence >= +0.4: `- Reply length: you have room to say more, two to four sentences.`
  - otherwise no line (a dead zone from -0.4 to +0.4).
- **Bounds.** At most one added line of at most 120 characters. No other plan field, option or state value changes.
- **Why +-0.4.** It is the existing label threshold (`agent_state.py:2823-2830`), so B flips only when the "Current Emotion" label class flips and introduces no new flicker class. The low bucket skips `COMFORT` so a distressed user is not answered with one sentence by a low-mood mind.
- **Measurable on the Mac tier** (prompt text reaches the frontier model through local Claude Code).

## Measures and proposed pass rules

### Deterministic gate (no model, under 2 s, runs on every commit)

Gate tests D1 to D9, all of them must pass for either variant to be eligible. Rule values are PROPOSED.

1. **D1 flag-off byte-identical.** SHA-256 of the output of `_compute_endocrine_options` over a grid (cortisol, dopamine, fatigue each in `{None, 0, 0.25, 0.5, 1, "x", NaN}`, with and without a `valence` key) and of `_build_realization_contract` over a plan grid, recorded from main before the change and compared with the flag off. Same pattern as `test_flag_off_matches_recorded_slice_02a_baseline`.
2. **D2 monotone.** With cortisol, dopamine and fatigue held, A's `top_p` is non-decreasing in valence over [-1, 1] at step 0.01; temperature and `num_predict` do not depend on valence. B's line level (brief, none, more) is non-decreasing in valence.
3. **D3 bounded.** A: `top_p` in [0.60, 0.95], and `|top_p_on - top_p_off| <= 0.10` for every input. B: added text at most 120 characters, one line.
4. **D4 end to end through a real `AgentState`.** For mood in a grid from -1 to 1, derive options with the real state. A: on-minus-off `top_p` at mood -0.8 is at least 0.05 (formula gives 0.08) and is exactly 0 at every mood >= 0. This is the check that A adds nothing where dopamine already acts.
5. **D5 inert inputs.** Valence `None`, `NaN`, `inf`, `True`, `"0.5"` and absent: identical to flag off.
6. **D6 flicker.** On 1000 seeded mean-reverting mood trajectories (step sd 0.05), B's line changes at most as often as the existing `get_emotion_label` class changes at the +-0.4 boundary. A's per-turn change in `top_p` is at most `0.10 * |delta mood|`.
7. **D7 no state change.** The slice 2a digests (`627dcce6...`, raw `cb62dc20...`) from `defects.py collect`, run in one process with the flag toggled, are unchanged with the flag on and off. Slice 4b writes no state.
8. **D8 proactive path.** Flag on, the proactive plan payload carries `valence`; flag off it does not (byte-identical payload).
9. **D9 clamp wiring.** On an offline copy (`app/state/interventions.py`, `clamp("mood", +-0.8)`), the sampler options that reach a recording client differ from flag off by exactly the amount D4 predicts, and the prompt differs only by variant B's line (two separate assertions). This proves the clamp, the flag and the client record agree.

The recording client records the options and the prompt per call. It is not `PinnedOptionsClient` (`backend/evals/action_path.py:60`), which overrides all three sampling options on purpose (`backend/evals/README.md`, "Sampling is pinned over the endocrine layer"), so the existing `evals run --path action` cannot see variant A at all.

### Model-backed measure for variant B (Mac, frontier tier through local Claude Code)

- **Rig.** Real `ActionService.execute` with a recording client, offline copy of a mind per seed, mood clamped by the slice 1 operator.
- **Seeds and prompts (PROPOSED).** Dev split seeds 1000 to 1004, which `python -m evals.lifesim panel --split dev --count 5` maps to steady_professional, chatty_student, forgetful_retiree, volatile_creative, terse_engineer. Per seed, the first 8 user turns after turn 20 whose intent is in `{small_talk, share_fact, share_update, share_plan, question}` (no `express_emotion`, so the user's own mood does not confound). 40 prompts. Frozen before the first run; editing one starts a new preregistration.
- **Arms.** current (flag off), B (flag on). Each at clamp +0.8, clamp -0.8, and a placebo clamp 0.0 with the 40 prompts split into halves by index parity (even and odd), fixed before the run. 5 repeats each (D-048).
- **Headline.** Cliff's delta (`backend/evals/brainbench/stats.py:168`) of reply word counts, positive-clamp replies against negative-clamp replies. `delta_on` and `delta_off` are computed per arm; the effect is `delta_on - delta_off`.
- **Noise band.** Per D-048: the largest pairwise gap of the headline across the 5 repeats. The Claude Code client cannot lower temperature, so the band includes default-temperature sampling noise.
- **Pass rule (PROPOSED).**
  1. Baseline first: the flag-off arm is run and recorded before the B arm, with the share of replies at 250 tokens (so the ceiling question for `num_predict` is answered with data).
  2. `delta_on - delta_off >= 0.33` and exceeds twice the larger arm noise band. The direction must be positive (longer replies at +0.8).
  3. If either arm's noise band exceeds 0.165, the rule is void and the result reads "not measurable at this tier", not pass. Both 0.33 and 0.165 are the values Aniket approved for slice 4 (D-050); reused so one convention covers the two mood slices.
  4. **Placebo:** with the flag on, the 0.0 versus 0.0 split gives `|delta| < ` the noise band.
  5. **Shuffled-label control:** the observed `delta_on - delta_off` lies outside the 99th percentile of 1000 label permutations of the recorded replies (seed 20261030), applied at analysis time. The permutation unit is the flag-on/flag-off label within each (prompt, seed, repeat) block, so the analysis stays paired by prompt; permuting individual replies across prompts is not allowed because it would give a falsely narrow null.
  6. **Quality guard:** the share of replies the existing `_validate_partial_response` or self-correction path rejects (`action.py:718-731`, `:1522-1556`) with the flag on is not above flag off by more than the noise band.

### Model-backed measure for variant A (BLOCKED on this machine)

No tier on the Mac honors sampling. The local Claude Code client ignores `temperature`, `top_p` and `num_predict` (`claude_code_client.py:72-82`), and `ollama` was not found on PATH when this was written (the run log records `command -v ollama`; re-check before relying on it). The output effect of A can run only on an Ollama tier (the GPU, D-036, or a small Ollama model on the Mac if Aniket allows it, open item 3). Until then A is proven only by D1 to D9, which show the number reaching the request and nothing about what the model does with it. The rule, for when a tier exists (all PROPOSED):

- Metric: pooled distinct-2 (share of unique bigrams) over the 40 prompts, per run; 5 repeats at the lowest temperature the tier allows (D-048).
- Pass: at clamp -0.8, `distinct2_off - distinct2_on >= 0.03` and above twice the larger noise band; at clamp +0.8 and at the 0.0 placebo, `|distinct2_on - distinct2_off|` below the noise band (A is inert there); shuffled-label control as above. Void if the noise band exceeds 0.015 (half the margin).
- Degeneration guard: at the corner mood -0.8 with cortisol at 1.0 (temperature 0.30, `top_p` 0.60, a region main never reaches), the share of replies containing a repeated 4-gram is not above flag off by more than the noise band.
- Why 0.03: a judgment about the smallest diversity change worth shipping, chosen with no data. It is replaced by the band the baseline shows before the run if the baseline contradicts it.

### What the tournament can and cannot settle

On the Mac, only B can pass a model-backed rule. If A wins the critic's comparison, it ships flag-off with its output effect marked unverified, and the claim "mood drives a sampler term" stays unproven until the sampler-honoring tier runs. The critic is told this. Both variants must still pass D1 to D9.

### Reported, not a pass rule

- Slice 4 flag on (`AFFECT_USER_INPUT_ENABLED`), 4b flag on, a hostile then a supportive scripted user: the mood path, then the reply register. Reported as a trace, because it adds the user loop that the clamp removes.
- The slice 4 clamp control, once run, will show the temperature and top_p movement from the premise table. The finding belongs in `results/slice-04`.

## What could go wrong

| Risk | Evidence | Control |
|---|---|---|
| Mood already reaches style by another path, so the flag-on effect is the old path | table in the premise check; directive "reserved" at `agent_state.py:2795` pushes toward short replies at mood < -0.6 even with the flag off | differential design (`delta_on - delta_off`), dead zone in B, placebo arm |
| Double counting with cortisol | `cortisol_tonic` is valence (`agent_state.py:404-411`); a low-mood mind already runs temperature 0.36 | A touches `top_p` only and only for valence < 0; D4 proves zero change at valence >= 0 |
| Compounded narrowing at low mood | temperature floor 0.30 (cortisol 1.0) meets A's `top_p` floor 0.60 | degeneration guard; floor 0.60 is a judgment, not data |
| Oscillation: mood, style, user reaction, mood | slice 4 flag on makes user text move mood (`pipeline.py:817` before, `event_valence` after); a terse reply can annoy the user and lower mood further | thresholds equal the label thresholds (D6); the loop cannot be measured with a scripted user, so the claim is limited to the clamp, and the trace above is descriptive |
| Proactive turns skip the term | `core.py:632-641` payload lacks `valence` and `fatigue` | D8; the proactive prompt already states the mood label (`core.py:608`) |
| Self-correction retry changes sampling | `action.py:1537-1541` adds 0.2 to temperature on the same options dict | A's `top_p` is carried unchanged; test that a retry keeps the A value |
| The existing probe harness hides A | `evals/action_path.py:60` pins sampling | separate recording rig; stated above |
| Terse reply to a user in need | B low bucket | skipped for goal `COMFORT`; the quality guard rule 6 |
| Clamp measures state, not a lived mood | offline copy sets the field directly | the claim is about the term, not about mood dynamics (slice 4) |

## Not claimed

No claim about felt realism or user preference. No claim about the local 4B to 9B tier (D-036). No claim about voice output (`SpeechExpression`). A pass for B is a frontier test-tier result (D-003, D-024). A's output effect is unclaimed until a sampler-honoring tier runs.

## Open for Aniket

1. **Does D-052 stand?** The wording in `prereg/slice-04.md` item 3 and `results/slice-04/README.md` is already corrected (D-056). The probe shows a +0.8 versus -0.8 clamp already moves temperature by 0.48 and top_p by 0.10. Say whether D-052 stands as written ("mood should drive a sampler or style term"), becomes "close the verified gaps 1 to 5", or is dropped.
2. **Approve or change the numbers:** A coefficient 0.10 and floor 0.60; B buckets +-0.4, the two sentence budgets, the `COMFORT` skip; `delta_on - delta_off >= 0.33` with the 2x-noise condition and the void at 0.165 (reused from D-050); 40 prompts from seeds 1000 to 1004; the 1000-permutation seed 20261030; the A margin 0.03 and its void at 0.015.
3. **Sampler tier for A.** Options: (a) wait for the GPU hand-back; (b) allow a small Ollama model on the Mac for this one measure (needs an install, not done here); (c) allow the Anthropic API for this one measure (the standing rule forbids it without your word). Without one of them A cannot win on evidence.
4. **Flag name.** `MOOD_STYLE_DRIVE_ENABLED` is mine; no reserved name exists. Accept or rename.
5. **Proactive payload.** Add `valence` to the proactive plan under the flag (D8), or leave proactive turns out of scope.
6. **Ledger.** Add the seven missing mood readers to `backend/app/state/ledger.py` so the drift test covers them (a slice 1 follow-up), or leave it.

## Decided 2026-10-30

D-057: close the verified gaps (this tournament). D-058: variant A is proven by gate tests only until a sampler-honoring tier exists. Still PROPOSED and awaiting approval: every numeric margin, the flag name `MOOD_STYLE_DRIVE_ENABLED`, the proactive payload `valence` key, and the ledger readers.
