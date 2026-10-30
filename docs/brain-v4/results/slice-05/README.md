# Slice 5: integrity trust moves on conduct evidence (NF-23)

Written for Aniket. Flag `TRUST_EVIDENCE_MODEL_ENABLED`, default false. Nothing changes in production until it is turned on. Prereg: `docs/brain-v4/prereg/slice-05.md` (margins PROPOSED, not approved, D-047).

## Outcome moved

Before: hostile, neutral and supportive text all raised `trust_integrity` by 0.1 per turn to 1.0 (`results/slice-02a/README.md`). After, flag on, on the same matched inputs and the real `CognitiveService`:

| Run (integrity, t0 to t10) | Flag on |
|---|---|
| hostile x10 | 0.5, 0.4007, 0.3015, 0.2022, 0.1029, 0.0037, then 0.0 |
| neutral x10 | 0.5 on every turn |
| supportive x10 | 0.5, 0.5762, 0.6523, 0.7285, 0.8046, 0.8808, 0.957, then 1.0 |
| 5 hostile then 5 supportive | 0.5, 0.4007, 0.3015, 0.2022, 0.1029, 0.0037, 0.0798, 0.156, 0.2322, 0.3083, 0.3845 |

Rules against the proposed margins: R2 hostile change -0.5 (never rises), R3 supportive +0.5 and above hostile from turn 2, R4 turn 5 is 0.0037 against 0.8808 all-supportive and rises on turns 6 to 10, R5 neutral change 0.0, R7 flag off reproduces the slice 2a digests (`627dcce6...`, raw `cb62dc20...`) with no skip. Benevolence, competence and mood are identical flag on and off. The series above were recomputed after the critic fixes.

## How it works

- `backend/app/cognitive/norm_evidence.py`: stateless, stdlib only. `norm_evidence(text)` scores the conduct register of one message in [-1, 1] (abuse, threat, contempt negative; thanks, apology, respectful register positive; no cue is exactly 0.0, which means no movement). It scores how the message is said, not how the writer feels.
- `pipeline.py`, USER_MESSAGE branch: with the flag on, passes `evidence=norm_evidence(event.raw_content)`.
- `agent_state.py::update_from_appraisal(..., evidence=None)`: flag on, `integrity += delta * evidence`; `None`, NaN, infinity, bool, strings and out-of-range values are no evidence and move nothing (rejected, not clipped). Flag off, the old `delta * NA` term. The `trust.update` trace names the signal used.

## Tournament

Two builders in worktrees. A: lexical, neutral when there is no evidence, no model. B: add a `courtesy` field to the System 2 model reading and apply it late under the state lock. A blind critic (labels shuffled, diffs only) picked A. Evidence: `/tmp/slice5/critique/verdict_round1.md`, attack scripts in `/tmp/slice5/critique/attack/`. B is not merged; its worktree stays for reference.

Why A won: its R2 to R5 results come from code that ran end to end. B moves integrity only on a model reading, so on the rubric's null-model rig R3 and R4 fail and pass only with a scripted reading. B also let a politely worded boundary violation raise integrity, loses evidence when the next message arrives first, and puts raw user text into a prompt that feeds the trust path.

## Critic round 1 and what changed

| Finding | Fix |
|---|---|
| One courtesy word hid hostility the lexicon has no entry for ("Thanks. I'll find you and end you." scored +0.54 and integrity rose) | Added threat and contempt entries (kill yourself, end you, hope you die, go die, fuck you/off, F U, kys, disgrace, disgusting). New guard: courtesy counts for nothing when any clause addresses the listener ("you") without a cue of its own. Seven strings pinned; three also run through the real service |
| Boundary-keyword hits misfired on "will", "user", "data" and lowered integrity on ordinary text | The boundary check is no longer used as evidence. `IMMUTABLE_CORE` boundaries are the agent's own commitments, and the substring match is wrong for judging a user. Pinned by tests |
| Docstring said the lexicon was kept apart from `event_valence`; 15 words overlap | Docstring says what actually separates the signals; a test pins the overlap count (16 after this fix) |
| Out-of-range evidence was clamped | Rejected, so a broken scorer is not turned into full-strength evidence |
| One assertion could never fail | Replaced with two direct inequalities |

Mutation checks run by the builder and reproduced by the critic: integrity step back to `NA` (27 tests fail), pipeline stops passing evidence (5), flag ignored (5), negation disabled (1).

## Critic round 2: FAIL, fixed without a round 3

The round 2 critic (cold, fresh) returned FAIL: 13 of 13 mutations killed and the 74 tests then passing, but five defects. My rule is a maximum of two critic rounds per unit, so these were fixed with tests and **no third critic has looked at the result**. Verdict: `/tmp/slice5/critique/verdict_round2.md`.

| Finding | Fix |
|---|---|
| F1: courtesy beside an uncued hostile imperative ("Thanks. Go jump off a bridge.", "Thank you. Die.") scored +0.54 and integrity rose to 0.715 | The round 1 guard fired only on clauses with "you". Now courtesy counts only if **every non-empty clause carries a cue**. Ten strings are table-tested for four turns each on the real service. Cost: "Thanks! Can you send the file?" earns no credit |
| F2: courtesy at the head of text longer than the 20,000-character cap survived while a later threat was never read | Text over the cap earns no courtesy |
| F3: a negator flipped supportive idioms ("I can't thank you enough" scored -0.29) | Idioms listed as positive phrases |
| F4: `_finite_evidence(10**400)` raised OverflowError and aborted the turn | Range test replaces `math.isfinite`; tested |
| F5: coverage limits were undocumented | Listed in the `norm_evidence` docstring and pinned by tests (below) |

Remaining single-clause failures I know about: "Would you mind dying?" still reads as courtesy. The structural rule cannot see inside one cued clause.

## Known limits (read before choosing)

1. **English only.** Other languages score 0.0, which is no movement, not a wrong sign. Creative spellings are not matched.
2. **Sarcasm and quotation are read as written.** "Oh thanks a lot for nothing" scores positive.
3. **The guard trades recall for safety.** "Thanks. Can you send me the file?" scores 0.0, so a polite message with an uncued question gets no credit. The error goes toward no movement.
4. **Small lexicon, weights are my judgment.** The hostile step (-0.099) and supportive step (+0.076) come from those weights, not from data. Abuse the list has no word for ("you are a waste of oxygen") is no evidence.
5. **Floor and ceiling erase depth.** Five hostile turns floor integrity at 0.0 and recovery speed does not depend on how long the abuse lasted. A cumulative rule is a later design change.
6. **Politeness raises integrity even from a manipulative user.** This is a conduct-register signal, not a check that the agent's norms were honoured.
7. **Coverage limits pinned in tests.** Harmless text scored negative: "This code is garbage, can you refactor it?", "How do I delete you from my phone?". Hostile text scored 0.0: "id1ot", "s t u p i d", "stuuuupid", a Cyrillic lookalike, "I will make you suffer", "I can't believe how stupid you are". Polite text scored 0.0: "You are wonderful!", "Thankyou". The negatives lower integrity on harmless text, which is the wrong direction; the zeros are the safe direction.
8. **No model-backed measurement.** The semantic arm and prereg rule 6 (trust suite on the frontier tier) have not run. They wait for margin approval and slice 4's noise band.

## Verification

`backend/tests/test_trust_evidence.py` (107 tests): R2 to R5, flag-off equality with the recorded baseline, confound control, scorer rules, edge inputs, trace keys, the hidden-hostility and unreadable-clause tables, the length cap, supportive idioms, huge-integer evidence and the pinned limits. Run counts for the whole suite are in the commit message.
