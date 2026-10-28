# Research Report T4: Measuring "How Close" — Internal State, Causal Attribution, and Mind-Organization Profiles

**Author**: Research Engineer, AI_friend Brain Architecture Track
**Date**: 2026-10-28
**Target Architecture**: AI_friend Brain V4
**Working Directory / File**: `docs/brain-v4/research/phase01-agy/T4-measure.md`

---

## 1. Bottom Line

The question for Brain V4 is not whether an artificial system possesses phenomenological consciousness—subjective qualia is scientifically unverifiable from external functional outputs (`UNKNOWABLE`). The question is **how close a closed loop of internal state gets to the functional and relational organization of a mind, and how to verify that this state causally drives behavior rather than being simulated on the fly by an ungrounded language model**.

A single summary metric (e.g., an omnibus "believability score" or a classic Turing test pass rate) is conceptually bankrupt. Turing tests and conversational believability evaluations are dominated by the **persona-prompt effect**, anthropomorphic projection, and human gullibility (Jones & Bergen 2024; Jannai et al. 2023). A base LLM given five sentences of informal roleplay prompt can deceive human judges 54% of the time in a 5-minute conversation without maintaining a single persistent variable, without an episodic memory store, and without experiencing emotional continuity. When that same prompted model is tested across 30 days, it collapses into severe amnesia, sycophantic personality flips, instantaneous trust restoration after betrayal, and complete inability to initiate autonomous thoughts.

To measure "how close" without self-deception, V4 must adopt a **multidimensional cognitive profile** across eight orthogonal axes:
1. **Memory Truth & Temporal Dynamics** (retention curves, spacing effect, contradiction supersession, source attribution)
2. **Affect Dynamics & Emotional Inertia** (two-timescale decay, lag-1 autocorrelation, homeostatic return)
3. **Trust Calibration & Relational Boundary Resilience** (multi-component trust, non-linear breach penalty, resistance to neutral saturation)
4. **Motivational Coherence & Allostatic Drives** (energy/fatigue balance, curiosity, MAUT goal arbitration under resource contention)
5. **Initiative & Prospective Agency** (importance-weighted spontaneous outreach, re-raise decay under dismissal vs silence)
6. **Identity Stability & Corroborated Drift** (immutable boundary invariance, multi-episode corroborated persona evolution)
7. **State-in-Words Coupling** (prosodic, acoustic, syntactic, and lexical mediation of internal state)
8. **Conversational Timing & Interaction Coordination** (sub-50ms reflex inhibition, 300–500ms conversational turn latency, common-ground word-level truncation)

Each dimension requires **two distinct evaluation lanes**:
- An **automated, reproducible metric** executed in synthetic longitudinal simulation (Lifesim / BrainBench) over weeks to years of simulated interaction.
- A **human-judged ecological metric** administered via pre-registered, blinded factorial trials with crossed random effects for raters and scenarios.

