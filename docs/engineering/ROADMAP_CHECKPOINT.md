# Roadmap checkpoint (Phase 0, Preserve)

Written 2026-10-28 by Claude (orchestrator) before the repository-intelligence work in `/Users/aniketsaha/Projects/PALabs/prompt.md` begins. Purpose: let anyone find and safely return to the interrupted Brain V4 build. Nothing was deleted, stashed, reset, merged or rewritten to produce this file. Every fact below was read from git, GitHub or the V4 records on the date above; section 10 lists what was not checked.

## 1. Repository state

| Item | Value | Source |
|---|---|---|
| Checkout | `/Users/aniketsaha/Projects/PALabs/AI_friend` | `git worktree list` |
| Branch | `main` | `git branch --show-current` |
| HEAD | `bd903d22feaa2701c56315b8ac717524abd708f9` | `git rev-parse HEAD` |
| Against `origin/main` | 0 ahead, 0 behind (after `git fetch`) | `git rev-list --left-right --count` |
| Working tree | clean (0 staged, 0 unstaged, 0 untracked) | `git status --porcelain -uall` |
| Tracked files | 1,287 | `git ls-files \| wc -l` |
| Remote | `origin` = `https://github.com/PALabs-v1/AI_friend.git` | `git remote -v` |
| Submodules | none | `git submodule status` (empty) |
| Stashes | 2, both from older fix branches, listed in section 4 | `git stash list` |

Recent commits on `main`, newest first:

```
bd903d22 test(memory): verify embedding provenance against a real Postgres with pgvector (V4 slice 14)
2a5fe338 docs(brain-v4): Wave A built, review findings and open risks
4d42db5a feat(memory): embedding model id and dimension on every vector row (V4 slice 14)
e4d46172 feat(state): ledger registry, offline copies and intervention operators (V4 slice 1)
6f036aec refactor(cognitive): stop injecting dead plan, simulation and adapter-gate objects (V4 slice 6)
966d5514 feat(llm): local Claude Code LLMClient (V4 slice 0)
105042ec docs(brain-v4): record D-041, Phase 04 approved, Wave A starts
```

## 2. Active implementation version

Brain V4 design-and-build cycle. Records live in `docs/brain-v4/`: `PROJECT_STATE.md` (where we are), `DECISION_LOG.md` (D-001 to D-041), `RISK_REGISTER.md` (R-01 to R-15), `phases/04-slice-plan.md` (the approved slices). The cycle is at phase 04, status `in-progress`, building Wave A.

