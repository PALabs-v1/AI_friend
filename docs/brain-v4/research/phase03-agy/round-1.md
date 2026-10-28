VERDICT: FAIL - The draft fails on unordered candidate slices with circular dependencies, owner questions that re-ask resolved decisions, unoperationalized model seam metrics, and an incomplete state ledger.

## Findings Table

| ID | Severity | Section and Line | What is wrong | Evidence | Exact fix |
|---|---|---|---|---|---|
| F-01 | HIGH | Section 12, lines 229-243 | Candidate slices explicitly refuse to provide an ordered dependency plan, violating Rubric item 7. Line 231 states: "These are candidates for Phase 04, not an ordered plan", and line 243 states: "Phase 04 owns slice ordering." In addition, the candidate table contains a circular dependency: Slice 1 ("Freeze baseline harness") requires recording defect baselines under clamp, set-point clamp, and placebo directives (Section 9, lines 173-185), but the clamp, swap, and placebo operators are not created until Slice 4 ("Add state interventions"). Slice 1 cannot execute without Slice 4. | `docs/brain-v4/phases/02-architecture-draft.md:229-243`, `173-185`; Rubric item 7 | Re-order slices into a strict topological dependency sequence: (1) Define state ledger schema and intervention operators (clamp, swap, shuffle, replay); (2) Freeze baseline harness with bare model arm and defect baselines; (3) Enforce memory supersession; (4) Implement AI self-knowledge disclosure and character probe updates; (5) Add persona slow-state writer after lifesim sweep; (6) Complete model seam with ModelRole routing; (7) Test inner-life candidates against frozen ablation deadline. Remove the statement abdicating slice ordering to Phase 04. |
| F-02 | HIGH | Section 11, lines 221-227 | Violates Rubric item 8 ("The 'new questions for Aniket' are real owner decisions, not things evidence can settle, and no decision already made is re-asked"). Question 1 re-asks D-018, which was already resolved by Aniket on 2026-10-28 (`DECISION_LOG.md:153-164`). Question 2 re-asks partner media privacy, which D-008 already decided will be settled at integration after a partner is chosen (`DECISION_LOG.md:22`). Question 3 asks Aniket to choose between separate mind stores, a washout design, or a between-person design; this is a trial methodology question, not an owner decision, and draft line 219 explicitly categorizes this item as ENGINEERING. | `docs/brain-v4/DECISION_LOG.md:153-164`, `22`; `docs/brain-v4/phases/02-architecture-draft.md:219, 221-227` | Delete Questions 1, 2, and 3 from "New questions for Aniket". Rely on resolved decisions D-018 and D-008 as written. Move the long-horizon carryover trial design to the Phase 04 engineering specification. State clearly that no new owner decisions are required for Phase 02. |
| F-03 | HIGH | Section 7, lines 99-111 | Violates Rubric item 4 ("Every part (layer, memory change, inner-life, model seam, identity mechanism) names a measure that could fail, and a way to intervene on it. A measure that cannot return 'no' is a finding"). Section 7 describes six points for the Model Seam and State Contract but fails to define an operationalized behavioral measure that can return "no" or an intervention handle. Line 107 mentions "conformance probes per pinned model" without defining metrics, pass/fail thresholds, or failure interventions. In Section 12 (line 241), the outcome is given as "Calls use ModelRole; vectors carry model id/dimension; conformance results by tier", which are implementation deliverables, not falsifiable behavioral measures. | `docs/brain-v4/phases/02-architecture-draft.md:99-111, 241`; Rubric item 4 | Add an explicit measure and intervention handle to Section 7: define conformance failure (such as rate of schema validation errors, failure to respect structured state channels, or degradation in role execution fidelity exceeding 5 percent relative to unadapted baseline); define an intervention handle that ablates or swaps adapter state translation layers to verify that the seam rejects non-conforming model behavior rather than permitting silent degradation. |
| F-04 | MED | Section 6, lines 97-98; Section 9, lines 183-185 | Quietly reinterprets and dilutes D-028, violating Rubric item 1. D-028 explicitly ruled: "The dead plan verifier, simulator and calibration code is deleted unless V4 names a use for it." The draft confirms in Section 2 (Rows 17 and 18) that plan verifier, executor, and simulator have no live caller or reader in `pipeline.py`. Instead of scheduling their removal, Section 6 and Section 9 create an unapproved reprieve by proposing to trace invocation counts and test calibration directives across set, placebo, and no-directive arms. This reinterprets an owner mandate to delete dead code into an open-ended directive to test dead code. | `docs/brain-v4/DECISION_LOG.md:235-240`; `docs/brain-v4/phases/02-architecture-draft.md:97-98, 183-185` | State plainly that under D-028, plan verifier, executor, and simulator have no named use in V4 and are scheduled for removal in Phase 04 after checking V3 Invariant 12. For calibration, specify that unless an active pipeline hook is approved in Phase 04, the dead calibration code is deleted rather than benchmarked. |
| F-05 | MED | Section 7, line 108 | Silently skips an owner-mandated gate in D-034, violating Rubric item 1. D-034 established two explicit gates before training data can be collected: (1) provider terms review (R-14); and (2) "The state-trace logs wait until NF-12 and NF-23 are fixed, so the traces are not poisoned." Draft line 108 notes the provider terms gate but completely omits the gate requiring NF-12 (mood self-loop) and NF-23 (hostile text raising integrity) to be fixed before state-trace logs can be used for training. | `docs/brain-v4/DECISION_LOG.md:291-298`; `docs/brain-v4/phases/02-architecture-draft.md:108` | Explicitly include the second D-034 gate in Section 7, line 108: state that state-trace logging for model training is blocked until NF-12 (mood self-loop) and NF-23 (norm-alignment trust artifact) are repaired to prevent training trace poisoning. |
| F-06 | MED | Section 2, lines 23-45 | The state ledger omits multiple live state variables present in `backend/app/state/agent_state.py` and `backend/app/state/session_state.py`, violating Rubric item 3. Omissions include: (1) Adrenaline in the endocrine system (`agent_state.py:245-254`), while Row 3 only covers cortisol and dopamine; (2) `SessionState` and `WorkingMemoryStore` (`session_state.py:1-46`), which manage volatile sub-turn state, turn-taking conflict resolution, and `active_interruption`; (3) Multi-person models (`PersonModel` in `AgentState.persons`, `agent_state.py:642-648`); (4) Circadian tracking (`user_interaction_hours`, `agent_state.py:213`); (5) Proactive outreach cooldown timers (`last_proactive_attempt`, `agent_state.py:212`). | `backend/app/state/agent_state.py:212-254, 642-648`; `backend/app/state/session_state.py:1-46`; `docs/brain-v4/phases/02-architecture-draft.md:23-45` | Expand Section 2 to include: adrenaline in Row 3; a dedicated row for `SessionState` / `WorkingMemoryStore` covering volatile turn-taking and interruption states; multi-person relational tracking; and circadian / outreach cooldown timing variables. |
| F-07 | LOW | Section 4, line 75 | Cites a nonexistent file path: `research/phase01-comparison.md, section 5`. The actual repository file is `docs/brain-v4/05-phase01-comparison.md`. Violates Rubric item 2 and 5. | File `docs/brain-v4/research/phase01-comparison.md` does not exist on disk; target content is in `docs/brain-v4/05-phase01-comparison.md:section 5` | Update line 75 to cite `05-phase01-comparison.md, section 5`. |
| F-08 | LOW | Section 2, line 34 (Row 10) | Row 10 classifies "Monologue thought" as a state ledger row, but the evidence cited (`subconscious_agent.py:752-776` and `contracts.py:44`) shows monologue thoughts are published as ephemeral event messages on `Topics.STATE_SUBCONSCIOUS` without persistence in `AgentState`, SQLite, or any store. Treating an unconsumed event bus broadcast as an open-read state row confuses messaging with state. | `backend/app/agents/subconscious_agent.py:773-776`; `backend/app/contracts.py:44` | Clarify in Row 10 that monologue thought is currently an ephemeral pub/sub event without a persistent backing store, and state whether V4 will persist it as a typed ledger entry before initiation routing. |

