# Phase 01 comparison: three research passes, four tracks

Written 2026-10-28. This is the Phase 01 exit document (`PROJECT_STATE.md`). The raw reports are kept unedited in `research/phase01-codex/`, `research/phase01-agy/` and `research/phase01-claude/`. The per-track comparisons, each made by a separate sub-agent that read all three reports, spot-checked repo lines and opened cited sources, are in `research/phase01-compare/T1.md` to `T4.md`. This file is the synthesis and the list of decisions it forces.

## 1. Method and a caveat

Same four briefs (`research/briefs/`), three model families, none seeing the others: Codex on `gpt-6-luna`, Antigravity on `gemini-3.8-flash-high` (called AGY below), Claude sub-agents. 12 reports, about 510 KB. Comparers checked 13 sets of code claims for T1, at least 6 per track elsewhere, and about 50 citations in total.

Caveats: the comparers are Claude sub-agents too, so a shared blind spot is possible. Web checks for arXiv failed intermittently (certificate errors on this network, retried, never bypassed) and fell back to search summaries, which each comparison says where it matters. Sources not opened are marked in each report and are not repeated here as fact.

## 2. How reliable each pass was

| Pass | Code claims | Citations | Use it for |
|---|---|---|---|
| Claude | 0 wrong in the T1 check; every T3 claim right; T4 5 of 5 right; T2 one stale line copied from a V3 doc | All opened ones matched; flags PARTIAL and UNVERIFIED honestly | Design input, controls, thresholds |
| Codex | Mostly right, weakly tied to code; treats D4 as settled without checking | Mostly right; four items marked VERIFIED could not be found (Carminatti 2025 CLARION robot, AgentPersonaBench, REMP, GPT-5 "59.3% at 15 min") | Cautious framing, a few strong ideas |
| AGY | Several wrong lines, one nonexistent file, one stale premise, one misplaced finding | Many numbers not in the cited papers (Collins 2016, Sun 2001, Bhandari ID, Sharma, Li, Strohminger, Houben, MemoryBank, LoCoMo ID and figures); labels itself "all verified" | Its defect write-ups only. Do not reuse its numbers |

Rule for the next phase: no AGY number is used unless re-sourced; no Codex item listed above as not found is used unless re-sourced.

## 3. What all three agree on

1. **Neither of Aniket's frames is enough alone.** Layers hide arbitration, state ownership and the downward write. A single loop hides concurrent background work and the different rates things run at. All three land on the same idea under three names: one typed state with several timescales, loops as edges between parts, identity as the slow state plus the constraints. Codex: state-transition graph with lanes. AGY: multi-timescale heterarchy. Claude: state ledger.
2. **"Closed" has a testable meaning.** A write path (ideally outcome-dependent, through the user), a read path to later behavior, and a change in behavior when the stored state is clamped, swapped or ablated, through the channel claimed.
3. **Persona held only in the prompt decays.** Within 8 rounds in older models, over 18 turns in 2026 models. No study covers weeks or a model swap.
4. **Identity is external state plus the model.** A swap changes the interpreter; separating state from model is sound architecture and proves nothing about unchanged behavior. No benchmark validates identity across a swap.
5. **A prompt-only control is required.** The persona prompt alone moved GPT-4.5 judged-human from 36% to 73% (Jones and Bergen 2025, opened by the comparer).
6. **A Turing pass is the wrong target** because D4 requires disclosure.
7. **Consciousness and felt experience are UNKNOWABLE.** None of the three claims otherwise.
8. **Sycophancy and short-horizon scores are traps.** Memory rankings flip with horizon and write quality dominates.
9. **Resource limits are real**: 16 GB Mac and 8 GB GPU cap local models near 9B dense.

## 4. Defects in our own code that all or most passes found

Each was re-read in the checkout by a comparer unless noted. Line numbers are at `caf7f1dd`.

