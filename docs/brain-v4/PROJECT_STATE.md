# PROJECT STATE: Brain V4 design cycle

**The single source of truth for "where are we."** An agent reads this first and updates it last. The status block below is owned by the agent that is working; a human edits it only to unblock.

```yaml
cycle: brain-v4-design
current_phase: 01          # see "Phases" below; advanced only when the previous phase's exit criteria are met
status: in-progress        # not-started | in-progress | blocked | awaiting-decision | complete
blocked_on: null                   # Phase 01 research running; next step is the written comparison
branch: main                       # direct pushes, D-016
base: origin/main @ b0d54e0d       # PR #217 merged
last_updated: 2026-10-28
last_agent: claude
```

## What this cycle is

Aniket, 2026-10-28: shift the focus from long training runs to working out the brain architecture itself. Not a copy of a human mind, and not a claim of consciousness. The question is how close a closed loop can get: a system whose internal state persists, was not scripted, and causally drives what it says and does (`00-origin.md`, section 3). Fiction is a source of design ideas only, never evidence (see `RISK_REGISTER.md` R-07).

The target and its rules were set earlier and still hold: D-001 to D-008 (`06-decisions.md`).

## Where the other work stands

| Item | State | Where | To resume |
|---|---|---|---|
| Brain V3 | Frozen by D-010. PR #217 merged. PR #218 open at `c58ee1ae`, 26/26 CI checks pass. | branch `brain-v3` | `docs/brain-research-v3/README.md` and `work/COVERAGE.md` |
| W3 trust model (tournament) | Two builds finished, uncommitted. Blind critic round 1 was lost with the scratch folder; 0 of 2 critic rounds used. | worktrees `codex-w3a`, `codex-w3b`; copies in `AI_friend-research-data/codex-drafts/w3-builds-2026-10-28/` | `DECISION_LOG.md` D-010 |
| W1 `llm_augmented` memory run | Not run. Pilot: 551 s per 209-turn cell with one worker (2.6 s per turn); the full 3m run is about 10 hours. | needs home-gpu | `DECISION_LOG.md` D-010 |
| home-gpu | Reachable, rebooted, storage changed (`/data` 59 GB, was 247 GB), Ollama not running. Shared with the Glioma project. Untouched by this cycle. | `ssh home-gpu` | D-013 |

## Phases

Provisional. The plan is a decision (D-015); Phase 00 ends when it is approved.

| Phase | Name | Exit criterion |
|---|---|---|
| 00 | Re-orient and set up records | Docs tracked in the repo; D-009 to D-012 recorded; D-013 to D-015 answered; Phase 01 scope approved |
| 01 | Architecture research | Independent research reports (Codex, Antigravity, Claude) compared in writing, as `05-comparison.md` did for V4 |
| 02 | Architecture draft | One design document: the loop, each part's job, what it reads and writes, how each is measured, what stays open |
| 03 | Cold critique | Critic rounds on the draft (max 2, per the standing cap); findings acted on |
| 04 | Slice plan | The smallest buildable slices, each with a measurable outcome; Aniket approves before any code |

## Progress log

Append one line per agent run. Newest at the bottom.

| Date | Agent | Phase | What happened |
|---|---|---|---|
| 2026-10-28 | claude | 00 | Re-oriented after a gap of about three weeks. Read the Glioma project's record system (PROJECT_STATE, DECISION_LOG, phase files, registries). Aniket decided: architecture-first cycle (D-009), V3 frozen not deleted (D-010), docs tracked in this repo (D-011), helpers Codex, Antigravity and Claude sub-agents, with Codex also allowed to write code (D-012). Created the branch from `origin/main`, copied `~/Projects/PALabs/brain-v4/` here (26 files, raw `*.log` excluded), added this file, `DECISION_LOG.md`, `RISK_REGISTER.md` and a resume prompt. Saved read-only copies of both W3 builds outside the repo. Then resolved D-013 to D-015 (home-gpu: ask each time; Vision Quest: portable identity and continuity after a transfer; framing: reactive, deliberative and reflective layers with identity across them; all four research briefs) and drafted the briefs in `research/briefs/`, not yet sent. Aniket then ordered direct work on `main` with no PRs (D-016); `CLAUDE.md` updated. Checked: home-gpu reachable but changed. Looked for Vision Quest episodes 5 and 6: no recap found, so nothing from them is used yet. |
| 2026-10-28 | claude | 01 | Pushed straight to `main` (`0dea39ee`; #219 shows merged). Aniket approved the four briefs and chose `gemini-3.8-flash-high` (D-015, D-017). Launched Phase 01: 4 Codex runs (`gpt-6-luna`, Aniket's choice) and 4 Antigravity runs, detached, plus 4 Claude sub-agent passes, all from the same briefs, none seeing the others' output. Shared-instructions symlinks added and checked with Codex and Antigravity (D-017). Fast-forwarded the local checkout. Codex needs an explicit `-m`; its default `gpt-6.1-sol` is rejected. Next: collect the 12 reports into `research/phase01-*/`, then write `05-phase01-comparison.md`. |
