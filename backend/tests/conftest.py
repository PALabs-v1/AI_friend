import os
import sys
import tempfile

# Set fallback environment variables for testing before any app modules are loaded
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
os.environ.setdefault("NEO4J_PASSWORD", "strong_ci_test_password")
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7687")
os.environ.setdefault("LIVEKIT_API_KEY", "dummy_key")
os.environ.setdefault("LIVEKIT_API_SECRET", "dummy_secret")
# Unit tests use the in-memory NATS simulator, but the production clients now
# correctly require an explicit identity even when no server is contacted.
os.environ["NATS_USER"] = "test_agent"
os.environ["NATS_PASSWORD"] = "test-only-password"

# Point persona discovery at nothing for the whole suite.
#
# `IdentityManager` searches upward for `config/persona.toml`, which is right in
# production and wrong here: any test that builds one with default arguments
# would otherwise inherit whatever character happens to be checked out beside
# the code — so the suite's results would depend on the repo's own persona file,
# and editing that file could turn tests red.
#
# This was also the reason the suite wrote to the tracked `app/personality.json`.
# That half is fixed properly now: `save()` writes to `agent_configs` whenever a
# durable store is attached, and only falls back to the JSON files when there is
# nowhere better. The env var stays for the isolation, which is its own reason.
#
# Tests that exercise authoring pass an explicit path, so this disables ambient
# discovery without disabling the feature.
os.environ.setdefault(
    "PERSONA_PROFILE_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_no_persona_file_here.toml"
    ),
)

# Keep identity writes out of the source tree.
#
# `IdentityManager` defaults `base_path` to the package directory, so
# `personality.json` and `history.json` — both **tracked in git** — are written
# by anything that saves without a durable store attached. The suite did exactly
# that: `test_subconscious_consolidation` builds a `ReflectionService` with no
# identity manager, and `_consolidate` → `evolve_persona` → `save()` rewrote the
# tracked file on every run, dirtying the working tree.
#
# A temp directory per session, so a test that saves is writing somewhere it is
# allowed to. Tests that care about the files pass an explicit `base_path`.
os.environ.setdefault("IDENTITY_BASE_PATH", tempfile.mkdtemp(prefix="test-identity-"))

# Disable first-boot seeding for the same reason `PERSONA_PROFILE_PATH` above
# points at nothing: a fresh write directory would otherwise get seeded from
# the repo's own shipped `personality.json`/`history.json` (#113), and the
# suite's results would then depend on the repo's persona content instead of
# genuinely starting empty. Real deployments want the seed; the suite wants
# isolation.
os.environ.setdefault("IDENTITY_SEED_ON_FIRST_BOOT", "false")

import asyncio
import re
import sqlite3
import types
from dataclasses import dataclass
from enum import Enum
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add the backend directory to Python path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


# =====================================================================
# 📦 HIGH-FIDELITY IN-MEMORY NATS SIMULATOR
# =====================================================================


class MockJSM:
    async def add_stream(self, config=None, **params):
        # Real JetStreamManager.add_stream takes `config: StreamConfig` OR
        # `**params` (e.g. name=, subjects=) -- app code uses both call
        # shapes (see nats_streams.py and BaseAgent._bootstrap_mesh), so
        # the mock has to accept both rather than only the older one.
        return None

    async def stream_info(self, name):
        # Return a mock object with config.subjects
        class MockConfig:
            def __init__(self):
                self.subjects = []

        class MockInfo:
            def __init__(self):
                self.config = MockConfig()

        return MockInfo()

    async def update_stream(self, config):
        return None


class MockMessage:
    def __init__(self, subject, data, headers=None):
        self.subject = subject
        self.data = data
        self.headers = headers

    async def ack(self):
        pass

    async def nak(self):
        pass


class MockJetStream:
    def __init__(self, connection):
        self.connection = connection

    async def publish(self, subject, data, headers=None):
        self.connection._trigger(subject, data, headers)
        await self.connection.drain()

    async def subscribe(self, subject, cb, durable=None, **kwargs):
        self.connection._subscribe(subject, cb)


