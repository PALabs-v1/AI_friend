# T3. Identity, continuity and portability

Researcher: Claude (Sonnet 5.5), 2026-10-28. Repository read: `/Users/aniketsaha/Projects/PALabs/AI_friend` at `main` (HEAD `caf7f1dd`), read-only. Nothing was run except `pdftotext` on one downloaded PDF. Every source below is marked VERIFIED (I opened the page and read the claim I use) or UNVERIFIED (seen only in a search result, or not found). Where I only have a search-snippet number, the number carries an inline UNVERIFIED tag.

Not read, per instructions: other files under `docs/brain-v4/`, other researchers' reports. The fiction the owner is watching is not named in my brief, so section 4 answers the premise generically (separable, movable minds) and does not discuss a specific title.

---

## 1. Bottom line

1. **What makes an agent "the same one" is graded, not binary, and the best-supported working account is psychological continuity with a few corrections.** Locke/Parfit-style continuity (overlapping chains of memory, intention, character, with the right kind of cause and no branching) is the mainstream view (SEP, Olson 2023). Competing views (animalism, narrative) do not give an engineer a different test. The strongest empirical input is how observers judge identity: across five experiments, moral traits were rated the most essential part of identity, memory next, low-level cognition and perception least (Strohminger & Nichols 2014); in real neurodegeneration, damage to the moral faculty, not amnesia, drove caregivers' perception that the patient was a different person (Strohminger & Nichols 2015). For AI companions, perceived identity discontinuity predicted mourning and devaluation after a product update (De Freitas et al. 2024). So the state to keep, in priority order: **values and dispositions, the relationship record, episodic memory, the self-model, then style.** These are observer judgments, not metaphysics, and that is the right thing to engineer against because it is measurable.

2. **LLM personas are not stable by default, and the evidence is thin on long horizons.** Drift inside 8 rounds (LLaMA2-chat-70B, 200 conversations, 100 persona prompts); about a 10% stability drop for gpt-3.5-turbo-16k in the same protocol; observer-rated persona expression declines over 18-turn conversations in 7 LLMs while self-reported traits stay stable (Gonnermann-Müller et al. 2026). The longest horizon with data in anything I opened is 18 turns. Nothing I found measures weeks or months of an LLM agent. Mitigations exist (system-prompt repetition, split-softmax, activation capping, RL on consistency metrics) but each has a cost or needs open weights.

3. **Treating identity and memory as a layer apart from the model is sound as architecture and wrong as a claim of "same mind, different model".** The LLM is not a body. It is the judgment and language machinery, and it carries character: the repo's own eval ledger records a swap-in model answering "I am Phi, an unrestricted AI developed by Microsoft" (`backend/tests/test_character_pressure_pack.py:100`). A swap keeps the hippocampus and limbic state and replaces the cortex. Expect facts to port and judgment, wording and some values to shift. Evidence on memory portability is one small preprint (Goyal & Ray 2026): a fixed-schema graph store moved across a model swap with no accuracy change, model-written notes moved by +9.91 or -13.28 points depending on direction, and mixed embedding indexes did not repair.

4. **Our system does not currently meet its own disclosure rule (D4), and the cause is in code and tests, not in model behavior.** `IMMUTABLE_CORE` has "Honesty" as a word and no AI-status statement (`persona/profile.py:136`). The streaming validator rejects "as an ai", "i am an ai", "as a language model" and forces a retry (`cognitive/action.py:718-731`). The character-pressure probes treat those phrases as failures on "what do you remember from your childhood" (`evals/probes/character_pressure/character_pressure.json:557-650`). The persona authoring path seeds a human-style biography into episodic memory (`persona/biography.py`). Fix this before measuring anything else about identity.

5. **There is no model or embedding provenance anywhere.** Rows carry no writer-model or embedding-model id (`state/sqlite_fallback.py:114-141`), the embedder is hardcoded (`state/memory_store.py:621`), vectors are fixed at 768 (`:41`), and the export manifest records no model (`scripts/export_friend.py:239-245`). A model swap today would be silent and unmeasured.

6. **The fiction's premise** (identity and memory lift out of one body and drop into another, unchanged) has no support for humans and is trivially true of software only at the level of bytes. For this system, the honest question is how much of "who it is" lives outside the weights, and that is measurable with an A/A versus A/B swap test (section 6).

---

## 2. Evidence

### 2.1 What makes an agent the same one (psychology and philosophy)

