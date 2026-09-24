# Current Architecture (as merged at `001069c`)

Architecture first, implementation second. This is what the brain *is*, independent of what V2 changed (that's `01-v2-delta.md`).

## Topology

Independent processes coordinated over NATS (JetStream where durability matters, core NATS for streaming audio):

- **brain_agent** (`backend/app/agents/brain_agent.py`) — the orchestrator. Owns turn lifecycle, calls into `CognitiveService`/`CognitivePipeline`, publishes chat output and audio-control events.
- **subconscious_agent** (`backend/app/agents/subconscious_agent.py`) — a separate process mirroring brain state via `state.broadcast`. Runs proactive-thought evaluation, reflection/consolidation, ACT-R decay, a continuous monologue (unconsumed), dream sequences (log-only), rest-phase replay.
- **surfacing_agent** (`backend/app/agents/surfacing_agent.py`) — episodic and semantic memory surfacing, publishes `memory.surfaced`.
- **vision/agent.py** — camera perception boundary.
- **transport_agent** (`backend/app/agents/transport_agent.py`) — bridges NATS audio topics to LiveKit tracks; the only place that currently *could* know when audio finished playing.
- **stt-agent** (Rust, `backend/crates/stt-agent`) — whisper.cpp / SenseVoice speech recognition, VAD, speculative barge-in detection.
- **voice-agent** (Rust, `backend/crates/voice-agent`) — TTS synthesis client, PCM streaming, DSP (reverb/attenuation), interruption/ducking.

Contracts are typed on both sides: `backend/app/contracts.py` (Python/Pydantic) and `backend/crates/contracts/src/lib.rs` (Rust), kept in parity by `test_rust_contract_fixtures.py`. `backend/tests/test_doc_drift.py` fails CI if any file path named in root `CLAUDE.md` stops resolving.

## Stores and their roles

| Store | Used for | Client |
|---|---|---|
| Postgres + pgvector (or SQLite fallback) | conversation history, memory rows, ACT-R scoring function `surface_actr_memories` | `app/state/conversation_store.py`, `memory_store.py`, `sqlite_fallback.py` |
| Qdrant | vector similarity search (optional — silently `None` if unreachable, always `None` under pytest) | `app/state/semantic_recall_store.py` |
| Neo4j | entity/relation graph, written by reflection; also persists agent state as a side channel | `app/state/graph_db.py` |
| Redis (or SQLite fallback) | working memory (last 8 turns, session vars), affect-state snapshot mirror | `app/state/working_memory_store.py`, `agent_state.py` |
| In-process SQLite vector index | fast candidate pool when running on the SQLite backend | `app/state/sqlite_vector_index.py` |
| `TemporalMemoryStore` | supersession/contradiction tracking — **constructed, never called** | `app/state/temporal_store.py` |

## Pipeline (per-turn, `CognitivePipeline.execute`, `pipeline.py:751`)

1. Extraction + VAP pre-generation check (threshold 0.7)
2. Conflict resolution (barge-in targeting, ADR-003)
3. `PerceptionService.perceive`
4. System 1 appraisal — Rust `cognitive_rust.compute_appraisal` or its Python fallback, **no LLM**
5. State update (PAD/trust/hormones) + System 2 LLM appraisal as a background task (arrives *after* stage 5, so it cannot inform the state update it would seem to precede)
6. `DecisionService.decide` (intent classification, MAUT goal scoring, behavior tree)
7. Payload prep + `PersonaPolicy.precheck`
8. `ActionService.execute` (response generation, one self-correction retry)
9. `validate_response` (persona/safety checks)
10. `reflection_needed` event → `trigger_reflection` (async)

`BackgroundScheduler` is preempted at the start of step 8 and resumed in a `finally` — the seam that is supposed to stop background cognition from starving the foreground turn, but nothing currently enqueues work on it (see Objective 9 findings).

## Path classification (reflex / interactive / deliberative / background)

| Path | Examples | Latency sensitivity |
|---|---|---|
| Reflex | `evaluate_deterministic_response` (boundary refusals, backchannels), `HeuristicIntentClassifier` (opt-in), barge-in stop targeting | must be near-instant; no LLM |
| Interactive | the main pipeline turn (stages 1-9 above) | must preserve conversational responsiveness; contains one LLM call (intent/ToM) plus the streamed response generation |
| Deliberative | System 2 semantic appraisal, self-correction retry, clarify/regulation lines | can trail the turn by design |
| Background | reflection (fact/persona/episodic consolidation), ACT-R decay, subconscious proactive-thought evaluation, dream/monologue | fully async; must not block a turn in progress |

No workstream in this cycle should move work across these tiers without re-measuring the latency budget for the tier it lands in (Interview Round 7 sets the actual numbers).

## Memory: write and read path

**Write** (`MemoryStore.add_memory`, `memory_store.py:1408`): dedupe/reinforce exact repeats → link graph entities → check for a content-polarity or valence-sign contradiction (`find_contradiction`) → embed via Ollama `nomic-embed-text` → insert row → upsert into Qdrant under the same UUID → invalidate L1 cache → update the lexicon. `contradicts_id` gets set automatically when a contradiction is detected, but **the old row is never modified** — nothing downstream reads `contradicts_id`, `valid_until`, or invokes `TemporalMemoryStore.apply_contradiction`. Supersession is stored but not enforced.

**Read** (`search_memories`, dispatches on `Config.MEMORY_RANKING_POLICY`, default `hybrid`): pull a 60-candidate similarity pool from whichever backend is active (Qdrant → SQLite vector index → pgvector HNSW), add archived-memory candidates, score with `hybrid_rank` (`z(cos) + 1.5·BM25/max + 0.2·z(ACT-R-base-level + 1.5·importance)`), materialize archive winners, and record a full trace (ids + per-term scores, never memory text) in `last_search_trace`. The legacy `actr_v1` policy still exists behind a config flag and adds PageRank spreading activation, pronoun-cue resolution, a goal buffer, and MRL/stress-narrowed pooling — all dropped from the default path (ADR-001).

## Affect: what actually drives mood

`emotional_bias` (the input to appraisal's goal-congruence term G) is set to **the agent's own current mood** (`pipeline.py:865`), not anything derived from what the user said. The appraisal fallback (`appraisal.py:206-227`, mirrored by the Rust extension) computes `G = clamp(emotional_bias)` and `relationship_impact = emotional_bias * 0.5`; the user's actual text (`event_content`) is used only for novelty scoring and boundary-keyword matching, never for valence. The one place the user's real sentiment is read — the LLM ToM call in `decision.py` — runs as part of intent classification, which happens at pipeline stage 6, *after* the affect update at stage 5. So on the synchronous path, mood cannot be moved by what the user says; it can only drift from its own previous value plus a slow System-2 background nudge.

## Trust: the arithmetic

Every `USER_MESSAGE` event runs (`agent_state.py:1342-1355`):
```
trust_benevolence += 0.1 * RI
trust_competence  += 0.1 * (0.6*G + 0.4*R)      # R = 1.0 for USER_MESSAGE
trust_integrity   += 0.1 * NA
```
`NA` (norm alignment) only drops below 1.0 if the message contains a substring of one of the seven `IMMUTABLE_CORE` boundary keywords (`profile.py:136`: "share", "user", "data", "adopt", "toxic", "behavior", "will" — after stripping short/common words). Plain hostility ("you're useless and stupid") matches none of them, so `NA = 1.0` and `trust_integrity` rises every single hostile turn. `trust_competence` is driven by the agent's own mood (`G`), not the user's behavior. Confirmed directly against the code and matches the affect-sim measurement in `docs/brain-research/01-problems.md` (trust ≈0.83 after 60 hostile turns).

## Identity/personality tiers (`persona/profile.py`)

- **IMMUTABLE** — a hardcoded Python constant (`IMMUTABLE_CORE`), refreshed from code every load (`identity.py:477`), never read from a file, never overridable.
- **CONSTITUTIONAL** — name, baseline PAD, rate coefficients, hormone half-lives, `base_tone`, `identity_summary`, `speech_patterns`, `traits`, `avoid`.
- **ADAPTIVE** — `relationship`, `initial_trust`/`initial_attachment`, up to 5 `adaptive_traits`, `speaking_style`.

`ReflectionService._consolidate_persona` (`learning.py:273`) routes proposed persona changes through `LearningGovernor` (blocks protected/constitutional keys) into `LearningReviewQueue.submit` when `LEARNING_REVIEW_REQUIRED=True` (the default). **Nothing in production ever calls `approve`** on that queue — it is in-memory (despite a docstring claiming durability) and dead-ends. Under default config, persona evolution is proposed but never applied.

## Voice lifecycle: what signals exist, and where completion is lost

Contract topics (`contracts.py` / `crates/contracts/src/lib.rs`): `audio.perception`, `audio.stop`, `audio.resume`, `chat.output` (carries `done`), `audio.stream` (raw PCM + `X-Latency-Meta` header), `audio.playback.progress` (`AudioPlaybackProgress{utterance_id, character_offset, word_index, completed}`), `audio.playback.backlog`, `audio.pre_generate`. There is no distinct STARTED event — onset is inferred from the first progress frame having `word_index==0, character_offset==0`.

The voice agent, on `chat.output.done`, only resets its own local state (`crates/voice-agent/src/main.rs:807-821`) — it never emits a trailer or done marker on `audio.stream`, which carries raw bytes only. `transport_agent.py` sets `is_done` **only when the payload is a dict with a `done` key** (`:333-337`); the raw-bytes production path never produces that shape, so `completed=True` never reaches `audio.playback.progress` in production, and the brain's COMPLETED `OutcomeRecord` branch (`brain_agent.py:1150-1177`) never fires outside tests.

## Barge-in sequence (ADR-003 targeting)

1. stt-agent emits a speculative `audio.stop` (`turn_id=None`) the instant it detects speech onset.
2. `brain_agent._on_chat_input` confirms it as `confirmed_user_speech` unless a grace window (0.15s) or an already-pending speculative intent applies.
3. The prior generation is cancelled and its in-flight reply snapshotted (`_SupersededReply`).
4. Pipeline stage 2 resolves the conflict and addresses the stop to the *interrupted* turn's id (not the new turn's id — this is what ADR-003 fixed from V1).
5. `_on_audio_stop` truncates the history row to what was actually heard, by row id.
6. voice-agent applies the stop (abort flag, ducking); transport flushes and rotates the LiveKit track.

Documented remaining gaps (ADR-003 "Known and not changed"): an ordinary (non-speculative) barge-in doesn't cut the history row; only one superseded-reply slot exists; several tight-timing interleavings (stop arriving after a new turn has already superseded it, a startle during pacing sleep, a redelivered `chat.input`) are unhandled. None of these are covered by property/fuzz testing today — there is no `hypothesis` or equivalent anywhere in the repo.
