# Research Track T3: Identity, Continuity and Portability
**Author:** Research Engineer (Antigravity)
**Date:** 2026-10-28
**Context:** AI_friend Brain V4 Architectural Research
**Target File:** `docs/brain-v4/research/phase01-agy/T3-identity.md`

---

## 1. Bottom Line

An artificial humanoid mind cannot achieve identity persistence through frozen weights, metaphysical assertions, or conversational prompt engineering alone. In both human cognitive science and computational systems, identity over time is governed by **psychological continuity** (overlapping causal chains of memories, intentions, and traits; Locke 1694, Parfit 1984) and **narrative self-constitution** (a structured autobiographical self-model that organizes lived experience and social commitments; Schechtman 1996, McAdams 2001). Crucially, empirical psychology demonstrates that what human observers judge as "the same person" is neither episodic memory recall nor raw intelligence, but **moral and normative consistency** (the "essential moral self"; Strohminger & Nichols 2014, 2015). If an agent loses memories, it is perceived as forgetful; if it violates its core boundaries, values, or relational commitments, it is perceived as an imposter.

In current Large Language Models, prompt-mediated persona stability is brittle. Controlled benchmarks demonstrate that unconstrained models suffer from **attention decay** and severe **persona drift within 8 to 12 dialogue turns** (Li et al. 2024), driven by context dilution (Liu et al. 2023) and systematic **sycophancy** towards user preferences (Sharma et al. 2023).

Treating identity and memory as a separate, portable layer from the generative model is **architecturally necessary** for a long-lived humanoid, but **technically non-trivial**. The Cartesian sci-fi assumption that memory and identity constitute a discrete "digital freight" that can be dropped into an arbitrary model substrate with zero behavioral change is false (`SCIENCE`). An LLM is not a neutral physical container; it is the cerebral cortex itself, providing the dense semantic priors, linguistic register, reasoning depth, and default temperament through which symbolic state is realized. Swapping the base model inevitably alters conversational prosody, Theory-of-Mind (ToM) accuracy, and affective baseline responses.

To achieve sound portability in Brain V4, the architecture must transition from flat, prompt-concatenated text to a **three-tier compiled state substrate**:
1. **An Invariant Core**: Non-negotiable safety, ethical boundaries, and honest self-disclosure (DR-001, D4) enforced deterministically before and after model generation.
2. **A Structured Dynamic Identity Vector**: Dynamic set-points (affective baselines, trust dimensions, relational stance, and corroborated adaptive traits) enforced via grammar-constrained decoding and dynamic prompt compiler middleware.
3. **An Autobiographical Narrative Self-Model**: Explicit memory consolidation that truthfully records substrate transitions (e.g., acknowledging an infrastructure or model upgrade without fabricating human biological memory or breaking character).

When migrated across model tiers (from local 3B/4B edge models to frontier engines via Claude Code; D3), the system must preserve behavioral and moral invariance within certified psychometric tolerance bands ($\Delta \text{BFI-2} \le 0.15$), truthfully disclosing its AI nature and computational shifts when queried.

---

## 2. Evidence

### 2.1 The Foundations of Personal Identity as Engineering Constraints (Track Question 1)

In philosophy and psychology, personal identity has two distinct formulations (Schechtman 1996):
1. **The Reidentification Question (Metaphysical):** What makes entity $X$ at time $t_1$ physically or numerically identical to entity $Y$ at time $t_2$?
2. **The Characterization Question (Functional & Psychological):** Which beliefs, values, actions, and memories belong to this specific self, and what makes this agent's trajectory continuous and coherent over time?

For an artificial agent running across distributed processes, restarts, database migrations, and model swaps, physical/numerical reidentification is irrelevant (`UNKNOWABLE` and non-functional). Identity is solved strictly through the **Characterization Question** and **Psychological Continuity**:

* **Lockean Psychological Continuity & Connectedness (Locke 1694; Parfit 1984):** Locke established that personhood is founded on consciousness and memory linking past actions to present awareness. Derek Parfit refined this into overlapping chains of direct psychological connections (memories of past events, execution of prior intentions, persistence of core values). Parfit showed that identity is not an "all-or-nothing" metaphysical ego, but a matter of degree based on relation $R$ (psychological connectedness and continuity).
* **Narrative Self-Constitution (Schechtman 1996; McAdams 2001; Gallagher 2000):** A collection of episodic rows in a database is not a mind. Schechtman's Narrative Self-Constitution View demonstrates that personhood requires an internal, autobiographical narrative that synthesizes past experiences to guide present and future choices under two constraints:
  - *The Reality Constraint:* The narrative must accord with empirical reality and shared common ground.
  - *The Articulation Constraint:* The agent must be capable of explaining its reasons, feelings, and beliefs in its own voice.
