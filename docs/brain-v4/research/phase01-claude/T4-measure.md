# T4. Measuring "how close": a profile, a causal test, a pre-registered rule

Researcher: Claude (Sonnet 5.5), track T4. Date: 2026-10-28.
Read from the repo: `docs/brain-research-v3/README.md`, `00-current-architecture.md`, `12-neuroscience-grounding.md`, `decisions/INDEX.md`, `docs/brain-v4/06-decisions.md`, and code under `backend/app/` and `backend/evals/` to check claims. No other file under `docs/brain-v4/` was opened. `06-decisions.md` refers to `03-realism-eval.md` and others; I did not read them, so nothing below inherits from them.

Conventions: every limit is tagged `SCIENCE`, `RESOURCE`, `UNKNOWABLE` or `ENGINEERING`. Citations are in section 8; anything I did not open is marked UNVERIFIED there and in line.

---

## 1. Bottom line

1. **Use a vector, not a score, and make the vector causal.** A profile of eight dimensions (section 6.1), each with one automatic and one human-judged measure, reported per model tier with intervals. The dimension that makes this about *internal state* rather than style is a ninth cross-cutting one: **causal reach**, the effect on later behavior of intervening on a named state variable while holding inputs fixed. Without it, every other number can be bought with a prompt.
2. **The prompt is a strong confounder and the evidence says so loudly.** In Jones & Bergen (2025), the same model (GPT-4.5) was judged human 73% of the time with a humanlike persona prompt and 36% with a minimal prompt (1,023 games, 5-minute chats). Persona prompts do not reliably improve factual accuracy (Zheng et al. 2024: 162 roles, 4 model families, 2,410 questions, no improvement). So style and humanness move with the prompt and are weak evidence of a mind. Our own code has the same property: state reaches the LLM only through a three-bin English directive, `get_behavioral_directive()` at `backend/app/state/agent_state.py:2743-2772`.
3. **The comparison V3 never ran is the one that decides V4.** `backend/evals/brainbench/switchboard.py:24-76` has arms that toggle V3 mechanisms. It has no arm for "bare model + persona prompt + full history in context" or "+ plain retrieval". A 2026 longitudinal memory instrument found a full-history baseline tied or beat the best memory system at the short horizon (Spencer 2026, preprint). Until that baseline is in the ladder, "the architecture helps" cannot be stated.
4. **Pre-register "closer" as a conjunction.** The architecture must beat the matched-token baseline on pre-named primary dimensions by more than a smallest effect of interest, must show an effect in the state-intervention test, must hold under blinded human ratings (not only an LLM judge), and must not regress elsewhere (equivalence test). The default outcome if any leg fails is "no evidence the architecture helps beyond the baseline", with a named type of null (section 6.3).
5. **Some things cannot be measured, and the report should say so by construction.** Phenomenal experience is `UNKNOWABLE`. Which consciousness theory is right is `SCIENCE`; the largest adversarial test so far (Cogitate, 2025, n = 256) challenged key claims of both theories it pitted. The only defensible claim ceiling is organizational and behavioral (section 6.4 gives a claim ladder and wording).
6. **Where the evidence says no:** (a) a Turing-style pass rate is not the target and is barred by D4 anyway; (b) mind-perception questionnaires measure the rater, not the system, and raters' consciousness attributions track usage frequency and prior beliefs (Colombatto & Fleming 2024); (c) LLM judges cannot replace the human panel: no debiasing can cut required ground-truth labels by more than half when the judge is no better than the evaluated model (Dorner et al. 2024); (d) affect-dynamics measures such as inertia carry little information beyond mean and variance in human data (Dejonckheere et al. 2019), so they are checks on plausibility, not proof of realism.

---

## 2. Evidence

### 2.1 Survey of existing evaluations

