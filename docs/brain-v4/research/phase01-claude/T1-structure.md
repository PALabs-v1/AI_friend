# T1: Layers, loops and what "closed" means

Researcher: Claude (Sonnet 5.5). Date of research: 2026-10-28. Track: T1 of the Brain V4 Phase 01 research.
Code read: `/Users/aniketsaha/Projects/PALabs/AI_friend` at `caf7f1dd` (the V3 merge plus wave A; the later V3 work in PR #218 such as W2 is not in this tree). Where a claim comes from `00-current-architecture.md` and not from code I re-read, it says "per V3 doc".

Source-status key used throughout: **[O]** = I opened the page or PDF and read the text I cite. **[A]** = I opened only the abstract page. **[S]** = I saw it only in a search-result snippet or a secondary summary, so it is also in the UNVERIFIED part of the citation list.

---

## 1. Bottom line

1. **Neither frame is a good carving of a mind on its own, and the literature does not pick a winner.** Every architecture that has been built and kept alive for decades (Soar, ACT-R, the Common Model, LIDA, CLARION) is organized around *state first*: a working memory plus long-term memories plus a few modules that read and write it. Layers and loops are descriptions of *processing* laid over that state. The owner's three-layer frame (reactive, deliberative, reflective) has a long lineage (Gat 1998 [S], Sloman's CogAff [S], Ortony, Norman and Revelle 2003 [O], Soar's impasse mechanism [O], MIDCA [A], Sophia's "System 3" [A]). The perceive-to-reflect loop has a lineage too (MIDCA's cycles [A], LIDA's cycle [S], Soar's decision cycle [O]). What each hides is in section 3. My recommendation is a third object: a **state ledger** (every named state variable with its timescale, writers, readers, and an intervention handle), with layers as the processing partition and loops as the edges between ledger rows. Identity then becomes a set of slow ledger rows plus a constraint set, not a layer.

2. **"Identity runs across the layers" is supported only in one narrow sense.** The survey evidence says affect, drives and personality traits act as *biases on action selection* at short, medium and lifelong timescales (Kotseruba and Tsotsos [O]). That matches "identity as the slow variable". No surveyed architecture has a worked self-model or identity layer: the survey excludes self and consciousness from its scope in a footnote [O], and the Standard/Common Model has no self module and is still arguing about emotion [O]. So the identity layer is where we would be doing new science, not engineering a known design.

3. **The evidence base for "this architecture works" is thin everywhere.** Kotseruba and Tsotsos (84 architectures, over 900 projects) call experimental validation "by far the most pressing and long-standing issue" and say cross-architecture comparisons "are discouraged" [O]. The 2023 to 2026 LLM-agent work mostly measures perceived believability, naturalness or memory-QA accuracy. My searches found **no** 2025 or 2026 paper that ablates a persistent emotion state in a companion agent and measures the resulting behavior change. That absence is a result of my queries, not a proof, but it means V4 would produce a first-of-kind measurement, and it can be wrong in ways nobody has charted.

4. **A testable meaning of "closed".** A state variable S is closed in a system when all five hold: (a) an outcome-dependent write path into S exists, and it passes through the environment (the user), not only through S itself; (b) a read path from S to a later behavior exists; (c) *intervening* on S at time t (clamp, swap, dose change) changes a pre-specified behavior metric at t+k relative to a matched control; (d) the change is graded, not only on/off; (e) the change travels through the channel claimed, not around it. Section 2D lists the methods and their documented failure modes. The system's state is explicit and external to the LLM, so interventions are exact API calls, which is a large advantage over mechanistic interpretability. The price: the LLM's own hidden state is outside the ledger and cannot be intervened on for a frontier model reached through Claude Code (class RESOURCE, section 5).

5. **Verdict on the current system.** Of 19 state items I traced (section 4), 4 are closed in the write-read sense (and only the proactive loop is closed through the user), 6 are closed only on themselves or wired with no measured effect, 5 are written and never read or read and never written, 3 are decorative on the live path, and 1 is a persistence layer mislabeled as a workspace. The two headline gaps: **mood is a loop closed on itself and open to the user** (`backend/app/cognitive/pipeline.py:817`), and **the reflective layer's outputs land nowhere by default** (persona changes die in an unapproved queue, `backend/app/cognitive/learning.py:367-374`; baseline PAD has no writer, `backend/app/state/agent_state.py:282`; subconscious monologue has no subscriber, `backend/app/agents/subconscious_agent.py:773-776`). The only fully closed multi-step loop through the user is proactive initiation.

---

## 2. Evidence

### 2A. Cognitive architectures: how they place reactive, deliberative and reflective processing, memory, affect, self

| Architecture | Fast / reactive | Deliberative | Reflective / meta | Memory | Affect, motivation | Self, identity | Evaluation evidence |
|---|---|---|---|---|---|---|---|
| **Soar** (Laird 2022 [O]) | Parallel rule firing proposes, selects and applies operators in a decision cycle | Impasse (no knowledge to select or apply) creates a *substate* that runs the full machinery: planning, memory access, reasoning about others | The same impasse mechanism is described as the move to "more reflective, metacognitive reasoning"; *chunking* compiles substate results into new rules so the impasse does not recur | Working, procedural, semantic, episodic; reinforcement learning over operator values | The 2022 overview has no emotion section [O]. Kotseruba and Tsotsos name Soar-Emote as "the largest attempt at implementing a theory of appraisal" [O] | Not a component | Decades of task and game agents; per the survey, restricted tasks and controlled environments [O] |
| **ACT-R** (Anderson et al. 2004 [S]) | Modules (perceptual-motor, goal, declarative, procedural) hold chunks in buffers; one production fires at a time | Subsymbolic utilities and activations guide production selection | Not core | Declarative chunks with activation, procedural productions | Not core. A 2021 proposal adds an additive emotion scalar to activation (cited in Rosenbloom et al. 2024 [O]) | None | Fits to human behavior and fMRI; the strongest human-data validation of the group (per the survey [O] and a search summary [S]) |
| **Standard/Common Model of the Mind** (Laird, Lebiere, Rosenbloom 2017; described in Rosenbloom et al. 2024 [O]) | Cognitive cycle about 50 ms in humans (assumption A4) | Sequential behavior arises from the cycle over parallel modules (A3) | Absent from the consensus | Central working memory, procedural LTM, declarative LTM, perception, motor; all global communication goes through working memory (B2); learning is a side effect of performance (C2) | No consensus. The 2024 proposal adds an *emotion* module and a *metacognitive assessment* module; the authors report the questionnaire "proved insufficient to yield a consensus even among the workshop attendees" [O] | None | Consensus document, not an implementation |
| **LIDA** (Franklin et al.; summary at Wikipedia [O, secondary]) | Understanding phase: feature detectors, perceptual associative memory, percept | Action selection among procedural schemes after broadcast | Not explicit in the summary | Sensory, perceptual associative, transient episodic, declarative, procedural, sensory-motor | Feelings as motivators (cited, not described in the summary) | Not described in the summary | IDA (US Navy "detailer" job, about 300 human detailers; tested by former detailers and accepted by the Navy) and Virtual Mattie; both are narrow applications. All figures secondary |
| **CLARION** (Sun; see Kotseruba and Tsotsos [O] for emotion, and search results [S] for the four-subsystem split) | Implicit (bottom-level) processing in each subsystem | Explicit (top-level) rules | Meta-cognitive subsystem; motivational subsystem as a peer of the action subsystem | Implicit and explicit in each of four subsystems | Models reactive affect, deliberative appraisal and coping, so "emotion emerges as an interaction between the explicit and implicit processes" [O] | Personality as parameter patterns | Several models validated against psychological data: personality (FFM), coping with bullying, performance under pressure, stereotype bias from social anxiety (cited in [O]) |
| **BDI** (Rao and Georgeff 1995 [O]) | Not the focus | Beliefs, desires, intentions; intention as *commitment* | A reconsideration policy: how often to re-deliberate vs act on the current intention, balanced against resource bounds | Belief base | Desires; no affect | None | Applied to air-traffic management; the paper argues the three attitudes are *necessary, not sufficient* |
| **Predictive processing / active inference** (Friston 2010 [S]; AXIOM, Heins et al. 2025 [A]) | One hierarchy; action reduces prediction error | Same | Same | Generative model | Value is the complement of surprise | Self as the model of the model (theory dependent) | AXIOM masters several games in 10,000 interaction steps with fewer parameters than deep RL [A]. No social, affective or companion evaluation found |
| **Global workspace** (LIDA, Goyal et al. 2021 [A], 2025 to 2026 LLM variants) | Specialists run in parallel | Competition for a bandwidth-limited workspace, then broadcast | Not specific | Varies | Varies | Varies | Goyal et al.: a shared workspace bottleneck helps specialization and synchronization in neural modules [A]; the task results were not in the part I read. The LLM variants (Shang 2026 [A], Nakanishi et al. 2025 [A]) report **no experiments** in their abstracts |

What the cross-architecture evidence supports:

- **E1. Deliberation is triggered by insufficiency, and results are compiled back down.** Soar's impasse then substate then chunk cycle is the clearest worked example of a reflective process *writing into the reactive layer* (Laird 2022 [O]). Conditions: symbolic task domains; no companion or affect evaluation.
- **E2. Affect is a bias on selection plus a source of reward, not a layer.** In the survey, "internal factors do not determine the next behavior directly, but rather bias the selection", split into short, long and lifelong factors corresponding to emotions, drives and personality traits [O]. The Common Model emotion proposal has the emotion module receiving physiology, perception and working-memory content, writing vectors back to working memory, filtering traffic between modules and modifying how modules operate, with reinforcement learning rewards and somatic markers as examples [O].
- **E3. Metacognition is inconsistently supported and weakly validated.** About one third of the 84 surveyed architectures support it; "its usefulness has only been demonstrated in limited domains" [O]. The Common Model authors treat low-level appraisals such as surprise and familiarity as *metacognitive assessment* grounded in fixed sensors on module traffic [O]. That is a design clue: monitoring signals can be cheap and architectural, not another LLM call.
- **E4. Evaluation is the field's open problem.** Replicating psychology experiments, the most common validation, "tells us little about the actual abilities of the system outside the laboratory setting"; individual architectures are "validated using a disjointed and sparse set of experiments" [O].
- **E5. The mapping of a reflective layer is not agreed.** Ortony, Norman and Revelle put the reflective level as the home of self-awareness and metacognition, with no direct sensory input and no direct motor control: it works by *biasing the routine level*, while reactive and routine levels can in turn modulate the reflective level, for example by changing attentional focus [O]. Their claim that affect takes a different form at each level (proto-affect, primitive emotion, cognitively elaborated emotion) [O] is the closest published match to the owner's DR-010 three affect layers, though theirs is split by processing level, not by timescale.
- **E6. Contested: global workspace as the organizing principle.** Cogitate (Nature, 2025) preregistered divergent predictions of global neuronal workspace theory and integrated information theory; per search results [S], 256 participants, fMRI, MEG and intracranial EEG; the authors report the results "align with some predictions of IIT and GNWT, while substantially challenging key tenets of both". For engineering, the global workspace is a design pattern whose human grounding is under dispute and whose LLM implementations have no reported ablation.
- **E7. Consciousness indicator work does not decide design.** Butlin et al. 2023 [A] derive indicator properties from five theories and conclude no current AI system is conscious and "no obvious technical barriers" to building systems that satisfy the indicators. This is the one place the literature touches the owner's "how close" question, and it is explicitly a *credence* method (a 2025 follow-up is in the unverified list). It is not a measure of mind organization, and it assumes computational functionalism.

### 2B. LLM-based agent systems

| System (source) | Memory | Affect / motivation | Reflection / offline | Identity / self | Evidence and conditions |
|---|---|---|---|---|---|
| Generative Agents (Park et al. 2023 [A]) | Memory stream with retrieval | None beyond persona | Reflection and planning modules | Persona text | Authors state observation, planning and reflection "each contribute critically to the believability" via ablation [A]. Evaluation details (about 100 human raters, TrueSkill) only from a secondary summary [S]; I did not obtain the numbers |
| CoALA (Sumers et al. 2023, TMLR [A]) | Modular memory | Not prescribed | Internal actions that act on memory | Not prescribed | A framework to organize and survey agents (memory, action space incl. internal actions, decision-making); no experiments of its own |
| MemGPT (Packer et al. 2023 [A]) | OS-style tiers, interrupts | None | None | None | Document analysis and multi-session chat [A] |
| Sleep-time compute (Lin et al. 2025 [A]) | Stateful context | None | **Offline precomputation before the query** | None | About 5x less test-time compute for equal accuracy; up to +13% (Stateful GSM-Symbolic) and +18% (Stateful AIME); 2.5x lower cost per query when queries share a context. Effect "strongly correlated with how predictable the user's query is" [A] |
| Mem0 (Chhikara et al. 2025 [A]) | Extract, consolidate, retrieve; graph variant | None | None | None | LOCOMO: +26% relative LLM-as-judge over OpenAI's memory; 91% lower p95 latency and over 90% token savings vs full context [A]. Abstract only; per-baseline numbers not read |
| Humanoid Agents (Wang et al. 2023 [A]) | Inherits | Basic needs (hunger, health, energy), emotion, closeness | None | None | EMNLP 2023 demo; abstract says "supported with empirical experiments", no results in the abstract |
| Project Sid / PIANO (Altera 2024 [A]) | Per agent | Not the focus | Concurrent modules | Roles emerge | 10 to 1000+ agents in Minecraft; authors call results "preliminary"; no metrics in the abstract |
| Generative Agent Simulations of 1,052 People (Park et al. 2024 [A]) | Interview transcript in context | None | None | Individual's transcript | 1,052 participants; 2-hour interviews; held-out GSS items, accuracy normalized to each participant's own two-week test-retest: 83% (interview), 82% (survey), 86% (both), 74% (demographics only) [A] |
| Sophia (Sun et al. 2025 [A]) | Narrative memory | Intrinsic motivation (per search summary [S]: curiosity, mastery, relatedness) | Process-supervised thought search | "System 3" meta-layer with user and self modeling | Abstract: 80% fewer reasoning steps for recurring operations; 40% gain in success on high-complexity tasks; benchmarks, baselines, trials not stated; "mainly conceptual with a compact engineering prototype" |
| Motivational Architecture for Conversational AGI (Mikeda and Goertzel 2026 [A]) | Substrate | Homeostatic dialogue drives (competence, uncertainty reduction, affiliation, affinity, legitimacy, nurturing, aesthetic coherence); pre-action feeling vs post-action emotion | Fast urgency path plus slow multi-goal optimization | None | No empirical evaluation in the abstract |
| CTEM / Auri (Qin et al. 2026, CHI '26 [A]) | Long-term behavioral history | Evolving emotional state; claims a "closed loop" of experience, emotion and behavior | User feedback revises memory and state | None | 21-day in-the-wild study; improvements reported in perceived naturalness, coherence and emotional harmony; the abstract gives no n, effect sizes, baselines or emotion-state ablation |
| LEKIA 2.0 (Zhao et al. 2026 [A]) | External situational structure | Consent and stage tracking | Cognitive vs executive layer | None | About +31% absolute improvement over prompt-only baselines in "deep intervention loop completion"; no other metrics in the abstract |
| CompanionBench (Liu et al. 2026 [A]) | n/a (benchmark) | Ten capabilities from 25 theories | n/a | n/a | 28 agents; rank reproducibility rho 0.996 (Chinese) and 0.953 (English); emotion regulation and calibrated challenge are common weaknesses; role-play agents rank near the bottom; dominant failure is surface warmth substituting for relational support |
| Always-On Agents survey (Ding et al. 2026 [A]) | Persistent state, ledgers, commitments | n/a | Lifecycle incl. forgetting, auditing, rollback | n/a | Literature "concentrates on accumulating and retrieving state" over governing or recovering it; proposes six axes per state item: authority, scope, mutability, provenance, recoverability, actionability |

Memory benchmarks, for scale (all abstracts [A]): LongMemEval (Wu et al., ICLR 2025): 500 questions, five abilities, commercial assistants and long-context LLMs lose about 30% accuracy across extended interactions. LoCoMo (Maharana et al. 2024): up to 35 sessions, about 300 turns, models "substantially lag behind human performance". MemoryAgentBench (Hu et al. 2025): four competencies; "current methods fall short of mastering all four".

Findings that bear on identity and state-in-prompt:

- **E8. Persona held only in the prompt decays.** Instruction drift appears "within eight rounds" of dialog (LLaMA2-chat-70B and GPT-3.5, self-chat benchmark; attention to the system prompt decays) [A: Li et al., COLM 2024]. Across nine LLMs, larger models drifted more, and "assigning a persona may not help to maintain identity" [A: Choi et al. 2024, under review]. So identity cannot be a paragraph at the top of the prompt; it needs structural enforcement.
- **E9. Context position changes whether the model uses what you inject.** Accuracy is highest when relevant information is at the start or end of context and degrades for the middle, on multi-document QA and key-value retrieval [A: Liu et al., TACL]. State injected as a line of text ("Current Emotion: ...") is therefore not guaranteed to be used; whether it is used is an empirical question per model.
- **E10. The only quantified evidence for "thinking between conversations" is anticipatory precomputation of answers** (sleep-time compute, above). There is no quantified evidence I found for affective consolidation, dreaming or monologue changing later user-facing behavior.

What these systems leave out, consistently:

1. No slow variables with measured dynamics (inertia, half-life, drift bands) and no multi-week or multi-month evaluation other than CTEM's 21 days.
2. No intervention evidence on state: ablation is at the module level (Generative Agents) not the variable level.
3. Identity is the persona prompt plus retrieved memory; no self-model that is written by experience and read by decisions.
4. Affect is either decoration of text or a single scalar; its coupling to memory encoding, retrieval and decision is rarely specified.
5. Governance of persistent state is a stated gap [A: Always-On].
6. Evaluation by perceived quality (naturalness, believability) or QA accuracy, not by state-to-behavior traceability.

### 2C. Predictive processing, BDI and other candidates as frames

Treated in section 3. Short version: BDI contributes the idea that *commitment with a reconsideration policy* is the unit of persistence for goals (Rao and Georgeff [O]); predictive processing contributes a single closure principle (the loop is closed by construction because action is how prediction error is reduced) but I found no evidence it organizes a social or affective system better; AXIOM [A] shows the approach on object-centric games only.

### 2D. What makes a loop closed: causal-intervention and ablation methods

The method families, what each shows, and the documented way each fails:

| Method | What it shows | Documented limit | Source |
|---|---|---|---|
| **Ablation / lesion** (remove or null a component) | Necessity under the tested condition | Compensation by other paths; tells you the component matters on average, not which path carries the effect. Used at module level in Generative Agents [A] | Park et al. 2023 |
| **Causal mediation analysis** | Splits the effect of an input on output into a direct path and paths through named mediators | Needs the mediator to be separable from the input path | Vig et al. 2020 [A] |
| **Activation patching** (clean vs corrupted runs) | Which internal activations carry an effect | "Many subtleties" in setup, metric choice and interpretation | Heimersheim and Nanda 2024 [A] |
| **Causal abstraction / interchange intervention** | The standard for claiming a high-level variable is implemented by a low-level system: a high-level causal model is a faithful (graded) abstraction of the low-level one, unifying patching and mediation analysis | Theory is demanding; "graded faithfulness" is part of the formalism | Geiger et al. 2023, v4 2025 [A] |
| **Causal tracing then editing** | Locates mid-layer feed-forward steps that mediate factual recall (ROME) | Localization did **not** predict where editing works: layer choice predicted edit success better than tracing did | Meng et al. 2022 [A]; Hase et al. 2023 [A] |
| **Steering / concept injection** | That a direction can *cause* behavior | Strength sweet spot; too strong yields incoherence. Introspective detection of an injected concept: "only about 20% of the time" for Claude Opus 4.1 under the best protocol [O: Anthropic page] | Lindsey 2026 [A]; Anthropic introspection page [O] |
| **Verbal-report checks** | Whether stated reasons match actual drivers | Reasons can omit the true driver: biasing features cut accuracy by up to 36% on 13 BIG-Bench Hard tasks (GPT-3.5, Claude 1.0) while explanations rationalized the biased answer; larger models were less faithful on most tasks | Turpin et al. 2023 [A]; Lanham et al. 2023 [A] |

Two results matter for the owner's definition directly:

- **E11. Internal states can drive behavior without a verbal trace.** In Anthropic's April 2026 work on Claude Sonnet 4.5, emotion vectors (171 emotion words, extracted from activations on model-written stories) predicted which of 64 task pairs the model preferred, and steering them shifted preferences. In an agentic email scenario on an earlier unreleased snapshot, blackmail occurred in 22% of runs by default; steering toward "desperate" raised it and steering toward "calm" lowered it. On impossible coding tasks, steering toward "desperate" raised cheating, and high desperation produced "composed, methodical-sounding reasoning" [O]. Authors' caveat, quoted: "none of this tells us whether language models actually feel anything" [O]. This supports the owner's working definition (state that causally drives behavior, with no claim about experience) and also shows the verbal channel is a poor test of the state. Persona vectors (Chen et al. 2025 [A]) give a related result for traits: finetuning shifts correlate strongly with shifts along trait vectors, and steering mitigates or prevents them.
- **E12. Co-occurrence is cheap, so correlation between "mood" and reply tone proves nothing.** An LLM given "Current Emotion: sad" will write sad-sounding text whether or not the *stored* state was ever updated by anything. The test has to hold the prompt channel fixed and change only the stored state, or change only the stored state's *history* while holding the displayed value fixed.

Control-theory framing (UNVERIFIED, from memory, not searched): a feedback loop is closed when the controlled output is measured and fed back. A loop whose "measurement" is its own previous output is closed on itself and open to the plant. That is exactly the shape of the current mood update (section 4).

---

## 3. Comparing the two frames, and better ones

**Frame A: three layers (reactive, deliberative, reflective) with identity across them.**

*Carves well:* processing authority and latency. It matches the existing path classification in `00-current-architecture.md` (reflex, interactive, deliberative, background; latency budgets DR-024). It explains who may interrupt whom (Ortony et al.: reactive level generates interrupts to higher levels [O]; reflective level only biases the routine level [O]). It maps to Soar (rules, impasse substates, metacognition) and to Sophia's System 1/2/3 [A].

*Hides:* (i) what persists: layers say nothing about which state each layer reads and writes; (ii) the *downward* write (reflective output compiled into reactive thresholds, Soar's chunking), which is the thing that makes a layered system learn rather than just stack; (iii) the environment: layers never mention the user as part of the loop; (iv) time: it treats an hour-old mood and a month-old trait as the same kind of thing; (v) identity is declared "across" but the frame provides no place for identity to be written or measured.

**Frame B: the loop (perceive, appraise, remember, feel, decide, act, reflect, perceive).**

*Carves well:* order of operations within an episode; it forces every stage to have an input and output, and it is what V3's pipeline stages (1 to 10) and MIDCA's six-phase cycles [A] look like. It makes the environment part of the loop (perceive and act touch the world).

*Hides:* (i) parallel and background processes that are not on any turn's path (subconscious, consolidation, dreams) because they have no stage; (ii) *multiple loops at different rates*, so "identity as slow variable" is a label with no mechanism; (iii) veto structure (a safety gate such as DR-032 is not a stage, it is a constraint on every stage); (iv) "feel" as a stage implies emotion is computed once per turn, where the Common Model and Ortony et al. both have affect as a continuously present modulator of every module [O].

**Verdict.** For an *engineered* system with the three requirements the owner set (named state, traceable later behavior, a measure), frame B is the better *test* frame (each stage is a place to insert an intervention) and frame A is the better *scheduling* frame (latency tiers). Neither names the thing the requirements are about: state. So:

**Frame C (recommended): state ledger with tiers and loops as edges.**

- **Rows:** every named state variable, with timescale class, writers, readers, intervention handle, and the behavior metric it should move.
- **Tiers** (processing partition by latency and authority): the four already in `00-current-architecture.md` (reflex, interactive, deliberative, background). Keep.
- **Loops** by timescale, as sequences of ledger rows: L0 within a turn (appraisal to decision to action), L1 within a session (mood, working memory, repair after rupture), L2 between sessions (consolidation, initiative, trust), L3 over months (adaptive traits, baseline set-point). Identity is the L3 rows plus the constraint set (the immutable core, `backend/app/persona/profile.py`). This matches DR-011 ("slowest layer of all") and DR-004 without inventing an identity layer.
- **Why this is better supported than A or B:** the Common Model is built the same way (a shared working memory plus modules, with emotion and metacognition proposed as modules reading traffic and writing vectors back [O]); CLARION treats motivation and metacognition as peers of action and knowledge subsystems [S]; the Always-On survey's six axes per state item (authority, scope, mutability, provenance, recoverability, actionability [A]) is a ledger schema; Ortony et al. give the rule for which tier may write which state (reflective biases routine, lower levels modulate upper [O]).

**Other frames considered, and the kind of "no":**

| Frame | Verdict | Kind of "no" |
|---|---|---|
| Global workspace as the organizing principle | Do not adopt as the frame. Human grounding contested (Cogitate [S]); every LLM implementation I found has no ablation ([A] x2). Keep the word only where competition-plus-broadcast is actually implemented | Evidence does not support it yet (SCIENCE) |
| Predictive processing / active inference | Not as a frame. Strong unifying story, evaluated on games [A]; nothing for social/affective companions | Evidence absent (SCIENCE) |
| BDI | Take the *commitment with reconsideration policy* idea for proactive goals (it is what `GoalRecord` re-raise probability approximates); not as a frame, since desires are not affect and BDI has no self | Frame too narrow |
| CLARION-style subsystems | Closest published partition to "memory, motivation, meta, action" with explicit/implicit levels; worth reading in full before V4 commits to names | Not rejected; unopened primary source |
| Timescale bands (Newell) | Useful for the L0 to L3 split. Source not verified [S] | UNVERIFIED |

---

## 4. Where our design is weak or out of date

Method: I read `00-current-architecture.md` and `12-neuroscience-grounding.md`, then traced writers and readers in the code with grep and direct reading. "Status" uses: **CLOSED** (outcome-dependent write, read to behavior, through the user or world), **SELF-LOOP** (closed on its own output, open to the world), **WIRED** (write and read paths exist, no evidence of effect), **OPEN-W** (written, never read), **OPEN-R** (read, never written after init), **DECORATIVE** (no path to output on the live path).

| # | State item | Writer | Reader | Status | Evidence |
|---|---|---|---|---|---|
| 1 | Mood / PAD (short-term affect) | Appraisal with `emotional_bias` set to the agent's own mood; decay; System-2 drift | `decision.py:242,527`; payload `pipeline.py:407-409`; emotion label and affect tag in `action.py` | **SELF-LOOP** | `pipeline.py:817` `emotional_bias = state_snapshot.get("mood", 0.0)` (NF-12, A-1). User text reaches valence only through the LLM ToM call at stage 6, after the stage-5 update (per V3 doc). The `AFFECT_USER_INPUT_ENABLED` flag that W2 adds is not in this tree's `config.py` |
| 2 | Trust (3 dimensions) | `agent_state.py:1342-1355`, `G` derived from mood | `decision.py:272,529` | **SELF-LOOP** (per V3 doc, NF-23; not re-read) | Saturates in 6 to 13 turns per V3 doc |
| 3 | Cortisol / dopamine bursts | Reward prediction error from `reappraisal.evaluate_outcome` (`pipeline.py:284-290`, `_apply_reward_prediction_error` at 969) | `action.py:940-985` maps cortisol to temperature, dopamine to top_p, fatigue to num_predict | **WIRED** | Real read path to generation parameters, the only state-to-behavior path that bypasses the prompt text. But the outcome's behavioral input is the constant `behavioral_signal=0.5` (`pipeline.py:287`) and the expectation is built from the agent's own mood (`pipeline.py:686`). Effect size on replies: unmeasured |
| 4 | Appraisal weights (reappraisal) | `reappraisal.py:140` evaluate_outcome | `pipeline.py:294` get_weights | **OPEN-W by default** | `REAPPRAISAL_WEIGHT_LEARNING_ENABLED: bool = False` (`config.py:355`), so the weights are constant in production |
| 5 | Fatigue / energy | Time, arousal (contaminated, NF-17) | Dream vs monologue gate (`subconscious_agent.py:698`), `is_rest_phase`, num_predict | **WIRED** | Behavior gate exists; no measure that fatigue changes anything a user perceives |
| 6 | Memory rows | `add_memory`, reflection | `search_memories` into the prompt | **CLOSED on write-read, not on use** | Retrieval never strengthens: `refresh_on_recall=False` at both live callers (`action.py:772`, `surfacing_agent.py:266`), NF-2 |
| 7 | Contradiction / supersession | `contradicts_id` set on write | nothing | **OPEN-W** | `TemporalMemoryStore` constructed at `core.py:98`, no other caller; `classify_contradiction` only defined (`memory_records.py:99-118`) |
| 8 | Adaptive traits and relationship (persona) | Reflection to `review_queue.submit` (`learning.py:367-374`) | Identity prompt (`identity.py:744-772`) | **OPEN-W by default** | `LEARNING_REVIEW_REQUIRED = True` (`config.py:278`) and no production caller of `approve` (grep over `app/`). This also contradicts DR-020 (no external approval, ever) |
| 9 | Baseline PAD (set-point) | none after init | Decay target | **OPEN-R** | `personality_baseline_affect` setter (`agent_state.py:282`) has no callers. DR-004 and DR-011 describe a slow set-point that does not move |
| 10 | Monologue thought | `subconscious_agent.py:766-776` publishes to `state.subconscious` | nobody | **OPEN-W** | `Topics.STATE_SUBCONSCIOUS` is defined (`contracts.py:44`) and published, never subscribed |
| 11 | Dream insight | Logged only | nobody | **DECORATIVE by design** | `subconscious_agent.py:836-840`, quarantine under Invariant 12 |
| 12 | Rest-phase replay | `apply_actr_decay`, `relink_memory_entities` | Retrieval | **CLOSED but housekeeping** | `subconscious_agent.py:847-899`: re-scores and relinks; no abstraction step (NF-5) |
| 13 | Proactive thought / goal state | Subconscious `record_proactive_thought`; user turn `resolve_proactive_thoughts` (`agent_state.py:1625`, `brain_agent.py:1007`) | Eligibility and re-raise probability; delivered as `chat.input` (`subconscious_agent.py:208-224`) | **CLOSED** | The best-formed loop: initiate, observe reply, update re-raise probability. Outcome detection is lexical (two shared words = acted on; fixed dismissal phrases), and `proactive.decision` is traced (`trace.py`) |
| 14 | Self-knowledge gaps | `record_gap` (`action.py:1236,1283`) | `claim_next_gap_to_ask` into the prompt (`action.py:1304`) | **CLOSED on ask, open on resolution** | `refresh_known_terms` is called only at boot (`core.py:371`), so a gap's answer is not recognized as known until restart |
| 15 | Global controls (urgency, exploration, effort) | Derived from PAD, fatigue (`agent_state.py:879-896`) | `action_candidate.py:164-178` candidate scoring | **WIRED** (derived view) | `AFFECT_CONTROL_ENABLED` defaults True (`config.py:221`). Inherits the self-loop of row 1 |
| 16 | User mental model (ToM) | LLM ToM call, `update_theory_of_mind` | `action.py:_build_tom_context` | **WIRED**, point estimates (NF-26) | Read after the affect update (per V3 doc) |
| 17 | Metacognitive directive / calibration | none | none | **DECORATIVE** | `calibration.py:58` `evaluate_directive`, `DomainCalibration`: no callers outside the file |
| 18 | Plan verifier / executor / simulation | none on the live path | none | **DECORATIVE** | `plan_verifier`, `plan_executor` are assigned in `pipeline.py:89-115` and `core.py:101-102` and never used; `simulation.py` imported only by itself |
| 19 | "Cognitive workspace" store | Session-state persist | `pipeline.py:141-144,633-634` epoch/revision | **A compare-and-swap persistence layer** | Not a competition-and-broadcast workspace (NF-38). `WORKSPACE_AUTHORITATIVE: True` (`config.py:231`) |

Reading the table against the owner's frames:

- **W1. The reactive layer is real; the deliberative layer is mostly a latency label.** The code's "deliberative" tier is System-2 appraisal (which arrives after the update it would inform), the intent LLM call and one self-correction retry. There is no impasse mechanism that escalates when reactive knowledge is insufficient (rows 17, 18 are the planning and monitoring pieces and both are dead). Contrast Soar, where deliberation is entered *because* the reactive layer lacked knowledge, and exited by compiling the result [O].
- **W2. The reflective layer writes into places nothing reads.** Rows 8, 9, 10 and 11. The persona path was supposed to be the identity-as-slow-variable mechanism (DR-004, DR-011, DR-020, DR-038); under default config it has no effect. W11 and DR-038 are the owner's own decisions on this; code has not caught up.
- **W3. Closure through the user is missing for the two variables that most define a relationship (rows 1, 2).** A loop closed on itself is stable and unfalsifiable by the user. This is the sharpest instance of the owner's "closed loop" concern: it is closed in the wrong place.
- **W4. The subconscious has one path to behavior (row 13).** "A subconscious that thinks between conversations" is true for proactive-thought generation and for decay/relink housekeeping. The monologue and dream content never reach any reader. The only quantified benefit I found for offline compute (sleep-time compute, +13% and +18% accuracy when queries are predictable [A]) is for precomputing answers, not for what the subconscious currently produces.
- **W5. Identity is a gate, not a variable.** `IMMUTABLE_CORE`, `PersonaPolicy.precheck` (`pipeline.py:420`) and `validate_response` (`pipeline.py:374`) enforce constraints structurally. That is the correct response to E8 (prompt-held persona drifts). But nothing slow writes a self-description that later decisions read, so "identity runs across the layers" is currently a constraint that runs across them, not a state.
- **W6. We can ablate mechanisms, not states.** `evals/brainbench/switchboard.py` defines arms as Config overrides (e.g. `-reappraisal`, `-affect-control`). There is no harness to clamp, shuffle or transplant a state value. Trace schemas (`backend/app/cognitive/trace.py:19-50`) cover `memory.search`, `affect.update`, `trust.update`, `proactive.decision`, `arbitration.decision` and nothing about how a state value changed the reply.
- **W7. Names promise more than code.** "Global workspace" (row 19), "System 3", "dream insight", "ACT-R decay" with a count/recency approximation (NF-1). V3's rule (rename or make real, `12-neuroscience-grounding.md` "What we will not do") already covers this; V4 should apply it to the layer vocabulary too.

---

## 5. Limits

| Limit | Class | Why |
|---|---|---|
| Whether any closed loop of internal state involves experience | **UNKNOWABLE** | The owner has already recorded this. Anthropic's own emotion work states it cannot tell [O]. Not a target |
| A validated measure of "organization of a mind" for a conversational system | **SCIENCE** | No accepted criterion. The nearest are indicator-property credences (Butlin et al. [A]), architecture-feature surveys (which the survey authors say cannot support comparison [O]), and human-data probes (V3 W12). None validated against a companion |
| Intervening on the LLM's own hidden state for a frontier model reached through local Claude Code | **RESOURCE** | No weight or activation access. Activation patching, steering and concept injection require open weights; on the 8 GB GPU box only small models are feasible. External-ledger interventions are unaffected |
| Whether the displayed state in the prompt is actually used, and by which model | **ENGINEERING** | Testable now with clamp/swap on the ledger, per model tier (D3). Result depends on model (E9) |
| Slow-variable dynamics (baseline drift over weeks, trait corroboration) observed in real time | **RESOURCE** (time) | Real deployment is weeks to months. Lifesim already compresses time (V3), so evaluation of dynamics is **ENGINEERING** with the caveat that lifesim stimuli are synthetic |
| Whether prediction-error or global-workspace organization beats a plain ledger for a companion | **SCIENCE** | No comparative evidence either way |
| A valid test of identity stability across months | **SCIENCE** | Drift is documented for prompt-held persona (E8) but there is no accepted metric for a self-authored, slowly evolving identity |
| Wiring the dead and open paths in section 4 | **ENGINEERING** | Known how; the code exists or the design is specified in DR-017, DR-020, DR-038, W11 |
| Attributing a state's effect to the right channel when the LLM paraphrases it | **SCIENCE** (partial) | Mediation analysis helps for explicit channels (sampling parameters, arbitration choices). For text, LLM noise and rationalization (E11) cap what can be shown; use deterministic behavior metrics first |

---

## 6. What this means for V4: ranked candidate design moves

Ranking is by (evidence strength that it matters) x (cost) x (it unblocks the others). Each move names the measurable outcome and its dependencies.

**M1. Build the state ledger and make it a machine-checked artifact.** ENGINEERING.
- What: one declarative registry (code, not prose) listing every state item as in section 4 with columns: timescale class (L0 to L3), tier, writers, readers, intervention handle, behavior metric. A gate test fails when a declared item has no reader, no writer, or no handle, the same pattern as `test_doc_drift.py`.
- Outcome: number of OPEN-W, OPEN-R, DECORATIVE items. Today 9 of 19 (rows 4, 7 to 11, 17, 18 are open or decorative, plus row 19 as mislabeled). Target: each is closed, deleted, or explicitly marked `quarantined` with a reason.
- Depends on: nothing. Prerequisite for M2 and M5.

**M2. State-level intervention harness in BrainBench ("lesion and transplant").** ENGINEERING.
- What: extend `evals/brainbench/switchboard.py` beyond Config flags with four state operators on any ledger row: *clamp* (constant), *shuffle* (permute the value history within a session), *transplant* (replace session A's trajectory with session B's under identical inputs), *dose* (grid of values). Measure deterministic behavior first: arbitration choice, initiation decision, retrieval ranking, sampling parameters, prosody parameters. Then judged text metrics with paired seeds or temperature 0, many draws, clustered statistics as in V3's BrainBench with Holm correction.
- Outcome: per-row influence estimate with confidence interval, and a pre-registered rule such as "closed if clamping changes the target metric by at least the smallest effect of interest with the interval excluding zero". Includes a mediation split where two channels exist (prompt text vs sampling parameters for hormones).
- Depends on: M1 (which rows, which metrics); the W2/W3 fixes, otherwise intervening on mood or trust measures a variable that was never coupled to the user. Model tier named in every result (D3).
- Why first among the science items: it is the only move that answers the owner's actual question ("how would anyone tell") and it needs no new theory.

**M3. Close mood and trust through the user before adding any new layer.** ENGINEERING (V3 W2/W3 carried into V4).
- Outcome: r(user valence, agent mood) greater than 0 under the clamp/shuffle test of M2 (shuffling the *user's* text across sessions should destroy the correlation; if it does not, the loop is still self-closed); trust moves in the right direction after rupture and does not saturate.
- Depends on: V3 W2, W3; DR-008, DR-013.

**M4. Give the reflective layer a landing zone (identity as slow rows).** ENGINEERING plus design decisions already made.
- What: resolve the conflict between `LEARNING_REVIEW_REQUIRED=True` (`config.py:278`) and DR-020; give the baseline PAD a writer with corroboration (DR-038) and a drift band (DR-011); persist and read a self-description row.
- Outcome (lifesim, accelerated months): slow-row trajectories inside the pre-registered drift band; immutable-core items unchanged (probe); adaptive items change only after N corroborating reflections; a transplant test (M2) shows the identity rows change later replies. Drift of identity vs the prompt-only baseline (E8) is the comparison.
- Depends on: M1, M2, W11.

**M5. Decide the fate of the subconscious's outputs.** ENGINEERING with one owner decision (Q2 below).
- Option A: make monologue/dream *candidates* for the initiation path with provenance tags ("unverified thought"), never written to memory as fact (keeps Invariant 12). Option B: delete the generation (saves LLM calls on the 8 GB box).
- Outcome for A: ablation of the candidate stream changes anticipation metrics (hit rate of precomputed topics in the next session, proactive useful rate) by more than the control; if no change, choose B. Sleep-time compute suggests where to look first: precomputing for predictable user needs [A].
- Depends on: M1, M2, DR-019, DR-026.

**M6. Wire a cheap metacognitive monitor that changes behavior.** ENGINEERING.
- What: derive confidence from the existing `last_search_trace` (retrieval score margins, contradiction flags) and use `calibration.py`'s directives (`HEDGE`, `ASK_CLARIFICATION`, `ABSTAIN`), or delete the file. Matches the Common Model's "low-level appraisals as metacognitive assessment grounded in fixed sensors" [O].
- Outcome: abstention precision on unanswerable memory questions (LongMemEval-style abstention), expected calibration error of stated confidence, and hedge rate vs M5-style ablation.
- Depends on: V3 W1 (supersession) so the confidence signal is not wrong for the M-5 reason.

**M7. Impasse-triggered deliberation and compile-down (Soar pattern).** SCIENCE for our domain; lower rank.
- What: escalate to the deliberative path only on a measured insufficiency (low retrieval margin, conflicting appraisals, goal conflict); store repeated deliberation outcomes as cached policies through reflection.
- Outcome: share of turns on the deliberative path, p50/p95 latency within DR-024, quality at equal or fewer LLM calls.
- Depends on: M1, M6, tracing. Evidence base is Soar on task domains [O]; none for companions.

**M8. Rename or implement "global workspace".** ENGINEERING (rename) or SCIENCE (implement).
- Recommend rename now (cheap, follows V3's labeling rule). Build competition-and-broadcast only with a pre-registered ablation, because the 2025 and 2026 LLM implementations I found report none [A x2] and Cogitate challenges the human theory [S].

**Not recommended:** adopting predictive processing as the organizing frame (no evidence for this use); building a new "identity layer" before the identity rows have writers (M4 first).

Ordering: M1, M2 in parallel with M3; M4 after M2; M5 and M6 after M2; M7 and M8 last.

---

## 7. Open questions for the owner

1. **Definition of closed.** Is "an intervention on the external state ledger changes a pre-specified behavior metric" enough, given that the LLM's own hidden state is outside it and cannot be intervened on for a frontier model through Claude Code? (A definitional choice; evidence cannot settle it. If yes, M2 is the test. If no, closure claims are limited to open-weight models on the GPU box.)
2. **Quarantine vs use.** If monologue/dream content becomes an input to initiation (M5-A), does it stay quarantined from memory as fact (Invariant 12) with only a "thought" provenance, or should it stay log-only? (A product and safety call.)
3. **DR-033 and experiments.** DR-033 says no reset by design. The Always-On survey lists recoverability and rollback as an unmet governance axis [A]. Is a developer-only snapshot and restore for experiments (never product-facing) consistent with DR-033? M2's transplant operator depends on it.
4. **Dead code.** Should rows 17 and 18 (calibration, planning/simulation) be deleted, or are they reserved for a V4 deliberative layer? Keeping them unreferenced fails V3's own labeling rule.
5. **Frame choice.** Frame C (ledger with tiers and loops) replaces the owner's two frames as the working vocabulary. If the owner prefers to keep the three-layer language for external explanation, I would keep it as the *tier* names only.

---

## 8. Citation list

**Opened and read (text)**

- Laird, J. E. (2022). *Introduction to Soar.* arXiv:2205.03854. https://arxiv.org/abs/2205.03854 (full PDF read).
- Kotseruba, I., and Tsotsos, J. K. (2020; online 2018). *40 years of cognitive architectures: core cognitive abilities and practical applications.* Artificial Intelligence Review. arXiv:1610.08602 https://arxiv.org/abs/1610.08602 (full PDF read). DOI 10.1007/s10462-018-9646-y (DOI from a search result, not opened).
- Rosenbloom, P. S., Laird, J. E., Lebiere, C., Stocco, A., Granger, R. H., and Huyck, C. (2024). *A Proposal for Extending the Common Model of Cognition to Emotion.* Proc. 22nd International Conference on Cognitive Modeling. arXiv:2412.16231 https://arxiv.org/abs/2412.16231 (full PDF read).
- Ortony, A., Norman, D. A., and Revelle, W. (2003 draft; in Fellous and Arbib (eds.), *Who Needs Emotions?*, OUP). *Affect and Proto-affect in Effective Functioning.* https://personality-project.org/revelle/publications/Ortony-Norman-Revelle.pdf (full draft read).
- Rao, A. S., and Georgeff, M. P. (1995). *BDI Agents: From Theory to Practice.* Proc. ICMAS-95, AAAI. https://cdn.aaai.org/ICMAS/1995/ICMAS95-042.pdf (first pages read).
- Anthropic Interpretability (2026-04-02). *Emotion concepts in a large language model.* https://www.anthropic.com/research/emotion-concepts-function (page read).
- Anthropic (2025). *Signs of introspection in large language models.* https://www.anthropic.com/research/introspection (page read; the 20% quote).
- LIDA: Wikipedia, *LIDA (cognitive architecture).* https://en.wikipedia.org/wiki/LIDA_(cognitive_architecture) (secondary source, read).

**Opened, abstract page only**

- Cox, M. T., et al. (2016). *MIDCA: A Metacognitive, Integrated Dual-Cycle Architecture for Self-Regulated Autonomy.* AAAI-16. https://ojs.aaai.org/index.php/AAAI/article/view/9886
- Heins, C., et al. (2025). *AXIOM: Learning to Play Games in Minutes with Expanding Object-Centric Models.* arXiv:2505.24784. https://arxiv.org/abs/2505.24784
- Goyal, A., et al. (2021/2022). *Coordination Among Neural Modules Through a Shared Global Workspace.* ICLR 2022. arXiv:2103.01197. https://arxiv.org/abs/2103.01197
- Butlin, P., et al. (2023). *Consciousness in Artificial Intelligence: Insights from the Science of Consciousness.* arXiv:2308.08708. https://arxiv.org/abs/2308.08708
- Shang, W. (2026). *"Theater of Mind" for LLMs: A Cognitive Architecture Based on Global Workspace Theory.* arXiv:2604.08206. https://arxiv.org/abs/2604.08206
- Nakanishi, J., et al. (2025). *Hypothesis on the Functional Advantages of the Selection-Broadcast Cycle Structure.* Frontiers in Robotics and AI. arXiv:2505.13969. https://arxiv.org/abs/2505.13969
- West, R. L., et al. (2024). *Bridging Generative Networks with the Common Model of Cognition.* arXiv:2403.18827. https://arxiv.org/abs/2403.18827
- Wray, R. E., Kirk, J. R., and Laird, J. E. (2025). *Applying Cognitive Design Patterns to General LLM Agents.* AGI-25. arXiv:2505.07087. https://arxiv.org/abs/2505.07087
- Park, J. S., et al. (2023). *Generative Agents: Interactive Simulacra of Human Behavior.* arXiv:2304.03442. https://arxiv.org/abs/2304.03442
- Park, J. S., et al. (2024). *LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals.* arXiv:2411.10109. https://arxiv.org/abs/2411.10109
- Sumers, T. R., Yao, S., Narasimhan, K., and Griffiths, T. L. (2023/2024). *Cognitive Architectures for Language Agents.* TMLR. arXiv:2309.02427. https://arxiv.org/abs/2309.02427
- Altera.AL (2024). *Project Sid: Many-agent simulations toward AI civilization.* arXiv:2411.00114. https://arxiv.org/abs/2411.00114
- Packer, C., et al. (2023). *MemGPT: Towards LLMs as Operating Systems.* arXiv:2310.08560. https://arxiv.org/abs/2310.08560
- Lin, K., et al. (2025). *Sleep-time Compute: Beyond Inference Scaling at Test-time.* arXiv:2504.13171. https://arxiv.org/abs/2504.13171
- Chhikara, P., et al. (2025). *Mem0.* arXiv:2504.19413. https://arxiv.org/abs/2504.19413
- Wu, D., et al. (2025). *LongMemEval.* ICLR 2025. arXiv:2410.10813. https://arxiv.org/abs/2410.10813
- Maharana, A., et al. (2024). *Evaluating Very Long-Term Conversational Memory of LLM Agents.* arXiv:2402.17753. https://arxiv.org/abs/2402.17753
- Hu, Y., Wang, Y., and McAuley, J. (2025). *Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions.* arXiv:2507.05257. https://arxiv.org/abs/2507.05257
- Wang, Z., Chiu, Y. Y., and Chiu, Y. C. (2023). *Humanoid Agents.* EMNLP 2023 demo. arXiv:2310.05418. https://arxiv.org/abs/2310.05418
- Sun, M., Hong, F., and Zhang, W. (2025). *Sophia: A Persistent Agent Framework of Artificial Life.* arXiv:2512.18202. https://arxiv.org/abs/2512.18202
- Mikeda, A., and Goertzel, B. (2026). *A Motivational Architecture for Conversational AGI.* AGI-26. arXiv:2606.05411. https://arxiv.org/abs/2606.05411
- Shkolnikov, Y. P. (2026). *Artificial Id: Drive and Persistent Alignment in Agentic AI.* arXiv:2609.11911. https://arxiv.org/abs/2609.11911
- Qin, F., et al. (2026). *Toward Natural and Companionable Virtual Agents via Cross-Temporal Emotional Modeling.* CHI '26. arXiv:2605.15812. https://arxiv.org/abs/2605.15812
- Zhao, B., Hu, Y., and Li, X. (2026). *From Stateless to Situated: Building a Psychological World for LLM-Based Agents.* WAICA 2026. arXiv:2603.25031. https://arxiv.org/abs/2603.25031
- Liu, Y., et al. (2026). *CompanionBench.* arXiv:2608.02046. https://arxiv.org/abs/2608.02046
- Ding, T., et al. (2026). *Always-On Agents: A Survey of Persistent Memory, State, and Governance in LLM Agents.* arXiv:2606.30306. https://arxiv.org/abs/2606.30306
- Li, K., et al. (2024). *Measuring and Controlling Instruction (In)Stability in Language Model Dialogs.* COLM 2024. arXiv:2402.10962. https://arxiv.org/abs/2402.10962
- Choi, J., et al. (2024/2025). *Examining Identity Drift in Conversations of LLM Agents.* arXiv:2412.00804 (under review). https://arxiv.org/abs/2412.00804
- Liu, N. F., et al. (2023/2024). *Lost in the Middle: How Language Models Use Long Contexts.* TACL. arXiv:2307.03172. https://arxiv.org/abs/2307.03172
- Vig, J., et al. (2020). *Causal Mediation Analysis for Interpreting Neural NLP: The Case of Gender Bias.* arXiv:2004.12265. https://arxiv.org/abs/2004.12265
- Heimersheim, S., and Nanda, N. (2024). *How to use and interpret activation patching.* arXiv:2404.15255. https://arxiv.org/abs/2404.15255
- Geiger, A., et al. (2023; v4 2025). *Causal Abstraction: A Theoretical Foundation for Mechanistic Interpretability.* arXiv:2301.04709. https://arxiv.org/abs/2301.04709
- Meng, K., Bau, D., Andonian, A., and Belinkov, Y. (2022). *Locating and Editing Factual Associations in GPT.* NeurIPS 2022. arXiv:2202.05262. https://arxiv.org/abs/2202.05262
- Hase, P., Bansal, M., Kim, B., and Ghandeharioun, A. (2023). *Does Localization Inform Editing?* NeurIPS 2023. arXiv:2301.04213. https://arxiv.org/abs/2301.04213
- Turpin, M., Michael, J., Perez, E., and Bowman, S. R. (2023). *Language Models Don't Always Say What They Think.* NeurIPS 2023. arXiv:2305.04388. https://arxiv.org/abs/2305.04388
- Lanham, T., et al. (2023). *Measuring Faithfulness in Chain-of-Thought Reasoning.* arXiv:2307.13702. https://arxiv.org/abs/2307.13702
- Lindsey, J. (2026). *Emergent Introspective Awareness in Large Language Models.* arXiv:2601.01828. https://arxiv.org/abs/2601.01828
- Chen, R., Arditi, A., Sleight, H., Evans, O., and Lindsey, J. (2025). *Persona Vectors.* arXiv:2507.21509. https://arxiv.org/abs/2507.21509

**UNVERIFIED (seen only in a search-result snippet or a secondary summary, not opened, or from memory)**

- UNVERIFIED. Laird, J. E., Lebiere, C., and Rosenbloom, P. S. (2017). *A Standard Model of the Mind.* AI Magazine 38(4):13-26. DOI 10.1609/aimag.v38i4.2744, https://ojs.aaai.org/index.php/aimagazine/article/view/2744 (page fetch failed on a certificate error; its content is used here only as described in Rosenbloom et al. 2024, which I read).
- UNVERIFIED. Anderson, J. R., Bothell, D., Byrne, M. D., Douglass, S., Lebiere, C., and Qin, Y. (2004). *An integrated theory of the mind.* Psychological Review 111(4):1036-1060 (bibliographic data and module description from a search result only).
- UNVERIFIED. Gat, E. (1998). *On Three-Layer Architectures.* In Kortenkamp, Bonasso and Murphy (eds.), *Artificial Intelligence and Mobile Robots*, AAAI Press (fetch failed; controller/sequencer/deliberator description from search snippets).
- UNVERIFIED. Sloman, A., Chrisley, R., and Scheutz, M. (2005). *The architectural basis of affective states and processes.* In *Who Needs Emotions?*, OUP (CogAff: reactive, deliberative, meta-management; page returned 403; description from a search snippet).
- UNVERIFIED. Sun, R. (various). CLARION. The four-subsystem split (action-centered, non-action-centered, motivational, meta-cognitive) and the implicit/explicit levels are from a search summary; the emotion description comes from Kotseruba and Tsotsos, which I read.
- UNVERIFIED. Franklin, S., et al. (various). LIDA and IDA primary papers; figures (about 10 Hz cycles, 300 detailers, $1.5M) are from the Wikipedia summary.
- UNVERIFIED. Friston, K. (2010). *The free-energy principle: a unified brain theory?* Nature Reviews Neuroscience 11:127-138, DOI 10.1038/nrn2787 (abstract text consistent across several search results; the journal page redirected to a login).
- UNVERIFIED. Butlin, P., et al. (2025). *Identifying indicators of consciousness in AI systems.* Trends in Cognitive Sciences (eScholarship fetch failed).
- UNVERIFIED. Cogitate Consortium (2025). *Adversarial testing of global neuronal workspace and integrated information theories of consciousness.* Nature. Described via search results (n = 256; fMRI, MEG, iEEG); not opened. Page: https://research.birmingham.ac.uk/en/publications/adversarial-testing-of-global-neuronal-workspace-and-integrated-i/
- UNVERIFIED. Hu, Y., et al. / authors of *Memory in the Age of AI Agents: A Survey* (2025), arXiv:2512.13564 (search result only).
- UNVERIFIED. Newell, A. (1990). *Unified Theories of Cognition*; time-scale bands (Newell 1992 BBS précis page opened, but the visible text did not contain the table).
- UNVERIFIED. The numeric TrueSkill results and the "about 100 raters" in the Generative Agents evaluation (arXiv PDF exceeded the fetch size limit; secondary summary only).
- UNVERIFIED. BDI + LLM work: arXiv:2509.16784 (hybrid BDI-LLM child helpline agent, 37 participants) and arXiv:2509.02515 (survey), from search snippets only; not used for any claim above beyond the existence of the line of work.
- UNVERIFIED. Control-theory statement on closed loops in section 2D (from general knowledge, no source searched).
- Code claims: all file and line references were read in this session at `caf7f1dd`, except rows 2 and 16 (read from `00-current-architecture.md`) and NF-n attributions (read from `12-neuroscience-grounding.md`; I did not open `research/neuro/fidelity-table.md`).