class MockNATSConnection:
    def __init__(self):
        self.subscribers = {}
        self._js = MockJetStream(self)
        self.pending_tasks = set()

    def jetstream(self):
        return self._js

    def jsm(self):
        return MockJSM()

    def _subscribe(self, subject, cb):
        self.subscribers.setdefault(subject, []).append(cb)

    def _trigger(self, subject, data, headers=None):
        async def run_callback(cb, msg):
            try:
                await cb(msg)
            except Exception:
                import logging

                logging.getLogger("MockNATS").exception("Subscriber callback failed")
                raise

        msg = MockMessage(subject, data, headers)
        for sub_subj, callbacks in self.subscribers.items():
            matched = False
            if (
                sub_subj == subject
                or sub_subj.endswith(".>")
                and subject.startswith(sub_subj[:-1])
                or (
                    sub_subj.endswith(".*")
                    and subject.split(".")[:-1] == sub_subj.split(".")[:-1]
                )
                or sub_subj == ">"
            ):
                matched = True

            if matched:
                for cb in callbacks:
                    task = asyncio.create_task(run_callback(cb, msg))
                    self.pending_tasks.add(task)
                    task.add_done_callback(self.pending_tasks.discard)

    async def drain(self):
        if self.pending_tasks:
            # Gather all pending subscriber tasks to ensure deterministic completion
            await asyncio.gather(*list(self.pending_tasks), return_exceptions=True)

    async def close(self):
        await self.drain()


# Create mock module objects for nats and its submodules
nats_module = types.ModuleType("nats")


async def mock_connect(nats_url, **kwargs):
    return MockNATSConnection()


nats_module.connect = mock_connect

nats_errors_module = types.ModuleType("nats.errors")


class NoRespondersError(Exception):
    pass


class TimeoutError(Exception):
    pass


nats_errors_module.NoRespondersError = NoRespondersError
nats_errors_module.TimeoutError = TimeoutError

nats_js_module = types.ModuleType("nats.js")

nats_js_errors_module = types.ModuleType("nats.js.errors")


class BadRequestError(Exception):
    pass


class ServiceUnavailableError(Exception):
    pass


class NotFoundError(Exception):
    pass


nats_js_errors_module.BadRequestError = BadRequestError
nats_js_errors_module.ServiceUnavailableError = ServiceUnavailableError
nats_js_errors_module.NotFoundError = NotFoundError

nats_js_api_module = types.ModuleType("nats.js.api")


class DeliverPolicy:
    ALL = "all"
    LAST = "last"
    NEW = "new"


# P1-1/P1-2: real nats-py's ConsumerConfig/StreamConfig are dataclasses and
# StorageType/RetentionPolicy/DiscardPolicy are str Enums (nats/js/api.py).
# Mirrored here, not imported for real -- this whole module exists so the
# suite never touches a real NATS connection, which means it must also
# never touch the real `nats` package, since importing the real
# `nats.js.api` alongside this file's fake `sys.modules["nats"]` would bind
# `ConsumerConfig`'s dataclass machinery to a parent module that isn't
# actually there. Field names and defaults match the real classes for the
# fields this codebase constructs; anything unused by app code is omitted
# rather than chased for completeness.
class StorageType(str, Enum):
    FILE = "file"
    MEMORY = "memory"


class RetentionPolicy(str, Enum):
    LIMITS = "limits"
    INTEREST = "interest"
    WORK_QUEUE = "workqueue"


class DiscardPolicy(str, Enum):
    OLD = "old"
    NEW = "new"


@dataclass
class ConsumerConfig:
    name: str | None = None
    durable_name: str | None = None
    deliver_policy: str | None = DeliverPolicy.ALL
    ack_wait: float | None = None
    max_deliver: int | None = None


@dataclass
class StreamConfig:
    name: str | None = None
    subjects: list | None = None
    retention: RetentionPolicy | None = None
    max_bytes: int | None = None
    discard: DiscardPolicy | None = DiscardPolicy.OLD
    max_age: float | None = None
    storage: StorageType | None = None


