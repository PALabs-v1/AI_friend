VERDICT: FAIL: Wave A contains critical live-path bypasses, subprocess leaks on cancellation, state mutation safety escapes, and tests that cannot fail across all four slices.

# Cold Critic Round 1: Wave A Code Review

## Findings Table

| id | severity | slice | file:line | what breaks and the input that triggers it | exact fix |
|---|---|---|---|---|---|
| F-01 | HIGH | 0 | `backend/app/llm/claude_code_client.py:122-150, 161-210` | Subprocess zombie and orphan leak on cancellation or early generator exit. Trigger: `task.cancel()` during `generate()` or `generate_stream()`, or an early `break` in `async for chunk in generate_stream()`. The `except (TimeoutError, OSError):` block fails to catch `asyncio.CancelledError` or `GeneratorExit`, and there is no `finally:` block terminating the spawned `process`. The child `claude` subprocess remains running in the background indefinitely, consuming CPU, memory, and leaking file descriptors. | Wrap subprocess execution in `try ... finally:` blocks in both `generate()` and `generate_stream()`: `finally: if process is not None and process.returncode is None: process.kill(); await process.wait()`. |
| F-02 | HIGH | 14 | `backend/app/state/memory_store.py:2569, 4182-4190, 4375` | Production hybrid memory search never executes the scope embedding provenance check. Trigger: Calling `search_memories()` with default `MEMORY_RANKING_POLICY="hybrid"`. The code branches to `_search_memories_hybrid`, calling `_fetch_similarity_candidates` and completely bypassing `_fetch_postgres_candidates` (the sole caller of `_assert_scope_embedding_provenance`). Incompatible embedding vectors in PostgreSQL are never rejected before `embedding <=> $1::vector` runs. If vector dimensions differ, PostgreSQL aborts with a database error; if dimensions match but models differ, foreign vector spaces are ranked together, silently degrading search quality. | Call `await self._assert_scope_embedding_provenance(conn, "memories", wing, room)` at the start of `_fetch_similarity_candidates` or in `_search_memories_hybrid` before executing `self._PG_SIMILARITY_SQL`. |
| F-03 | HIGH | 1 | `backend/app/state/interventions.py:86, 194-204` | Interventions bypass offline copy safety and directly mutate live in-memory state on `AgentState` targets without checking for an offline copy marker. Trigger: Passing a bare `AgentState` instance (such as `live_core.state.current_state` or `brain_agent.cognitive_core.state.current_state`) to `clamp()`, `swap()`, `shuffle()`, or `replay()`. Line 77 only inspects `hasattr(target, "current_state")` for `StateService`; for bare `AgentState`, line 86 unconditionally returns `("attr", None, "")` and executes `setattr()` directly on the live agent state without checking for `OFFLINE_COPY.json`. In addition, no audit record is written because `root` is None. | In `_kind_and_root()`, raise `InterventionError` if `target` is not an `OfflineCopy` or an offline `StateService`. Reject bare `AgentState` instances unless explicitly marked as offline test fixtures with a validated marker. |
| F-04 | HIGH | 1 | `backend/app/state/ledger.py:86`, `backend/app/state/interventions.py:118-126, 151-177` | Query narrowing catch-22 on `agent_state` table when multiple rows exist. Trigger: An offline copy where `state_cache.db` has more than one row in `agent_state` (such as multi-agent setups or test fixtures with multiple persona records). `_sqlite_read()` and `_sqlite_write()` raise `InterventionError: N rows matched; narrow where`, but passing `where={"agent_name": "friend"}` is rejected by `_where_clause()` with `InterventionError: where has columns not in the key: ['agent_name']` because `_state()` defined `key_columns=()`. It is impossible to intervene on `agent_state` in any multi-agent database. | Update `_state()` in `ledger.py` to specify `key_columns=("agent_name",)` on `SqliteBinding`, or update `_where_clause()` to permit filtering on valid table columns. |
| F-05 | MED | 14 | `backend/app/state/memory_store.py:3120-3127` | `EmbeddingMismatchError` in archive lookup is silently swallowed without updating search error metrics. Trigger: Hybrid archive search encountering a row with mismatched embedding model or dimension. Line 3122 raises `EmbeddingMismatchError`, but line 3125 catches all exceptions and silently returns `[]`, leaving `self.last_search_error` clean and concealing corrupt or foreign archive rows from the caller and telemetry. | In `_fetch_archive_rows`, re-raise `EmbeddingMismatchError` or assign `self.last_search_error = str(arch_err)` before returning so telemetry and callers observe the provenance failure. |
| F-06 | MED | 14 | `backend/db/schema.sql:86-89` | Missing schema migration backfill and missing backfill idempotency test required by slice 14 card. Trigger: Running migrations on an existing database with legacy rows. Slice 14 card explicitly requires an additive schema change with backfill and an idempotency test. No SQL backfill statement exists in `schema.sql`. While Python coalesces NULL to default values, the database columns remain NULL, preventing pgvector partial index optimization or pure-SQL vector queries from verifying provenance. | Add `UPDATE memories SET embedding_model = 'nomic-embed-text', embedding_dim = 768 WHERE embedding_model IS NULL AND embedding IS NOT NULL;` and a matching statement for `archived_memories` in `schema.sql`, accompanied by an idempotency test. |
| F-07 | MED | 6 | `backend/app/cognitive/decision.py:885-887`, `backend/tests/test_dead_wiring_dropped.py:70-94` | Omitted code touches and non-failing baseline test. Trigger: Reverting slice 6 commit `6f036aec`. Slice 6 card specified touching `decision.py:884-888` and `action_candidate.py:507-512` to drop `PROCEED` default consumers. Commit `6f036aec` omitted these touches. `test_proceed_candidate_scores_match_pre_removal_baseline` tests code that was never modified and passes unconditionally even if slice 6 is reverted. | Remove dead `metacognitive_directive` wiring in `decision.py:885-887` and `action_candidate.py:507-512` as planned, and replace the no-op test with one that verifies the absence of the dead consumer wiring. |
| F-08 | MED | 1 | `backend/app/state/offline_copy.py:127-140`, `backend/app/state/interventions.py:35, 256-259` | Audit trail destruction upon copy deletion. Trigger: Calling `copy.delete()` after performing interventions. `interventions.py` writes operator logs to `root / "interventions.jsonl"`. `OfflineCopy.delete()` executes `shutil.rmtree(self.root)`, deleting the intervention log along with the databases. The audit record of what interventions were actually executed on that copy is permanently destroyed upon cleanup. | Write intervention records to `dest_parent / f"interventions_{self.copy_id}.jsonl"` or append to an external audit log outside `self.root`. |
| F-09 | LOW | 14 | `backend/app/state/memory_store.py:638, 760-785` | Scope provenance cache key omits model and dimension. Trigger: Reconfiguring `store.embedding_model` or `store.embedding_dimension` at runtime on an existing store instance. The scope cache key is `(table, wing, room)`. Subsequent searches see the key in `_embedding_scopes_verified` and skip verification against the new model. | Change cache key to `(table, wing, room, self.embedding_model, self.embedding_dimension)`. |
| F-10 | LOW | 0 | `backend/app/llm/claude_code_client.py:171-174` | Potential pipe deadlock on large prompts in `generate_stream`. Trigger: Supplying a prompt exceeding the OS pipe buffer (64 KB). `process.stdin.write()` and `drain()` run synchronously before any stdout reading starts. If the subprocess fills stdout before finishing stdin reading, both processes deadlock until timeout. | Write to stdin and read stdout concurrently using asynchronous tasks. |
| F-11 | LOW | 14 | `backend/app/state/memory_store.py:1363, 1376` | Hardcoded 768 vector dimension in `get_embedding` mock and `_mock_embedding_vector`. Trigger: Setting `Config.MOCK_LLM_TEXT = True` with `Config.EMBEDDING_DIMENSION != 768`. Generates 768-d vectors that fail dimension validation against the store configured dimension. | Replace 768 with `self.embedding_dimension`. |
| F-12 | LOW | 1 | `backend/app/state/runtime_paths.py:79-90` | `runtime_state_db` bypasses offline copy check when `base_path` is explicitly passed. Trigger: Instantiating a live service with an explicit `base_path` pointing to an offline copy folder. Line 79 sets `from_deployment = not base_path`, skipping the `OFFLINE_MARKER` check. | Validate that `(Path(base) / OFFLINE_MARKER).exists()` raises `OfflineCopyError` even when `base_path` is explicitly provided. |

