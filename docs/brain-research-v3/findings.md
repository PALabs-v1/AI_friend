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
- **Status**: open (healthcheck itself not yet fixed), but the container's actual readiness is now positively confirmed, not assumed: Phase 4b's `tools/memory_consistency.py` wrote and read back real vectors against it directly, and Phase 4a's GPU sweep did 8 real embedding models' worth of retrieval against it. The healthcheck's blindness to real failures is still a live gap for chaos testing (Phase 9) to hit deliberately.

## F-004: Postgres host port disagreement across `.env.example`, the setup wizard, and the prerequisite checker

- **Severity**: medium
- **Where**: `docker-compose.infra.yml:41` binds host 5433; `.env.example:33` and `scripts/bootstrap/env_wizard.py:188` generate URLs on 5432; `scripts/bootstrap/check_prereqs.py:147` checks port 5432. `start.sh:136` patches `DIRECT_URL` to 5433 for its own Prisma step but doesn't fix the other consumers.
- **Impact**: a fresh setup following `.env.example` or the wizard's output would generate a `DATABASE_URL` pointing at a port nothing listens on. This session's own tunnel setup used 5433 correctly only because I read `docker-compose.infra.yml` directly rather than `.env.example`.
- **Fix**: define the host port once (5433, since that's what's actually bound) and make `.env.example`, the wizard, and the prereq checker agree.
- **Status**: open. Found by Codex C1's infra audit (C0); not yet fixed.

## F-005: `llama3.2:3b` fails to produce parseable classification output most of the time (measured, not assumed)

- **Severity**: medium — a model-compatibility gap in `_classify_intent_and_goal`'s JSON contract, not a production incident. `LLM_FAST_MODEL` (`backend/app/config.py:197`) is a configurable default, not a fixed production commitment: which LLM backs this call is a per-deployment/per-user choice, and `llama3.2:3b` here is one of several dev/test-tier models this research cycle exercises, not something locked in. The reason this still matters: whichever model a given deployment configures for this role has to survive this same JSON contract, so a model that fails it 95% of the time in isolation is a real compatibility finding about that model, worth knowing before anyone points a deployment at it.
- **Where**: `DecisionService._classify_intent_and_goal` (`backend/app/cognitive/decision.py:702`), the call that produces `intent`, `suggested_goal`, `implied_goals` AND `tom_inferences` from one JSON blob
- **Evidence**: Phase 4a's ToM experiment (`09-gpu-experiment-results.md`) called this exact method directly against `llama3.2:3b` for 21 hand-labelled messages × 3 repeats (63 calls). **60/63 calls (95%) failed to produce a parseable JSON block** (`event.metadata.get("tom_inferences")` stayed `None`). The identical harness against `qwen2.5:3b` with the same 63 prompts had a 0% failure rate, ruling out a test-harness bug. Even the rare successful parses were often substantively wrong (e.g. "I got the job offer, I'm so happy!" parsed to `inferred_valence: 0.0`).
- **What this does NOT establish**: whether a real running session with this model configured would see the same failure rate. This experiment calls the classifier in isolation (a single message, a fresh neutral agent state, no real conversation history/context) — a live session's real prompt includes fuller context that could behave differently. That's a real, untested hypothesis, not a confirmed mitigating factor.
- **Impact if it generalizes**: since `intent`, `suggested_goal` and `implied_goals` come from the same parse as `tom_inferences`, a 95% failure rate on this call would mean any deployment configuring `llama3.2:3b` for `LLM_FAST_MODEL` gets broken intent classification too, not just unused ToM fields. If it does NOT generalize, then something about this experiment's narrower prompt context specifically breaks `llama3.2:3b` in a way fuller context does not, which would itself be worth understanding before running this model with less context in any other codepath.
- **Fix**: not attempted this session (root-causing a specific model's structured-output reliability is outside Phase 4's scope). Recommended next step: capture raw LLM responses (not just the parsed result) for a handful of the failing calls, either by re-running this experiment with response logging added, or by adding temporary logging to `_classify_intent_and_goal` in a real running session, to see what `llama3.2:3b` is actually emitting before deciding whether this is a prompt problem, a parsing-regex problem, or a genuine model capability gap.
- **Status**: open. Worth a follow-up before recommending `llama3.2:3b` for this role in any deployment, but it is not a production defect today — no deployment path in this research cycle depends on that specific model for this call.

## F-006: `qwen3:4b`'s thinking-mode output is completely unparseable by the current JSON extractor

- **Severity**: low (qwen3:4b is not a configured production model; this is a forward-looking gap)
- **Where**: `backend/app/cognitive/json_extract.py` (no handling for `<think>...</think>` reasoning blocks); surfaced via the same ToM experiment as F-005
- **Evidence**: `qwen3:4b` failed to produce a parseable JSON block on **63/63 calls (100%)**, with a median latency of 20.2 seconds per call — 10-15x slower than the other two models tested, consistent with generating a long thinking trace before any answer. `grep -n think backend/app/cognitive/json_extract.py` returns nothing.
- **Root cause (plausible, not confirmed with a captured raw transcript)**: Qwen3's default thinking mode prefixes its output with a `<think>...</think>` block; `extract_first_json_value` has no code path to skip past it, so it either finds no JSON or finds a malformed fragment inside the reasoning trace.
- **Fix**: not attempted (qwen3:4b isn't used anywhere in production today; this only matters if it or a similar thinking-mode model is adopted later). If it is, either disable thinking mode in the Ollama request options or add think-block stripping to `json_extract.py` before the JSON search.
- **Status**: open, low priority, informational for future model choices.

## F-007: Throttled or concurrent reflection drops episodes instead of deferring them

- **Severity**: high for memory formation. Most turns in a conversation burst never become episodic memory.
- **Where**: `ReflectionService.trigger_reflection` (`backend/app/cognitive/learning.py`, the `is_reflecting` and `REFLECTION_MIN_INTERVAL_SECONDS` early returns). Both paths return an already-completed future and discard `recent_episodes`. Nothing queues them for the next reflection.
- **Evidence**: read from the code, and consistent with a live two-turn `llm_augmented` run (Phase 6 smoke test, local `llama3.2:3b`). There, each turn was spaced 3 simulated hours apart, so both consolidated. The effective interval from `.env` is 300 s (`Config` default is 30 s). Every `CHAT`/`REMEMBER` turn within 300 s of the last started reflection is silently lost to long-term memory, as is any turn that arrives while a reflection (about 8 s on the Mac) is still running.
- **Measured scale**: V2's throttle rule, applied to lifesim's real session timing for the full 12-persona panel (dev seeds 1000-1011; 2,623 / 14,916 / 29,922 user turns at the 1-month / 6-month / 1-year horizons), yields **0.36 reflections per user turn** at every horizon. This is an upper bound (it assumes every turn is `CHAT`) and ignores the concurrent-reflection drop. **At least about 64% of user turns are never consolidated.**
- **Impact**: in a normal back-and-forth, only about the first turn of each 5-minute window is consolidated. What the user says in the other turns can only be recalled while it is still in working memory. This is invisible in production: no log line, no metric.
- **Fix**: not attempted in Phase 6. BrainBench measures V2 as it ships, and this is W1/W7 territory (Phase 7). An obvious candidate is buffering throttled episodes and folding them into the next reflection, which is what `_build_episode_summary` already supports for a batch. BrainBench's memory suite will quantify how much it costs recall before and after.
- **Status**: open. Found during Phase 6 (BrainBench) while tracing why `architecture_only` could not form memory (DR-037).