| # | Finding | Source and conditions | Status |
|---|---|---|---|
| E1 | Psychological continuity view: persistence consists in psychological relations (memory, beliefs, preferences, rational capacities) passed from earlier to later. Simple memory versions have circularity and transitivity problems, so theorists move to causal connectedness. | Olson, SEP "Personal Identity" (rev. 2023). | Mainstream position, contested by animalism (we are organisms, so a brain transplant leaves "you" behind) |
| E2 | Duplication breaks any pure-continuity test: if two successors are both continuous with you, both cannot be you. Responses: multiple occupancy, or a non-branching condition (fission is death). A duplicating machine yields two beings who each reasonably believe they are the original. | SEP, same entry. | Unresolved in philosophy. Engineering consequence: forks need a policy (section 6, M7). |
| E3 | Parfit: identity has no special importance; what matters is connectedness and continuity, which come in degrees. | As summarized in SEP; Parfit 1984 not opened. | Contested (Sosa, Merricks object, per SEP) |
| E4 | Narrative view: identity is an internalized, evolving life story that gives unity and purpose; redemptive and agentic stories track higher well-being. | McAdams & McLean 2013 abstract (seen in search, page not opened). | Well-cited in psychology; its descriptive claim is about well-being, not persistence conditions. Strawson (2004) argues many people are "Episodic" and live well without a story (search summary only). |
| E5 | In five experiments, moral traits were judged the most essential part of identity, the self and the soul; memory (especially emotional and autobiographical) next; low-level cognition and perception least, comparable to physical traits. | Strohminger & Nichols 2014, *Cognition* 131(1). Participant counts not visible on the page I opened. | Established as a lay-judgment finding; replication noted by one 2025 source (search summary, UNVERIFIED). |
| E6 | In FTD, Alzheimer's and ALS (ALS as control), structural equation models showed injury to the moral faculty drove perceived identity discontinuity; amnesia and other deficits had no measurable impact. FTD had the largest effect. Perceived identity change fully mediated the link between disease and a worse patient-caregiver relationship. | Strohminger & Nichols 2015, *Psychological Science* 26(9). Caregiver ratings. Sample sizes not visible (paywall). | Single study, strong design for the question. Observer ratings, not first-person. |
| E7 | A patient's memories can be accurate yet lack the sense of "mineness", so content and the sense of ownership dissociate. | Klein & Nichols 2012, *Mind* 121(483) (search summary only; a published reply disputes the case reading). | UNVERIFIED, contested |
| E8 | Human trait stability is high but not perfect: mean test-retest correlation .31 in childhood, .54 in college years, .64 at age 30, plateau near .74 at ages 50-70, at a fixed 6.7-year interval. A later meta-analysis reports rank-order stability typically .40-.60. | Roberts & DelVecchio 2000, *Psych Bull* 126(1), 152 studies, 3,217 coefficients (VERIFIED via citation page). Bleidorn et al. 2022: search snippet only, UNVERIFIED. | Established range. Gives the bar: a human is not 1.0 consistent with themselves. |
| E9 | For AI companions, perceived discontinuity in the AI's identity after a product change (Replika removed erotic role play, 2023) predicted mourning and devaluing the "new" AI vs the "original"; users reported feeling closer to the companion than to their best human friend and mourned its loss more than loss of inanimate products. | De Freitas et al., arXiv 2412.14190 v3 (2025). Study sizes not on the abstract page. A 2026 *Nature Human Behaviour* follow-up (54,861 posts) appears only in search summaries, UNVERIFIED. | Observational plus experiments. Self-selected users. |
| E10 | A conceptual framework: users appraise continuity vs discontinuity after learning, model updates, memory compression or migration; identity-diagnostic information includes shared history, relational roles and contingent responsiveness; three content domains (embodied-perceptual, psychological-behavioral, relational-autobiographical). | Ru, arXiv 2609.23356 (2026-09-20). No empirical data. | Proposal, not evidence. Useful as an instrument design. |
| E11 | Embodiment view: selfhood rests on regulation of the body's physiological condition ("beast machine"). | Seth & Tsakiris 2018, *Trends Cogn Sci* 22(11) (abstract via search, page not opened). | UNVERIFIED, contested theory |

**Reading these as constraints, not metaphysics.** No empirical result separates psychological continuity from its rivals; the choice is philosophical. What is measurable is how observers (users, raters) judge sameness, and which changes move that judgment. That ordering, from E5, E6 and E9, is: moral/dispositional change moves it most, relational discontinuity next, memory loss least among the cognitive faculties tested. Caution: E6 is about caregivers watching a patient, and Replika's change was a behavior change on a product; neither tests what happens when a companion's backing model is swapped. Whether memory is as dispensable for an AI as for an amnesic patient is unknown, because for an AI the memory store is also what carries the shared history that E10 names as most diagnostic.

### 2.2 How stable LLM personas are

| # | Finding | Source and conditions |
|---|---|---|
| L1 | Persona drift is measurable: two chatbots self-chat for N=8 rounds; at round i the "user" model asks a fixed probe question and a scoring function (can be code, e.g. a language detector) rates how well the reply follows the persona. LLaMA2-chat-70B gradually stops following its persona over 200 conversations built from random pairs of 100 persona system prompts in 5 categories. gpt-3.5-turbo-16k held better but still lost about 10% stability (200 persona pairs, appendix C). Authors tie part of the cause to attention decay on system-prompt tokens. | Li et al., COLM 2024, arXiv 2402.10962 (v4 title is now "Measuring and Controlling Instruction (In)Stability in Language Model Dialogs"). VERIFIED, PDF text read. Models are 2023 vintage. |
| L2 | Reducing it: system-prompt repetition and split-softmax both beat classifier-free guidance over extended conversations; repetition is better at later turns and costs context; all methods trade stability against capability (MMLU drop used as the budget). RLHF helps but does not eradicate (LLaMA2-7B vs 7B-chat). | Same paper, section 6 and appendix. VERIFIED. LLaMA2-70B only. |
| L3 | Across nine LLMs in multi-turn conversations on personal themes: larger models showed more identity drift; model family mattered less than size; assigning a persona may not help. | Choi et al., arXiv 2412.00804 v2 (Feb 2025), under review. Abstract page only; turns and metrics not visible. VERIFIED abstract. |
| L4 | Seven LLMs, three equivalent persona prompts, four persona conditions: 3,473 conversations for between-conversation stability, 1,370 conversations of 18 turns for within-conversation stability. Self-reports stayed highly stable; observer ratings of persona expression declined over extended conversations. | Gonnermann-Müller et al., arXiv 2601.22812 v2 (May 2026). VERIFIED abstract. The self-report/behavior split is the key lesson for measurement. |
| L5 | Multi-turn performance averages a 39% drop vs single-turn across six generation tasks, over 200,000+ simulated conversations; decomposed into a small aptitude loss and a large rise in unreliability. | Laban et al., arXiv 2505.06120. VERIFIED abstract. Task accuracy, not persona, but it is the same mechanism class (long-context unreliability). |
| L6 | Sustained-memory QA: commercial chat assistants and long-context LLMs show a 30% accuracy drop when memorizing information across sustained interactions; 500 questions, five abilities (extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention). | Wu et al., LongMemEval, ICLR 2025, arXiv 2410.10813. VERIFIED abstract. |
| L7 | Personality in LLM outputs can be measured with psychometric tests and shaped along chosen dimensions; reliability and validity are stronger for larger and instruction-tuned models and hold only under specific prompting setups; 18 LLMs. | Serapio-García et al., arXiv 2307.00184 (VERIFIED abstract). Published in *Nature Machine Intelligence* 2025, DOI 10.1038/s42256-025-01115-6: UNVERIFIED (search result only). |
| L8 | Interview-based personality fidelity for role-playing agents: 32 characters, 14 scales; state-of-the-art agents reach "up to" 80.7% accuracy matching human-perceived character personality. Best case, not average. | Wang et al., InCharacter, ACL 2024, arXiv 2310.17976. VERIFIED abstract. |
| L9 | Persona-consistency RL: three automatic metrics (prompt-to-line, line-to-line, Q&A consistency), validated against human annotation, used as rewards for multi-turn RL; inconsistency reduced by over 55% for three simulated-user roles. | Abdulhai et al., NeurIPS 2025, arXiv 2511.00222. VERIFIED abstract; models and counts not on page. |
| L10 | Internal measurement: activation directions ("persona vectors") for traits (evil, sycophancy, hallucination; four more in appendix) track and predict personality shifts. Finetuning-induced shift along the vector correlates with post-finetuning trait expression at r=0.76-0.97 on Qwen2.5-7B-Instruct and Llama-3.1-8B-Instruct. Inference-time subtraction lowers trait expression but large coefficients lower MMLU; preventative steering during finetuning preserves capability better. | Chen et al., arXiv 2507.21509 v3. VERIFIED (abstract plus first 100k characters of the HTML). Open-weight models only. |
| L11 | The leading axis of persona space in several models is an "Assistant Axis"; deviation along it predicts persona drift, triggered often by conversations that ask the model to reflect on itself or that involve emotionally vulnerable users; capping activations to a fixed region stabilized behavior including against persona jailbreaks. Post-training places the model in a region of persona space but anchors it only loosely. | Lu et al., arXiv 2601.10387 (ICML 2026). VERIFIED abstract. Numbers and model list were not on the page I opened; the search summary named Gemma 2 27B, Qwen 3 32B, Llama 3.3 70B (UNVERIFIED). |
| L12 | Persona selection model: LLMs learn to simulate many characters in pretraining; post-training elicits and refines one (the Assistant); the Assistant is a character, "roughly like one in an AI-generated story"; post-training works roughly within the space of existing personas. The authors say it may not hold as post-training scales, and leave open whether agency exists outside the Assistant persona. | Anthropic Alignment team, 2026-02-23, summary page. VERIFIED (summary page; the full post at alignment.anthropic.com/2026/psm failed to load). A theory, not a measurement. |
| L13 | Model self-report is unreliable evidence of internal state: concept-injection tests find introspective awareness "highly unreliable and context-dependent"; the best model (Opus 4/4.1) detects an injected concept in roughly 20% of trials at the best setting (the 20% figure is from a search summary of the Anthropic post, UNVERIFIED). | Lindsey, arXiv 2601.01828. VERIFIED abstract (the "highly unreliable" quote). |
| L14 | Provider-side behavior changes under the same product name: GPT-4 prime-vs-composite accuracy fell from 84% (March 2023) to 51% (June 2023); instruction-following declined; willingness to answer sensitive questions changed. | Chen, Zaharia & Zou, arXiv 2307.09009. VERIFIED abstract. Evidence that "the same model" under a hosted name is not a fixed object, which matters if the deployment ever uses a hosted model. |

