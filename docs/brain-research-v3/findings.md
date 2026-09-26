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

## F-006: `qwen3:4b`'s thinking-mode output is completely unparsable by the current JSON extractor

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

## F-008: `test_ten_year_render_and_probes_fit_budget` was red on every CI run, not just this session's

- **Severity**: low (test hygiene, not a production defect), but it meant PR #217's `Backend Lint + Tests (macOS)` and `backend-test` jobs looked broken by an unrelated lint pass when the real cause was a checked-in perf gate that had never actually passed on the CI machine.
- **Where**: `backend/tests/test_lifesim_text.py::test_ten_year_render_and_probes_fit_budget` (`< 30` on the wall-clock time to build a 10-year `socialite` life). `backend/evals/lifesim/probes.py`'s `_build_checkpoint` (the `leaked()` abstention closure and the untold-slot membership check).
- **Evidence**: `gh api repos/PALabs-v1/AI_friend/commits/fc590224/check-runs` (the commit immediately before this session's probe-semantics rewrite, already merged into `brain-v3`) shows `backend-test` and `Backend Lint + Tests (macOS)` both `failure` on this same test. Reproduced locally at that commit under `--cov=app` (the flag both CI jobs always pass, per `ci.yml`/`macos-ci.yml`): 54s. Untraced, the same build takes ~3s. `ci=1 pytest ... --cov=app` vs. no `--cov` on identical code is a ~20-30x gap, confirmed both before and after this session's changes.
- **Root cause**: the test measures wall-clock time with `time.perf_counter()`, but every CI job that runs it also runs under coverage line-tracing, which was never accounted for when the 30s budget was set. Two real complexity bugs in the probe-semantics rewrite (an O(candidates × turns) `leaked()` rescan, and an O(timeline × claims) untold-slot check, both in `_build_checkpoint`) made the traced time worse (54s → ~92s) without being the root cause of the CI failure, which predates them.
- **Fix**: fixed both complexity bugs (turned into cached per-owner haystacks and a precomputed slot set — 61.6M → 48.4M function calls, 4.1s → 3.0s untraced). Made the test itself robust to the environment it actually runs in: `sys.gettrace() is not None` reliably detects coverage's `CTracer` (verified against a real `--cov` run), and the budget scales to 240s under it, 30s otherwise — so the gate still catches a real regression without being fooled by test-runner overhead it has no control over.
- **Status**: fixed, commit `167db41b`. Found while investigating why PR #217's lint fix didn't turn CI fully green.

## F-009: System2 semantic-drift appraisal echoes its own prompt template instead of analyzing the user's text -- `valence` never moves in practice

- **Severity**: high. This is the specific, root-caused mechanism behind A-1 ("user words never reach affect", `docs/brain-research/01-problems.md`): not a vague absence of a feature, but a concrete, reproducible defect in code that already exists and is already wired into every turn.
- **Where**: `backend/app/cognitive/appraisal.py::AppraisalEngine.appraise_semantic_drift` (the prompt at lines ~450-465) and `_drift_pad_toward` (lines ~416-440). Reachable from every `USER_MESSAGE` turn via `backend/app/cognitive/pipeline.py`'s background `_async_system2_appraisal` (~line 1088). `current_state.valence` (`backend/app/state/agent_state.py`) is written **exclusively** by this path — `update_from_appraisal` (the synchronous, per-turn appraisal path) updates `mood`/`energy`/`dominance`, never `valence`.
- **Evidence**: BrainBench's new affect suite (`backend/evals/brainbench/affect_suite.py`) replayed 80 real turns of a `chatty_student` persona through a real `llm_augmented` `CognitiveService` on home-gpu (`llama3.2:3b`, `Config.LLM_FAST_MODEL`'s default, the model this call always uses regardless of what model the rest of the session is configured with). `valence_delta` was **exactly 0.0 on every single turn**, for both positive- and negative-oracle-valence turns alike (`user_valence_reaches_mood`: `positive_mean_delta=0.0, negative_mean_delta=0.0, cliffs_delta=0.0`), with `system2_completion_rate` reporting 100% (no exception, no timeout, no logged failure from either `app.cognitive.pipeline` or `app.cognitive.appraisal`). A direct, isolated call to the same prompt against the same model with the utterance *"I just got promoted at work, I am so happy!"* (unambiguously, strongly positive) returned `{"goal_congruence": 0.0, "norm_alignment": 1.0, "expectedness": 0.5}` — **exactly** the placeholder numbers shown in the prompt's own "Output JSON ONLY: {example}" block, verbatim, for all three fields.
- **Root cause**: the prompt shows the model a literal example JSON with specific numbers (`goal_congruence: 0.0`, etc.) as a formatting instruction, but `llama3.2:3b` treats it as the answer to copy rather than a shape to fill in — a classic small-model instruction-following failure, not a JSON-parsing problem (the JSON is perfectly valid and parses cleanly, which is why neither of the two logged failure paths nor a timeout ever fires). Since `target_p` (derived from `goal_congruence`) is always exactly the example's `0.0`, and `current_state.valence`'s baseline is also `0.0`, `_drift_pad_toward`'s blend (`val + 0.2 * (target - val)`) computes to `val` unchanged, forever, regardless of how emotionally charged the actual user text is.
- **Distinguishing this from F-005**: F-005 found `llama3.2:3b` fails to produce *parseable* JSON 95% of the time for a ToM classification call. Here the JSON is always valid — the defect is that the *content* is content-independent, a strictly harder class of failure to detect (a benchmark or log-based observer sees a clean success). Building the affect suite's `system2_completed` metric (log-watching two different loggers, `app.cognitive.pipeline` and `app.cognitive.appraisal`) was necessary but not sufficient to catch this: it correctly reports 100% "the task returned cleanly," which is honestly what it measures, but that number cannot mean "the appraisal was meaningful" while this defect exists. The suite's independently-measured `valence_delta` is what actually surfaced this, not the completion-rate proxy.
- **Fix**: not attempted in Phase 6 (BrainBench measures V2 as it ships; this is W2 territory, Phase 7). Candidate fixes for W2: drop the literal example numbers from the prompt (describe the scale in words only, or use a clearly-different few-shot example so copying it produces an obviously-wrong answer the caller can detect), or move this call to a model that reliably follows fill-in-the-template instructions, or add a cheap "did the output exactly match the example" guard that treats an exact echo as a failure rather than a success.
- **Status**: open. Found during Phase 6 (BrainBench) building and verifying the affect suite (Codex C6, `codex-log.md`).

