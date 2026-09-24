# DR-029: A self-correction retry always finishes

**Round**: 8 (voice/turn-taking)
**Date**: 2026-09-24
**Question**: Should a self-correction retry (register V-2) be protected from interruption?

**Decision**: yes, always finish. Once started, a self-correction completes without being cut off by its own prior stop signal or an unrelated event.

**Consequence for W4**: this is the concrete semantic V-2's `flush` flag needs to implement — `AudioStop` needs to distinguish "stop and let a self-correction proceed" from "stop everything including any in-flight correction." The bug today (an unscoped `audio.stop` from the self-correction path cancels its own retry) is now clearly a bug, not an ambiguous case — DR-029 gives W4 a definite target behavior to test against.

**Open sub-question for W4's implementation, not decided here**: what happens if the *user* interrupts during a self-correction (as opposed to the correction being cancelled by its own stale stop signal)? DR-029's protection is specifically against the mechanism's own stop signal, per the question as asked — whether genuine new user speech can still interrupt a self-correction in progress is closer to DR-028's territory (ordinary barge-in should cut off what's playing) and should be resolved as "yes, real user speech still interrupts; only the mechanism's own internal stop is what gets suppressed" unless W4 surfaces a reason to ask this explicitly.