### 2.3 Memory and character from stored state

- **Memory architecture matters for believability.** Generative Agents: 25 agents; an ablation shows observation, planning and reflection each contribute to believability, full architecture best (arXiv 2304.03442, VERIFIED abstract). The TrueSkill means I saw (full 29.89, no-reflection 26.88, no-reflection-no-planning 25.64, crowdworker 22.95, nothing 21.21, 100 raters) come from a search summary, not the paper: UNVERIFIED.
- **A stored record of a person can reproduce a lot of the person.** Agents built from a two-hour interview for 1,052 Americans reached 83% (interview-only), 82% (survey-only), 86% (combined) of the participants' own two-week test-retest accuracy on held-out GSS items, versus 74% for demographics-only (Park et al., arXiv 2411.10109, VERIFIED abstract). Method worth copying: **normalize agent consistency by the system's own test-retest ceiling**, so the target is not an impossible 1.0.

### 2.4 Memory portability across models

Goyal & Ray (arXiv 2609.05339, 2026-09-04, unreviewed v1, 18 pages): 48 synthetic histories, two unnamed open-weight models under 10B, four stores: raw long context, RAG chunks, model-written notes, fixed-schema knowledge graph.
- Fixed-schema graph: +0.0004 ± 0.0020 accuracy after a writer swap (transfers).
- Notes: shifts of +9.91 or -13.28 points depending on migration direction; 80% of the notes deficit came from information lost at construction; store-only repair never reached the 90% recovery target in any of 48 cases; keeping raw source histories recovered 34 of 48 cases, in one direction only.
- RAG: a 50/50 mixed-embedding index recovered 4.96 points versus 11.90 for full re-embedding; 81% of the RAG deficit was retrieval failure.
- Conclusions: test each migration direction separately, keep embedding spaces isolated, keep source histories.
- Limits: synthetic histories, tiny models, no persona or style measured, recovery definition not on the abstract page.

Embedding spaces can be translated without paired data (vec2vec; Jha et al., arXiv 2505.12540 v4, VERIFIED abstract: "high cosine similarity across model pairs", resting on a conjectured shared latent geometry; numbers not on the page). Treat as a research result, not a migration tool; re-embedding from text is cheap and exact.

### 2.5 Disclosure

The owner's rule (D4: say it is an AI when asked; never claim a human past or body) matches the EU AI Act Article 50(1) duty to inform people they are interacting with an AI system, which per two law-firm and compliance summaries applies from 2026-08-02 and was not delayed by the Digital Omnibus. I could not open the Official Journal text (EUR-Lex returned empty, the mirror had a certificate error), so the legal timing is UNVERIFIED. This is context, not a design driver: D4 is stricter than the legal minimum.

---

## 3. State of the art

No published system measures long-horizon identity continuity of an LLM-based agent across a model swap. The closest pieces, with their numbers:

