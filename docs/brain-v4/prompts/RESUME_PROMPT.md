# Resume prompt: Brain V4 design cycle

> **Invoke with:** *"Read `docs/brain-v4/prompts/RESUME_PROMPT.md` and continue working."*
>
> Nothing else is needed. This file finds where the cycle is and does the next piece.

## 1. Find where you are

Read `docs/brain-v4/PROJECT_STATE.md`.

| `status` | What you do |
|---|---|
| `in-progress` | Resume `current_phase`. Read `git log --oneline -10` and continue from the last commit. |
| `awaiting-decision` or `blocked` | Re-read `blocked_on`. If `DECISION_LOG.md` now shows those entries RESOLVED, clear the field and continue. If not, present each open decision with its alternatives and recommendation, ask Aniket one question at a time, and stop. |
| `not-started` | Begin `current_phase`. |
| `complete` | Report; do not start new work without Aniket. |

Cross-check against reality (`git log --oneline -10`, `git worktree list`, `git status`). If the repository and the state file disagree, trust the repository and say so.

## 2. Read, in this order

1. `docs/brain-v4/README.md`, then `00-origin.md` (why this exists) and `06-decisions.md` (D-001 to D-008).
2. `docs/brain-v4/DECISION_LOG.md` (what is decided, what is open).
3. `docs/brain-v4/RISK_REGISTER.md`.
4. The phase's own files, once they exist under `docs/brain-v4/phases/`.
5. Brain V3, only if the task touches it: `docs/brain-research-v3/README.md` and `work/COVERAGE.md`. V3 is frozen (D-010); do not change it.

## 3. How to work

- **Ask before deciding.** Aniket asked to be consulted on each decision, small or large. Any judgment call that is not already in `DECISION_LOG.md` becomes a new `OPEN` entry with alternatives and a recommendation, then a question. Do not pick one and proceed.
- **Write it down the moment it exists.** Records go into `docs/brain-v4/` straight away, never batched in `/tmp` (risk R-01).
- **Independent research.** Codex and Antigravity (`agy`) each get the same brief and never see my conclusions or each other's reports. Keep their reports unedited. Compare in writing. Launch Codex detached. Briefs carry design text only (risk R-05).
- **Critics never see builder reasoning.** Max 2 critic rounds per unit; after round 2, fix findings with tests and do not run a round 3.
- **Honest claims.** No consciousness claim, ever. "Perceived coherence, shown by a blind test" is the working definition. Fiction gives ideas, not evidence (R-07).
- **Mechanics.** Work directly on `main` and push with `git push origin HEAD:main`, no PR (D-016; rebase on `origin/main` first). Never force-push. Never `--no-verify`. No git writes in another agent's worktree. No `sudo`. Do not touch home-gpu without Aniket's go-ahead (D-013). LLM calls only through local Ollama or local Claude Code.
- **Style.** Direct, short, concrete. File names and line numbers. No em dashes. End with the next action.

## 4. Finish

Append one line to the progress log in `PROJECT_STATE.md`, update `status`, `blocked_on` and `last_updated`, commit, rebase on `origin/main` and push to `main`. Report one of: DONE, DONE_WITH_CONCERNS, BLOCKED, NEEDS_CONTEXT.
