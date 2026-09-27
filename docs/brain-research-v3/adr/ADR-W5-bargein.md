# ADR-W5: Barge-in end to end

**Status:** spec frozen 2026-09-27 (this file, sections 1-7). Implementation,
results and the mutation re-run are appended in section 8 when W5 merges.
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

Pending. Filled when W5 merges.
