# 00 — Baseline V1: the brain as the code actually runs it

Verified against `main` at `dac8d0a` (2026-09-24) by reading the code and, where
marked **[measured]**, by running it. Where this disagrees with
`ARCHITECTURE.md` (a target design), this file describes what runs.

## Process topology

Five Python agents and two Rust agents talk only over NATS JetStream.

| Agent | Owns | Subscribes | Publishes |
|---|---|---|---|
| `brain_agent` (`backend/app/agents/brain_agent.py`) | `CognitiveService`, the turn loop, active turn id, generation task | `chat.input`, `vision.*`, `user.voice.properties`, `audio.playback.*`, `audio.stop`, `system.tick`, `memory.surfaced`, `audio.perception` | `chat.output`, `audio.stop`/`audio.resume`, `state.update`, `state.broadcast`, `telemetry.reflection` |
| `surfacing_agent` | proactive recall cadence, `recently_surfaced` | `chat.input`, `system.tick`, `state.update` | `memory.surfaced`, `agent.voice.modulation` |
| `subconscious_agent` | consolidation, monologue, dreams, proactive queue | `system.tick`, `chat.input`, `state.broadcast`, `state.presence`, `audio.perception`, `vision.description` | `chat.input` (source=subconscious), `state.subconscious` |
| `system_agent` | the heartbeat | — | `system.tick` every 60 s |
| `transport_agent` | LiveKit bridge | `audio.stream`, `audio.stop`, `audio.playback.visemes` | `audio.inbound`, `audio.playback.*`, `state.presence` |
| `voice-agent` (Rust) | TTS, active speaking turn, abort/duck flags | `chat.output`, `audio.stop`, `audio.resume`, `agent.voice.modulation`, … | `audio.stream`, `audio.playback.visemes` |
| `stt-agent` (Rust) | endpointing, speculative stop keywords | `audio.inbound` | `audio.perception`, speculative `audio.stop`, `chat.input` (turn_id **None**) |

`scripts/check_subject_wiring.py` passes; its allowlist records six known gaps
(`audio.pre_generate` is published but unreachable, `state.subconscious` and
`telemetry.reflection` have no consumer, `voice.segmentation_feedback` has no
publisher).

## The foreground turn

`chat.input` → `BrainAgent._process_chat_input_flow` (pacing sleep 300–900 ms)
→ `CognitivePipeline.execute`:

| Stage | What runs | LLM? |
|---|---|---|
| 2 | speculative-stop arbiter (`decision.is_speculative_stop_confirmed`) → `audio.stop`/`audio.resume` | no |
| 3 | perception: intent defaults to CHAT | no |
| 4 | appraisal (`AppraisalEngine.appraise`, Rust with Python mirror) | no |
| 5 | reappraisal prediction error → hormone bursts; `update_from_appraisal`; System-2 appraisal task (background) | background LLM |
| 6 | decision: deterministic short-circuits, else **LLM intent + ToM classifier**, MAUT goal scoring, behaviour tree | yes (fast model) |
| 8 | action: prompt assembly, memory rendering, streamed generation, validation, self-correction | yes |
| 9 | identity validation, optional second pass | sometimes |
| 10 | reflection (fact extraction, consolidation), rate-limited | yes |

Memory reaches a turn two ways: `memory.surfaced` from the surfacing agent
(asynchronous, kept in `CognitiveService.surfaced_memories`, last 5, **never
cleared between turns**) and, only if that list is empty, a synchronous
`search_memories` in `ActionService._surface_fallback_memories`. Both callers
pass `limit=3, refresh_on_recall=False`.

## Memory (V1)

* **Stores.** Postgres+pgvector (`memories`, `archived_memories`), Qdrant
  (`ai_friend_memories`, off whenever pytest is loaded), Neo4j entity graph,
  SQLite fallback for all of it. `TemporalMemoryStore` (belief validity,
  contradiction states) is constructed in `core.py` and **never called**.
* **Encode.** `add_memory`: exact duplicate → reinforce (`recall_count+1`,
  `importance=max`), else embed with Ollama `nomic-embed-text` (no task
  prefix), insert, upsert Qdrant. Consolidation writes one LLM summary per
  episode batch at a **flat importance 0.6** (`learning.py`).
* **Retrieve (V1 ranker).** `score = ln(n) − 0.5·ln(h+1) + 1.5·importance +
  0.15·(1−d_emo) + spacing + cos·(1+0.1·v·w−0.2·A·C) − 0.5·d_emo`, then
  `+5.0` per query word found **as a substring** (`DIRECT_CUE_BOOST`), a goal
  buffer boost of up to +1.8 and PageRank boost. Candidates: SQLite
  `ORDER BY last_recalled_at DESC LIMIT 20`; Postgres top-20 by the same
  ACT-R score in SQL; Qdrant top-20 by cosine.
* **Forget.** `apply_actr_decay` on consolidated message contents uses age
  since *creation*; pruning needs ~46 days (importance < 0.5) or ~338 days;
  archive hard-deletes after 30/180/720 days; importance ≥ 0.9 is never deleted.

## Affect (V1)

`AgentState` holds PAD (`mood`, `energy`, `dominance`), three trust
components, attachment, fatigue, and phasic dopamine/cortisol/adrenaline with
wall-clock half-lives. Per user turn (`update_from_appraisal`):

```
mood   ← 0.7·mood   + 0.3·(w1·G + w2·RI)        G = clamp(agent mood), RI = 0.5·mood
trust_integrity ← trust_integrity + 0.1·NA       NA = 1 unless a boundary word appears
```

so **the user's words never enter valence** [measured: mood stays exactly
constant for positive, negative and hostile scripts], and trust rises on any
message without a boundary word [measured: 0.83 after 60 hostile turns].
`ReappraisalEngine` then adapts `w1, w2` from a prediction error computed on
that same mood signal, persisted across restarts [measured: runaway to +1.0
from mood 0.6; permanent numbing (`w1=0.1`) after COMFORT turns]. Tick decay
pulls PAD to baseline with a 13.9 h half-life, driven by the tick message's
`interval` field rather than elapsed time.

## What is decorative in V1 (read but without measurable effect, or never read)

Measured with the retrieval ablation (E2): every ACT-R term except the cue
boost changes hit@3 by ≤ 0.01 at V1's scale — recency, frequency, spacing,
importance, both emotion terms, the goal buffer. By reading:
`GlobalControls.learning_gain`, `CapabilityLimitationModel.evaluate_directive`,
`PersonModel` trust updates, planning/
simulation/adapter-gate/provider-negotiator services, `TemporalMemoryStore`.

The full, evidence-linked problem list is `01-problems.md`.
