# DR-026: A sufficiently significant self-initiated thought can interrupt

**Round**: 7 (executive control)
**Date**: 2026-09-24
**Question**: Should DR-019's self-initiated thoughts ever interrupt an ongoing user turn?

**Decision**: yes, if significant enough — rare, but not impossible, the way a person might interrupt if something urgent occurs to them. Not the default (DR-019 already established self-initiated thought as opportunistic/stricter-gated); this is the exception case.

**Consequence for W5 (barge-in) and W9 (proactive)**: this is a genuinely new interruption source that ADR-003's barge-in model never had to consider — today, the only things that interrupt an active turn are user speech and (implicitly) higher-priority system events, never the agent's own initiative. W5's state-machine work (already planned to cover chat.input/audio.stop/progress/lifecycle/proactive-turn interleavings) needs an explicit "self-initiated high-significance interrupt" event type in its interleaving matrix, gated by the same significance threshold DR-025 introduces. This raises the bar for W5's property tests: they now need to verify the *rare* interrupt case behaves correctly (doesn't corrupt turn state, doesn't happen when the user is mid-utterance in a way that would be genuinely disruptive) in addition to the already-planned ordinary interleavings.
