# ADR-W5: Barge-in end to end

**Status:** implemented 2026-09-27. Sections 1-7 are the spec, frozen
2026-09-27 and unchanged. Section 8 records the implementation, the review
findings, the results and the mutation re-run. Section 9 records Codex cold
critic round 1 and its fixes.
Owns (`../work/COVERAGE.md`): V-3, F-013, DR-026, DR-027, DR-028, DR-030.
Builds on ADR-003 (`../../brain-research/adr/ADR-003-barge-in-turn-targeting.md`)
and W4's playback lifecycle (`AudioPlaybackLifecycle` in `backend/app/contracts.py`).

Sections 1-7 are the frozen reference for both W5 builders and for the
critic. A builder that finds the spec wrong stops and reports; it does not
change the spec.

## 1. What is wrong today

| Id | Defect | Where |
|---|---|---|
| F-013 | An ordinary barge-in (`confirmed_user_speech`) stops audio but writes no terminal outcome and does not cut the history row to what was heard. BrainBench: `confirmed_barge_in` history mismatch 0.143 in wave A. | `brain_agent.py` `_on_audio_stop` returns early for that reason |
| V-3 | `_on_chat_input` awaits the whole turn. nats-py delivers one message per subscription at a time, so a second `chat.input` cannot arrive while a turn runs, and preempting an in-flight turn from speech is unreachable. | `_on_chat_input` (`await task`) |
| slot | One `_superseded_reply` slot. A second supersession before the first resolves loses the first reply. | `_begin_turn` |
| proactive history | A proactive reply is stored without a row id, so no cut can address it, and it never gets a terminal. | `_finish_reply` |
| proactive preempts user | Any `chat.input` from the subconscious replaces the active generation, including a user reply in progress. DR-026 allows this only for a significant thought. | `_on_chat_input` |
| DR-030 | No grace window, and no reaction to the user starting to talk over a proactive turn until their final transcript. | none |

## 2. Configuration (frozen names and defaults)

All in `backend/app/config.py`. Both state machines read them from `Config` at
run time and may shrink the time values for speed.

| Name | Default | Meaning and justification |
|---|---|---|
| `PROACTIVE_GRACE_MIN_IMPORTANCE` | 0.75 | A speaking proactive reply with `importance >= this` gets the grace window; below it cedes instantly. Same bar as `PROACTIVE_SELF_DIRECTED_IMPORTANCE_MIN`, the existing "high importance" line; `PROACTIVE_USEFUL_IMPORTANCE_MIN` (0.45) is only "worth saying". |
| `PROACTIVE_GRACE_WINDOW_S` | 0.6 | Longest the proactive reply keeps playing after the user's first partial. The STT publishes a final no earlier than `STT_ENDPOINT_SILENCE_MS` (700) after speech stops and a partial no earlier than `STT_MIN_SPEECH_MS` (250) after it starts, so the final lands at least ~0.7 s after the first partial. 0.6 s therefore always expires before the user's final: the grace never delays the user's reply, and it adds 0 ms to DR-024's time to first audio (300-500 ms, measured from the final). At ~150 words/min it lets about 1.5 words finish. |
| `SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE` | 0.9 | A subconscious input with `importance >= this` may interrupt a user reply in progress (DR-026, "rare"). Above the 0.75 initiation bar, so an interrupt is rarer than an initiation. |
| `USER_MID_UTTERANCE_TIMEOUT_S` | 1.2 | The user is mid-utterance if a non-empty partial arrived within this many seconds and no final `chat.input` from them since. Partials arrive every `STT_PARTIAL_INTERVAL_MS` (500) while speaking and the endpointer waits 700 ms of silence, so 1.2 s covers a within-utterance pause. |
| `REPLY_TERMINAL_WAIT_S` | 2.0 | After the brain cuts a reply that already published audio, how long it waits for the transport's lifecycle terminal before resolving the reply itself. Same bound as `REPLY_INSERT_WAIT_S`. |

## 3. Terms

- **Reply**: the output of one turn, user or proactive, identified by its
  `turn_id`. A reply **starts** when its first `chat.output` chunk with
  content is published.
- **Heard text**: `text[:offset].strip()` (DR-027, unchanged granularity),
  where `offset` is the lifecycle `heard_offset`, else the last playback
  progress `character_offset`, clamped to `len(text)`. COMPLETED hears the
  full text.
- **Speaking**: a reply is speaking from its first lifecycle `STARTED` (or
  first progress frame) until its lifecycle terminal.
- **Resolution**: the one terminal fact per started reply: status
  `COMPLETED | TRUNCATED | CANCELLED | FAILED`, heard text, offset, source
  (`user` or `proactive`), reason.
- **Ledger**: every started, unresolved reply, keyed by `turn_id`. Replaces
  the single superseded slot.
- **Brain self-stop reasons**: `confirmed_user_speech`, `proactive_ceded`,
  `proactive_grace_expired`, `self_thought_interrupt`. The brain acts on
  these when it publishes them; `_on_audio_stop` ignores them on receipt, so a
  redelivered one changes nothing.

## 4. State machine

Per reply:

```
            first chunk published
 GENERATING ─────────────────────▶ STARTED ──lifecycle STARTED/PLAYING──▶ SPEAKING
    │                                 │                                     │
    │ cut before any chunk            │ cut                                 │ cut
    ▼                                 ▼                                     ▼
 CANCELLED (resolved)            CUT_PENDING ◀──────────────────────────────┘
                                      │ lifecycle COMPLETED / INTERRUPTED (not flushed) / FAILED
                                      │   or REPLY_TERMINAL_WAIT_S elapses
                                      ▼
                         COMPLETED | TRUNCATED | FAILED  (resolved)
 SPEAKING ──lifecycle COMPLETED──▶ COMPLETED;  ──INTERRUPTED (not flushed)──▶ TRUNCATED;  ──FAILED──▶ FAILED
```

