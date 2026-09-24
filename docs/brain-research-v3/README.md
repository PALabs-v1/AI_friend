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
| [findings.md](findings.md) | Pre-existing problem ledger (Objective 9) | ongoing |
| [codex-log.md](codex-log.md) | One line per Codex task: verdict, disagreement, resolution | done (C0, C1) |
| `interview/round-N.md` | Interview briefs | round 1 pending |
| `decisions/DR-NNN-*.md` | Recorded architecture decisions from Aniket | none yet |
| [baseline/manifest.json](baseline/manifest.json), [baseline/RESULTS.md](baseline/RESULTS.md) | Brain V2 Local Baseline: env facts, headline numbers, both machines | done |
| `results/{mac,home-gpu}/` | Raw JSON/logs backing the baseline (Phase 3); GPU experiments, scale tests, lifesim runs land here in later phases | Phase 3 done |

## Process

Codex CLI runs as an independent second engineer throughout (see root `CLAUDE.md`'s fan-out rules and the plan's Codex protocol). For every audit, Codex runs cold — without my conclusions — and I commit my own audit before reading its output, so the independence is provable from commit timestamps, not just asserted.

## Status (2026-09-24)

Phase 0 (setup), Phase 1 (reconstruction + dual audit), Phase 2 (10-round architecture interview, 36 decisions, `decisions/INDEX.md`), and Phase 3 (Brain V2 Local Baseline, both machines, [baseline/RESULTS.md](baseline/RESULTS.md)) are complete. Two decisions carry the most weight for everything downstream: DR-002 (an autonomous humanoid mind, not a companion/assistant mode) and DR-020 (personality evolution is entirely self-authored, no external approval). The baseline quantifies the open problems for the first time instead of just asserting them: production hybrid retrieval worst-case hit@3 is 0.699 (vs. 0.023 pre-V2) but `obsolete_win` is 0.61-0.67 (M-5, W1's target is ≤0.10), and user-valence-to-mood correlation is literally undefined under production's `agent_mood` appraisal input because mood never leaves 0.00 regardless of script — including `hostile` (A-1, W2's target). Both machines agree on every result (Rust: 179/179 on both; barge-in mutation kills: 33/38 on both, same 5 equivalents). Next: Phase 4 (real infrastructure + GPU experiments on home-gpu), then Phase 5-7 (lifesim, BrainBench, workstreams W1-W11).
