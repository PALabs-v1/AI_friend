# Local Infrastructure (Phase 0)

Recorded 2026-09-24. Start commit `001069c5181dbb1bbcb8a5ee848f7c7eebb1e5c2` (`origin/main`, PR #216 merge). Working branch `brain-v3`, pushed to `origin/brain-v3` (renamed from the plan's `research/brain-v3`: that ref path collides with the existing `origin/research` branch — see below).

## Topology

Per Aniket's decision: home-gpu runs heavy infra, embeddings and GPU experiments; the Mac runs code, unit tests, the no-LLM simulator, and a small OrbStack stack for functional/chaos tests. Reached over an SSH tunnel, not a public bind — every compose port was already loopback-only (`docker-compose.infra.yml`'s own P0-2 comment: this was fixed 2026-08-22 after an audit found all nine services reachable LAN-wide, including an unauthenticated LiveKit SFU).

## Machines

| | Mac (`anikets-macbook-air`) | home-gpu (`aniket-b450m-ds3h-v2`) |
|---|---|---|
| Role | code, unit tests, lifesim generation, small chaos stack | heavy infra, real embeddings, GPU experiments, scale tests |
| CPU | Apple M5, 10 cores (4P+6E) | Ryzen (12 threads) |
| RAM | 16 GB | 15 GB |
| GPU | none (Metal only) | RTX 2060 SUPER, 8 GB VRAM, driver 595.84, CUDA 12.2 |
| OS | macOS 27.0 (26A428) | Ubuntu 24.04.4 LTS, kernel 7.0.0-31-generic |
| Disk | root fs only | `/` 115 GB (37 GB free after cleanup); `/data` 247 GB (229 GB free) — all new data goes here |
| Python | 3.13 (root `.venv`, unused for this project); **3.12.14** in `backend/.venv` (this cycle) | 3.12.3 system; **3.12.3** in `/data/aif-v3/backend/.venv` |
| Rust | cargo/rustc 1.98.0, `~/.cargo/bin` on PATH | cargo/rustc 1.98.0 at `~/.cargo/bin` (not on non-interactive ssh PATH — invoke by full path or `export PATH="$HOME/.cargo/bin:$PATH"`) |
| Ollama | native, :11434 (nomic-embed-text, embeddinggemma, llama3.2:3b, qwen2.5:3b) | native, :11434 (nomic-embed-text, llama3.2:3b, qwen2.5:3b, qwen3:4b, gemma3:4b, phi4-mini, qwen2.5-coder:7b) |
| Docker | OrbStack 29.4.0 (was stopped; started this session) | Docker 29.7.2 (was already running) |
| Reachability | — | Tailscale 100.88.246.46, SSH alias `home-gpu` (also `100.88.246.46`), passwordless sudo enabled for `aniket` |

## Repository state

- **Mac shared checkout** (`/Users/aniketsaha/Projects/PALabs/AI_friend`): unchanged, still on `main`. An untracked `master_prompt.md` lives there (the task brief); left alone.
- **Mac session worktree**: `~/.claude-worktrees/AI_friend-2527627756/10f9894b`, branch `brain-v3` (tracks `origin/brain-v3`), created from `origin/main` at `001069c` per CLAUDE.md's branching ritual.
- **home-gpu**: the old checkout at `~/AI_friend` was stale (`156f3b7`, unrelated `feat(phase-07)` work, remote pointed at a different fork `Aniket-a14/AI_friend`), 12 GB, no running containers, no volumes. Deleted at Aniket's instruction. Its `.env` could not be backed up before deletion (a `/data/backups` permission fix landed one command too late) — not a loss of anything unique, it was disposable local dev config.
- **home-gpu now**: a fresh clone at `/data/aif-v3`, `git clone --branch brain-v3 https://github.com/PALabs-v1/AI_friend.git`, at `001069c`. `~/AI_friend` is a symlink to `/data/aif-v3` so path-hardcoded scripts (`scripts/remote/gpu.sh`, muscle memory) keep working. `.env` copied from the Mac's current one as a starting point; DB/Qdrant/Neo4j URLs in it already resolve to `127.0.0.1`, which is correct since services run locally on home-gpu itself.
- **Branch name change**: the plan's `research/brain-v3` was rejected by `git push` — `directory file conflict`, because `origin/research` already exists as a leaf branch and git refs are path-like (`refs/heads/research` can't also be a directory prefix). Renamed to `brain-v3`. Nothing else in the plan depended on the literal string `research/`.

