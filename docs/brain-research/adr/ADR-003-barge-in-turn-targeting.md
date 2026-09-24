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
reply (`_SupersededReply`: text, playback progress, intent, history state),
playback progress for that turn keeps updating the snapshot, and an accepted
stop truncates the snapshot to what was heard without cancelling the current
turn. The superseded reply's generation had already ended: it finished, or
`_replace_active_generation` cancelled it when the new utterance arrived.

History is written to the right row: a reply whose generation was cancelled
never reached `log_message`, so its heard portion is appended rather than
overwriting "the last assistant message" (which was the previous turn's
reply; a pre-existing defect found here). A logged reply is rewritten only
after its spawned insert has landed. A turn cancelled by the next utterance
gets exactly one terminal outcome record (CANCELLED), not a second TRUNCATED.

## Not solved here

V-2 (the self-correction retry is cancelled by its own `audio.stop`) needs
flush-without-abort semantics in the voice agent; see `07-future-research.md` §5.

## Tests

`test_stage2_signal_targets_the_interrupted_reply[stop|resume]`,
`test_stage2_falls_back_to_own_turn_without_interrupted_id`,
and in `tests/test_brain_v2_regressions.py`:
`test_superseded_stop_truncates_the_playing_reply_to_what_was_heard`,
`test_superseded_progress_does_not_leak_into_the_new_turn`,
`test_superseded_turn_is_honoured_once`,
`test_brain_still_ignores_a_genuinely_stale_stop`,
`test_brain_rejects_non_command_stop_for_the_superseded_turn`,
`test_stop_for_the_active_turn_still_cancels_and_truncates`,
`test_truncating_an_unlogged_reply_appends_instead_of_overwriting_the_previous_one`,
`test_truncation_waits_for_the_pending_history_insert`,
`test_replaced_turn_gets_one_terminal_record_not_two`.
All but the active-turn test fail on the code before the second review's fixes.