nats_js_api_module.DeliverPolicy = DeliverPolicy
nats_js_api_module.StorageType = StorageType
nats_js_api_module.RetentionPolicy = RetentionPolicy
nats_js_api_module.DiscardPolicy = DiscardPolicy
nats_js_api_module.ConsumerConfig = ConsumerConfig
nats_js_api_module.StreamConfig = StreamConfig

# Register them in sys.modules to satisfy python's package import system
sys.modules["nats"] = nats_module
sys.modules["nats.errors"] = nats_errors_module
sys.modules["nats.js"] = nats_js_module
sys.modules["nats.js.errors"] = nats_js_errors_module
sys.modules["nats.js.api"] = nats_js_api_module

# Real `import nats` followed by dotted attribute access (`nats.js.errors.X`,
# as base.py's _bootstrap_mesh does) works because CPython's import
# machinery sets each submodule as an attribute of its parent when it is
# first imported. Registering only in sys.modules above does not do that --
# it satisfies `from nats.js.api import X` (which looks the dotted name up
# in sys.modules directly) but not attribute chains, which would raise
# AttributeError instead of whatever real exception the code is trying to
# catch. Wire the same parent/child attributes real import does.
nats_module.errors = nats_errors_module
nats_module.js = nats_js_module
nats_js_module.errors = nats_js_errors_module
nats_js_module.api = nats_js_api_module


# =====================================================================
# 🗄️ IN-MEMORY SQLITE-BACKED ASYNCPG MOCK
# =====================================================================


