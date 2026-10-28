# Track-specific briefs (each is appended to common.md)

Four tracks, each sent to Codex and to Antigravity, plus a Claude pass written
before any report is read. Report file names: `T1-structure.md`,
`T2-field-2026.md`, `T3-identity.md`, `T4-measure.md`, one set per tool.

---

## Track T1: layers, loops and what "closed" means

Write your report to `T1-structure.md` in your working directory.

1. How do the leading cognitive architectures (for example Soar, ACT-R, LIDA,
   CLARION, global-workspace implementations, BDI systems, predictive-
   processing models) organize reactive, deliberative and reflective
   processing? Where do they put memory, affect or motivation, and identity or
   self-model? Which of them have been implemented and evaluated, and what did
   the evaluations show?
2. How do current LLM-based agent systems (memory-augmented and long-running
   agents, companion systems, simulated-society agents, and any newer designs
   from 2025 and 2026) organize the same functions? What do they leave out?
3. Compare the two frames in the shared context, layers with identity across
   them and the perceive-to-reflect loop. Which carves a mind better for an
   engineered system, why, and are there better frames? Say what each hides.
4. What makes a loop "closed" in a testable sense: internal state at time t
   changes behavior at time t+k in a way that can be traced, intervened on and
   measured? Survey causal-intervention and ablation methods used to show
   that an internal state actually drives behavior (and not just co-occurs).
5. Read the current system (`docs/brain-research-v3/00-current-architecture.md`
   and the code it names). Which layers and loops exist, which are open
   (written but never read, or read but never written), and which are
   decorative? Cite files.

---

## Track T2: what changed in the field in 2026

Write your report to `T2-field-2026.md` in your working directory.

1. What changed since early 2026 in: capable small and mid-size models that
   can run locally; long-term memory and agent-memory research; companion and
   persona systems; evaluation of long-horizon agents; work on machine
   consciousness indicators and the surrounding debate (state the positions;
   do not take a side)?
2. Which of those changes would make a design choice in a memory-first, local-
   model, affect-and-identity architecture obsolete, cheaper, or newly
   possible? Be specific: name the choice and the change.
3. What are the credible failure modes and harms reported for companion and
   persona-driven systems (dependence, sycophancy, drift, deception), with
   evidence and conditions, that a design should respect from the start?
4. What would you want to know before committing to any architecture in this
   space that nobody has published yet?

---

## Track T3: identity, continuity and portability

Write your report to `T3-identity.md` in your working directory.

1. What is the best-supported account of what makes an agent "the same one"
   over time, in psychology and philosophy of personal identity (for example
   psychological continuity and narrative accounts), taken as engineering
   constraints and not as metaphysics? Which state would an artificial mind
   have to keep to count as continuous: memories, values, style, self-model,
   relationships?
2. How stable are LLM-driven personas in practice? Evidence on persona drift
   over long conversations, across sessions, and across model swaps, and on
   what reduces drift. Include methods for measuring identity stability.
3. Portability. Is it technically sound to treat identity and memory as a
   layer that is separate from the language model running it, so the same
   mind can run on a different model? What is lost or changed on a swap, how
   would that be measured, and what should the system truthfully say about
   itself afterward (the owner's rule is that it always discloses being an AI
   and never claims a human past)?
4. Two questions raised by a work of fiction the owner has been watching, to
   be treated as design questions and not as evidence: (a) can an identity and
   memory be separated from the body or model that runs them; (b) what counts
   as the same mind after being moved. Answer them on the evidence, and say
   where the fiction's premise has no support.
5. Read the current system: where does identity live now (persona files,
   memory, learned weights, drift rules)? What would break in a model swap
   today? Cite files.

---

## Track T4: measuring "how close"

Write your report to `T4-measure.md` in your working directory.

1. The owner wants a profile of measures, not a single score, for how close a
   closed loop of internal state gets to the organization of a mind. Propose
   a profile: which dimensions (for example memory truth, affect dynamics,
   drives, initiative, identity stability, state-in-words coupling, timing),
   and for each, a measure that can be run automatically and one that needs
   human judgment.
2. Survey existing evaluations: believability and Turing-style tests,
   mind-perception and social-presence questionnaires, long-horizon memory
   benchmarks, persona-consistency benchmarks, affect and emotion-dynamics
   benchmarks, consciousness-indicator checklists. For each: what it
   measures, validity evidence, cost, known failure modes (priming, novelty
   effects, the persona-prompt effect, judge bias).
3. How do you show that a measured behavior is caused by an internal state of
   the system and not by the prompt or the model alone? Propose ablation and
   intervention designs that fit a small research budget.
4. How would you pre-register an acceptance rule for "closer", with sample
   sizes and thresholds justified from the literature, and what would count as
   a result that says the architecture does not help?
5. What cannot be measured at all, and how should a report say so without
   overclaiming?