## Per-Slice Test Evaluation

### Slice 0: Local Claude Code `LLMClient`
1. `test_stream_yields_before_process_exits` (`backend/tests/test_claude_code_client.py:139`):
   Can it fail? YES. The test creates a process with `auto_eof=False` where `process.wait()` blocks on an un-triggered event. If `generate_stream()` buffers tokens and waits for process completion instead of yielding incrementally, `anext()` blocks and times out after 0.2 seconds.
2. `test_generate_timeout_kills_process_and_returns_standard_fallback` (`backend/tests/test_claude_code_client.py:173`):
   Can it fail? YES. If timeout logic is removed or fails to call `_terminate()`, `assert process.killed is True` fails.
3. `test_command_uses_only_documented_flags_and_forwards_system_model` (`backend/tests/test_claude_code_client.py:260`):
   Can it fail? YES. The test asserts exact equality for CLI flags (`--print`, `--no-session-persistence`, `--tools`, `""`, `--permission-prompts`, `none`, `--output-format`, `stream-json`, `--include-partial-messages`, `--verbose`). Omitting any flag breaks the test.

### Slice 6: Drop Live Wiring of Dead Objects
1. `test_live_core_and_pipeline_do_not_hold_dead_phase_objects` (`backend/tests/test_dead_wiring_dropped.py:18`):
   Can it fail? YES. Asserts `not hasattr(core, attr)` and `not hasattr(core.pipeline, attr)` for `plan_verifier`, `plan_executor`, `episodic_simulator`, and `offline_adapter_gate`. If any dropped attribute is re-added, the test fails.