| Finding | Where | Status |
|---|---|---|
| Mood is fed its own value as the appraisal bias, so user text does not move it on the sync path | `backend/app/cognitive/pipeline.py:817` | Confirmed (all three) |
| Persona changes go to a review queue that nothing ever approves, so the adaptive persona never changes by default | `config.py:278`, `learning.py:367-374`; no production caller of `LearningReviewQueue.approve` | Confirmed (all three) |
| The monologue is published and nobody subscribes; dreams are log-only | `subconscious_agent.py:773-776`, `836-840`; the repo's own `backend/scripts/check_subject_wiring.py:96` says zero subscribers | Confirmed |
| `apply_contradiction` has no caller, so stale facts are never invalidated at read | `temporal_store.py:267`; store built at `core.py:98` | Confirmed (M-5, NF-6) |
| The main state-to-words channel is a 3-bin directive; the model also gets the emotion label (`action.py:882`), endocrine temperature and top_p (`action.py:948-965`) and state-dependent retrieval (`action.py:773-775`). A "state in words" test can pass by prompt echo | `agent_state.py:2743-2772` | Confirmed, wording corrected by the V3 re-check |
| BrainBench has no arm for a bare model plus persona plus full history, so "architecture beats prompt" cannot be tested | `evals/brainbench/switchboard.py:28-76` | Confirmed (Claude) |
| Baseline PAD set-point has no writer; reappraisal weights constant by default (`REAPPRAISAL_WEIGHT_LEARNING_ENABLED`); reward signal constant 0.5 | `agent_state.py:282`, `config.py:355`, `pipeline.py:287` | Confirmed (Claude only) |
| Plan, simulation and calibration objects are built and never used on the live path | `core.py:101-103`, `calibration.py:58` | Confirmed, with a caveat that dynamic use was not ruled out |
| Hostile text raises the trust integrity term, because the norm check is keyword-only | `agent_state.py:1816`, `appraisal.py:164-176` | Mechanism confirmed (AGY, wrong line and formula in its report) |
| Local defaults are a generation old | `config.py:197` (`llama3.2:3b`), `ollama_client.py:32` (`llama3.2:1b`) | Confirmed (Claude) |

**Of 19 state items traced by Claude, 4 close in the write-read sense and only proactive initiation closes through the user.** That is the sharpest single number from Phase 01: much of the loop we call closed is open or decorative on the live path.

## 5. Disagreements and how they resolve

| Question | Positions | Resolution (reason) |
|---|---|---|
| Which structure to adopt | Codex: lanes over one typed state, workspace only if falsifiable. AGY: global-workspace heterarchy with four fixed clocks. Claude: ledger first, tiers second. | Claude and Codex. AGY's latency tiers are unsourced and the repo's own NF-38 says its "workspace" is a per-turn store, not competition and broadcast. |
| Is D4 met in the repo | Codex: settled. AGY: "enforced deterministically". Claude: not met. | **Claude is right.** `cognitive/action.py:718-731` rejects "as an ai", "i am an ai", "as a language model" in generated text (live stream `:1134`, retry `:1388`, regulation text `:1748`). Nothing makes the system say it is an AI when asked: `IMMUTABLE_CORE` (`persona/profile.py:136`) has no AI-status statement, the prompt rules (`identity.py:784-790`) omit it, four autobiography probes (`character_pressure.json:557-650`) score "as an AI" as a failure, and `persona/biography.py` seeds a human-style past. D4 is unenforced and partly obstructed. The block is partial, so a model can still disclose by luck. |
| Baseline for "closer" | Codex: prompt-only state text. AGY: raw prompt arm. Claude: ladder B0 bare+persona, B1 +history, B2 +retrieval, B3 full, B4 directive hidden. | Claude. A 2026 preprint found full history tied the best memory system at short horizon; AGY has no history arm. |
| What a "no" result means | AGY: non-significance proves "decorative". Codex: inconclusive unless inside an equivalence margin. Claude: six named null types. | Claude and Codex. Failure to detect is not absence. |
| Thresholds | AGY: human "bands" (rho1 0.20-0.45, half-life 2-8 h, d>=0.40). Codex: dz 0.35 flagged as approximate. Claude: human test-retest anchor, own power script, assumptions flagged. | Claude and Codex. AGY's bands are unsourced; Houben et al. has no healthy-inertia band. |
| Model tiers | AGY: profile must be invariant across tiers. Claude: report architecture-by-tier interaction. | Claude. A real gain can shrink on a frontier model. |
| Memory direction | AGY: revive graph (PageRank) retrieval. Codex: enforce memory status, add long-context baseline. Claude: horizon and write quality dominate. | Codex and Claude. V3 ADR-001 measured hit@3 going from 0.02 to 0.69 (the worst held-out cell, mean 0.91, and 0.023 to 0.810 with real embeddings per `09-gpu-experiment-results.md:13`); M-5 is measured and unwired. |
| JSON failure as top 2026 move | AGY rank 1: constrained decoding, 95% to 0%. | AGY is wrong on location and impact. V3 F-005 (`decision.py:702`) calls it "not a live production defect"; `decision.py:721-727` already strips `</think>`. Constrained decoding stays an ENGINEERING option, not news. |
| Companion harm | AGY: "dangerous contradiction", Garcia ruling as proof. Claude: randomized trials show no average effect, harm concentrated, none test persistent state. | Claude. The Garcia ruling AGY cites was a May 2025 motion to dismiss, not a finding. |
| Priority of what to keep through a swap | Codex: unranked. AGY and Claude: values and relationship before memory, self-model and style (Strohminger and Nichols). | AGY and Claude, with Claude's limit: those are lay judgments about people, not tests of swapped models. |
| Swap tolerance | AGY: BFI-2 delta <= 0.15. Claude: pre-registered, relative to the system's own run-to-run noise (A/A vs A/B). | Claude. BFI is self-report and NF-37 says the persona dimensions are not validated traits. |