Brain V3 is frozen by D-010 and lives on branch `brain-v3` (PR #218 open at `c58ee1ae`). V3 documents are in `docs/brain-research-v3/`.

## 3. Completed work

| Milestone | Evidence |
|---|---|
| Phases 00 to 03 (orientation, research from 15 reports, architecture draft, two critic rounds) | `PROJECT_STATE.md` progress log; `phases/02-architecture-draft.md` |
| Phase 04 slice plan, approved by Aniket (D-041) | `phases/04-slice-plan.md` |
| Slice 0, local Claude Code `LLMClient` | `966d5514` |
| Slice 6, dead wiring dropped | `6f036aec` |
| Slice 1, ledger registry, offline copies, intervention operators | `e4d46172` |
| Slice 14, embedding provenance per row | `4d42db5a` |
| Slice 14 Postgres path verified on a throwaway pgvector container (`backend/scripts/verify_pg_provenance.py`, 7 checks) | `bd903d22` |

## 4. Work in progress and preserved state

### 4.1 Unmerged or uncommitted work that must not be touched

Ten worktrees carry uncommitted changes. All belong to the frozen V3 line or are scratch. None was changed by this checkpoint.

| Worktree (under `~/.claude-worktrees/AI_friend-2527627756/` unless noted) | Branch | Dirty files | What it is |
|---|---|---|---|
| `codex-w1a`, `codex-w1b` | `codex/brain-v3-w1a`, `-w1b` | 24, 18 | V3 W1 builds, uncommitted |
| `codex-w2` | `codex/brain-v3-w2` | 41 | V3 W2 build, uncommitted |
| `codex-w3a`, `codex-w3b` | `codex/brain-v3-w3a`, `-w3b` | 20, 29 | V3 W3 trust-model tournament, two builds; copies also in `AI_friend-research-data/codex-drafts/w3-builds-2026-10-28/`. Blind critic round 1 was lost, 0 of 2 rounds used |
| `codex-w5`, `codex-w5b` | `codex/brain-v3-w5`, `-w5b` | 10, 1 | V3 W5 barge-in state machine builds |
| `codex-lsv` | `codex/brain-v3-lsv` | 12 | lifesim expressed-valence label (DR-039) |
| `ff53b3e9` | `Aniket-a14/brain-runtime-audit-ff53b3e9` | 1 (`.venv`) | runtime audit; remote branch is gone |
| `AI_friend/.claude/worktrees/agent-ac06f9551a5b0bcd9` | `claude/lifesim-text-b` | 1 (`backend/.venv`) | lifesim text builds |
| `10f9894b` (this session's harness worktree) | `Aniket-a14/brain-v4-design-10f9894b` | 0 | stale at `0dea39ee`; use the main checkout for real work |
| scratchpad `waveA/s00`, `s06`, `s14` | `wave-a/s00`, `s06`, `s14` | 6, 5, 8 | throwaway Codex builds of Wave A, already reviewed and landed on `main`; safe to remove on Aniket's word |

The scratchpad is `/private/tmp/claude-501/-Users-aniketsaha-Projects-PALabs/10f9894b-8bc3-4774-874f-49cf17be526c/scratchpad/`. It is a temp directory and can disappear.

### 4.2 Local-only branches (not on the remote)

`cleanup/antigravity`, `cleanup/claude`, `cleanup/codex`, `integration/cleanup` (dead-code removal attempts), many `codex/brain-v3-*` branches, `worktree-agent-ac06f9551a5b0bcd9`. One remote ref, `codex-local/phase1`, is outside the normal set. Remote branches that exist on `origin`: `main`, `brain-v3`, `Aniket-a14/brain-v4-design-10f9894b`.

### 4.3 Stashes (shared across all worktrees)

- `stash@{0}` on `fix/p2-4-merge-in-turn-calls`: a reverted P2-4 attempt (6 files, 601 insertions, 244 deletions).
- `stash@{1}` on `fix/p1-stage2-ack-and-retention`: P1-1 in progress (5 files, 84 insertions, 2 deletions).

### 4.4 Background jobs at checkpoint time

- Antigravity cold critic, round 1 of 2, on the four Wave A commits. It finished after this checkpoint was first written: `docs/brain-v4/research/waveA-critic/round-1.md` exists (untracked, 15.8 KB) and its first line is `VERDICT: FAIL`. The table lists HIGH findings F-01 to F-04 at least (slice 0 subprocess not killed on cancellation; slice 14 hybrid search path skips the provenance check; slice 1 interventions accept a bare live `AgentState`; slice 1 multi-row `agent_state` catch-22). **None is verified by me yet.** Per the standing cap, the next step is to check each finding against the code, fix with tests, and run round 2 at most.

## 5. Known failing checks (found while writing this checkpoint)

CI on `main` was green at `105042ec`, the commit before Wave A. It is red on all three Wave A commits that ran CI. Cause traced from the run logs:

| # | Failure | Where | Cause | Introduced by |
|---|---|---|---|---|
| F1 | `tests/test_memory_insert.py::TestPgPlaceholderGeneration::test_timed_placeholders_sequential_and_aligned` and `::test_untimed_uses_current_timestamp_literal` fail (`assert nums == list(range(1, 26))` and `range(1, 24)`) | job `backend-test` (CI), Cognitive Regression Gate, macOS Compatibility CI | The insert now carries two more columns, so the expected placeholder count is stale. `verify_pg_provenance.py` ran the real SQL on Postgres and passed, so the SQL is not the suspect. Stale expectation is the likeliest reading and is **not yet confirmed**. | slice 14, `4d42db5a` |
| F2 | `radon cc app --min D` is non-empty: `SQLiteConnection._create_schema - D (24)` at `backend/app/state/sqlite_fallback.py:63` | job `code-quality` (CI) | The slice 14 column additions pushed the function over the D threshold. The workflow treats D/E/F as blocking. | slice 14, `4d42db5a` |

These were not fixed here. The roadmap is paused and Aniket has not said to fix them. Fix is small and known (update the two test expectations; extract helpers from `_create_schema`), so it should be the first item when the build resumes.

## 6. Remaining planned milestones

From `phases/04-slice-plan.md` (16 slices, 2 split into 2a and 2b):

| Wave | Slices | State |
|---|---|---|
| A | 0, 1, 6, 14 | built and pushed; cold critic round 1 returned FAIL (section 4.4), unverified; CI red (section 5); 12 unbound ledger rows remain from slice 1 |
| B | 2a (baseline harness), 13 (ModelRole routing) | not started; 2a depends on 0 and 1, 13 depends on 1 |
| C | 2b, 4, 5, 7, 9, 10 | not started; 4, 5, 9 are judgment slices (two-variant tournament plus cold critic) |
| D | 8, 11, 12, 15, 3 | not started; 11 is a judgment slice; 3 needs the GPU |

Dependencies are in the slice-plan overview table (section 2 of that file).

## 7. Existing architectural problems on record

The Brain V4 comparison and the V3 re-check name these. They are claims from earlier audits, not re-verified in this phase (see section 10).

- The output filter blocks the phrase "as an AI", so the repo does not meet the D4 decision (`05-phase01-comparison.md`).
- `TemporalMemoryStore` has no writer and no reader in the live path (`research/phase03-claude/round-2.md`).
- Mood self-feed (NF-12) and the integrity update (NF-23) are known defects, scheduled as slices 4 and 5.
- 12 ledger rows have no handle yet (slice 1 follow-up).
- The V3 re-check found 12 stale and 9 wrong statements in V3 documents; corrections are held until Aniket releases the freeze (`research/v3-recheck.md`).

## 8. Open questions and decisions owed

For Aniket (from `PROJECT_STATE.md` and the session record):

1. `dist/` removal needs the installers moved to release assets first.
2. Removing the old untracked `~/Projects/PALabs/brain-v4/` archive.
3. A LICENSE for the new org repos; making them public.
4. GPU hand-back before slices 2b and 3 (D-036).
5. Provider-terms check before training on frontier outputs (R-14).
6. Fixing NF-12 and NF-23 before state-trace logs are used for training.
7. Whether to apply the V3 document corrections.
8. Whether to fix F1 and F2 now or at resume.

## 9. How to resume safely

1. Read `docs/brain-v4/PROJECT_STATE.md`, then `docs/brain-v4/prompts/RESUME_PROMPT.md`.
2. `cd /Users/aniketsaha/Projects/PALabs/AI_friend` (the main checkout). Do not work in `~/.claude-worktrees/AI_friend-2527627756/10f9894b`; it is stale.
3. `git fetch origin && git status` and compare with section 1. If HEAD differs, trust git over this file.
4. Fix F1 and F2 first, with the matching tests, so `main` is green.
5. Read the critic report (`docs/brain-v4/research/waveA-critic/round-1.md`, FAIL). Verify each finding against the code, fix with tests, run round 2 at most. No round 3.
6. Continue with slice 2a and slice 13.
7. Standing rules still apply: V3 frozen; no sudo; no secrets; LLM calls only through local Ollama or local Claude Code; GPU only with Aniket's go-ahead; never `--no-verify`; never force-push; before pushing `git fetch origin`, `git rebase origin/main`, `git push origin HEAD:main`.
8. Remove the throwaway Docker container `pg_slice14_test` (port 55433) when Wave A is clean.

## 10. Sources and confidence limits

Read directly for this file: `git` (status, log, branch, worktree, stash, ls-files, rev-list), `gh run list` and `gh run view --log-failed` for CI, `docs/brain-v4/PROJECT_STATE.md`, `docs/brain-v4/prompts/RESUME_PROMPT.md`, the heading structure of `phases/04-slice-plan.md`, and the process list.

Not checked, and so only as reliable as the source it came from:

- The content of the ten dirty worktrees was counted, not read. Their status as "V3 builds" comes from `PROJECT_STATE.md` and branch names.
- Section 7 repeats earlier audit findings without re-reading the code. Phases 1 to 5 of the repo-intelligence project exist to re-verify them.
- F1's cause (stale expectation) is inferred from the CI assertion text and the passing real-Postgres run. I did not open the test file.
- Whether `.agents/CONTEXT.md` (17,202 lines, "the engineering ledger") agrees with the V4 records was not checked.
- GitHub state was read once; the Wave A runs on `4d42db5a` and `bd903d22` had a job still in progress when the log was pulled.
- No test suite was run for this checkpoint.