2. `test_dead_modules_and_phase_benchmark_scripts_remain_importable` (`backend/tests/test_dead_wiring_dropped.py:43`):
   Can it fail? YES. Asserts that the dead modules and V3 benchmark scripts can still be imported. If any file is deleted, `importlib.import_module` raises `ModuleNotFoundError`.
3. `test_proceed_candidate_scores_match_pre_removal_baseline` (`backend/tests/test_dead_wiring_dropped.py:70`):
   Can it fail? NO (Finding F-07). Tests `CandidateSelector().score_and_select()` with `metacognitive_directive="PROCEED"`. Slice 6 did not touch `action_candidate.py` or `decision.py`. If Slice 6 is deleted, this test still passes verbatim. It cannot fail on any slice 6 behavioral change.

### Slice 1: Ledger Registry and Intervention Operators
1. `test_clamp_changes_the_stored_value_and_the_live_reader_sees_it` (`backend/tests/test_interventions.py:102`):
   Can it fail? YES. If `clamp()` fails to update SQLite, the `read_value()` check and subsequent `StateService.hydrate_state()` assertion fail.
2. `test_a_deployment_path_inside_an_offline_copy_is_refused` (`backend/tests/test_interventions.py:255`):
   Can it fail? YES. If `runtime_state_db()` does not check for `OFFLINE_MARKER` when resolving from `IDENTITY_BASE_PATH`, `pytest.raises(OfflineCopyError)` fails.
3. `test_every_writer_and_reader_symbol_still_exists` (`backend/tests/test_ledger_registry.py:84`):
   Can it fail? WEAK / CANNOT FAIL BEHAVIORALLY. The test only checks `symbol in file.read_text()` via substring containment. It does not verify that the symbol writes or reads the row. If the code stops reading or writing that row, the test still passes as long as the token remains in the file. Several symbols are string constants (`_WEIGHT_KEY`, `BIOGRAPHY_SOURCE`) or local variable names (`emotional_bias`), and 14 rows have empty reader or writer tuples.

