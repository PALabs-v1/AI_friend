# Brain V3 Research Cycle

Start commit: `001069c5181dbb1bbcb8a5ee848f7c7eebb1e5c2` (`origin/main`, PR #216 "Brain V2" merge, 2026-09-24). Working branch: `brain-v3` (renamed from the originally planned `research/brain-v3` — collided with the pre-existing `origin/research` branch at the git ref-path level).

This directory is the working record for the next research/engineering cycle, per `master_prompt.md`'s brief: reconstruct Brain V2 → explain it → interview Aniket on the decisions that depend on product intent → establish a local baseline → validate against real infrastructure → build a synthetic longitudinal life simulator → benchmark the whole brain → research and implement the open problems → adversarially review → check for regressions → simplify.

## Index

| Doc | Contents | Status |
|---|---|---|
| [00-current-architecture.md](00-current-architecture.md) | Architecture-first map of the brain as merged: agents, event topics, stores, pipeline stages, path classification | done |
| [01-v2-delta.md](01-v2-delta.md) | Per-change V1 → problem → V2 → algorithm → trade-off → code location → unresolved | done |
| [02-audit-comparison.md](02-audit-comparison.md) | My independent audit vs. Codex C1's independent cold audit | done |
| [03-open-questions.md](03-open-questions.md) | Every decision that needs Aniket, grouped into interview rounds | done |
| [04-local-infrastructure.md](04-local-infrastructure.md) | Machines, services, environments, baseline test runs, F-001 | done |
| [05-research-plan.md](05-research-plan.md) | Workstreams W1-W10: hypothesis, metric, threshold, ablation | done |
| [06-benchmark-plan.md](06-benchmark-plan.md) | Lifesim + BrainBench design, seeds, statistics, gates | done |
| [07-gpu-experiment-audit.md](07-gpu-experiment-audit.md) | Audit of `experiments/gpu/` before running it for real; Codex C2 comparison | done |
| [08-live-infra-validation.md](08-live-infra-validation.md) | Live Postgres+Qdrant+Neo4j cross-store consistency validation (Phase 4b) | done |
| [09-gpu-experiment-results.md](09-gpu-experiment-results.md) | Real-embedding (8 models) and real-LLM ToM results, H-R1/H-R2/H-R3/ToM rules applied | done |
| [findings.md](findings.md) | Pre-existing problem ledger (Objective 9) | ongoing, F-001..F-006 |
| [codex-log.md](codex-log.md) | One line per Codex task: verdict, disagreement, resolution | done (C0, C1, C2) |
| `interview/round-1..10.md` | Interview briefs | done, all 10 rounds |
| `decisions/DR-001..036-*.md` | Recorded architecture decisions from Aniket | done, all 36 |
| [baseline/manifest.json](baseline/manifest.json), [baseline/RESULTS.md](baseline/RESULTS.md) | Brain V2 Local Baseline: env facts, headline numbers, both machines | done |
| `results/{mac,home-gpu}/` | Raw JSON/logs backing the baseline (Phase 3) and the GPU experiments (Phase 4a); scale tests and lifesim runs land here in later phases | Phase 3-4 done |

## Process

Codex CLI runs as an independent second engineer throughout (see root `CLAUDE.md`'s fan-out rules and the plan's Codex protocol). For every audit, Codex runs cold — without my conclusions — and I commit my own audit before reading its output, so the independence is provable from commit timestamps, not just asserted.

## Status (2026-09-24)

Phases 0-4 are complete: setup, reconstruction + dual audit, the 10-round/36-decision interview, the Brain V2 Local Baseline (both machines), and real infrastructure + GPU validation. Two decisions carry the most weight for everything downstream: DR-002 (an autonomous humanoid mind, not a companion/assistant mode) and DR-020 (personality evolution is entirely self-authored, no external approval).

Every open problem now has real, measured evidence behind it, not just an assertion:

- **M-5** (stale facts win retrieval): `obsolete_win` 0.61-1.00 on production's ranker, confirmed on both the synthetic benchmark (Phase 3) and all 8 real embedding models tested (Phase 4a). W1's target is ≤0.10.
- **A-1** (user words never reach affect): mood stays at exactly 0.00 under production's real appraisal input across every scripted conversation including `hostile` (Phase 3); confirmed the update mechanism itself is sound once fed a real signal, but none of the 3 candidate local LLMs (llama3.2:3b, qwen2.5:3b, qwen3:4b) are accurate enough to be that signal — the ToM decision rule fails decisively for all three (Phase 4a). W2 needs a better estimator, not just a wire-up.
- **Live infrastructure**: Postgres+Qdrant+Neo4j cross-store consistency verified directly against the real stack (Phase 4b) — write parity, dedup, entity metadata, and archive promotion all pass.
- **New**: H-R3 (nomic task prefixes) gets a real, well-evidenced "no" — prefixing measurably hurts retrieval on the `summary` regime. Two new findings surfaced from the ToM experiment: F-005 (production's own fast model, llama3.2:3b, fails to parse this classification call 95% of the time — severity unconfirmed for production, flagged for priority follow-up) and F-006 (qwen3:4b's thinking-mode output is 100% unparseable by the current JSON extractor, low priority).

Both machines agree on every deterministic result (Rust: 179/179 on both; barge-in mutation kills: 33/38 on both, same 5 equivalents; memory retrieval numbers byte-identical across arm64/x86_64).

Next: Phase 5 (lifesim) and Phase 6 (BrainBench), then Phase 7 (workstreams W1-W11, informed by everything measured in Phases 3-4).
