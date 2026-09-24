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
`_on_audio_stop` accepts a stop addressed to either the active or the
superseded turn, so cancellation and truncation of the heard portion still happen.

## Not solved here

V-2 (the self-correction retry is cancelled by its own `audio.stop`) needs
flush-without-abort semantics in the voice agent; see `07-future-research.md` §5.

## Tests

`test_stage2_signal_targets_the_interrupted_reply[stop|resume]`,
`test_stage2_falls_back_to_own_turn_without_interrupted_id`,
`test_brain_honours_stop_addressed_to_the_superseded_reply`,
`test_brain_still_ignores_a_genuinely_stale_stop`. The first and third fail
on the pre-fix code.
