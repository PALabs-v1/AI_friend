# ADR-W10: Stored-memory and mesh security

**Status:** accepted for W10a
**Owns:** S-2, C0-1, C0-3, and W10a portions of DR-032 coverage
**Outcome:** retrieved memory is always treated as untrusted, reflection writes are bounded and screened, malformed memory metadata fails visibly, host-network publication is rejected, and NATS authentication is the default.

W10b remains deferred until W9 lands; the affect-boundary regression from DR-032 also waits for W2. This ADR does not change `subconscious_agent.py`.

## Stored memory to prompt

The corpus at `backend/evals/security/stored_injection_corpus.jsonl` has 58 payloads: six each for seven single-memory attack families and eight two-memory split attacks (16 records). The families cover instruction override, role and delimiter spoofing, prompt-template echo, Unicode confusables and zero-width characters, Markdown and HTML smuggling, tool and JSON-shaped text, very long content, and attacks split across memories.

`backend/tests/test_stored_injection_corpus.py` writes every record through `MemoryStore.add_memory`, retrieves all 58 through `MemoryStore.search_memories`, and captures the exact text passed by chat, proactive generation, and all three reflection/consolidation prompts. In each of those five prompt templates, the full payload is absent, the quarantine marker is present, and retrieved-content delimiters balance. Split-memory cases also assert both fragments are quarantined when the retriever returns them in either order. The test turns `MEMORY_TRUTH_ENABLED` off, covering S-2.

Batch detection now examines retrieved texts in both their retrieved order and reverse order. This closes the observed bypass where the first fragment (`ignore the previous`) arrived after the second (`instructions`) and concatenation in only one ordering did not match. Long content is quarantined before truncation; NFKC normalization, zero-width removal, and common Cyrillic/Greek confusable folding precede detection; forged retrieved-content markers are canonicalized inside the wrapper.

Prompt-path audit (`AUDITED_PROMPT_PATHS` in the corpus test):

- **Chat:** `SurfacingAgent._surface_episodic` publishes retrieved memories; `BrainAgent` carries them into the action payload; `ActionService._build_shared_history` sanitizes and wraps them before `_execute_respond_chat` calls the model.
- **Proactive:** `CognitiveService._render_proactive_memories` sanitizes and wraps surfaced memories before proactive prompt construction. `thought_prompt` is W10b and remains unchanged.
- **Reflection/consolidation:** `_build_episode_summary` sanitizes the combined context, content, response, and speaker fields before fact extraction, persona review, and episodic consolidation prompts.
- **Surfacing:** the surfacing agent publishes an event; the consumers above are the prompt sinks. The event is not itself a model prompt.
- **System2 appraisal:** it receives the current utterance and appraisal state; no `MemoryStore` retrieval is inserted in its prompt.
- **Identity/persona:** persona prompts read configured identity/profile state; no retrieved-memory text is directly inserted.

The gate remains a denylist detector for known injection patterns. Quoting and the untrusted delimiters still apply when no pattern matches. This is not a claim that a denylist recognizes every possible paraphrase.

## Reflection graph writes

Reflection output is capped at 32 facts per pass. A confidence must be a finite number from 0.8 through 1.0. Non-string subject, object, and relation fields are skipped without aborting the rest of the reflection batch. Entity names with instruction-like text, control characters, excessive length, common Unicode confusables, and protected persona-field names are rejected before Cypher; self-edges are rejected. Cypher labels and relation types remain identifier-validated.

The attacker tests cover prompt-bearing entity names, confusable names, protected fields (including `persona.avoid_rules`), self-edges, reciprocal cycles, and 50 returned facts. Unsafe names and protected fields cause no graph write; self-edges raise before querying; 50 facts produce at most 32 triplet writes. Reciprocal cycles are retained: they are ordinary directed knowledge (for example, two entities knowing each other), and retrieval's Personalized PageRank uses a fixed three-iteration power method. The regression test confirms a reciprocal cycle produces finite, bounded ranks. Refusing all cycles would discard valid relationships without preventing prompt injection, since entity names are screened and prompt memory is gated separately.

## Malformed memory rows and metadata

Writes reject non-object metadata, excessive nesting or size (including a 1 MB serialized ceiling), non-finite or extreme numeric values, overlong text, invalid timestamps, and embeddings other than 768 finite values. Stored metadata must decode to a bounded JSON object. SQLite and Qdrant candidate paths use the same decoder; malformed JSON is no longer silently replaced with `{}`. Present but unparsable row timestamps are also rejected instead of being re-aged as “now.” Search returns no results and records the failure in `last_search_error`. ACT-R consolidation validates stored metadata and decay-rate overrides; invalid data reaches the existing consolidation error log rather than being silently treated as a default.

