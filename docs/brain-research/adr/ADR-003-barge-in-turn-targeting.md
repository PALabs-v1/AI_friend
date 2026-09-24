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
  (`update_last_assistant_message(heard, message_id=)`), or nothing if the
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
* **The text must belong to the stopped turn.** `last_assistant_response`
  is reset only by user turns, so while a proactive (subconscious) turn
  speaks it still holds the previous user reply. `_reply_turn_id` records
  whose text it is; a stop for another turn cuts nothing, and a proactive
  turn's completed playback does not record the user reply COMPLETED again.
* **A reply is resolved once** (`_reply_resolved`): its first terminal
  outcome (COMPLETED, TRUNCATED or CANCELLED) ends it. A facial startle
  publishes a stop for the active turn on every startle, even while idle.
* **CANCELLED only when the reply's generation was cut.** Replacing a
  proactive turn, or a turn still in its pacing sleep, after the user reply
  finished generating no longer records that reply CANCELLED
  (`_reply_generating`).

Known and not changed here (pre-existing):
* With A, then "stop" B, then C before B's Stage 2, B's stop for A is
  dropped as stale.
* An ordinary barge-in (`confirmed_user_speech`) flushes the playing reply's
  audio but does not cut its history row.
* One superseded slot: a proactive turn that starts while A still plays takes
  it, so a later "stop" cuts nothing for A.
* A stop at offset 0, or at or past the end without `completed`, records no
  terminal outcome.
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
a scripted core and a history double that mirrors the store's id contract;
the same contract is tested on the real `ConversationHistoryStore` (SQLite).
It also pins the invariants: superseded progress never becomes the new
turn's, a superseded stop is honoured once, a genuinely stale stop neither
cancels nor cuts. The reviewer's three mutations of those invariants each
fail the module. Against earlier commits, some tests fail on the changed
store contract (`message_id=`) rather than on behaviour; the behavioural
evidence for R4-1 is the reviewer's collision repro, which rewrote an older
identical reply on b993dd8 and leaves it intact now (real store, both the
startle and the spoken-stop path).