### Slice 14: Embedding Provenance per Row
1. `test_write_records_embedding_model_and_dimension` (`backend/tests/test_embedding_provenance.py:41`):
   Can it fail? YES. If `MemoryStore.add_memory()` stops recording `embedding_model` and `embedding_dim` columns, assertions on the returned row dictionary fail.
2. `test_import_rejects_a_different_embedding_configuration` (`backend/tests/test_embedding_provenance.py:189`):
   Can it fail? YES. If `_verify_embedding_manifest()` does not check model and dimension against current configuration, `pytest.raises(ValueError)` fails.
3. `test_the_postgres_path_check_is_scoped_by_wing_and_runs_once_per_scope` (`backend/tests/test_embedding_provenance.py:152`):
   Can it fail? CANNOT FAIL ON LIVE HYBRID PATH (Finding F-02). Tests `store._assert_scope_embedding_provenance` directly on an in-memory SQLite fixture. On live PostgreSQL instances using default hybrid retrieval, this scope check is never called.

## Mutation Testing

Four behaviors were tested by modifying isolated copies in `/tmp` and executing tests against them:

1. **Slice 0 (Timeout Termination)**:
   - Target: `backend/app/llm/claude_code_client.py`
   - Mutation: Replaced `process.kill()` inside `_terminate()` with `pass`.
   - Result: `test_generate_timeout_kills_process_and_returns_standard_fallback` failed with `AssertionError`. Mutation was successfully caught.

2. **Slice 1 (Shuffle Operator Permutation)**:
   - Target: `backend/app/state/interventions.py`
   - Mutation: Removed `random.Random(seed).shuffle(after)` from `shuffle()`.
   - Result: `test_shuffle_keeps_the_values_changes_the_order_and_is_seeded` failed with `AssertionError: Shuffle failed to change order!`. Mutation was successfully caught.

3. **Slice 14 (Dimension Validation)**:
   - Target: `backend/app/state/memory_store.py`
   - Mutation: Removed `len(vector) != dimension` check from `_validated_embedding()`.
   - Result: `test_a_wrong_dimension_write_is_refused_without_raising_into_the_turn` failed with `AssertionError` (a 384-dimension vector was accepted instead of rejected). Mutation was successfully caught.

4. **Slice 6 (Dead Service Re-injection)**:
   - Target: `backend/app/cognitive/core.py`
   - Mutation: Restored `self.plan_verifier = object()` in `CognitiveService.__init__`.
   - Result: `test_live_core_and_pipeline_do_not_hold_dead_phase_objects` failed with `AssertionError`. Mutation was successfully caught.

Additional reasoned and script-verified mutations:
- **Slice 0 Process Leak (Finding F-01)**: Script verified that breaking out of `client.generate_stream()` or cancelling a task running `client.generate()` leaves `mock_proc.killed == False`, confirming the background process leak.
- **Slice 1 Where Catch-22 (Finding F-04)**: Script verified that calling `_where_clause(row.sqlite, {"agent_name": "friend"})` for `agent_state` rows raises `InterventionError: where has columns not in the key: ['agent_name']`, confirming the catch-22 error when multiple rows exist.

## What Could Not Be Verified

1. **PostgreSQL pgvector execution**: No live PostgreSQL instance with the pgvector extension was available. Queries relying on `<=>`, `halfvec`, HNSW indexing, and the `surface_actr_memories` stored procedure could only be verified through static SQL string analysis.
2. **Physical Claude Code CLI execution**: The real `claude` executable was not run against live Anthropic endpoints; all testing used stubbed subprocesses and synthetic stream events.
3. **Neo4j graph store**: No live Neo4j database was available to verify semantic graph cue expansion during memory search.
4. **Multi-threaded concurrency load**: Concurrent thread safety of `_embedding_scopes_verified` and SQLite connection pool contention under sustained load could not be benchmarked.
