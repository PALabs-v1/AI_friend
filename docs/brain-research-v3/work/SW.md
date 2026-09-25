# SW: pre-existing problem sweep (Wave B)

Owns (see `COVERAGE.md`): A-7, B-2, F-001, F-003, F-004, F-006, C0-2, C0-4,
C0-5, C0-6, G-1, G-2, G-3, G-5, SW-1, SW-2, SW-6, SW-7.

Starts after W4 and W10a merge: W4 changes the contracts (SW-2 touches
subjects), and W10a changes NATS auth and env files (G-2).

Every item is a small, evidence-backed wire-or-delete or a config fix. For
each: reproduce or confirm it still holds on the current tree, fix it, add a
test that fails before the fix, and update its status in `findings.md` or
the register. If an item no longer reproduces, record the commit that fixed
it instead of inventing a fix.

## Items

1. **SW-1, constructed-but-unused pipeline members.** In `pipeline.py` /
   `core.py`: `plan_verifier`, `plan_executor`, `episodic_simulator`,
   `offline_adapter_gate`, `provider_capability_negotiator`,
   `external_action_dispatcher`. For each: grep every reference, then decide
   to wire it (only if a recorded decision needs it) or delete it along
   with its tests and docs. Not yours:
   - `learning_governor` (W11 makes it the sole evolution gate)
   - `temporal_memory_store` (W1)
2. **SW-2, orphan NATS subjects** with no publisher or no subscriber:
   `audio.pre_generate`, `state.subconscious`, `telemetry.reflection`,
   `voice.segmentation_feedback`. Delete, or wire them, on both the Python
   and Rust contract sides. Keep fixture parity (`test_rust_contract_fixtures.py`)
   and `mesh-integrity.yml`'s subject wiring check green.
3. **A-7**: `CapabilityLimitationModel.evaluate_directive`,
   `GlobalControls.learning_gain`, and calibration are computed or defined but
   never used. Delete, or wire them where a decision needs them.
4. **B-2**: `tests/conftest.py`'s `os._exit` in `pytest_sessionfinish`
   suppresses pytest's summary and tracebacks locally. Find why it exists
   (likely a hanging thread or event loop at exit), fix that cause, and
   remove the `os._exit`. Local `pytest` without `CI=1` must print a normal
   summary and must not hang.
5. **F-001**: `cargo test -p stt-agent` fails on macOS on the vendored
   `libonnxruntime` signature. Fix it at the source (`build.rs` re-sign, or
   the fetch step), not by a local workaround.
6. **F-003 / C0-2**: Qdrant healthcheck `./qdrant --version || exit 0` can
   never fail. Use a real readiness probe.
7. **F-004 / C0-4, G-1, G-3**: env and config drift.
   - Postgres port disagreement across `.env.example` / `env_wizard.py` /
     `docker-compose.infra.yml` / `check_prereqs.py`: one value, everywhere.
   - `LLM_FAST_MODEL` disagreement between `.env.example` and `config.py`
     (model choice is pluggable; the point is one documented default).
   - `QDRANT_HOST` / `QDRANT_PORT` missing from both env files.
   - Add a test that parses the four sources and fails on a disagreement.
8. **G-2**: per-agent NATS credentials. If W10a made auth the default, this
   is the `.env` bootstrap half; verify both are consistent.
9. **C0-6**: `mesh-integrity.yml`'s env-completeness check is warning-only.
   Make it a failing gate once item 7 makes it pass.
10. **F-006**: the JSON extractor cannot read thinking-mode output
    (`<think>...</think>` preambles). Strip reasoning blocks before extraction.
    Test with real captured `qwen3:4b` output shapes (the Phase 4a raw results
    under `results/home-gpu/` have them).
11. **C0-5, G-5**: GPU experiments README. Copy CI's exact maturin build
    invocation, and fix the stale host facts (driver 595.84, the model list
    actually on the box).
12. **SW-6**: `.agents/CONTEXT.md` numeric drift. Reconcile each number with
    its source, or delete the number.
13. **SW-7**: `MockDeterministicLLM.generate_stream` in
    `tests/integration/harness/mock_cognitive_engines.py` returns a coroutine
    instead of being an async generator. `action.py` iterates it with
    `async for` and would crash. Make it an async generator (see
    `evals/brainbench/adapters.py` `NullLLM.generate_stream` for why), and
    add a test that streams one real turn through it.

## Acceptance (mechanical)

- Every item: a fail-before/pass-after test, or a recorded "no longer
  reproduces, fixed by <sha>".
- Full backend pytest and `cargo test --workspace` green.
- `mesh-integrity.yml` checks pass locally with `act` or by running their
  scripts directly.
- `tests/test_brainbench_gates.py` and `tests/test_brain_v3_coverage.py` pass.
- `COVERAGE.md` rows for every item are updated to `CLOSED` with the
  evidence.
