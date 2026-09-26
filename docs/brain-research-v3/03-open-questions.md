# Open Questions — Index

Every decision below depends on product intent or humanoid-behavior philosophy, not on something derivable from the code or an experiment. Where a question *can* be settled by code or experiment instead, it's a workstream item in `05-research-plan.md`, not here. Full explanation, alternatives and consequences for each question are written up in `interview/round-N.md` immediately before that round is asked — this index exists so the full scope is visible up front, per the master prompt's instruction not to uncover ambiguities piecemeal.

Rounds run in this order; each is a separate conversation turn or set of turns, never all at once.

## Round 1 — Identity and product philosophy
- What belongs in `IMMUTABLE_CORE` (currently: Honesty, Privacy, two boundaries — `persona/profile.py:136`)? Is that list complete, too broad, too narrow?
- Should behavioral adaptation differ per user (household with multiple people), or is there exactly one relationship to model?
- Is AI_friend a companion, an assistant, or does that vary by context?
- Which CONSTITUTIONAL fields (baseline PAD, traits, speech patterns) should ever be able to change, even slowly, versus fixed at persona authoring time?

## Round 2 — Memory and forgetting
- When a fact changes (M-5), should the old value become historically-queryable-only, or should it actively resist being retrieved for present-tense questions? What signals "this is now false" vs. "this is now historical"?
- What counts as a correction ("no, Thursday not Tuesday") vs. a genuine belief change over time ("I used to like cappuccino, now I don't")? Does the distinction matter for how they're stored?
- Should contradictory memories be allowed to coexist indefinitely, or must one always supersede?
- What should happen to trivial episodic memories ("what did I eat yesterday") over a long horizon — decay in retrievability, archive, or eventually delete?
- Do emotionally significant memories deserve protection from decay that trivial ones don't?

## Round 3 — Affect
- Should the user's words move the agent's mood at all (A-1 is currently a hard no on the synchronous path)? If yes, how strongly, and how fast should that effect decay?
- Is there a meaningful difference between momentary emotion, longer mood, and long-term relationship sentiment, or is one PAD state enough?
- Can a single hostile or a single very positive message create a lasting effect, or should lasting change require a pattern across many turns?
- What should the personality baseline (`baseline_mood` etc.) actually mean — a reset target, a slow anchor, or something else?

## Round 4 — Trust and relationship
- Is "trust" one number or several (the current code tracks benevolence/competence/integrity but averages them into one)? Does familiarity, affinity, or emotional closeness need to exist as its own variable?
- Should hostility be able to *lower* trust at all today it cannot (A-3) — and if so, how sharply, and can it recover?
- Should reliability (did the agent do what it said) move trust differently than emotional warmth does?
- Is trust global, or should it (eventually, multi-person) be tracked per person?

## Round 5 — Autonomy and proactive behavior
- What should count as "important enough" for the agent to initiate conversation unprompted? The current gate is a flat idle-time + cooldown + probability check (`agent_state.py:1775`) with no notion of the content's importance.
- How should repeated unresolved goals resurface without becoming annoying? Is there a model for "annoyance risk" you want, even a simple one?
- Should proactive behavior differ by time of day / context, or is idle-time the only signal that should matter?

## Round 6 — Learning and personality evolution
- Persona evolution is currently proposed but never approved in production (`LearningReviewQueue.approve` has no caller) — is that the intended state (evolution effectively off), or should there be an approval path (auto, human-in-the-loop, or something else)?
- How fast should adaptive traits (`speaking_style`, up to 5 `adaptive_traits`) be allowed to drift, if at all?
- Should learned behavior differ per person, or is there one persona regardless of who's talking?

## Round 7 — Executive control and reflexes
- What are the actual latency budgets for each path (reflex / interactive / deliberative / background)? I'll propose concrete numbers grounded in what's measured; you confirm or correct them.
- Should regulation (calming/pausing) ever be able to win over immediately responding, given `urgency` is currently pinned at ≥0.65 on every user turn (register A-6), making that outcome structurally impossible today?
- What should require an LLM call versus stay strictly deterministic, beyond what's already reflex-routed?

## Round 8 — Voice and turn-taking
- When a reply is interrupted mid-sentence, what should count as "what the user heard" for history purposes — word-level truncation (current behavior for confirmed barge-in when it does cut history), or something coarser/finer?
- Should an ordinary (non-speculative) barge-in cut the history row the way a speculative one does? Today it doesn't.
- What should happen to a self-correction retry that gets interrupted by its own stop (V-2) — should it always be allowed to finish, or is that also interruptible?
- Priority when a proactive turn and a user turn collide: should the user always win?

## Round 9 — Boundaries, privacy, and security
- Beyond the two current `IMMUTABLE_CORE` boundaries, what should never adapt regardless of relationship state or emotional intensity?
- Should any affect state be strong enough to override a safety/boundary response, or is that always a hard no?
- Multi-person households: what's the policy on cross-person memory/disclosure leakage, if that's ever in scope?

## Round 10 — Long-horizon edge cases
- Decades of simulated history: should very old, never-recalled memories eventually become effectively unreachable, or must everything stay reachable in principle?
- How should the agent handle a user who is inconsistent on purpose (testing it) versus one who's genuinely forgetful?
- Are there topics (grief, loss, in a synthetic-only sense per the master prompt's guardrail) that need special handling in the benchmark design itself, separate from runtime behavior?

---

Each round's brief (`interview/round-N.md`) expands every bullet above with: what exists today (file:line), what V2 changed if relevant, why the decision matters, the realistic alternatives and their consequences — then asks. Answers are recorded verbatim in `decisions/DR-NNN-<slug>.md` and indexed in `decisions/INDEX.md`.
