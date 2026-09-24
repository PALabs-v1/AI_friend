# Interview Round 5 — Autonomy and Proactive Behavior

Per DR-002, this round settles where "has its own mind" becomes actual behavior, not just philosophy. Gates W9 and touches the subconscious agent's currently-unconsumed monologue/dream mechanisms.

## Q1. What should trigger unprompted initiation?

**What exists**: a flat gate — idle ≥7200s (2 hours), cooldown 3600s (1 hour) since the last attempt, energy ≥0.2, and a coin-flip probability ≥0.5 (`agent_state.py:1775`). Nothing about *content* — an urgent unresolved thought and a trivial one are equally likely to surface, because nothing about importance factors in.

**Why it matters**: per DR-002, the reason to speak unprompted isn't purely "is this useful to the user" — it can include "I've been thinking about something." But that still needs *some* gate, or the agent talks constantly.

## Q2. How should repeated unresolved thoughts resurface without becoming annoying?

**What exists**: no model at all. `GoalRecord`/`review_due_goals` (`goals.py`) are fully implemented but have zero production callers — nothing currently tracks "I brought this up before, did they respond, should I bring it up again."

## Q3. Should the same idle-time gate apply uniformly, or should proactive behavior have some sense of context (time of day, what's happening)?

**What exists**: idle-time is the only signal. No time-of-day awareness, no sense of "is this a bad moment."

## Q4. Given DR-002 — should the agent ever initiate about something that's purely its own (a thought, an observation, something it's "interested in") rather than something it judges useful to the user specifically?

**What exists**: `SubconsciousEngine.evaluate_and_think` already generates a candidate thought via one LLM call, but nothing distinguishes "this is useful to bring up" from "this is something I want to say" — there's no such category today.
