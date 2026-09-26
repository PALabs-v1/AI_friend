# DR-016: Initiation is importance-weighted, not a flat gate

**Round**: 5 (autonomy/proactive)
**Date**: 2026-09-24
**Question**: What should gate unprompted initiation beyond today's flat idle-time + cooldown + coin-flip?

**Decision**: idle-time/cooldown remain as a floor, but the probability/urgency of actually speaking scales with the thought's importance or emotional significance — a genuinely notable thought can break through sooner; a trivial one waits longer or never surfaces unprompted.

**Consequence for W9**: `check_proactive_eligibility` (`agent_state.py:1775`) needs an importance input from whatever candidate thought is being evaluated — today's gate runs *before* any thought is generated (pure timer check), so this requires either generating a candidate first and then gating on its importance, or a cheap pre-classification step. `SubconsciousEngine.evaluate_and_think`'s existing LLM call is the natural place to also emit an importance/urgency score, avoiding a second LLM call.

**Addendum, 2026-09-27 (the measured rate, confirmed)**: W9 implemented this.
On the full wave-A panel (`13-wave-a-results.md`, run `v3waveA-B`) it gives,
at one year, 1.26 initiations a day, 88% useful, 0.15 annoying a day and 0
cooldown violations; in the first week, about one initiation every eight
days. That is half the absolute useful outreach of V2's no-broadcast control
(1.11 against 2.25 a day) for about 1% of its annoyance. Asked whether to
keep this rate, loosen the gate for the first weeks only, or loosen it
overall, Aniket chose: **keep this rate**. Any future change to W9's
importance gate is measured against `v3waveA-B`.