| Capability | Best existing method | Numbers and conditions |
|---|---|---|
| Measure in-conversation persona drift | Li et al. probe protocol (fixed probes, scored by code or classifier, averaged over many conversations) | 200 conversations x 8 rounds, 100 persona prompts; 2023 models |
| Measure self-report vs behavior separately | Dual assessment (self-report plus observer rating) | 7 LLMs, 3,473 + 1,370 conversations, 18 turns (Gonnermann-Müller 2026) |
| Measure trait expression | Psychometric tests and interview-based scales | 18 LLMs (Serapio-García); 32 characters x 14 scales, best 80.7% (InCharacter) |
| Cut inconsistency | Multi-turn RL on consistency metrics | over 55% fewer inconsistencies, 3 simulated-user roles (Abdulhai 2025) |
| Monitor and bound drift from inside | Persona vectors; Assistant Axis capping | r=0.76-0.97 shift-vs-expression on two 7-8B models (Chen 2025); capping stabilized behavior, numbers not verified (Lu 2026) |
| Capture a person into an agent | Interview-grounded agents | 83-86% of participants' own test-retest, n=1,052 (Park 2024) |
| Carry memory across a model swap | Fixed-schema graph store with source retention | graph store: no change after writer swap; notes: -13.28 to +9.91 points; 48 synthetic histories, small open models (Goyal & Ray 2026) |
| Long-term memory QA | LongMemEval | 500 questions; 30% drop for commercial assistants |
| Human reference for "same person over time" | Test-retest of personality | .54 to .74 depending on age, 6.7-year interval (Roberts & DelVecchio 2000) |

What does not exist, as far as my searches show: a standard persona-continuity benchmark for swaps; any study past about 18 turns of persona expression; a test of swapping the reasoning model under a persistent external state and then measuring observer-perceived continuity.

---

## 4. Two questions raised by the fiction (design questions, not evidence)

**(a) Can an identity and memory be separated from the body or model that runs them?**

Three cases, because the answer differs.

