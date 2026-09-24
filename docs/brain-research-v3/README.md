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
| `baseline/` | Brain V2 Local Baseline manifest + results (Phase 3) | not started |
| `results/` | Raw JSON from GPU experiments, scale tests, lifesim runs | not started |

## Process

Codex CLI runs as an independent second engineer throughout (see root `CLAUDE.md`'s fan-out rules and the plan's Codex protocol). For every audit, Codex runs cold — without my conclusions — and I commit my own audit before reading its output, so the independence is provable from commit timestamps, not just asserted.

## Status (2026-09-24)

Phase 0 (setup) and Phase 1 (reconstruction + dual audit) complete. Next: explain Brain V2 to Aniket and open interview Round 1 (`03-open-questions.md`). See `04-local-infrastructure.md` for the environment and `02-audit-comparison.md` for the audit comparison, including one resolved disagreement with Codex over the voice completion signal (F-002).
