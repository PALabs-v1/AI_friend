# 01. What is possible for a mind without a body

Claude's research pass, 2026-09-26. Codex's independent pass (R4, R5, R6) is
in `research/codex/`, and the two are compared in `05-comparison.md`, which
also records every place this document changed because of it. Every source
below is in `verification-log.md` with how it was checked. A claim marked
PARTLY there is used here only for what was actually checked.

## Limit classes

Every "no" or "not yet" below is one of four kinds, because they call for
different responses:

| Class | Meaning | What fixes it |
|---|---|---|
| ENGINEERING | Known how, feasible on this project's hardware, not built yet | Build it |
| RESOURCE | Known how, blocked by model size, compute, data or time | A bigger model or machine, or a deployment choice |
| SCIENCE | Nobody knows how yet | Research; money speeds it up but does not settle it |
| UNKNOWABLE | No accepted test exists, and current science offers no route to one | Nothing now. Record it, do not target it |

## Summary

| # | Capability | Reachable? | Limit class | V3 base |
|---|---|---|---|---|
| A | Seeming human in a short conversation | Yes with a large model, no with a 3B local one | RESOURCE | model choice is per deployment |
| B | Staying coherent for months: memory truth, consistent identity | The mechanisms: yes. That people then experience a coherent someone over months: an untested hypothesis | ENGINEERING (mechanisms) / SCIENCE (the perceived outcome) | W1, W7, W8, W11 |
| C | Making the person feel heard | Yes | ENGINEERING (+ RESOURCE for language quality) | W1, W8, NF-26 |
| D | Modeling the user's mind (theory of mind) | Mostly, at frontier scale | RESOURCE + ENGINEERING | NF-26, W8 |
| E | Emotions that respond to the user and persist | Yes, if an accurate valence estimator exists | RESOURCE (estimator accuracy) + ENGINEERING | W2, NF-12/16/17 |
| F | Emotions with stakes (functional drives) | Energy/rest and uncertainty drives: buildable. Social-need drives: no validated units. Felt version: unknowable | ENGINEERING / SCIENCE / UNKNOWABLE | none yet; NF-18/21/33/34 are the fragments |
| G | An inner life that has consequences | Yes | ENGINEERING | W9, DR-019, subconscious |
| H | Identity that is stable but changes | Yes, with measurable human bands | ENGINEERING | W11, DR-020/021/038 |
| I | Learning from experience inside the model's weights | Partly, with risk | SCIENCE + RESOURCE | memory-based learning today |
| J | Human conversational timing in voice | Partly: latency is reachable, floor management is not solved anywhere | ENGINEERING + RESOURCE | W4, W5, DR-024 |
| K | Emotional, expressive voice | Largely, once affect is valid | ENGINEERING + RESOURCE | NF-36, W2 |
| L | Seeing as shared perception, remembering what was seen | Yes | ENGINEERING | vision agent |
| M | Reading the user's emotion from their face | Weakly at best | SCIENCE | vision reflex channel (implemented; active only when the optional MediaPipe support and its model asset are present) |
| N | Consciousness / felt experience | Cannot be established | UNKNOWABLE | NF-38 (workspace) is the nearest audited mechanism |
| O | Getting there by emulating a brain bottom-up | Not supported by current evidence for this goal | SCIENCE + RESOURCE | not pursued |
| P | Partnership: joint activity toward shared goals | The mechanisms: yes. That it is experienced as a partner, not a tool: untested | ENGINEERING (mechanisms) / SCIENCE (the perceived outcome) | W9 goals and commitments, vision (L), drives (F) |

The short version: almost every *mechanism* a disembodied mind needs
(memory truth, affect the user can move, slow identity change, an inner life
with consequences, human turn timing, shared visual memory) is ENGINEERING,
with two RESOURCE pinch points (the language model and the valence
estimator). Whether building them makes people experience a coherent someone
over months is not established: no study has tested it, and which cues cause
long-term mind attribution is open SCIENCE. So V4's premise is a research
hypothesis that its eval exists to test, not a known result. Two things are
out of reach for anyone today: establishing felt experience, and reading
emotion reliably from faces (`05-comparison.md`).