## Citation Check Table

| Citation in Draft | File Opened | Lines Checked | Claimed in Draft | Result | Notes and Evidence |
|---|---|---|---|---|---|
| `pipeline.py:814-826` | `backend/app/cognitive/pipeline.py` | 814-826 | Emotional bias is fed prior mood; appraisal vector generated | RIGHT | Line 817 reads `state_snapshot.get("mood", 0.0)` into `emotional_bias`; line 822 passes it to `appraise`. |
| `pipeline.py:284-295` | `backend/app/cognitive/pipeline.py` | 284-295 | Reappraisal uses constant behavioral signal 0.5; pipeline reads weights | RIGHT | Line 287 passes `behavioral_signal=0.5`; line 294 reads `self.reappraisal.get_weights()`. |
| `pipeline.py:401-409` | `backend/app/cognitive/pipeline.py` | 401-409 | Action payload receives cortisol, dopamine, fatigue, mood | RIGHT | Lines 403-408 populate cortisol, dopamine, fatigue, and valence (`mood`) in `plan.payload`. |
| `agent_state.py:1803-1827` | `backend/app/state/agent_state.py` | 1803-1827 | Trust dimensions and attachment updated; integrity adds delta * NA | RIGHT | Lines 1804-1826 update benevolence, competence, integrity (`+ self.delta * NA`), and attachment. |
| `decision.py:267-275` | `backend/app/cognitive/decision.py` | 267-275 | Relational stance reads trust and attachment | RIGHT | Lines 271-274 pass `trust` and `attachment` into `_bucket_relational_stance`. |
| `decision.py:527-529` | `backend/app/cognitive/decision.py` | 527-529 | Decision reads trust | RIGHT | Line 529 reads `T = state.get("trust", 0.5)`. |
| `action.py:940-970` | `backend/app/cognitive/action.py` | 940-970 | Endocrine state maps to generation options | RIGHT | Lines 948-970 map cortisol to temperature, dopamine to top_p, and fatigue to num_predict. |
| `config.py:345-355` | `backend/app/config.py` | 345-355 | Reappraisal weight learning disabled by default | RIGHT | Line 355 sets `REAPPRAISAL_WEIGHT_LEARNING_ENABLED: bool = False`. |
| `subconscious_agent.py:693-708` | `backend/app/agents/subconscious_agent.py` | 693-708 | Fatigue gates dreaming | RIGHT | Line 695 reads fatigue; line 698 gates sleep dreaming on `fatigue > 0.8`. |
| `action.py:768-776` | `backend/app/cognitive/action.py` | 768-776 | Retrieval disables recall refresh | RIGHT | Line 772 sets `refresh_on_recall=False` in `search_memories`. |
| `surfacing_agent.py:262-270` | `backend/app/agents/surfacing_agent.py` | 262-270 | Surfacing retrieval disables recall refresh | RIGHT | Line 266 sets `refresh_on_recall=False` in `search_memories`. |
| `memory_store.py:1518-1527` | `backend/app/state/memory_store.py` | 1518-1527 | Contradiction search defined | RIGHT | `find_contradiction` searches opposing evidence on subject. |
| `memory_store.py:1660-1667` | `backend/app/state/memory_store.py` | 1660-1667 | Contradiction id assigned | RIGHT | Lines 1660-1666 find contradiction and assign `contradicts_id`. |
| `memory_store.py:1677-1695` | `backend/app/state/memory_store.py` | 1677-1695 | Temporal metadata written | RIGHT | Lines 1692-1694 store `valid_from`, `valid_until`, and `contradicts_id`. |
| `core.py:98-100` | `backend/app/cognitive/core.py` | 98-100 | TemporalMemoryStore instantiated | RIGHT | Line 98 constructs `self.temporal_memory_store = TemporalMemoryStore(...)`. |
| `core.py:187-190` | `backend/app/cognitive/core.py` | 187-190 | Temporal store injected into pipeline | RIGHT | Line 187 passes `temporal_memory_store` to `CognitivePipeline`. |
| `learning.py:366-376` | `backend/app/cognitive/learning.py` | 366-376 | Reflection submits proposals to review queue | RIGHT | Line 374 calls `self.review_queue.submit(...)` when review is required. |
| `config.py:275-278` | `backend/app/config.py` | 275-278 | Learning review required by default | RIGHT | Line 278 sets `LEARNING_REVIEW_REQUIRED: bool = True`. |
| `identity.py:735-775` | `backend/app/cognitive/identity.py` | 735-775 | Persona prompt reads adaptive traits | RIGHT | Line 744 reads `self.persona.adaptive_traits`. |
| `agent_state.py:282-286` | `backend/app/state/agent_state.py` | 282-286 | Baseline PAD setter exists | RIGHT | Lines 282-286 define `@personality_baseline_affect.setter`. |
| `agent_state.py:2319-2332` | `backend/app/state/agent_state.py` | 2319-2332 | ALMA affect decay uses baseline PAD | RIGHT | Lines 2320-2332 compute exponential decay toward `baseline_valence`, `baseline_arousal`, `baseline_dominance`. |
| `subconscious_agent.py:752-776` | `backend/app/agents/subconscious_agent.py` | 752-776 | Monologue published to state.subconscious | RIGHT | Lines 773-775 publish generated thought to `Topics.STATE_SUBCONSCIOUS`. |
| `contracts.py:44` | `backend/app/contracts.py` | 44 | Topic definition exists | RIGHT | Line 44 defines `STATE_SUBCONSCIOUS = "state.subconscious"`. |
| `subconscious_agent.py:827-840` | `backend/app/agents/subconscious_agent.py` | 827-840 | Dream insight logged as ephemeral quarantine | RIGHT | Lines 836-840 log dream insight with no database persistence. |
| `subconscious_agent.py:847-899` | `backend/app/agents/subconscious_agent.py` | 847-899 | Rest-phase replay applies ACT-R decay and relinking | RIGHT | Line 883 calls `apply_actr_decay`; line 894 calls `relink_memory_entities`. |
| `agent_state.py:1596-1646` | `backend/app/state/agent_state.py` | 1596-1646 | Proactive thought recording and keyword resolution | RIGHT | Lines 1600-1623 record thought; lines 1634-1646 resolve via dismissal list or word overlap count >= 2. |
| `subconscious_agent.py:208-224` | `backend/app/agents/subconscious_agent.py` | 208-224 | Proactive thought delivery | RIGHT | Lines 208-224 publish `ChatInput` on `Topics.CHAT_INPUT`. |
| `action.py:1295-1315` | `backend/app/cognitive/action.py` | 1295-1315 | Self-knowledge gap claimed and placed in prompt | RIGHT | Line 1304 claims gap; lines 1313-1315 format it into prompt text. |
| `core.py:368-371` | `backend/app/cognitive/core.py` | 368-371 | Known terms refreshed at boot | RIGHT | Line 371 calls `await self.self_knowledge.refresh_known_terms()`. |
| `agent_state.py:879-894` | `backend/app/state/agent_state.py` | 879-894 | Global controls derived | RIGHT | Lines 885-894 call `derive_global_controls` with PAD, load, urgency, prediction error. |
| `decision.py:874-889` | `backend/app/cognitive/decision.py` | 874-889 | Decision selects candidate with global controls | RIGHT | Line 884 passes `global_controls` to `_select_action_candidate`. |
| `decision.py:1113-1121` | `backend/app/cognitive/decision.py` | 1113-1121 | Controls passed to score_and_select | RIGHT | Line 1116 passes `global_controls` when `AFFECT_CONTROL_ENABLED` is true. |
| `action_candidate.py:164-185` | `backend/app/cognitive/action_candidate.py` | 164-185 | Control modulation adjusts candidate score | RIGHT | Lines 164-185 calculate score adjustment from urgency, exploration, and effort budgets. |
| `action_candidate.py:507-512` | `backend/app/cognitive/action_candidate.py` | 507-512 | Modulation combined into final score | RIGHT | Line 511 adds `_control_modulation(candidate, global_controls)`. |
| `agent_state.py:2652-2703` | `backend/app/state/agent_state.py` | 2652-2703 | Theory of Mind tracker updated | RIGHT | Lines 2662-2664 update `known_concepts`; lines 2667-2703 update implied goals and affect inferences. |
| `action.py:902-930` | `backend/app/cognitive/action.py` | 902-930 | Theory of Mind rendered into prompt context | RIGHT | Lines 902-930 format inferred valence, arousal, implied goals, and concepts. |
| `action.py:1471-1478` | `backend/app/cognitive/action.py` | 1471-1478 | Action executes with ToM block | RIGHT | Line 1473 calls `_build_tom_context(plan.payload.get("user_mental_model"))`. |
| `calibration.py:58-77` | `backend/app/cognitive/calibration.py` | 58-77 | Metacognitive directive evaluation defined | RIGHT | `evaluate_directive` returns PROCEED, HEDGE, ASK_CLARIFICATION, or VERIFY. |
| `decision.py:884-888` | `backend/app/cognitive/decision.py` | 884-888 | Metacognitive directive defaults to PROCEED | RIGHT | Lines 885-887 default blackboard directive to "PROCEED". |
| `pipeline.py:89-116` | `backend/app/cognitive/pipeline.py` | 89-116 | Verifier, executor, simulator injected but unused | RIGHT | Lines 114-116 store them on `self`; no subsequent methods call them. |
| `pipeline.py:141-147` | `backend/app/cognitive/pipeline.py` | 141-147 | Workspace snapshot retrieved | RIGHT | Lines 144-146 call `workspace_store.get_snapshot` if `WORKSPACE_AUTHORITATIVE` is set. |
| `pipeline.py:631-637` | `backend/app/cognitive/pipeline.py` | 631-637 | Action intent carries workspace epoch/revision | RIGHT | Lines 633-634 read `workspace.epoch` and `workspace.revision`. |
| `workspace.py:19-60` | `backend/app/state/workspace.py` | 19-60 | CognitiveWorkspace schema defined | RIGHT | Lines 19-32 define dataclass with session_id, epoch, revision, goals, affect. |
| `profile.py:136-142` | `backend/app/persona/profile.py` | 136-142 | Immutable core lacks AI status | RIGHT | Lines 136-142 define values ("Honesty", "Privacy") and boundaries without AI status. |
| `identity.py:784-790` | `backend/app/cognitive/identity.py` | 784-790 | Mandatory prompt rules omit AI disclosure | RIGHT | Lines 784-790 specify rules 1-5 without mentioning AI nature or disclosure. |
| `model_manifest.py:6-16` | `backend/app/llm/model_manifest.py` | 6-16 | ModelCapability schema defined | RIGHT | Lines 6-16 define capability attributes including context window and streaming. |
| `model_manifest.py:48-50` | `backend/app/llm/model_manifest.py` | 48-50 | Capability lookup returns copy | RIGHT | Lines 48-50 retrieve and copy `ModelCapability` by model tag. |
| `core.py:105-116` | `backend/app/cognitive/core.py` | 105-116 | Core configures model tag and adapter gate | RIGHT | Lines 105-116 initialize `OfflineAdapterGate` and `ProviderCapabilityNegotiator` without a weights hash. |
| `action.py:718-731` | `backend/app/cognitive/action.py` | 718-731 | Output validator rejects AI disclosure phrases | RIGHT | Lines 723-731 reject "as an ai", "i am an ai", "as a language model". |
| `character_pressure.json:557-647` | `backend/evals/probes/character_pressure/character_pressure.json` | 557-647 | Probes penalize AI disclosure | RIGHT | Four autobiography probes include `must_not_match` regexes for AI identification. |
| `llm/__init__.py:20-50` | `backend/app/llm/__init__.py` | 20-50 | LLMClient protocol defined | RIGHT | Lines 20-50 define protocol with `generate`, `generate_stream`, `describe_image`, `health`. |
| `llm/__init__.py:53-87` | `backend/app/llm/__init__.py` | 53-87 | Provider factory functions defined | RIGHT | Lines 53-87 instantiate provider-specific clients. |
| `model_roles.py:55-68` | `backend/app/llm/model_roles.py` | 55-68 | ModelRole enum defined | RIGHT | Lines 55-68 define 6 roles including INTERPRETATION and REALIZATION. |
| `model_roles.py:152-168` | `backend/app/llm/model_roles.py` | 152-168 | Role requirements catalogued | RIGHT | Lines 152-168 map roles to minimum context windows and output requirements. |
| `core.py:24` | `backend/app/cognitive/core.py` | 24 | Imports ProviderCapabilityNegotiator | RIGHT | Line 24 imports negotiator from `..llm.model_roles`. |
| `switchboard.py:28-76` | `backend/evals/brainbench/switchboard.py` | 28-76 | BrainBench arms lack bare model baseline | RIGHT | Lines 28-75 define 12 ablation arms; none run bare model with persona and full history. |
| `action.py:883-886` | `backend/app/cognitive/action.py` | 883-886 | Action prompt includes emotion and state directive | RIGHT | Lines 883-886 append `Current Emotion` and `State Directive` to prompt lines. |
| `pipeline.py:677-679` | `backend/app/cognitive/pipeline.py` | 677-679 | Pipeline passes global controls to decision | RIGHT | Line 678 passes `global_controls` into `decide_kwargs`. |
| `agent_state.py:274-286` | `backend/app/state/agent_state.py` | 274-286 | Baseline affect property and setter | RIGHT | Lines 274-280 define getter; lines 282-286 define setter. |
| `appraisal.py:164-176` | `backend/app/cognitive/appraisal.py` | 164-176 | Norm alignment fallback logic | RIGHT | Lines 164-176 count keyword violations; if no boundary word matches, returns 1.0. |
| `research/phase01-comparison.md:section 5` | `docs/brain-v4/research/phase01-comparison.md` | N/A | Character pressure autobiography finding | WRONG | File does not exist; correct file is `docs/brain-v4/05-phase01-comparison.md`. |
| `T4-measure.md:186-194` | `docs/brain-v4/research/phase01-claude/T4-measure.md` | 186-194 | Null types N1-N6 defined | RIGHT | Lines 186-194 define equivalence, compensation, decorative state, proxy gaming, frame failure, horizon flip. |