- A lifecycle `INTERRUPTED` with `flushed=True` is a self-correction's
  rejected take (DR-029). It resolves nothing.
- A cut in `CUT_PENDING` that times out resolves `TRUNCATED` at the last
  heard offset if the reply was ever speaking, else `CANCELLED`.
- First terminal wins. Every later terminal fact for a resolved reply is a
  no-op (repeated frames, JetStream redelivery, a stop plus a lifecycle
  event).
- Resolution writes, once:
  - history: the reply's own row (addressed by its `message_id`, ADR-003) is
    rewritten to the heard text when heard text differs from the stored
    text. A reply never stored gets no write; nothing is appended (ADR-003).
    Proactive replies are now stored with a `message_id` too.
  - outcome: a user reply emits exactly one `OutcomeRecord` with the same
    status. A proactive reply has no `ActionIntent`, so it emits none; its
    resolution is its record.

Global (per agent):

- **Floor**: the active turn is the latest accepted input's turn.
- **User speech tracking**: `_on_user_speech_partial` stamps the last partial
  time; a user final `chat.input` clears it.

## 5. Events and transitions

| Event | Handler (frozen) | Behaviour |
|---|---|---|
| user `chat.input` (final) | `_on_chat_input` | Redelivery (an `utterance_id` already accepted, bounded memory of the last 256) is ignored. Onset-noise drop unchanged (`BARGE_IN_ONSET_GRACE_S`). Publishes `confirmed_user_speech` (unless speculative intent pending, as today). Any reply still generating is cut: CANCELLED if it never started, else CUT_PENDING. Any speaking reply is CUT_PENDING (the transport flushes it; its INTERRUPTED lifecycle gives the heard offset, closing F-013 and DR-028). Starts the new turn **and returns without awaiting it** (V-3). |
| subconscious `chat.input` | `_on_chat_input` | If the user is mid-utterance: **declined**. Else if a user reply is generating or speaking: interrupts only if `importance >= SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE` (publishes `self_thought_interrupt` scoped to that reply, cuts it as above, starts the thought turn); otherwise **declined**. Else: starts normally (an older proactive reply is cut as above). A declined input creates no turn, no reply, no stop, no cancel, no proactive-attempt mark, and is recorded in `declined_proactive_inputs`. |
| user partial (`audio.perception`, non-empty `text`) | `_on_user_speech_partial` (new; brain subscribes to `audio.perception`) | Ignored within `BARGE_IN_ONSET_GRACE_S` of our audio onset (echo). Stamps mid-utterance. If a proactive reply is speaking: importance < `PROACTIVE_GRACE_MIN_IMPORTANCE` → publish `proactive_ceded` for it and cut it now, without awaiting any timer; otherwise start one grace timer (a second partial does not restart it) that at `PROACTIVE_GRACE_WINDOW_S` publishes `proactive_grace_expired` and cuts it, unless the reply resolved first or the user's final arrived first (which cuts it through the row above). A proactive reply still generating (not started) is cut CANCELLED at once, whatever its importance. |
| `audio.stop` | `_on_audio_stop` | `flush` → unchanged (DR-029). Speculative → unchanged (duck). Brain self-stop reasons → ignored. `confirmed_command` for the active turn or for **any** unresolved ledger reply (ADR-003 extended past one slot) → cut that reply. Any other confirmed stop (e.g. facial startle) for the active turn → cut it; for any other turn → stale, no effect. |
| lifecycle | `_on_audio_playback_lifecycle` | As section 4. Unknown turns ignored. Reordered or duplicate events are resolved by W4's `PlaybackLifecycleTracker`. |
| progress | `_on_audio_playback_progress` | Updates the heard offset of the reply it names (ledger entry or active). Never resolves anything. |

Concurrency rules (V-3):

- One generation task at a time; a new accepted input cancels the running one
  under `_generation_lock` (latest wins). No input queue, so no unbounded
  backlog. The JetStream ack now means "accepted", not "answered"; a crash
  mid-turn loses that turn instead of replaying it. Accepted, because a
  replay after a crash re-answers a question the user may have moved past.
- Handlers stay serial per subject (nats-py). Every handler returns in
  bounded time: none awaits a turn's generation, and every other await is
  bounded (`REPLY_INSERT_WAIT_S`, `REPLY_TERMINAL_WAIT_S`, the grace timer
  runs as its own task).
- The ledger holds at most 32 unresolved replies. Overflow resolves the
  oldest `FAILED` with reason `reply_ledger_overflow`, so it is still
  accounted for.

## 6. Invariants (both state machines check at least these)

| # | Invariant |
|---|---|
| I1 | Every started reply has exactly one resolution once the transport has delivered a terminal for every started utterance and all timers have fired; no reply is ever resolved twice. |
| I2 | A user reply with an intent has exactly one `OutcomeRecord`, with the resolution's status. A proactive reply has none. |
| I3 | Every stored reply's history row equals its heard text at word level (COMPLETED: full text). No row other than a reply's own is ever rewritten. Nothing is appended by a cut. |
| I4 | A stop for a turn that is neither active nor in the ledger changes nothing: no resolution, no cancel, no history write. |
| I5 | Every handler returns in bounded time; `_on_chat_input` returns while the scripted generator is still blocked. |
| I6 | A speaking proactive reply with importance >= `PROACTIVE_GRACE_MIN_IMPORTANCE` is stopped no later than first accepted partial + `PROACTIVE_GRACE_WINDOW_S` (plus scheduling slack) and no earlier than the first of: the window, the user's final. |
| I7 | A speaking proactive reply below `PROACTIVE_GRACE_MIN_IMPORTANCE` is stopped during the handling of the first accepted partial. |
| I8 | A subconscious input interrupts a user reply only if its importance >= `SELF_THOUGHT_INTERRUPT_MIN_IMPORTANCE` and the user is not mid-utterance. A declined input changes nothing observable except `declined_proactive_inputs`. |
| I9 | A user `chat.input` delivered while a turn generates cancels that turn (preemption reachable). |
| I10 | Redelivering any `chat.input`, `audio.stop` or lifecycle event changes nothing. |
| I11 | The ledger never exceeds 32 entries. |
| I12 | Brain-side overhead from a user final `chat.input` to the new turn's first `chat.output` (excluding the pacing sleep and the generator's own time) is under 50 ms p95. A grace window never delays it. |