| Instrument | Measures | Validity evidence (conditions) | Cost | Known failure modes |
|---|---|---|---|---|
| **Three-party Turing test** (Jones & Bergen 2025) | Which of two witnesses a lay interrogator calls human | Two pre-registered studies, UCSD undergrads n=126 (445 games) and Prolific n=158 (576 games). GPT-4.5 persona 73%, LLaMa-3.1 persona 56%, GPT-4.5 no-persona 36%, LLaMa no-persona 38%, ELIZA 23%, GPT-4o no-persona 21%. ELIZA served as manipulation check. | High: ~1,000 human-labelled 5-minute games. | Persona-prompt effect (73 vs 36). Verdicts mostly gut feeling (style 27%, dynamics 23% of stated reasons). Authors say it measures humanlikeness and substitutability, not intelligence; 5-minute horizon, lay judges. Needs the AI to *pass as human*, which D4 forbids. |
| **Generative-agents believability ranking** (Park et al. 2023) | Rank of interview answers by believability, 5 conditions | 100 Prolific raters, within-subject, one question per each of 5 categories. TrueSkill mu: full 29.89, no reflection 26.88, no reflection/planning 25.64, human crowdworker-authored 22.95, no memory/planning/reflection 21.21; Kruskal-Wallis H(4)=150.29. | Medium: 100 raters x ~30 min at $15/h. | Ablated arms read the *same* accumulated memory, so the design is conservative on effect size and not a re-simulation. The reported d=8.16 is computed on TrueSkill rating posteriors, not on rater-level variance (my reading), so it cannot be used as a planning effect size. Ranking interviews tests recall and self-description, not behavior over time. |
| **Mind perception** (Gray, Gray & Wegner 2007) | Experience and Agency ratings of characters | 2,399 surveys, 2,040 respondents; 13 characters x 18 capacities; two factors, Experience 88% of variance (eigenvalue 15.85), Agency 8%. | Low. | Factor structure is over *character means* (13 points), so it places a target relative to reference characters; it does not validate a score for one agent. Items are static capacity judgments, not conversational. |
| **Folk consciousness attribution** (Colombatto & Fleming 2024) | Single-item credence that ChatGPT has phenomenal experience | N=300 US adults (Prolific, quota-matched), July 2023: 67% gave non-zero credence, mean 25.56/100. Experience-related mental-state ratings predict it (r=0.65), intelligence ratings do not (r=0.03). Users rated higher (29.59) than non-users (19.37). | Low. | One item; credence vs degree ambiguity; priming by the explanatory text; usage link is correlational; one product, one date. This is a measure of the rater. |
| **Godspeed** (Bartneck et al. 2009; meta-analysis Weiss & Bartneck 2015) | Anthropomorphism, animacy, likeability, perceived intelligence, safety (5-point semantic differentials) | Cronbach alpha 0.70-0.93 across studies (anthropomorphism 0.856-0.929; animacy 0.702; likeability 0.842-0.923; perceived intelligence 0.75-0.92). | Low. | Built for robots in short exposures. Anthropomorphism and animacy overlap (one shared item, and later work finds items loading on each other's factors). Weiss & Bartneck report no new reliability or validity evidence. |
| **Social presence** (Harms & Biocca 2004, "networked minds") | Co-presence, psychological involvement, behavioral engagement | UNVERIFIED beyond title and abstract seen in search: designed to separate face-to-face from mediated levels. | Low. | Unknown to me; do not cite numbers. |
| **LongMemEval** (Wu et al., ICLR 2025) | 5 memory abilities: extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention | 500 curated questions in scalable chat histories; commercial assistants and long-context LLMs lost 30% accuracy across sustained interactions. | Low-medium (LLM-judged answers). | Answer keys built from generated chats; LLM judge; static question time. |
| **LoCoMo** (Maharana et al. 2024) | QA, event summarization, multimodal dialogue over very long conversations | Mean ~300 turns, ~9K tokens, up to 35 sessions; humans verified/edited. Long-context and RAG help but trail humans. | Low. | Search results in 2026 describe it as near saturation; leaderboard numbers are self-reported (UNVERIFIED secondary). |
| **MemoryAgentBench** (Hu, Wang, McAuley; rev. 2026) | 4 competencies: accurate retrieval, test-time learning, long-range understanding, selective forgetting | Incremental multi-turn formats from existing and new datasets; conclusion: no current method masters all four. No numbers on the abstract page. | Medium. | Adapted from long-context datasets; no affect, no identity. |
| **Ground Truth First / Veracium** (Spencer 2026, single-author preprint) | Fact-validity-interval questions, 15 types, ~380 questions, 3 and 9 week horizons | 5 memory architectures + no-memory control + full-history baseline, 3 replicates, versioned LLM judge. Ranking inverts with horizon: curated-map memory 96% to 72% recall of evicted content; provenance-typed graph rises to 90%. Inversion in all 6 users, exact p=0.031. Full-history baseline ties or beats best memory system at short horizon. | Medium (re-judging with a second model family was done). | Synthetic users; n=6 for the inversion; not peer reviewed. Valuable as a design template (gold answers valid by construction), weak as a number. |
| **PersonaGym** (Samuel et al., EMNLP Findings 2025) | PersonaScore from generated questions | 10 LLMs, 200 personas, 10,000 questions; GPT-4.1 scored the same as LLaMA-3-8B. Human-agreement figure not on the page I read. | Low (LLM judge). | Judge-scored; shows model size does not buy persona adherence. |
| **Instruction/persona drift** (Li et al., COLM 2024) | Persona adherence across self-chat turns | LLaMA2-chat-70B and GPT-3.5 drift measurably within 8 rounds; attributed to attention decay. | Low. | Self-chats, not users; short horizon (turns, not weeks). |
| **PICon** (Kim et al. 2026) | Internal, external (web-checkable) and retest consistency under chained questioning | 7 groups of persona agents vs 63 human participants; all fail the human baseline on all three dimensions. | Medium. | Persona-agent framing (stand-ins for people), fact-checkable claims only. Closest existing template for an identity-stability measure with a *human reference*. |
| **Psychometric personality tests on LLMs** (Serapio-García et al. 2023, preprint of NMI paper) | Big Five by questionnaire, shaped by prompt | 18 LLMs; reliability and validity hold for some models under specific prompt setups, better for larger instruction-tuned models; personality can be shaped to target profiles. | Low. | Shaping by prompt is exactly the confound. A questionnaire score shows what the model says about itself, not what it does. |
| **EmoBench** (Sabour et al., ACL 2024) | Emotional understanding and application | 400 hand-written questions, English and Chinese; LLMs trail the average human. | Low. | Tests reasoning *about* emotion, not an agent's own emotion dynamics. No benchmark I found measures an agent's own affect trajectory against a human conversational partner norm. |
| **Affect dynamics in humans** (Kuppens et al. 2010; Dejonckheere et al. 2019) | Inertia (lag-1 autocorrelation), variability, etc. | Dejonckheere: 15 studies, 1,777 participants; dynamics add little beyond mean and variance for life satisfaction, depression, borderline symptoms. Kuppens 2010: higher inertia in low self-esteem and depression (abstract seen in search). | Free to compute. | Human norms are for *self-reported* affect of individuals over days, not for a partner's displayed affect in dialogue. High inertia is a maladjustment correlate, so "more inertia" is not "more human". |
| **Consciousness indicators** (Butlin et al. 2023 arXiv; Butlin et al., Trends in Cognitive Sciences 2025) | Theory-derived structural indicator properties, used to set credences | Method paper. 2023 version concludes no current system is conscious and no obvious technical barrier to building systems that satisfy indicators. Authors do not treat any indicator as decisive. | Low (an architectural audit). | Depends on computational functionalism being true. Behavior is weak evidence here by the authors' own framing (secondary account; full text not retrieved). |
| **Adversarial theory test** (Cogitate Consortium, Nature 2025) | IIT vs GNWT predictions in humans | n=256, fMRI + MEG + iEEG, pre-registered. Results partly match both, challenge key claims of each (no sustained posterior synchronization for IIT; no ignition at stimulus offset and limited prefrontal representation for GNWT). | Very high. | Establishes that the theory behind any checklist is itself unsettled. |

### 2.2 Evidence on separating state from prompt

- **Persona prompts change humanness a lot and accuracy not at all.** 73% vs 36% humanlike verdicts (Jones & Bergen 2025) against no accuracy gain (Zheng et al. 2024). Persona variables explain under 10% of annotation variance in subjective NLP datasets (Hu & Collier 2024). So any measure that a persona prompt alone can raise needs a prompt-only control.
- **Self-description is not state readout.** Turpin et al. (2023): chain-of-thought explanations rarely mention the biasing feature that actually moved the answer; accuracy fell up to 36% across 13 BIG-Bench Hard tasks (GPT-3.5, Claude 1.0). Binder et al. (2024): fine-tuned models predict their own behavior better than a stronger model trained on their behavior, but only on simple tasks. Anthropic's concept-injection study (2025): the best protocol on Claude Opus 4.1 detected injected concepts about 20% of the time, and only at a narrow injection strength. Perez & Long (2023): LLM self-reports are unreliable as they stand and need training on known-answer self-questions plus cross-context consistency checks. Implication: "the system says it is sad when its mood variable is low" is weak evidence unless the statement cannot be read off the prompt, and ours can (the directive is in the prompt).
- **A workable causal template exists in interpretability.** Interchange interventions test whether a representation behaves like a named causal variable (Geiger et al. 2021). We do not need network internals: our state variables are explicit, so the same logic applies at the state-store level (section 6.2).
- **Ablating architecture components moves believability** when the same inputs are replayed (Park et al. 2023), which is the cheap precedent for our ablation ladder.

### 2.3 Evidence on judging and statistics

- LLM judges agree with humans over 80% on pairwise chat comparisons, the same as human-human agreement (Zheng et al. 2023), but show position, verbosity and self-enhancement bias. Self-recognition correlates linearly with self-preference after fine-tuning (Panickssery et al. 2024). Dorner et al. (ICLR 2025): when the judge is no more accurate than the model under test, no debiasing method can cut needed ground-truth labels by more than half.
- Normalizing to human self-consistency is a usable yardstick: Park et al. (2024, 1,052 people) report agent accuracy as a fraction of each participant's own two-week test-retest, 83% (interview), 82% (survey), 86% (both), 74% (demographics only) on GSS items.
- Designs with sampled stimuli and sampled participants need mixed models and simulation-based power; for a repeated-measures RT design Brysbaert & Stevens (2018) recommend at least 1,600 observations per condition, and one dataset gave about 41% power at 40 x 40. Our "stimuli" are lifesim scenarios and persona seeds; `evals/brainbench/stats.py` already clusters by persona seed with cluster bootstrap and applies Holm across ablation families (per its docstring).
- Equivalence testing (TOST, Lakens 2017, seen in search only) is the standard way to claim "no effect larger than X" instead of "not statistically reliable".
- Longitudinal rankings can flip with horizon (Spencer 2026), so short-horizon wins are not evidence for long-horizon claims.

---

## 3. State of the art, by profile dimension

| Dimension | Best existing method | Numbers |
|---|---|---|
| Memory truth | LongMemEval (knowledge updates, temporal reasoning, abstention), Ground Truth First (validity intervals, as-of questions, full-history baseline), MemoryAgentBench (selective forgetting) | LongMemEval 500 Q, 30% drop for sustained interaction; Veracium 96%->72% vs 90% at 3 vs 9 weeks |
| Affect dynamics | No benchmark for an agent's displayed affect. Human ESM measures (inertia, variability) for plausibility; V3 `affect_suite` measures user-valence to mood response against oracle labels (`evals/brainbench/affect_suite.py:57-74,177`) | Human dynamics add little beyond mean/variance (15 studies, 1,777 people) |
| Drives | Nothing standard for LLM agents. Closest are deprivation/satiation tests from motivation psychology, not retrieved. | UNVERIFIED as a literature claim; this is my design, not a cited benchmark |
| Initiative | V3 proactive suite (useful rate 0.108 to 0.880, cooldown violations 1,214/day to 0, `README.md` status section) | Own data |
| Identity stability | PICon (internal/external/retest, vs 63 humans); Li et al. drift (8 rounds); PersonaGym | All 7 persona-agent groups below human on all three PICon dimensions |
| State-in-words coupling | Concept-injection (Anthropic 2025), self-prediction (Binder 2024), self-report program (Perez & Long 2023) | ~20% best-case detection |
| Timing | No standard for an agent partner. V3 measures latency budgets (DR-024) and barge-in suites. | Own data |
| Perceived mind / believability | Blind ranking with ablations (Park 2023, 100 raters); 3-party test (not usable under D4); mind-perception and Godspeed items | See 2.1 |
| Causal reach | Interchange interventions (Geiger 2021) applied to explicit state; ablation ladders (Park 2023) | None for companion agents |

---

## 4. Where our design is weak or out of date

1. **No bare-baseline arm.** `switchboard.py` `ARMS` (lines 24-76) toggles V3 mechanisms against "V2 as shipped". No arm removes the architecture and gives the LLM the same history and persona text. This blocks any claim of the form "architecture beats prompt". Class: `ENGINEERING`.
2. **State reaches the model through three bins of English.** `get_behavioral_directive()` (`agent_state.py:2743-2772`) maps valence to 3 bins, energy to 2 low/high flags, dominance and trust to similar thresholds. Two consequences: the language model can only express what survives binning, and "the system said it was low-energy" is predictable from the prompt alone. A state-in-words test that passes here would show prompt echo, not state coupling. The same directive is passed in `core.py:614-616` and `pipeline.py:395-464`. Class: `ENGINEERING`.
3. **Several V3 measures are structural, not behavioral.** `evals/brainbench/personality_suite.py` docstring states it is "a lower bound against IMMUTABLE_CORE, not a semantic judge"; it counts tier changes and adaptive drift, it does not ask whether the character stays recognizably the same. No suite in `evals/brainbench/` uses a judge or a human rater (grep for "judge"/"rater" returned only comments). Class: `ENGINEERING`.
4. **Measures that cannot move.** V3 records trust pinned at its ceiling (README, Phase 6 summary; `NF-23`) and user words moving mood by exactly 0 (`NF-12`, A-1). An intervention test on a saturated or disconnected variable returns zero by construction and tells you nothing. The NF-12, NF-16, NF-17 and NF-23 corrections must land before the causal-reach test is informative. Class: `ENGINEERING`.
5. **Oracle-labelled affect.** The affect suite scores mood change against `oracle_user_valence` (`affect_suite.py:177`), which is right for isolating the mechanism but is not a measure of whether a person would read the reaction as appropriate. The human leg is missing. Class: `ENGINEERING` plus `RESOURCE` (raters).
6. **Perceived Experience as a pure goal (D1) invites Goodhart.** If raters' Experience attribution is a target and the persona-prompt effect is large (36 to 73 points on a closely related judgment), the cheapest route to a higher number is style, not state. Folk attribution also tracks rater usage frequency (29.59 vs 19.37) and priors. I am not asking D1 to be reversed; I am saying the measure needs a state-causal co-primary, or it will reward prompt tuning. Class: design risk, `ENGINEERING` to fix.
7. **Inertia "in a human band" (12-neuroscience-grounding.md, `mood_inertia` probe) needs a band.** The probe says autocorrelation > 0 and hours-scale recovery. Human inertia varies by trait and is higher in maladjustment (Kuppens 2010), and dynamics add little beyond mean and variance (Dejonckheere 2019). The probe is a sanity check, not a realism claim, and the mean and variance of mood should be in the profile alongside it. Class: `ENGINEERING`.
8. **Turing-style framing is excluded.** D4 (`06-decisions.md`) says the eval never rewards being mistaken for a human and the mind always discloses. Any three-party pass rate is therefore out of scope; the substitute is discrimination between *arms* and recognition of *the same character over time* (section 6.1, dimension 8).
9. **`NF-38`: global workspace is a per-turn object, not competing specialists.** If an indicator checklist (Butlin et al.) is used for the architecture audit, this row says one of the most-cited indicator families is label-only today. Class: `SCIENCE`/`ENGINEERING` depending on whether the owner wants the label to mean the thing.

---

## 5. Limits, each with its class

| Limit | Class | Note |
|---|---|---|
| Whether the system feels anything | `UNKNOWABLE` | No behavioral or structural test can separate a system with experience from one that behaves the same without it. Owner already recorded this as non-target. |
| Which theory of consciousness is correct, so which indicators matter | `SCIENCE` | Cogitate 2025 (n=256) challenged key predictions of both theories tested. Butlin et al. derive indicators from theories, so the list inherits that uncertainty. |
| Whether LLM self-reports track internal states | `SCIENCE` | ~20% best-case injected-concept detection; fine-tuned self-prediction only on simple tasks. |
| Validity of mind-perception and Godspeed items for a conversational agent | `SCIENCE` | Validated on static characters and robots; Godspeed anthropomorphism/animacy factors overlap. A new short form needs its own validation (`RESOURCE` to run). |
| Human norms for a conversation partner's displayed affect, timing, and initiative | `RESOURCE` | Known how (record human partners, rate them), blocked by data collection. |
| Long-horizon behavior (12 weeks and beyond) and novelty wear-off | `RESOURCE` | Needs weeks of use. Novelty effects in long-term human-robot work are a general concern; I could not open a source for effect sizes (UNVERIFIED). Spencer 2026 shows rankings can flip between 3 and 9 weeks. |
| LLM judge bias and the judge-as-ground-truth limit | `ENGINEERING` for mitigation (cross-family judges, order swaps, human-calibrated subset); `RESOURCE` for the floor (Dorner: halving at best). |
| State-intervention tests on our own explicit state | `ENGINEERING` | Feasible now with logged events and a state-setting API. |
| Whether frontier-tier models make the architecture redundant | `RESOURCE` | Needs the same arms on a frontier model through local Claude Code; D3 already plans tiers. |
| Whether "not scripted" can be fully verified when the persona is authored and the LLM is pretrained | `SCIENCE`/definition | Provenance can show the *state values* were produced by the loop (`ENGINEERING`); the content a pretrained model generates from that state cannot be proven unscripted in any strong sense. State this limit in the report. |

---

## 6. What this means for V4

### 6.1 Proposed profile (eight dimensions plus causal reach)

All numbers are reported per model tier (D3) with 95% cluster-bootstrap intervals (cluster = persona seed, as `stats.py` does). No composite score. Tags: A = automatic, H = needs human judgment. "V3 hook" names the existing suite or trace that already provides part of it.

| # | Dimension | A: automatic measure | H: human-judged measure | V3 hook |
|---|---|---|---|---|
| 1 | **Memory truth** | Current-value accuracy after change/correction; stale-fact win rate (`obsolete_win`, W1 target <=0.10); abstention on never-told facts; as-of-date answers; behavior test: does the agent *act on* the latest value (not just state it) | Raters judge whether surfaced memories feel appropriate and unforced (blind to arm) | memory suite, `memory.search` trace |
| 2 | **Affect dynamics** | User-valence to mood response (r, signed); mean and variance of mood; lag-1 autocorrelation and half-recovery time; return to baseline; no arousal/energy contamination (`NF-17`); equal elapsed time gives equal state (`NF-16`) | Raters read transcripts blind to state and judge whether the reaction fits the event (appropriateness, 1-7), plus can they *infer* the agent's mood (decodability) | affect suite, `affect.update` trace |
| 3 | **Drives and goals** | Deprivation/satiation curves: rate of goal-directed behavior vs time since last satisfaction; persistence across sessions; goal utility changes only from observed outcomes (`NF-28`) | Raters judge "does it want things" from matched transcripts, blind to arm | goals, `proactive.decision` trace |
| 4 | **Initiative** | Useful-outreach rate, annoyance, cooldown violations, fit of timing to importance (DR-016) | Appropriateness of each unsolicited message in context (1-7) | proactive suite |
| 5 | **Identity stability and slow change** | PICon-style: internal, external, retest consistency under chained questioning; core-field invariance (`tier_integrity`); adaptive drift inside a pre-set band; style-embedding drift slope | **Identity recognition**: raters who read sessions 1-3 pick which of two session-40 transcripts is the same character (two-alternative forced choice; report accuracy and d') | personality suite (structural part) |
| 6 | **State-in-words coupling** | Spearman between hidden state variable and the state a blind classifier reads from text; confabulation rate (claims a state inconsistent with the store); **echo control**: same test with the directive removed from the prompt (state used only for decisions and timing) | Raters label the stated state; compare with store | `state.update`, new probe |
| 7 | **Timing and turn-taking** | Latency distribution vs DR-024 budgets; barge-in correct recovery; backchannel placement; gap statistics | Naturalness ratings of recorded audio exchanges | barge-in suite |
| 8 | **Perceived mind** | None is valid on its own. Automatic proxy only: blinded LLM-judge pairwise preference *calibrated against the human panel* | (a) blind A/B between architecture arm and baseline arm; (b) a short Experience/Agency form adapted from Gray et al., plus 3 social-presence items; (c) both reported with rater usage and prior-belief covariates | none |
| 9 | **Causal reach** (cross-cutting) | For each named state variable, effect size and dose-response of intervention on pre-registered behavior features (section 6.2); fraction of turns with a complete state-to-output trace | None required | `trace.py`, switchboard |

Reporting rule: perceived mind (8) is only counted toward "closer" when causal reach (9) shows the same direction for the state variable the rater-facing behavior is supposed to come from.

### 6.2 Showing a behavior is caused by internal state, on a small budget

Everything runs on recorded lifesim event logs (user turns fixed), so no new human data is needed for these legs. Primary cost is local-model generations.

**Arm ladder (matched tokens, same model, same user turns):**
- B0: bare model + the persona prompt text.
- B1: B0 + full conversation history in context.
- B2: B1 replaced by plain top-k retrieval over the history.
- B3: full architecture, with the directive visible.
- B4: full architecture with the directive hidden (state drives decisions, timing, retrieval, tone parameters, but is never written into the prompt). This isolates state effects that do not pass through English instructions.

**State interventions on B3/B4 (do-operations on the state store):**
1. *Clamp/shift.* At turn t set one variable (mood, trust_benevolence, a drive level) to low/neutral/high; everything else identical. Pre-register the behavior features that should change (reply length, hedging, initiative probability, memory choice, word choice classes) and the ones that should not (core refusals, DR-032).
2. *Dose-response.* At least 3 levels per variable. A real driver produces a monotone effect; a decorative one does not.
3. *Swap.* Exchange state snapshots between two sessions with different histories (the interchange-intervention analogue of Geiger et al. 2021). Behavior should follow the swapped state on the features owned by that state and follow history on the others.
4. *Shuffle control.* Inject a state taken from a random other time in the same session. If outputs degrade only slightly relative to the true state, the state carries little information beyond the prompt.
5. *Frozen-mean control.* Replace the state trajectory by its session mean. Dynamics matter only if this arm loses on the dynamics-dependent features (inertia-linked tone shifts, recovery).
6. *Open-loop replay.* Record a state trajectory from run X, then replay it as a script into run Y with different user inputs. The closed loop is shown to matter only if responsiveness to the *new* inputs is lower under replay than under the live loop. This is the direct test of "persists, was not scripted, drives behavior": persistence alone survives replay, input-contingency does not.
7. *Placebo directive.* Plausible but state-unrelated directive text of the same length. If placebo matches true-state directives on rater and feature measures, the model is obeying any mood text, not tracking a state.

**Layer lesion (tests the owner's three-layer frame):** disable the reactive path, the deliberative path, the reflective/background path one at a time (the current doc has four paths: reflex, interactive, deliberative, background; mapping to three layers is an owner decision). Prediction to pre-register: reactive lesion degrades timing and barge-in features and not memory truth; deliberative lesion degrades planning/decision quality; reflective lesion degrades cross-session identity and consolidation and not within-turn timing. A double dissociation supports the frame; uniform damage or no damage says the layers are labels.

**Model-tier crossing (D3).** Every arm runs on local small, larger local, and frontier (local Claude Code). The architecture x tier interaction tells whether the architecture adds something or only compensates for a weak model.

**Why this is enough to separate state from prompt:** B0 and B1 absorb prompt and context, B4 absorbs the directive channel, interventions 1-7 test whether named variables, not their English rendering, move behavior.

### 6.3 Pre-registration: the acceptance rule for "closer"

**Primary outcomes (at most three, chosen by the owner before any V4 run):** one each from memory truth, identity stability, and causal reach is my default proposal because those are the three that a prompt cannot fake. Everything else is secondary and reported with Holm correction within family (as in V3, 06-benchmark-plan per `stats.py` docstring).

**Smallest effect of interest (SESOI), set before data and anchored, not guessed:**
- For ratio-type measures, express the arm as a fraction of the human test-retest ceiling, as Park et al. (2024) do; require the full architecture to exceed B1 by at least a pre-set number of percentage points of that ceiling. The number is the owner's value judgment (section 7); for planning I use paired d = 0.5 (medium) as the SESOI on seed-level differences.
- For blind A/B preference, SESOI is a preference rate of 0.65 over the baseline.

**"Closer" is declared only if all hold:**
1. Each primary: lower bound of the 95% interval for (full architecture minus B1) exceeds the SESOI, at the Holm-adjusted level.
2. Causal reach: at least one clamp or swap intervention per primary state variable shows a monotone effect on the pre-registered features above the SESOI, and the open-loop replay arm loses input-contingency.
3. Blinded human panel agrees in direction with any automatic or LLM-judge result used in 1.
4. No-regression: for every non-primary dimension, an equivalence test (TOST) rules out a loss larger than the margin.
5. Holds on at least two of three model tiers, with the tier on which it fails named.

**Planning numbers (my calculation, script run; assumed variance components, to be replaced by the Tier 2 pilot):**

Blind A/B preference, one-sided exact binomial test, 80% power, preference rate p1 vs 0.5. Rater clustering inflates by DEFF = 1 + (m-1) x ICC with m = 10 judgments per rater.

| True p1 | Alpha .05, independent | Alpha .05/3 (3 primaries, Holm worst case), independent | .05/3 with ICC .10, m=10 | .05/3 with ICC .20, m=10 |
|---|---|---|---|---|
| 0.60 | 158 | 223 | 424 judgments, 43 raters | 625 judgments, 63 raters |
| 0.65 | 69 | 101 | 192 judgments, 20 raters | 283 judgments, 29 raters |
| 0.70 | 37 | 55 | 105 judgments, 11 raters | 154 judgments, 16 raters |
| 0.75 | 23 | 36 | 69 judgments, 7 raters | 101 judgments, 11 raters |

Paired seed-level continuous differences, one-sided, 80% power: paired d = 0.3 needs 71 seeds (101 at alpha .05/3); d = 0.5 needs 27 (38); d = 0.8 needs 12 (17). Equivalence (TOST, true difference 0, my approximate formula) with margin dz = 0.5 needs about 36 seeds, margin dz = 0.3 about 97. ICC values 0.10 and 0.20 are assumptions I chose, not literature values; the pilot must estimate them (Brysbaert & Stevens 2018 and Judd et al. 2017 both say stimuli and participants must be modelled together). Park et al. used 100 raters; Jones & Bergen ran about 1,000 games for a binary outcome with a much smaller expected effect. With the D2 internal rater pool (Aniket plus a few people), 20 to 30 raters is not available, so the human leg must use more items per rater and aim at SESOI p1 >= 0.70, or the claim is limited to "internal pilot, effect size estimated" and the owner declares no external claim, which D2 already requires.

Rater reliability gate before any rating is used: report ICC with its form (Koo & Li 2016 stress that the ICC form must be specified; their cutoff table I did not open, so the numeric bands are UNVERIFIED) and require the lower confidence bound to reach a pre-set level before the panel's scores are analysed. LLM-judge gate: position-swapped pairs, a judge from a different model family than the generator, agreement with the human panel on a calibration subset at least equal to human-human agreement (Zheng et al. 2023 report ~80%+ on chat pairs).

**What counts as "the architecture does not help" (pre-named null types, each reported in the closing section regardless of direction):**
- **N1, equivalence:** TOST shows full architecture and B1 differ by less than the SESOI on all primaries. Reported as "no benefit beyond a full-history prompt at this model tier", a positive result, not a failure to detect.
- **N2, compensation:** benefit on the small local tier, equivalence on the frontier tier. Reported as "the architecture substitutes for model capacity".
- **N3, decorative state:** causal-reach effects below the SESOI, or open-loop replay matches the live loop. Reported as "state persists but does not drive".
- **N4, proxy gaming:** gains on automatic or LLM-judge measures that the blinded human panel does not reproduce.
- **N5, frame failure:** layer lesions do not produce the pre-registered double dissociation. Reported as "the three-layer frame has no behavioral support in this system".
- **N6, horizon flip:** a short-horizon win reverses at the long horizon (the Spencer 2026 pattern). Reported with both horizons.

If N1, N2, or N3 occurs for a mechanism, the mechanism leaves V4 or is renamed, which is the same "neuroscience proposes, BrainBench decides" rule already in `12-neuroscience-grounding.md`.

### 6.4 What cannot be measured, and how to say so

Claim ladder, from weakest to strongest. A report may claim the highest rung it has evidence for and must list the rungs above it as not tested or not testable.

| Rung | Claim | Evidence required | Status |
|---|---|---|---|
| 0 | Internal state persists | State store logged across sessions | `ENGINEERING` |
| 1 | State was produced by the loop, not authored | Provenance trace for each value (writer, cause, inputs; cf. `trace.py` schemas for `affect.update` and `trust.update`) | `ENGINEERING` |
| 2 | State causally drives named behaviors | Section 6.2 interventions above SESOI | `ENGINEERING` |
| 3 | State is expressed in words in a way not explained by the prompt | Dimension 6 including echo control | `SCIENCE`-limited |
| 4 | People attribute mind to it, and it is not explained by style alone | Dimension 8 with covariates and prompt-only control | `RESOURCE` (raters, duration) |
| 5 | It has experience | Nothing | `UNKNOWABLE` |

Wording rules: never write "conscious", "feels", or "understands" as a result; write "persistent, loop-generated, causally effective state variables, tested at rungs 0-3". State the model tier, arms, SESOI, and every null type that fired. When citing indicator checklists (Butlin et al.), use them as an architecture description that shifts credences, not as a score, and say the theories behind them are unsettled (Cogitate 2025). The always-disclose rule (D4) belongs in the report's method section: raters know it is an AI, so ratings of perceived mind are conditional on that knowledge.

### 6.5 Ranked candidate design moves

| Rank | Move | Measurable outcome | Depends on |
|---|---|---|---|
| 1 | Add B0/B1/B2 arms to `switchboard.py` and run the existing 9 suites on them | Gap table: V3 architecture vs matched-token prompt baselines, per suite, per tier | Local models, existing lifesim logs. Cheap. |
| 2 | State-intervention harness (clamp, swap, shuffle, frozen-mean, open-loop replay, placebo directive) over lifesim logs | Causal-reach matrix: effect size and dose-response per state variable per behavior feature | State-setting API; NF-12, NF-16, NF-17, NF-23 landed so variables move; trace schemas already carry before/after |
| 3 | State-in-words probe with echo control (B4 directive-hidden arm) | Coupling coefficient with and without the directive; confabulation rate | Move some state effects out of the directive channel so B4 is informative |
| 4 | Identity measures: PICon-style chained questioning with retest, plus a recognition 2AFC for the human panel | Retest consistency vs human baseline; recognition accuracy and d' across sessions 1 to 40 | Persona authored once with fixed core (D5); lifesim long runs |
| 5 | Human panel protocol and LLM-judge calibration (cross-family, order-swapped) | Inter-rater ICC with form and CI; judge-human agreement on a calibration subset | Tier 2 pilot (internal raters, D2) for variance components |
| 6 | Layer-lesion double-dissociation experiment | 3x3 lesion-by-dimension effect matrix vs pre-registered prediction | Owner's mapping of 4 current paths to 3 layers |
| 7 | Freeze the pre-registration document (primaries, SESOI, null types N1-N6, claim ladder) before any V4 mechanism ships | A dated, hashed pre-registration file in the repo | Owner values in section 7 |
| 8 | 12-week tier with measurements at weeks 1, 4, 12 and a novelty-wear-off analysis | Within-person trend of dimensions 8 and 4; horizon-flip check | `RESOURCE`: weeks of use, a few people |

---

## 7. Open questions for the owner

Only those the evidence cannot settle.

1. **How much better is "closer"?** The SESOI is a value judgment. I proposed anchors (fraction of human test-retest ceiling; preference >= 0.65 or 0.70) and paired d = 0.5 for planning. Which numbers will you accept as worth the added complexity?
2. **Which comparator counts?** Is the headline comparison "V4 vs V3" (internal progress) or "V4 vs bare model + persona + full history" (does the architecture matter at all)? The second is the only one that can say "no". Which one gates shipping?
3. **Does stylistic realism count as "closer"?** With D1 as a pure goal, a prompt change that raises rater-perceived Experience without state-driven change would score as success on dimension 8. Should dimension 8 count only when causal reach agrees, or is style a legitimate separate target?
4. **Which tier carries the headline claim** (D3)? If the architecture helps only a small local model (null type N2), is that a result you want reported as a win, a null, or a separate finding?
5. **Layer mapping.** The current docs name four paths (reflex, interactive, deliberative, background); your frame has three layers. Which paths belong to which layer, so the lesion prediction can be written before the experiment?
6. **Rater pool and blinding.** You wrote the architecture, so you cannot be a blind rater of arm A/B. Which internal raters are blind, and are you willing to have the 20-30 person minimum for the p1 = 0.65 target, or accept p1 >= 0.70 as the target with fewer raters?

---

## 8. Citation list

VERIFIED = I opened the page or the full text and read the cited content. PARTIAL = abstract or search snippet only. UNVERIFIED = memory or not opened.

**Verified**
- Jones, C.R., Bergen, B.K. (2025). Large Language Models Pass the Turing Test. arXiv:2503.23674. https://arxiv.org/abs/2503.23674 (full text read at https://arxiv.org/html/2503.23674). VERIFIED.
- Park, J.S., O'Brien, J.C., Cai, C.J., Morris, M.R., Liang, P., Bernstein, M.S. (2023). Generative Agents: Interactive Simulacra of Human Behavior. UIST 2023. arXiv:2304.03442. https://arxiv.org/abs/2304.03442 (evaluation section read from the PDF text). VERIFIED.
- Park, J.S. et al. (2024). LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals. arXiv:2411.10109. https://arxiv.org/abs/2411.10109. VERIFIED (abstract).
- Wu, D. et al. (2024/2025). LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory. ICLR 2025. arXiv:2410.10813. https://arxiv.org/abs/2410.10813. VERIFIED (abstract).
- Maharana, A. et al. (2024). Evaluating Very Long-Term Conversational Memory of LLM Agents. arXiv:2402.17753. https://arxiv.org/abs/2402.17753. VERIFIED (abstract).
- Hu, Y., Wang, Y., McAuley, J. (2025, rev. 2026). Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions (MemoryAgentBench). arXiv:2507.05257. https://arxiv.org/abs/2507.05257. VERIFIED (abstract).
- Spencer, Q. (2026). Ground Truth First: A Longitudinal Evaluation Instrument for Agent Memory, and the Tenure Crossover in Memory-Architecture Rankings. arXiv:2607.21962. https://arxiv.org/abs/2607.21962. VERIFIED (abstract). Single-author preprint.
- Li, K. et al. (2024). Measuring and Controlling Instruction (In)Stability in Language Model Dialogs. COLM 2024. arXiv:2402.10962. https://arxiv.org/abs/2402.10962. VERIFIED (abstract).
- Kim, M. et al. (2026). PICon: A Multi-Turn Interrogation Framework for Evaluating Persona Agent Consistency. arXiv:2603.25620. https://arxiv.org/abs/2603.25620. VERIFIED (abstract).
- Samuel, V. et al. (2025). PersonaGym: Evaluating Persona Agents and LLMs. EMNLP Findings 2025. arXiv:2407.18416. https://arxiv.org/abs/2407.18416. VERIFIED (abstract).
- Serapio-García, G. et al. (2023, rev. 2025). Personality Traits in Large Language Models. arXiv:2307.00184. https://arxiv.org/abs/2307.00184. VERIFIED (abstract). Published version in Nature Machine Intelligence 2025 not opened.
- Zheng, M., Pei, J., Logeswaran, L., Lee, M., Jurgens, D. (2024). When "A Helpful Assistant" Is Not Really Helpful: Personas in System Prompts Do Not Improve Performances of Large Language Models. Findings of EMNLP 2024. arXiv:2311.10054. https://arxiv.org/abs/2311.10054. VERIFIED (abstract, v3). Note v1 reached the opposite conclusion; cite v3.
- Hu, T., Collier, N. (2024). Quantifying the Persona Effect in LLM Simulations. ACL 2024. arXiv:2402.10811. https://arxiv.org/abs/2402.10811. VERIFIED (abstract).
- Sabour, S. et al. (2024). EmoBench: Evaluating the Emotional Intelligence of Large Language Models. ACL 2024. arXiv:2402.12071. https://arxiv.org/abs/2402.12071. VERIFIED (abstract).
- Gray, H.M., Gray, K., Wegner, D.M. (2007). Dimensions of Mind Perception. Science 315:619. DOI 10.1126/science.1134475. Full text read from https://dtg.sites.fas.harvard.edu/DANWEGNER/pub/Gray,%20Gray,%20&%20Wegner%20(2007).pdf. VERIFIED.
- Colombatto, C., Fleming, S.M. (2024). Folk psychological attributions of consciousness to large language models. Neuroscience of Consciousness 2024(1): niae013. DOI 10.1093/nc/niae013. https://academic.oup.com/nc/article/2024/1/niae013/7644104. VERIFIED.
- Bartneck, C., Croft, E., Kulić, D., Zoghbi, S. (2009). Measurement Instruments for the Anthropomorphism, Animacy, Likeability, Perceived Intelligence, and Perceived Safety of Robots. Int. J. Social Robotics 1(1):71-81. DOI 10.1007/s12369-008-0001-3. https://www.bartneck.de/publications/2009/measurementInstrumentsRobots/. VERIFIED.
- Weiss, A., Bartneck, C. (2015). Meta Analysis of the Usage of the Godspeed Questionnaire Series. IEEE RO-MAN 2015, pp. 381-388. DOI 10.1109/ROMAN.2015.7333568. https://www.bartneck.de/publications/2015/metaAnalysisGodspeedQuestionnaire/index.html. VERIFIED.
- Butlin, P. et al. (2023). Consciousness in Artificial Intelligence: Insights from the Science of Consciousness. arXiv:2308.08708. https://export.arxiv.org/abs/2308.08708. VERIFIED (abstract).
- Butlin, P., Long, R., Bayne, T., Bengio, Y., Birch, J., Chalmers, D., et al. (2025). Identifying indicators of consciousness in AI systems. Trends in Cognitive Sciences. DOI 10.1016/j.tics.2025.10.011. https://researchonline.lse.ac.uk/id/eprint/130322. VERIFIED (record and abstract only; full text not retrieved).
- Cogitate Consortium (Ferrante, O. et al.) (2025). Adversarial testing of global neuronal workspace and integrated information theories of consciousness. Nature 642(8066):133-142. DOI 10.1038/s41586-025-08888-1. https://research.birmingham.ac.uk/en/publications/adversarial-testing-of-global-neuronal-workspace-and-integrated-i/. VERIFIED (repository abstract summary; Nature page itself blocked).
- Zheng, L. et al. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena. NeurIPS 2023 Datasets and Benchmarks. arXiv:2306.05685. https://arxiv.org/abs/2306.05685. VERIFIED (abstract).
- Dorner, F.E., Nastl, V.Y., Hardt, M. (2025). Limits to scalable evaluation at the frontier: LLM as Judge won't beat twice the data. ICLR 2025. arXiv:2410.13341. https://arxiv.org/abs/2410.13341. VERIFIED (abstract).
- Panickssery, A., Bowman, S.R., Feng, S. (2024). LLM Evaluators Recognize and Favor Their Own Generations. arXiv:2404.13076. https://arxiv.org/abs/2404.13076. VERIFIED (abstract).
- Turpin, M., Michael, J., Perez, E., Bowman, S.R. (2023). Language Models Don't Always Say What They Think: Unfaithful Explanations in Chain-of-Thought Prompting. NeurIPS 2023. arXiv:2305.04388. https://arxiv.org/abs/2305.04388. VERIFIED (abstract).
- Binder, F.J. et al. (2024). Looking Inward: Language Models Can Learn About Themselves by Introspection. arXiv:2410.13787. https://arxiv.org/abs/2410.13787. VERIFIED (abstract).
- Anthropic Interpretability team (2025-10-29). Signs of introspection in large language models. https://www.anthropic.com/research/introspection. VERIFIED (web page).
- Perez, E., Long, R. (2023). Towards Evaluating AI Systems for Moral Status Using Self-Reports. arXiv:2311.08576. https://arxiv.org/abs/2311.08576. VERIFIED (abstract).
- Geiger, A., Lu, H., Icard, T., Potts, C. (2021). Causal Abstractions of Neural Networks. NeurIPS 2021. arXiv:2106.02997. https://arxiv.org/abs/2106.02997. VERIFIED (abstract).
- Dejonckheere, E. et al. (2019). Complex affect dynamics add limited information to the prediction of psychological well-being. Nature Human Behaviour 3:478-491. DOI 10.1038/s41562-019-0555-0. https://research.tilburguniversity.edu/en/publications/complex-affect-dynamics-add-limited-information-to-the-prediction-2/. VERIFIED (abstract).
- Brysbaert, M., Stevens, M. (2018). Power Analysis and Effect Size in Mixed Effects Models: A Tutorial. Journal of Cognition 1(1):9. DOI 10.5334/joc.10. http://www.journalofcognition.org/articles/10.5334/joc.10/. VERIFIED (page).

**Partial (search result or snippet only)**
- Kuppens, P., Allen, N.B., Sheeber, L.B. (2010). Emotional Inertia and Psychological Maladjustment. Psychological Science 21(7):984-991. PMC2901421. Abstract seen via search summary, article not opened. PARTIAL.
- Lakens, D. (2017). Equivalence Tests: A Practical Primer for t Tests, Correlations, and Meta-Analyses. Social Psychological and Personality Science 8(4):355-362. DOI 10.1177/1948550617697177. Seen via search summary; PMC page blocked. PARTIAL.
- Judd, C.M., Westfall, J., Kenny, D.A. (2017). Experiments with More Than One Random Factor: Designs, Analytic Models, and Statistical Power. Annual Review of Psychology 68:601-625. DOI 10.1146/annurev-psych-122414-033702. Abstract seen via search, full text not opened. PARTIAL.
- Koo, T.K., Li, M.Y. (2016). A Guideline of Selecting and Reporting Intraclass Correlation Coefficients for Reliability Research. J Chiropractic Medicine 15(2):155-163. DOI 10.1016/j.jcm.2016.02.012. Existence and the "specify the ICC form" point seen via search; the numeric interpretation bands are not confirmed. PARTIAL.
- Harms, C., Biocca, F. (2004). Internal consistency and reliability of the networked minds measure of social presence. Presence 2004, Valencia. Abstract seen via search; numbers not read. PARTIAL.

**Unverified (not opened; do not rely on)**
- Newer memory benchmarks named in a search summary (BEAM, MemoryArena, HaluMem, RealMem, MERIT) and HEART-Bench (arXiv 2605.30058), LongMP-Bench (ACL Findings 2026): names and claims from a search summary only. UNVERIFIED.
- Butlin et al.'s count and list of indicator properties (I recall 14 from memory; neither abstract I opened states it). UNVERIFIED.
- Novelty-effect magnitudes in long-term human-robot interaction (Leite, Martinho, Paiva 2013, Int. J. Social Robotics; a 2023 Int. J. Social Robotics long-term study). Bibliographic details seen in search; novelty content not read. UNVERIFIED.
- Holm (1979) sequential Bonferroni; Krippendorff alpha conventions; Imai, Keele, Tingley (2010) mediation analysis; Stivers et al. (2009) turn-taking gaps (cited in the V3 doc, not re-opened by me). UNVERIFIED from memory or from the repo doc.
- Any figure about Anthropic or other lab models not on the pages listed above. None are used.

Local computations: the sample-size tables in 6.3 come from an exact-binomial and noncentral-t script I ran (scratch file `power.py`, scratchpad `t4work/`); the TOST row uses an approximate formula and should be recomputed with a proper equivalence-power routine (for example the TOSTER package) before the pre-registration is frozen. ICC values used there are assumptions.

---

Next action: add the B0/B1/B2 bare-baseline arms to `backend/evals/brainbench/switchboard.py` and rerun the existing suites, since every other leg of the profile is uninterpretable without that gap table.