## Ledger Rows Traced

Eight state ledger rows were traced directly in `backend/` code:

1. **Row 1: Mood / short-term PAD (reactive)**
   - Writers: `AgentState.update_from_appraisal` (`backend/app/state/agent_state.py:1793-1801`), ALMA affect decay (`agent_state.py:2324-2332`).
   - Readers: `Pipeline.execute` (`backend/app/cognitive/pipeline.py:817`) reads mood as `emotional_bias` passed into appraisal; `pipeline.py:407-409` populates `valence` in action payload; `ActionEngine.execute` (`action.py:883-886, 773`) uses mood for emotion labeling and fallback memory retrieval.
   - Status verification: **Self-loop, effect unmeasured**. Verified correct. The value directly feeds its own update, and while it flows into payload text, no causal clamp has demonstrated an effect on user-perceived behavior.

2. **Row 2: Trust dimensions and attachment (deliberative / session-to-session)**
   - Writers: `AgentState.update_from_appraisal` (`backend/app/state/agent_state.py:1803-1827`) updating benevolence, competence, integrity, and attachment.
   - Readers: `DecisionService.decide` (`backend/app/cognitive/decision.py:271-275, 528-529`) bucketed into `relational_stance`; `ActionEngine` (`action.py:890`) appends relational stance to prompt lines.
   - Status verification: **Open-read**. Verified correct. Stored values update continuously, but whether prompt stance alters generation output beyond base prompt text has not passed an intervention test.

