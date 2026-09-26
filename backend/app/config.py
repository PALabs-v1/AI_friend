import os
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import (
    AliasChoices,
    Field,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_discovered_parent = Path(__file__).resolve().parent.parent.parent
if "AI_FRIEND_ENV_PATH" in os.environ:
    _env_file = Path(os.environ["AI_FRIEND_ENV_PATH"])
elif _discovered_parent == Path("/"):
    _env_file = Path("/app/.env")
else:
    _env_file = _discovered_parent / ".env"

_LLM_SOURCE_FIELDS = {
    "OLLAMA_URL": "ollama_url",
    "LLM_FAST_MODEL": "llm_fast_model",
    "LLM_CHAT_MODEL": "llm_chat_model",
    "LLM_REFLECTION_MODEL": "llm_reflection_model",
    "LLM_NUM_CTX": "llm_num_ctx",
    "LLM_INTENT_CLASSIFICATION_ENABLED": "llm_intent_classification_enabled",
}


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_env_file), env_file_encoding="utf-8", extra="ignore"
    )

    # Captured during construction, rather than reconstructed from global
    # environment state whenever the computed field is read.  That distinction
    # matters for tests and for callers that construct an isolated settings
    # object with `_env_file=None` or explicit constructor overrides.
    _llm_env_files: list[Path] = PrivateAttr(default_factory=list)
    _llm_sources: dict[str, str] = PrivateAttr(default_factory=dict)

    def __init__(self, **values: Any):
        explicit = {
            name
            for name in set(values) & set(_LLM_SOURCE_FIELDS)
            if values.get(name) not in (None, "")
        }
        requested_env_file = values.get("_env_file", _env_file)
        if requested_env_file is None:
            env_files: list[Path] = []
        elif isinstance(requested_env_file, (list, tuple)):
            env_files = [Path(item) for item in requested_env_file if item]
        else:
            env_files = [Path(requested_env_file)]

        super().__init__(**values)
        self._llm_env_files = env_files
        dotenv_fields: set[str] = set()
        for env_file in env_files:
            try:
                dotenv_fields.update(
                    key
                    for key, value in dotenv_values(env_file).items()
                    if value is not None
                )
            except (OSError, UnicodeError):
                continue
        self._llm_sources = {
            field_name: (
                "constructor"
                if env_name in explicit
                else "process_env"
                if env_name in os.environ
                else "env_file"
                if env_name in dotenv_fields
                else "code_default"
            )
            for env_name, field_name in _LLM_SOURCE_FIELDS.items()
        }

    DEBUG: bool = False
    # #162: the one signal `validate_no_placeholder_secrets_in_production`
    # gates on. Deliberately not derived from `DEBUG` (which defaults False in
    # every CI run and most local dev sessions too, so gating on it would fire
    # the placeholder check constantly where it shouldn't) or from `DATABASE_
    # URL`'s shape (which says nothing about whether this deployment is meant
    # to be reachable by anyone but its operator). Unset -- the default --
    # means the check never runs, so nothing about existing behavior changes
    # until an operator explicitly opts in.
    ENVIRONMENT: str = "development"
    # #160: `main.py` already reads this via `getattr(Config, "LOG_JSON",
    # False)` to pick JSON vs plain-text logging, but the field was never
    # actually declared here - `extra="ignore"` meant setting LOG_JSON=true
    # in .env had silently no effect at all, always falling through to the
    # getattr default. The JSON formatter itself (logging_config.py) was
    # already correct; only the toggle to reach it was dead.
    LOG_JSON: bool = False
    TESTING_CONSOLIDATION_BYPASS_SILENCE: bool = False
    ALLOWED_ORIGINS_STR: str = Field(default="*", alias="ALLOWED_ORIGINS")
    LAN_ONLY: bool = True
    BACKEND_ACCESS_KEY: str | None = None
    # C4: unchanged default (0.0.0.0) so nothing breaks today -- Docker
    # deployments need this regardless of LAN_ONLY, since Docker's port
    # publishing forwards to the container's network interface, not loopback.
    # This exists for the bare `python main.py` / no-Docker path, where an
    # operator who wants to restrict exposure (e.g. behind their own reverse
    # proxy) now has a lever instead of needing a code change.
    BACKEND_BIND_HOST: str = "0.0.0.0"  # nosec B104 - see the C4 comment above
    # H3: caps how many session tokens one client IP can mint per window.
    # BACKEND_ACCESS_KEY already gates *who* can call /token; this bounds how
    # often, since a valid key doesn't imply unlimited LiveKit room creation.
    TOKEN_RATE_LIMIT_MAX_REQUESTS: int = 60
    TOKEN_RATE_LIMIT_WINDOW_SECONDS: float = 60.0
    LAN_CORS_ORIGIN_REGEX: str = (
        r"^https?://("
        r"localhost|"
        r"127\.0\.0\.1|"
        r"127(?:\.\d{1,3}){3}|"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}|"
        r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
        r")(?::\d+)?$"
    )

    # NATS Configuration
    NATS_URL: str = "nats://127.0.0.1:4222"

    # Redis Configuration
    REDIS_URL: str = "redis://127.0.0.1:6379"

    # Qdrant Configuration
    QDRANT_HOST: str = "127.0.0.1"
    QDRANT_PORT: int = 6333

    # LiveKit Configuration
    LIVEKIT_URL: str = "ws://127.0.0.1:7880"
    # Browser-facing URL. In Compose this differs from LIVEKIT_URL, which is
    # resolvable only by services on the internal Docker network.
    LIVEKIT_PUBLIC_URL: str = "ws://127.0.0.1:7880"
    LIVEKIT_API_KEY: str | None = None
    LIVEKIT_API_SECRET: str | None = None

    # Memory & Personality
    DATABASE_URL: str | None = None
    # Refuse to hide a Postgres outage behind process-local SQLite state.
    # Default remains lenient for development and existing no-Postgres tests.
    ORGANISM_MODE_STRICT_STORAGE: bool = False
    PERSONALITY_SEED_PATH: str | None = None
    HISTORY_SEED_PATH: str | None = None
    # A user-authored persona (see app/persona/profile.py). Unset means "use the
    # PSYCH_* / AI_NAME defaults below", which is exactly the previous behaviour.
    PERSONA_PROFILE_PATH: str | None = None
    # The authored biography (see app/persona/biography.py). Unset means "walk
    # up for config/biography.md", the historical behaviour. Set, it can point
    # anywhere — which is the point: a biography is a real person's life, and
    # keeping it out of the repo should not require a code change.
    BIOGRAPHY_PATH: str | None = None
    # Where `IdentityManager` writes runtime `personality.json`/`history.json`
    # evolution. Unset means a `.identity_state/` directory beside (not inside)
    # the `app/` package — outside the git-tracked tree, unlike the historical
    # default of "beside the code", which made the package directory itself
    # writable state and let a save without a durable store dirty a tracked
    # file (see H2 / issue #113). Containers should point this at a mounted
    # volume, e.g. `/app/data`.
    IDENTITY_BASE_PATH: str | None = None
    # Whether a fresh write location with no existing personality.json/
    # history.json gets seeded from the shipped `PERSONALITY_SEED_PATH`/
    # `HISTORY_SEED_PATH` (or package-directory defaults) on first use. True in
    # every real deployment, so a fresh install still boots with the shipped
    # persona instead of an empty one now that the write default no longer
    # matches the seed location. The test suite disables this (like it already
    # disables `PERSONA_PROFILE_PATH` discovery) so a fresh per-session temp
    # directory stays genuinely empty rather than picking up the repo's seed.
    IDENTITY_SEED_ON_FIRST_BOOT: bool = True

    # Neo4j Graph Configuration
    NEO4J_URI: str | None = None
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str | None = None
    NEO4J_AUTH: str | None = None

    AI_NAME: str = "AI Friend"
    OLLAMA_URL: str = "http://127.0.0.1:11434"

    # Bring-your-own cloud API key for hardware that cannot host a local model.
    # "ollama" (the default) is local-first, on-device behavior. Setting this to
    # a third party (e.g. anthropic) sends conversation data off-device, so it
    # is never enabled by default and must be explicitly configured.
    LLM_PROVIDER: str = "ollama"
    ANTHROPIC_API_KEY: str | None = None

    LLM_FAST_MODEL: str = "llama3.2:3b"
    LLM_CHAT_MODEL: str | None = None
    LLM_REFLECTION_MODEL: str | None = None

    # LLM context window configuration. 8192 is sized to ensure system prompt and
    # persona identity fit comfortably within KV-cache constraints on home-GPU class
    # hardware (e.g. RTX 2060 Super 8GB) without front-truncation of system instructions.
    LLM_NUM_CTX: int = 8192

    LLM_STREAM_MAX_SECONDS: int = 120
    LLM_INTENT_CLASSIFICATION_ENABLED: bool = True
    INTENT_CLASSIFIER_BACKEND: Literal["llm", "heuristic"] = "llm"
    # Experimental typed realization: request a bounded structured envelope from the model.
    # False preserves the default streaming text contract.
    LLM_TYPED_REALIZATION_ENABLED: bool = False
    # Candidate selection and temporal memory grounding. Historical environment
    # names remain accepted at the settings boundary; runtime code uses one field.
    MEMORY_TRUTH_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("MEMORY_TRUTH_ENABLED", "PHASE_02_MEMORY_TRUTH"),
    )

    # Global-control scoring and emotion-regulation candidates. Canonical names
    # win when both spellings occur within the same settings source.
    AFFECT_CONTROL_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AFFECT_CONTROL_ENABLED", "PHASE_03_AFFECT_CONTROL"
        ),
    )

    # Authoritative workspace instance: production turns supply an authoritative workspace instance
    # to ActionIntent rather than falling back to (0, 0) -- see
    # state/session_state.py's `workspace_authoritative_enabled()`.
    WORKSPACE_AUTHORITATIVE: bool = True

    # P1-1: the control tier's JetStream consumer settings (system.tick).
    # Both were previously implicit -- a 30s server-default ack_wait and
    # UNLIMITED max_deliver -- while the tick callback ran a ~28s
    # consolidation inline before ack. That combination is what produced
    # duplicate consolidations and duplicate proactive utterances: the
    # callback outran the deadline and JetStream redelivered, forever.
    #
    # With consolidation dispatched to a worker, the callback's remaining
    # worst case is one short proactive-thought LLM call, so this deadline
    # is now a genuine liveness bound rather than a number chosen to
    # accommodate a defect -- which is exactly why the roadmap requires
    # P1-1 to land before P1-2 sizes the control tier.
    MESH_CONTROL_ACK_WAIT_S: float = 30.0
    # Bounded, unlike the JetStream default. A tick that fails repeatedly is
    # a bug to surface, not a message to retry forever.
    MESH_CONTROL_MAX_DELIVER: int = 3

    # Reflex tier consumer budget. `vision.facial_reflex` and `audio.stop` handlers both return
    # fast and synchronously by design -- a short ack_wait is therefore an honest latency budget,
    # ensuring stalled handlers are redelivered quickly.
    MESH_REFLEX_ACK_WAIT_S: float = 5.0
    MESH_REFLEX_MAX_DELIVER: int = 2
    MOCK_LLM_TEXT: bool = False

    # Measurement trace flag. When true, transport_agent, subconscious_agent, and ollama_client
    # emit timestamps and prompt digests for forensic latency and prefix-sharing analysis.
    # A prompt digest is recorded unconditionally when this is on; full prompt text is not.
    MEASURE_TRACE: bool = False
    # Literal prompt text capture for shared prefix analysis across cognitive turns.
    # Separate from MEASURE_TRACE to prevent accidental conversation logging.
    MEASURE_TRACE_FULL_PROMPTS: bool = False

    REFLECTION_ENABLED: bool = True
    SYSTEM2_APPRAISAL_ENABLED: bool = True
    # Minimum interval between reflection passes to prevent back-to-back LLM passes during
    # fast-paced conversation while still permitting multiple reflections per minute when warranted.
    REFLECTION_MIN_INTERVAL_SECONDS: float = 30.0

    # Rest-phase replay interval: periodic consolidation sweep that runs when the agent is idle
    # and in rest phase (night OR fatigue > 0.8). 1800s (30 minutes) avoids redundant rescoring.
    REST_PHASE_REPLAY_INTERVAL_SECONDS: float = 1800.0

    # Learning review governance: controls whether persona-trait suggestions from reflection
    # require governed review (LearningGovernor, cognitive/learning_governance.py) rather than
    # auto-applying directly via IdentityManager.evolve_persona.
    LEARNING_REVIEW_REQUIRED: bool = True
    # How many recent high-importance memories one rest-phase sweep samples
    # for re-scoring/pruning via the existing `apply_actr_decay` pipeline.
    REST_PHASE_REPLAY_LIMIT: int = 20
    REST_PHASE_REPLAY_MIN_IMPORTANCE: float = 0.5
    REST_PHASE_REPLAY_LOOKBACK_HOURS: int = 168  # 7 days

    PROACTIVE_ENABLED: bool = True
    PROACTIVE_IDLE_THRESHOLD_SECONDS: float = 7200.0
    PROACTIVE_COOLDOWN_SECONDS: float = 3600.0
    PROACTIVE_MIN_ENERGY: float = 0.2
    # Proactive turn-taking probability threshold: 0.5 matches the midpoint of the
    # turn_taking_probability formula (0.5 + 0.3*D - 0.1*F + 0.2*V).
    PROACTIVE_MIN_TURN_PROBABILITY: float = 0.5
    PROACTIVE_DEBUG_THRESHOLD_OVERRIDE: str | None = None
    # Quiet hours are a conservative fallback until the brain has enough of
    # the user's own turn timestamps to infer active hours.
    PROACTIVE_QUIET_START_HOUR: int = 22
    PROACTIVE_QUIET_END_HOUR: int = 6
    PROACTIVE_ACTIVITY_HISTORY_MINIMUM: int = 8
    PROACTIVE_USEFUL_IMPORTANCE_MIN: float = 0.45
    PROACTIVE_SELF_DIRECTED_IMPORTANCE_MIN: float = 0.75
    # Three ignored raises drive resurfacing probability to exactly zero;
    # each is reviewed after a day, avoiding a fast tick-driven penalty.
    PROACTIVE_IGNORE_ZERO_AFTER: int = 3
    PROACTIVE_IGNORE_REVIEW_SECONDS: float = 86400.0

    PSYCH_ALPHA: float = 0.3
    PSYCH_BETA: float = 0.5
    PSYCH_GAMMA: float = 0.2
    PSYCH_DELTA: float = 0.1
    PSYCH_EPSILON: float = 0.03
    PSYCH_LAMBDA_DECAY: float = 0.05

    ACTR_DECAY_RATE: float = 0.5
    ACTR_SPREAD_WEIGHT: float = 1.0
    ACTR_EMOTION_WEIGHT: float = 0.5

    # Brain V2 retrieval (docs/brain-research/adr/ADR-001). "hybrid" selects a
    # vector-similarity candidate pool and ranks it with z-scored relevance +
    # whole-word BM25 + a small ACT-R history prior (app/state/memory_ranking.py).
    # "actr_v1" is the previous ranker, kept for A/B comparison and rollback.
    MEMORY_RANKING_POLICY: Literal["hybrid", "actr_v1"] = "hybrid"
    # Candidates ranked per query, chosen by cosine similarity. 20 and 60 were
    # within noise on the benchmark; 60 had the better worst case.
    MEMORY_CANDIDATE_POOL: int = 60
    # SQLite has no vector index, so the hybrid path computes similarity for
    # up to this many most-recently-touched rows (in Rust) and keeps the top
    # MEMORY_CANDIDATE_POOL. V1 scored only the 20 most recent rows, which is
    # why a memory older than 20 newer ones could never be recalled.
    MEMORY_SQLITE_SCAN_LIMIT: int = 5000

    MAUT_W_GOAL: float = 0.35
    MAUT_W_EMOTION: float = 0.25
    MAUT_W_IDENTITY: float = 0.20
    MAUT_W_CONTEXT: float = 0.20
    INTENT_PERSISTENCE_RATE: float = 0.5
    CONTEXT_SHIFT_THRESHOLD: float = 0.6

    REAPPRAISAL_ENABLED: bool = True
    REAPPRAISAL_LEARNING_RATE: float = 0.05
    # Brain V2 (docs/brain-research/adr/ADR-002): adapting the appraisal->
    # valence weights (w1, w2) from reappraisal outcomes is OFF by default.
    # Measured with the real AgentState/AppraisalEngine/ReappraisalEngine
    # (`python -m evals.cognitive affect`): it drives the per-turn valence
    # loop gain above 1 (mood saturates at +1.0 and stays there), and any
    # COMFORT turn drives w1 to its floor, persisted across restarts, so
    # one sad conversation numbs the agent for good. The prediction error
    # itself still fires the dopamine/cortisol bursts; only the weight
    # update and hydration of previously learned weights are gated.
    REAPPRAISAL_WEIGHT_LEARNING_ENABLED: bool = False

    RUNTIME_AUTO_BOOTSTRAP: bool = True
    RUNTIME_BOOTSTRAP_RETRIES: int = 12
    OLLAMA_REQUIRED_MODELS_STR: str = Field(default="", alias="OLLAMA_REQUIRED_MODELS")

    # P1-6: Q-M2-2 answered SQLite as emergency-only, not a supported runtime
    # mode. A Postgres connection failure at bootstrap defaults to refusing
    # to start under ENVIRONMENT=production rather than silently downgrading
    # (losing pgvector) into a mode nobody chose to run. Set true only for a
    # deliberate degraded deployment, not as a standing default.
    ALLOW_SQLITE_FALLBACK: bool = False
    # Written by runtime_bootstrap.py (a different process than the backend
    # API) when the SQLite fallback is entered, so /health can surface it,
    # and removed again once Postgres answers. Same pattern as
    # VISION_HEALTH_FILE below.
    #
    # Scope, stated plainly: bootstrap runs inside the brain_agent container
    # under docker-compose.prod.yml, while /health is served by main.py
    # elsewhere, so this path only carries the signal between them if it is
    # on a shared mount. It is not the primary alarm and is not relied on to
    # be -- the ERROR log and the production fail-closed are, and both work
    # regardless of topology. Point this at a shared volume to get the
    # /health flag in a multi-container deployment.
    SQLITE_FALLBACK_HEALTH_FILE: str = "/tmp/sqlite_fallback_active"  # nosec B108 - shared-volume health signal, not a secret path

    SOVITS_URL: str = "http://127.0.0.1:9871"
    STT_LANGUAGE: str = "en"
    TTS_LANGUAGE: str = "en"

    CUSTOM_GPT_PATH: str = "GPT_weights/ai_friend_voice.ckpt"
    CUSTOM_SOVITS_PATH: str = "SoVITS_weights/ai_friend_voice.pth"

    # Vision / VLM Configuration
    VLM_MODEL: str = "moondream"
    VLM_ENABLED: bool = True
    VLM_APPRAISAL_INTERVAL: float = 5.0
    VLM_HABITUATION_THRESHOLD: float = 0.005
    VLM_PROMPT: str = (
        "Describe what you see in this image briefly. Focus on what the user is doing."
    )
    # Touched on every successful frame capture so a container healthcheck can
    # probe the real path rather than mere process liveness (see finding E1).
    VISION_HEALTH_FILE: str = "/tmp/vision_agent_healthy"  # nosec B108 - same as SQLITE_FALLBACK_HEALTH_FILE above

    # CPU-only facial reflex channel (app/vision/reflex.py). Deliberately independent of
    # VLM_ENABLED/VISION_SUSPEND_DURING_TURN -- costs zero VRAM and samples continuously
    # across cognitive turns.
    FACIAL_REFLEX_ENABLED: bool = True
    # Empty means "resolve relative to app/vision/agent.py's own location" --
    # see VisionAgent.__init__ -- not the process cwd, avoiding relative path resolution bugs.
    FACIAL_REFLEX_MODEL_PATH: str = ""

    # Suspends VLM appraisal during cognitive turns to prevent GPU contention with the
    # conversational LLM, eliminating decode throughput degradation on single-GPU deployments.
    VISION_SUSPEND_DURING_TURN: bool = True
    # Circuit breaker for VLM inference: consecutive-failure threshold followed by cooldown,
    # preventing repetitive stalls on failed frames or unready VLM services.
    VLM_BREAKER_FAILURE_THRESHOLD: int = 3
    VLM_BREAKER_COOLDOWN_S: float = 30.0

    # Pinhole-camera focal length calibration in pixels. VISION_FOCAL_PX is calibrated for
    # VISION_FOCAL_REFERENCE_WIDTH_PX (the normalized width from CameraLink and ScreenLink).
    # Default assumes ~60 degree horizontal FOV typical of laptop webcams.
    VISION_FOCAL_PX: float = 443.0
    VISION_FOCAL_REFERENCE_WIDTH_PX: int = 512

    # Vector embedding batch size: nomic-embed-text achieves optimal batch throughput
    # at batch size 32 on local inference.
    EMBEDDING_BATCH_SIZE: int = 32

    # Salience-gated visual episodic memory. Screen captures are privacy-sensitive and
    # receive a hard TTL instead of graded ACT-R decay.
    VISUAL_SCREEN_TRACE_TTL_H: float = 24.0
    # A visual trace is stored only when the frame is perceptually novel
    # (VisualAppraisalService.last_frame_was_novel), the appraisal produced
    # a description, and the moment was affectively significant -- current
    # arousal or |valence| clears one of these two thresholds.
    VISUAL_MEMORY_AROUSAL_THRESHOLD: float = 0.55
    VISUAL_MEMORY_VALENCE_THRESHOLD: float = 0.15

    # Half-life of a phasic hormone burst, in seconds. Real phasic bursts last
    # only hundreds of milliseconds; these are the *felt* afterglow at
    # conversational timescale, so both are deliberately much slower than that
    # onset speed and much faster than the ALMA mood decay (PSYCH_LAMBDA_DECAY,
    # hours). These are now only the
    # *defaults* a PersonaProfile inherits when a persona file does not name
    # them (see app/persona/profile.py); AgentState reads the persona's values,
    # not these. Cortisol's is the longer of the two deliberately: an acute
    # stress response outlasts a reward burst.
    #
    # Cortisol phasic half-life: 4500s (~75 minutes) aligns with measured human
    # cortisol plasma half-life (~66-90 minutes).
    DOPAMINE_PHASIC_HALFLIFE_S: float = 90.0
    CORTISOL_PHASIC_HALFLIFE_S: float = 4500.0
    # Adrenaline sits between dopamine's 90s and cortisol's 4500s, governing the most
    # conversationally visible reactive responses (startle, interruption, shock) which fade
    # over minutes.
    ADRENALINE_PHASIC_HALFLIFE_S: float = 120.0

    SAMPLE_RATE: int = 32000
    BINARY_SUBJECTS: list[str] = ["audio.inbound", "audio.stream"]
    SYSTEM_TICK_INTERVAL: int = 60
    FEEDBACK_ALPHA: float = 0.70
    TRANSPORT_AUDIO_QUEUE_SIZE: int = 256
    VOICE_FILLER_MIN_INTERVAL_SECONDS: float = 1.5
    VOICE_FILLER_MAX_PLAYBACK_BACKLOG: int = 4
    # Voice filler latency threshold: 1.2s ensures speculative fillers only fire when
    # time-to-first-token experiences a perceptible delay above baseline generation overhead.
    VOICE_FILLER_THRESHOLD: float = 1.2
    # Barge-in onset grace period: 0.15s protects against false barge-in triggers
    # immediately after speech audio starts playing.
    BARGE_IN_ONSET_GRACE_S: float = 0.15

    GRAPH_CACHE_TTL: int = 300
    MIN_PERCEPTION_CONFIDENCE: float = 0.55
    STATE_SENSORY_WEIGHT: float = 0.20
    STATE_SENSORY_PERSIST_INTERVAL: float = 2.0

    # L5: pydantic-settings already validates *type* (a non-numeric
    # TOKEN_RATE_LIMIT_WINDOW_SECONDS fails to load at all), but not *range* -
    # a negative timeout or a zero halflife loads fine and only breaks
    # something at runtime. This is deliberately a short, curated list of
    # fields where an out-of-range value causes a specific concrete failure
    # (division by zero in decay math, a busy-loop tick, a rate limiter that
    # blocks everything or nothing), not an exhaustive sweep of every numeric
    # setting - most of the ~40 others have no failure mode worth guarding.
    _POSITIVE_FLOAT_FIELDS = (
        "DOPAMINE_PHASIC_HALFLIFE_S",
        "CORTISOL_PHASIC_HALFLIFE_S",
        "ADRENALINE_PHASIC_HALFLIFE_S",
        "TOKEN_RATE_LIMIT_WINDOW_SECONDS",
        "LLM_STREAM_MAX_SECONDS",
    )
    _NON_NEGATIVE_FLOAT_FIELDS = ("ACTR_DECAY_RATE",)
    _POSITIVE_INT_FIELDS = (
        "SYSTEM_TICK_INTERVAL",
        "TOKEN_RATE_LIMIT_MAX_REQUESTS",
        "TRANSPORT_AUDIO_QUEUE_SIZE",
        "MEMORY_CANDIDATE_POOL",
        "MEMORY_SQLITE_SCAN_LIMIT",
    )

    @model_validator(mode="after")
    def validate_numeric_ranges(self):
        for name in self._POSITIVE_FLOAT_FIELDS:
            value = getattr(self, name)
            if not (value > 0):
                raise ValueError(f"{name} must be > 0 (got {value!r})")
        for name in self._NON_NEGATIVE_FLOAT_FIELDS:
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0 (got {value!r})")
        for name in self._POSITIVE_INT_FIELDS:
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"{name} must be >= 1 (got {value!r})")
        if not (0 < self.QDRANT_PORT <= 65535):
            raise ValueError(
                f"QDRANT_PORT must be a valid port (got {self.QDRANT_PORT!r})"
            )
        return self

    # #162: the literal placeholder strings this repo's own `.env.example`
    # ships. Deliberately this narrow set, not a generic "looks like a weak
    # password" heuristic -- a heuristic strong enough to catch real weak
    # passwords is also strong enough to reject a legitimate one that happens
    # to contain a common word, and a false positive here means production
    # refuses to boot. This only catches the exact templates a deployment gets
    # by copying `.env.example` and forgetting to edit it, which is the
    # failure mode the issue describes.
    _PLACEHOLDER_SECRET_MARKERS = (
        "your_password_here",
        "your_graph_password_here",
        "your_api_key_here",
        "your_api_secret_here",
    )
    # P0-1: the LiveKit dev credential that was committed in livekit.yaml's
    # `keys:` block (now removed). Catching it here too means a deployment
    # still carrying it in LIVEKIT_API_KEY/SECRET -- e.g. via an old .env
    # copied forward -- also refuses to boot.
    #
    # These are matched WHOLE, not as substrings, unlike the markers above.
    # "devkey" is only six characters; substring-matching it would reinstate
    # exactly the heuristic the comment above rejects, and a false positive
    # here means production refuses to start. A real credential is never
    # equal to one of these, so equality is both sufficient and safe.
    _PLACEHOLDER_SECRET_EXACT = (
        "devkey",
        "secretsecretsecret",
    )
    # Only the fields that gate a real, network-reachable service if left at
    # the shipped placeholder -- Postgres/Neo4j auth and the LiveKit room
    # credentials. Optional integrations (e.g. Anthropic) are
    # deliberately excluded: a placeholder there disables a feature, it
    # doesn't expose one.
    _SECRET_BEARING_FIELDS = (
        "DATABASE_URL",
        "NEO4J_PASSWORD",
        "NEO4J_AUTH",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    @model_validator(mode="after")
    def validate_no_placeholder_secrets_in_production(self):
        if self.ENVIRONMENT != "production":
            return self
        for name in self._SECRET_BEARING_FIELDS:
            value = getattr(self, name, None)
            if not value:
                continue
            if (
                any(marker in value for marker in self._PLACEHOLDER_SECRET_MARKERS)
                or value.strip() in self._PLACEHOLDER_SECRET_EXACT
            ):
                raise ValueError(
                    f"{name} still contains a placeholder value from .env.example "
                    "-- refusing to start with ENVIRONMENT=production. Set a real "
                    "secret before deploying."
                )
        return self

    @model_validator(mode="after")
    def set_defaults(self):
        if not self.LLM_CHAT_MODEL:
            self.LLM_CHAT_MODEL = self.LLM_FAST_MODEL
        if not self.LLM_REFLECTION_MODEL:
            self.LLM_REFLECTION_MODEL = self.LLM_CHAT_MODEL
        return self

    @field_validator("DEBUG", mode="before")
    @classmethod
    def validate_debug(cls, v: object) -> bool:
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in {"1", "true", "t", "yes", "y", "debug", "development", "dev"}:
                return True
            if v_lower in {
                "0",
                "false",
                "f",
                "no",
                "n",
                "release",
                "production",
                "prod",
                "",
            }:
                return False
        return bool(v)

    @field_validator("LIVEKIT_URL")
    @classmethod
    def normalize_livekit_scheme(cls, v: str) -> str:
        """E3: both the frontend and transport_agent hand this straight to
        LiveKit's room.connect(), which needs ws(s)://, not http(s)://. Normalize
        here so an existing .env carrying the old http:// default (from before
        this fix) self-heals instead of requiring every deployment to be
        manually edited.
        """
        if v.startswith("https://"):
            return "wss://" + v[len("https://") :]
        if v.startswith("http://"):
            return "ws://" + v[len("http://") :]
        return v

    @field_validator("LIVEKIT_PUBLIC_URL")
    @classmethod
    def normalize_livekit_public_scheme(cls, v: str) -> str:
        if v.startswith("https://"):
            return "wss://" + v[len("https://") :]
        if v.startswith("http://"):
            return "ws://" + v[len("http://") :]
        return v

    @field_validator("EMBEDDING_BATCH_SIZE")
    @classmethod
    def validate_embedding_batch_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE must be positive")
        return v

    @field_validator("LLM_NUM_CTX")
    @classmethod
    def validate_llm_num_ctx(cls, v: int) -> int:
        if v < 4:
            raise ValueError("LLM_NUM_CTX must be at least 4")
        return v

    @field_validator("VISUAL_SCREEN_TRACE_TTL_H")
    @classmethod
    def validate_visual_trace_ttl(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("VISUAL_SCREEN_TRACE_TTL_H must be positive")
        return v

    @field_validator(
        "VISUAL_MEMORY_AROUSAL_THRESHOLD", "VISUAL_MEMORY_VALENCE_THRESHOLD"
    )
    @classmethod
    def validate_visual_memory_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("visual-memory thresholds must be between 0 and 1")
        return v

    # F4: these were previously computed in ConfigMeta.__getattr__, which
    # special-cased these two names and silently delegated every other
    # attribute straight to config_instance - a surprising place to look for
    # derived config, and easy to miss when adding a third computed value.
    # Real computed_field properties are visible on AppSettings itself, so
    # ConfigMeta's job shrinks to "look up the instance," nothing more.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        return self.ALLOWED_ORIGINS_STR.split(",")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def OLLAMA_REQUIRED_MODELS(self) -> list[str]:
        if self.OLLAMA_REQUIRED_MODELS_STR.strip():
            return [
                model.strip()
                for model in self.OLLAMA_REQUIRED_MODELS_STR.split(",")
                if model.strip()
            ]
        # set_defaults() (a model_validator) already backfills these from
        # LLM_FAST_MODEL, but that invariant isn't visible to mypy here.
        models = [
            self.LLM_CHAT_MODEL or self.LLM_FAST_MODEL,
            self.LLM_FAST_MODEL,
            self.LLM_REFLECTION_MODEL or self.LLM_FAST_MODEL,
            "nomic-embed-text",
        ]
        if self.VLM_ENABLED:
            models.append(self.VLM_MODEL)
        return list(dict.fromkeys(models))

    # HUMANOID_ARCHITECTURE_RESEARCH.md Phase 0: the repo has three other LLM
    # config authorities beyond this class (Compose's own inline fallbacks,
    # `.env.example`, and per-host `EnvironmentFile=` units), and the ledger's
    # own 2026-09-02 entries record two separate incidents where a value read
    # from the wrong one of those was mistaken for "the deployed model." This
    # is the one place that states what *this process* actually resolved and
    # which file it read to get there, so a caller (a startup log line, an
    # eval report) can say so plainly instead of that being re-derived by
    # hand each time. Deliberately a snapshot, not a new settings surface --
    # every value here already exists as its own field; this only names their
    # source together.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def LLM_PROVENANCE(self) -> dict[str, Any]:
        # pydantic-settings resolves constructor values before process
        # environment, the configured dotenv file, and field defaults. Report
        # the winner for each value instead of labelling every value with the
        # dotenv path. A Compose interpolation or systemd EnvironmentFile is
        # intentionally represented as ``process_env``: by the time Python
        # starts, that is the only source this process can prove.
        sources = dict(self._llm_sources)

        # These two values are filled from LLM_FAST_MODEL by set_defaults(),
        # so an absent explicit value has a derived rather than default source.
        if sources["llm_chat_model"] == "code_default" and self.LLM_CHAT_MODEL:
            sources["llm_chat_model"] = "derived_from_llm_fast_model"
        if (
            sources["llm_reflection_model"] == "code_default"
            and self.LLM_REFLECTION_MODEL
        ):
            sources["llm_reflection_model"] = "derived_from_llm_chat_model"

        return {
            "env_file": (str(self._llm_env_files[-1]) if self._llm_env_files else None),
            "env_file_exists": bool(self._llm_env_files)
            and all(env_file.exists() for env_file in self._llm_env_files),
            "llm_chat_model": self.LLM_CHAT_MODEL,
            "llm_fast_model": self.LLM_FAST_MODEL,
            "llm_reflection_model": self.LLM_REFLECTION_MODEL,
            "llm_num_ctx": self.LLM_NUM_CTX,
            "llm_intent_classification_enabled": self.LLM_INTENT_CLASSIFICATION_ENABLED,
            "ollama_url": self.OLLAMA_URL,
            "precedence": [
                "constructor",
                "process_env",
                "env_file",
                "code_default",
            ],
            "sources": sources,
        }


config_instance = AppSettings()


class ConfigMeta(type):
    def __getattr__(cls, name):
        return getattr(config_instance, name)


class Config(metaclass=ConfigMeta):
    @staticmethod
    def validate():
        # Validation is now handled by Pydantic during instantiation.
        pass
