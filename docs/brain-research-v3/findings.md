# Findings Ledger

Running record of pre-existing problems found during the Brain V3 cycle (Objective 9). One entry per issue: evidence, severity, fix commit. Expanded through every phase; not a dumping ground for research conclusions (those go in the numbered docs and ADRs).

## F-001: stale code signature on vendored `libonnxruntime.1.27.0.dylib` breaks `cargo test -p stt-agent` on macOS

- **Severity**: medium (blocks local Rust test iteration on macOS; does not affect CI, which builds fresh on Linux runners)
- **Where**: `backend/target/{debug,release}/libonnxruntime.1.27.0.dylib`, vendored by the `sherpa-onnx` crate's build script (a dependency of `crates/stt-agent`)
- **Evidence**: `cargo test -p stt-agent` SIGKILLs before any test output, reproducible with `--test-threads=1` and with the sandbox disabled. `log show --predicate 'eventMessage CONTAINS "stt_agent"'` shows `kernel: CODE SIGNING: cs_invalid_page(...) final status 0x23020200, denying page sending SIGKILL` against that dylib. The same stale artifact exists in the shared checkout's `backend/target/{debug,release}` from before this session, so it predates Brain V3 work.
- **Root cause**: the prebuilt dylib's ad-hoc signature no longer matches its on-disk pages — the classic result of a build script relocating/touching an already-signed binary (e.g. via `install_name_tool`) without re-signing it.
- **Fix**: `codesign --force --sign - <path-to-dylib>` restores a valid signature; `cargo test -p stt-agent` then passes 70/70. This is a local build-artifact fix (nothing to commit — it's a `target/` output, gitignored). Worth a one-line mitigation for future contributors on macOS: either a postbuild step in the crate's `build.rs` that re-signs the vendored dylib after any modification, or a note in `backend/README.md`.
- **Status**: worked around this session (2026-09-24). Not yet fixed at the source (`build.rs` re-sign step not added — low priority, since a clean build from a freshly-extracted `sherpa-onnx` prebuilt archive may not exhibit this at all; only reproduces after the dylib has been touched post-signing on this machine). Revisit if it recurs after a clean `cargo clean`.

## F-002: voice completion signal is built but has no producer (dead code, confirmed by exhaustive trace)

- **Severity**: high (this is register V-2/ADR-003's "no reliable finished-playing event," now diagnosed precisely rather than just observed)
- **Where**: `backend/app/agents/transport_agent.py:407-438` (the `FIX-CLD-02` completion-marker machinery)
- **Evidence**: `_on_nats_audio`'s `is_done` flag is only ever set from a dict-shaped NATS payload (`data.get("done", False)`, line 337). Exhaustive grep across the repo (`grep -rn "audio.stream\|AUDIO_STREAM" backend/app`, `grep -n '"done"' backend/crates/voice-agent/src/main.rs backend/app/agents/*.py`) shows exactly one producer of the `audio.stream` topic anywhere — `voice-agent`'s `publish_pcm` (`crates/voice-agent/src/main.rs:1715,1754`) — and it always publishes raw bytes, never a dict. `transport_agent.py` subscribes to exactly three subjects (`audio.stream`, `audio.stop`, `audio.playback.visemes`), not `chat.output`, so there's no alternate path either.
- **Root cause**: the consumer-side fix (queue a completion marker, drain it in FIFO order behind real PCM, publish `AudioPlaybackProgress(completed=True)` once it's actually reached the LiveKit audio source) was built correctly, but nothing was ever wired to produce the `is_done=True` shape it depends on.
- **How this was found**: Codex's independent cold audit (C1) claimed this signal *does* reach the brain in production, citing only consumer-side line numbers. Resolving the disagreement required tracing the producer side, which the audit hadn't checked — see `02-audit-comparison.md`'s "Contested and resolved" section for the full trace.
- **Fix**: workstream W4 (`05-research-plan.md`) — either voice-agent emits an end-of-stream trailer that `_on_nats_audio` can recognize, or (better, since transport is the process that actually owns LiveKit playout timing) transport derives completion from its own queue-drain plus a `chat.output.done` subscription it doesn't currently have, rather than depending on voice-agent to shape a payload correctly.
- **Status**: open, scoped into W4. Not yet fixed.

## F-003: Qdrant healthcheck can never fail

- **Severity**: medium
- **Where**: `docker-compose.infra.yml:205`, `./qdrant --version || exit 0`
- **Evidence**: the healthcheck command always exits 0 regardless of whether Qdrant is actually serving traffic — `--version` succeeding proves the binary runs, not that the service is ready. Found by Codex C1's independent infra audit (C0).
- **Impact**: the "healthy" status Phase 0 reported for `brain_vectors` (`04-local-infrastructure.md`) is not proof of readiness, only proof the process started.
- **Fix**: replace with a real readiness probe (Qdrant's `/readyz` or `/collections` endpoint).
- **Status**: open. Not blocking Phase 0-1 since the container did in fact come up correctly (verified by direct client connection in Phase 4b, still pending), but the healthcheck itself should not be trusted going forward.

## F-004: Postgres host port disagreement across `.env.example`, the setup wizard, and the prerequisite checker

- **Severity**: medium
- **Where**: `docker-compose.infra.yml:41` binds host 5433; `.env.example:33` and `scripts/bootstrap/env_wizard.py:188` generate URLs on 5432; `scripts/bootstrap/check_prereqs.py:147` checks port 5432. `start.sh:136` patches `DIRECT_URL` to 5433 for its own Prisma step but doesn't fix the other consumers.
- **Impact**: a fresh setup following `.env.example` or the wizard's output would generate a `DATABASE_URL` pointing at a port nothing listens on. This session's own tunnel setup used 5433 correctly only because I read `docker-compose.infra.yml` directly rather than `.env.example`.
- **Fix**: define the host port once (5433, since that's what's actually bound) and make `.env.example`, the wizard, and the prereq checker agree.
- **Status**: open. Found by Codex C1's infra audit (C0); not yet fixed.