class SQLiteConnection:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consolidated INTEGER DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_configs (
                id INTEGER PRIMARY KEY,
                personality TEXT,
                background_history TEXT,
                evolved_learnings TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def _translate_query(self, query: str):
        # 1. Translate PostgreSQL $1, $2 placeholders to SQLite ?
        translated = re.sub(r"\$\d+", "?", query)
        # 2. Replace NOW() with CURRENT_TIMESTAMP
        translated = re.sub(
            r"\bNOW\(\)", "CURRENT_TIMESTAMP", translated, flags=re.IGNORECASE
        )
        # 3. Translate PostgreSQL ON CONFLICT DO UPDATE to SQLite INSERT OR REPLACE INTO generically
        if "ON CONFLICT" in translated.upper():
            # Strip everything starting from ON CONFLICT
            parts = re.split(r"\bON\s+CONFLICT\b", translated, flags=re.IGNORECASE)
            base_query = parts[0].strip()
            # Replace INSERT INTO with INSERT OR REPLACE INTO
            translated = re.sub(
                r"\bINSERT\s+INTO\b",
                "INSERT OR REPLACE INTO",
                base_query,
                flags=re.IGNORECASE,
            )
        return translated

    async def execute(self, query, *args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        cursor = self.conn.cursor()
        cursor.execute(translated, cleaned_args)
        self.conn.commit()

    async def fetch(self, query, *args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        cursor = self.conn.cursor()
        cursor.execute(translated, cleaned_args)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetchrow(self, query, *args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        cursor = self.conn.cursor()
        cursor.execute(translated, cleaned_args)
        row = cursor.fetchone()
        return dict(row) if row else None

    async def fetchval(self, query, *args):
        translated = self._translate_query(query)
        cleaned_args = [str(arg) if hasattr(arg, "hex") else arg for arg in args]
        cursor = self.conn.cursor()
        cursor.execute(translated, cleaned_args)
        row = cursor.fetchone()
        return row[0] if row else None


class MockPoolAcquisition:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockPGPool:
    def __init__(self):
        self.connection = SQLiteConnection()

    def acquire(self):
        return MockPoolAcquisition(self.connection)

    async def close(self):
        pass


# Intercept and stub asyncpg library
class MockAsyncPG:
    Pool = MockPGPool

    async def create_pool(self, dsn=None, **kwargs):
        return MockPGPool()


sys.modules["asyncpg"] = MockAsyncPG()


# =====================================================================
# 🛠️ STANDARD PYTEST CONFIGURATION & FIXTURES
# =====================================================================


def pytest_configure(config):
    """Dynamically enable benchmark-autosave if pytest-benchmark is installed.
    This prevents CI/CD failures on environments that do not have pytest-benchmark,
    while ensuring local runs are always saved for visualization.
    """
    if config.pluginmanager.hasplugin("benchmark"):
        config.option.benchmark_autosave = True


@pytest.fixture(scope="module")
def ollama_tags() -> dict:
    """Gate for BrainBench's real-model replays (one per llm_augmented suite).

    Opt-in only: set BRAINBENCH_REAL_LLM=1. Each replay is GPU work that
    takes 10+ minutes on home-gpu; skipping on reachability alone meant any
    full `pytest` run on a machine that can see home-gpu silently queued an
    hour of model calls and looked hung. The endpoint is BRAINBENCH_LLM_URL
    (home-gpu by default, never localhost).
    """
    import json
    import subprocess

    if os.environ.get("BRAINBENCH_REAL_LLM") != "1":
        pytest.skip("real-model replay: set BRAINBENCH_REAL_LLM=1 to run")
    url = os.environ.get("BRAINBENCH_LLM_URL", "http://100.88.246.46:11434")
    try:
        response = subprocess.run(
            ["curl", "-s", "--max-time", "2", url.rstrip("/") + "/api/tags"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"BrainBench LLM endpoint {url} is unreachable: {exc}")
    if response.returncode != 0:
        pytest.skip(f"BrainBench LLM endpoint {url} is unreachable")
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Ollama responded but /api/tags was not valid JSON: {exc}")


@pytest.fixture
def mock_llm_service():
    """Mock for OllamaClient (Hardened for AI Friend Core)"""
    client = MagicMock()
    # Support **kwargs in async mock
    client.generate = AsyncMock(
        return_value='{"intent": "CHAT", "goal": "ENGAGE", "confidence": 0.9}'
    )
    client.generate_stream = MagicMock()

    async def mock_stream(prompt, system=None, **kwargs):
        """Streaming mock that accepts Resilient parameters (model, num_thread, etc.)"""
        yield "Hello "
        yield "there!"

    client.generate_stream.side_effect = mock_stream
    return client


@pytest.fixture
def mock_graph_db():
    """Mock for Neo4j GraphDB (Async-Mesh Migration)"""
    db = MagicMock()
    # All methods MUST be async to match AsyncGraphDatabase driver
    db.execute_query = AsyncMock(return_value=[])
    db.create_triplet = AsyncMock(return_value=None)
    db.consolidate_relationship = AsyncMock(return_value=None)
    db.invalidate_cache = AsyncMock(return_value=None)
    db.close = AsyncMock(return_value=None)
    return db


@pytest.fixture
def actr_v1_ranking(monkeypatch):
    """Run a test against the V1 ACT-R ranker (`MEMORY_RANKING_POLICY="actr_v1"`).

    Brain V2 made the hybrid ranker the default (ADR-001). Tests that specify
    V1's own mechanics -- cue boost, PPR, MRL pool tiers, mood-congruent and
    recency-over-relevance ordering -- still describe a selectable policy, so
    they pin it rather than being deleted or rewritten to a new meaning.
    """
    from app.config import Config

    monkeypatch.setattr(Config, "MEMORY_RANKING_POLICY", "actr_v1")


@pytest.fixture
def mock_memory_store():
    """Mock for PGVector MemoryStore"""
    store = MagicMock()
    store.search_memories = AsyncMock(return_value=[])
    # The real add_memory returns True/False; None would read as a failed write.
    store.add_memory = AsyncMock(return_value=True)
    return store


@pytest.fixture
def no_quiet_hours(monkeypatch):
    """Empty the default quiet-hour window (start == end) for a test about
    something else. W9's gate reads the user's hour of day, so any test that
    expects eligibility at the real wall clock passed in daytime and failed
    whenever CI ran at night. Quiet-hour behaviour has its own tests."""
    from app.config import Config

    monkeypatch.setattr(Config, "PROACTIVE_QUIET_START_HOUR", 0)
    monkeypatch.setattr(Config, "PROACTIVE_QUIET_END_HOUR", 0)


@pytest.fixture(autouse=True)
def enforce_test_config():
    """AI Friend Core: Ensure deterministic configuration for cognitive tests."""
    from app.config import Config

    original_classifier = Config.LLM_INTENT_CLASSIFICATION_ENABLED
    original_interval = Config.REFLECTION_MIN_INTERVAL_SECONDS
    original_enabled = Config.REFLECTION_ENABLED
    original_facial_reflex = Config.FACIAL_REFLEX_ENABLED

    Config.LLM_INTENT_CLASSIFICATION_ENABLED = True
    Config.REFLECTION_MIN_INTERVAL_SECONDS = 0
    Config.REFLECTION_ENABLED = True
    # Off by default for the same reason VLM/reflection knobs are pinned
    # here: `VisionAgent()` would otherwise load a real ~3.7MB MediaPipe
    # model from disk on every construction across the whole suite whenever
    # a developer machine happens to have it downloaded (models/ is
    # gitignored, so CI never does) -- tests that actually exercise this
    # wiring (test_vision.py::TestVisionAgentFacialReflex) monkeypatch it
    # back on explicitly.
    Config.FACIAL_REFLEX_ENABLED = False

    yield

    Config.LLM_INTENT_CLASSIFICATION_ENABLED = original_classifier
    Config.REFLECTION_MIN_INTERVAL_SECONDS = original_interval
    Config.REFLECTION_ENABLED = original_enabled
    Config.FACIAL_REFLEX_ENABLED = original_facial_reflex


@pytest.fixture(autouse=True)
def _shutdown_leaked_subject_metrics_threads():
    """`SubjectMetrics.__init__` (app/metrics.py) unconditionally starts a
    real daemon `threading.Thread` and nothing joins it unless the owning
    object's `.stop()`/`.shutdown()` is called explicitly -- easy to forget
    in a unit test that only exercises one method. Measured mid-suite: 211
    live OS threads in one pytest process, each waking every 50ms forever,
    which starves the GIL/scheduler and inflates wall-clock time for every
    other test without raising CPU time (a run showed ~20 minutes elapsed
    against ~2 minutes of actual CPU time). `.shutdown()` is idempotent, so
    calling it here even when a test already shut its instance down is safe.
    """
    from app.metrics import SubjectMetrics

    instances: list[SubjectMetrics] = []
    original_init = SubjectMetrics.__init__

    def _tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances.append(self)

    SubjectMetrics.__init__ = _tracked_init
    try:
        yield
    finally:
        SubjectMetrics.__init__ = original_init
        for instance in instances:
            try:
                instance.shutdown()
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _shutdown_leaked_vision_agents():
    """Ensure any FaceLandmarker or VisionAgent threads created during tests
    are cleanly closed on test teardown to prevent unjoined XNNPACK threadpools."""
    try:
        from app.vision.agent import VisionAgent
    except ImportError:
        yield
        return

    instances: list[VisionAgent] = []
    original_init = VisionAgent.__init__

    def _tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances.append(self)

    VisionAgent.__init__ = _tracked_init
    try:
        yield
    finally:
        VisionAgent.__init__ = original_init
        for instance in instances:
            try:
                landmarker = getattr(instance, "_face_landmarker", None)
                if landmarker is not None and hasattr(landmarker, "close"):
                    landmarker.close()
            except Exception:
                pass


def pytest_sessionfinish(session, exitstatus):
    """Force exit to prevent background thread pool/connection pool hangs."""
    import os

    # mutmut runs pytest in-process to collect test-to-function coverage and
    # then forks isolated mutant workers. Calling os._exit here would terminate
    # the collector before it can persist those associations. MUTANT_UNDER_TEST
    # is set for every mutmut phase, including its clean baseline run.
    # CI also needs pytest's normal shutdown path: os._exit can run before the
    # terminal reporter flushes a collection traceback, turning a useful test
    # failure into a bare "collecting ..." line in the Actions log. CI jobs
    # have a hard timeout, so retaining diagnostics is more valuable than the
    # local-only escape hatch for leaked resources.
    if "MUTANT_UNDER_TEST" in os.environ or os.environ.get("CI", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        return

    os._exit(exitstatus)