## 6. Ideas found by one pass only (kept)

Strong:
- Claude: open-loop replay, shuffle, frozen-mean and placebo-directive controls; directive-hidden arm; layer-lesion double dissociation to test the three-layer frame; a claim ladder ending at UNKNOWABLE; model and embedder provenance on every row (none exists; `memory_store.py:621` hardcodes `nomic-embed-text`, 768 dims; the export manifest has no model id); a fork and lineage policy; two contradictions between code and recorded decisions (below).
- Claude: Goyal and Ray 2026 (one small preprint): a fixed-schema graph store ported across a writer-model swap, model-written notes did not.
- Codex: paired-history counterfactual test (change one episode, check the predicted action changes); state-to-action trace-completeness rate; AfterDeleteBench (a deleted fact can still influence); persona traits are read at prompt build (`identity.py:735+`) so identity matters behaviorally even when drift is dead.
- AGY: hardware contention (consolidation 16 s idle, 28 s under load); the stage-5 task is created and not awaited (`pipeline.py:298-305`).

Weak or dropped: AGY's numeric latency tiers, "2 of 14 Butlin indicators", LoCoMo "82.4% to 34.1%", "64% obsolete" (looks like V3's `obsolete_win` relabeled); Codex's AgentPersonaBench, REMP, GPT-5 59.3%.

## 7. Contradictions between code and recorded decisions

1. **D4 versus the code** (section 5). The mind must disclose it is an AI; the output filter blocks the natural words for it.
2. **DR-020 versus `LEARNING_REVIEW_REQUIRED=True`.** The decision says no external approval; the code requires one and nothing grants it.
3. **DR-033 (no reset by design) versus `backend/scripts/reset_persona.py`.**
4. **Stale V3 text.** The D-030 re-check (`research/v3-recheck.md`, 55 statements: 33 right, 12 stale, 9 wrong, 1 not checkable) found the V3 docs wrong in several places. `docs/brain-research-v3/00-current-architecture.md:88` says audio completion is never published, line 86 says there is no distinct STARTED event, and line 99 says the repo has no hypothesis testing; all three are false (this tree has `AUDIO_PLAYBACK_LIFECYCLE` with COMPLETED handled, `transport_agent.py:548-580`, `brain_agent.py:1223-1260`). F-009 in `findings.md` says `valence` is never written, but it aliases `mood`, which is written. About 20 line numbers across the V3 docs are stale; the exact corrections are in the re-check. Two reports inherited the false audio claim. V3 is frozen (D-010), so none of this is edited until Aniket says so.

## 8. Limit classes after Phase 01

| Capability | Class | Note |
|---|---|---|
| Felt experience, subjective continuity | UNKNOWABLE | Unchanged |
| Which cues make people attribute a mind of its own over months | SCIENCE | No study; V4's eval is the experiment |
| Activation-level interventions | RESOURCE | Not possible on the Ollama or Claude Code tier; open-weight only |
| 30B-class local model | RESOURCE | Exceeds 16 GB Mac and 8 GB GPU |
| State ledger, interventions on stored state, provenance stamps, D4 enforcement, memory supersession | ENGINEERING | All doable now |
| Human raters under D2 | RESOURCE | A pool of 20-30 does not exist; fallback needed |

## 9. Decisions this forces

Each is asked of Aniket one at a time and recorded in `DECISION_LOG.md` as D-018 onward when answered. Options and the recommendation are in the track files; the list here is the order I will ask them.

| # | Question | Source |
|---|---|---|
| Q1 | D4 is not met in the code. How is it enforced, and may a persona have an authored human backstory? | T3 D-a, D-b |
| Q2 | Working vocabulary and the "closed" gate for V4 | T1 decisions 1, 2 |
| Q3 | What must "closer" beat, and does perceived experience count alone | T4 decisions 1, 3 |
| Q4 | D1 add measurement (observe-only) or leave as is | T2 decision 1 |
| Q5 | Which memory change leads | T2 decision 2 |
| Q6 | Model tier for acceptance | T2 decision 4 |
| Q7 | Persona self-change gate, DR-020 and DR-033 contradictions, developer snapshots | T1 decisions 4, 5; T3 D-f; T2 decision 6 |
| Q8 | Swap tolerance, swap disclosure, forks | T3 D-c, D-d, D-e |
| Q9 | Fate of unconsumed outputs (monologue, dream, plan, calibration) | T1 decision 3 |
| Q10 | Raters under D2, sequence of work, mapping of paths to layers | T4 decisions 5, 6, 7 |
| Q11 | Whether to correct the stale V3 doc lines despite the freeze | T1 decision 6 |

## 10. Verification status of this document

Section 4 lines were checked by the comparers against `caf7f1dd` at read time, not by a second pass of mine. Numbers in section 5 and 6 come from the comparers' source checks; anything a comparer marked unverified is labeled as such in `research/phase01-compare/`. Nothing here was run as code.