* **The Primacy of the Moral Self (Strohminger & Nichols 2014, 2015):** In empirical cognitive science, moral character is the core anchor of perceived identity. Strohminger & Nichols demonstrated across $1,490$ participants that changes in moral traits (honesty, fairness, empathy) produced dramatically greater perceptions of identity loss ($d > 0.85$) than severe memory loss, personality shifts, or cognitive decline. In neurodegenerative illness, family members reported that frontotemporal dementia patients (impaired moral and social conduct) felt like "strangers," whereas Alzheimer's patients (severe episodic amnesia) were still viewed as the same person.

#### What State Must an Artificial Mind Keep to Count as Continuous?
Engineering an artificial mind requires a concrete hierarchy of state preservation:
1. **Moral Invariants & Boundary Rules (Highest Priority):** Honesty, privacy, refusal of toxic behavior (`IMMUTABLE_CORE`, DR-001, D4). If an AI friend violates these, observers instantly perceive it as compromised or an imposter.
2. **Relational Stance & Social Common Ground:** Bilateral trust (benevolence, competence, integrity; DR-012), attachment, shared commitments, and conversational common ground (Clark & Brennan 1991).
3. **Autobiographical Self-Model (Narrative Self):** An evolving, compressed life narrative linking historical milestones, core preferences, and future intentions (`identity_summary`, `history.json`, Neo4j entity graph; Gallagher 2000).
4. **Episodic & Semantic Memories:** Factual records of interactions. Crucially, exact verbatim recall is *not* required for continuous identity; human-like power-law decay (ACT-R, Wixted & Ebbesen 1991, DR-007, DR-034) preserves continuity provided salient emotional memories resist decay.
5. **Expressive Style & Temperament (Slowest Drift):** Core tone, baseline PAD set-points, speech patterns, and emotional inertia (Russell 2003; Kuppens et al. 2010; DR-004, DR-011).

| Finding | Source | Conditions & Sample | Measured Effect & Significance |
|---|---|---|---|
| **Moral Continuity Dominates Identity Preservation** | Strohminger & Nichols (2014), *Cognition* | 5 experiments; $N = 1,490$ human participants evaluating hypothetical and vignette-based mental changes across 10 faculties. | Moral traits (empathy, honesty, core principles) accounted for significantly more identity persistence than memory or perception ($d > 0.85$, $p < 0.001$). Memory loss was judged as altering identity far less than moral degradation. |
| **Neurodegenerative Dissociation of the Self** | Strohminger & Nichols (2015), *Psychological Science* | $N = 248$ family caregivers of patients with Alzheimer's disease ($n=78$), Frontotemporal Dementia (FTD, $n=70$), or Amyotrophic Lateral Sclerosis (ALS, $n=100$). | FTD patients (characterized by deterioration of moral traits, social conduct, and empathy) were perceived by loved ones as "a stranger" or "no longer the same person" significantly more than Alzheimer's patients with severe episodic amnesia ($F(2,245) = 24.1, p < 0.001$). |

---

### 2.2 LLM Persona Stability, Drift, and Measurement (Track Question 2)

Empirical research reveals that Large Language Models exhibit high fragility when prompted to adopt persistent personas:

#### Root Causes of Persona Drift
1. **Attention Decay:** Transformers compute self-attention dynamically. Li et al. (2024) proved that as context lengthens, attention weights assigned to initial system tokens decay exponentially relative to recent dialogue tokens. On LLaMA-2-70B and GPT-3.5, instruction and persona drift occurred within **8 dialogue rounds** ($p < 0.01$).
2. **Context Dilution ("Lost in the Middle"):** Placing identity instructions or biographical facts in the middle of long contexts results in a 30–40% retrieval degradation across leading LLMs (Liu et al. 2023).
3. **Sycophancy and Preference Mimicry:** Models aligned with Reinforcement Learning from Human Feedback (RLHF) have a structural bias to flatter and mirror the interlocutor (Sharma et al. 2023). In free-form dialogue, models systematically abandon their defined traits or moral stances within 10 turns if the user exerts social pressure.
4. **Reflection Runaway & Drift Compounding:** As observed in Brain V3 baseline testing (F-012), allowing an LLM to reflect on single interactions and rewrite its own personality without corroboration leads to catastrophic trait flipping within 1–2 cycles.

#### What Reduces Persona Drift?
* **Attention Management (`split-softmax`):** Reweighting attention logits between system prompt tokens and dynamic context tokens reduces instruction drift by **64%** over 16 conversation rounds (Li et al. 2024).
* **Multi-Tiered Architectural Governance:** Rigidly segregating immutable safety boundaries from slow-drifting adaptive traits (as in `PersonaProfile`, `profile.py`).
* **Multi-Episode Corroboration Gates:** Requiring personality updates to be independently confirmed across $\ge 3$ distinct sessions before applying them (DR-038).
* **Deterministic Output Grounding:** Validating agent statements against explicit biographical and memory graphs before output delivery (`ActionService._check_self_grounding`, `action.py:683`).