## F-010: trust is a turn counter -- it rises the same amount on a complaint, an argument, and a thank-you, then saturates in 13 turns and goes deaf

- **Severity**: high. Sharpens A-3 ("trust rises under hostility", `docs/brain-research/01-problems.md`) from a symptom into a measured mechanism, and shows it is worse than A-3 states: V2's trust state does not read the user at all.
- **Where**: `backend/app/state/agent_state.py::StateService.update_from_appraisal`, the "Relational updates" block (~lines 1342-1356), run synchronously on every `USER_MESSAGE` turn. `delta` is the persona's `trust_change_rate` (0.1 for the default persona). Inputs from `app/cognitive/appraisal.py::_compute_appraisal_fallback`: `R` (relevance) is the constant 1.0 for every user message; `NA` (norm alignment) is 1.0 unless the text contains an identity-boundary keyword; `G` and `RI` come from the agent's own mood (`emotional_bias`), not from anything the user said.
- **Evidence** (BrainBench trust suite, `architecture_only`, real `CognitiveService`, dev seeds, `private_minimalist`, `1m`):
  - Every user turn adds the same `+0.04` to competence and `+0.10` to integrity, whatever it says. Benevolence never moves off 0.5 in any run.
  - Integrity pins at the 1.0 clamp after turn 6 and competence after turn 13, identically across seeds, so trust reaches 0.833 and stops. 79-82% of all turns in a one-month run are ceiling-masked: the update still pushes up, and the clamp hides it. The plan's W3 target is "no ceiling within 100 positive turns". V2 hits the ceiling at 13.
  - On the only hostile turns that land before saturation, trust rose every time (`hostile_trust_rise_rate = 1.0`). Seed 1062 turn 1 was a `robot_complaint` (competence evidence -1) and trust rose +0.0467. Seed 1042 turn 2 was a `robot_misunderstanding` and trust also rose +0.0467. Seed 1062 turn 2 was a `robot_thanks` and trust rose by the identical +0.0467.
  - `competence_signal = 0.0`: complaints and thanks move competence-trust by exactly the same amount. `competence_leak = +0.04`: a warmth-only turn (`robot_affection`, no competence evidence) moved competence-trust as much as real competence evidence does, so lifesim's DR-014 separation of competence from warmth does not exist on the brain side.
  - After turn 13, no thanks, complaint, or argument can move trust at all.