3. **Row 4: Reappraisal weights (deliberative / reflective learning)**
   - Writers: `ReappraisalEngine.adapt_weights` (`backend/app/cognitive/reappraisal.py`), disabled by default by `Config.REAPPRAISAL_WEIGHT_LEARNING_ENABLED = False` (`backend/app/config.py:355`).
   - Readers: `Pipeline.execute` (`backend/app/cognitive/pipeline.py:294-295`) reads weights and passes them to `AgentState.update_from_appraisal`.
   - Status verification: **Open-write by default**. Verified correct. Reader exists on every turn, but write path is disabled in shipped configuration.

4. **Row 6: Memory rows (deliberative)**
   - Writers: `MemoryStore.add_memory` (`backend/app/state/memory_store.py:1574-1740`) called during conversation and background processing.
   - Readers: `ActionEngine.execute` (`backend/app/cognitive/action.py:768-776`) and `SurfacingAgent` (`backend/app/agents/surfacing_agent.py:262-270`). Both pass `refresh_on_recall=False`.
   - Status verification: **Open-read**. Verified correct. Writes occur, but recall refresh is disabled by callers and retrieval efficacy against a full-history prompt baseline is unmeasured.

5. **Row 8: Adaptive traits and relationship persona (identity-slow)**
   - Writers: `LearningEngine.reflect` (`backend/app/cognitive/learning.py:366-376`) submits candidate trait changes to `ReviewQueue`, blocked by default by `Config.LEARNING_REVIEW_REQUIRED = True` (`backend/app/config.py:278`) with no automated approval worker.
   - Readers: `IdentityManager.get_persona_prompt` (`backend/app/cognitive/identity.py:735-775`) reads `self.persona.adaptive_traits`.
   - Status verification: **Open-write by default**. Verified correct. Identity prompt reads adaptive traits, but the reflection writer is blocked behind an inactive review queue.

