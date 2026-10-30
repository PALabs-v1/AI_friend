"""State ledger registry (V4 slice 1, D-020).

One row per piece of state the architecture draft (`docs/brain-v4/phases/
02-architecture-draft.md`, section 2) names, with where it is stored, who writes
it, who reads it, and how an intervention reaches it. This is data: the
operators in `interventions.py` read it, and `tests/test_ledger_registry.py`
fails when a binding, table, column, file or symbol it names disappears.

A row has up to two bindings:

- `sqlite`: the persisted column, reached on an `OfflineCopy` (files).
- `attr`: the in-process attribute on `AgentState`, reached on an offline
  `StateService` opened from a copy. Needed because some state is deliberately
  not persisted (hormone bursts, `agent_state.py` comment on phasic peaks).

A row with neither is `unbound`, and must say why. Unbound rows are the debt:
they have no running intervention handle yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Encoding = Literal["real", "int", "text", "json"]
Tier = Literal["immutable", "constitutional", "adaptive", "runtime"]


@dataclass(frozen=True)
class SqliteBinding:
    store: str  # name used in OfflineCopy.stores
    table: str
    column: str
    encoding: Encoding = "real"
    key_columns: tuple[str, ...] = ()  # callers pass `where` for these
    # Accepted in `where` but not required: a single-row copy needs no filter,
    # a multi-row copy cannot be addressed without one.
    optional_key_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttrBinding:
    path: str  # dotted path on AgentState, e.g. "user_mental_model.inferred_valence"


@dataclass(frozen=True)
class LedgerRow:
    id: str
    name: str
    draft_row: str  # row number in the architecture draft, section 2
    timescale: str
    tier: Tier
    kind: Literal["stored", "derived"] = "stored"
    sqlite: SqliteBinding | None = None
    attr: AttrBinding | None = None
    writers: tuple[str, ...] = ()  # "path::symbol", checked by the drift test
    readers: tuple[str, ...] = ()
    unbound_reason: str = ""

    @property
    def bound(self) -> bool:
        return self.sqlite is not None or self.attr is not None


_AS = "backend/app/state/agent_state.py"
_PL = "backend/app/cognitive/pipeline.py"
_DEC = "backend/app/cognitive/decision.py"
_ACT = "backend/app/cognitive/action.py"
_EXPR = "backend/app/cognitive/expression.py"

# Every place mood reaches the reply, found by grep for slice 4b
# (`prereg/slice-04b.md`, premise check). `emotional_bias` was the only reader
# listed; the seven paths after it (sampler through two tone properties,
# directive, label, stance, breath tag, candidate scoring, voice trajectory)
# were invisible to the drift test.
_MOOD_READERS = (
    f"{_PL}::emotional_bias",
    # Sampler: temperature and top_p follow mood through the hormone tones.
    f"{_AS}::cortisol_tonic",
    f"{_AS}::dopamine_tonic",
    # Directive text and emotion label, both rendered into the prompt.
    f"{_AS}::get_behavioral_directive",
    f"{_AS}::get_emotion_label",
    # Relational stance, one third of its score.
    f"{_DEC}::_bucket_relational_stance",
    # Breath or sigh opener tag.
    f"{_ACT}::_prepended_affect_tag",
    # Candidate scoring, via the global controls.
    f"{_AS}::_refresh_global_controls_locked",
    # Voice trajectory (not text).
    f"{_EXPR}::derive_speech_expression",
    # Slice 4b variant B reply-length line (flag `MOOD_STYLE_DRIVE_ENABLED`).
    f"{_ACT}::_mood_style_line",
)


def _state(
    id_: str,
    name: str,
    draft_row: str,
    column: str,
    *,
    attr: str | None = None,
    tier: Tier = "adaptive",
    timescale: str = "reactive",
    writers: tuple[str, ...] = (),
    readers: tuple[str, ...] = (),
) -> LedgerRow:
    return LedgerRow(
        id=id_,
        name=name,
        draft_row=draft_row,
        timescale=timescale,
        tier=tier,
        sqlite=SqliteBinding(
            "state_cache",
            "agent_state",
            column,
            "real",
            optional_key_columns=("agent_name",),
        ),
        attr=AttrBinding(attr or column),
        writers=writers,
        readers=readers,
    )


_UPD = (f"{_AS}::update_from_appraisal",)
_TRUST_READ = (f"{_DEC}::_bucket_relational_stance",)

REGISTRY: tuple[LedgerRow, ...] = (
    # --- affect and relationship (agent_state table) -------------------------
    _state(
        "mood",
        "Mood (valence)",
        "1",
        "mood",
        writers=_UPD,
        readers=_MOOD_READERS,
    ),
    _state(
        "energy",
        "Energy (arousal base)",
        "5",
        "energy",
        writers=(f"{_AS}::handle_system_tick",),
    ),
    _state("dominance", "Dominance", "1", "dominance", writers=_UPD),
    _state(
        "trust_benevolence",
        "Trust: benevolence",
        "2",
        "trust_benevolence",
        timescale="deliberative",
        writers=_UPD,
        readers=_TRUST_READ,
    ),
    _state(
        "trust_competence",
        "Trust: competence",
        "2",
        "trust_competence",
        timescale="deliberative",
        writers=_UPD,
        readers=_TRUST_READ,
    ),
    _state(
        "trust_integrity",
        "Trust: integrity",
        "2",
        "trust_integrity",
        timescale="deliberative",
        writers=_UPD,
        readers=_TRUST_READ,
    ),
    _state(
        "attachment",
        "Attachment",
        "2",
        "attachment",
        timescale="deliberative",
        writers=_UPD,
        readers=_TRUST_READ,
    ),
    _state(
        "fatigue", "Fatigue", "5", "fatigue", writers=(f"{_AS}::handle_system_tick",)
    ),
    _state(
        "last_proactive_attempt",
        "Proactive cooldown timestamp",
        "24",
        "last_proactive_attempt",
        timescale="reflective",
        writers=(f"{_AS}::mark_proactive_attempt",),
        readers=(f"{_AS}::proactive_candidate_eligible",),
    ),
    # --- constitutional temperament ------------------------------------------
    _state(
        "baseline_valence",
        "Baseline valence",
        "9",
        "baseline_valence",
        tier="constitutional",
        timescale="identity-slow",
    ),
    _state(
        "baseline_arousal",
        "Baseline arousal",
        "9",
        "baseline_arousal",
        tier="constitutional",
        timescale="identity-slow",
    ),
    _state(
        "baseline_dominance",
        "Baseline dominance",
        "9",
        "baseline_dominance",
        tier="constitutional",
        timescale="identity-slow",
    ),
    LedgerRow(
        "dopamine_halflife_s",
        "Dopamine half-life",
        "31",
        "identity-slow",
        "constitutional",
        attr=AttrBinding("dopamine_halflife_s"),
        unbound_reason="",
    ),
    LedgerRow(
        "cortisol_halflife_s",
        "Cortisol half-life",
        "31",
        "identity-slow",
        "constitutional",
        attr=AttrBinding("cortisol_halflife_s"),
    ),
    LedgerRow(
        "adrenaline_halflife_s",
        "Adrenaline half-life",
        "31",
        "identity-slow",
        "constitutional",
        attr=AttrBinding("adrenaline_halflife_s"),
    ),
    # --- hormone bursts: in-process only, not persisted by design ------------
    LedgerRow(
        "cortisol_burst",
        "Cortisol phasic peak",
        "3",
        "reactive",
        "runtime",
        attr=AttrBinding("cortisol_phasic_peak"),
        writers=(f"{_AS}::release_cortisol",),
        readers=(f"{_ACT}::cortisol",),
    ),
    LedgerRow(
        "dopamine_burst",
        "Dopamine phasic peak",
        "3",
        "reactive",
        "runtime",
        attr=AttrBinding("dopamine_phasic_peak"),
        writers=(f"{_AS}::release_dopamine",),
        readers=(f"{_ACT}::dopamine",),
    ),
    LedgerRow(
        "adrenaline_burst",
        "Adrenaline phasic peak",
        "3",
        "reactive",
        "runtime",
        attr=AttrBinding("adrenaline_phasic_peak"),
        writers=(f"{_AS}::release_adrenaline",),
    ),
    # --- user model -----------------------------------------------------------
    LedgerRow(
        "tom_inferred_valence",
        "ToM: inferred user valence",
        "16",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("state_cache", "agent_state", "inferred_valence"),
        attr=AttrBinding("user_mental_model.inferred_valence"),
        writers=(f"{_AS}::update_theory_of_mind",),
        readers=(f"{_ACT}::_build_tom_context",),
    ),
    LedgerRow(
        "tom_inferred_arousal",
        "ToM: inferred user arousal",
        "16",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("state_cache", "agent_state", "inferred_arousal"),
        attr=AttrBinding("user_mental_model.inferred_arousal"),
        writers=(f"{_AS}::update_theory_of_mind",),
        readers=(f"{_ACT}::_build_tom_context",),
    ),
    LedgerRow(
        "tom_known_concepts",
        "ToM: known concepts",
        "16",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("state_cache", "agent_state", "known_concepts", "json"),
        writers=(f"{_AS}::update_theory_of_mind",),
        readers=(f"{_ACT}::_build_tom_context",),
    ),
    LedgerRow(
        "tom_implied_goals",
        "ToM: implied goals",
        "16",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("state_cache", "agent_state", "implied_goals", "json"),
        writers=(f"{_AS}::update_theory_of_mind",),
        readers=(f"{_ACT}::_build_tom_context",),
    ),
    # --- history lists --------------------------------------------------------
    LedgerRow(
        "interaction_hours",
        "Interaction-hour history",
        "23",
        "reflective",
        "runtime",
        sqlite=SqliteBinding(
            "state_cache", "agent_user_activity_hours", "hours_json", "json"
        ),
        attr=AttrBinding("user_interaction_hours"),
        writers=(f"{_AS}::record_user_interaction",),
        readers=(f"{_AS}::is_quiet_hour",),
    ),
    LedgerRow(
        "proactive_goals",
        "Proactive thought and goal state",
        "13",
        "reflective",
        "runtime",
        sqlite=SqliteBinding(
            "state_cache", "agent_proactive_goals", "goals_json", "json"
        ),
        writers=(f"{_AS}::record_proactive_thought",),
    ),
    # --- learned weights (adaptive_weights table, one row per weight_key) -----
    LedgerRow(
        "reappraisal_weights",
        "Reappraisal weights",
        "4",
        "deliberative",
        "adaptive",
        sqlite=SqliteBinding(
            "state_cache", "adaptive_weights", "weights_json", "json", ("weight_key",)
        ),
        writers=("backend/app/cognitive/reappraisal.py::_WEIGHT_KEY",),
        readers=(f"{_PL}::get_weights",),
    ),
    LedgerRow(
        "goal_utilities",
        "TD-learned goal utilities",
        "26",
        "deliberative",
        "adaptive",
        sqlite=SqliteBinding(
            "state_cache", "adaptive_weights", "weights_json", "json", ("weight_key",)
        ),
        writers=(f"{_DEC}::goal_utilities",),
        readers=(f"{_DEC}::goal_utilities",),
    ),
    # --- other sqlite stores --------------------------------------------------
    LedgerRow(
        "identity_relationship",
        "Relationship (persona, adaptive)",
        "8",
        "identity-slow",
        "adaptive",
        sqlite=SqliteBinding("identity_core", "identity_core", "relationship", "text"),
        readers=("backend/app/cognitive/identity.py::get_persona_prompt",),
    ),
    LedgerRow(
        "memory_valid_until",
        "Memory row validity end",
        "7a",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("memories", "memories", "valid_until", "text", ("id",)),
        writers=("backend/app/state/memory_store.py::contradicts_id",),
    ),
    LedgerRow(
        "memory_contradicts_id",
        "Memory row contradiction link",
        "7a",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("memories", "memories", "contradicts_id", "text", ("id",)),
        writers=("backend/app/state/memory_store.py::contradicts_id",),
    ),
    LedgerRow(
        "memory_valence",
        "Memory row valence",
        "6",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("memories", "memories", "valence", "real", ("id",)),
    ),
    LedgerRow(
        "memory_source",
        "Memory row source (includes `biography`)",
        "25",
        "identity-slow",
        "runtime",
        sqlite=SqliteBinding("memories", "memories", "source", "text", ("id",)),
        writers=("backend/app/persona/biography.py::BIOGRAPHY_SOURCE",),
        readers=(f"{_ACT}::_build_shared_history",),
    ),
    LedgerRow(
        "belief_valid_until",
        "Temporal belief validity end",
        "7b",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding(
            "temporal", "beliefs", "valid_until", "real", ("record_id",)
        ),
    ),
    LedgerRow(
        "belief_status",
        "Temporal belief status",
        "7b",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("temporal", "beliefs", "status", "text", ("record_id",)),
    ),
    LedgerRow(
        "proactive_queue_thought",
        "Queued outreach thought",
        "29",
        "reflective",
        "runtime",
        sqlite=SqliteBinding(
            "state_cache", "proactive_outreach_queue", "thought", "text", ("id",)
        ),
        writers=("backend/app/state/proactive_queue.py::enqueue",),
        readers=("backend/app/state/proactive_queue.py::pop_all",),
    ),
    LedgerRow(
        "session_working_state",
        "Session working-state values",
        "21",
        "reactive",
        "runtime",
        sqlite=SqliteBinding(
            "working_memory", "working_state", "value", "text", ("key",)
        ),
        writers=("backend/app/state/session_state.py::persist_session_state",),
    ),
    # --- unbound: no intervention handle yet (the debt, with reasons) --------
    LedgerRow(
        "global_controls",
        "Global controls",
        "15",
        "reactive",
        "runtime",
        kind="derived",
        unbound_reason="Derived from PAD, fatigue, urgency and prediction error (`agent_state.py` `_refresh_global_controls_locked`); clamp the source rows instead.",
    ),
    LedgerRow(
        "cognitive_workspace",
        "Cognitive workspace snapshot",
        "19",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding(
            "workspace", "workspace_state", "focus", "text", ("session_id",)
        ),
        writers=("backend/app/state/workspace_store.py::SQLiteWorkspaceStore",),
    ),
    LedgerRow(
        "person_models",
        "Per-person relationship models",
        "22",
        "deliberative",
        "runtime",
        unbound_reason="Decorative on the live path (no production caller); in-memory pydantic models, no store.",
    ),
    LedgerRow(
        "ai_self_description",
        "AI self-description and model pin",
        "20",
        "identity-slow",
        "immutable",
        unbound_reason="Absent today; created by slice 9 (AI self-knowledge) and slice 14/15 (pin).",
    ),
    LedgerRow(
        "self_knowledge_gaps",
        "Self-knowledge gaps",
        "14",
        "deliberative",
        "runtime",
        unbound_reason="Stored in Postgres (`self_knowledge_gaps`); no offline Postgres copy tooling yet.",
    ),
    LedgerRow(
        "memory_rows_postgres",
        "Memory rows in Postgres",
        "6",
        "deliberative",
        "runtime",
        unbound_reason="Production path is Postgres; only the SQLite fallback is bound (`memory_*` rows above). Postgres copy tooling is a follow-up.",
    ),
    LedgerRow(
        "lexicon_and_graph",
        "Learned lexicon and knowledge graph",
        "27",
        "deliberative",
        "runtime",
        sqlite=SqliteBinding("memories", "vocabulary", "times_seen", "int", ("term",)),
        writers=("backend/app/state/lexicon_store.py::MentalLexicon",),
    ),
    LedgerRow(
        "conversation_history",
        "Conversation history and heard-history",
        "28",
        "reactive",
        "runtime",
        sqlite=SqliteBinding("memories", "messages", "content", "text", ("id",)),
        writers=("backend/app/state/sqlite_fallback.py::SQLiteConnection",),
    ),
    LedgerRow(
        "monologue_and_dream",
        "Monologue and dream output",
        "10-11",
        "reflective",
        "runtime",
        unbound_reason="Events on a topic, no stored row; becomes a typed candidate record in slice 12.",
    ),
    LedgerRow(
        "rest_replay",
        "Rest-phase replay",
        "12",
        "reflective",
        "runtime",
        unbound_reason="A process acting on memory rows; intervene by disabling the process, not by writing a row.",
    ),
    LedgerRow(
        "reflex_inputs",
        "Reflex-path inputs",
        "30",
        "reactive",
        "runtime",
        unbound_reason="Sensor events; intervene by suppressing the input, not by writing a row.",
    ),
    LedgerRow(
        "calibration_and_plan_objects",
        "Calibration, plan verifier, executor, simulation, adapter gate",
        "17-18",
        "deliberative",
        "runtime",
        unbound_reason="Decorative; wiring dropped in slice 6 (D-040).",
    ),
)

_BY_ID = {row.id: row for row in REGISTRY}
if len(_BY_ID) != len(REGISTRY):  # pragma: no cover - import-time guard
    raise RuntimeError("duplicate ledger row id")


def get_row(row_id: str) -> LedgerRow:
    try:
        return _BY_ID[row_id]
    except KeyError:
        raise KeyError(f"unknown ledger row {row_id!r}") from None


def bound_rows() -> tuple[LedgerRow, ...]:
    return tuple(r for r in REGISTRY if r.bound)


def unbound_rows() -> tuple[LedgerRow, ...]:
    return tuple(r for r in REGISTRY if not r.bound)