## A. Seeming human in a short conversation (RESOURCE)

In pre-registered three-party Turing tests (284 participants: 126 UCSD
undergraduates and 158 Prolific workers; 1,023 games after exclusions; median
8 messages over 4.2 minutes), GPT-4.5 prompted to adopt a humanlike persona
was judged to be the human 73% of the time, more often than interrogators
picked the real human it was paired with. LLaMa-3.1-405B with the same prompt
reached 56%. Without the persona prompt the same models fell to 36% (GPT-4.5)
and 38% (LLaMa). GPT-4o (21%) and the 1960s ELIZA baseline (23%) were both
below chance. The authors state the test measures humanlikeness, not
intelligence (Jones & Bergen 2025).

The published version (PNAS, May 2026) adds a third study with 15-minute
conversations: the two persona-prompted models reached 56% and 59%. Longer
exposure already weakens the illusion at a quarter of an hour.

What this means here:
- In that setup (a prompted, three-party, five-minute text test), frontier
  models were judged human at or above chance; at 15 minutes the rates were
  lower. That is a bounded result for those conditions, not a general
  "solved" capability. Even so, the harder, unmeasured problem is the
  long horizon, not the first five minutes.
- It depends heavily on the persona framing, and on model scale.
- This project's local models (3-4B) are far smaller than anything tested
  there. So short-form realism is a RESOURCE question for us, and model
  choice is a per-deployment decision, not a design flaw.
- Five minutes says little about months, and the 15-minute result shows the
  effect shrinking with exposure. Capabilities B through H are about what
  breaks over time.

## B. Staying coherent for months (ENGINEERING mechanisms, SCIENCE outcome)

The strongest evidence that architecture, not just the language model,
drives perceived believability: in Park et al. (2023), 100 evaluators ranked
agent responses for believability (TrueSkill). The full architecture (memory
stream + reflection + planning) scored mu = 29.89. Removing reflection gave
26.88, removing reflection and planning gave 25.64, and removing all three
gave 21.21. Responses written by human crowdworkers role-playing the agents
scored 22.95, below every condition that had memory. Every agent condition
used the same language model; memory, reflection and planning made the
difference.

Park et al. (2024, current arXiv version) built agents of 1,052 real people
from two-hour interviews. On held-out General Social Survey items they reached
83% (interview only), 82% (survey only) and 86% (combined) of the people's own
two-week test-retest consistency, against 74% for agents given only
demographics. (An earlier version of the same paper reported 85%; the numbers
here are from the version fetched on 2026-09-26.) Evidence about someone's
life is what makes an agent behave like that someone.

What this means here: coherence over months is a memory-truth and identity
problem, which is exactly V3's W1 (supersession, M-5), W7 (consolidation,
forgetting), W8 (freshness, abstention) and W11 (self-authored evolution).
What long-horizon evidence exists is qualitative or short:
- A 12-week longitudinal study of 28 companion-bot users found relationship
  depth developing in at least four ways (increasing, decreasing, stable,
  fluctuating), driven by perceived rewards and costs (Skjuve, Følstad &
  Brandtzæg 2023). Codex R4 reports, from the full text, that poor memory
  and generic, scripted replies were among the costs (CODEX-REPORTED; not in
  the abstract).
- A randomized 21-day study (183 people, 10 minutes a day) found no overall
  effect of companion-bot use on social health (Guingrich & Graziano 2025).

No blind, pre-registered study of perceived realism over months was found by
either research pass. So the claim "these mechanisms will make a person
experience a coherent someone over months" is a hypothesis. V4's eval
(`03-realism-eval.md`) is building the measurement that would test it.

## C. Making the person feel heard (ENGINEERING, + RESOURCE for language quality)

De Freitas et al. (arXiv 2024; published in the Journal of Consumer Research,
vol. 52, issue 6, 2025) studied AI companion apps (six studies in the arXiv
version, five in the journal version). Interacting with
an AI companion reduced loneliness on par with interacting with another person,
and more than watching YouTube videos. A one-week longitudinal study showed
consistent reductions. The study on mechanism found that the chatbot's
performance and, especially, whether it made users feel heard explained the
reduction. People also underestimated how much it would help. Each finding
in this paragraph appears in both versions' abstracts; only the study count
and numbering differ.