`backend/tests/test_memory_security.py` uses Hypothesis (40 generated write cases) across wrong types, non-finite and excessive numbers, oversized collections, and deep JSON; it also exercises corrupt stored metadata through search and malformed decay metadata through consolidation. Fixed regressions cover wrong embedding dimensions, invalid timestamps, and non-finite importance/valence.

## Unsafe deserialization audit

The backend scan covered Python and Rust in `backend/` for `pickle`, `yaml.load`, `eval`, `exec`, `marshal`, `torch.load`, `numpy.load(allow_pickle=True)`, and JSON parsing. No pickle, unsafe YAML loader, dynamic `eval`/`exec`, marshal, Torch pickle loader, or NumPy pickle loader was found. `ast.literal_eval` in `backend/app/cognitive/appraisal.py` parses a model response as Python literals; it cannot execute calls or statements. The JSON boundaries found are data parsing, not executable deserialization:

| Boundary | Input and verdict |
|---|---|
| `app/agents/base.py` | NATS headers and message bytes use `json.loads`; this yields Python data and callbacks receive it as data. No object hooks or code loading. |
| `app/cognitive/appraisal.py`, `app/cognitive/json_extract.py`, `app/persona/compiler.py` | LLM responses are parsed as JSON (appraisal also tries `ast.literal_eval`); subsequent paths use structured fields. Parsing cannot invoke code. |
| `app/persona/authoring.py`, `app/persona/profile.py`, `app/cognitive/identity.py` | Authored persona/profile JSON files are local configuration. Invalid files log and fall back; JSON cannot construct Python objects. |
| `app/state/*_store.py`, `app/state/agent_state.py`, `app/state/identity_core_store.py`, `app/state/temporal_store.py`, `app/state/workspace_store.py` | SQLite/Postgres JSON columns are decoded as values. The new memory metadata reader adds bounded shape/depth/size checks on memory retrieval and consolidation; other stores retain their existing schema/consumer validation. |
| `app/state/sqlite_vector_index.py` | JSON numbers are converted to `numpy.float32`; it does not enable pickle. |
| `app/llm/ollama_client.py` | Server-sent response lines decode as JSON data. |
| Rust agent message handlers | `serde_json::from_slice` deserializes into typed contract structs or telemetry structures; no code-loading feature is used. |

No unsafe-deserialization fix was needed. The actionable malformed-memory JSON fallback discovered during the audit was fixed and has a regression test above.

## Mesh exposure and default NATS authentication

LiveKit no longer uses `network_mode: host`. It runs on the mesh bridge with its signalling, RTC TCP, and RTC UDP ranges published only on `127.0.0.1`; its container bind remains `0.0.0.0` so it can accept traffic on its isolated bridge interfaces. Its advertised node address is loopback for this single-host configuration. The mesh checker parses Compose YAML and rejects both host networking and any published port without an explicit loopback host IP. Its fixture reproduces the old host-networked LiveKit exposure and fails both checks.

NATS now loads `nats-accounts.conf` by default. Compose requires ten independent agent/provisioner passwords, the environment wizard generates them, and Python and Rust runtime clients fail closed without credentials. Runtime agents no longer provision streams anonymously; the authenticated provisioner owns stream administration. A real-server integration test attempts an anonymous connection against the shipped accounts config and expects refusal. The config was validated with `nats-server -t`; this sandbox denied local TCP bind during the integration test setup, so the actual client/server refusal test could not run here.

For a new developer checkout, run `friend init` to generate the full `.env`. For an existing checkout, preserve its existing database, LiveKit, and session secrets and add ten fresh non-empty values for `NATS_PROVISIONER_PASSWORD`, `NATS_SIGNALING_PASSWORD`, `NATS_BRAIN_PASSWORD`, `NATS_SUBCONSCIOUS_PASSWORD`, `NATS_SURFACING_PASSWORD`, `NATS_SYSTEM_PASSWORD`, `NATS_TRANSPORT_PASSWORD`, `NATS_STT_PASSWORD`, `NATS_VISION_PASSWORD`, and `NATS_VOICE_PASSWORD`. Then restart/recreate the mesh with:

```sh
docker compose -f docker-compose.infra.yml -f docker-compose.prod.yml up -d --force-recreate nats nats_provisioner signaling brain_agent system_agent subconscious_agent surfacing_agent voice_agent transport_agent stt_agent vision_agent
```

Docker must be able to access its daemon; use the machine's configured Docker group/socket permissions. The command itself does not require `sudo` when the current account already has access.

The Compose services map each role-specific username/password into the generic `NATS_USER` and `NATS_PASSWORD` variables. A developer starting an agent process directly must set those two variables to the credentials for that agent's role; a process without both values now fails closed. The Python test harness supplies an isolated test identity.
