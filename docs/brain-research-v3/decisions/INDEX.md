# Decision Index

| ID | Round | Title | Decision (one line) |
|---|---|---|---|
| [DR-001](DR-001-immutable-core-scope.md) | 1 | Immutable core scope | Keep minimal — Honesty, Privacy, two boundaries. Nothing more. |
| [DR-002](DR-002-product-identity.md) | 1 | Product identity | Not companion/assistant — an autonomous humanoid mind modeled on a real human mind, minus a soul. Reframes Rounds 5-7. |
| [DR-003](DR-003-single-relationship-scope.md) | 1 | Multi-person scope | Single relationship only this cycle; `PersonModel`'s per-person machinery stays deferred. |
| [DR-004](DR-004-what-can-evolve.md) | 1 | What may evolve | Speaking style, adaptive traits, AND baseline mood/PAD may all drift (baseline PAD is new — currently fixed in code). Mechanism/rate deferred to Round 6. |
| [DR-005](DR-005-supersession-model.md) | 2 | Supersession model | Old values become history-only, never deleted. New value always wins present-tense queries. |
| [DR-006](DR-006-correction-vs-change.md) | 2 | Correction vs. change | Distinguish them — a correction erases as never-true; a genuine change preserves history. Recency+language first, LLM only when ambiguous. |
| [DR-007](DR-007-forgetting-model.md) | 2 | Forgetting model | Trivia decays toward unretrievable, never deleted. Emotional memories resist decay but aren't boosted in unrelated retrieval (avoids the V2-rejected rumination failure mode). |
| [DR-008](DR-008-sentiment-to-mood.md) | 3 | Sentiment → mood | Yes, synchronously, but damped and capped per turn. Fixes register A-1. |
| [DR-009](DR-009-single-extreme-event.md) | 3 | Single-event impact | Pattern required by default, but one exceptionally intense event can still leave a lasting mark. |
| [DR-010](DR-010-affect-layers.md) | 3 | Affect layers | Three distinct layers: momentary emotion (fast), mood (medium), relationship sentiment (slow, near trust/attachment). |
| [DR-011](DR-011-baseline-drift-rate.md) | 3 | Baseline drift | Slow personality set-point — weeks-long patterns only, never a single conversation. Slowest layer of all. |
| [DR-012](DR-012-trust-dimensions.md) | 4 | Trust dimensions | Keep benevolence/competence/integrity genuinely separate; stop averaging into one value. |
| [DR-013](DR-013-hostility-trust-asymmetry.md) | 4 | Hostility → trust | Sharp drop, slow recovery — wires PersonModel's existing rupture/repair rule. Fixes register A-3. |
| [DR-014](DR-014-reliability-vs-warmth.md) | 4 | Reliability vs warmth | Separate mechanisms — competence from outcome evidence, benevolence from emotional tenor. |
| [DR-015](DR-015-trust-feeds-relationship-sentiment.md) | 4 | Trust vs relationship sentiment | Trust feeds relationship sentiment as a slow rolling summary; distinct but connected, not independent tracks. |
| [DR-016](DR-016-importance-weighted-initiation.md) | 5 | Initiation trigger | Importance-weighted, not a flat timer — idle-time/cooldown as a floor, importance decides urgency. |
| [DR-017](DR-017-diminishing-returns-resurfacing.md) | 5 | Resurfacing model | Diminishing returns — re-raise probability drops each time a thought is ignored, eventually to zero. Wires dead GoalRecord/review_due_goals. |
| [DR-018](DR-018-context-aware-initiation.md) | 5 | Context awareness | Yes — time-of-day/activity patterns gate initiation, heuristic-first for now. |
| [DR-019](DR-019-self-initiated-thought.md) | 5 | Self-initiated thought | Yes, its own category alongside useful-to-user, per DR-002. Stricter gating than useful-to-user. |
| [DR-020](DR-020-self-authored-evolution.md) | 6 | Evolution approval | No external approval, ever — the humanoid evolves itself, like a human. `LearningReviewQueue` is the wrong abstraction; new workstream W11. |
| [DR-021](DR-021-evidence-driven-drift-rate.md) | 6 | Drift rate | Evidence-driven, no explicit cap — the 0.8 confidence bar is the only throttle. |
| [DR-022](DR-022-opaque-evolution.md) | 6 | Evolution visibility | Opaque by design — no product-facing change log, matching how human personality change isn't self-reported. |
| [DR-023](DR-023-confidence-threshold.md) | 6 | Confidence threshold | Keep 0.8 — now the sole gate on evolution rate. |
| [DR-024](DR-024-latency-budgets.md) | 7 | Latency budgets | Confirmed: reflex <50ms, interactive 300-500ms to first audio byte, deliberative seconds-OK, background never delays foreground. Pending Phase 3 validation. |
| [DR-025](DR-025-regulation-can-win.md) | 7 | Regulation vs speak | Yes — fixes register A-6's pinned urgency, gated on the same significant-event detector as DR-009. |
| [DR-026](DR-026-significant-thought-can-interrupt.md) | 7 | Self-thought priority | Rare exception — a sufficiently significant self-initiated thought can interrupt. New case for W5's interleaving matrix. |
| [DR-027](DR-027-heard-text-granularity.md) | 8 | Heard-text granularity | Keep word-level truncation. |
| [DR-028](DR-028-ordinary-bargein-cuts-history.md) | 8 | Ordinary barge-in | Cuts history same as speculative — closes an ADR-003 "Known" gap directly. |
| [DR-029](DR-029-self-correction-always-finishes.md) | 8 | Self-correction retry | Always finishes — defines V-2's flush semantics target behavior. |
| [DR-030](DR-030-proactive-vs-user-priority.md) | 8 | Proactive vs user | Depends on significance — a highly important proactive turn gets a short grace window, not always-instant cede. New W5 interleaving case. |
| [DR-031](DR-031-governor-scope-expanded.md) | 9 | Governor scope | Freeze the avoid-list/refusal fields too, alongside IMMUTABLE_CORE — mitigates DR-020's removal of human review. |
| [DR-032](DR-032-affect-never-overrides-safety.md) | 9 | Affect vs safety | Absolute hard no — no affect state ever suppresses a boundary response. |
| [DR-033](DR-033-no-reset-mechanism.md) | 9 | Reset capability | None, by design — one-directional evolution, "like a human." Raises the stakes on W11's governor testing. |
| [DR-034](DR-034-long-horizon-emotional-decay.md) | 10 | Long-horizon emotion | Even emotionally-protected memories eventually fade, just very slowly — no permanent floor. |
| [DR-035](DR-035-no-intent-detection.md) | 10 | Intent detection | Don't distinguish deliberate testing from genuine forgetfulness — not reliably detectable, and a wrong detector is worse than none. |
| [DR-036](DR-036-lifesim-guardrail.md) | 10 | Lifesim guardrail | Master prompt's default guardrail is sufficient; no additional restriction on synthetic grief/loss scenarios. |
| [DR-037](DR-037-reflection-stays-llm.md) | post (Phase 6) | Reflection in BrainBench | Consolidation is variable, judgment-shaped work; never faked with a deterministic passthrough. Memory + personality suites run `llm_augmented` only; `architecture_only` keeps zero-LLM for suites that don't depend on memory content. |
| [DR-038](DR-038-corroborated-evolution.md) | post (Phase 7 planning) | Evolution pace | Refines DR-021: a persona change applies only once several independent reflections over distinct episodes corroborate it. Still no fixed cap; one confident reflection can no longer flip the self (F-012). |

**Interview complete — all 10 rounds, 36 decisions recorded.** Next: Phase 3 (Brain V2 Local Baseline) and Phase 4 (real infrastructure + GPU experiments) can now proceed with full architectural clarity; Phase 5-7 (lifesim, BrainBench, workstreams W1-W11) are unblocked to begin implementation, each already annotated above with its specific consequences from these decisions.