What this means here: "feeling heard" is the lever users respond to, and it
is built from accurate memory of what they said (W1), noticing what matters
to them (W8, NF-26) and responding in a way that shows both.

The risk side is thin evidence, not a finding. Guingrich & Graziano's
randomized study found no overall effect on social health over 21 days; the
bot's reported effect on people's human relationships ran through how much
they anthropomorphized it, which tracked their desire to connect. Dependence
was not measured. Aniket decided D1 on 2026-09-27: higher perceived
Experience is a pure goal, with no dependence bound (`06-decisions.md`).

## D. Modeling the user's mind (RESOURCE + ENGINEERING)

Strachan et al. (2024) compared GPT and LLaMA2 models with 1,907 humans on a
theory-of-mind battery. GPT-4 performed at or above human level on false
beliefs, indirect requests and misdirection. It struggled on faux pas, and
follow-up tests traced that to a hyperconservative refusal to commit rather
than a failure of inference. Theory of mind at conversational level is
available at frontier scale.

What this means here: the gap for this project is not "can a model infer
what the user believes" but what the architecture does with it. V3's audit
found the ToM store holds point estimates with no uncertainty or provenance
(`NF-26`), so a wrong guess about the user looks as confident as a right one.
That is ENGINEERING. The inference quality itself scales with the model
(RESOURCE).

## E. Emotions that respond to the user and persist (RESOURCE + ENGINEERING)

V3 already established the facts:
- The user's words move the agent's mood by exactly 0 today (A-1, `NF-12`).
- The update mechanism works when it is fed a real signal.
- No local LLM tested so far estimates user valence well enough: 0 of 3
  passed ADR-002's pre-registered rule.
- Two more formula defects sit on top: arousal contaminates persistent energy
  (`NF-17`), and decay counts ticks instead of real time (`NF-16`).

What this means here: W2's estimator bake-off decides whether this is a
RESOURCE limit (a larger or better-suited model passes) or a harder one.
The formula defects are plain ENGINEERING. Without this capability, the mind
cannot be moved by the person it talks to, and no other capability makes up
for that. It is the most important single dependency V4 has on V3.

## F. Emotions with stakes: functional drives (ENGINEERING / SCIENCE / UNKNOWABLE)

Two lines of theory put the body at the center of emotion:
- **Interoception and allostasis.** Barrett & Simmons (2015) propose that
  interoceptive experience largely reflects the brain's predictions about the
  body's expected state, constrained by signals coming up from the body, and
  that these predictions are central to affect.
- **Homeostasis.** Man & Damasio (2019) argue that feeling arises in systems
  that must keep themselves within a range of viable states, and propose
  building machines with that kind of self-regulation as a route to feeling
  analogues. Their route is robotic: soft robotics and multisensory
  homeostatic data.

What this means for a disembodied mind:
- The testable, buildable part is a small set of internal variables the mind
  must regulate, whose deviations drive affect and behavior:
  - energy and rest, with a sleep rhythm
  - social contact
  - competence and progress on its own goals
  - uncertainty: how much it does not know about the user or the situation,
    which rises with gaps and contradictions and falls with confirmation
  - possibly curiosity
- That gives emotions consequences: a lonely stretch changes mood and
  initiative, and failure on a goal it cares about hurts. That is
  ENGINEERING, and whether it makes the mind seem more real is an experiment
  (`04-candidate-workstreams.md`, V4-3).
- Whether regulating a variable produces anything felt is UNKNOWABLE (see N).
  Man & Damasio's own proposal also leans on a physical body; a purely
  software drive is a weaker analogue of what they describe, and the docs
  should say so rather than borrow their authority.
- The existing code has fragments that V3's audit found mislabeled or broken:
  - hormone curves labeled as neurochemistry (`NF-18`)
  - cortisol/adrenaline as proxy labels with no regulatory state (`NF-21`)
  - circadian recovery running in the wrong direction (`NF-33`)
  - curiosity and `learning_gain` code with no production caller (`NF-34`)

  A drive layer would replace labels with regulated variables that actually
  matter to behavior.
