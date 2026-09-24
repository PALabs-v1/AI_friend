# Findings Ledger

Running record of pre-existing problems found during the Brain V3 cycle (Objective 9). One entry per issue: evidence, severity, fix commit. Expanded through every phase; not a dumping ground for research conclusions (those go in the numbered docs and ADRs).

## F-001: stale code signature on vendored `libonnxruntime.1.27.0.dylib` breaks `cargo test -p stt-agent` on macOS

- **Severity**: medium (blocks local Rust test iteration on macOS; does not affect CI, which builds fresh on Linux runners)
- **Where**: `backend/target/{debug,release}/libonnxruntime.1.27.0.dylib`, vendored by the `sherpa-onnx` crate's build script (a dependency of `crates/stt-agent`)
- **Evidence**: `cargo test -p stt-agent` SIGKILLs before any test output, reproducible with `--test-threads=1` and with the sandbox disabled. `log show --predicate 'eventMessage CONTAINS "stt_agent"'` shows `kernel: CODE SIGNING: cs_invalid_page(...) final status 0x23020200, denying page sending SIGKILL` against that dylib. The same stale artifact exists in the shared checkout's `backend/target/{debug,release}` from before this session, so it predates Brain V3 work.
- **Root cause**: the prebuilt dylib's ad-hoc signature no longer matches its on-disk pages — the classic result of a build script relocating/touching an already-signed binary (e.g. via `install_name_tool`) without re-signing it.
- **Fix**: `codesign --force --sign - <path-to-dylib>` restores a valid signature; `cargo test -p stt-agent` then passes 70/70. This is a local build-artifact fix (nothing to commit — it's a `target/` output, gitignored). Worth a one-line mitigation for future contributors on macOS: either a postbuild step in the crate's `build.rs` that re-signs the vendored dylib after any modification, or a note in `backend/README.md`.
- **Status**: worked around this session (2026-09-24). Not yet fixed at the source (`build.rs` re-sign step not added — low priority, since a clean build from a freshly-extracted `sherpa-onnx` prebuilt archive may not exhibit this at all; only reproduces after the dylib has been touched post-signing on this machine). Revisit if it recurs after a clean `cargo clean`.
