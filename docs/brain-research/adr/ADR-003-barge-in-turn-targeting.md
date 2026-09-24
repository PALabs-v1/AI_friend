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

History rules, each from a review finding (01-problems.md, R2/R3):

* **Only the reply's own row is rewritten.** The cut calls
  `update_last_assistant_message(heard, expected=full_reply)`, which rewrites
  the newest assistant row still holding exactly that reply, or nothing.
  Unguarded, "the last assistant message" was the previous turn's reply when
  this one was never stored (cancelled mid-generation, or an insert the store
  swallowed), or a newer reply. The rewrite waits for the reply's own spawned
  insert first.
* **Nothing is appended.** A reply cancelled before it was stored gets no
  row: appending its heard part landed after the user's "stop", where it
  read as the answer to "stop". Consequence, accepted: the words the user
  heard from a reply cut off mid-generation are not in history.
* **The text must belong to the stopped turn.** `last_assistant_response`
  is reset only by user turns, so while a proactive (subconscious) turn
  speaks it still holds the previous user reply. `_reply_turn_id` records
  whose text it is; a stop for another turn cuts nothing.
* **A reply is resolved once.** A facial startle publishes a stop for the
  active turn on every startle, even while idle; after the first cut,
  later stops neither rewrite nor record it again (`_reply_resolved`).
* A turn cancelled by the next utterance gets exactly one terminal outcome
  record (CANCELLED), not a second TRUNCATED.

Known, pre-existing, not changed here: with three utterances in quick
succession (A, then "stop" B, then C before B's Stage 2), B's stop for A is
dropped as stale, and `_replace_active_generation` can record CANCELLED for
A's intent although A had finished generating.

## Not solved here

V-2 (the self-correction retry is cancelled by its own `audio.stop`) needs
flush-without-abort semantics in the voice agent; see `07-future-research.md` §5.

## Tests

`tests/test_brain_v2_regressions.py`:
`test_stage2_signal_targets_the_interrupted_reply[stop|resume]`,
`test_stage2_falls_back_to_own_turn_without_interrupted_id`.

`tests/test_barge_in_real_flow.py` drives the real `_on_chat_input` flow with
a scripted core and a history store double with the real store's semantics;
each test names the finding it pins. The three review-3 history defects, the
failed-insert overwrite and the append-dimension case fail on the code the
third review judged (f39c59f); the finished-reply cut fails on the code the
second review judged (the reply was never cut). The store's content guard is
tested against the real `ConversationHistoryStore` on SQLite.
