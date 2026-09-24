# DR-016: Initiation is importance-weighted, not a flat gate

**Round**: 5 (autonomy/proactive)
**Date**: 2026-09-24
**Question**: What should gate unprompted initiation beyond today's flat idle-time + cooldown + coin-flip?

**Decision**: idle-time/cooldown remain as a floor, but the probability/urgency of actually speaking scales with the thought's importance or emotional significance — a genuinely notable thought can break through sooner; a trivial one waits longer or never surfaces unprompted.

**Consequence for W9**: `check_proactive_eligibility` (`agent_state.py:1775`) needs an importance input from whatever candidate thought is being evaluated — today's gate runs *before* any thought is generated (pure timer check), so this requires either generating a candidate first and then gating on its importance, or a cheap pre-classification step. `SubconsciousEngine.evaluate_and_think`'s existing LLM call is the natural place to also emit an importance/urgency score, avoiding a second LLM call.
