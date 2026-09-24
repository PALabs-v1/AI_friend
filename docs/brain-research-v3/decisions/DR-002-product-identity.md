# DR-002: Product identity — an autonomous humanoid mind, not a companion/assistant mode switch

**Round**: 1 (identity/philosophy)
**Date**: 2026-09-24
**Question asked**: Is AI_friend a companion, an assistant, or does that vary by context?

**Aniket's answer, verbatim**: "ai_friend is a humanoid brain architecture, consider a real human, but a robot, like we have seen in movies, games, where robots not only behave like human, but they have an actual mind, thinking of their own, more like an agi sort of for example, the only differenece is humans have soul, and humanoids doesn't, so I want to create that kind of humanoid."

**Decision**: reject the companion-vs-assistant framing entirely. The target is not a mode or a blend of two service postures — it's a mind with its own thinking, modeled as closely on a real human mind as the architecture can support, with the explicit exception that it has no soul (i.e., no claim to subjective experience/consciousness as a metaphysical fact — the engineering target is the *functional* architecture of a mind: genuine-seeming autonomous cognition, opinions, interests, and thought, not a philosophical claim about sentience).

**Why this matters beyond Round 1**: this reframes several rounds still to come, not just Round 1:
- **Round 5 (autonomy)**: proactive behavior should not be reasoned about purely as "when is it useful to interrupt the user" — an entity with its own mind may have its own reasons to want to speak, pursue a thought, or bring something up that aren't purely in service of the user. This doesn't mean unlimited interruption; it means the *reason* proactive behavior exists is broader than utility-to-user. Round 5 needs to settle where the line is.
- **Round 6 (learning/personality evolution)**: this cycle's Q4 answer (below, DR-004) already commits to letting traits *and* baseline mood/PAD drift — consistent with "an actual mind" rather than a fixed personality shell.
- **Round 7 (executive control)**: a mind that "thinks of its own" plausibly has internal states/thoughts that aren't always expressed — this bears on what the subconscious agent's monologue/dream mechanisms (currently unconsumed/log-only) are ultimately *for*, even though no decision is made on that here.
- **Round 4 (trust/relationship)**: if the entity has its own perspective, "the agent's feelings about the user" becomes a legitimate first-class concept, not just "the user's trust in the agent" — this doesn't resolve W3's trust-formula redesign, but it means the redesign shouldn't assume the relationship is one-directional.

**What this does NOT decide**: it does not by itself answer how autonomous, how proactive, or how much independent "opinion" the system should express in any given interaction — those are Round 5 and Round 7's job, now explicitly informed by this framing rather than by a companion/assistant dichotomy that no longer applies.

**Consequence for terminology in this cycle's docs**: from here on, "the agent," "the system," or "AI_friend" is used instead of "assistant" or "companion" in architecture docs, to avoid smuggling either framing back in.