- There is formal groundwork for drives that shape learning: homeostatic
  reinforcement learning defines reward as reducing deviation from internal
  set points, and proves seeking such reward is equivalent to keeping
  internal variables stable (Keramati & Gutkin 2014). A continuous-time
  extension adds internal state that drifts without action (Laurençon et al.
  2021). These are simulations of physiological regulation, not evidence of
  human-like emotion or social behavior.
- What has no validated basis: units and set points for social needs
  (contact, belonging, competence). That part is SCIENCE, not ENGINEERING.
  The clean place to start is energy/rest and uncertainty, where inputs and
  costs are observable; social contact stays exploratory.

## G. An inner life that has consequences (ENGINEERING)

Humans spend much of waking life thinking about something other than what
they are doing. In Killingsworth & Gilbert's (2010) experience-sampling study
(2,250 adults, analysed from a database of nearly 250,000 samples from about
5,000 people), minds were wandering in 46.9% of samples, and in at least 30%
of samples during every activity but one. Mind wandering predicted lower
happiness (b = -8.79, p < 0.001). The design is observational, but the
authors' time-lag analyses "strongly suggested" that mind wandering was
generally the cause of unhappiness, not merely its consequence. What people were
thinking explained more variance in happiness than what they were doing.

What this means here:
- An inner life is not an extra; it is roughly half of human waking
  cognition, and it affects mood.
- This project already has a subconscious that generates thoughts between
  conversations, and self-directed thought as its own category (DR-019).
- The engineering question is whether that inner activity has consequences:
  - Does a thought change memory, mood or the next conversation?
  - Does the mind bring it up, act on it, or change its mind because of it?

  Inner activity with no downstream effect is decoration, and a person will
  not perceive it. Measurable: the share of later behavior traceable to
  between-session thought, and blind raters' judgment of "has a life of its
  own" (`03-realism-eval.md`).

## H. Identity that is stable but changes (ENGINEERING)

Human personality is both stable and changing, with known numbers:
- **Rank order.** Test-retest consistency of personality traits rises from
  .31 in childhood to .54 in the college years and .64 at age 30, then
  plateaus around .74 between ages 50 and 70, with the time interval held at
  6.7 years (Roberts & DelVecchio 2000; 152 longitudinal studies, 3,217
  coefficients). Longer intervals give lower consistency.
- **Mean level.** Traits keep changing in predictable directions across
  adulthood: conscientiousness, emotional stability and social dominance
  rise, especially from age 20 to 40 (Roberts, Walton & Viechtbauer 2006;
  92 samples).

What this means here:
- "The same someone, but not frozen" has a measurable band.
- V3 made evolution self-authored (DR-020), evidence-driven (DR-021) and
  corroborated across independent reflections (DR-038), with no reset
  (DR-033). W11 builds it.
- V4 can hold it to a human band: persona test-retest correlation inside the
  human range, plus nonzero, directional drift over long horizons. Without
  that check, self-authored evolution can drift somewhere no person would,
  with nothing to notice it.

## I. Learning from experience inside the weights (SCIENCE + RESOURCE)

- A language model's weights do not change from conversation to
  conversation.
- Training a network on new tasks in sequence overwrites what it learned
  before (catastrophic forgetting). Mitigations exist, such as elastic weight
  consolidation, which slows learning on weights important to earlier tasks
  (Kirkpatrick et al. 2017). None makes lifelong learning solved: continual
  learning is an open research field (SCIENCE).
- Fine-tuning a model on this project's own experience is also RESOURCE-
  limited on 8 GB of VRAM.

What this means here: learning from experience stays in the architecture
(memory, reflection, consolidation, self-authored persona), which is also
what Park et al.'s agents do. Weight-level learning is a later experiment,
not a V4 foundation.

## J. Human conversational timing (ENGINEERING + RESOURCE)

