# 02. How V4 builds on V3

V3 lives in the `AI_friend` repo. Until PR #217 merges, its docs are on
branch `brain-v3`, not `main`. Paths below are relative to the repo root.
`tools/check_v3_paths.py` checks they still exist.

V3 status as of 2026-09-26, 23:00 IST:
- **Merged**: W4 (playback lifecycle), W9 (proactive initiation), W10a
  (security part 1), P8's scale runner.
- **Done**: the W12 neuroscience fidelity audit (39 findings, `NF-1`..`NF-39`).
- **Wave-A benchmark `v3waveA-B`**: finished on home-gpu, 240/240 cells, 0
  errors, at commit `e3fcb9a`. The comparison against `v2base-B` is the next
  V3 step.
- **Open, in order**: W5, W10b; then wave B (W1, W2, W3, SW; W8 after W1);
  then wave C (W6, W7, W11); P9 alongside; P10 last.
- The order is recorded in `docs/brain-research-v3/work/COVERAGE.md`.

## What V3 gives V4, capability by capability

Letters match `01-what-is-possible.md`.

| V4 capability | V3 foundation (owner) | V3 status | What V4 adds on top |
|---|---|---|---|
| A. Short-form humanlikeness | Model choice is per deployment; `backend/app/llm/__init__.py` is the boundary | exists | A measured model-tier comparison on the realism eval, so model effects are separated from architecture effects |
| B. Coherence over months | W1 temporal truth (M-5, `NF-6`), W7 consolidation and forgetting (DR-007, DR-034), W8 freshness and abstention (M-10, C1-1) | open (wave B/C) | Long-horizon perceived-coherence probes; nothing new to build if V3 lands |
| C. Feeling heard | W1 (accurate memory of what was said), W8 (what matters now) | open | Rater items for "felt heard" in the realism eval |
| D. Theory of mind | `NF-26` (point estimates, no uncertainty) owned by W8 | open | Uncertainty and provenance surfaced in conversation ("I might be wrong, but...") |
| E. Affect moved by the user | W2 estimator bake-off and three-layer affect (A-1, `NF-12`, `NF-16`, `NF-17`; DR-008..DR-010) | open, highest risk | Nothing replaces it. If W2 is BLOCKED, V4 inherits the block |
| F. Functional drives | Fragments only: `NF-18`, `NF-21`, `NF-33` (W2), `NF-34` (W8), owned as label or dead-code fixes | audited; no V3 workstream builds a drive layer | A regulated drive layer (energy/rest, social need, competence, curiosity). New V4 workstream |
| G. Inner life with consequences | W9 (importance-weighted initiation, merged), DR-019 self-directed thought, the subconscious (`backend/app/agents/subconscious_agent.py`) | partly merged | Traceability: inner activity must change memory, mood or later behavior, and be measured |
| H. Stable-but-changing identity | W11 self-authored evolution and baseline drift (DR-011, DR-020, DR-021, DR-023, DR-033, DR-038); the personality suite (`backend/evals/brainbench/personality_suite.py`) | open (wave C) | A human-band identity probe (rank-order consistency, directional drift) |
| I. Learning in weights | Memory-based learning (reflection, consolidation) | exists | Not a V4 foundation item; later experiment |
| J. Human turn timing | W4 lifecycle (merged), W5 barge-in (next), DR-024 budgets, DR-027..DR-030 | partly merged | Turn-gap distribution measured against Stivers et al. 2009; full-duplex feasibility |
| K. Expressive voice | `NF-36` (prosody labels), `backend/app/cognitive/expression.py` (valence/arousal to affect label and breath) | audited | Voice carries actual affect and drives, checked by listeners |
| L. Shared visual perception | `backend/app/vision/agent.py` | exists; no V3 workstream or `NF-n` row covers vision | Visual observations become memories under W1's truth maintenance |
| M. Emotion from faces | none | n/a | Weak, context-conditioned evidence only; never moves trust or mood alone |
| N. Consciousness indicators | `NF-38` (global workspace is a per-turn object), owned by W12 | audited | An indicator-property audit as a report, never a claim |
| O. Emulation | n/a | not pursued | Not pursued |
| P. Partnership, shared goals | W9 goal and commitment tracking (merged), `backend/app/vision/agent.py` for shared perception | partly: goals exist, shared ownership does not | Goals owned by both, progress it notices, setbacks it feels, initiative to help (V4-12, `07-partner-architecture.md`) |

## V3 rules that carry into V4 unchanged

- **Neuroscience proposes, BrainBench decides**
  (`docs/brain-research-v3/12-neuroscience-grounding.md`). V4 extends the
  judge: BrainBench plus the perceived-realism eval.
- **Every correction ships behind a flag**, with a pinned unit test and a
  comparison arm (the `NEURO_<NAME>_ENABLED` pattern).
- **DR-032: affect never overrides safety.** A mind built to be perceived as
  real raises the stakes of this rule; it does not relax it.
- **DR-002 is already the V4 goal.** It reads: "an autonomous humanoid mind
  modeled on a real human mind, minus a soul"
  (`docs/brain-research-v3/decisions/INDEX.md`). V4 does not change the
  product identity. It adds a way to measure whether the mind lives up to
  it.
- **LLM calls stay local** (Ollama or local Claude Code), with model choice
  per deployment, unless Aniket says otherwise.

## V3 results V4 should read first, when they exist

1. Wave-A comparison (`v3waveA-B` vs `v2base-B`): proactive, barge-in and
   resources deltas. The run is done; the write-up is pending.
2. W2's estimator bake-off table: decides whether E is reachable locally.
3. W12's human-data probes on V2 and wave A (the "before" picture for
   forgetting, spacing, habituation, mood inertia, trust).
4. W11's first personality-suite results: the starting point for H's band.