## Infra stack (home-gpu, compose project `aifv3`)

```
docker compose -p aifv3 -f docker-compose.infra.yml up -d postgres qdrant neo4j redis nats
```

| Service | Container | Image | Port (loopback) | Status |
|---|---|---|---|---|
| Postgres+pgvector | `postgres_db` | `pgvector/pgvector:pg16` | 5433 | healthy |
| Qdrant | `brain_vectors` | `qdrant/qdrant:v1.9.0` | 6333/6334 | healthy |
| Neo4j | `brain_graph` | `neo4j:5.26.0` | 7474/7687 | healthy |
| Redis | `brain_cache` | `redis:7-alpine` | 6379 | healthy |
| NATS JetStream | `nats_mesh` | `nats:2.10.24-alpine` | 4222/8222 | healthy |

Not started: `livekit`, `ollama` (docker profile — native Ollama is used instead), `gpt-sovits` (voice weights are empty; CUDA-only; out of scope for this infra pass).

**Mac → home-gpu tunnel** (background, PID recorded at setup time):
```
ssh -f -N \
  -L 15433:127.0.0.1:5433 -L 16333:127.0.0.1:6333 -L 16334:127.0.0.1:6334 \
  -L 17474:127.0.0.1:7474 -L 17687:127.0.0.1:7687 \
  -L 16379:127.0.0.1:6379 -L 14222:127.0.0.1:4222 \
  home-gpu
```
All 5 tunneled ports verified reachable from the Mac (`nc -z`). Re-run this command if the tunnel drops (`ps aux | grep "ssh.*home-gpu"` to check).

**OrbStack (Mac)**: daemon started this session (was stopped). No local compose stack is up yet — brought up on demand for Phase 9 chaos work with its own project name (`aifv3-mac` convention, using the same `docker-compose.infra.yml`) and its own DB/collection names, so it never collides with home-gpu's data.

## Environments built this session

| | Mac `backend/.venv` | home-gpu `/data/aif-v3/backend/.venv` |
|---|---|---|
| Python | 3.12.14 | 3.12.3 |
| Deps | `requirements-dev.txt` + `hypothesis` | `requirements-dev.txt` + `hypothesis` |
| `cognitive_rust` | 7.1.1, built locally (`maturin build --manifest-path crates/cognitive-rust/Cargo.toml --out target/wheels --release`), abi3, arm64 | 7.1.1, same command, abi3, manylinux x86_64 |
| Import check | passes | passes |

## Baseline test runs (this commit, this session)

- **Mac, `CI=1 pytest -q`**: **2548 passed**, 0 failed, ~91s. (Docs claimed 2540 at merge time; small positive drift, not investigated further here — full triage belongs in Phase 3's formal baseline.)
- **Mac, `cargo test --workspace`**: **179 passed** (21 cognitive-rust + 10 contracts + 70 stt-agent + 78 voice-agent), 0 failed, after the fix below.

### Finding: stale/invalid code signature on vendored `libonnxruntime.1.27.0.dylib` (fixed)

`cargo test -p stt-agent` SIGKILLed immediately on every run, before any test printed output — reproduced with `--test-threads=1`, reproduced with the harness sandbox disabled, so not a sandboxing artifact. `log show` traced it to the kernel:

```
CODE SIGNING: cs_invalid_page(0x103f4c000): p=26278[stt_agent-a921ff] final status 0x23020200, denying page sending SIGKILL
... rejecting invalid page ... in file ".../target/debug/libonnxruntime.1.27.0.dylib"
```

The dylib (vendored by the `sherpa-onnx` crate's build script, used for the SenseVoice STT path) had a broken ad-hoc code signature — `codesign -dv` showed a valid-looking adhoc signature, but at least one page's content didn't match its recorded hash, which is what happens when a prebuilt, already-signed binary gets touched (e.g. `install_name_tool`) after signing without being re-signed. The same stale artifact exists in the **shared checkout's** own `backend/target/{debug,release}`, so this predates this session — it is a standing local-build hazard on this Mac, not something introduced here.

**Fix applied** (local artifact only, no source change): `codesign --force --sign - target/debug/libonnxruntime.1.27.0.dylib`. Confirmed: `cargo test -p stt-agent` then passes 70/70, and the full workspace run is clean. This doesn't need a repo change (nothing to commit — it's a generated build artifact), but it's worth a one-line note in `backend/README.md` or a `Makefile`/`justfile` postbuild hook if this bites again after every clean rebuild. Logged in `findings.md`.

- **home-gpu**: no test run yet — Rust toolchain resolved (`~/.cargo/bin`, version-matched at 1.98.0) and the wheel builds and imports cleanly, but full `cargo test --workspace` / `pytest` there is deferred to Phase 3's formal baseline (this machine's role is infra + Python GPU work per the split decision, not day-to-day Rust test iteration).