## 7. Frozen interface for the two state machines

Both machines drive the real `BrainAgent` built the way
`backend/tests/test_barge_in_real_flow.py` builds it (scripted core, history
double with `log_message(role, text, message_id=)` and
`rewrite_assistant_message(text, message_id=)`, `publish` captured). They
call the handlers in section 5 directly, through a fake NATS that delivers
each subject's messages one at a time, in order, with subjects running
concurrently, and that can redeliver any message.

Observables they may read:

- `agent.reply_resolutions`: append-only list of `ReplyResolution(turn_id,
  status, heard_text, character_offset, source, reason)`.
- `agent.get_outcome_history(turn_id)` for `OutcomeRecord`s.
- `agent.declined_proactive_inputs`: list of declined `utterance_id`s.
- `agent.reply_ledger_size()`: current ledger size.
- Captured `publish` calls (topic, payload), the history double's rows.

Inputs they generate: user and subconscious `chat.input` (subconscious with
`metadata.source="subconscious"`, `importance`, `category`), partials
(`AudioPerception` payloads), `audio.stop` with every reason, target kind
(active, ledger, stale, unknown, none) and `speculative`/`flush` value, W4
lifecycle events with duplicates, reordering and `flushed`, progress frames,
and redelivery of any of these.

## 8. Implementation, results, mutation re-run

### How it was built

Two Codex builders worked from sections 1-7 in separate worktrees without
seeing each other: W5-A implemented the brain side and state machine A;
W5-B wrote state machine B from the spec alone. Both stopped on a Codex usage
limit before finishing, and the reviewer finished W5 in the integration
worktree.

Code: `backend/app/agents/brain_agent.py` (`ReplyResolution`, the reply
ledger `_reply_ledger`, `_resolve_reply`, `_cut_reply`,
`_allow_proactive_input`, `_handle_user_final`, `_on_user_speech_partial`,
`_handle_ledger_audio_stop`, redelivery memory for `chat.input` and
`audio.stop`), the five config names in `backend/app/config.py`, and
`app.clock.sleep` (below).

**V-3, ordering and backpressure.** `_on_chat_input` validates, applies the
barge-in decision, replaces the generation task under `_generation_lock` and
returns; it never awaits the turn. nats-py still delivers `chat.input` one
message at a time, so inputs are accepted in order; the latest accepted input
cancels the running generation (latest wins), and there is no input queue, so
nothing can back up. The JetStream ack now means "accepted": a crash mid-turn
loses that turn instead of replaying it.

### Review findings fixed by the reviewer (each with a test that fails without it)

