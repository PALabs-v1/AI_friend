# ADR-003 — A confirmed barge-in targets the reply that is playing

**Status:** Accepted, shipped. **Date:** 2026-09-24. Problem V-1 in `../01-problems.md`.

## Problem

Turn-scoped `audio.stop` / `audio.resume` exist so a late signal cannot kill
or restore the wrong turn: the Rust voice agent and the transport honour a
turn-scoped signal only when its `turn_id` equals the turn they are currently
speaking (`mesh_signal_applies_to_active_turn`). The STT publishes
`chat.input` with `turn_id=None`, so the brain gives every utterance a fresh
id. Stage 2 then stamped the confirmed stop (and a rejected interruption's
resume) with **the new utterance's id**, which never matches the speaking
reply. Result: a confirmed "stop" left the old reply playing at 30% volume
until it finished, and a rejected interruption never restored full volume.
Existing tests asserted the new id, encoding the bug.

## Alternatives

1. **Unscoped signal (`turn_id=None`)** — always applies. Loses the protection
   turn-scoping was added for (a delayed stop killing a newer reply).
2. **Look up the speaking turn in the voice agent** — requires a new
   request/reply contract across the Rust boundary.
3. **The brain records the turn each utterance supersedes and Stage 2
   addresses it** — no contract change, keeps scoping precise.

## Decision

