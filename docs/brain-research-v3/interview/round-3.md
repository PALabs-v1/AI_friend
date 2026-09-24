# Interview Round 3 — Affect

Gates workstream W2 (register A-1, currently a BLOCKER-severity finding). Verified directly this session: `pipeline.py:865` sets the appraisal input to the agent's own current mood, never the user's expressed sentiment. Mood is currently mathematically incapable of being moved by what the user says on the synchronous path — it can only drift from its own prior value, plus a slow background System-2 LLM nudge that runs on a separate timer.

Per DR-002: this isn't "should the chatbot react to sentiment" — it's whether something with its own mind should be *affected* by what's said to it the way a person would be, and how much.

## Q1. Should the user's words move mood synchronously, at all?

**What exists**: no — confirmed by direct trace (`00-current-architecture.md`). ADR-002 Part 2 (proposed, unshipped) suggests routing the appraisal inputs from the ToM `inferred_valence` estimator instead, gated on the Phase 4 GPU experiment showing r≥0.8, sign agreement ≥0.9.

**Why it matters**: if yes, the *strength* and *decay rate* of that effect are the two knobs that determine whether the agent feels responsive (good) or whiplash-y/manipulable by a single sentence (the exact failure V1's weight-learning caused via a different mechanism — mood saturating at +1.0 for 143/200 turns).

## Q2. Emotion, mood, and relationship sentiment — one PAD state, or three distinct timescales?

**What exists**: one PAD state today (`mood`/`energy`/`dominance` on `AgentState`), decaying toward a baseline every tick. No separate "relationship sentiment" variable — the closest thing is `trust`/`attachment`, which are separate from affect entirely.

**Why it matters**: the master prompt explicitly distinguishes emotion (momentary), mood (longer), and relationship state (long-term) as a possible three-tier model. Collapsing them into one PAD value (today's reality) means a single bad exchange and a genuinely soured long-term relationship look identical in the state model — same variable, same decay curve.

## Q3. Can one message create a lasting effect, or does lasting change require a pattern?

**What exists**: no distinction — there's no mechanism today that could create either kind of effect from user words at all (per Q1). This question is about what to build once Q1 is answered yes.

## Q4. Now that baseline mood can evolve (DR-004), what is it an anchor *of*?

**What exists**: today baseline is fixed at persona-authoring time and momentary mood decays toward it every tick (`agent_state.py`'s tick handler). DR-004 already decided baseline may drift — this question is about what governs that drift, at the concept level (the actual rate/mechanism is Round 6's job): is baseline a slow-moving "personality set point" that only shifts from sustained, weeks-long patterns (many similar interactions, not one), or something that can move faster than that?
