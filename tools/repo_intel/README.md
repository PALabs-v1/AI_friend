# repo-intel

Deterministic repository inventory, coverage accounting and read tracking. Standard library only (Python 3.11+, SQLite). No network, no model calls, nothing from the repository is executed.

Run from the repository root: `.venv/bin/python -m tools.repo_intel <command>`. The index lives in `.repo-intel/index.db` (git-ignored). Override with `--db` or `$REPO_INTEL_DB`.

## What it guarantees

- **Every file is accounted for.** File lists come from `git ls-files` (tracked) and `git ls-files --others --exclude-standard` (untracked, not ignored), NUL-separated, so odd names are safe. Each file gets a sha256, size, line count, category, scope verdict and, if out of scope, a written reason.
- **Ignored paths are counted, never opened.** `.env`, local databases and virtualenvs show up as names and sizes only.
- **Stages are separate and hash-bound.** Each stage stores the sha256 it was done against. Edit a file and every stage for it is stale at once, with no rewriting.
- **READ is earned.** `claim` hands a named reader a batch and records exactly what was handed out. `ack` accepts the reader's notes only if (1) the file still has the same hash, and (2) the note quotes literal strings that occur in the delivered text. A reader that did not look cannot ack.
- **REVIEWED is stricter.** A structured note (role, a summary of at least 40 characters) whose listed symbols all occur in the file. A hallucinated symbol leaves the file READ but not REVIEWED.
- **VERIFIED needs an independent second reader** of a different tool family. Not implemented yet; the count stays at 0 and is printed that way.
- **Scans are atomic and read-only.** One scan is one transaction. `GIT_OPTIONAL_LOCKS=0` keeps `git status` from touching `.git/index`.
- **Repository text is untrusted.** Batches are wrapped in markers with a notice not to follow instructions found inside.

Reading every file is not the same as understanding it. The coverage report prints that caveat on every run.

## Commands

| Command | What it does | Exit codes |
|---|---|---|
| `inventory` | Scan the working tree and update the index. Prints added, removed, modified, scope-flipped files. | 0 ok, 1 unreadable files |
| `reconcile` | Same scan, changes nothing. | 0 matches, 2 drift |
| `coverage [--json] [--md PATH] [--strict]` | The progress report. `--strict` also checks drift. | 0 ok, 1 unread or failed files, 2 drift |
| `claim --reader N --family F [--prefix P] [--out FILE]` | Reserve the next batch of unread files and print it, or write it to FILE and print a manifest. | 0 |
| `ack BATCH --reader N --notes FILE.jsonl` | Validate notes, record reads. Rejected files return to the pool. | 0 all accepted, 1 some rejected |
| `release-expired` | Expire claims older than the TTL (default 120 min). | 0 |
| `files {unread,stale,unreviewed,failed,excluded,all}` | List files by state. | 0 |
| `show PATH` | One file's row and all notes. | 0 |
| `resume` | Compact grounding for a new session: coverage line, drift, next action. | 0 |

The progress line in the owner's format (the console prints a real em dash where this shows a hyphen):

```
Codebase Coverage: 245 / 1,850 files read (13.24%) - 1,605 remaining.
```

## Note format for `ack`

One JSON object per line:

```json
{"path": "backend/app/x.py", "role": "what it is for", "summary": "at least 40 characters on what it does and who uses it",
 "symbols": ["ClassName", "function_name"], "evidence": ["literal text from the file", "another literal string"],
 "depends_on": ["backend/app/y.py"], "uncertainties": "what you could not tell from this file alone"}
```

`evidence` strings must be at least 5 characters and appear verbatim in the delivered text (two for files over 300 bytes, one under, none for an empty file).

## Scope

`scope.toml` holds ordered rules, first match wins. Binary files, symlinks, submodules, lockfiles, generated result data and vendored skill-pack payload are excluded from the read count N but still hashed and listed with a reason in the report. Change a rule, run `inventory`, and files whose scope flips are reported.

## Resuming after a context loss or a new session

1. `python -m tools.repo_intel resume`
2. If it reports drift: `inventory`.
3. `claim`, read the batch, `ack`, repeat.

The index is the memory. Nothing else needs to survive a session.

## Tests

`.venv/bin/python -m pytest tools/repo_intel/tests -p no:cacheprovider -p no:benchmark -q`. The suite builds throwaway git repositories with awkward names, symlinks, binaries and ignored directories. Eleven deliberate breakages of the tool were each caught by at least one test.
