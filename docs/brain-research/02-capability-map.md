# 02 — Cognitive capability map

Each capability is listed with its V1 mechanism, verified status, the
benchmark that measures it (if any), and what V2 did. "Benchmark" means an
executable measurement in `backend/evals/cognitive` or the test suite, not a
description. Ordering follows architectural leverage: memory first, because
every other capability reads it.

| Capability | V1 mechanism (file) | Verified status | Measured by | V2 |
|---|---|---|---|---|
| Episodic retrieval | ACT-R + substring cue (`memory_store.py`) | broken: 2–4% hit@3 | `memory` E1–E7 | **replaced** (ADR-001): 69–100% |
| Long-delay recall | recency term | 0.00 hit@3 for facts > 21 days old on SQLite | category `old` | 0.62 (hard) – 1.00 |
| Forgetting trivial info | archive/prune by age since creation | pruning practically unreachable (46–338 days) | trivia share, trap rate | trap-in-top-3 0.31 → 0.02 via ranking; decay unchanged |
| Emotional memories | mood-distance terms | no measurable effect | category `emotional` | retrieved by relevance: 0.72 on hard/summary (V1 0.03) |
| Similar-memory discrimination | cosine (under-weighted) | weak | paraphrase vs same-topic lures | z-scored relevance |
| Contradiction / preference change | stored `contradicts_id`, unused; `TemporalMemoryStore` unwired | broken: obsolete wins 0.64 | `updated`, `obsolete_win`, E7 | **open**: perfect validity is worth obsolete 0.67 → 0.00 (07 §1) |
| Multi-hop association | PageRank over Neo4j | relation leg unreachable (M-4, fixed) | none yet | not in hybrid; experiment designed (07 §2) |
| Affect ← user | appraisal G = own mood | broken: user never moves valence | `affect` sim | mechanism validated, estimator pending (ADR-002) |
| Affect stability | reappraisal weight learning | runaway / numbing | `affect` sim, gate test | **fixed**: learning off |
| Affect decay | tick-driven exponential, 13.9 h | works if ticks arrive (A-5) | `distance_to_baseline_after_idle` | unchanged |
| Trust | +0.1·NA per turn | rises under hostility | `affect` sim `final_trust` | proposed: `PersonModel` (07 §4) |
| Working memory | Redis list, 8 turns, mostly unused | only `session_state` used | — | unchanged |
| Attention / salience | `GlobalControls` urgency etc. | urgency ≥ 0.65 on every user turn; regulation can't win | — | not addressed (07 §7) |
| Reflex / barge-in | STT speculative stop → Stage 2 arbiter | confirmed stop ignored (V-1) | `test_brain_v2_regressions` | **fixed** (ADR-003) |
| Self-correction | mid-stream validation + retry | retry cancels itself (V-2) | — | proposed (07 §5) |
| Proactive behaviour | subconscious thoughts, gates | cooldown can reset (V-4); memory injection path (S-1) | — | S-1 **fixed** |
| Background consolidation | subconscious after 300 s silence | runs; loses summaries on embed outage (M-13) | — | unchanged |
| Metacognition | `CapabilityLimitationModel` | never invoked | — | not addressed |
| Planning / simulation | verified planner, episodic simulator | constructed, never called | — | not addressed |
| Personality stability | three-tier persona, review-gated learning | holds (learning never approved) | persona tests | unchanged |

Capabilities the brief lists that have no mechanism at all yet (perception
uncertainty fusion, prediction, drives) are not listed as rows. The register
in `ARCHITECTURE.md` §2 records them as BUILD/EXPERIMENT; nothing here changes that.
