# Interview Round 7 — Executive Control and Reflexes

## Q1. Latency budgets per path

**What's actually measured so far**: SQLite memory retrieval at 5k memories, p50/p95: 3.9/6.6 ms (V2's own benchmark). Microbenchmarks of isolated functions (appraisal, decision-tree walk, arbitration) run in the 0.6-2µs range — irrelevant at the scale that matters, since the real cost is the LLM calls and any I/O around them, not these functions. Nothing has measured true end-to-end pipeline latency (turn received → first audio byte) yet — that's part of Phase 3's baseline.

**My proposal, to confirm or correct** (industry-typical bands for a voice agent, not yet validated against this codebase's real numbers):
- **Reflex** (deterministic responses, barge-in stop targeting): <50ms — no LLM, no network I/O beyond local NATS.
- **Interactive** (the main turn: perception → appraisal → decision → first token of the streamed response): time-to-first-audio-byte target 300-500ms — this is the number that determines whether the agent feels "alive" in conversation versus laggy.
- **Deliberative** (System 2 semantic appraisal, self-correction retry): can trail the turn by a few seconds without being noticed, since it doesn't block the spoken response.
- **Background** (reflection, consolidation, ACT-R decay): no hard budget, but must never delay a foreground turn — this is what `BackgroundScheduler`'s preempt/resume exists for, and Phase 8/9 (scale and chaos testing) will verify it actually holds under load.

## Q2. Should regulation ever be able to win over immediately responding?

**What exists**: `urgency` is set to ≥0.65 on every single user turn (register A-6), which makes "the system chooses to pause/regulate instead of speaking" structurally impossible today — the arbitration always resolves in favor of speaking regardless of what regulation logic exists.

**Why it matters**: per DR-002, a mind with genuine internal state plausibly sometimes needs a beat before responding (processing something surprising or emotionally significant) rather than always replying instantly.

## Q3. What should require an LLM call, beyond what's already reflex-routed?

**What exists**: reflex-routed today: boundary refusals, backchannels, simple greetings, barge-in targeting. Everything else (intent classification, ToM, response generation, reflection) goes through an LLM. This question is really: is there anything currently LLM-routed that should become deterministic, or vice versa?

## Q4. Should "wanting to say something" (DR-019's self-initiated thought) ever override normal turn-taking, or is not-interrupting-the-user an absolute reflex-level rule regardless of internal urge?

**What exists**: proactive turns already compete for a single "active generation" slot with user turns (`brain_agent.py`), but there's no notion of "this self-initiated thought is so significant it should interrupt" — proactive behavior today is purely opportunistic (only speaks when nothing else is happening).