## Detached remote execution (home-gpu, Phase 6)

`/data/aif-v3` is kept fast-forwarded to `origin/brain-v3` (currently at
`c3314ea`) with its own `.venv` verified importable and test-clean (141/143
BrainBench+lifesim tests, 2 real-LLM tests excluded from that count since
they need a model call). This exists so a long `llm_augmented` verification
run doesn't have to live on the Mac's process tree at all: a MacBook forces
system sleep on lid-close regardless of `caffeinate` unless it's in
clamshell mode (external display + power) — this Mac has neither connected
— so any Mac-side process driving a real LLM replay dies or stalls the
moment the lid closes, even though the actual inference already happens on
home-gpu over the network. Running the driver process itself on home-gpu
removes the Mac from the loop entirely.

Pattern, proven working (launched, confirmed running detached from the SSH
session that started it, via a second `ssh ... pgrep` from a different
session, then cleanly stopped):

```bash
ssh home-gpu "cd /data/aif-v3/backend && \
  nohup env CI=1 .venv/bin/python -m pytest <target> -q \
  > /data/aif-v3/run-logs/<name>.log 2>&1 < /dev/null & disown; \
  echo LAUNCHED pid=\$!"
```

Before each use: `ssh home-gpu "cd /data/aif-v3 && git fetch -q origin && git merge --ff-only origin/brain-v3"`
to bring it current (has been a clean fast-forward every time so far — no
local tracked-file changes live there, only ignored build artifacts and a
few untracked prior-phase result directories left alone). Codex builds
still cannot move here: Codex CLI is not installed on home-gpu, so Codex's
own work (as opposed to verifying it) still requires the Mac to stay awake.

## Known gaps carried into Phase 1+ (from the original exploration pass, not yet re-verified against code)

- `.env` / `.env.example` disagree on `LLM_FAST_MODEL` (`qwen2.5:3b` vs `llama3.2:3b`).
- `.env` lacks the per-agent NATS credentials (`NATS_BRAIN_USER`, etc.) that `.env.example` defines — relevant only if the full prod mesh (not just infra) is ever started.
- `QDRANT_HOST` / `QDRANT_PORT` are not set in either `.env` file; code defaults to `127.0.0.1:6333`, which happens to be correct for both the tunnel (Mac) and the local stack (home-gpu).
- `models/GPT_weights` and `models/SoVITS_weights` (repo root, mounted by `docker-compose.infra.yml`) are empty on the Mac and absent on home-gpu (re-checked 2026-09-26) — GPT-SoVITS voice cannot run anywhere without fetching weights; out of scope unless a workstream needs it.
- The GPU experiments README's host description (driver 535, specific model list) is stale versus the box's actual state (driver 595.84; confirmed above).

## Next

Codex C0 (read-only) runs an independent audit of this same compose/env/config surface, with none of the above shared. Its findings go to `02-audit-comparison.md` alongside the Phase 1 Brain V2 audit comparison.
