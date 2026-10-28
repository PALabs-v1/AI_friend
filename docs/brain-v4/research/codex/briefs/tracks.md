# Track-specific briefs (each was appended to common.md)

Launched 2026-09-26 ~20:22 IST as three detached runs:
`codex --search exec --skip-git-repo-check -s workspace-write -c sandbox_workspace_write.network_access=true -C ~/Projects/PALabs/brain-v4/research/codex -o <last-message file> - < <common.md + track brief>`

---

## Your track: R4, perceived realism and how to measure it

Write your report to `R4-realism.md` in your working directory.

Questions:
1. How human does current AI already seem to people, and under what
   conditions does that break? Short-form evidence (Turing-test-style studies:
   format, duration, judges, rates) vs long-horizon evidence (weeks/months of
   use of companion or social agents: what users report, what gives the agent
   away over time, what makes attachment form or collapse).
2. What exactly makes a conversational agent seem to have a mind of its own
   to a person over months? Look for empirical work on: memory continuity,
   consistency of identity and opinions, emotional responsiveness and
   persistence, initiative, being wrong and admitting it, mind perception
   (e.g. agency vs experience dimensions), anthropomorphism, the uncanny
   valley for minds or voices.
3. How should "perceived realism" be measured as an acceptance test? Survey
   existing evaluation designs (believability evaluations, three-party Turing
   tests, blind transcript rating, long-horizon diary or longitudinal user
   studies, validated questionnaires for mind perception, social presence,
   anthropomorphism or relationship quality). For each: what it measures,
   validity evidence, cost, and failure modes (judges primed, novelty effects,
   the persona-prompt effect). Then propose a concrete, pre-registerable eval
   design for this project that could be run repeatedly (including an
   automatable proxy tier and a human-rater tier), with sample sizes and pass
   thresholds justified from the literature.
4. Risks: ethical and product risks of a mind built to be perceived as real
   (dependence, deception, disclosure norms), with evidence, as they would
   constrain the eval and the product.

---

## Your track: R5, a mind without a body (affect, drives, inner life, identity, consciousness)

Write your report to `R5-mind.md` in your working directory.

Questions:
1. Emotion without a body. What do the main theories say about how much
   emotion depends on the body (interoception, homeostasis, allostasis,
   somatic markers, constructed emotion, appraisal)? What does that imply for
   a disembodied agent: can functional analogs (internal needs such as energy,
   social contact, rest, competence, with homeostatic regulation) give emotions
   real stakes, and is there any artificial-homeostasis or artificial-drive
   work (in robotics, reinforcement learning or cognitive architectures) that
   shows measurable behavioral benefit? Numbers where they exist.
2. Inner life. What is the evidence on spontaneous thought and
   mind-wandering in humans (how much, what content, what function), and on
   artificial agents that think, reflect or plan between interactions? What
   makes such inner activity matter to behavior rather than being decoration?
3. Identity over time. Human personality stability and change (rank-order
   and mean-level change, time scales, what causes change). Evidence on LLM
   persona consistency and drift over long interactions. What does an agent
   that "changes over time but stays the same someone" need?
4. Learning from experience. LLM weights do not learn online; what are the
   known approaches (external memory, continual learning, fine-tuning on
   experience, catastrophic forgetting mitigations), and what do they get and
   not get, at what cost, on this project's hardware?
5. Consciousness and "actual depth". What does the current science of
   consciousness say can and cannot be established about an AI system (e.g.
   indicator-property approaches, and their critics)? Which parts of "real,
   thinking, not just mere code" are testable behavior and which are not
   testable at all? Be precise; do not overclaim in either direction.
6. Whole-brain approaches. How far are connectome-based or whole-brain
   simulation efforts from producing behavior at any scale (worm, fly, mouse),
   and why does or doesn't that matter for this project?

---

## Your track: R6, voice and vision realism

Write your report to `R6-senses.md` in your working directory.

The repo is at `/Users/aniketsaha/.claude-worktrees/AI_friend-2527627756/10f9894b`
(read-only). Its current voice path: STT and voice agents in Rust
(`backend/crates/stt-agent`, `backend/crates/voice-agent`), a LiveKit
transport (`backend/app/agents/transport_agent.py`), barge-in handling, and a
playback lifecycle contract added in V3's W4. Vision:
`backend/app/vision/agent.py`. Read enough to know what exists.

Questions:
1. Conversational timing. Human turn-taking numbers (gap and overlap
   distributions across languages, backchannels, interruption handling), and
   what listeners perceive as unnatural. How close do current systems get:
   cascaded STT->LLM->TTS pipelines vs full-duplex speech models (latency,
   overlap handling, backchannels), with measured numbers.
2. Expressive voice. Evidence on how prosody conveys emotion and how
   accurately listeners decode it; state of the art in controllable,
   emotional TTS and voice cloning, including what runs locally on 8 GB VRAM
   or on an Apple M-series Mac, with latency numbers. Where does synthetic
   voice still get detected as synthetic, and by whom?
3. Vision that feels like seeing. What does it take for an agent's vision to
   feel like shared perception in conversation: noticing change, referencing
   what it saw earlier (visual memory), gaze and joint attention, reading the
   user's facial affect (and how reliable automatic facial-expression affect
   reading actually is, including the scientific dispute over facial
   expressions as emotion signals). What local vision-language models are
   feasible on this hardware, at what latency and quality?
4. Coupling to the mind. Evidence that coupling voice and vision to internal
   state (mood driving prosody, attention driving what is noticed, memory of
   what was seen) changes how real or likeable an agent seems, vs decoupled
   expression. Include avatar/embodied-conversational-agent literature where
   relevant (without a physical body).
5. Hardware and cost reality: for each capability, what can run on this
   project's two machines now, what needs a bigger GPU, and what currently
   needs frontier-scale models.