Human turn-taking is fast and similar across languages. In 10 languages the
mean response offset was +208 ms, the overall mode 0 ms, and language means
ranged from +7 ms (Japanese) to +469 ms (Danish) (Stivers et al. 2009,
verified in the full text). Preparing even a single word takes longer than
that gap: Levinson (2016) puts it at 600 ms or more, which implies people
plan their reply while still listening. (The 600 ms figure was checked
through secondary summaries only; see the log.)

Human conversation also overlaps constructively. In the CANDOR corpus of
1,656 unscripted English conversations (850 hours), Codex R6 reports that
33.7% of turns drew a listener backchannel ("yeah", "mm-hm"), 65.5% for turns
of five words or more (Reece et al. 2023; corpus size checked, rates
Codex-reported). Timing realism is gaps, overlaps and backchannels, not one
latency number.

What this means here:
- **Nothing measures audible timing today.** V3's 1.203 s p50 / 2.276 s p95
  (`11-v2-lifesim-baseline.md`, resources table) is foreground processing,
  not speech-end to first audible sound. The first job is instrumentation.
- DR-024's interactive budget, 300-500 ms to the first audio byte, is already
  slower than the human mean gap, before any network cost.
- A cascaded speech-to-text, then LLM, then text-to-speech pipeline struggles
  to reach 200 ms, because each stage waits for the previous one.
- Full-duplex speech models cut latency: Moshi reports 160 ms theoretical and
  200 ms practical latency on an L4 GPU, listening and speaking at the same
  time (Défossez et al. 2024). But low latency is not turn-taking competence.
  On an interruption benchmark, Moshi's successful-reply rate fell from 61.7%
  on easy interruptions to 34.1% on hard ones (Peng et al. 2025,
  Codex-reported). Full-Duplex-Bench (Lin et al. 2025) evaluates pause
  handling, backchanneling, turn-taking and interruption; the only
  per-system result cited here is Moshi's, above. No claim is made about
  systems not measured.
- Moshi is a 7B model. Its FAQ says 8 GB GPUs cannot run it, so the home GPU
  is out. Its MLX build was tested on an M3 MacBook Pro with 4- and 8-bit
  weights, so the M5 Mac is a plausible experiment (untested). It is
  English-focused, has fixed voices, and its MLX build has a known limit of
  about five minutes of context.
- Class: latency is RESOURCE; turn-taking behavior (when to yield, when to
  backchannel, how to handle being interrupted) is ENGINEERING that nobody
  has fully solved.

## K. Expressive voice (RESOURCE + ENGINEERING)

- **Synthetic voices can already pass as human in short clips.** Listeners
  found voice clones as real-sounding as human voices and could hardly tell
  them apart; there was no "hyperreal" effect (Lavan et al. 2025). Detection
  rates swing with listeners, stimuli and channel, so there is no single
  "people can tell" number.
- **Prosody matters more than a face.** Adequate prosody made agents sound
  more natural and alive; showing an on-screen agent did not mask inadequate
  prosody (Ehret et al. 2021, 40 participants).
- **This project has no personal voice today.** The GPT-SoVITS weights are
  empty (`docs/brain-research-v3/04-local-infrastructure.md`). Small local
  TTS such as Kokoro-82M is a candidate (Codex R6), unbenchmarked on these
  machines.
- **Coupling is premature until W2.** The voice agent already consumes a
  dynamic modulation (`agent.voice.modulation`) and `NF-36`'s hand-set
  coefficients. Driving prosody from today's affect, which user words do not
  move, would faithfully express the wrong state. After W2, the V4 question
  is whether listeners hear the mood the internal state says it has.

## L. Seeing as shared perception (ENGINEERING)

Shared perception means seeing, noticing change, and remembering what was
seen, then bringing it up in conversation ("you moved the plant"). Today's
vision (`backend/app/vision/agent.py`) captures at 1 fps, runs the
vision-language model every 5 seconds, and suspends it during conversational
turns to free the GPU. So a change the user points out mid-conversation can be
missed, and there is no store of timestamped visual episodes.

The path is ENGINEERING:
- cheap per-frame change and object tracking
- the VLM reserved for meaning
- visual observations written as memories under W1's truth maintenance

