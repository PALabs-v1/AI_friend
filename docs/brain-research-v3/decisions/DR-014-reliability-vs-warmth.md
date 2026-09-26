# DR-014: Reliability and emotional warmth move trust through separate mechanisms

**Round**: 4 (trust/relationship)
**Date**: 2026-09-24
**Question**: Should reliability (follow-through, correctness) move trust differently than emotional warmth?

**Decision**: yes, separate mechanisms. Competence/reliability trust comes from actual outcomes — promises kept or broken, correct vs. wrong information, follow-through on commitments. Benevolence/warmth trust comes from the emotional tenor of the relationship (feeds from DR-013's hostility handling, and from the positive side of the same mechanism). They can diverge: reliable but cold, or warm but flaky, and the system should be able to represent that.

**Consequence for W3**: `trust_competence`'s current formula (`0.6·G + 0.4·R`, where G is the agent's own mood and R is just "a message happened") needs to be replaced with something driven by actual outcome evidence — this is exactly what `PersonModel.update_trust_from_reliance`'s success/failure split already does (`+0.05/+0.02` success, `-0.15/-0.10` failure) and currently has no caller. Wiring it requires the pipeline to actually classify whether a given turn was a "reliability-relevant" event (a promise, a factual claim later found right/wrong, a commitment followed through) — this classification doesn't exist yet and is real new work for W3, not just a wiring exercise like DR-013.
