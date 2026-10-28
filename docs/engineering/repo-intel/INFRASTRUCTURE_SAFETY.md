# Did the repo-intel work damage AI Friend?

Written for the maintainer who must decide whether the tooling can be merged. Directive section 16 asks for evidence on nine points. Each row says what was run and what it showed.

| Requirement | Evidence | Result |
|---|---|---|
| No product code changed | `git status --short backend frontend website scripts` and `git diff --stat HEAD -- . ':!tools' ':!docs'` print nothing. Every changed or new path is under `tools/repo_intel/` or `docs/engineering/repo-intel/`. | Holds |
| Existing tests still work | Backend suite at the audited revision, run with the repo's own venv on Python 3.13: **3,189 passed, 10 skipped, 2 failed** in 180 s (`backend/tests`, three modules excluded, see below). | Two failures, both pre-existing |
| Pre-existing versus new failures | The two failures are `tests/test_memory_insert.py::TestPgPlaceholderGeneration` (two tests). Their cause is commit `4d42db5a` (V4 slice 14) adding two placeholders to the Postgres insert. The backend tree was untouched by this work, so they are not regressions from it. Recorded as claim #43 with a runtime check that reproduces them. | Pre-existing; a real defect on `main` |
| Test modules that could not run | `test_brainbench_stats.py`, `test_memory_security.py`, `test_playback_lifecycle.py` fail at import: the `hypothesis` package is not installed in this venv. They were excluded, not fixed, and no dependency was installed. | Not verified here |
| Rust and GPU tests | Not run. The committed logs (`docs/brain-research-v3/results/`: `cargo test` 179 tests ok on both machines) were read, not reproduced. | Not verified here |
| New tests for the tooling | `python -m pytest tools/repo_intel/tests`: 80 passed (inventory, scope, batch, structure, knowledge, benchmark gate, report generators). | Holds |
| Lint and format | `ruff check tools/repo_intel` clean and `ruff format` applied with the repo's `ruff.toml`. The pre-commit pin is ruff 0.16.1; the locally installed version may differ, and CI is the authority. | Holds locally |
| No database schema changed | The tool writes only to `.repo-intel/index.db` (gitignored). It adds tables (`symbols`, `edges`, `claims`, `claim_evidence`, `claim_history`, `reviews`) to that private file with `CREATE TABLE IF NOT EXISTS`; the index schema version is unchanged. `backend/db/schema.sql` is untouched. | Holds |
| No runtime service behaviour changed | No file in `backend/app`, `backend/crates`, compose files or CI workflows changed. Runtime claim checks are opt-in (`--runtime`), allowlisted (`python`, `pytest`, `cargo`, `grep`, `ls`, `wc`, `ruff`, and read-only `git` subcommands), take an argument list and never use a shell. | Holds |
| No dependency changes | No `requirements*.txt`, `pyproject.toml`, `Cargo.toml` or lock file changed. The tool is standard library only (`ast`, `sqlite3`, `tomllib`, `hashlib`). | Holds |
| No model assets or datasets touched | Nothing under `backend/models`, `backend/voice_samples` or any `outcomes.jsonl`. | Holds |
| Roadmap work not overwritten | The Wave A branches, stashes and worktrees listed in `ROADMAP_CHECKPOINT.md` section 4 were not modified. All work was done on `main` in the main checkout, as the owner override in `CLAUDE.md` allows. | Holds |

## Limits of this verification

- The backend run is on Python 3.13; the repo documents 3.12 as the supported version (`app/vision/adapters.py` uses PEP 695). The suite ran, but a 3.12 run is the proper baseline.
- 10 skipped tests are not named in this run's summary.
- Nothing was started: no NATS server, Postgres, Qdrant, Neo4j, LiveKit or GPU. Integration and live-service behaviour is unverified.