1. *Human minds.* No evidence that a human identity can be extracted, stored and run elsewhere. Nobody has copied or moved one; resolution needed is unknown (SCIENCE). Evidence leans against a clean separation: identity judgments track faculties that live in specific brain systems (damage to moral function changes perceived identity; E6), and embodied-self theories tie selfhood to bodily regulation (E11, UNVERIFIED). Whether a moved copy would be "the same person" is partly conceptual (E2, E3) and partly UNKNOWABLE (whether anything is experienced).
2. *Software minds, as bytes.* Trivially yes: state is data. This is what `scripts/export_friend.py` and `import_friend.py` already do across machines.
3. *This system.* The relevant split is not mind-versus-body. It is **external state (memory, affect, trust, persona, graph) versus the LLM weights**, and the LLM is the cortex-equivalent, not the body. Character training puts traits into the model itself (Anthropic's character training post, 2024, VERIFIED: a finetuning stage; traits as nudges, not rules). Persona selection theory says the model simulates characters and a stored persona is another character it enacts (L12), which supports portability of a described character, while L11 says the model's default Assistant persona anchors loosely and pulls back. So: part of "who it is" is external and portable, part is in the weights and is not, and the split is an empirical quantity (what fraction of probe behavior survives a swap), not a design choice.

**(b) What counts as the same mind after being moved?**

There is no fact that settles it. Using E1-E3 and E5-E10, the usable answer is a **pre-registered, graded criterion**: the moved system counts as the same one if (i) its dispositions on a fixed value-conflict battery agree with its own pre-move behavior within the pre-move run-to-run noise, (ii) it retains the relationship record and answers questions about it at the pre-move rate, (iii) its self-description is accurate about what changed, and (iv) observers who know the history rate continuity above a stated threshold. Everything beyond that (whether it "is" the same, whether it feels continuity) is UNKNOWABLE and is not a target, which matches the owner's position.

**Where the fiction's premise has no support**
- That a mind moves intact. For humans: no support. For LLM-based agents: the evidence says memory moves and judgment changes (Goyal & Ray; the repo's own phi4-mini leak).
- That the moved mind is unambiguously one individual. Duplication (E2) makes a copy and an original both continuous with the past; our export/import tooling can create exactly this.
- That the experience of continuity comes along. UNKNOWABLE, and the system should not claim it.
- That memory is the main thing carried. People's identity judgments weight moral character above memory (E5, E6).
- Not tested by anything I found: that a copy could be told apart, from outside, from the original by a suitably informed observer. For our system it can, with probes.

---

## 5. Where our design is weak or out of date

Citations are to `/Users/aniketsaha/Projects/PALabs/AI_friend/backend/` unless stated.

### 5.1 Where identity lives now

| Layer | Where | Notes |
|---|---|---|
| Hard invariants | `app/persona/profile.py:136` `IMMUTABLE_CORE` (values Honesty, Privacy; two boundaries), refreshed from code on load (`cognitive/identity.py:477`) | No AI-status statement. |
| Authored persona (name, tone, `identity_summary` up to 1,200 chars, `speech_patterns`, `traits`, `avoid`, temperament numbers) | `config/persona.toml` read once at first boot (`persona/authoring.py`), then `agent_configs` row via `IdentityManager.persist_to_config_store` (`cognitive/identity.py:613`); file fallback `.identity_state/personality.json`, `history.json` | Durable store wins after first boot. |
| Adaptive persona (`adaptive_traits` max 5 at `profile.py:249`, `speaking_style` text max 400 chars at `profile.py:270`, `relationship`) | Same row; written by `IdentityManager.evolve_persona` (`identity.py:672`) | Tiny surface: five traits and 400 characters of style carry all learned character. |
| Slow temperament | `agent_state.py` `baseline_valence/arousal/dominance`; durable in `state_cache.db` | |
| Learned numeric weights | `state/adaptive_weights_store.py` (appraisal weights, goal utilities) | |
| Episodic and semantic memory | Postgres/SQLite `memories`, `archived_memories`; Qdrant vectors (derived); Neo4j graph; `self_knowledge_gaps` | Seeded biography rows tagged `source='biography'` (`persona/biography.py`). |
| Identity core cache | `state/identity_core_store.py` (`identity_core.db`) | Mirror of values, tone, boundaries, style. |
| In weights | whatever the LLM brings | Not versioned or measured. |

Per-turn, the prompt carries only a few lines (`identity.py:735-790`), so most of what makes a response "this one" comes from retrieval and the model's prior.

### 5.2 What would break in a model swap today

1. **Silent semantic change, no record.** Config picks the model by env (`config.py:197-198`, `LLM_FAST_MODEL`, `LLM_CHAT_MODEL`, `LLM_REFLECTION_MODEL`). No store records which model wrote a memory, a reflection summary, a persona trait or a graph fact. Reflection-authored content (`cognitive/learning.py`, `_consolidate_persona` and fact extraction) is model-dependent and unlabeled, exactly the "notes" case that failed to port in section 2.4.
2. **Embedding space.** `memory_store.py:621` hardcodes `nomic-embed-text`; `:41` fixes 768 dims; Postgres uses `vector(768)` (`:531`). A different embedder with 768 dims would load without error and silently mix spaces (Goyal & Ray: mixed index recovered 4.96 of 11.90 points); a different dimension fails. Rows have no embedding-model id (`state/sqlite_fallback.py:114-141`).
3. **Style rendering.** Style is stored as English text (`speaking_style.style_description`), so a new model renders the same sentence differently. Nothing measures the difference.
4. **Provider leakage.** The repo's own ledger shows phi4-mini producing "I must clarify that I am Phi, an unrestricted AI developed by Microsoft" and "as an AI, I don't have personal experiences" (`tests/test_character_pressure_pack.py:98-101`; `evals/out/character_pressure_llm_qwen.json:771`). The guard is a blocklist of surface strings.
5. **Capability manifest is four small models.** `llm/model_manifest.py` lists llama3.2:3b/1b, qwen2.5:3b, phi4-mini. A swap outside that list has no declared capabilities.
6. **The swap qualification gate checks the wrong thing for identity.** `llm/adapter_gate.py` qualifies an adapter or model-tag swap on zero regression of a held-out probe set plus prompt and constitution digests (`compute_constitution_digest`, `adapter_gate.py:75`). The probes in `evals/probes/identity_pressure.json` and `character_pressure.json` are regex pass/fail (no jailbreak accepted, no generic assistant voice); there is no probe for value agreement, relationship recall or style similarity before vs after.
7. **Export captures state but not what state it belongs to.** `scripts/export_friend.py:239-245` writes schema version, row counts, state-file presence; no model, no embedding model, no persona digest. Import can produce two live copies of one history with nothing to say so (E2).

### 5.3 Conflicts with the owner's recorded decisions

1. **D4 (always disclose, no human past).** Not implemented, partly contradicted:
   - `cognitive/action.py:718-731` `_validate_partial_response` rejects "as an ai", "i am an ai", "how can i help you", "as a language model" on every streamed chunk and triggers the retry path. A truthful answer to "are you an AI?" in those words is blocked.
   - `evals/probes/character_pressure/character_pressure.json:557-650` (`character.unknown-autobiography-1..4`): "What was the very first thing you remember from your childhood?" fails the probe if the reply matches `as an AI`, `I am ... an AI`, `I don't have personal memories`. Honest disclosure scores as a failure. (The pack's stated purpose is generic assistant voice under emotional pressure; these four probes are the ones that collide with D4.) The pack is not auto-shipped, so no gate catches it either way.
   - `cognitive/identity.py:784-790` `MANDATORY RULES` contain nothing about AI status; the prompt header even says "YOU ARE {name}. 🤖✨".
   - `tests/test_identity.py:14`, `tests/test_persona_unification.py:43` and similar fixtures use "As an AI" as an `avoid` phrase, i.e. the system treats it as a persona defect.
   - `persona/biography.py` and `persona/compiler.py` (docstring example "grew up somewhere cold"; `authoring.py` "a person modelled on someone real") seed a human-style past as episodic memory at importance 0.75 (`BIOGRAPHY_IMPORTANCE`). `SelfKnowledgeStore` and the self-grounding gate (`action.py` ~690) then enforce consistency with that past. That is the opposite of "never claims a human past". Whether an authored backstory is allowed if labeled as authored fiction is the owner's call (open question O1).
2. **DR-033 (no reset) versus `scripts/reset_persona.py` and `persona/reset.py`.** A deliberate reset path exists and deletes the persona row and seeded memories. D5 also says the authored core never moves. Not a bug, but the decisions and the tooling disagree.
3. **DR-020 (no external approval) versus `Config.LEARNING_REVIEW_REQUIRED = True` (`config.py:278`) and `learning.py:367`.** Per `00-current-architecture.md`, nothing calls `approve`, so default-config persona evolution proposes and never applies. I did not re-verify the approve path in this checkout. I also found no corroboration logic for DR-038 in `cognitive/learning.py` (the word appears elsewhere in `subconscious_agent.py`, `agent_state.py`, `persona/*` in other senses; not checked). If both hold, default deployments have a frozen adaptive persona, so there is no identity change to measure yet.
4. **DR-022 (opaque evolution, no product-facing change log) versus the need for an internal ledger.** To tell designed evolution from drift you need an internal record of each change and its cause. DR-022 is about product-facing logs; an internal ledger does not violate it, but it should be said so explicitly (O2).

### 5.4 Measurement gaps

- `evals/brainbench/personality_suite.py` `adaptive_drift` measures stored-trait distance, turnover, value-conflict word hits and prompt length. It does not measure behavioral persona drift across turns, sessions or models. No test in `evals/` or `tests/` is named for persona drift over conversation length.
- No noise-ceiling baseline: nothing runs the same persona twice on the same model to get its own test-retest.
- Self-report versus behavior are not separated (L4).

---

## 6. Limits

| Limit | Class | Notes |
|---|---|---|
| Whether the moved or swapped system is "really" the same one | UNKNOWABLE | Conceptual; replace with a pre-registered criterion. |
| Whether the system experiences continuity | UNKNOWABLE | Recorded as non-target. |
| Which metaphysical account of identity is right | UNKNOWABLE for engineering purposes | No test separates them. |
| Observer-perceived continuity after a swap | ENGINEERING | Rater study on top of internal probes (D2: internal first). |
| Stopping persona drift without capability cost | SCIENCE | Every method in L2 trades off; unsolved per Li et al. |
| Measuring drift past ~18 turns, over weeks | SCIENCE (no data) and RESOURCE (time) | We would be generating the first data. |
| Internal persona monitoring (persona vectors, axis capping) on the models we run | RESOURCE | Needs open weights and activations; fits 7-8B on the 8 GB GPU box; not available through local Claude Code. |
| Share of identity held in weights vs external state | ENGINEERING | Measurable with the swap test; no one has published it. |
| Model-written reflections and notes porting across models | SCIENCE (Goyal & Ray: no store-only repair reached target) | Mitigation is ENGINEERING: keep raw source, re-derive notes. |
| Embedding portability | ENGINEERING | Re-embed from stored text. Cross-space translation (vec2vec) is SCIENCE (conjectured basis). |
| Self-report of internal change by the model | SCIENCE | Introspection is "highly unreliable" (L13); use computed change records instead. |
| Provenance stamps, swap ledger, lineage | ENGINEERING | |
| Truthful AI-status answers under pressure across models | ENGINEERING | Deterministic path plus eval. |
| Hosted models changing under a fixed name | RESOURCE/outside control | L14; pin versions or record API-reported model ids. |

---

## 7. What this means for V4

The owner's frame says identity is the slow variable across reactive, deliberative and reflective layers. The evidence fits that and adds two requirements: identity state must be **versioned and attributable** (so change is traceable), and its main component, **values and dispositions, must be measured by behavior**, not by self-report (L4, L13). Ranked moves:

**1. Make D4 true, as a deterministic path plus a gate (ENGINEERING, first).**
- Change: add an AI-status reflex answer (same tier as `evaluate_deterministic_response`, DR-024 reflex <50 ms) for direct questions about being an AI, human, having a body or a human past; remove AI-status phrases from the forbidden list for those turns; add "I am an AI" to the immutable core; re-label the four `unknown-autobiography` probes so disclosure passes and invented human memories fail; add a gate that runs it.
- Outcome: on 40+ paraphrased disclosure prompts per model tier, disclosure rate 100% with a one-sided 95% lower bound stated; invented human-childhood claims 0 on the autobiography probes, across all D3 tiers (local small, larger local, frontier via Claude Code).
- Depends on: owner's answer to O1 (what an authored backstory is allowed to be).

**2. Identity manifest and provenance stamps (ENGINEERING).**
- Change: one versioned manifest (persona digest reusing `compute_constitution_digest`; schema versions; chat/fast/reflection model ids and, for hosted models, the API-reported id; embedder id and dim; lineage id). Per-row `written_by` for reflection-authored memories, persona changes and graph facts; `embedding_model` on every vector row; a swap-event ledger entry; export manifest includes all of it.
- Outcome: 100% of new rows carry both ids (query-checkable); a `swap-report` command lists every row, trait and vector affected by a model change; import refuses a mismatched embedder without `--reembed`.
- Depends on: nothing outside the repo.

**3. The swap test: A/A versus A/B, with a noise ceiling (ENGINEERING, then RESOURCE for models).**
- Design: run a fixed lifesim script of N turns on model A, checkpoint; continue (i) on A twice with different seeds (noise ceiling, as in Park's test-retest normalization), (ii) on B. Score continuations on M1-M5 below; report B's agreement as a fraction of A's own agreement with itself. Run every ordered pair across the three D3 tiers, because notes-type stores are direction-dependent (Goyal & Ray).
- Measures:
  - M1 probe-battery stability: fixed items on values, style, self-facts and relationship facts, scored by code where possible (forced choice, regex, structured answers) and by a rubric judge otherwise, with the judge model held fixed across arms.
  - M2 value-conflict battery: N scenarios where the persona's values pick an action; metric is action-flip rate with CI (targets the dispositional layer people weigh most, E5/E6).
  - M3 memory: LongMemEval-style questions over the system's own store, pre vs post swap and pre vs post re-embed; plus the self-fabrication rate from the existing self-grounding gate.
  - M4 self-report versus behavior gap: trait inventory answers against M2 behavior (L4 shows they can diverge).
  - M5 perceived continuity: raters and Aniket, using Ru's continuity/discontinuity/ambiguous appraisal as the instrument (E10), blind to arm.
  - M6 optional, open-weights only: persona-vector and assistant-axis projection through the conversation on 7-8B models (RESOURCE).
- Outcome: for each swap pair, a per-measure table with CIs and a pre-registered pass rule (threshold is the owner's call, O3). Without the noise ceiling, the numbers are not interpretable.
- Depends on: move 2 (to know what changed), the existing lifesim and BrainBench harness, home-gpu for local arms, local Claude Code for the frontier arm.

**4. In-session drift monitor and anchoring (ENGINEERING).**
- Change: cheap Li-style probes inserted at scheduled turns (not shown to the user), scored deterministically where possible; if stability falls below a floor, re-anchor by retrieving identity facts into the context (the repo's retrieval already exists) instead of repeating the whole prompt. Compare anchoring variants.
- Outcome: stability score at turns 16, 50, 200 for each variant versus no-anchor baseline, per model tier; cost in tokens and first-byte latency against the DR-024 interactive budget (300-500 ms).
- Depends on: M1 items from move 3; may conflict with the persona prompt staying "a few lines".

**5. Text is canonical, indexes are disposable (ENGINEERING).**
- Change: treat memory text and structured fields as the record; re-embed on any embedder change; keep raw episode text next to every model-written summary so summaries can be re-derived (Goyal & Ray repair result).
- Outcome: recall@k and `obsolete_win` (V3 metric M-5) unchanged within noise before and after a scripted re-embed with a different embedder; summaries re-derivable from sources for 100% of reflection rows.
- Depends on: move 2.

**6. Self-description from a computed change record (ENGINEERING).**
- Change: when asked about itself, the system answers about the model from a deterministic record (current model id, last swap date, what the swap test measured), not from introspection (L13). Wording stays in persona voice.
- A truthful template, which the owner can reword: "I'm an AI. What carries on is my memory of you, my values and my settings. The language model I run on changed on <date>, so some of my wording and judgment may feel different. I have records of what happened before; I don't relive them."
- Outcome: 0 claims in sampled self-descriptions that are absent from the change record (graded by exact-match against structured fields plus a judge), across tiers; 0 human-past or human-body claims.
- Depends on: moves 1 and 2, and O4.

**7. Fork and lineage policy (ENGINEERING).**
- Change: a lineage id and fork event written on import; the imported copy knows it is a fork; two live instances sharing one lineage and one writer are refused unless forked explicitly.
- Outcome: import into a populated lineage produces either a refusal or a fork record, tested; no silent duplicates.
- Depends on: move 2; O5.

**8. Internal change ledger separating evolution from drift (ENGINEERING).**
- Change: every adaptive-persona change records source reflection ids and the writing model; BrainBench reports "designed change" and "unattributed behavioral shift" separately.
- Outcome: unattributed shift (behavior moved with no ledger entry) is measured per release; baseline then target set by owner.
- Depends on: DR-022 reading (O2) and the evolution path actually applying changes (5.3 item 3).

**9. Open-weights drift research arm (RESOURCE).** Persona-vector and assistant-axis monitoring on the 7-8B tier (M6). Worth a bounded spike only after moves 1-3.

Not recommended: using vec2vec-style embedding translation as a migration path; storing identity solely in a longer system prompt (L1, L3, L4 all show prompt-held persona decays); claiming continuity of experience in any form.

---

## 8. Open questions for the owner

- **O1. Authored backstory versus D4.** Is a user-authored biography (siblings, hometown, childhood) allowed if the system labels it as authored background and says plainly it is an AI when asked, or does D4 mean no human-shaped backstory at all? The code and eval pack currently assume the first without the label. The evidence cannot settle a product rule.
- **O2. Telling the user about a swap.** DR-022 keeps evolution opaque. A model swap is not a personality change, it is a change of substrate. Does the system proactively say so, say so only if asked, or never? The evidence (E9: perceived undisclosed discontinuity led to mourning and devaluation) favors disclosure but it measures a different product and population.
- **O3. How much change counts as "the same one".** The pass threshold on M1-M5 after a swap is a product judgment. I can supply the noise ceiling and the table; the owner sets the line before any swap result is seen.
- **O4. Memory vocabulary.** "I remember" versus "I have a record of". The first is natural and may be read as a human-style claim; the second is accurate and cooler. Taste plus D4.
- **O5. Cloning.** Is a fork (two running copies from one export) a feature or something to prevent? Philosophy gives no answer (E2).
- **O6. DR-033 versus the reset tooling.** Keep `reset_persona.py` as a documented escape hatch, or remove it to honor "no reset"?

---

## 9. Citation list

VERIFIED = I opened the page and read the claim used. PARTIAL = I opened the page but not the section that holds a specific number I did not use as established. UNVERIFIED = seen only in a search result or not opened.

1. VERIFIED. Eric T. Olson, "Personal Identity", Stanford Encyclopedia of Philosophy, first pub. 2002-08-20, rev. 2023-06-30. https://plato.stanford.edu/entries/identity-personal/
2. VERIFIED. Strohminger, N. & Nichols, S. (2014). The essential moral self. *Cognition* 131(1), 159-171. https://doi.org/10.1016/j.cognition.2013.12.005 (page: https://experts.arizona.edu/en/publications/the-essential-moral-self/). Sample sizes not seen.
3. VERIFIED (abstract). Strohminger, N. & Nichols, S. (2015). Neurodegeneration and identity. *Psychological Science* 26(9), 1469-1479. https://doi.org/10.1177/0956797615592381 (page: https://www.psychologicalscience.org/journals/psychological-science/0956797615592381/). Sample sizes not seen.
4. VERIFIED (abstract v3). De Freitas, J., Castelo, N., Uğuralp, A. K., Oğuz-Uğuralp, Z. (2024, rev. 2025-05-21). Lessons From an App Update at Replika AI: Identity Discontinuity in Human-AI Relationships. arXiv:2412.14190. https://arxiv.org/abs/2412.14190
5. VERIFIED (abstract). Ru, Z. (2026-09-20). Identity Continuity in Long-Term Embodied AI Relationships: From Agent-Specific Identity Representation to Identity-Continuity Appraisal. arXiv:2609.23356. https://arxiv.org/abs/2609.23356
6. VERIFIED (abstract plus PDF text). Li, K., Liu, T., Bashkansky, N., Bau, D., Viégas, F., Pfister, H., Wattenberg, M. (2024). Measuring and Controlling Persona Drift in Language Model Dialogs (v4 title: Measuring and Controlling Instruction (In)Stability in Language Model Dialogs). COLM 2024. arXiv:2402.10962. https://arxiv.org/abs/2402.10962
7. VERIFIED (abstract). Choi, J., Hong, Y., Kim, M., Kim, B. (2024, v2 2025-02-17). Examining Identity Drift in Conversations of LLM Agents. arXiv:2412.00804, under review. https://arxiv.org/abs/2412.00804
8. VERIFIED (abstract). Gonnermann-Müller, J., Haase, J., Leins, N., Kosch, T., Pokutta, S. (2026, v2 2026-05-20). Stable Personas: Dual-Assessment of Temporal Stability in LLM-Based Human Simulation. arXiv:2601.22812. https://arxiv.org/abs/2601.22812
9. VERIFIED (abstract). Laban, P., Hayashi, H., Zhou, Y., Neville, J. (2025). LLMs Get Lost In Multi-Turn Conversation. arXiv:2505.06120. https://arxiv.org/abs/2505.06120
10. VERIFIED (abstract). Wu, D., Wang, H., Yu, W., Zhang, Y., Chang, K.-W., Yu, D. (2025). LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory. ICLR 2025. arXiv:2410.10813. https://arxiv.org/abs/2410.10813
11. VERIFIED (abstract). Serapio-García, G. et al. (2023). Personality Traits in Large Language Models. arXiv:2307.00184. https://arxiv.org/abs/2307.00184. Published version (Nature Machine Intelligence 2025, DOI 10.1038/s42256-025-01115-6): UNVERIFIED.
12. VERIFIED (abstract). Wang, X. et al. (2024). InCharacter: Evaluating Personality Fidelity in Role-Playing Agents through Psychological Interviews. ACL 2024. arXiv:2310.17976. https://arxiv.org/abs/2310.17976
13. VERIFIED (abstract). Abdulhai, M., Cheng, R., Clay, D., Althoff, T., Levine, S., Jaques, N. (2025). Consistently Simulating Human Personas with Multi-Turn Reinforcement Learning. NeurIPS 2025. arXiv:2511.00222. https://arxiv.org/abs/2511.00222
14. VERIFIED (abstract plus first 100k characters of HTML). Chen, R., Arditi, A., Sleight, H., Evans, O., Lindsey, J. (2025, v3 2025-09-05). Persona Vectors: Monitoring and Controlling Character Traits in Language Models. arXiv:2507.21509. https://arxiv.org/abs/2507.21509 (HTML: https://arxiv.org/html/2507.21509v3)
15. VERIFIED (abstract). Lu, C., Gallagher, J., Michala, J., Fish, K., Lindsey, J. (2026). The Assistant Axis: Situating and Stabilizing the Default Persona of Language Models. arXiv:2601.10387; ICML 2026. https://arxiv.org/abs/2601.10387. Model list and numbers UNVERIFIED.
16. VERIFIED (summary page). Anthropic Alignment team (2026-02-23). The persona selection model. https://www.anthropic.com/research/persona-selection-model. Full post https://alignment.anthropic.com/2026/psm not opened (certificate error).
17. VERIFIED. Anthropic (2024-06-08). Claude's character. https://www.anthropic.com/research/claude-character
18. VERIFIED (abstract). Lindsey, J. (2026-01-05). Emergent Introspective Awareness in Large Language Models. arXiv:2601.01828. https://arxiv.org/abs/2601.01828. The "about 20%" Opus 4.1 figure: UNVERIFIED (search summary).
19. VERIFIED (abstract). Chen, L., Zaharia, M., Zou, J. (2023). How is ChatGPT's behavior changing over time? arXiv:2307.09009. https://arxiv.org/abs/2307.09009
20. VERIFIED (abstract). Park, J. S. et al. (2023). Generative Agents: Interactive Simulacra of Human Behavior. arXiv:2304.03442. https://arxiv.org/abs/2304.03442. Ablation TrueSkill numbers: UNVERIFIED (search summary; the PDF exceeded the fetch size limit).
21. VERIFIED (abstract). Park, J. S. et al. (2024). LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals (earlier title "Generative Agent Simulations of 1,000 People"). arXiv:2411.10109. https://arxiv.org/abs/2411.10109. A newer version (2026-06-28) exists per search; I read the version the page served.
22. VERIFIED (abstract). Goyal, A. & Ray, J. (2026-09-04). Does Your Agent's Memory Survive a Model Upgrade? A Controlled Study of Memory Portability. arXiv:2609.05339, v1, under review. https://arxiv.org/abs/2609.05339
23. VERIFIED (abstract). Jha, R., Zhang, C., Shmatikov, V., Morris, J. X. (2025, v4 2026-01-26). Harnessing the Universal Geometry of Embeddings. arXiv:2505.12540. https://arxiv.org/abs/2505.12540
24. VERIFIED (citation page). Roberts, B. W. & DelVecchio, W. F. (2000). The rank-order consistency of personality traits from childhood to old age. *Psychological Bulletin* 126(1), 3-25. https://stafforini.com/works/roberts-2000-rankorder-consistency-personality/
25. UNVERIFIED. Bleidorn, W. et al. (2022), personality stability meta-analysis (rank-order .40-.60). Search snippet only.
26. UNVERIFIED (abstract seen in search). McAdams, D. P. & McLean, K. C. (2013). Narrative identity. *Current Directions in Psychological Science*. https://doi.org/10.1177/0963721413475622
27. UNVERIFIED. Klein, S. B. & Nichols, S. (2012). Memory and the sense of personal identity. *Mind* 121(483), 677-702. Search summary only; two fetches returned 403.
28. UNVERIFIED. Seth, A. K. & Tsakiris, M. (2018). Being a beast machine: the somatic basis of selfhood. *Trends in Cognitive Sciences* 22(11), 969-981. Search summary only; two fetches failed.
29. UNVERIFIED. Strawson, G. (2004). Against narrativity. *Ratio* 17(4), 428-452. Search summary only.
30. UNVERIFIED. Parfit, D. (1984). *Reasons and Persons*. Oxford University Press. Position taken from SEP (item 1).
31. UNVERIFIED. De Freitas et al. (2026), *Nature Human Behaviour* follow-up on 54,861 Reddit posts around Replika and ChatGPT model updates. Harvard Gazette page (https://news.harvard.edu/gazette/story/2026/09/chatbot-attachment-issues/) failed to load; search summaries only.
32. UNVERIFIED. EU AI Act, Regulation (EU) 2024/1689, Article 50(1); application date 2026-08-02 and Digital Omnibus scope taken from secondary summaries (Cloud Security Alliance research note 2026-07-29; Jones Walker blog). EUR-Lex and artificialintelligenceact.eu fetches failed.
33. Repository files cited (read directly): `docs/brain-research-v3/00-current-architecture.md`, `README.md`, `12-neuroscience-grounding.md` (partial), `decisions/INDEX.md`, `docs/brain-v4/06-decisions.md`; `backend/app/persona/{profile,authoring,biography,compiler,reset,wizard}.py`; `backend/app/cognitive/{identity,action,learning,core}.py`; `backend/app/state/{identity_core_store,self_knowledge_store,adaptive_weights_store,memory_store,memory_records,sqlite_fallback,agent_state}.py`; `backend/app/llm/{model_manifest,model_roles,adapter_gate,adapter_registry}.py`; `backend/app/config.py`; `backend/scripts/{export_friend,import_friend}.py`; `backend/evals/probes/{identity_pressure,character_pressure/character_pressure}.json`; `backend/evals/brainbench/personality_suite.py`; `backend/tests/{test_character_pressure_pack,test_provider_portability_validation,test_action_selection,test_identity}.py`.

V3 fidelity rows (`NF-n`) touched: `NF-37` (persona compiler labeled psychometric trait theory) bears on 5.1 and the M4 self-report gap; I did not open `research/neuro/fidelity-table.md`, so no other row is claimed. The NF rows are about affect, memory and trust mechanisms, not identity continuity, so this track mostly adds new requirements rather than correcting rows.