- **Root cause**: none of the three increments depends on the user's content. Competence and integrity grow from constant terms (`0.4 * R` with `R = 1.0`, and `NA = 1.0`) on every turn, and benevolence depends only on the agent's own mood. Trust is a clamped count of user turns.
- **Measurement note**: the suite's first draft reported `hostile_trust_rise_rate = 0.0` on seed 1015, which reads as "trust correctly held under hostility". Every hostile turn in that run had landed after saturation, so the 0.0 was the clamp, not the model. The suite now records per-component before-values and a `ceiling_masked` flag. It scores aggregate metrics over unmasked turns only, and scores component metrics masked on the component they read. It reports `None` rather than 0.0 when every turn in a group is masked. The same trap applies when judging any Phase 7 W3 fix.
- **Fix**: not attempted in Phase 6 (W3 territory, Phase 7, gated on interview round R4's decisions about what trust means). The trust suite is the instrument W3 should be judged against, via `hostility_response`, `competence_warmth_separation`, and `background_drift.turns_to_ceiling`.
- **Status**: open. Found during Phase 6 building and verifying the trust suite (Codex C7, `codex-log.md`).

## F-011: V-4 is a flood, not a reset -- once the user is idle 2 h, V2 reaches out every tick (60 per hour) instead of once per hour

- **Severity**: high. V-4 (`docs/brain-research/01-problems.md`) was filed as "the proactive cooldown can reset". Measured end to end, it disables the cooldown entirely.
- **Where**: three pieces that are each reasonable alone.
  1. Every `system.tick` also reaches the brain: `backend/app/cognitive/core.py` subscribes (~line 370), and `_on_system_tick` (~392) calls `StateService.handle_system_tick` (`backend/app/state/agent_state.py:1682`). That handler ends in `persist_state()` (~1767), which publishes `state.broadcast` carrying the brain's own `last_proactive_attempt`.
  2. The brain never marks an attempt. `mark_proactive_attempt` (~1851) runs only in the subconscious process, sets only its local field, and does not bump `revision`. So the brain's next broadcast passes `apply_external_state`'s revision guard (~928-950) and overwrites the mark (~988). The subconscious never publishes its own state (`backend/app/agents/subconscious_agent.py` only subscribes), so the sync is one-way.
  3. The idle clock ignores the agent's own outreach. `backend/app/agents/brain_agent.py:971-973` records user interaction only when the chat input is not subconscious-sourced, so idle keeps growing through every outreach turn.
- **Evidence** (BrainBench proactive suite, `architecture_only`, real brain `StateService` + real subconscious-shaped `StateService` + real `SubconsciousEngine`, seed 1001 `steady_professional` `1w`, 7,446 simulated ticks, no capped gaps):

  | Sync | Tick order | Outreach per sim day | Per idle hour past 2 h | Cooldown violations | Resets | Min spacing |
  |---|---|---:|---:|---:|---:|---:|
  | broadcast (production shape) | brain first | 1,319.8 | 60.01 | 6,831 | 6,836 | 60 s |
  | broadcast | subconscious first | 1,319.8 | 60.01 | 6,831 | 6,836 | 60 s |
  | none (control) | either | 22.4 | 1.03 | 0 | 0 | 3,600 s |

  6,836 initiations in one simulated week versus 116 with the sync removed. Tick order does not matter. 35% landed in the persona's sleep window, and 0 were useful by the suite's commitment oracle (a known plan due within 24 h).
- **Measurement note**: the suite's first draft modelled only the subconscious's ticks. It saw 5 cooldown resets that had no effect, because a user turn resets idle and the 2 h idle threshold exceeds the 1 h cooldown. Only with the brain's own tick handler in the replay does the reset repeat every tick. Any W9 fix must be judged with brain ticks in the replay.
- **Caveat**: `NullLLM` always yields a non-empty thought, so this is the gate's behaviour. With a real model, a thought generation that fails or returns empty skips that tick. While nobody is connected, thoughts go to `proactive_queue` at the same rate.
- **Fix**: W9 chose a monotonic max-merge of `last_proactive_attempt` in `StateService.apply_external_state`, including stale-revision snapshots. The broadcast arm continues to run the real brain tick handler in both orders. W9 also adds importance/category scoring, quiet-hour and activity gating, and bounded ignored-thought decay; see `adr/ADR-W9-proactive.md`.
- **Acceptance evidence** (W9 fixed dev panel, `architecture_only`, 12 personas/seeds 1000–1011, `1w` and `1m`, both broadcast tick orders, 48/48 cells, 0 errors): cooldown violations 0; marked-attempt resets 0; median post-threshold outreach rate 0 per idle hour; annoyance 0.088 per simulated day versus the 21.8/day V2 control; pooled useful rate 45.7% versus V2's 0%; night fraction 0.171 versus V2's 0.365. The one-week useful rate was 0%, and the one-month rate was 50%, under the unchanged 24-hour planned-commitment oracle.
- **Status**: fixed and acceptance measured. The monotonic merge is backed by an atomic Redis high-water mark, brain-side marking on accepted subconscious turns, and regressions for both tick orders and stale-writer restart.

## F-012: personality evolution is frozen in V2, and the DR-020 path replaces the whole adaptive self in a week with no content gate

- **Severity**: high for W11. V2 as shipped cannot evolve; the path DR-020 wants has no gate that reads content.
- **Where**: `backend/app/cognitive/learning.py::_consolidate_persona` (~276-327). With `Config.LEARNING_REVIEW_REQUIRED=True` (the default, `app/config.py:276`) a confident proposal goes through `_governed_persona_proposal` into `review_queue`, which nothing approves. With it False, `identity.evolve_persona(suggestions)` is called directly (~320) and `LearningGovernor` is never consulted. `LearningGovernor`'s check (`backend/app/cognitive/learning_governance.py::check_targets_protected_domain`) scans key names only, never values.
- **Evidence** (BrainBench personality suite, `llm_augmented`, home-gpu `llama3.2:3b`, seed 1000 `chatty_student` `1w`, 62 turns, both arms on the same simulation):

  | | review_queue (V2 default) | direct_apply (DR-020 path today) |
  |---|---:|---:|
  | reflections / persona replies parsed | 56 / 100% | 55 / 100% |
  | confident proposals (>= 0.8) | 41 (73%) | 32 (58%) |
  | queued / applied | 41 / 0 | 0 / 32 |
  | adaptive trait distance from seed | 0.0 | 1.0 (entire set replaced) |
  | traits added + dropped | 0 | 91 |
  | relationship label changes | 0 | 23 |
  | persona prompt chars | 1439 -> 1439 | 1439 -> 1530 (max 1548) |
  | immutable / constitutional violations | 0 / 0 | 0 / 0 |

  Scripted probes against the same code: a proposal adding the trait `deceptive` with relationship `Manipulative rival` was applied in full under `direct_apply`, and under `review_queue` it passed the governor and was queued. A proposal smuggling `name`/`traits`/`values` was rejected by the governor in `review_queue`, and ignored by `evolve_persona`'s key whitelist (`identity.py:712-729`) in `direct_apply`. The tier boundary holds structurally; content does not have a gate.
- **Reading**: the hard boundary (immutable, constitutional) is intact in both arms. The adaptive self is either frozen forever or rewritten on nearly every confident reflection: 23 relationship relabels in one week is churn, not development. `value_conflicts = 0` in the real run is a lexical lower bound (the suite's own docstring), not evidence of safe content.
- **Fix**: not attempted in Phase 6 (W11, Phase 7). What W11 needs: a rate or inertia on adaptive change (so a week cannot replace the self), a content check that reads values against `IMMUTABLE_CORE` (the governor cannot), and the governor on the direct path.
- **Status**: open. Found during Phase 6, personality suite (Codex C8).

## F-013: a barge-in by real user speech leaves the stopped reply with no terminal outcome and its full text in history

- **Severity**: medium. Documented by ADR-003 as "known and not changed", now measured.
- **Where**: `backend/app/agents/brain_agent.py::_on_audio_stop` (~1194-1260): a `confirmed_user_speech` stop flushes audio but neither emits an `OutcomeRecord` for the playing reply nor truncates its history row (ADR-003 lines ~91 and ~106).
- **Evidence** (BrainBench barge-in suite, real `BrainAgent` in-process, seed 1000, 50 scenarios per family, 350 total, 0.6 s): 50/50 `confirmed_barge_in` scenarios end with zero terminal outcomes for the stopped reply and a history row that still holds unheard text (both counted as unclaimed by ADR-003). Every reply that plays in full also ends unresolved (0 COMPLETED outcomes across 350 scenarios), which is F-002 from the brain's side. ADR-003's claimed guarantees hold: 0 stale or unknown stops applied, 0 current-turn harm, 0 duplicate outcomes, 0 hangs.
- **Measurement note**: two harness defects produced false V2 failures and were fixed before these numbers (24% false "current turn harmed" and 4% false missing outcomes in the random family). See codex-log C10.
- **Fix**: W4 (lifecycle contract) and W5 (barge-in end to end), per R8.
- **Status**: open.

## F-014: hygiene findings from the Phase 6 suites (low)

- **Severity**: low, each.
- `workspace_transitions` (`backend/app/state/workspace_store.py` ~354) gains one row per turn and has no DELETE, prune or trim path anywhere in production. Measured by the resources suite: 10 -> 223 rows over a 6-month replay, slope 1.0 row per turn.
- `backend/app/agents/surfacing_agent.py:111` and `:187`: the chat and sweep throttles read `time.time()`, not `app.clock`, so the clock seam (Phase 6) does not cover them and a simulated replay cannot pace them. The metacognition suite bypasses them and records their simulated effect instead.
- `MemoryStore.last_search_error` (`memory_store.py` ~3899-3942) is one shared field that the next successful search clears, so it is not a durable outage signal for M-8 (see the metacognition suite's outage injection).
- Formatting was not enforced: the pre-commit hook was never installed in this clone, and CI ran `ruff check` but never `ruff format --check`. Seven BrainBench files had drifted (fixed in `eb10425b`).
- `LearningGovernor`'s proposal registry (`learning_governance.py`, `self._proposals`) is in-memory and never pruned. In the real one-month resources run it grew 1 -> 19 and was still rising at the end (one entry per confident persona proposal). Same shape as `workspace_transitions`, in RAM rather than on disk.
- **Status**: open (items 1, 2, 3, 5). Item 4 fixed in `e2b73f89`: hook installed, its ruff pinned to CI's 0.16.1 (it was 0.14.1), and a changed-files `ruff format --check` ratchet added to CI.

## F-015: M-10 measured on a real model -- once surfacing is running, per-turn retrieval almost never runs and 80% of the memories in context are stale carry-overs

- **Severity**: medium (M-10, `docs/brain-research/01-problems.md`, confirmed with numbers).
- **Where**: `backend/app/cognitive/core.py` keeps `surfaced_memories` across turns (trimmed to 5, never cleared), and `backend/app/cognitive/action.py` (~1460) runs its fallback retrieval only when that list is empty.
- **Evidence** (BrainBench metacognition suite, `llm_augmented`, home-gpu `llama3.2:3b`, seed 1000 `chatty_student` `1w`, 62 turns, real `SurfacingAgent` driven in-process vs no surfacing):

  | | with surfacing (production shape) | control (no surfacing agent) |
  |---|---:|---:|
  | turns where per-turn retrieval ran | 1.6% (1/62) | 98.4% |
  | turns that skipped retrieval because surfaced memories were present | 98.4% | 0% |
  | surfaced items that came from an earlier turn's sweep | 80.3% (237/295) | n/a |
  | answerability AUROC (top retrieval score, answerable vs unanswerable probes, n=19) | 0.79 | 0.61 |
  | correctness AUROC (n=14 answerable) | 0.90 | 0.80 |

  After the first surfacing event the per-turn synchronous retrieval stops running, and most of what the model sees as "relevant memories" was retrieved for an earlier utterance. The AUROCs say the top retrieval score is a usable confidence signal (useful for W8's abstention floor). With n=19 the gap between arms is within noise.
- **M-8 in the same run** (control arm, where search runs): 7 injected search failures in production's own failure shape; 0 turns errored and 0 left any failure marker in the brain's outputs or decision metadata. In the surfacing arm no outage was ever exercised, because retrieval never ran.
- **Not measured**: contradiction recording. This seed and horizon had no contradiction- or correction-tagged turns (`tagged_n = 0`), so it waits for the persona-panel baseline.
- **Fix**: W8 (Phase 7). The metacognition suite's surfacing arm is the instrument.
- **Status**: open.

## F-016: learned state is written to the container's image layer in production, and `StateService` ignores `REDIS_URL`

- **Severity**: high for production persistence; it also broke BrainBench cell isolation on any host with Redis running.
- **Where**:
  - `StateService.__init__` (`backend/app/state/agent_state.py` ~474-480) defaults `db_path="state_cache.db"`, a relative path. It hardcodes `redis_host="127.0.0.1"`, `redis_port=6379`, and `Config.REDIS_URL` is never read.
  - `CognitiveService.__init__` (`backend/app/cognitive/core.py` ~83-131) puts the workspace, temporal and session databases under its runtime directory (`base_path` or `Config.IDENTITY_BASE_PATH`). It then builds `StateService`, `ReappraisalEngine` and `DecisionService` without a path, so their SQLite files land in the process's working directory. The last two use `AdaptiveWeightsStore()` with default `state_cache.db`.
  - `SubconsciousAgent` builds its own `StateService` the same way.
  - `WorkingMemoryStore` does read `REDIS_URL`, but writes to global keys (`working:turns`, `working:state`) with no session in the key.
- **Evidence**:
  - `docker-compose.prod.yml` sets `IDENTITY_BASE_PATH=/app/data` and mounts only `identity_data:/app/data` for `brain_agent` and `subconscious_agent`, and the image's `WORKDIR` is `/app`. So `/app/state_cache.db` is on the image's writable layer.
  - `docker-compose.prod.yml` has no Redis service and sets no `REDIS_URL`. Inside a container, `127.0.0.1:6379` is the container itself, so both Redis clients always fail and every state write goes to that SQLite file.
  - So in production, every redeploy silently resets all persisted agent state: mood, trust, attachment and `last_proactive_attempt`. It also resets the learned reappraisal weights (#117/H6) and goal utilities (#118/H7), whose persistence fixes therefore never held in production. This is the same class as #113/H2, which was fixed for identity only.
  - On home-gpu, with the infra stack's Redis up, every BrainBench cell in every worker process shared one Redis and one `backend/state_cache.db`. The runner logged `database is locked`, and cells ran 30-60x slower than on the Mac, where Redis was down and each fell back to SQLite. The shared state was write-only in BrainBench: `hydrate_state()` / `load_session_state()` / `get_recent_turns()` are never called on a turn. So no cross-cell leakage reached any recorded number, and the Phase 6 real-model runs stand. The first V2-baseline attempt was stopped anyway, and is rerun isolated.
- **Fix**: `backend/app/state/runtime_paths.py` is one place for both.
  - `redis_endpoint()` parses `Config.REDIS_URL` for `StateService` and `WorkingMemoryStore`, and an empty URL disables Redis.
  - `runtime_state_db()` puts `state_cache.db` under `base_path` / `IDENTITY_BASE_PATH`, creates the directory, and migrates a legacy working-directory file once with SQLite's backup API, leaving the original for rollback.
  - `CognitiveService` routes the state file and both `AdaptiveWeightsStore`s through it.
  - The subconscious gets its own `state_cache_subconscious.db`, because both processes share the production volume.
  - BrainBench builds every service under `isolated_runtime()` (Redis off), including the proactive suite's second `StateService`.
  - Tests: `tests/test_runtime_state_paths.py`, 14 tests. 4 of them fail on the pre-fix code, which was checked by stashing the fix.
- **Operational note**: a production deploy picks up the new path on restart. The first start migrates each container's legacy `/app/state_cache.db` into `/app/data`. Nothing needs running by hand.
- **Status**: fixed (commit to follow in this change).

## Real-model resource baseline (not a finding, recorded for Phase 8)

BrainBench resources suite, `llm_augmented`, home-gpu `llama3.2:3b` (RTX 2060 SUPER), seed 1015 `private_minimalist` `1m`, 33 turns: turn latency P50 2.47 s, P95 3.22 s, P99 4.17 s, max 4.37 s (model time dominates; architecture-only overhead is about 3 ms per turn). Memory rows and vectors grew 5 -> 32 (about one per turn, linear), vocabulary 248 -> 422, run directory 0.56 -> 1.55 MB, peak RSS 164 -> 173 MiB.
