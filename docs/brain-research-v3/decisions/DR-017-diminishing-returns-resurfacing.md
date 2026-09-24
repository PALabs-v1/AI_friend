# DR-017: Unresolved thoughts resurface with diminishing returns

**Round**: 5 (autonomy/proactive)
**Date**: 2026-09-24
**Question**: How should a repeatedly-unresolved thought resurface without becoming annoying?

**Decision**: each time a thought is raised and not acted on or dismissed, the probability of raising it again drops, eventually to zero — the way a person stops bringing something up after being ignored a few times.

**Consequence for W9**: this wires the currently-dead `GoalRecord`/`review_due_goals` (`goals.py`) into an actual production loop for the first time. Needs: a record of "this thought was raised, here's the outcome" (acted on / dismissed / ignored), a decay function on re-raise probability keyed to that history, and a floor (fully dropped) rather than an asymptote that never quite reaches zero. This is the annoyance model the master prompt asks for — simple, concrete, buildable without a separate "annoyance risk" abstraction.