Option 3. `BrainAgent._process_chat_input_flow` records the previously active
reply as `_superseded_turn_id` and passes it as `interrupted_turn_id` in the
event metadata. `_resolve_turn_conflict` stamps stop/resume with it (and falls
back to the utterance's own id for callers that do not supply it).
`_on_audio_stop` accepts Stage 2's `confirmed_command` stop for the
superseded turn, once. Any other stop for that turn (a facial-startle stop
published before the new utterance took over) is stale and ignored.

Accepting it must not act on the *current* turn's state. By the time Stage 2
publishes the stop, the new utterance has already reset
`last_assistant_response` and `last_audio_progress`, and the running task is
the "stop" utterance's own turn. So `_begin_turn` snapshots the superseded
reply (`_SupersededReply`: text, playback progress, intent, the task storing it),
playback progress for that turn keeps updating the snapshot, and an accepted
stop truncates the snapshot to what was heard without cancelling the current
turn. The superseded reply's generation had already ended: it finished, or
`_replace_active_generation` cancelled it when the new utterance arrived.

History rules, each from a review finding (01-problems.md, R2/R3/R4):

* **Only the reply's own row is rewritten, addressed by id.** The brain
  generates the history row id when it stores a reply
  (`log_message(..., message_id=)`) and a cut rewrites exactly that row
  (`rewrite_assistant_message(heard, message_id=)`, the id is required), or nothing if the
  row does not exist. Addressing "the newest assistant row" rewrote the
  previous reply when this one was never stored, or a newer reply; addressing
  by content (the R3 fix) rewrote an older reply with identical text (R4-1).
* **Nothing is appended.** A reply cancelled before it was stored has no id
  and gets no write: appending its heard part landed after the user's
  "stop", where it read as the answer to "stop". Consequence, accepted: the
  words the user heard from a reply cut off mid-generation are not in history.
* **The wait for the reply's insert is bounded** (`REPLY_INSERT_WAIT_S`, 2 s):
  it runs under `_turn_state_lock` and the store's pool has no command
  timeout. A reply whose insert is still pending after that stays uncut.
  The UPDATE itself (and the pool acquire before it) is not bounded; that
  was so before this ADR.
* **The id is published with the reply.** The reply text, the end of its
  generation, its row id and its insert task are set in one critical
  section, so a stop can never see a finished reply without the id its
  insert will use.
* **Outcome records describe delivery, history describes the stored row.**
  A reply cut but never stored (cancelled mid-generation) or whose insert
  was still pending is recorded TRUNCATED with the heard text while history
  has no row, or the full row, respectively.
* **The text must belong to the stopped turn.** `last_assistant_response`
  is reset only by user turns, so while a proactive (subconscious) turn
  speaks it still holds the previous user reply. `_reply_turn_id` records
  whose text it is; a stop for another turn cuts nothing, and a proactive
  turn's completed playback does not record the user reply COMPLETED again.
* **A reply is resolved once** (`_reply_resolved`) for COMPLETED and
  TRUNCATED: a facial startle publishes a stop for the active turn on every
  startle, even while idle, and transport may repeat a completed frame.
* **CANCELLED only while the reply is still generating**
  (`_reply_generating`), and once. It is cleared when generation ends
  normally, when a stop cancels it, and on replacement. (A flow that raises
  after its reset leaves it set; the task is already done, so nothing can
  record it CANCELLED later except a replacement of that finished task,
  which checks `.done()` first.) So replacing a proactive turn, or a turn
  still in its pacing sleep, never records a reply CANCELLED after it
  finished or was already cut.

Known and not changed here (pre-existing):
* **No COMPLETED record fires in production.** The Rust voice agent
  publishes raw PCM on `audio.stream` (`crates/voice-agent/src/main.rs`),
  and the transport marks an utterance done only from dict payloads
  (`transport_agent.py`), so `audio.playback.progress` never carries
  `completed=True` live. Every COMPLETED path here is exercised by tests
  only. Fixing it is a voice-agent/transport contract change, out of scope.
  (A superseded-reply COMPLETED branch was added and removed during review
  because it was dead in production and untested. Note that every COMPLETED
  record, including the live-turn path kept here, would share one caveat if
  the marker were produced: it fires when the last frame is buffered, up to
  ~1 s ahead of the speaker.)
* A superseded reply's completed frame is not recorded: it lands in the
  superseded snapshot, which only a stop reads.
* With A, then "stop" B, then C before B's Stage 2, B's stop for A is
  dropped as stale.
* An ordinary barge-in (`confirmed_user_speech`) flushes the playing reply's
  audio but does not cut its history row.
* One superseded slot: a proactive turn that starts while A still plays takes
  it, so a later "stop" cuts nothing for A.
* A stop at offset 0, or at or past the end without `completed`, records no
  terminal outcome.
* A completed frame that arrives before the reply finished generating
  (none arrives live, see the first item) can be followed by a CANCELLED
  from replacement.
* A fully generated reply whose flow is cancelled while it waits for the
  final `_turn_state_lock` section is not stored and is recorded CANCELLED.
* A user turn whose generator raised after committing its intent (the
  fallback line spoken, its text empty) can be recorded CANCELLED by a later
  stop of a proactive turn.
* A chat.input redelivered with the same turn id becomes its own
  superseded turn, with a snapshot taken at redelivery: a Stage 2 stop
  addressed to it cuts the first delivery's row at that stale snapshot
  offset, and a startle for it cuts nothing.
* A startle while a new user turn is in its pacing sleep cancels that turn
  before its user row is logged.
* A startle in that window also no longer cuts the reply still playing: the
  stop is addressed to the new turn, whose audio has not started, and the
  voice agent does not stop the old reply for it either.

## Not solved here

V-2 (the self-correction retry is cancelled by its own `audio.stop`) needs
flush-without-abort semantics in the voice agent; see `07-future-research.md` §5.

## Tests

`tests/test_brain_v2_regressions.py`:
`test_stage2_signal_targets_the_interrupted_reply[stop|resume]`,
`test_stage2_falls_back_to_own_turn_without_interrupted_id`.

`tests/test_barge_in_real_flow.py` drives the real `_on_chat_input` flow with
a scripted core (proactive turns honour their delay; a script can pause after
committing its intent; pacing is settable) and a history double implementing
every store contract the brain has used, so a run against older brain code
writes what that code really would have. The id contract is also tested on
the real `ConversationHistoryStore` (SQLite) row by row, with two identical
rows. Most tests that guard a fix fail on the commit before it on history
or record contents; two (`..._stuck_insert_...`, `..._real_store_...`) fail
on b993dd8 on the missing constant and keyword, and their behaviour is
pinned by the mutations `M8` and `M11` instead.

`tests/test_barge_in_truncation.py`, `tests/test_brain_agent_turn_state_lock.py`
and `tests/test_embodied_feedback.py` set up a stored reply the way the turn
flow leaves it (owner, row id), so they exercise the production path.

**Mutation check, reproducible:** `python scripts/barge_in_mutations.py`
(run it with the interpreter that has the backend's dev requirements)
applies each of 38 mutations of these gates and the store SQL to a temporary
copy of `backend/` and runs the barge-in modules against each (no `-x`,
10-minute timeout). The unmutated copy runs first and must pass, or the
script exits 2 with no verdict. `classify()` counts a mutant as killed only
when tests fail; a collection or import error, an erroring test, or a hang
is an ERROR, never a kill. `tests/test_barge_in_mutation_patterns.py` pins
`classify()`, pins the runner the module actually binds (no `-x`, timeout
passed, a hang returns no verdict; a stale duplicate definition once
shadowed it), and fails on every commit where a mutation pattern no longer
matches the code. 33 are
killed. The 5 survivors are listed in the script as equivalent, each
with its reason. The script exits non-zero on any other survivor or error.