| Finding | Fix | Test |
|---|---|---|
| W5-A scoped the user-final `confirmed_user_speech` stop to the active turn. An older reply still draining (a significant thought had taken the floor) kept playing over the user. | Stop unscoped again; every started, unresolved reply is cut. | `test_user_final_stops_every_playing_reply_not_only_the_active_turn`; mutant `W5_user_final_stop_scoped_to_active_turn` |
| W5-A cut the playing reply even with a speculative intent pending, when no stop is published and Stage 2 may reject the interruption and resume the audio: the reply resolved TRUNCATED after the terminal wait while it played to the end. | Nothing is cut on that path; Stage 2's `confirmed_command` stop cuts through the ledger, or the reply completes. | `test_pending_speculative_intent_does_not_record_a_cut_reply`; mutant `W5_speculative_pending_still_cuts` |
| The grace, terminal-wait and mid-utterance timers used real time, so both machines raced them and Hypothesis reported `FlakyStrategyDefinition`. | `app.clock.sleep` (production: `asyncio.sleep`; under `ManualClock` it returns when the clock is advanced); the W5 timers and stamps use `app.clock`. Both machines run on a `ManualClock`. | both machines deterministic at 1,000 examples |
| Every `BrainAgent` starts a `SubjectMetrics` thread that wakes every 50 ms and never exits; Hypothesis builds one harness per example. A run reached 1,453 threads and a load average above 800. | Both machines and the BrainBench barge-in suite shut the threads down per agent. | probe: 2-4 threads through a run |
| A self-thought interrupt of a user reply still generating (no chunk yet) cut it without publishing its `self_thought_interrupt` stop: `_cut_reply` publishes only for a started reply. Section 5 says the interrupt publishes its scoped stop, and a filler may already be playing under that turn. Found by machine B. | `_allow_proactive_input` publishes the scoped stop itself when the reply has not started. | `test_self_thought_interrupt_before_the_first_chunk_still_publishes_its_stop`; mutant `W5_self_thought_interrupt_silent_before_first_chunk` |
| A confirmed stop naming no turn missed the ledger (looked up under `""`) and fell through to the pre-W5 truncation, which recorded its own TRUNCATED outcome and history row; the ledger reply then recorded a second outcome on the transport's terminal (I2). Found by machine B. | `_handle_ledger_audio_stop` addresses an unscoped confirmed stop to the active turn. | `test_unscoped_confirmed_stop_cuts_the_active_reply_through_the_ledger`; mutant `W5_unscoped_confirmed_stop_bypasses_ledger` |
| `ManualClock` keeps time as a `datetime` (microseconds), so advancing by exactly `deadline - monotonic()` could land a fraction of a microsecond short; the sleeper never woke and machine B's timer-by-timer `advance` spun forever (a 1,000-example run hung for 36 minutes). | A deadline within one resolution step is due; B's `advance` also fails loudly if a timer stays pending at its own deadline. | `test_advancing_exactly_to_next_deadline_wakes_the_sleeper` (fails without the fix) |
| A proactive reply still generating when a typed user final landed (no partial first) was never resolved: `_replace_active_generation` gated its CANCELLED on `_reply_generating`, the *user* reply's flag, which was already false once an earlier user reply had finished. The entry sat in the ledger until an overflow recorded it FAILED. Found while deleting the single slot. | Each entry records the flow task generating it; a flow that ends before its first chunk (cancelled, failed or empty) resolves its reply CANCELLED in its own `finally` (`_end_generation`), with the canceller's reason. | `test_typed_user_final_cancels_a_proactive_reply_still_generating` (fails on the checkpoint); mutants `W5_unstarted_flow_end_does_not_resolve`, `W5_cancel_reason_not_recorded` |
| A user reply in its pacing sleep (300-900 ms before generation) was not "in flight": the check read the same generating flag, set only after the sleep. A routine thought landing then was accepted and ceded the user's reply CANCELLED, so the user's question went unanswered (DR-026 lets only a significant thought interrupt). | An unresolved user reply is in flight from the moment its turn begins; with `_end_generation`, unresolved means live. | `test_a_user_reply_in_its_pacing_sleep_is_in_flight_for_a_thought` (fails on the checkpoint); mutant `W5_pacing_user_reply_not_in_flight` |
| A proactive reply's text reached its ledger entry only in `_finish_reply`. A transport COMPLETED arriving while the done marker was being published resolved it with nothing heard, and the full row was stored afterwards with no entry to own it. | The text is on the entry before the done marker is published, for every reply. | `test_a_terminal_that_beats_the_end_of_the_flow_hears_the_whole_reply`; mutant `W5_final_text_reaches_the_entry_after_done` |
| A resolved reply stays in the ledger while its history rewrite waits for the reply's own insert (up to `REPLY_INSERT_WAIT_S`). A second, different stop landing then (a startle after a voice command) cancelled again and released adrenaline a second time: one interruption felt twice. Found by the surviving mutant `W5_ledger_resolved_reply_can_be_cut_twice`, whose guard (a scan of past resolutions) could not see that window. | Each entry records whether its interruption was felt; adrenaline is released once per reply. Cutting was already idempotent. A voice command after the user's own speech cut the reply (the brain's stop, no adrenaline) is still the first one felt. | `test_a_stop_while_the_cut_writes_history_is_not_a_second_interruption` (adrenaline twice without it), `test_superseded_stop_is_honoured_once`; mutant `W5_one_interruption_felt_twice` |
| Wall-clock latency asserts inside the machines (I5's 50 ms, I12's p95) failed under load (70 ms, 116 ms). | I5 is checked structurally (the handler returned, the turn is still running); I12 moved to dedicated tests. | `test_i12_first_output_overhead_p95_under_50ms`, `test_i12_an_open_grace_window_never_delays_the_users_reply` |

Harness defects in machine B, fixed without touching its invariants: the
proactive stub's signature, a per-step check of I1's quiescence condition,
the overflow expectation (34 inputs overflow a 32-entry ledger twice, at the
last reported heard offset), blocking a proactive turn the stub cannot
block, the user-final source name (`whisper`), a flushed COMPLETED treated
as a no-op (spec: only a flushed INTERRUPTED is), and the fake transport
flushing only the active turn on an unscoped stop. A second batch, found once
B ran on the precise clock: a user reply cancelled while its stub was still
blocked never reached Stage 6, so I2 expects no outcome for it (the harness
now records which turns yielded an `action_intent`); the playback rules sent
lifecycle and progress for a reply that had published nothing, which this
harness (no fillers) cannot produce; the I6 check picked up an unscoped stop
from an earlier final that predates the grace window; a proactive reply was
expected to cede twice, and to cede after it had finished; and the lifecycle
rule injected events into a live stream without the transport taking them
into account, so the transport reused a seq with a different state, played
on after its own terminal, and never retried after an injected flush. The
fake transport also ignored an external stop naming no turn (the voice agent
flushes what is playing), and a stop did not drop a self-correction's queued
retry take, which then played to COMPLETED after the brain had cut the
reply. The model kept counting a reply that a confirmed stop had cancelled
before its first chunk as a reply in flight, and missed that the terminal-
wait timeout is a reply's first terminal. The
fake transport and the model now read every lifecycle stream through the W4
reducer (`PlaybackLifecycleTracker`, verified by W4's own property tests),
the one the brain runs, instead of a looser seq rule of their own. The
BrainBench suite had
the same class of model gaps (it never modelled the transport's flush on a
user final, ran the speculative path without a speculative intent, and
counted proactive terminals from `OutcomeRecord`s, which a proactive reply
never has, I2). The corrected suite still fails the pre-W5 brain: history
differs from what was heard in 100% of `confirmed_barge_in` and
`stale_stop` scenarios and 6% of `random`.

### The single superseded slot is deleted

Section 3 says the ledger replaces the single superseded slot. The first
cut kept the slot's code behind the ledger, which handled every stop for a
live reply first. A probe that recorded every entry into the old paths found
them reached in production only with no reply to act on (a no-op), and in
tests that built slot state by hand; 22 of the 49 mutants survived because
they mutated that shadowed code. Keeping it meant two outcome paths, which
is what I2 forbids, so it is gone:

- `_SupersededReply` and the `_superseded_*` fields; `_begin_turn` keeps
  returning the superseded turn id, which Stage 2 needs as
  `interrupted_turn_id`.
- `_truncate_interrupted_reply` and the tail of `_on_audio_stop` that
  called it. A stop that addresses no live reply is logged and ignored; it
  no longer releases adrenaline for an interruption that cut nothing.
- The pre-ledger outcome paths: FIX-CLD-05's CANCELLED in
  `_replace_active_generation`, the CANCELLED in `_cancel_active_generation`,
  and the `_reply_contexts` path in the lifecycle handler (a terminal for a
  turn with no ledger entry is for a reply already resolved).
- `_reply_resolved`, `_reply_log_task`, `_reply_message_id`,
  `_reply_turn_id`, `_reply_generating`. Each reply's row id, insert task,
  text and intent live on its entry.

`_process_chat_input_flow` now wraps the turn body (`_run_turn`) so a reply
that never started resolves however the flow ends. Deleting the flag that
gated FIX-CLD-05 is what exposed the first two findings above.

Behaviour that differs from the pre-W5 code, all as sections 3-4 specify:
a cut with no progress waits for the transport's heard offset (DR-028)
instead of keeping the full text; a cut at offset 0 records that nothing was
heard; and a COMPLETED terminal records the whole reply, superseding
FIX-CLD-04's "trust the COMPLETED offset" (chunk offsets are stamped into the
true text, so a smaller COMPLETED offset can only be trailing whitespace).

Tests that built slot state by hand now build a ledger entry and drive it
with real messages: `test_barge_in_truncation.py`,
`test_brain_agent_turn_state_lock.py` (the race it guarded cannot happen: a
new turn never writes another reply's entry, and the test shows that),
`test_embodied_feedback.py`, `test_playback_progress.py`,
`test_causal_slice.py` (cancellation tests run the real flow wrapper) and
two tests in `test_barge_in_real_flow.py`.

### Spec clarifications (sections 1-7 stay frozen; these resolve ambiguities found in review)

- The user-final `confirmed_user_speech` stop is unscoped: it names no turn, and every started, unresolved reply is cut (section 5's "any speaking reply is CUT_PENDING").
- A confirmed stop that names no turn applies to the active turn, as ADR-003 set for unscoped signals (the voice agent always honours one). I4 is about stops that name a turn.
- `flushed` matters only on INTERRUPTED (section 4); a COMPLETED or FAILED is a terminal whatever its flag.
- With a speculative intent pending, the user final cuts nothing (no stop is published; Stage 2 decides).
- A reply whose flow ends before its first chunk (cancelled, failed or empty) resolves CANCELLED: nothing was published, so no terminal can come (section 4's "cut before any chunk"). The reason is the canceller's, else `generation_failed` or `nothing_generated`.
- A user reply is in flight for section 5's self-thought gate from the moment its turn begins, pacing included.

### Invariant reconciliation (A vs B)

| Invariant | A | B |
|---|---|---|
| I1 one resolution per started reply | yes | yes, plus at quiescence |
| I2 one outcome per user reply, none for proactive | yes | yes |
| I3 history equals heard; own row only; nothing appended | yes | yes |
| I4 stale stop changes nothing | yes | yes |
| I5 handlers bounded; chat handler returns while generating | yes | yes |
| I6 grace bound | yes | yes |
| I7 low importance cedes on the first partial | yes | yes |
| I8 interrupt only when significant and not mid-utterance | yes | yes |
| I9 preemption reachable | yes | yes |
| I10 redelivery changes nothing | chat input, stops | chat input, stops, lifecycle, progress |
| I11 ledger bound | yes | yes, with an overflow burst |
| I12 first-output overhead | dedicated test | dedicated test |
| W4 lifecycle duplicates and reordering | no | yes |
| DR-029 flushed take resolves nothing | no | yes |
| Terminal-wait timeout, TRUNCATED and CANCELLED | no | yes |
| A generating proactive reply is cancelled on a partial | no | yes |

Both machines ship, so the union is checked on every run.

### Results

**BrainBench barge-in suite** (real `BrainAgent` in process, seed 1000, 50
scenarios per family, 10 families, 500 scenarios, 58 s on the Mac):

| Measure | Pre-W5 brain | W5 |
|---|---:|---:|
| history differs from what was heard, `confirmed_barge_in` | 50/50 | 0/50 |
| history differs from what was heard, `stale_stop` | 50/50 | 0/50 |
| history differs from what was heard, `random` | 3/50 | 0/50 |
| replies with zero or multiple terminal outcomes | 0 | 0 |
| started replies without a terminal | 0 | 0 |
| stale or unknown stops applied, current turn harmed, hangs | 0 | 0 |
| violations in the three W5 families (self-thought gate, grace bound, low-importance cede) | n/a (no W5 handlers) | 0 |

The pre-W5 column runs the same suite with HEAD's `brain_agent.py`, over the
seven families that existed before W5. Across all 500 W5 scenarios every
invariant the suite checks reports 0 violations; 1,017 replies started and
all reached one terminal.

**State machines** (Hypothesis, stateful step count 20, `ManualClock`), on
the final code after the deletion: machine A passes 1,000 examples (66 s);
machine B passes 1,000 examples (5 min), and passed two further independent
1,000-example runs before the deletion. Both run at 20 examples in CI
(`W5_SM_EXAMPLES` raises it).

**Full backend suite** (`CI=1 pytest`, Mac, final code): 3,209 passed, 11
skipped, 0 failed. Older tests updated for W5: two asserted right after
`_on_chat_input` returned, which since V-3 is before the turn runs; two
built a partial `BrainAgent` without the reply ledger; and the tests that
built single-slot state by hand (listed under the deletion above).

### Mutation re-run (`scripts/barge_in_mutations.py`)

Before W5 the script held 38 mutants against ADR-003's single-slot code;
W5 added 11. After the deletion, 30 of the 38 no longer matched any source (their code is
gone); `tests/test_barge_in_mutation_patterns.py` fails on that, so they
could not linger. Each was either ported to the gate's ledger form or
dropped with the code it mutated:

| Ported to | From | Gate |
|---|---|---|
| `W5_stale_progress_leaks_into_the_active_turn` | M1, Z42 | progress for a superseded reply stays on its own entry |
| `W5_stale_stop_is_addressed` | M3, N8 | only Stage 2's command reaches a superseded reply (I4) |
| `W5_unstarted_flow_end_does_not_resolve` | N2, M12, M13 | a reply whose flow ends before its first chunk resolves CANCELLED |
| `W5_cancel_reason_not_recorded` | N3 | that CANCELLED carries the canceller's reason |
| `W5_row_id_only_inside_the_lock` | N4, N17, M14 | the row id reaches the entry before the flow waits for the lock |
| `W5_out_of_order_lifecycle_applied` | M5 | only an APPLIED lifecycle event acts |
| `W5_insert_ignores_brain_id` | M10 | the reply is stored under the id the brain generated |
| `W5_record_offset_is_trimmed_length` | Y20 | the outcome records the heard offset |
| `W5_fully_heard_reply_is_rewritten` | N6 | a reply heard in full is not rewritten |
| `W5_final_text_reaches_the_entry_after_done` | Y19 | the text is on the entry before the done marker |
| `W5_proactive_reply_takes_the_user_turns_intent` | N18 | a proactive reply has no user intent (I2) |
| `W5_pacing_user_reply_not_in_flight` | new | the pacing-window finding |
| `W5_flushed_take_resolves_the_reply` | new | DR-029: a flushed INTERRUPTED resolves nothing |
| `W5_one_interruption_felt_twice` | new | the felt-once finding |

Dropped with their code: M2, M4, M6, M15, N5, N7, N11, N12, N13, N14, X12,
X17, Z17 (the superseded slot, `_reply_contexts`, `_reply_resolved`,
`_reply_generating`, `_reply_turn_id`).

**Result on the final code: 32 mutants, 30 killed, 2 survive and are
equivalent, 0 unexplained survivors, 0 errors** (4 parallel shards, each on
its own copy of `backend/` with a passing unmutated baseline):

| Group | Mutants | Killed | Survived (equivalent, reason recorded in the script) |
|---|---:|---:|---|
| ADR-003 history and store gates kept (M7, M8, M9, M11, N9, S3, S5, Y14) | 8 | 6 | S5: history ids are fresh UUIDs, so the addressed row is always the brain's own assistant row. Y14: since the ledger `last_audio_progress` is diagnostic only; every cut reads its own entry's progress. |
| W5 gates from sections 4-6 | 10 | 10 | |
| W5 ledger ports and the review findings | 14 | 14 | |

The first run after the deletion (22 survivors at the checkpoint, of which
21 mutated shadowed single-slot code) is what drove the deletion. The run
after it left four survivors; each became a fix or a test: M9 (the unstored
guard was shadowed by a second check, now single), the felt-once finding,
a stale non-command stop for a superseded reply still playing (no test had
one: `test_a_stale_stop_for_a_superseded_reply_still_playing_cuts_nothing`),
and Y14 (equivalent, recorded).

## 9. Cold critic, round 1 (Codex)

A fresh Codex session reviewed the diff against sections 1-7, on its own
copy of the tree, with sections 8-9 of this ADR withheld. It ran the
real-flow, regression and state-machine suites (55 passed), the BrainBench
barge-in suite and the clock seam (60 passed), and three targeted mutants
(all killed). Verdict: **FAIL**, with four real-flow defects and one LOW. It
found no second outcome or history path: `_resolve_reply` is the only
emitter, and `_store_heard_reply` is the only rewrite.

Each fix below has a test that fails on the code the critic reviewed. Red
was checked by running the new tests against that exact `brain_agent.py`
(and `clock.py` for #5).

| # | Sev | Defect (critic's reproducer) | Fix | Test (red on the reviewed code) |
|---|---|---|---|---|
| 1 | MED | `started` was set before a chunk's publish and kept when the publish raised. With the fallback's publish failing too, `_end_generation` skipped the reply as started: it never resolved and no OutcomeRecord was emitted. | `started` is still set before the publish, because a stop landing mid-publish must cut the chunk. If the publish raises, it is rolled back unless an earlier chunk went out or the transport has reported progress. The flow's end then resolves the reply CANCELLED (`generation_failed`). | `test_a_reply_whose_chunks_never_reach_the_broker_is_resolved_once` |
| 2 | HIGH | Overflow marked the oldest reply resolved, awaited its history write, and only then removed it from the ledger. Cancelled during the write, the entry stayed. Later overflows picked it, returned at once, and the ledger grew past its cap (33, 35, ...). `_begin_turn` also sat outside the flow's cleanup, so a flow cancelled there left its own new entry behind. | `_resolve_reply` now does everything but the history write synchronously: it frees the slot, emits the outcome and records the resolution. The overflow caller shields the write, so the row still lands if the flow is cancelled. `_begin_turn` moved inside the flow's `try`. `_end_generation` only touches the entry its own flow created. | `test_a_cancel_during_an_overflow_write_keeps_the_ledger_bounded`, `test_a_flow_cancelled_while_its_overflow_resolves_resolves_its_own_reply` |
| 3 | MED | A replacement or a confirmed stop awaited the cancelled generation, without bound, while holding `_generation_lock`. A generator that stalls in its cancellation cleanup blocked every later `chat.input`. | `_await_generation_teardown` waits at most `GENERATION_TEARDOWN_WAIT_S` (0.25 s). Past that, the old task is left to finish and the reply it had not started is resolved by the canceller. A **publish fence** (`_generation_fenced`: the current task has a pending cancellation) drops every chunk and done marker from a generation that was told to stop, so a generator that swallows its cancel cannot speak after its successor starts. | `test_a_generation_stalled_in_its_cancel_cleanup_does_not_hold_chat_input`, `test_a_generation_that_swallows_its_cancel_cannot_speak_afterwards` |
| 3b | MED | Found while fixing #3: `except asyncio.CancelledError: pass` around `await prior_task` also swallowed the handler's *own* cancellation (shutdown, a callback timeout), and the handler then started a new turn anyway. This is also why the critic's reproducer, written with `wait_for`, could not fail on the old code: `wait_for`'s cancel reached the stalled task, unstuck it, and was then swallowed. | `asyncio.wait` never swallows its caller's cancellation. | `test_a_handler_cancelled_while_it_waits_for_teardown_stays_cancelled` |
| 4 | MED | The stream-error fallback was published but its text was never put on the reply's entry, so a COMPLETED terminal resolved the reply as `""` or as the partial text. The chunk was also deliberately untracked (P4-2). | The reply is now what reached the voice: the words already published, then the fallback. Unpublished words are dropped. The fallback is tracked against that text like any chunk. The text goes on the entry before the done marker, and the turn returns it, so `last_assistant_response` and the history row hold it too. This closes the P4-2 gap the untracked chunk documented. | `test_a_stream_error_fallback_is_the_text_its_terminal_resolves`, `test_stream_to_speech_exception_fallback_is_tracked_against_what_was_spoken` (2 cases; supersedes `..._omits_offsets`) |
| 5 | LOW | `ManualClock` kept cancelled sleepers until the next `set` or `advance`. | A sleeper removes itself when its wait ends, however it ends. | `test_a_cancelled_sleeper_leaves_without_the_clock_moving` |

**One behaviour made deterministic in passing.** A cut reply used to stay in
the ledger while its history row was written. A confirmed "stop" from Stage
2 that landed in that window was felt as the interruption, and one landing
after it was ignored, so the outcome depended on the write's timing.

- With #2 the reply leaves the ledger at once.
- A reply the user's own speech cut (TRUNCATED) now stays addressable in a
  bounded map of resolved replies (`_resolved_replies`,
  `REPLY_RESOLUTIONS_MAX`). Stage 2's command for it is felt exactly once,
  whenever it lands.
- A stop for a reply that COMPLETED or never played is still not an
  interruption.

Existing tests `test_superseded_stop_is_honoured_once` and
`test_a_stop_while_the_cut_writes_history_is_not_a_second_interruption` pin
this behaviour.

**Why the shield sits on the overflow and not in `_resolve_reply`.**
Shielding every resolution adds a task hop per terminal. That delayed a
burst of 32 transport terminals past machine B's bounded settle. The only
caller that can be cancelled while a row is being written is the overflow
inside a flow:
- the lifecycle handler, the grace timer and the teardown path are not
  cancelled mid-write;
- the unstarted-reply paths have no row to write.

So the write stays inline and the overflow caller shields it.

### Mutation re-run after round 1

Nine mutants were added, one per fix. Each breaks exactly the line its fix
added.

| Mutant | Finding | What it breaks |
|---|---|---|
| `W5_failed_first_chunk_stays_started` | #1 | the rollback of `started` after a failed publish |
| `W5_resolved_reply_leaves_the_ledger_last` | #2 | puts the ledger pop back after the history write |
| `W5_overflow_write_not_shielded` | #2 | the overflow caller's shield |
| `W5_teardown_unbounded` | #3 | the teardown bound |
| `W5_no_publish_fence` | #3 | the fence |
| `W5_timed_out_teardown_leaves_the_reply` | #3 | the canceller resolving an unstarted reply past the bound |
| `W5_fallback_is_not_the_reply_text` | #4 | the fallback becoming the reply's text |
| `W5_cut_reply_not_addressable_after_resolving` | felt-once | the stop lookup in resolved replies |
| `W5_cancelled_sleeper_retained` | #5 | the sleeper self-removal (`tests/test_clock_seam.py` joins the mutation run) |

**Result: 41 mutants, 39 killed, 2 survive as the recorded equivalents
(S5, Y14), 0 unexplained survivors, 0 errors.** All 9 new mutants are
killed.

### Verification after round 1

| Check | Result |
|---|---|
| Machine A, 1,000 examples | pass |
| Machine B, 1,000 examples | pass |
| Full backend suite (`CI=1`) | 3,218 passed, 11 skipped, 0 failed |
| BrainBench barge-in suite | 500 scenarios, 0 violations in every family |
| Ruff check and format | clean |

## 10. Cold critic, round 2 (Codex, last round per the cap)

A fresh Codex session reviewed the round-1 fixes against the same withheld
sections, this time with its own scratch reproducers targeting concurrent
subscription interleavings the state machines and BrainBench suite cannot
reach: both harnesses await each event's handler before delivering the
next, so a real race between two `asyncio` tasks (a lifecycle event and an
in-flight publish, a partial and an awaited cut) never occurs in them.
Verdict: **FAIL**, four real-flow defects and one LOW. Each has a
reproducer copied verbatim into `backend/tests/test_w5_critic_r2_race.py`
and confirmed red on the code the critic reviewed before any fix landed.

| # | Sev | Defect (critic's reproducer) | Fix | Test (red on the reviewed code) |
|---|---|---|---|---|
| I3 | MED | An `INTERRUPTED` lifecycle arriving while the final `chat.output` publish is awaited let `_resolve_reply` remove the ledger entry before `_finish_reply` assigned its history row ID. When the flow resumed, `_finish_reply` saw no entry and inserted the full, unheard text. | `_resolve_reply` now narrows `entry.text` to the heard text at resolution time (before the entry can be popped), and keeps a bounded record of resolved replies (`_resolved_replies`, already used for the felt-once stop lookup). `_finish_reply` checks that record when the live ledger has nothing, and stores the resolved entry's (narrowed) text instead of the raw `full_response` argument. | `test_interrupted_terminal_before_finish_reply_must_not_store_full_history` |
| I8 | MED | `_allow_proactive_input` checked `_is_user_mid_utterance()` on entry, then awaited the stop publish through `_cut_reply` without rechecking. A real user partial delivered during that await updated the mid-utterance state but was never consulted again, so the thought was granted the floor anyway. | A second `_is_user_mid_utterance()` check runs immediately before `return True`, after the await. A partial that lands during the cut now declines the thought the same way a partial present at entry always did. | `test_partial_arriving_during_proactive_cut_must_decline_thought` |
| I5 | MED | The pending-insert wait was bounded, but the following `rewrite_assistant_message` await in `_store_heard_reply` had no deadline. A stalled store held `_on_audio_playback_lifecycle` open, which serialises later lifecycle events behind it. | The rewrite is spawned via `self.spawn(...)` (tracked, fire-and-forget) instead of awaited. A first attempt bounded it with `asyncio.wait(timeout=REPLY_INSERT_WAIT_S)`, which still failed the critic's own reproducer (a 30 ms return-time contract against a rewrite jammed forever): any positive bound is still a wait. Fire-and-forget is the only shape that satisfies both "the row eventually gets the heard text" and "the handler never blocks on it." Three pre-existing tests that asserted on stored history immediately after resolution needed an explicit drain of `agent._background_tasks` added before their assertions, since the write can now still be in flight when the handler returns. | `test_history_rewrite_is_unbounded_inside_lifecycle_handler` |
| I10 | MED | Stop deduplication (`_seen_audio_stops`) keeps only 256 keys. After eviction, replaying an old unscoped (`turn_id=None`) stop resolved its target against whatever turn was active *now*, not the turn active when it was first seen. | A separate `_unscoped_stop_targets` `OrderedDict` binds an unscoped stop's target once, independent of the 256-key eviction window, and is consulted first. An evicted-then-redelivered unscoped stop rebinds to its original target, never to a newer turn. | `test_evicted_stop_redelivery_must_not_cut_a_later_turn` |
| LOW | LOW | The next flow's coroutine was constructed before `_replace_active_generation` awaited prior teardown. A caller cancelled during that wait left the coroutine neither scheduled nor closed, which surfaced as `RuntimeWarning: coroutine ... was never awaited`. A `warnings.catch_warnings`-based regression test was tried first and found unreliable (the warning fires outside the capture window relative to GC timing). Replaced with a deterministic contract test. | `_replace_active_generation` now takes a zero-arg coroutine **factory**, called only after teardown finishes. Its two existing test call sites (`test_causal_slice.py`) and the one production call site (`_on_chat_input`) pass the bare function; nothing is constructed before teardown completes. | `test_replace_active_generation_builds_the_next_coroutine_only_after_teardown` |

**Two round-1 reproducers now fail against round-2 code, by design, not as
new defects.** The critic's evidence section flags: (a) the overflow
reproducer expects a resolved reply to remain in `_reply_ledger`, but round
1's fix #2 (§9) deliberately removes a resolved entry from the ledger at
once; (b) the cancellation reproducer expects replacement within 10 ms,
while `GENERATION_TEARDOWN_WAIT_S` is 250 ms by design (§9 #3). Both are
stale expectations against round-1's own frozen behaviour, not code paths
this round touched.

**Speech-segmentation note.** The critic flagged a change outside W5 (token-
fragment merge ordering, split threshold `score >= 0.7`) in the diff it was
given. That change predates this round and is unrelated to any of the five
findings above; left as-is.

### Mutation re-run after round 2

The 3 mutation patterns whose source lines changed shape under I3/I5/I10
(`M7_cut_not_addressed_by_id`, `W5_row_id_only_inside_the_lock`,
`W5_insert_ignores_brain_id`) were updated to match; `check_patterns()`
reports 0 problems, all 41 patterns match exactly once before the run.

**Result: 41 mutants, 39 killed, the same 2 recorded equivalents (S5,
Y14) survive, 0 unexplained survivors, 0 errors.** No new mutants were
added this round; the fixes are races and a factory-vs-value signature
change, not new branches the existing mutation set misses.

### Verification after round 2

| Check | Result |
|---|---|
| The critic's 4 reproducers (`test_w5_critic_r2_race.py`) | red before the fix, green after |
| Machine A, 1,000 examples | pass |
| Machine B, 1,000 examples | pass |
| BrainBench barge-in suite (500 scenarios) | 0 violations in every family (90/90 rate entries) |
| Targeted W5/barge-in/causal-slice/clock-seam battery | 231 passed |
| Full backend suite (`CI=1`) | 3,280 passed, 11 skipped, 1 failed |
| Mutation re-run | 41 mutants, 39 killed, 2 equivalent (unchanged from round 1) |

The one full-suite failure,
`test_stored_injection_corpus.py::test_stored_injection_corpus_is_quarantined_on_every_prompt_path`,
is a pre-existing intermittent flake, not a round-2 regression: it passed
12/12 across 3 isolated runs, and a bisect running all 193 `tests/test_*.py`
files alphabetically up to and including it (same code, same file set)
passed cleanly (3,004 passed, 0 failed). W5/`brain_agent.py` never touches
the memory/embedding/injection-gate path this test exercises. Filed in
`findings.md` for whoever next touches that suite; not chased further
under W5's critic-rounds cap.