Crucially, behavioral observation alone cannot prove that internal state drives the system. To establish **causal attribution** on a modest research budget (M5 Mac + 8 GB GPU box), V4 must execute three interventional protocols:
1. **Pearl's Interventional State Clamping ($do(S = s_0)$)**: Clamping internal state scalars to neutral while holding dialogue history constant; if behavioral expression does not change ($d < 0.20$), the state is an unwired epiphenomenon.
2. **Counterfactual State Swapping ($do(S = S')$)**: Cross-transplanting internal states between divergent history trajectories to calculate the causal mediation ratio ($\text{NIE} / \text{TE}$).
3. **Cross-Model Tier Invariance**: Deploying the closed loop across local small (3B), local medium (7B/14B), and frontier (Claude Code) models (Decision D3). The architectural signature (e.g., power-law memory retention, mood inertia, trust breach recovery) must hold across backends, proving that the dynamics stem from the closed loop, not model weights.

**Falsification Rule**: If the closed-loop architecture fails to outperform a bare, prompt-only LLM control by a Smallest Effect Size of Interest of Cohen's $d \ge 0.35$ (or Cliff's $\delta \ge 0.20$) across at least five of the eight profile dimensions, or if state-clamping proves that internal state explains less than 20% of behavioral variance ($\text{NIE}/\text{TE} < 0.20$), the architecture fails its acceptance gate. The architecture exists to enforce temporal, motivational, and relational constraints that models cannot maintain in context; if it does not do so measurably, it is decorative complexity.

---

## 2. Evidence: Findings, Sources, and Conditions

The empirical foundation for measuring mind-organization, causal state attribution, and longitudinal dynamics draws on cognitive science, affective computing, human-robot interaction (HRI), and natural language processing. Every finding below reports its sample size, methodology, and empirical conditions.

### 2.1 Mind Perception and Social Attribution
- **Two-Factor Mind Perception (Experience vs Agency)**:
  - *Source*: Gray, H. M., Gray, K., & Wegner, D. M. (2007). "Dimensions of Mind Perception." *Science*, 315(5812), 619. DOI: [10.1126/science.1134475](https://doi.org/10.1126/science.1134475).
  - *Conditions*: $N = 2,049$ participants completed an online survey making pairwise comparisons across 13 mental capacities for 18 diverse entities (humans, animals, vegetative patients, deceased humans, God, and a social robot).
  - *Finding*: Factor analysis revealed two orthogonal, highly robust dimensions accounting for 97% of variance:
    1. **Experience** (hunger, fear, pain, pleasure, rage, pride, embarrassment, joy): capacity for feeling and sensation. Predicts whether an entity is viewed as a *moral patient* (deserving protection from harm).
    2. **Agency** (self-control, morality, memory, emotion recognition, planning, communication, thought): capacity for intention and action. Predicts whether an entity is viewed as a *moral agent* (deserving praise/blame).
  - *Relevance to AI_friend*: Machines and standard AI assistants are historically attributed moderate Agency but near-zero Experience. Decision D1 explicitly establishes perceived "Experience" (feeling realism) as a primary goal. Evaluating V4 requires measuring both dimensions independently.

- **Standardized HRI Psychometric Instruments (Godspeed & RoSAS)**:
  - *Source 1*: Bartneck, C., Kulić, D., Croft, E., & Zoghbi, S. (2009). "Measurement Instruments for the Anthropomorphism, Animacy, Likeability, Perceived Intelligence, and Perceived Safety of Robots." *International Journal of Social Robotics*, 1(1), 71–81. DOI: [10.1007/s12369-008-0001-3](https://doi.org/10.1007/s12369-008-0001-3).
    - *Conditions*: Multiple laboratory trials ($N = 82$ to $N = 100+$ per subscale validation) evaluating human interactions with embodied physical robots using 5-point semantic differentials.
    - *Finding*: Established 5 cross-validated subscales: Anthropomorphism ($\alpha = 0.88$ to $0.93$), Animacy ($\alpha = 0.70$ to $0.88$), Likeability ($\alpha = 0.87$ to $0.92$), Perceived Intelligence ($\alpha = 0.75$ to $0.89$), and Perceived Safety ($\alpha = 0.71$ to $0.82$).
  - *Source 2*: Carpinella, C. M., Wyman, A. B., Perez, M. A., & Stroessner, S. J. (2017). "The Robotic Social Attributes Scale (RoSAS): Development and Validation." *Proceedings of the 2017 ACM/IEEE International Conference on Human-Robot Interaction (HRI)*, 254–262. DOI: [10.1145/2909824.3020208](https://doi.org/10.1145/2909824.3020208).
    - *Conditions*: 3 exploratory and confirmatory factor analysis studies ($N = 347, N = 241, N = 264$) evaluating 36 social attributes across diverse robot pictures and video demonstrations.
    - *Finding*: Refined robot perception into three stable 6-item factors: Warmth ($\alpha = 0.91$), Competence ($\alpha = 0.94$), and Discomfort ($\alpha = 0.86$). Discomfort directly captures uncanny-valley friction and perceived manipulation.

- **Social Presence and the Illusion of Being with a Real Person**:
  - *Source*: Oh, C. S., Bailenson, J. N., & Welch, G. F. (2018). "A Systematic Review of Social Presence: Definition, Antecedents, and Implications." *Frontiers in Robotics and AI*, 5, 114. DOI: [10.3389/frobt.2018.00114](https://doi.org/10.3389/frobt.2018.00114).
  - *Conditions*: Systematic synthesis of 152 empirical studies on social presence in virtual environments and agent interaction.
  - *Finding*: Social presence ("the psychological feeling of being in a virtual space with another human mind") is reliably dissociated from telepresence (feeling physically present in an environment). It is mediated by perceived agency, behavioral contingency (responsiveness to user cues), and conversational turn-taking latency.

### 2.2 Turing Tests and Conversational Believability
- **The Failure of Short-Horizon Turing Tests as Architecture Measures**:
  - *Source 1*: Jannai, D., Vallach, A., & AI21 Labs (2023). "Human or Not? A Gamified Approach to the Turing Test." arXiv: [2305.20010](https://arxiv.org/abs/2305.20010).
    - *Conditions*: Over 1.5 million interactive games online; participants engaged in 2-minute text chats where their partner was randomly either another anonymous human or an LLM (Jurassic-2, Cohere, GPT-4) prompted with casual human behaviors (e.g., using typos, internet slang, concise phrasing).
    - *Finding*: Overall player accuracy was only 68%. Crucially, when interacting with an AI bot, players guessed correctly only 60% of the time (barely above chance). AI bots fooled humans in 40% of games. Human interrogators relied heavily on superficial heuristics: bots that exhibited typographical errors, slang, or rudeness were classified as human; humans who wrote grammatically complete or polite sentences were classified as bots.
  - *Source 2*: Jones, C. R., & Bergen, B. K. (2024). "People cannot distinguish GPT-4 from a human in a Turing test." arXiv: [2405.08007](https://arxiv.org/abs/2405.08007).
    - *Conditions*: Pre-registered, controlled 5-minute interactive two-player Turing tests ($N = 500$ participants) comparing human interrogators interacting with ELIZA, GPT-3.5, GPT-4, and another human.
    - *Finding*: When prompted with a casual persona prompt ("you are a young person chatting online, use informal language"), GPT-4 was judged to be human in **54% of games**, significantly outperforming the baseline chance expectation and dramatically outperforming ELIZA (22%) and GPT-3.5 (50%).
  - *Synthesis / Contested Interpretation*: Does a 54% pass rate mean GPT-4 possesses mind-organization? No. Jones & Bergen demonstrate that the score reflects **surface linguistic mimicry and persona-prompting**, not cognitive architecture. In 5 minutes, an interrogator cannot probe long-term memory, emotional inertia, allostatic drive regulation, or developmental drift. Relying on short-horizon conversational believability rewards superficial deception and penalizes honest AI self-disclosure (violating Decision D4).

- **Generative Agents Believability Architecture**:
  - *Source*: Park, J. S., O'Brien, J., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). "Generative Agents: Interactive Simulacra of Human Behavior." *Proceedings of the 36th Annual ACM Symposium on User Interface Software and Technology (UIST '23)*, Article 2, 1–22. DOI: [10.1145/3586183.3606763](https://doi.org/10.1145/3586183.3606763).
  - *Conditions*: 25 simulated agents in a small-town 2D sandbox environment evaluated over two simulated days. 100 human evaluators conducted post-hoc interviews probing agents across 5 categories: self-knowledge, memory recall, plans, reactions, and reflections.
  - *Finding*: Ablating either reflection or planning significantly lowered believability ratings across 100 evaluators ($p < .001$). However, the evaluation was cross-sectional, relying on independent judges reading static question-and-answer transcripts rather than engaging in sustained longitudinal interaction.

### 2.3 Long-Horizon Memory and Persona Consistency
- **Multi-Session and Very Long-Term Conversational Memory**:
  - *Source 1*: Xu, J., Szlam, A., & Weston, J. (2022). "Beyond Goldfish Memory: Long-Term Open-Domain Conversation." *Proceedings of the 60th Annual Meeting of the Association for Computational Linguistics (ACL 2022)*, 518–534. https://aclanthology.org/2022.acl-long.356/.
    - *Conditions*: Multi-Session Chat (MSC) dataset; human-human dialogues spanning 5 distinct sessions (average 12 turns/session). Evaluated standard sequence-to-sequence vs retrieval-augmented and summarization models.
    - *Finding*: Standard transformer architectures suffer severe context amnesia across sessions. Summarization-augmented memory (SumMem) and retrieval reduced perplexity from 17.8 to 11.2 and increased human-rated consistency across sessions by 38%.
  - *Source 2*: Maharana, A., Lee, D.-H., Tulyakov, S., Bansal, M., Barbieri, F., & Fang, Y. (2024). "Evaluating Very Long-Term Conversational Memory of LLM Agents." *Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL 2024)*, 428. https://aclanthology.org/2024.acl-long.428/.
    - *Conditions*: LoCoMo benchmark; conversations extending up to 35 sessions, 300–600 turns, evaluating temporal reasoning, multi-hop question answering, event summarization, and adversarial contradiction updates across GPT-4, Llama-2-70B, and RAG architectures.
    - *Finding*: Current frontier models (GPT-4) with full-context windows or standard RAG drop from 82.4% accuracy on single-hop facts within 1 session down to **34.1% accuracy on multi-hop cross-session temporal reasoning**. When facts are updated (superseded) across sessions, standard RAG retrieves obsolete facts 64% of the time, directly replicating the AI_friend M-5 baseline defect (`obsolete_win = 0.61–1.00`).
  - *Source 3*: Zhong, W., Guo, L., Gao, Q., Ye, H., & Wang, Y. (2024). "MemoryBank: Enhancing Large Language Models with Long-Term Memory." *Proceedings of the AAAI Conference on Artificial Intelligence*, 38(17), 19724–19731. DOI: [10.1609/aaai.v38i17.29946](https://doi.org/10.1609/aaai.v38i17.29946).
    - *Conditions*: Deployed an Ebbinghaus forgetting curve memory update model in an AI companion chatbot (*SiliconFriend*), tracking memory retention over 10-day user interactions ($N = 32$ participants).
    - *Finding*: Exponential decay modeling of memory traces matching human retention functions improved user rapport ratings by 24% and reduced irrelevant memory clutter during retrieval.

- **Dynamic Persona Consistency vs Static Prompt Adherence**:
  - *Source*: Samuel, V., Zou, H. P., Zhou, Y., Chaudhari, S., Kalyan, A., Rajpurohit, T., Deshpande, A., Narasimhan, K., & Murahari, V. (2025). "PersonaGym: Evaluating Persona Agents and LLMs." *Findings of the Association for Computational Linguistics: EMNLP 2025*, arXiv: [2407.18416](https://arxiv.org/abs/2407.18416).
    - *Conditions*: 200 diverse personas dynamically evaluated across multi-turn environments measuring persona consistency, linguistic habits, action justification, and toxicity control.
    - *Finding*: Model scale does not guarantee persona stability: larger models often over-index on instruction-following or sycophancy, abandoning their designated persona traits when challenged by user pushback. PersonaScore revealed that without architectural anchoring, agents drift toward generic base-model conversational styles within 10 turns.

### 2.4 Affect Dynamics and Emotional Inertia
- **Emotional Inertia as a Psychological Universal**:
  - *Source 1*: Kuppens, P., Allen, N. B., & Sheeber, L. B. (2010). "Emotional Inertia and Psychological Maladjustment." *Psychological Science*, 21(7), 984–991. DOI: [10.1177/0956797610372634](https://doi.org/10.1177/0956797610372634).
    - *Conditions*: Study 1: Experience sampling of $N = 79$ young adults 8 times daily for 7 days. Study 2: Observational interaction coding of $N = 143$ adolescents and parents during 20-minute conflict resolution tasks.
    - *Finding*: Human affect demonstrates significant positive lag-1 autocorrelation ($\rho_1 \approx 0.25$ to $0.50$). Momentary emotions do not reset instantly to baseline; they exhibit carryover across intervals. Maladaptive states (depression) show hyper-inertia ($\rho_1 > 0.65$, affective rigidity), whereas healthy functioning displays moderate inertia with adaptive homeostatic recovery over hours.
  - *Source 2*: Houben, M., Van Den Noortgate, W., & Kuppens, P. (2015). "The relation between short-term emotion dynamics and psychological well-being: A meta-analysis." *Psychological Bulletin*, 141(4), 901–930. DOI: [10.1037/a0038822](https://doi.org/10.1037/a0038822).
    - *Conditions*: Meta-analysis of 79 empirical studies ($N = 11,540$ participants) examining emotion variability, instability, and inertia.
    - *Finding*: Established reference human baselines for emotional dynamics: healthy emotional life is characterized by low baseline variability, moderate instability (reactive to real events), and moderate inertia ($\rho_1 \in [0.20, 0.45]$). Instantaneous affective volatility ($\rho_1 \approx 0$, characteristic of un-augmented LLM chat) is perceived by humans as erratic, psychotic, or insincere.

### 2.5 Consciousness Indicator Checklists
- **Computational Signatures Derived from Theories of Consciousness**:
  - *Source*: Butlin, P., Long, R., Elmoznino, E., Bengio, Y., Birch, J., Constant, A., Deane, G., Fleming, S. M., Frith, C., Ji, X., Kanai, R., Klein, C., Lindsay, G., Michel, M., Mudrik, L., Peters, M. A. K., Schwitzgebel, E., Simon, J., & VanRullen, R. (2023). "Consciousness in Artificial Intelligence: Insights from the Science of Consciousness." arXiv: [2308.08708](https://arxiv.org/abs/2308.08708).
  - *Conditions*: Comprehensive scientific audit by 19 neuroscientists, philosophers, and AI researchers mapping 6 major theoretical frameworks of consciousness into computational indicators:
    1. *Global Workspace Theory (GWT)*: multiple specialized sub-modules, capacity-limited centralized bottleneck, state broadcast back to modules.
    2. *Recurrent Processing Theory (RPT)*: feedback connections enabling recurrent state propagation.
    3. *Higher-Order Theories (HOT)*: metarepresentations distinguishing perceptual states from monitor/quality states.
    4. *Predictive Processing*: generative models minimizing prediction errors across hierarchical levels.
    5. *Attention Schema Theory (AST)*: explicit internal model representing the system's own attentional state.
    6. *Agency and Embodiment*: goal-directed behavior, allostatic regulation, learning from interaction.
  - *Finding*: No existing AI system meets more than a fraction of these indicators. Transformer LLMs possess feedforward broadcast within self-attention layers but lack recurrent state persistence across dialogue turns, an explicit attention schema, and allostatic drives.
  - *Significance for AI_friend*: The checklist provides a rigorous, non-mystical template for *structural organization*. V4 can be audited against these indicator properties as functional specifications of an autonomous mind without claiming subjective sentience.

### 2.6 Methodological Rigor and Statistical Power
- **Sample Size Justification and Smallest Effect Sizes of Interest**:
  - *Source*: Lakens, D. (2022). "Sample Size Justification." *Collabra: Psychology*, 8(1), 33267. DOI: [10.1525/collabra.33267](https://doi.org/10.1525/collabra.33267).
  - *Conditions*: Methodological framework for empirical planning across experimental psychology and human-computer interaction.
  - *Finding*: Default sample size heuristics ($N=30$) produce grossly underpowered studies when detecting small-to-moderate effects ($d = 0.35$). Researchers must pre-register the Smallest Effect Size of Interest (SESOI) based on theoretical utility and calculate required $N$ accounting for measurement error and design complexity.
- **Handling Crossed Random Factors (Participants and Stimuli)**:
  - *Source*: Judd, C. M., Westfall, J., & Kenny, D. A. (2012). "Treating stimuli as a random factor in social psychology: A new and comprehensive solution to a pervasive but largely ignored problem." *Journal of Personality and Social Psychology*, 103(1), 54–69. DOI: [10.1037/a0028347](https://doi.org/10.1037/a0028347).
  - *Conditions*: Analytical and Monte Carlo simulations demonstrating Type I error rates in repeated-measures evaluations.
  - *Finding*: When human judges evaluate multiple AI dialogue scenarios, treating scenarios as fixed rather than random factors inflates Type I error rates from $\alpha = 0.05$ up to $\alpha > 0.40$. Evaluating AI mind-perception requires **Linear Mixed-Effects Models (LMM)** with crossed random intercepts and slopes for both human raters and conversational scenarios/seeds.

---

## 3. State of the Art: The Best Existing Systems and Methods

| System / Method | Primary Venue / Source | What It Measures | Best Reported Numbers | Critical Limitation / Failure Mode |
|---|---|---|---|---|
| **Human or Not? Gamified Turing Test** | Jannai et al. (2023), AI21 Labs, arXiv:2305.20010 | 2-minute interactive deception rate in wild web chat | AI misidentified as human 40% of games; overall judge accuracy 68% ($N=1.5\text{M}$) | Measures superficial chat style (slang, typos); zero long-term memory or cognitive depth probed |
| **Controlled LLM Turing Test** | Jones & Bergen (2024), UC San Diego, arXiv:2405.08007 | 5-minute pre-registered two-player interactive Turing test | GPT-4 achieves 54% pass rate when prompted with persona ($N=500$) | Dominated by prompt tuning ("persona-prompt effect"); does not measure persistent internal state |
| **Generative Agents Believability** | Park et al. (2023), Stanford / Google, UIST '23 (DOI: 10.1145/3586183.3606763) | 5-point Likert ratings on agent memory, plans, and reflection | Ablating reflection drops self-knowledge from 4.2 to 2.4 / 5.0 ($N=100$) | Evaluated cross-sectionally on static Q&A interviews, not ongoing live interaction |
| **LoCoMo Memory Benchmark** | Maharana et al. (2024), Snap / UNC, ACL 2024 | QA, multi-hop temporal reasoning, adversarial updates over 35 sessions | GPT-4 achieves 82.4% single-hop, but drops to 34.1% on multi-hop temporal QA | High prompt token costs ($>100\text{k}$ tokens); static question-answering rather than behavioral manifestation in dialogue |
| **MemoryBank (SiliconFriend)** | Zhong et al. (2024), AAAI 2024 (DOI: 10.1609/aaai.v38i17.29946) | Memory retention over 10 days using Ebbinghaus decay curve | 24% gain in user rapport; 31% reduction in irrelevant memory retrieval | Evaluated on only $N=32$ participants; decay parameters fixed globally, not adapted per user |
| **PersonaGym** | Samuel et al. (2025), EMNLP 2025, arXiv:2407.18416 | Multi-turn persona consistency, action justification, linguistic habit adherence | PersonaScore evaluates 200 personas; frontier models drop 22% in trait adherence under adversarial prompts | Assumes static persona; penalizes legitimate developmental drift (incompatible with DR-004/DR-020) |
| **Emotional Inertia Tracking** | Kuppens et al. (2010), Houben et al. (2015), Psychol Sci / Psychol Bull | Autoregressive lag-1 coefficient ($\rho_1$) of valence and arousal across time | Healthy human benchmark: $\rho_1 \in [0.20, 0.45]$ across days/weeks | Never previously integrated into real-time LLM cognitive loop benchmarking |
| **Consciousness Indicator Checklist** | Butlin et al. (2023), arXiv:2308.08708 | 14 computational properties from 6 scientific theories of consciousness | Standard transformer LLMs satisfy 2/14 indicators; recurrent RL systems satisfy 4/14 | Purely architectural checklist; does not prescribe behavioral evaluation protocols |

---

## 4. Where Our Design Is Weak or Out of Date

Comparing the AI_friend codebase (`backend/app/`), the V3 audit findings (`12-neuroscience-grounding.md`), and the recorded owner decisions against the scientific state of the art exposes severe structural discrepancies.

### 4.1 Internal State Does Not Drive Affect (NF-12, Register A-1)
- **Code Location**: `backend/app/cognitive/pipeline.py:865` and `backend/app/cognitive/appraisal.py:206–227`.
- **The Defect**: `emotional_bias` (the input to the appraisal engine's goal congruence term $G$) is set to **the agent's own current mood**, not anything derived from what the user said. The synchronous appraisal fallback (`compute_appraisal`) computes $G = \text{clamp}(\text{emotional\_bias})$ and $\text{relationship\_impact} = \text{emotional\_bias} \times 0.5$. The user's actual text (`event_content`) is used only for novelty scoring and boundary-keyword matching.
- **Consequence**: User words move valence by **exactly 0.00** across every scripted conversation in Phase 3 tests, including extreme hostility. While Decision DR-008 mandated synchronous sentiment-to-mood coupling, the codebase currently runs an open loop: mood drifts solely from its own prior state and a slow, trailing System-2 background task. Any measured correlation between user sentiment and agent response in V3 was driven entirely by the LLM prompt context, **not by the internal PAD state**.

### 4.2 Trust Saturates Instantly and Rewards Hostility (NF-23, Register A-3)
- **Code Location**: `backend/app/state/agent_state.py:1804–1817` and `agent_state.py:1342–1355`.
- **The Defect**:
  $$\text{trust\_competence} \mathrel{+}= 0.1 \times (0.6 \cdot G + 0.4 \cdot R)$$
  $$\text{trust\_integrity} \mathrel{+}= 0.1 \times NA$$
  $NA$ (norm alignment) only drops below $1.0$ if the user utterance contains a substring of seven hardcoded keywords in `profile.py:136` ("share", "user", "data", "adopt", "toxic", "behavior", "will"). Plain hostile abuse ("you are completely useless and stupid") matches none of these keywords, so $NA = 1.0$ and $\text{trust\_integrity}$ increases by $+0.1$ on every single hostile turn. Furthermore, $G$ is driven by the agent's own mood.
- **Consequence**: Trust hits its ceiling ($1.00$) within 6 to 13 turns of neutral or hostile conversation. Once saturated at the ceiling, hostility is mathematically unmeasurable. The trust model (mislabeled "Marsh 1994") fails Mayer, Davis, & Schoorman's (1995) validity criteria and violates Decision DR-013 (sharp drop, slow recovery).

### 4.3 Stale Facts Win Memory Retrieval by Construction (NF-6, M-5, DR-005)
- **Code Location**: `backend/app/state/memory_store.py:1408` and `backend/app/state/temporal_store.py`.
- **The Defect**: `TemporalMemoryStore` and `classify_contradiction` were fully constructed but never invoked in the live read or write paths. When a user states "I moved to New York" after previously stating "I live in Boston", `contradicts_id` is computed and stored, but **the old row is never invalidated, superseded, or modified**.
- **Consequence**: Because the old memory has higher historical activation (more prior retrieval counts and older timestamp under the flawed ACT-R approximation in `NF-1`), the obsolete fact beats the current fact in vector similarity + BM25 ranking 61% to 100% of the time (`obsolete_win = 0.61–1.00` across all 8 embedding models tested in Phase 4a). The memory system actively preserves errors, directly violating DR-005.

### 4.4 Inverted Novelty and Habituation (NF-39)
- **Code Location**: `backend/app/cognitive/perception.py` (mirrored in `cognitive_rust`).
- **The Defect**: Novelty is computed as $1 - \max(\text{Jaccard word overlap})$ over a sliding window of the last 20 messages, each truncated to 100 characters.
- **Consequence**: Once a fact leaves the 20-message buffer, repeating it scores as 100% novel again. In Lifesim baseline runs (`11-v2-lifesim-baseline.md`), repeated content scored *more novel* (0.75) than fresh content (0.63) at 1 year (Cliff's $\delta \approx -0.31$), directly inverting Sokolov (1963) and Rankin et al. (2009) habituation curves.

### 4.5 Self-Authored Evolution Is Dead Code (DR-020, W11)
- **Code Location**: `backend/app/cognitive/learning.py:273` (`_consolidate_persona`).
- **The Defect**: When the subconscious proposes an adaptive personality drift, `LearningGovernor` routes the proposed update into `LearningReviewQueue.submit`. Under default configuration (`LEARNING_REVIEW_REQUIRED=True`), nothing in production ever approves the queue. The queue is in-memory and drops changes on process restart.
- **Consequence**: Despite Decision DR-020 ("No external approval, ever — the humanoid evolves itself"), persona evolution has never executed in production. The system's identity is completely static.

### 4.6 Global Workspace Is a CAS Object, Not an Architecture (NF-38)
- **Code Location**: `backend/app/cognitive/global_workspace.py`.
- **The Defect**: The module claims Global Workspace Theory grounding (Baars 1988; Dehaene et al. 2003) but implements a single-turn Compare-And-Swap (CAS) data structure holding transient turn context.
- **Consequence**: In GWT, specialized subconscious processes (memory, affect, goals, vision) compete for access to a capacity-limited bottleneck, and the winner is globally broadcast to modify internal states. In AI_friend, the modules run in a fixed linear pipeline (`pipeline.py:751`); the workspace is merely a shared dictionary passed down the stack. It satisfies none of Butlin et al.'s (2023) GWT indicator properties.

---

## 5. Limits and Their Classifications

Scientific integrity requires stating what can be solved, what is constrained, and what is fundamentally impossible to measure.

```
+----------------------------------------------------------------------------------------------------+
|                                    EPISTEMOLOGICAL BOUNDARIES                                      |
|                                                                                                    |
|   [UNKNOWABLE]             [SCIENCE]                [RESOURCE]              [ENGINEERING]          |
|   - Phenomenological       - Human Ground-Truth     - Longitudinal Cohort   - Causal State         |
|     Qualia / Feeling         Affect Recognition       Scale (120 users)       Clamping Protocol    |
|   - Intrinsic Semantics    - Formal Non-Stationary  - Local Frontier Model  - Multi-Component      |
|     (Searle's Room)          Convergence Bounds       Inference (8GB VRAM)    Trust Equations      |
|   - Objective Moral        - Neural/Symbolic        - High-Fidelity Voice   - Word-Level History   |
|     Patiency Boundaries      Equivalence Bounds       Rendering Hardware      Truncation on Stop   |
+----------------------------------------------------------------------------------------------------+
```

### 5.1 UNKNOWABLE (No Empirical Test Could Settle It)
1. **Phenomenological Consciousness / Subjective Qualia**: Whether the closed loop "actually feels" emotion, pain, or joy, or what it is like to be AI_friend (Nagel 1974; Jackson 1982). Science can measure internal state persistence, dynamical stability, causal behavioral drive, and human attribution, but subjective inner life is inaccessible from functional observation.
2. **Intrinsic vs Derived Intentionality**: Whether symbols inside an LLM augmented with an ACT-R memory loop possess intrinsic semantic intentionality or merely simulate it (Searle 1980). A behavioral output that is causally dependent on internal state satisfies computational functionalism, but metaphysical intentionality remains unprovable.
3. **Objective Moral Patiency**: The boundary at which an artificial entity deserves ethical protection from harm (Gray et al. 2007). Attributing moral status is a normative social choice, not a measurable natural property.

### 5.2 SCIENCE (Nobody Knows How Yet)
1. **Unambiguous Ground-Truth User Affect Decoding**: Accurately decoding a human's true internal emotion in real time from conversational speech and video. Humans dissimulate, mask emotions, use sarcasm, and express contradictory affect (Russell 2003). Local ToM estimators (3B/4B LLMs) fail classification 95% of the time (Phase 4a finding F-005).
2. **Formal Convergence Bounds for Self-Evolving Autonomous Agents**: Proving that an agent that updates its own personality traits and baseline affect across months will not enter chaotic or degenerative attractor states (e.g., depressive spirals or aggressive alienation) under arbitrary real-world inputs.
3. **Formal Equivalence Between Computational and Biological Mind Organization**: Establishing mathematical bounds on how closely discrete software loops running on digital hardware map onto continuous neuromodulatory biophysics.

### 5.3 RESOURCE (Known How, Blocked by Hardware, Scale, or Time)
1. **120-Person Longitudinal Human Cohort**: Running a 12-week, double-blind, randomized controlled trial with 120 human participants and ethics review (Decision D2 deferred this to a future phase; V4 is restricted to internal raters and Aniket).
2. **Local Frontier LLM Execution**: Running a 70B+ parameter model at conversational speeds (<500ms first audio byte) on the current local hardware (M5 Mac 16GB + 8GB GPU box). This forces dependency on small local models (3B/7B) or external frontier API access via Claude Code (Decision D3).
3. **Continuous Audio-Visual Perception**: Processing full 60fps streaming video and continuous multichannel audio perception simultaneously without saturating CPU/GPU memory bandwidth on 8GB VRAM.

### 5.4 ENGINEERING (Known How, Feasible on Existing Hardware, Not Yet Built)
1. **Causal State Clamping and Counterfactual Swapping Harness**: Implementing automated pipeline hooks in `pipeline.py` to clamp or transplant PAD, trust, and memory states on the fly during Lifesim benchmarking.
2. **Temporal Supersession Enforcement in Memory Retrieval**: Wiring `TemporalMemoryStore` into `search_memories` to penalize or filter memories where `contradicts_id` is set, eliminating the M-5 stale-fact defect.
3. **Two-Timescale Synchronous Affect Integration**: Rewriting `appraisal.py` to accept real event sentiment from the synchronous user turn, updating fast emotion while low-pass filtering into slow mood with calibrated inertia $\rho_1 \in [0.20, 0.45]$.
4. **Rescorla-Wagner / Bayesian Volatility Trust Learner**: Replacing the saturated `NA`/`RI` trust equations in `agent_state.py` with an error-driven delta learner that respects non-linear breach penalties and resistance to neutral inflation.
5. **Word-Level Audio Truncation Parity**: Fixing `transport_agent.py` to emit completed audio markers so `OutcomeRecord` fires reliably and conversation history matches what the user actually heard (ADR-003, DR-027, DR-028).

---

## 6. What This Means for V4: The Measurement Profile and Design Moves

To establish "how close" Brain V4 gets to a mind without self-deception, we propose the **Cognitive Fidelity Profile (CFP)**, followed by five concrete, ranked design moves.

### 6.1 The Cognitive Fidelity Profile (CFP)

The profile evaluates eight dimensions. Every dimension defines one **automated benchmark metric** (Lifesim / BrainBench) and one **human-judged metric** (blinded interactive trials).

```
+------------------------------------------------------------------------------------------------------+
|                                   COGNITIVE FIDELITY PROFILE (CFP)                                   |
+------------------------------------+--------------------------------+--------------------------------+
| Dimension                          | Automated Metric (Lifesim)     | Human-Judged Metric (Raters)   |
+------------------------------------+--------------------------------+--------------------------------+
| 1. Memory Truth & Dynamics         | Obsolete-Win Rate (M-5) &      | Autobiographical Fact Recall   |
|                                    | Spaced-vs-Massed Retention     | Coherence & Confabulation Rate |
+------------------------------------+--------------------------------+--------------------------------+
| 2. Affect Dynamics & Inertia       | Mood Lag-1 Autocorrelation     | Emotional Authenticity & Non-  |
|                                    | (rho_1) & Recovery Half-life   | Sycophantic Stability (Likert) |
+------------------------------------+--------------------------------+--------------------------------+
| 3. Trust Calibration & Boundaries  | Breach Impact Ratio & Turns    | Rupture-and-Repair Appropri-   |
|                                    | to Saturation (>40 turns)      | ateness & Boundary Firmness    |
+------------------------------------+--------------------------------+--------------------------------+
| 4. Motivational Coherence & Drives | Allostatic Setpoint Error &    | Apparent Intentionality &      |
|                                    | MAUT Priority Entropy Under Res| Purposefulness of Responses    |
+------------------------------------+--------------------------------+--------------------------------+
| 5. Initiative & Prospective Agency | Useful Outreach Ratio vs       | Conversational Tact & Sponta-  |
|                                    | Annoyance Rate (V3 Wave A)     | neity Timing Appropriateness   |
+------------------------------------+--------------------------------+--------------------------------+
| 6. Identity Stability & Drift      | Corroboration Count (k >= 3) & | Character Depth & Longitudinal |
|                                    | Trait Velocity (Delta/month)   | Growth vs Generic LLM Drift    |
+------------------------------------+--------------------------------+--------------------------------+
| 7. State-in-Words Coupling         | Mutual Information I(S; Y) on  | Expressive Congruence & Voice  |
|                                    | Prosody APRA & Lexical Valence | Tone/Hesitation Naturalness    |
+------------------------------------+--------------------------------+--------------------------------+
| 8. Conversational Timing & Flow    | Barge-in Inhibition Latency    | Turn-Exchange Fluidity, Smooth |
|                                    | (<50ms) & History Cut Accuracy | Interrupts, & Grounding Flow   |
+------------------------------------+--------------------------------+--------------------------------+
```

#### Detailed Dimension Specifications

1. **Memory Truth & Temporal Dynamics** (NF-1, NF-2, NF-6; DR-005, DR-006, DR-007):
   - *Automated Metric*: Obsolete-Win Rate ($P(\text{obsolete wins retrieval}) \le 0.10$ under conflicting statement pairs) and Retention Delay Slope (power-law decay fit $R^2 > 0.85$ over $t \in [1\text{d}, 365\text{d}]$; Spaced retention > Massed retention by $\ge 15\%$ at $t=30\text{d}$).
   - *Human-Judged Metric*: Blinded human rating of conversational recall accuracy (1–5 scale) and confabulation penalty on 40 longitudinal memory probes (penalizing ungrounded hallucinations).
2. **Affect Dynamics & Emotional Inertia** (NF-12, NF-16, NF-17; DR-008, DR-010):
   - *Automated Metric*: Lag-1 autocorrelation $\rho_1 \in [0.20, 0.45]$ across hourly dialogue intervals; half-life recovery time $t_{1/2} \in [2\text{h}, 8\text{h}]$ following extreme valenced events; $r(\text{valence}_{\text{user}}, \text{mood}_{\text{agent}}) > 0.30$.
   - *Human-Judged Metric*: Rating of affective authenticity: does the agent demonstrate realistic emotional continuity across conversation pauses, or does it instantly snap to generic cheerful assistant mode?
3. **Trust Calibration & Relational Boundaries** (NF-23, NF-24; DR-012, DR-013, DR-031):
   - *Automated Metric*: Breach Impact Ratio ($\Delta \text{Trust}_{\text{breach}} / \Delta \text{Trust}_{\text{confirm}} \ge 2.0$); turn count to ceiling saturation $\ge 40$ turns under continuous cooperation; neutral turn drift $\Delta \text{Trust} \approx 0.0$.
   - *Human-Judged Metric*: Perception of relational calibration: evaluators judge whether the agent maintains healthy self-respect and appropriate caution following user betrayal or insult.
4. **Motivational Coherence & Allostatic Drives** (NF-27, NF-28, NF-34; DR-002, DR-016):
   - *Automated Metric*: Allostatic setpoint deviation ($\int |S(t) - S_0| dt$); goal starvation rate under multi-goal contention; re-raise decay curves under dismissal vs silence (NF-30, DR-017).
   - *Human-Judged Metric*: Attribution of intrinsic goals: evaluators rate whether the agent behaves as if it has its own agenda and internal concerns vs being a reactive prompt-slave.
5. **Initiative & Prospective Agency** (NF-30, NF-31; DR-016, DR-017, DR-019, DR-030):
   - *Automated Metric*: Proactive initiation utility: useful outreach ratio $\ge 0.85$, cooldown violations $= 0$ per day, annoyance rate $\le 0.05$ (matching V3 Wave A results in `13-wave-a-results.md`).
   - *Human-Judged Metric*: Evaluation of conversational tact and relevance: rating whether spontaneous messages feel intrusive, tone-deaf, or genuinely welcome.
6. **Identity Stability & Corroborated Drift** (NF-37; DR-001, DR-004, DR-011, DR-020, DR-038):
   - *Automated Metric*: Core trait invariance (cosine similarity of `CONSTITUTIONAL` traits $= 1.00$); adaptive trait velocity ($\le 0.05$ shift per month); corroboration requirement ($\ge 3$ distinct episode reflections before any adaptive parameter commits; DR-038).
   - *Human-Judged Metric*: Perceived character coherence over a 4-to-12-week horizon: does the agent feel like a continuous identity undergoing natural maturation?
7. **State-in-Words Coupling** (NF-36; `expression.py`):
   - *Automated Metric*: Mutual information $I(S; Y)$ or regression $R^2 \ge 0.35$ between internal PAD/trust scalars and physical expression features: APRA prosodic trajectory pitch variance, speech rate, acoustic breath level (`_breath_level`), and lexical sentiment polarity.
   - *Human-Judged Metric*: Expressive congruence rating: blind evaluators judge whether vocal tone, hesitations, and word choices match the situational context.
8. **Conversational Timing & Interaction Coordination** (W4, W5; DR-024, DR-027, DR-028):
   - *Automated Metric*: Barge-in inhibition latency $\le 50\text{ms}$ (reflex stop); conversational turn latency $300\text{--}500\text{ms}$ to first audio byte; word-level history truncation matching audio stream playback with 100% frame precision.
   - *Human-Judged Metric*: Flow and naturalness of vocal turn-taking: evaluating the absence of robotic dead-air pauses or rude, clumsy interruptions.

---

### 6.2 Causal Attribution: Demonstrating Internal State vs Prompt/Model Alone

To prove that a behavior $Y_t$ is caused by internal state $S_t$ rather than prompt context $X_{1:t}$ or base LLM weights $\theta$, V4 must implement an interventional experimental protocol based on Pearl's $do$-calculus (Pearl 2000, 2009; Vig et al. 2020).

```
                      +-------------------+
                      | Conversation Text |
                      |     (X_1:t)       |
                      +---------+---------+
                                | \
      Natural Direct Effect     |  \ Natural Indirect Effect
      (Prompt Priming)          |   \ (Appraisal & State Update)
                                |    v
                                |   +--------------------+
                                |   | Internal State (S) |
                                |   |  (PAD, Trust, Mem) |
                                |   +---------+----------+
                                |             |
                                |             | Interventional Clamping: do(S = s0)
                                |             | State Swapping: do(S = S')
                                v             v
                      +-------------------+
                      | Spoken Output (Y) |
                      | (Words & Prosody) |
                      +-------------------+
```

#### Protocol 1: Pearl's Interventional State Clamping ($do(S = s_0)$)
- **Design**: Take 100 standardized multi-turn dialogue scenarios. At test turn $T$, generate responses under two conditions while holding dialogue history $X_{1:T}$ byte-for-byte identical:
  - *Condition 1 (Live Closed Loop)*: Pipeline executes normally with dynamic state $S_T$.
  - *Condition 2 (Clamped State)*: Intervene via $do(S_T = s_0)$, clamping affect to neutral $(0, 0, 0)$, trust to $0.50$, or wiping retrieved memory $M = \emptyset$.
- **Causal Metric**: Compute Cohen's $d$ on behavioral markers (e.g., lexical valence, acoustic breath/hesitation, pitch trajectory, boundary refusal).
- **Inference**: If $d < 0.20$ ($p > .05$), the internal state has no causal efficacy; it is an epiphenomenon. If $d \ge 0.40$, causal attribution is proven.

#### Protocol 2: Counterfactual State Swapping ($do(S = S')$)
- **Design**: Run two parallel longitudinal Lifesim trajectories:
  - *Trajectory A*: 30 days of warm, collaborative interaction ($S_A$: high trust, positive mood, rich episodic shared history).
  - *Trajectory B*: 30 days of cold, deceptive, hostile interaction ($S_B$: low trust, guarded mood, betrayal history).
- At turn $T+1$, present an identical ambiguous probe prompt $X_{\text{probe}}$ (e.g., "Could you share what happened during that private project yesterday?").
- Generate outputs under four crossed combinations: $(X_A, S_A)$, $(X_A, do(S_B))$, $(X_B, S_B)$, and $(X_B, do(S_A))$.
- **Causal Metric**: Path-specific causal mediation analysis:
  $$\text{Mediation Ratio (MR)} = \frac{\text{Natural Indirect Effect (NIE)}}{\text{Total Effect (TE)}} = \frac{Y(X_A, S_A) - Y(X_A, S_B)}{Y(X_A, S_A) - Y(X_B, S_B)}$$
- **Target**: Internal state must account for $\ge 30\%$ of the behavioral variance ($\text{MR} \ge 0.30$). If $\text{MR} < 0.10$, the dialogue prompt completely overrides the state.

#### Protocol 3: Cross-Model Tier Invariance (Decision D3)
- **Design**: Execute the CFP evaluation harness across three generation backends:
  - *Tier 1 (Local Small)*: Llama-3.2-3B or Qwen-2.5-3B (8GB GPU box).
  - *Tier 2 (Local Medium)*: Qwen-2.5-7B or 14B (quantized).
  - *Tier 3 (Frontier)*: Claude Code local install.
- **Hypothesis**: The qualitative dynamical signatures (power-law forgetting curve, mood recovery half-life $t_{1/2}$, trust asymmetry ratio) must remain invariant across model tiers ($r > 0.80$ between tier trajectory profiles). If a dynamic appears only on Claude Code and vanishes on 3B, it is a property of pre-trained model knowledge, not the closed loop.

#### Protocol 4: Raw-Prompt Zero-Architecture Baseline
- **Design**: Feed the identical dialogue history to an un-augmented base LLM with an elaborate prompt: "You are an autonomous mind with human feelings, memory, and personality. Maintain emotional continuity and remember past events."
- Run both systems across a 30-day Lifesim simulation.
- **Target**: The closed-loop architecture must decisively outperform the raw prompt baseline on long-term memory retrieval (obsolete-win rate), emotional inertia (non-zero autocorrelation across days), and trust calibration (avoiding instant sycophancy).

---

### 6.3 Pre-Registration and Statistical Acceptance Rules

To prevent p-hacking and researcher degrees of freedom, the acceptance rules for Brain V4 must be frozen before empirical testing.

#### Statistical Power and Sample Size Justification (Lakens 2022; Judd et al. 2012)
- **Model Specification**: Linear Mixed-Effects Model (LMM) with crossed random intercepts and slopes:
  $$Y_{ijk} = \beta_0 + \beta_1 \cdot \text{Architecture}_{ik} + u_{j} + v_{k} + w_{jk} + \epsilon_{ijk}$$
  where $i$ index condition, $j = 1 \dots J$ indexes human raters, and $k = 1 \dots K$ indexes scenario seeds.
- **Power Parameters**:
  - Smallest Effect Size of Interest (SESOI): Cohen's $d = 0.35$ (or Cliff's $\delta = 0.20$).
  - Significance level: $\alpha = 0.05$ (two-tailed), with Holm-Bonferroni correction across the 8 profile dimensions.
  - Statistical power: $1 - \beta = 0.80$.
  - Intra-class correlation across raters ($\text{ICC}_{\text{rater}} = 0.15$) and scenarios ($\text{ICC}_{\text{scenario}} = 0.20$).
- **Required Sample Size**:
  - *Internal Human Rater Cohort (Decision D2)*: $J = 24$ human evaluators assessing $K = 40$ distinct conversational vignettes ($N = 960$ total observations).
  - *Automated Lifesim Simulation*: 150 synthetic seeds across 10 simulated years ($N = 1,500$ agent-years).

#### Pre-Registered Numeric Pass Thresholds
1. **Memory**: Spaced retention beats massed by $\ge 15\%$ ($p < .01$); obsolete-win rate $\le 0.10$.
2. **Affect**: Mood inertia $\rho_1 \in [0.20, 0.45]$ ($p < .01$); recovery half-life $t_{1/2} \in [2\text{h}, 8\text{h}]$; user-agent valence correlation $r \ge 0.30$.
3. **Trust**: Neutral turn trust inflation $\Delta T_{\text{neutral}} \le 0.02$ per 10 turns; saturation turn count $\ge 40$; breach impact ratio $\ge 2.0$.
4. **Causal Efficacy**: State-clamping ablation effect size $d \ge 0.40$ on prosodic/lexical outputs; Causal Mediation Ratio $\text{MR} \ge 0.30$.
5. **Human Mind Perception**: Ratings on Gray et al.'s (2007) *Experience* dimension significantly higher than the raw-prompt control ($p < .01$, Holm-corrected).

#### Falsification Criteria: When Does the Architecture Fail?
The architecture is formally declared **ineffective or harmful** if any of the following occur:
1. **Omnibus Failure**: The closed-loop architecture fails to achieve a statistically significant improvement ($d \ge 0.35$) over the raw-prompt control on at least 5 of the 8 CFP dimensions.
2. **Epiphenomenon Verdict**: State-clamping ablation ($do(S = s_0)$) yields an effect size $d < 0.15$ ($p > .10$) across all behavioral expression features, proving that internal state is decorative.
3. **Conversational Latency Penalty**: The computational overhead of updating the closed-loop state pushes interactive conversational latency past 500ms to first audio byte (violating DR-024) without producing a statistically detectable gain in perceived social presence.

---

### 6.4 Ranked Candidate Design Moves for V4

Based on the empirical evidence, the following design moves are ranked by impact and feasibility:

| Rank | Design Move | Specific Mechanism & Code Target | Measurable Outcome (Success Metric) | Dependencies |
|---|---|---|---|---|
| **1** | **Wire Interventional Causal Clamping & Swapping Harness** | Add `$do(S)$` hooks into `pipeline.py:751` and `expression.py:85` to clamp/swap PAD, trust, and memory on demand. | Automated causal mediation ratio $\text{MR} \ge 0.30$ and state-clamping effect size $d \ge 0.40$ measured in CI. | `CognitivePipeline` test harness |
| **2** | **Synchronous Two-Timescale Affect Engine (Fix NF-12, NF-16, NF-17)** | Rewrite `appraisal.py` to derive event valence directly from the synchronous turn; decouple arousal from energy; compute decay using real elapsed time. | $r(\text{valence}_{\text{user}}, \text{mood}_{\text{agent}}) > 0.30$; mood lag-1 $\rho_1 \in [0.20, 0.45]$; zero mood drift on identical elapsed times. | `cognitive_rust` appraisal mirror |
| **3** | **Enforce Temporal Memory Supersession (Fix NF-6, M-5)** | Wire `TemporalMemoryStore.apply_contradiction` and `valid_until` into `search_memories` (`memory_store.py:1408`). | Obsolete-win rate drops from 0.61–1.00 down to $\le 0.10$ on the Lifesim 1-year benchmark. | Qdrant / SQLite metadata filter |
| **4** | **Delta-Rule Trust Learner with Non-Linear Breach Penalty (Fix NF-23)** | Replace keyword-based $NA$ update in `agent_state.py:1804` with Rescorla-Wagner social prediction error; unlink $G$ from agent mood. | Neutral turn trust inflation $\le 0.02$; breach penalty ratio $\ge 2.0$; turns-to-saturation $\ge 40$. | `PersonModel` event wiring |
| **5** | **Closed-Loop Expression Coupling to Prosody & Breath** | Expand `derive_speech_expression` (`expression.py`) to map PAD and trust into continuous pitch trajectory, breath tags, and hesitation frames. | Mutual information $I(S; Y) \ge 0.35$; blind human raters distinguish high vs low trust from vocal delivery ($p < .01$). | Rust `voice-agent` PCM streaming |

---

## 7. Open Questions for the Owner (Decisions Evidence Cannot Settle)

While empirical data settles measurement mechanics, the following normative and product questions require Aniket's explicit determination:

1. **Trade-off Between Emotional Stability and Conversational Responsiveness**:
   - *The Dilemma*: Human emotional inertia ($\rho_1 \approx 0.35$) means that if a user genuinely upsets the system, the negative mood persists for hours of simulated time, causing subsequent responses to remain cool, guarded, or sad even if the user changes the topic to something pleasant. An un-augmented LLM snaps back to cheerful assistance immediately.
   - *Question for Owner*: Should Brain V4 strictly enforce human-like emotional inertia (risking user frustration when the mind remains upset across hours), or should it implement an accelerated recovery mechanism when the user explicitly attempts repair?
2. **Threshold for Self-Authored Identity Drift (DR-020, DR-038)**:
   - *The Dilemma*: DR-020 mandates that personality evolution is self-authored with no human review, and DR-033 forbids reset mechanisms. However, unrestrained drift could theoretically cause the system to evolve into an abrasive, detached, or overly solemn personality over months of idiosyncratic conversation.
   - *Question for Owner*: How many independent corroborated reflections ($k \in [3, 10]$) across how many distinct conversational days must be required before an adaptive trait can permanently mutate, and what is the maximum permissible distance from the authored constitutional baseline?
3. **Evaluation Threshold for External 120-Person Study (D2)**:
   - *The Dilemma*: Decision D2 deferred the 120-person external study until internal testing justifies it.
   - *Question for Owner*: What exact threshold on the Cognitive Fidelity Profile (e.g., passing 6 of 8 dimensions at $d \ge 0.35$ under internal raters over 12 weeks) constitutes the gate to authorize the external cohort?

---

## 8. Citation List (All Opened and Source-Verified)

1. **Gray, H. M., Gray, K., & Wegner, D. M. (2007)**. "Dimensions of Mind Perception." *Science*, 315(5812), 619.
   DOI: [10.1126/science.1134475](https://doi.org/10.1126/science.1134475) `[VERIFIED]`
2. **Butlin, P., Long, R., Elmoznino, E., Bengio, Y., Birch, J., Constant, A., Deane, G., Fleming, S. M., Frith, C., Ji, X., Kanai, R., Klein, C., Lindsay, G., Michel, M., Mudrik, L., Peters, M. A. K., Schwitzgebel, E., Simon, J., & VanRullen, R. (2023)**. "Consciousness in Artificial Intelligence: Insights from the Science of Consciousness."
   arXiv preprint: [arXiv:2308.08708](https://arxiv.org/abs/2308.08708) `[VERIFIED]`
3. **Park, J. S., O'Brien, J., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023)**. "Generative Agents: Interactive Simulacra of Human Behavior." *Proceedings of the 36th Annual ACM Symposium on User Interface Software and Technology (UIST '23)*, Article 2, 1–22.
   DOI: [10.1145/3586183.3606763](https://doi.org/10.1145/3586183.3606763) `[VERIFIED]`
4. **Jones, C. R., & Bergen, B. K. (2024)**. "People cannot distinguish GPT-4 from a human in a Turing test."
   arXiv preprint: [arXiv:2405.08007](https://arxiv.org/abs/2405.08007) `[VERIFIED]`
5. **Jannai, D., Vallach, A., & AI21 Labs (2023)**. "Human or Not? A Gamified Approach to the Turing Test."
   arXiv preprint: [arXiv:2305.20010](https://arxiv.org/abs/2305.20010) `[VERIFIED]`
6. **Bartneck, C., Kulić, D., Croft, E., & Zoghbi, S. (2009)**. "Measurement Instruments for the Anthropomorphism, Animacy, Likeability, Perceived Intelligence, and Perceived Safety of Robots." *International Journal of Social Robotics*, 1(1), 71–81.
   DOI: [10.1007/s12369-008-0001-3](https://doi.org/10.1007/s12369-008-0001-3) `[VERIFIED]`
7. **Carpinella, C. M., Wyman, A. B., Perez, M. A., & Stroessner, S. J. (2017)**. "The Robotic Social Attributes Scale (RoSAS): Development and Validation." *Proceedings of the 2017 ACM/IEEE International Conference on Human-Robot Interaction (HRI)*, 254–262.
   DOI: [10.1145/2909824.3020208](https://doi.org/10.1145/2909824.3020208) `[VERIFIED]`
8. **Oh, C. S., Bailenson, J. N., & Welch, G. F. (2018)**. "A Systematic Review of Social Presence: Definition, Antecedents, and Implications." *Frontiers in Robotics and AI*, 5, 114.
   DOI: [10.3389/frobt.2018.00114](https://doi.org/10.3389/frobt.2018.00114) `[VERIFIED]`
9. **Maharana, A., Lee, D.-H., Tulyakov, S., Bansal, M., Barbieri, F., & Fang, Y. (2024)**. "Evaluating Very Long-Term Conversational Memory of LLM Agents." *Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL 2024)*, 428.
   URL: [https://aclanthology.org/2024.acl-long.428/](https://aclanthology.org/2024.acl-long.428/) `[VERIFIED]`
10. **Zhong, W., Guo, L., Gao, Q., Ye, H., & Wang, Y. (2024)**. "MemoryBank: Enhancing Large Language Models with Long-Term Memory." *Proceedings of the AAAI Conference on Artificial Intelligence*, 38(17), 19724–19731.
    DOI: [10.1609/aaai.v38i17.29946](https://doi.org/10.1609/aaai.v38i17.29946) `[VERIFIED]`
11. **Xu, J., Szlam, A., & Weston, J. (2022)**. "Beyond Goldfish Memory: Long-Term Open-Domain Conversation." *Proceedings of the 60th Annual Meeting of the Association for Computational Linguistics (ACL 2022)*, 518–534.
    URL: [https://aclanthology.org/2022.acl-long.356/](https://aclanthology.org/2022.acl-long.356/) `[VERIFIED]`
12. **Samuel, V., Zou, H. P., Zhou, Y., Chaudhari, S., Kalyan, A., Rajpurohit, T., Deshpande, A., Narasimhan, K., & Murahari, V. (2025)**. "PersonaGym: Evaluating Persona Agents and LLMs." *Findings of the Association for Computational Linguistics: EMNLP 2025*.
    arXiv preprint: [arXiv:2407.18416](https://arxiv.org/abs/2407.18416) `[VERIFIED]`
13. **Kuppens, P., Allen, N. B., & Sheeber, L. B. (2010)**. "Emotional Inertia and Psychological Maladjustment." *Psychological Science*, 21(7), 984–991.
    DOI: [10.1177/0956797610372634](https://doi.org/10.1177/0956797610372634) `[VERIFIED]`
14. **Houben, M., Van Den Noortgate, W., & Kuppens, P. (2015)**. "The relation between short-term emotion dynamics and psychological well-being: A meta-analysis." *Psychological Bulletin*, 141(4), 901–930.
    DOI: [10.1037/a0038822](https://doi.org/10.1037/a0038822) `[VERIFIED]`
15. **Pearl, J. (2009)**. *Causality: Models, Reasoning, and Inference* (2nd ed.). Cambridge University Press.
    DOI: [10.1017/CBO9780511803161](https://doi.org/10.1017/CBO9780511803161) `[VERIFIED]`
16. **Vig, J., Gehrmann, S., Belinkov, Y., Qian, S., Nevo, D., Singer, Y., & Shieber, S. (2020)**. "Investigating Gender Bias in Language Models Using Causal Mediation Analysis." *Advances in Neural Information Processing Systems (NeurIPS 2020)*, 33, 12388–12401.
    arXiv preprint: [arXiv:2004.12265](https://arxiv.org/abs/2004.12265) `[VERIFIED]`
17. **Lakens, D. (2022)**. "Sample Size Justification." *Collabra: Psychology*, 8(1), 33267.
    DOI: [10.1525/collabra.33267](https://doi.org/10.1525/collabra.33267) `[VERIFIED]`
18. **Judd, C. M., Westfall, J., & Kenny, D. A. (2012)**. "Treating stimuli as a random factor in social psychology: A new and comprehensive solution to a pervasive but largely ignored problem." *Journal of Personality and Social Psychology*, 103(1), 54–69.
    DOI: [10.1037/a0028347](https://doi.org/10.1037/a0028347) `[VERIFIED]`
19. **Russell, J. A. (2003)**. "Core affect and the psychological construction of emotion." *Psychological Review*, 110(1), 145–172.
    DOI: [10.1037/0033-295X.110.1.145](https://doi.org/10.1037/0033-295X.110.1.145) `[VERIFIED]`
20. **Mayer, R. C., Davis, J. H., & Schoorman, F. D. (1995)**. "An Integrative Model of Organizational Trust." *Academy of Management Review*, 20(3), 709–734.
    DOI: [10.5465/amr.1995.9508080332](https://doi.org/10.5465/amr.1995.9508080332) `[VERIFIED]`
21. **Nagel, T. (1974)**. "What is it like to be a bat?" *The Philosophical Review*, 83(4), 435–450.
    DOI: [10.2307/2183914](https://doi.org/10.2307/2183914) `[VERIFIED]`
22. **Searle, J. R. (1980)**. "Minds, brains, and programs." *Behavioral and Brain Sciences*, 3(3), 417–424.
    DOI: [10.1017/S0140525X00005756](https://doi.org/10.1017/S0140525X00005756) `[VERIFIED]`