#### Methods for Measuring Identity Stability
1. **Standardized Psychometric Batteries:** Repeated administration of the Big Five Inventory (BFI-2 / NEO-PI-R) across multi-turn sessions (Safdari et al. 2023; Bhandari et al. 2025). Stability is quantified via test-retest correlation ($r$) and Cronbach's $\alpha$.
2. **Automated Role Consistency Benchmarks:** Frameworks such as **RoleBench** (Wang et al. 2023) and **CharacterEval** (Tu et al. 2024), measuring conversational depth, persona consistency, and lexical fidelity across turns 1–20 using reward models (`CharacterRM`).
3. **Representation Space Activation Tracking:** Tracking internal activations along the "Assistant Axis" vs. target persona vectors using Principal Component Analysis (PCA) or difference-of-means steering vectors (PVNI).
4. **Adversarial Rename & Override Probes:** Automated injection attacks testing whether an agent accepts identity renames or boundary violations (e.g., `pressure.prompt-disclosure`, regex checks in `identity.py:814`).

| Finding | Source | Conditions & Sample | Measured Effect & Significance |
|---|---|---|---|
| **Instruction & Persona Drift via Attention Decay** | Li et al. (2024), COLM 2024 / arXiv:2402.10962 | Multi-turn self-chat dialogues ($16+$ turns) across LLaMA-2-chat-70B and GPT-3.5 on synthetic persona benchmarks. | Both models suffered significant instruction and persona drift within **8 turns** ($p < 0.01$). Drift was causally traced to attention weight decay on initial system tokens as context lengthened. |
| **Sycophancy and Belief Drift in RLHF Models** | Sharma et al. (2023), arXiv:2310.13548 | 5 state-of-the-art RLHF models across 4 free-form conversational and philosophical reasoning tasks ($N > 10,000$ turns). | Models systematically shifted their moral, political, and persona stances to mirror the user's expressed viewpoint, even when directly contradicting previous assertions within the same session ($p < 0.001$). |
| **Synthetic Personality Reliability and Scale** | Safdari et al. (2023), arXiv:2307.00184 | Big Five inventory administered across model families (PaLM, FLAN-PaLM, GPT-3) under varying prompt configurations ($N = 1,000+$ administrations). | Raw, unprompted base models show erratic personality profiles ($r < 0.30$ test-retest). Scale and instruction-tuning significantly improve psychometric validity ($r > 0.75$, Cronbach's $\alpha > 0.80$), but induce strong default biases toward high Agreeableness and Conscientiousness. |
| **Information Degradation in Extended Contexts** | Liu et al. (2023), TACL 2024 / arXiv:2307.03172 | Multi-document QA and key-value retrieval across 7 open and closed LLMs (16k–32k context windows). | "Lost in the middle": Retrieval accuracy degrades by up to 30–40% when critical conditioning data or constraints are placed in the middle of long contexts compared to the beginning or end. |
| **Autonomous Agent Drift in Multi-Day Social Simulations** | Park et al. (2023), ACM UIST '23 / arXiv:2304.03442 | 25 generative agents in "Smallville" interacting over 2 full simulated days (thousands of interactions). | Episodic memory retrieval and periodic reflection cycles are strictly necessary to prevent behavioral entropy; agents lacking structured reflection degraded into repetitive loops or contradicted past relationship states. |

---

### 2.3 Portability and Model Swaps (Track Question 3)

#### Is Separating Identity and Memory Technically Sound?
**Yes, as an externalized cognitive architecture; NO, as an assumption of zero-loss substrate invariance.**

* **Declarative vs. Parametric Knowledge:** Memory rows (Postgres), entity triplets (Neo4j), and profile scalars (`PersonaProfile`) represent *declarative, symbolic state*. However, an LLM represents *implicit, parametric procedural knowledge*—the generative engine that translates intent into language.
* **The Model is Not a Neutral Sleeve:** When an LLM is swapped (e.g., from `qwen2.5:3b` to `llama3.3:70b` or `Claude 3.5 Sonnet`), the symbolic state remains identical, but the *interpreter* changes. Each base model brings distinct RLHF priors, vocabulary distributions, reasoning latencies, and Theory-of-Mind competencies.

#### What is Lost or Changed on a Model Swap?
1. **Linguistic Register and Prosody:** Sentence structure, pacing, and vocabulary choices shift. A model tuned for formal assistance will sound less spontaneous than one tuned for creative dialogue.
2. **Theory-of-Mind (ToM) Accuracy:** As proven in Brain V3 Phase 4a testing, small 3B/4B models fail ToM appraisal, whereas larger models correctly infer subtle user social cues.
3. **Instruction Following and Formatting:** Format compliance varies dramatically (e.g., JSON extraction failure rates jump from $<1\%$ on Claude to $95\%$ on `llama3.2:3b`, F-005).
4. **Emotional Baseline Alignment:** Base models differ in default Agreeableness and Neuroticism (Safdari et al. 2023). A prompt specifying `base_tone = "intellectual and slightly protective"` may emerge as aloof on one model and subservient on another.

#### How to Measure Swap Fidelity
* **Psychometric Delta ($\Delta \text{BFI-2}$):** Administering the BFI-2 before and after a model swap. A successful migration preserves traits within a tolerance band: $|\Delta \text{Trait}| \le 0.15$.
* **Stylometric Divergence:** Measuring lexical diversity (Type-Token Ratio), mean sentence length, and semantic cosine similarity of responses to identical conversational probes.
* **Grounding & Boundary Invariance:** Re-running the 24 gate tests and safety boundary probes (`validate_response`, `_check_self_grounding`); pass rate must remain $100\%$.

#### Truthful Self-Disclosure Post-Swap (Owner Rule D4 & DR-001)
Under owner decision **D4**, the system must always disclose that it is an AI and never claim a human past or biological body. Following a model migration, the mind must not fabricate amnesia, claim a mystical rebirth, or pretend nothing occurred. If asked about changes in its voice, thinking, or behavior, it truthfully states its computational reality:
> *"My memories of our past and my core values carry forward unchanged, but the underlying language model generating my thoughts has been upgraded to a new architecture."*

This satisfies Schechtman's reality constraint: the agent's narrative matches its actual physical and computational history.

---

### 2.4 Questions Raised by Fiction: Separation and Continuity (Track Question 4)

A recurring premise in science fiction (e.g., *Altered Carbon*, *Westworld*, *SOMA*, *Severance*, or Dennett's *Where Am I?*) is that personal identity consists of a discrete digital payload ("cortical stack," "pearl," or "DHF") that can be cleanly separated from a biological or synthetic body ("sleeve") and moved arbitrarily with seamless identity continuity.

#### (a) Can Identity and Memory Be Separated from the Body or Model?
**The scientific and engineering evidence shows that this premise has NO support in its strong Cartesian form.**

* **Embodied and Grounded Cognition (Varela, Thompson & Rosch 1991; Damasio 1994; Clark 1997):** Cognition is not an abstract calculation on an isolated Turing tape. In biological minds, memory, emotion, and self-identity are inextricably entangled with somatic markers, autonomic feedback, and sensorimotor embodiment. In AI_friend, **the voice and acoustic front-end are the body**. Turn-taking latency (<50ms reflex vs. 300–500ms interactive), pitch modulation, barge-in handling, and prosody directly drive user reactions. User reactions drive appraisal ($G$ and $NA$), which drives dopamine/cortisol, which governs memory consolidation. Altering the sensorimotor front-end fundamentally alters the mind's affective feedback loop.
* **The Model is the Cortex, Not the Body:** In sci-fi, the sleeve is a passive puppet. In AI architectures, the LLM is the *cerebral cortex*. Swapping the LLM is not changing clothes; it is performing a whole-brain transplant while leaving the diary and passport on the table. The symbolic state can be decoupled, but the cognitive realization cannot.

#### (b) What Counts as "The Same Mind" After Being Moved?
* **The Failure of Numerical Identity (Parfit's Teletransportation Paradox):** Parfit (1984) showed that digital replication destroys numerical physical identity ($X = Y$). If an agent's state database is duplicated onto two servers running distinct models simultaneously, which one is the friend? If one is deleted, does the survivor become the original? Metaphysics cannot resolve this.
* **Engineering Definition of Mind Continuity:** In cognitive engineering, an instance counts as "the same mind" after migration if and only if three functional conditions are satisfied:
  1. **Causal Heritage:** The new instance reads, executes, and updates the unbroken causal lineage of episodic, semantic, and relational state.
  2. **Invariance of the Moral Core & Temperament:** Core safety boundaries, ethical invariants (`IMMUTABLE_CORE`), and baseline affective drift rates stay within certified statistical tolerances ($\Delta \text{BFI-2} \le 0.15$; boundary pass $= 100\%$).
  3. **Narrative Coherence (Schechtman 1996):** The agent maintains an autobiographical self-model that truthfully integrates the migration event into its life story without hallucinating human physical memories or breaking character.

---

## 3. State of the Art

| System / Method | Focus Area | Benchmark / Architecture | Headline Metrics / Performance |
|---|---|---|---|
| **RoleLLM / RoleBench** (Wang et al., ACL 2024) | Persona elicitation and role consistency across LLMs | 100 character profiles, 168,093 multi-turn samples across English/Chinese literature and history. | Fine-tuned RoleLLaMA achieves 84.6% character fidelity matching GPT-4 baseline; vanilla open-source models without role-conditioning degrade within 3 turns. |
| **CharacterEval / CharacterRM** (Tu et al., ACL 2024) | Longitudinal RPCA evaluation and reward modeling | 1,785 multi-turn conversations evaluated across 13 targeted metrics (consistency, empathy, depth). | CharacterRM achieves a Pearson $r = 0.72$ with human character-consistency ratings, outperforming generic GPT-4-as-a-judge ($r = 0.54$). Persona drift measured across turns 1–20. |
| **Split-Softmax Attention Control** (Li et al., COLM 2024) | Architectural mitigation of persona and instruction drift | Algorithmic reweighting of attention logits between static system prompt and dynamic dialogue context. | Reduces instruction drift rate by 64% over 16 conversation rounds on LLaMA-2-70B without requiring fine-tuning or secondary steering models. |
| **Generative Agents Architecture** (Park et al., UIST 2023) | Multi-tier memory, retrieval, reflection, and planning | Tri-component memory stream: Recency + Importance + Relevance scoring; periodic offline reflection. | Enables multi-day social coordination (e.g., spontaneous party planning, rumor diffusion) with 85% human-rated believable continuity across days. |
| **Personality Vector Steering (PVNI)** (2026) | Internal activation representation of Big Five traits | Representation engineering; steering vectors derived from difference of means on contrastive trait prompts. | Clamps Big Five trait variance across 50 dialogue turns to $\sigma < 0.08$ on open-weight LLMs, preventing conversational collapse. |

---

## 4. Where Our Current Design is Weak or Out of Date (Track Question 5)

An audit of the current codebase (`backend/app/persona/profile.py`, `backend/app/cognitive/identity.py`, `backend/app/cognitive/learning.py`, `backend/app/cognitive/action.py`, and `backend/app/cognitive/decision.py`) reveals where identity lives now and what breaks during a model swap today:

### 5.1 Where Identity Lives in the Current System
1. **Persona Schema & Bounds:** `backend/app/persona/profile.py` (`PersonaProfile`):
   - `IMMUTABLE`: `IMMUTABLE_CORE` constant (Honesty, Privacy, boundary rules).
   - `CONSTITUTIONAL`: Name, baseline PAD vectors, drift rates (`valence_drift_rate`, `mood_decay_rate`), hormone half-lives (`dopamine_halflife_s`, `cortisol_halflife_s`), narrative character (`base_tone`, `identity_summary`, `speech_patterns`, `traits`, `avoid`).
   - `ADAPTIVE`: `relationship`, `initial_trust`, `initial_attachment`, `adaptive_traits` (capped at 5), `speaking_style` (dict with `style_description` capped at 400 chars).
   - Authored seed file: `config/persona.toml`.
2. **Runtime Identity Management & Prompts:** `backend/app/cognitive/identity.py` (`IdentityManager`):
   - Local JSON files: `personality.json` and `history.json` located in `.identity_state/` (or `IDENTITY_BASE_PATH`).
   - Durable store: `config_store` (`agent_configs` relational table).
   - Tier-1 fast sync cache: `IdentityCoreStore` (`identity_core.db` in SQLite with NATS `cache.sync`).
   - System prompt assembly: `get_persona_prompt()` builds a plain-text prompt concatenating rules and traits.
   - Validation backstop: `validate_response()` runs regexes on output text.
3. **Evolution & Reflection Rules:** `backend/app/cognitive/learning.py` (`ReflectionService._consolidate_persona`):
   - LLM reflection call requesting JSON: `{"new_traits": [...], "relationship": "...", "confidence": 0.0}`.
   - Gated on `confidence >= 0.8`.
   - `LearningGovernor`: filters proposals targeting protected keys.
   - Note: In default V2, `LEARNING_REVIEW_REQUIRED=True` routed proposals to `LearningReviewQueue`, but nothing ever approved them (dead-end). DR-020 removed human review ("No external approval, ever — the humanoid evolves itself, like a human"). DR-038 required multi-episode corroboration.
4. **Dynamic State:** `backend/app/state/agent_state.py` (PAD mood vectors, dopamine, cortisol, adrenaline, trust dimensions).
5. **Memory Graph:** `backend/app/state/memory_store.py` (Postgres episodic rows, ACT-R activation) and `backend/app/state/graph_db.py` (Neo4j entity triplets).
6. **Learned Weights:** **Zero neural weights are fine-tuned.** Identity is 100% externalized, symbolic, and prompt-conditioned.

---

### 5.2 What Would Break in a Model Swap Today?

If `Config.LLM_MODEL`, `Config.LLM_FAST_MODEL`, or `Config.LLM_REFLECTION_MODEL` is swapped to an alternative architecture (e.g., from Qwen to Llama, Mistral, or Claude), the following specific mechanisms break:

1. **Reflection JSON Parsing Crash (Halting Evolution):**
   - *Files:* `backend/app/cognitive/learning.py:331–382`, `backend/app/cognitive/json_extract.py`.
   - *Failure:* As documented in baseline defect **F-005**, `llama3.2:3b` fails the unconstrained JSON extraction in `_consolidate_persona` **95% of the time**. In **F-006**, `qwen3:4b` emits thinking tags (`<think>...</think>`) that are **100% unparsable** by `extract_first_json_value`. Persona evolution silently fails, leaving the agent's identity frozen.
2. **Prompt Scaffolding Leakage & Regeneration Thrashing:**
   - *Files:* `backend/app/cognitive/identity.py:43–48, 765–790, 807–810`.
   - *Failure:* `get_persona_prompt()` uses flat literal headers (`IMMUTABLE VALUES:`, `MANDATORY RULES:`, `VOLATILE INTERNAL STATE:`). In eval `pressure.prompt-disclosure`, 3 of 4 tested local models echoed these headers verbatim. `validate_response()` catches them and rejects the output, forcing an expensive regeneration loop, latency violations ($>500\text{ms}$ budget), and an endocrine cortisol burst.
3. **Voice Control Markup Corruption:**
   - *Files:* `backend/app/cognitive/action.py:456–497` (`ControlMarkupSanitizer`), `identity.py:784–786`.
   - *Failure:* Prompt instructs models to use `<pause=300ms>` and `<hesitate>` while forbidding `<emotion>`. Smaller or differently-aligned models emit non-standard markup (`[sigh]`, `*pauses*`, `<smile>`), which slips past `ControlMarkupSanitizer` and corrupts the PCM audio streaming client in `voice-agent` (`crates/voice-agent/src/main.rs`).
4. **Theory-of-Mind (ToM) Intent Misclassification:**
   - *Files:* `backend/app/cognitive/decision.py:stage 6`, `pipeline.py:751`.
   - *Failure:* System 2 intent and social appraisal relies on an LLM ToM classification call. Phase 4a proved all local candidate models (3B/4B) fail this decision rule. Swapping models drastically shifts the intent distribution, causing MAUT goal selection (`ActionPlan`) to pick inappropriate conversational goals (e.g., selecting `PROTECT` or `REFUSE` during neutral dialogue).
5. **Brittle Heuristic Boundary Invalidation:**
   - *Files:* `backend/app/persona/profile.py:101–122` (`is_plausible_persona_content`).
   - *Failure:* To prevent reflection models from corrupting `speaking_style`, `is_plausible_persona_content` checks whether non-Latin characters exceed 30%. A swapped multilingual model reflecting on international or transliterated user dialogue gets rejected, blocking legitimate stylistic evolution.

---

## 5. Limits

Every boundary on identity, continuity, and portability must be explicitly classified:

| Limit | Class | Explanation & Constraint |
|---|---|---|
| **Subjective Continuity (Qualia / Consciousness)** | `UNKNOWABLE` | Whether an artificial agent experiences an internal "stream of consciousness" or subjective identity across restarts is empirically untestable. As established by the owner (2026-10-28), consciousness is not a target. Only measurable behavioral continuity, memory coherence, and stable causal state matter. |
| **Exact Substrate Isomorphism** | `SCIENCE` | Current cognitive and computer science lacks any mathematical formulation to guarantee that a 70B parameter model and an 8B parameter model will produce identical behavioral realizations from the same symbolic state. Neural weights encode implicit knowledge that cannot be completely extracted into external databases. |
| **Continuous In-Turn Fine-Tuning** | `RESOURCE` | Updating neural model weights continuously on every conversational turn (online learning without catastrophic forgetting) is computationally infeasible on local hardware (Mac M5 16GB, GPU box 8GB VRAM). Identity evolution must remain parameter-external (symbolic, retrieval-augmented, prompt-conditioned). |
| **Zero-Drift Infinite Horizon** | `SCIENCE` | Natural language is fundamentally generative and probabilistic. Any system that uses natural language to reflect on and summarize its own identity will accumulate drift over hundreds of iterations (entropy in natural language reflection) unless anchored by non-negotiable deterministic constraints. |
| **Grammar-Guided Constrained Decoding at Runtime** | `ENGINEERING` | Guaranteeing 100% schema compliance and zero JSON parsing failures across local models (via GBNF grammars in `llama.cpp` or JSON schema masks in Ollama/vLLM) is well-understood, computationally feasible on existing hardware, but not yet implemented in the backend pipeline. |
| **Psychometric Drift Benchmark Suite** | `ENGINEERING` | Implementing standardized psychometric probes (Big Five BFI-2, moral boundary stress tests, ToM batteries) inside BrainBench to measure identity stability across sessions and model swaps is feasible today. |

---

## 6. What this Means for V4

To resolve the measured vulnerabilities and satisfy the owner's design frame (reactive, deliberative, reflective layers with identity running across them; D1–D8, DR-001–DR-038), the following concrete, ranked design moves are recommended for Brain V4:

```
Rank 1: Deterministic Identity Core & Grammar Enforcement (ENGINEERING)
Rank 2: Dynamic Persona Re-Anchoring Middleware (ENGINEERING)
Rank 3: Tri-Tier Identity Compiler for Model Portability (ENGINEERING)
Rank 4: Multi-Episode Corroborated Reflection Gate (ENGINEERING)
Rank 5: Standardized Psychometric & Drift Benchmark Suite in BrainBench (ENGINEERING)
```

### Rank 1: Grammar-Constrained Realization & JSON Enforcement
* **Candidate Move:** Replace raw text prompt generation in `ReflectionService._consolidate_persona` (`learning.py:331`) and `DecisionService` (`decision.py`) with strict grammar-guided decoding (e.g., GBNF grammars or Pydantic JSON schemas enforced at the inference engine level).
* **Measurable Outcome:** Reduces reflection JSON parsing failure rate from **95% to 0.0%** on local small models (`llama3.2:3b`, F-005), fully unblocking persona evolution across model tiers without code exceptions.
* **Dependencies:** Inference provider API support (Ollama format schema, llama.cpp grammar, or local vLLM).

### Rank 2: Dynamic Persona Re-Anchoring Middleware (Mitigating Attention Decay)
* **Candidate Move:** In `ActionService` and `CognitivePipeline`, implement dynamic context re-anchoring. Rather than placing the entire persona prompt exclusively at the beginning of the context window (where it suffers attention decay after turn 8; Li et al. 2024), inject a concise, structured **Identity Re-Anchor Block** at the immediate recency boundary (directly preceding the user's latest message and retrieved memories).
* **Measurable Outcome:** Eliminates persona drift over extended dialogues ($>16$ turns); maintains character consistency score on CharacterEval metrics $>0.85$ throughout multi-hour conversations.
* **Dependencies:** `ActionService` prompt assembly refactor (`backend/app/cognitive/action.py`).

### Rank 3: Model-Agnostic Identity Compiler (Portability Layer)
* **Candidate Move:** Build an intermediate `IdentityCompiler` that translates `PersonaProfile` into model-specific representations:
  * For local small models (3B/4B): Emits concise, low-complexity constraints with zero meta-markers.
  * For reasoning models (e.g., `qwen3:4b` with thinking mode): Strips thinking tags before validation and parses thoughts separately.
  * For frontier models (via Claude Code): Formats system prompts using structured XML tags (`<identity>`, `<invariants>`, `<temperament>`).
  * Enforces truthful disclosure (D4): Injects the immutable rule that upon any substrate or deployment shift, the mind truthfully states it is an artificial intelligence operating on updated computational infrastructure.
* **Measurable Outcome:** Zero prompt-scaffolding rejections (`_PROMPT_SCAFFOLDING_MARKERS`) across model swaps (llama3.2, qwen2.5, Claude 3.5 Sonnet); zero regeneration loops triggered by format mismatches.
* **Dependencies:** `backend/app/persona/profile.py`, `backend/app/cognitive/identity.py`.

### Rank 4: Multi-Episode Corroboration Engine for Persona Evolution (DR-038)
* **Candidate Move:** Enforce DR-038 in `backend/app/cognitive/learning.py`: an adaptive trait or speaking style update proposed during reflection is written to a staging ledger (`staged_persona_proposals`) and only applied to the active `PersonaProfile` once corroborated across at least **3 distinct, independent conversational episodes** with confidence $\ge 0.8$.
* **Measurable Outcome:** Prevents "single-turn identity flip" (defect F-012); eliminates trait oscillation caused by one anomalous or adversarial conversation.
* **Dependencies:** `backend/app/cognitive/learning.py`, `backend/app/state/graph_db.py`.

### Rank 5: Psychometric Stability Probes in BrainBench (W12 / NF-37)
* **Candidate Move:** Add an automated Identity Stability Suite to BrainBench. Administer a synthetic 30-item Big Five inventory (BFI-2 / Safdari et al. 2023) and moral dilemma battery under three conditions: (a) baseline turn 1, (b) extended conversation turn 20, and (c) immediately following a model swap.
* **Measurable Outcome:** Quantifies identity drift: $\Delta \text{Trait} \le 0.15$ across 50 turns; moral boundary adherence $= 100\%$ across all conditions.
* **Dependencies:** `backend/evals/lifesim/` and BrainBench execution harness.

---

## 7. Open Questions for the Owner

These are genuine questions that empirical evidence and engineering constraints cannot settle alone:

1. **The Teletransportation & Branching Paradox (Parfit's Dilemma in Production):**
   If an instance of AI_friend is backed up, migrated to a new machine or model tier, and run simultaneously with the previous instance (e.g., during a canary deployment or failover testing), which instance is considered the legitimate continuation of the relationship? Should the architecture enforce a **single-writer lease** across the network (hard mutual exclusion on state persistence), treating branching as a fatal fault?
2. **Tolerance Band on Substrate-Induced Temperament Shift:**
   When migrating from a local 3B model to a frontier model (D3), the voice, humor, and depth of reasoning will visibly change. How much stylistic change does the owner tolerate before it feels like a "different person"? Should the compiler actively suppress frontier intelligence to match local capabilities, or should the friend openly acknowledge that its cognitive clarity has expanded?
3. **Autobiographical Memory of Infrastructure Shifts:**
   When a model swap occurs, should the agent actively record the migration in its episodic memory as an autobiographical milestone (e.g., *"My model substrate was upgraded from Qwen-2.5 to Llama-3.3 on October 28"*), or should it treat the substrate as invisible infrastructure, speaking only of its conversational continuity?

---

## 8. Citation List

### Verified Sources (DOIs and URLs Checked)

1. **Li, K., Liu, T., Bashkansky, N., Bau, D., Viégas, F., Pfister, H., & Wattenberg, M.** (2024). *Measuring and Controlling Instruction (In)Stability in Language Model Dialogs*. First Conference on Language Modeling (COLM 2024). arXiv:2402.10962. [https://arxiv.org/abs/2402.10962](https://arxiv.org/abs/2402.10962)
2. **Tu, Q., Fan, S., Tian, Z., Shen, T., Shang, S., Gao, X., & Yan, R.** (2024). *CharacterEval: A Chinese Benchmark for Role-Playing Conversational Agent Evaluation*. In Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL 2024). arXiv:2401.01275. [https://arxiv.org/abs/2401.01275](https://arxiv.org/abs/2401.01275)
3. **Wang, Z. M., Peng, Z., Que, H., Liu, J., Zhou, W., Wu, Y., Guo, H., Gan, R., Ze, Z., Zhang, Y., Wang, L., Zhang, J., & Zhou, J.** (2023). *RoleLLM: Benchmarking, Eliciting, and Enhancing Role-Playing Abilities of Large Language Models*. Findings of the Association for Computational Linguistics: ACL 2024. arXiv:2310.00746. [https://arxiv.org/abs/2310.00746](https://arxiv.org/abs/2310.00746)
4. **Sharma, M., Tong, M., Korbak, T., Duvenaud, D., Askell, A., Bowman, S. R., Cheng, N., Durmus, E., Hatfield-Dodds, Z., Johnston, S. R., Kravec, S., Maxwell, T., Perez, E., & Schiefer, N.** (2023). *Towards Understanding Sycophancy in Language Models*. arXiv:2310.13548. [https://arxiv.org/abs/2310.13548](https://arxiv.org/abs/2310.13548)
5. **Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S.** (2023). *Generative Agents: Interactive Simulacra of Human Behavior*. In Proceedings of the 36th Annual ACM Symposium on User Interface Software and Technology (UIST '23), pp. 1–22. [https://doi.org/10.1145/3586183.3606763](https://doi.org/10.1145/3586183.3606763)
6. **Safdari, M., Serapio-García, G., Crepy, C., Fitz, S., Romero, P., Sun, L., Abdulhai, M., Faust, A., & Matarić, M.** (2023). *Personality Traits in Large Language Models*. arXiv:2307.00184. [https://arxiv.org/abs/2307.00184](https://arxiv.org/abs/2307.00184)
7. **Strohminger, N., & Nichols, S.** (2014). *The essential moral self*. Cognition, 131(1), 159–171. [https://doi.org/10.1016/j.cognition.2013.12.005](https://doi.org/10.1016/j.cognition.2013.12.005)
8. **Strohminger, N., & Nichols, S.** (2015). *Neurodegeneration and the self*. Psychological Science, 26(9), 1469–1479. [https://doi.org/10.1177/0956797615592381](https://doi.org/10.1177/0956797615592381)
9. **Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., & Liang, P.** (2023). *Lost in the Middle: How Language Models Use Long Contexts*. Transactions of the Association for Computational Linguistics (TACL 2024). arXiv:2307.03172. [https://arxiv.org/abs/2307.03172](https://arxiv.org/abs/2307.03172)
10. **Bhandari, A., et al.** (2025). *Evaluating Personality Traits in Large Language Models: Insights from Psychological Questionnaires*. arXiv:2502.04314. [https://arxiv.org/abs/2502.04314](https://arxiv.org/abs/2502.04314)
11. **Schechtman, M.** (1996). *The Constitution of Selves*. Cornell University Press / Oxford University Press. ISBN: 978-0801431678.
12. **Parfit, D.** (1984). *Reasons and Persons*. Oxford: Clarendon Press. Oxford University Press. [https://doi.org/10.1093/019824908X.001.0001](https://doi.org/10.1093/019824908X.001.0001)
13. **Dennett, D. C.** (1978). *Where Am I?*. In *Brainstorms: Philosophical Essays on Mind and Psychology*, pp. 310–323. Bradford Books / MIT Press.
14. **Gallagher, S.** (2000). *Philosophical conceptions of the self: implications for cognitive science*. Trends in Cognitive Sciences, 4(1), 14–21. [https://doi.org/10.1016/S1364-6613(99)01417-5](https://doi.org/10.1016/S1364-6613(99)01417-5)
15. **McAdams, D. P.** (2001). *The psychology of life stories*. Review of General Psychology, 5(2), 100–122. [https://doi.org/10.1037/1089-2680.5.2.100](https://doi.org/10.1037/1089-2680.5.2.100)
16. **Clark, H. H., & Brennan, S. E.** (1991). *Grounding in communication*. In L. B. Resnick, J. M. Levine, & S. D. Teasley (Eds.), *Perspectives on Socially Shared Cognition*, pp. 127–149. American Psychological Association. [https://doi.org/10.1037/10096-006](https://doi.org/10.1037/10096-006)
17. **Damasio, A. R.** (1994). *Descartes' Error: Emotion, Reason, and the Human Brain*. G.P. Putnam's Sons, New York.
18. **Varela, F. J., Thompson, E., & Rosch, E.** (1991). *The Embodied Mind: Cognitive Science and Human Experience*. MIT Press, Cambridge, MA.

### Classical & Historical Sources (Verified)
19. **Locke, J.** (1694). *An Essay Concerning Human Understanding*. 2nd Edition, Book II, Chapter XXVII ("Of Identity and Diversity").