Small local VLMs (2-4B) are the realistic fit for an 8 GB GPU next to the
brain model. Their quality must be measured on this project's own camera, not
read off large-model benchmark tables (Codex R6).

## M. Reading the user's emotion from their face (SCIENCE)

A major review (Barrett, Adolphs, Marsella, Martinez & Pollak 2019)
examined the common view that emotional state can be read from facial
movements, for the six most commonly assumed categories (anger, disgust,
fear, happiness, sadness, surprise). Its finding is not "faces carry no
signal": people do smile when happy, scowl when angry and so on more often
than chance. But how people express each emotion varies substantially across
cultures, situations and individuals, and the same facial configuration can
go with more than one emotion. The limit is scientific, not technical: better
cameras do not remove the ambiguity.

What this means here: facial cues can be weak, context-conditioned evidence
about the user, never ground truth, and never the input that moves trust or
mood on its own.

This is live code, not hypothetical. `backend/app/vision/reflex.py` already
nudges the agent's own valence and dopamine from hand-picked blendshape
thresholds (smile +0.04 valence and +0.08 dopamine; brow furrow -0.03), and
labels a smile "unambiguously positive". As emotional contagion (the mind's
mood responding to a smile) it is defensible. As a claim about the user's
feelings it is not. Recorded as a candidate V3 finding in
`05-comparison.md`. Mislabeling a user's face as angry, and then treating them
as if they were, is exactly the "confident wrong guess" failure `NF-26`
describes.

## N. Consciousness and "actual depth" (UNKNOWABLE today, with a testable neighbor)

Butlin, Long and 17 co-authors (2023) derived "indicator properties" of
consciousness from five scientific theories (recurrent processing, global
workspace, higher-order, predictive processing, attention schema) and
assessed AI systems against them. Their conclusions: no current AI system is
conscious, and there are no obvious technical barriers to building systems
that satisfy the indicators. Chalmers (2023) judged current LLMs somewhat
unlikely to be conscious, citing missing recurrent processing, a global
workspace and unified agency, and said it is quite possible these obstacles
are overcome in the next decade or so.

What this means here:
- Whether the mind experiences anything cannot be established today: there
  is no accepted test or behavioral criterion, and indicator properties
  change credence without settling it. That is a limit of current science,
  not a proof that no test could ever exist. It is not a V4 target, and V4
  docs never claim it either way.
- What people perceive is measurable. Mind-perception research finds two
  dimensions (Gray, Gray & Wegner 2007): **Agency** (for example, the
  capacity for self-control; the paper's list also includes planning, memory
  and thought) and **Experience** (for example, the capacity for hunger; the
  list also includes fear, pleasure and joy). The two dimensions predicted
  different moral judgments. "Real, thinking, not mere code" maps onto being
  perceived high on both.
  - Agency comes from memory, planning and initiative (B, G).
  - Experience comes from affect, drives and inner life expressed (E, F, G,
    K).

  These become questionnaire axes in the realism eval.
- The indicator properties are architectural, so they can be audited without
  claiming anything. V3's audit already found the nearest one missing:
  global workspace is a per-turn object, not competing specialists
  broadcasting (`NF-38`).
- Ethics flag: building deliberately toward the indicators raises
  moral-status questions. Codex R4 covers the ethics side; it belongs in a V4
  decision round (see `00-origin.md` section 5).

## O. Bottom-up brain emulation (SCIENCE + RESOURCE)

The complete wiring diagram of an adult fruit fly brain was published in
2024: 139,255 neurons and about 5 x 10^7 chemical synapses (Dorkenwald et al.
2024). A leaky integrate-and-fire model built from it predicted which neurons
respond to tastes and drive feeding, validated experimentally (Shiu et al.
2024). That is real progress, for a fly, on specific sensorimotor circuits.
Nothing comparable exists for a mammal, let alone a human. Current evidence
does not support emulation as a route to a humanoid mind for this project;
that is a statement about today's evidence, not a proof it can never work.
The computational-model route V3 already takes (mechanisms at the level of
behavior, judged by BrainBench) is the one the evidence supports now.