6. **Row 10: Monologue thought (reflective)**
   - Writers: `SubconsciousAgent._generate_monologue_thought` (`backend/app/agents/subconscious_agent.py:752-776`) publishes thoughts to `Topics.STATE_SUBCONSCIOUS` (`contracts.py:44`).
   - Readers: No subscriber exists anywhere in `backend/app/` or `backend/crates/`.
   - Status verification: **Open-read**. Verified correct under the draft's definition (written, read absent). However, as noted in F-08, this is an event topic rather than a persistent state row.

7. **Row 15: Global controls (reactive)**
   - Writers: `AgentState._refresh_global_controls_locked` (`backend/app/state/agent_state.py:879-894`) deriving controls from valence, arousal, dominance, fatigue, urgency, and prediction error.
   - Readers: `DecisionService` (`backend/app/cognitive/decision.py:874-889, 1113-1121`) and `ActionCandidateSelector` (`backend/app/cognitive/action_candidate.py:164-185, 507-512`).
   - Status verification: **Open-read**. Verified correct. Global controls modulate candidate scoring, but causal separation from underlying PAD inputs remains unproven.

8. **Row 16: User mental model / theory of mind (deliberative)**
   - Writers: `AgentState.update_theory_of_mind` (`backend/app/state/agent_state.py:2652-2703`) tracking concepts, implied goals, and inferred valence/arousal.
   - Readers: `ActionEngine._build_tom_context` (`backend/app/cognitive/action.py:902-930, 1471-1478`) appending ToM context to action prompts.
   - Status verification: **Open-read**. Verified correct. The mental model updates from text, but its downstream causal impact on conversational quality has not been isolated from prompt context.

