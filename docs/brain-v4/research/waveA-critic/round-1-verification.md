# Wave A critic round 1: verification against the code

Written for the maintainer deciding whether to run critic round 2. Round 1 (`round-1.md`) returned FAIL with 12 findings and was recorded as unverified. This file checks each against `main` at `85f09350`. Each verdict is a claim in the repo-intel ledger (`python -m tools.repo_intel claims show N`) with a deterministic check; only claims marked verified have had an independent reader.

| Finding | Critic severity | Verdict | Claim | Evidence |
|---|---|---|---|---|
| F-01 subprocess leak on cancellation | HIGH | Holds | #16 | `claude_code_client.py` has no `CancelledError` and no `finally:` |
| F-02 hybrid search never runs the provenance check | HIGH | Overstated | #41 | scope assert is called from one site, but the row-level `_check_row_embedding` is called five times on other retrieval paths (independent reader disagreed with the original claim) |
| F-03 interventions mutate live state on a bare AgentState | HIGH | Holds, narrower | #44 | `interventions.py` returns no marker for a bare AgentState by documented design (unit tests); a risk only if a live object is passed |
| F-04 agent_state query narrowing catch-22 | HIGH | **Holds, fixed** | #57 | reproduced on a copy with two `agent_state` rows: unscoped read raised `narrow where`, and `where={"agent_name": ...}` raised `not in the key`. Fixed with `SqliteBinding.optional_key_columns`; the product runs one agent, so the live impact is low, the harness impact was total for multi-row copies |
| F-05 mismatch swallowed in archive lookup | MED | Holds | #45 | broad `except Exception` logs and returns `[]`; `last_search_error` not set on that path |
| F-06 no migration backfill or idempotency test | MED | Partly wrong | #46 | columns are added idempotently; no backfill UPDATE exists, but NULL rows count as the default model by design and a test covers it |
| F-07 slice 6 omitted touches, non-failing baseline test | MED | Half right | #55 | `git diff --name-only 6f036aec^ 6f036aec` has no `decision.py`, although the slice card lists `decision.py:884-888`; behaviour-neutral. The "non-failing baseline" is a characterization test and is meant to pass before and after, so that part is not a defect |
| F-08 audit trail destroyed on copy deletion | MED | Contradicted | #47 | `audit_path = dest_parent / AUDIT_FILENAME`: the audit file lives outside the copy root |
| F-09 scope cache key omits model and dimension | LOW | Holds | #48 | `key = (table, wing, room)` |
| F-10 pipe deadlock on large prompts | LOW | Plausible, not reproduced | #49 | write plus drain precede any stdout read |
| F-11 hard-coded 768 in mock embeddings | LOW | Holds | #50 | two `np.random.randn(768)` sites |
| F-12 explicit base_path bypasses offline check | LOW | Documented design | #51 | the comment in `runtime_paths.py` states the harness route is intentionally allowed |

Result: 2 of 12 contradicted or documented-by-design (F-08, F-12), 3 overstated or partly wrong (F-02, F-06, F-07), 7 hold at the stated or lower severity (F-04 reproduced and fixed, #57), 0 unsettled. Round 2 of the critic cap (max 2) is still available.
Also fixed on `main` while doing this: the two failing Postgres placeholder tests (`2569ae0a`).