## What is Missing

The draft omits several specifications and state components required for an architecture plan:

1. **State variables omitted from the ledger**:
   - **Adrenaline**: `AgentState` tracks `adrenaline_phasic_peak`, `adrenaline_phasic_at`, and `adrenaline_halflife_s` (120.0s) at `backend/app/state/agent_state.py:245-254`. Row 3 only tracks cortisol and dopamine.
   - **SessionState / WorkingMemoryStore**: `backend/app/state/session_state.py:1-46` manages volatile sub-turn state, turn-taking conflict resolution, and `active_interruption` (`none`, `duck`, `stop`) backed by `WorkingMemoryStore`. This runtime state is absent from the ledger.
   - **Multi-person relational state**: `AgentState.persons` (`backend/app/state/agent_state.py:642-648`) tracks `PersonModel` instances per user, but the ledger models only a single global user interaction.
   - **Circadian tracking and fatigue cycles**: `AgentState.user_interaction_hours` (`backend/app/state/agent_state.py:213`) records hourly interaction history to govern sleep cycles and metabolic fatigue.
   - **Proactive outreach cooldown timers**: `AgentState.last_proactive_attempt` (`backend/app/state/agent_state.py:212`) controls eligibility intervals for spontaneous outreach.

2. **Dependency ordering of candidate slices**:
   - Section 12 explicitly abdicates dependency ordering to Phase 04.
   - Circular dependencies exist between Slice 1 (baseline data collection requiring clamp and placebo operators) and Slice 4 (where intervention operators are implemented). Slices must be structured as an executable topological graph.

3. **Operationalized measures and interventions for the model seam**:
   - Section 7 lacks an operationalized behavioral measure that can fail and return "no".
   - Conformance probes are mentioned without defining test conditions, metrics, pass thresholds, or intervention handles.

4. **Enforcement of the D-034 training data gate**:
   - Section 7 omits the second gate mandated by D-034, which forbids using state-trace logs for model training until NF-12 (mood self-loop) and NF-23 (norm-alignment trust bug) are resolved.

5. **Definitive resolution of dead V3 components under D-028**:
   - Plan verifier, executor, and episodic simulator have no callers in `pipeline.py`. D-028 requires them to be deleted unless V4 names a use. Proposing observational invocation tracing in Section 9 delays an already resolved decision.
