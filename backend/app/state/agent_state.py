"""
Agent State -- PAD + Relational Framework (psychological_layer.md section2).

Affective dimensions: Valence (V), Arousal (Ar), Dominance (D)
  -- Mehrabian & Russell (1974)

Relational dimensions: Trust (T), Attachment (At)
  -- Marsh (1994) for trust, Bowlby for attachment

State updates: ALMA mood-pull + exponential decay (Gebhard, 2005)
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis

from .. import clock
from ..cognitive.goals import GoalRecord, review_due_goals
from ..config import Config
from ..errors import StateConflictError
from ..persona import PersonaProfile
from ..utils.background_tasks import spawn_background
from .person_model import PersonModel

if TYPE_CHECKING:
    from ..cognitive.appraisal import AppraisalRecord
    from ..cognitive.calibration import CapabilityLimitationModel
    from ..cognitive.global_controls import GlobalControls
    from ..cognitive.tom import UserMentalModel


def _default_user_mental_model():
    from ..cognitive.tom import UserMentalModel

    return UserMentalModel()


def _new_capability_model() -> "CapabilityLimitationModel":
    """Construct calibration lazily to keep state imports acyclic."""
    from ..cognitive.calibration import CapabilityLimitationModel

    return CapabilityLimitationModel()


logger = logging.getLogger(__name__)

# Redis script: keep the larger of the stored and incoming proactive-attempt
# watermark, so a stale writer can never move the cooldown backwards (V-4).
_PROACTIVE_WATERMARK_MAX_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local incoming = tonumber(ARGV[1])
if not current or tonumber(current) < incoming then
    redis.call('SET', KEYS[1], ARGV[1])
    return ARGV[1]
end
return current
"""


@lru_cache(maxsize=8)
def _zone(name: str) -> tzinfo | None:
    """The configured user zone; None (host local time) when unset or unknown."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("[State] Unknown USER_TIMEZONE %r; using host local time", name)
        return None


def user_hour(timestamp: float) -> int:
    """Hour of day at `timestamp` for the user (Config.USER_TIMEZONE).

    Every hour-of-day decision goes through here, so the recorded activity
    hours, the quiet-hour default and night fatigue all agree on one clock.
    """
    return datetime.fromtimestamp(timestamp, _zone(Config.USER_TIMEZONE)).hour


def in_hour_window(hour: int, start: int, end: int) -> bool:
    """`hour` in [start, end), wrapping midnight when start > end; empty if equal."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# Bounds on the proactive state every snapshot and state.broadcast carries.
_MAX_INTERACTION_HOURS = 168
_MAX_PROACTIVE_GOALS = 256


def _parse_interaction_hours(raw: Any) -> list[int] | None:
    """User turn hours from storage or a broadcast; None when absent or
    unreadable, so the caller keeps what it has."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    return [
        int(hour) % 24
        for hour in raw[-_MAX_INTERACTION_HOURS:]
        if isinstance(hour, int) and not isinstance(hour, bool)
    ]


def _goal_payload(goals: list[GoalRecord]) -> list[dict[str, Any]]:
    """Thought history as persisted and broadcast: fields left at their
    default are omitted, and `GoalRecord.model_validate` restores them.

    The full dump is about 580 bytes a goal, mostly empty defaults, and the
    brain broadcasts this on every tick into the file-backed AI_MESSAGES
    stream: at the 256-goal cap that was 149 KiB a minute, about 1.4 GB over
    the stream's 7-day window against its 1 GiB cap, so state snapshots
    evicted chat history. Omitting defaults cuts it to under half.
    """
    return [goal.model_dump(exclude_defaults=True) for goal in goals]


def _bounded_proactive_goals(goals: list[GoalRecord]) -> list[GoalRecord]:
    """Hold the thought history to _MAX_PROACTIVE_GOALS. A closed thought (never
    raised again) is what stops it being raised again, so closed ones go first
    and oldest first; list order is creation order, which stays correct under
    simulated time where created_at (wall clock) does not."""
    excess = len(goals) - _MAX_PROACTIVE_GOALS
    if excess <= 0:
        return goals
    closed = [
        goal
        for goal in goals
        if goal.proactive_raise_count <= len(goal.proactive_outcomes)
        and goal.re_raise_probability(Config.PROACTIVE_IGNORE_ZERO_AFTER) == 0.0
    ]
    evicted = {id(goal) for goal in closed[:excess]}
    kept = [goal for goal in goals if id(goal) not in evicted]
    return kept[-_MAX_PROACTIVE_GOALS:]


def _parse_proactive_goals(raw: Any) -> list[GoalRecord] | None:
    """Thought history from storage or a broadcast. One malformed record is
    skipped, not allowed to abort hydration or a whole state broadcast."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    goals = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            goals.append(GoalRecord.model_validate(item))
        except ValueError as error:
            logger.warning("Skipping malformed proactive goal record: %s", error)
    return _bounded_proactive_goals(goals)


# Roadmap sectionC: recognising a somatic comfort fires a phasic dopamine burst of
# this size. Kept here rather than in somatic.py because it is a property of the
# endocrine response, not of visual recognition -- any future reward channel
# (a warm reply, a resolved goal) should fire through the same mechanism.
SOMATIC_DOPAMINE_SPIKE = 0.25


@dataclass(slots=True)
class AgentState:
    """
    Multidimensional PAD + Relational state for human-like dynamics.

    Backward compatibility: 'mood' and 'energy' are kept as the canonical
    dataclass field names. New PAD vocabulary is provided via properties.
    """

    # PAD Affective Dimensions (Mehrabian & Russell, 1974)
    mood: float = 0.0  # V (Valence): -1.0 to 1.0
    energy: float = 0.5  # Ar (Arousal): 0.0 to 1.0
    dominance: float = 0.5  # D (Dominance): 0.0 to 1.0 -- NEW

    # Relational Dimensions
    trust_benevolence: float = 0.5  # Tb (Marsh): 0.0 to 1.0
    trust_competence: float = 0.5  # Tc (Marsh): 0.0 to 1.0
    trust_integrity: float = 0.5  # Ti (Marsh): 0.0 to 1.0
    attachment: float = 0.1  # At (Bowlby): 0.0 to 1.0

    # Interaction Tracking
    interaction_count: int = 0  # For Bowlby attachment frequency
    active_goals: list[str] = field(default_factory=list)
    last_update: datetime = field(default_factory=datetime.now)
    last_user_interaction: float = field(default_factory=time.time)
    # Proactive outreach cooldown timestamp. Initialized to 0.0 so a fresh agent
    # reads the cooldown as already satisfied.
    last_proactive_attempt: float = 0.0
    user_interaction_hours: list[int] = field(default_factory=list)
    fatigue: float = 0.0  # Metabolic fatigue cycle F(t)
    user_mental_model: "UserMentalModel" = field(
        default_factory=_default_user_mental_model
    )

    # Persistent baseline affect
    baseline_valence: float = 0.0
    baseline_arousal: float = 0.5
    baseline_dominance: float = 0.5

    # Phasic hormone bursts (see the `dopamine` and `cortisol` properties).
    # Stored as a peak plus the moment it was released, so the current level is
    # derived from elapsed time rather than needing a tick to decay it. That
    # keeps the reading correct even if system.tick stalls, and makes decay
    # testable without a clock stub.
    #
    # Deliberately NOT persisted, unlike the baselines above. Both half-lives
    # are minutes-scale, so a burst is already within rounding distance of zero
    # by the time any realistic restart completes; carrying it across one would
    # preserve a value that no longer means anything. Restoring a state with no
    # burst is also the safe direction -- peak defaults to 0.0, which reads as
    # "nothing outstanding" rather than as a stale reward or a stale threat. If
    # a future hormone decays on an hours-scale, that one *will* need
    # persisting.
    dopamine_phasic_peak: float = 0.0
    dopamine_phasic_at: float = field(default_factory=time.time)
    cortisol_phasic_peak: float = 0.0
    cortisol_phasic_at: float = field(default_factory=time.time)
    # Adrenaline, unlike dopamine and cortisol, has no tonic counterpart --
    # see `adrenaline_tonic` -- so `adrenaline_phasic_peak` alone is the current
    # adrenaline level at the moment of release.
    adrenaline_phasic_peak: float = 0.0
    adrenaline_phasic_at: float = field(default_factory=time.time)

    # Half-lives are temperament, not deployment settings: how long a reward
    # glows and how long a fright lingers are among the most recognisable things
    # about a person. StateService seeds these from the PersonaProfile; defaults
    # match constitutional baseline constants.
    dopamine_halflife_s: float = 90.0
    cortisol_halflife_s: float = 4500.0
    adrenaline_halflife_s: float = 120.0

    # Cross-process state revision tracking: brain_agent and subconscious_agent
    # each own a StateService over the same logical agent. `revision` increments
    # once per `persist_state` call; `writer_id` names which process incremented it last.
    # Not persisted across restarts: a restarted process begins a new revision history.
    revision: int = 0
    writer_id: str = ""

    # Person-indexed relationship state: scalar competence and benevolence fields
    # remain compatibility mirrors for the active person, while PersonModel retains
    # the source relationship history. These fields remain last to preserve positional args.
    persons: dict[str, PersonModel] = field(default_factory=dict)
    active_person_id: str = "default_user"
    capability_model: "CapabilityLimitationModel" = field(
        default_factory=_new_capability_model
    )

    # --- Affect Split Properties ---
    @property
    def personality_baseline_affect(self) -> dict[str, float]:
        """Persistent slow-evolving personality baseline affect."""
        return {
            "valence": self.baseline_valence,
            "arousal": self.baseline_arousal,
            "dominance": self.baseline_dominance,
        }

    @personality_baseline_affect.setter
    def personality_baseline_affect(self, value: dict[str, float]):
        self.baseline_valence = value.get("valence", self.baseline_valence)
        self.baseline_arousal = value.get("arousal", self.baseline_arousal)
        self.baseline_dominance = value.get("dominance", self.baseline_dominance)

    @property
    def short_term_affect(self) -> dict[str, float]:
        """Transient highly reactive short-term affect."""
        return {
            "valence": self.mood,
            "arousal": self.energy,
            "dominance": self.dominance,
        }

    @short_term_affect.setter
    def short_term_affect(self, value: dict[str, float]):
        self.mood = value.get("valence", self.mood)
        self.energy = value.get("arousal", self.energy)
        self.dominance = value.get("dominance", self.dominance)

    # --- PAD Property Aliases (section2.1) ---
    @property
    def valence(self) -> float:
        """PAD Valence (V) -- maps to 'mood'."""
        return self.mood

    @valence.setter
    def valence(self, value: float):
        self.mood = value

    @property
    def arousal(self) -> float:
        """PAD Arousal (Ar) -- maps to 'energy' + fatigue-induced restlessness
        plus any outstanding adrenaline.

        The adrenaline term (Bucket 11, voice remediation Phase 3, item 2) is
        deliberately *derived* here rather than written into `energy` at
        release time, the same pattern dopamine/cortisol already use for
        reward and stress: a startle should read as an immediate, bounded
        lift on top of whatever arousal already is, fading on its own over
        `adrenaline_halflife_s` as the burst decays, not a permanent nudge to
        the underlying baseline.
        """
        fatigue_restlessness = 0.2 * self.fatigue
        adrenaline_lift = 0.3 * self.adrenaline
        return max(0.0, min(1.0, self.energy + fatigue_restlessness + adrenaline_lift))

    @arousal.setter
    def arousal(self, value: float):
        self.energy = value

    @property
    def trust(self) -> float:
        """PAD Trust (T) -- maps to average of Benevolence, Competence, and Integrity."""
        return (
            self.trust_benevolence + self.trust_competence + self.trust_integrity
        ) / 3.0

    @trust.setter
    def trust(self, value: Any):
        if isinstance(value, dict):
            self.trust_benevolence = float(
                value.get("trust_benevolence", self.trust_benevolence)
            )
            self.trust_competence = float(
                value.get("trust_competence", self.trust_competence)
            )
            self.trust_integrity = float(
                value.get("trust_integrity", self.trust_integrity)
            )
        elif isinstance(value, (list, tuple)) and len(value) == 3:
            self.trust_benevolence = float(value[0])
            self.trust_competence = float(value[1])
            self.trust_integrity = float(value[2])
        else:
            try:
                scalar_value = float(value)
                self.trust_benevolence = scalar_value
                self.trust_competence = scalar_value
                self.trust_integrity = scalar_value
            except (ValueError, TypeError):
                pass

    # --- Endocrine Hormonal Properties (Physiological Regulation) ---
    @staticmethod
    def _decayed(peak: float, released_at: float, half_life: float) -> float:
        """Exponential decay of a phasic burst from `peak` toward zero.

        Shared by both hormones so the two cannot drift apart: a fix to the
        decay maths that only landed on one of them would be a subtle and
        long-lived bug, since each reads plausibly on its own.
        """
        if peak <= 0.0:
            return 0.0
        half_life = max(1e-6, float(half_life))
        elapsed = max(0.0, clock.time() - released_at)
        return peak * math.exp(-math.log(2.0) * elapsed / half_life)

    @staticmethod
    def _burst_amount(amount: Any, hormone: str) -> float:
        """Validate a release amount, returning 0.0 for anything unusable."""
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return 0.0
        # NaN survives `float()` and then defeats every comparison downstream:
        # `nan <= 0.0` is False, so it passes the positivity guard, and
        # `min(1.0, nan)` returns 1.0 -- a NaN input would fire a *maximum*
        # burst. Infinity takes the same route. Both are caller bugs, not
        # signals, and a maximum stress response is the worst possible reading
        # of a malformed one.
        if not math.isfinite(amount):
            logger.warning(
                "[Endocrine] Ignoring non-finite %s release %r.", hormone, amount
            )
            return 0.0
        return max(0.0, amount)

    @property
    def cortisol_tonic(self) -> float:
        """Background stress tone: inverse valence plus a fatigue contribution.

        This is the whole of what `cortisol` used to be. Like tonic dopamine it
        is a pure function of current affect, so it has no memory: the moment
        valence recovers, the stress is gone.
        """
        base_cortisol = 0.5 - (self.valence / 2.0)
        fatigue_contribution = 0.3 * self.fatigue
        return max(0.0, min(1.0, base_cortisol + fatigue_contribution))

    @property
    def cortisol_phasic(self) -> float:
        """The decaying remainder of recent acute stress.

        The tonic term is derived entirely from valence, which made stress
        unable to outlive its cause -- recover your mood and the alarm stops,
        instantly and completely. That is not how a threat response works. The
        HPA axis releases cortisol on a slower course than a dopamine burst and
        it clears slower too, which is why the default half-life here is
        markedly longer than dopamine's: being frightened has a hangover, being
        pleased mostly does not.
        """
        return self._decayed(
            self.cortisol_phasic_peak, self.cortisol_phasic_at, self.cortisol_halflife_s
        )

    @property
    def cortisol(self) -> float:
        """
        Stress hormone: tonic tone plus any decaying acute burst.
        High cortisol -> rigid/defensive behavior (low LLM temperature).
        Low cortisol -> relaxed/creative behavior (higher temperature).
        Range: 0.0 (fully relaxed) to 1.0 (maximum stress).

        With no burst outstanding this is exactly the historical derived value,
        so every existing reading and test is unaffected.
        """
        return max(0.0, min(1.0, self.cortisol_tonic + self.cortisol_phasic))

    def release_cortisol(self, amount: float) -> float:
        """Fire an acute stress response, returning the new cortisol level.

        The mirror of `release_dopamine`, and the reason the endocrine layer is
        no longer one-sided. Tonic cortisol and tonic dopamine are perfectly
        anti-correlated by construction -- both are functions of valence alone,
        one rising exactly as the other falls -- so before this the agent could
        not be *both* stressed and rewarded, a combination that describes a
        great deal of ordinary experience. Phasic dopamine already broke that
        coupling in one direction; this breaks it in the other.
        """
        amount = self._burst_amount(amount, "cortisol")
        if amount <= 0.0:
            return self.cortisol

        target_total = min(1.0, self.cortisol + amount)
        # Relative to the tonic floor, so that floor stays free to drift with
        # affect underneath a decaying burst instead of being double-counted.
        self.cortisol_phasic_peak = max(0.0, target_total - self.cortisol_tonic)
        self.cortisol_phasic_at = clock.time()
        return self.cortisol

    @property
    def dopamine_tonic(self) -> float:
        """Background reward tone: positive valence x arousal.

        This is the whole of what `dopamine` used to be. It tracks the ongoing
        affective state instantaneously and has no memory of its own.
        """
        return max(0.0, min(1.0, max(0.0, self.valence) * self.arousal))

    @property
    def dopamine_phasic(self) -> float:
        """The decaying remainder of recent reward bursts.

        Real dopamine signalling separates a slow tonic level from fast phasic
        bursts on reward (Grace 1991; Schultz's reward-prediction-error work).
        The tonic term above cannot represent "something good happened thirty
        seconds ago and I am still lit up" -- being a pure function of current
        valence and arousal, it forgets instantly. This is that memory, decaying
        exponentially from the peak toward zero.
        """
        return self._decayed(
            self.dopamine_phasic_peak, self.dopamine_phasic_at, self.dopamine_halflife_s
        )

    @property
    def dopamine(self) -> float:
        """
        Reward hormone: tonic tone plus any decaying phasic burst.
        High dopamine -> exploratory/playful behavior (higher top_p).
        Low dopamine -> conservative/flat behavior (lower top_p).
        Range: 0.0 (no reward signal) to 1.0 (peak reward).

        With no burst outstanding this is exactly the historical derived value
        (`max(0, V) * Ar`), so every existing reading and test is unaffected.
        """
        return max(0.0, min(1.0, self.dopamine_tonic + self.dopamine_phasic))

    def release_dopamine(self, amount: float) -> float:
        """Fire a phasic burst, returning the new total dopamine level.

        Implements the roadmap's `D_t = min(1.0, D_{t-1} + amount)`
        (`docs/FUTURE_FINETUNED_ADAPTER.md` sectionC) literally, which the previous
        derived-only property could not: with dopamine computed purely from
        valence and arousal there was no `D_{t-1}` to add to, and the only way
        to move it was to move mood itself.

        The burst is stored relative to the tonic floor, so that floor stays
        free to drift with affect underneath a decaying burst instead of being
        double-counted into it.
        """
        amount = self._burst_amount(amount, "dopamine")
        if amount <= 0.0:
            return self.dopamine

        target_total = min(1.0, self.dopamine + amount)
        self.dopamine_phasic_peak = max(0.0, target_total - self.dopamine_tonic)
        self.dopamine_phasic_at = clock.time()
        return self.dopamine

    @property
    def adrenaline_tonic(self) -> float:
        """Adrenaline has no ambient baseline, unlike dopamine/cortisol.

        Real epinephrine is fundamentally reactive rather than mood-tracking:
        it spikes on startle, a genuine interruption, or shock, and is
        otherwise silent -- there is no equivalent of "background reward tone"
        or "background stress tone" for it. Kept as an explicit property
        (rather than inlining 0.0 into `adrenaline` below) so
        `release_adrenaline` can measure its peak "relative to the tonic
        floor" using the exact same code shape as `release_cortisol`/
        `release_dopamine` above -- the floor here is just always zero.
        """
        return 0.0

    @property
    def adrenaline_phasic(self) -> float:
        """The decaying remainder of the most recent startle/interruption/shock.

        Half-life sits between dopamine's (a reward glow, ~90s) and
        cortisol's (an acute-stress hangover, ~75min) by design -- a startle
        response fades over minutes, not seconds or hours.
        """
        return self._decayed(
            self.adrenaline_phasic_peak,
            self.adrenaline_phasic_at,
            self.adrenaline_halflife_s,
        )

    @property
    def adrenaline(self) -> float:
        """
        Startle hormone: purely phasic, no tonic component (see
        `adrenaline_tonic`). Range: 0.0 (nothing outstanding) to 1.0
        (maximum startle). Consumed by `arousal` above as a short, sharp,
        self-fading lift on top of whatever arousal already is.
        """
        return max(0.0, min(1.0, self.adrenaline_tonic + self.adrenaline_phasic))

    def release_adrenaline(self, amount: float) -> float:
        """Fire an acute startle/interruption/shock response.

        Mirrors `release_cortisol`/`release_dopamine`'s "peak relative to the
        tonic floor" shape exactly, even though that floor is always zero
        here (see `adrenaline_tonic`) -- so a future change to the shared
        release pattern only has to be reasoned about once, not separately
        for a channel that looks superficially different.
        """
        amount = self._burst_amount(amount, "adrenaline")
        if amount <= 0.0:
            return self.adrenaline

        target_total = min(1.0, self.adrenaline + amount)
        self.adrenaline_phasic_peak = max(0.0, target_total - self.adrenaline_tonic)
        self.adrenaline_phasic_at = clock.time()
        return self.adrenaline


class StateService:
    """Manages Internal State continuity and Neo4j persistence."""

    @staticmethod
    def _trace_enabled() -> bool:
        # Runtime import avoids app.state -> app.cognitive.__init__ -> core -> app.state.
        from ..cognitive.trace import enabled

        return enabled()

    @staticmethod
    def _emit_trace(kind: str, **fields: Any) -> None:
        from ..cognitive.trace import emit

        emit(kind, **fields)

    def __init__(
        self,
        graph_store=None,
        db_path: str | None = None,
        redis_host: str | None = None,
        redis_port: int | None = None,
        publish_cb=None,
        persona: "PersonaProfile | None" = None,
        writer_id: str = "",
    ):
        from .runtime_paths import redis_endpoint, runtime_state_db

        self.graph = graph_store
        # F-016: resolved under the deployment's data directory, not the
        # working directory. The subconscious gets its own file: in
        # production both processes share the `/app/data` volume, and
        # sharing one file would have each overwrite the other's row.
        if db_path is None:
            filename = (
                "state_cache_subconscious.db"
                if writer_id == "subconscious_agent"
                else "state_cache.db"
            )
            db_path = runtime_state_db(filename, legacy_filename="state_cache.db")
        self.db_path = db_path
        self.publish_cb = publish_cb
        # Stamped onto `current_state.writer_id` on every `persist_state` call --
        # identifies which OS process (brain vs subconscious) last wrote this revision.
        # Empty string is a valid default for non-agent callers (tests, scripts).
        self.writer_id = writer_id
        # Temperament has to exist before the state it initialises.
        self.persona = persona or PersonaProfile.load()
        self.current_state = AgentState(
            baseline_valence=self.persona.baseline_valence,
            baseline_arousal=self.persona.baseline_arousal,
            baseline_dominance=self.persona.baseline_dominance,
            # A friend starts where its author placed it, then lives its own way
            # from there -- these are seeds, not settings (Tier.ADAPTIVE).
            mood=self.persona.baseline_valence,
            energy=self.persona.baseline_arousal,
            dominance=self.persona.baseline_dominance,
            trust_benevolence=self.persona.initial_trust,
            trust_competence=self.persona.initial_trust,
            trust_integrity=self.persona.initial_trust,
            attachment=self.persona.initial_attachment,
            persons={
                "default_user": PersonModel(
                    person_id="default_user",
                    trust_benevolence=self.persona.initial_trust,
                    trust_competence=self.persona.initial_trust,
                )
            },
            # Named rather than **-unpacked: a generic dict[str, float] spread
            # makes mypy check every AgentState field for float-compatibility,
            # not just these two.
            dopamine_halflife_s=self.persona.hormone_halflives()["dopamine_halflife_s"],
            cortisol_halflife_s=self.persona.hormone_halflives()["cortisol_halflife_s"],
            adrenaline_halflife_s=self.persona.hormone_halflives()[
                "adrenaline_halflife_s"
            ],
        )
        self.proactive_goals: list[GoalRecord] = []
        self.last_speculative_intent = None  # Transient sensory state
        # A2: serializes short-term affect mutation so the fire-and-forget
        # System-2 semantic-drift task cannot clobber a fresher appraisal.
        self._state_lock = asyncio.Lock()
        # Derived control state is immutable and replaced only while holding
        # `_state_lock`. It is a read-only input for downstream consumers.
        from ..cognitive.global_controls import GlobalControls

        self._global_controls = GlobalControls()
        # Separate from `_state_lock` on purpose. This one serializes the *write
        # ordering* in `persist_state`, which now yields at each `to_thread`
        # dispatch; callers reach `persist_state` from methods already holding
        # `_state_lock`, so reusing it would deadlock.
        self._persist_lock = asyncio.Lock()

        # P4-8: strong-reference holder for the fire-and-forget
        # state.broadcast publish below.
        self._background_tasks: set[asyncio.Task] = set()

        # Connect to Redis. The endpoint comes from `Config.REDIS_URL` like
        # every other client (F-016: this one used to hardcode localhost);
        # an empty URL disables it.
        self.redis_client: redis.Redis | None = None
        endpoint = (
            (redis_host or "127.0.0.1", redis_port or 6379)
            if redis_host or redis_port
            else redis_endpoint()
        )
        if endpoint is not None:
            try:
                self.redis_client = redis.Redis(
                    host=endpoint[0],
                    port=endpoint[1],
                    db=0,
                    socket_connect_timeout=1.0,
                    decode_responses=True,
                )
                self.redis_client.ping()
            except Exception:
                self.redis_client = None

        self._initialize_sqlite()

        # --- Psychological Coefficients (section2.4) ---
        # These are the agent's temperament, so they come from the persona rather
        # than from process-global config. `PersonaProfile.from_config()` reads
        # the same PSYCH_* settings as before, so an unconfigured deployment is
        # unchanged; a persona file overrides them per-friend.
        coefficients = self.persona.coefficients()
        self.alpha = coefficients["alpha"]  # Valence drift rate
        self.beta = coefficients["beta"]  # Arousal response rate
        self.gamma = coefficients["gamma"]  # Dominance stability
        self.delta = coefficients["delta"]  # Trust change rate (Marsh)
        self.epsilon = coefficients["epsilon"]  # Attachment growth rate (Bowlby)
        self.lambda_decay = coefficients["lambda_decay"]  # ALMA decay

        # Legacy coefficients (kept for idle evolution)
        self.trust_baseline = 0.5
        self.sensory_weight = getattr(Config, "STATE_SENSORY_WEIGHT", 0.20)
        self.min_perception_confidence = getattr(
            Config, "MIN_PERCEPTION_CONFIDENCE", 0.55
        )
        self.sensory_persist_interval = getattr(
            Config, "STATE_SENSORY_PERSIST_INTERVAL", 2.0
        )
        self._last_sensory_persist = 0.0

    def _get_active_person_model_locked(self) -> PersonModel:
        """Return and synchronize the active person's model while locked."""
        active_person_id = self.current_state.active_person_id
        person = self.current_state.persons.get(active_person_id)
        if person is None:
            person = PersonModel(
                person_id=active_person_id,
                trust_benevolence=self.current_state.trust_benevolence,
                trust_competence=self.current_state.trust_competence,
            )
            self.current_state.persons[active_person_id] = person
        self._sync_active_person_trust_locked(person)
        return person

    def _affect_snapshot(
        self, *fields: str, energy_arousal: bool = False
    ) -> dict[str, float]:
        """Capture selected affect dimensions for an enabled trace sink."""
        state = self.current_state
        values = {
            "mood": state.mood,
            "valence": state.valence,
            "arousal": state.energy if energy_arousal else state.arousal,
            "dominance": state.dominance,
        }
        names = fields or ("mood", "arousal", "dominance")
        return {name: values[name] for name in names}

    def _trust_snapshot(self, *fields: str) -> dict[str, float]:
        """Capture selected trust dimensions for an enabled trace sink."""
        state = self.current_state
        values = {
            "trust": state.trust,
            "benevolence": state.trust_benevolence,
            "competence": state.trust_competence,
            "integrity": state.trust_integrity,
            "attachment": state.attachment,
        }
        names = fields or ("benevolence", "competence", "integrity")
        return {name: values[name] for name in names}

    def _trace_update(
        self,
        kind: str,
        cause: str,
        inputs: dict[str, Any],
        before: dict[str, float],
        after: dict[str, float],
        **fields: Any,
    ) -> None:
        """Emit a state change with deltas derived from its snapshots."""
        self._emit_trace(
            kind,
            cause=cause,
            inputs=inputs,
            before=before,
            after=after,
            delta={key: after[key] - before[key] for key in before},
            **fields,
        )

    def _trace_proactive(self, **fields: Any) -> None:
        """Emit only proactive-decision fields available at this gate."""
        if self._trace_enabled():
            self._emit_trace(
                "proactive.decision",
                **{key: value for key, value in fields.items() if value is not None},
            )

    def _sync_active_person_trust_locked(
        self,
        person: PersonModel,
        *,
        cause: str = "active_person_sync",
        inputs: dict[str, Any] | None = None,
    ) -> None:
        """Mirror active per-person trust into legacy state fields.

        Callers hold ``_state_lock`` before invoking this helper. Integrity is
        intentionally left untouched because this package only grounds
        competence and benevolence in reliance and rupture outcomes.
        """
        before = (
            self._trust_snapshot("competence", "benevolence")
            if self._trace_enabled()
            else None
        )
        self.current_state.trust_competence = person.trust_competence
        self.current_state.trust_benevolence = person.trust_benevolence
        if before is not None:
            after = self._trust_snapshot("competence", "benevolence")
            self._trace_update(
                "trust.update",
                cause,
                inputs
                if inputs is not None
                else {
                    "person_benevolence": person.trust_benevolence,
                    "person_competence": person.trust_competence,
                },
                before,
                after,
                person_id=person.person_id,
            )

    async def set_active_person(self, person_id: str) -> PersonModel:
        """Select a person and synchronize their trust into legacy scalars."""
        async with self._state_lock:
            self.current_state.active_person_id = person_id
            person = self._get_active_person_model_locked()
            self._sync_active_person_trust_locked(person)
            return person

    async def get_active_person_model(self) -> PersonModel:
        """Return the active person's model under the state lock."""
        async with self._state_lock:
            return self._get_active_person_model_locked()

    async def update_active_person_reliance(
        self, outcome_success: bool, stake_weight: float = 0.5
    ) -> None:
        """Apply a reliance outcome to the active person under the state lock."""
        async with self._state_lock:
            person = self._get_active_person_model_locked()
            person.update_trust_from_reliance(outcome_success, stake_weight)
            if self._trace_enabled():
                self._sync_active_person_trust_locked(
                    person,
                    cause="reliance_update",
                    inputs={
                        "outcome_success": outcome_success,
                        "stake_weight": stake_weight,
                    },
                )
            else:
                self._sync_active_person_trust_locked(person)

    async def record_active_person_rupture_repair(
        self, kind: str, magnitude: float, notes: str = ""
    ) -> None:
        """Record an active-person rupture or repair under the state lock."""
        async with self._state_lock:
            person = self._get_active_person_model_locked()
            person.record_rupture_repair(kind, magnitude, notes)
            if self._trace_enabled():
                self._sync_active_person_trust_locked(
                    person,
                    cause="rupture_repair",
                    inputs={"kind": kind, "magnitude": magnitude},
                )
            else:
                self._sync_active_person_trust_locked(person)

    def _refresh_global_controls_locked(
        self, *, urgency: float = 0.0, prediction_error: float = 0.0
    ) -> None:
        """Derive and replace controls while the caller holds `_state_lock`."""
        from ..cognitive.global_controls import derive_global_controls

        self._global_controls = derive_global_controls(
            {
                "valence": self.current_state.valence,
                "arousal": self.current_state.arousal,
                "dominance": self.current_state.dominance,
            },
            load=self.current_state.fatigue,
            urgency=urgency,
            prediction_error=prediction_error,
        )

    def get_global_controls(self) -> "GlobalControls":
        """Return the immutable control snapshot for action selection."""
        controls = getattr(self, "_global_controls", None)
        if controls is not None:
            return controls
        # Preserve the minimal `object.__new__(StateService)` construction used
        # by persistence/lock audit tests without adding mutable control state.
        from ..cognitive.global_controls import GlobalControls

        return GlobalControls()

    def _initialize_sqlite(self):
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_state (
                    agent_name TEXT PRIMARY KEY,
                    mood REAL,
                    energy REAL,
                    dominance REAL,
                    trust_benevolence REAL,
                    trust_competence REAL,
                    trust_integrity REAL,
                    trust REAL,
                    attachment REAL,
                    fatigue REAL,
                    last_user_interaction REAL,
                    interaction_count INTEGER,
                    inferred_valence REAL,
                    inferred_arousal REAL,
                    implied_goals TEXT,
                    known_concepts TEXT,
                    baseline_valence REAL DEFAULT 0.0,
                    baseline_arousal REAL DEFAULT 0.5,
                    baseline_dominance REAL DEFAULT 0.5,
                    last_proactive_attempt REAL DEFAULT 0.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_user_activity_hours (
                    agent_name TEXT PRIMARY KEY,
                    hours_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_proactive_goals (
                    agent_name TEXT PRIMARY KEY,
                    goals_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            # `CREATE TABLE IF NOT EXISTS` does not add a column to a table
            # that already exists from before this field did. Migrated
            # separately by checking pragma_table_info first.
            cursor = conn.execute("PRAGMA table_info(agent_state)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "last_proactive_attempt" not in existing_cols:
                conn.execute(
                    "ALTER TABLE agent_state ADD COLUMN last_proactive_attempt REAL DEFAULT 0.0"
                )
        conn.close()

    async def hydrate_state(self, agent_name: str = "my friend"):
        """Loads state from Redis or local SQLite cache.

        Takes `_state_lock` like every other mutation path. Hydration is called
        once from `CognitiveService.initialize`, so today nothing races it --
        but it writes roughly twenty `current_state` fields one at a time, and
        it was the only writer not holding the lock. A future caller that
        re-hydrates mid-session would interleave with the fire-and-forget
        System-2 appraisal and leave a state that is half restored and half
        appraised, which is precisely the failure the lock exists to prevent.
        """
        logger.info(f"[State] Hydrating {agent_name} from Redis/SQLite...")
        async with self._state_lock:
            await self._hydrate_locked(agent_name)
            # Lock audit harnesses replace `_hydrate_locked` on a partial
            # service. A real service always has `current_state` here.
            if hasattr(self, "current_state"):
                self._refresh_global_controls_locked()

    def _apply_redis_state(self, data: dict[str, str]) -> None:
        """Load one Redis `state:<agent>` hash into current_state."""
        self.current_state.mood = float(data.get("mood", 0.0))
        self.current_state.energy = float(data.get("energy", 0.5))
        self.current_state.dominance = float(data.get("dominance", 0.5))
        self.current_state.trust_benevolence = float(data.get("trust_benevolence", 0.5))
        self.current_state.trust_competence = float(data.get("trust_competence", 0.5))
        self.current_state.trust_integrity = float(data.get("trust_integrity", 0.5))
        self.current_state.attachment = float(data.get("attachment", 0.1))
        self.current_state.fatigue = float(data.get("fatigue", 0.0))
        self.current_state.last_user_interaction = float(
            data.get("last_user_interaction", clock.time())
        )
        self.current_state.last_proactive_attempt = float(
            data.get("last_proactive_attempt", 0.0)
        )
        hours = _parse_interaction_hours(data.get("user_interaction_hours"))
        if hours is not None:
            self.current_state.user_interaction_hours = hours
        goals = _parse_proactive_goals(data.get("proactive_goals"))
        if goals is not None:
            self.proactive_goals = goals
        self.current_state.interaction_count = int(data.get("interaction_count", 0))
        self.current_state.user_mental_model.inferred_valence = float(
            data.get("inferred_valence", 0.0)
        )
        self.current_state.user_mental_model.inferred_arousal = float(
            data.get("inferred_arousal", 0.5)
        )
        self.current_state.user_mental_model.implied_goals = json.loads(
            data.get("implied_goals", "[]")
        )
        self.current_state.user_mental_model.known_concepts = json.loads(
            data.get("known_concepts", "[]")
        )
        self.current_state.baseline_valence = float(data.get("baseline_valence", 0.0))
        self.current_state.baseline_arousal = float(data.get("baseline_arousal", 0.5))
        self.current_state.baseline_dominance = float(
            data.get("baseline_dominance", 0.5)
        )

    def _apply_sqlite_state(
        self, conn: sqlite3.Connection, row: sqlite3.Row, agent_name: str
    ) -> None:
        """Load the SQLite agent_state row and its side tables."""
        self.current_state.mood = row["mood"]
        self.current_state.energy = row["energy"]
        self.current_state.dominance = row["dominance"]
        self.current_state.trust_benevolence = row["trust_benevolence"]
        self.current_state.trust_competence = row["trust_competence"]
        self.current_state.trust_integrity = row["trust_integrity"]
        self.current_state.attachment = row["attachment"]
        self.current_state.fatigue = row["fatigue"]
        self.current_state.last_user_interaction = row["last_user_interaction"]
        self.current_state.last_proactive_attempt = row["last_proactive_attempt"] or 0.0
        self.current_state.interaction_count = row["interaction_count"]
        self.current_state.user_mental_model.inferred_valence = row["inferred_valence"]
        self.current_state.user_mental_model.inferred_arousal = row["inferred_arousal"]
        self.current_state.user_mental_model.implied_goals = json.loads(
            row["implied_goals"] or "[]"
        )
        self.current_state.user_mental_model.known_concepts = json.loads(
            row["known_concepts"] or "[]"
        )
        self.current_state.baseline_valence = row["baseline_valence"]
        self.current_state.baseline_arousal = row["baseline_arousal"]
        self.current_state.baseline_dominance = row["baseline_dominance"]
        activity_row = conn.execute(
            "SELECT hours_json FROM agent_user_activity_hours WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        hours = _parse_interaction_hours(activity_row[0] if activity_row else None)
        if hours is not None:
            self.current_state.user_interaction_hours = hours
        goals_row = conn.execute(
            "SELECT goals_json FROM agent_proactive_goals WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        goals = _parse_proactive_goals(goals_row[0] if goals_row else None)
        if goals is not None:
            self.proactive_goals = goals

    async def _hydrate_locked(self, agent_name: str):
        # 1. Try Redis
        if self.redis_client:
            try:
                # Off the loop: synchronous client, and a dead Redis costs a
                # full connect timeout here rather than returning quickly.
                # redis-py's sync Redis shares stubs with the async client, so
                # hgetall is typed Awaitable[dict] | dict even on this
                # always-sync client -- cast to the runtime-true shape.
                data = cast(
                    "dict[str, str]",
                    await asyncio.to_thread(
                        self.redis_client.hgetall, f"state:{agent_name}"
                    ),
                )
                if data:
                    self._apply_redis_state(data)
                    watermark = await asyncio.to_thread(
                        self.redis_client.get,
                        f"proactive-watermark:{agent_name}",
                    )
                    if watermark is not None:
                        self.current_state.last_proactive_attempt = max(
                            self.current_state.last_proactive_attempt,
                            float(watermark),
                        )
                    logger.debug("[State] Hydrated successfully from Redis.")
                    return
            except Exception as e:
                logger.warning(f"Failed to hydrate state from Redis: {e}")

        # 2. Try SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM agent_state WHERE agent_name = ?", (agent_name,)
                )
                row = cursor.fetchone()
                if row:
                    self._apply_sqlite_state(conn, row, agent_name)
                    logger.debug("[State] Hydrated successfully from SQLite.")
                    return
        except Exception as e:
            logger.error(f"Failed to hydrate state from SQLite: {e}")

        # 3. Fallback to Neo4j graph_store if self.graph is available
        if self.graph:
            try:
                query = "MATCH (a:Agent {name: $name}) RETURN a"
                res = await self.graph.execute_query(query, {"name": agent_name})
                if res and len(res) > 0:
                    agent_node = res[0].get("a")
                    if agent_node:
                        self.current_state.mood = float(agent_node.get("mood", 0.0))
                        self.current_state.energy = float(agent_node.get("energy", 0.5))
                        self.current_state.dominance = float(
                            agent_node.get("dominance", 0.5)
                        )
                        self.current_state.trust_benevolence = float(
                            agent_node.get("trust_benevolence", 0.5)
                        )
                        self.current_state.trust_competence = float(
                            agent_node.get("trust_competence", 0.5)
                        )
                        self.current_state.trust_integrity = float(
                            agent_node.get("trust_integrity", 0.5)
                        )
                        self.current_state.attachment = float(
                            agent_node.get("attachment", 0.1)
                        )
                        self.current_state.fatigue = float(
                            agent_node.get("fatigue", 0.0)
                        )
                        self.current_state.last_user_interaction = float(
                            agent_node.get("last_user_interaction", clock.time())
                        )
                        self.current_state.last_proactive_attempt = float(
                            agent_node.get("last_proactive_attempt", 0.0)
                        )
                        self.current_state.interaction_count = int(
                            agent_node.get("interaction_count", 0)
                        )
                        self.current_state.user_mental_model.inferred_valence = float(
                            agent_node.get("inferred_valence", 0.0)
                        )
                        self.current_state.user_mental_model.inferred_arousal = float(
                            agent_node.get("inferred_arousal", 0.5)
                        )

                        goals = agent_node.get("implied_goals", [])
                        if isinstance(goals, str):
                            try:
                                goals = json.loads(goals)
                            except Exception:
                                goals = []
                        self.current_state.user_mental_model.implied_goals = (
                            goals if isinstance(goals, list) else []
                        )

                        concepts = agent_node.get("known_concepts", [])
                        if isinstance(concepts, str):
                            try:
                                concepts = json.loads(concepts)
                            except Exception:
                                concepts = []
                        self.current_state.user_mental_model.known_concepts = (
                            concepts if isinstance(concepts, list) else []
                        )

                        self.current_state.baseline_valence = float(
                            agent_node.get("baseline_valence", 0.0)
                        )
                        self.current_state.baseline_arousal = float(
                            agent_node.get("baseline_arousal", 0.5)
                        )
                        self.current_state.baseline_dominance = float(
                            agent_node.get("baseline_dominance", 0.5)
                        )
                        logger.debug(
                            "[State] Hydrated successfully from Neo4j fallback."
                        )
            except Exception as e:
                logger.warning(f"Failed fallback hydration from Neo4j: {e}")

    async def apply_external_state(self, data: dict[str, Any]) -> None:
        """P1-5: apply a `state.broadcast` snapshot from another process's
        `StateService` onto this one's `current_state`.

        `subconscious_agent` and `brain_agent` run as separate OS processes
        (`ARCHITECTURE.md`), so they can never share one `AgentState` object
        -- the only channel between them is the mesh. `persist_state` already
        broadcasts the full snapshot on `state.broadcast` after every real
        appraisal/interaction update (rate-limited to
        `STATE_SENSORY_PERSIST_INTERVAL` on the sensory path, uncapped on
        appraisal/event/tick/ToM updates), so it is close to real-time,
        unlike polling `hydrate_state` on a timer -- and it needed no new
        publisher, since the brain already broadcasts this to no listener
        that applied it. `_on_state_broadcast` (subconscious_agent.py)
        called this method's Neo4j-only predecessor and now also calls this.

        Takes `_state_lock` like every other mutation path, same reasoning
        as `hydrate_state`: applied under the lock, a receiver processing a
        broadcast mid fire-and-forget System-2 appraisal cannot leave
        `current_state` half-overwritten and half-appraised.

        Same field set as `_hydrate_locked`'s Redis/SQLite/Neo4j branches,
        applied from the broadcast dict instead -- this is the fourth
        source of the same shape, not a new one.
        """
        async with self._state_lock:
            # Compare-and-swap guard. A broadcast is "stale" when it carries a revision
            # older than what this process already holds -- expected under normal mesh
            # delivery, not an error, so this logs and ignores without conflict error.
            incoming_revision = data.get("revision")
            if incoming_revision is not None:
                incoming_revision = int(incoming_revision)
                if incoming_revision < self.current_state.revision:
                    # Proactive attempts are a monotonic cooldown watermark.
                    # The subconscious owns the attempt until the next brain
                    # tick; an older snapshot must never erase that mark.
                    try:
                        incoming_attempt = float(
                            data.get("last_proactive_attempt", 0.0)
                        )
                    except (TypeError, ValueError):
                        incoming_attempt = 0.0
                    self.current_state.last_proactive_attempt = max(
                        self.current_state.last_proactive_attempt,
                        incoming_attempt,
                    )
                    logger.debug(
                        "[State] %s: stale state.broadcast rejected "
                        "(incoming revision %d < current %d, writer=%r)",
                        StateConflictError.__name__,
                        incoming_revision,
                        self.current_state.revision,
                        data.get("writer_id", ""),
                    )
                    return
                if (
                    incoming_revision == self.current_state.revision
                    and data.get("writer_id", "") != self.current_state.writer_id
                    and self.current_state.writer_id != ""
                ):
                    # Two writers produced the same revision number -- a
                    # design hazard section18 Experiment 3 measures, not resolved
                    # here. Applied anyway (rejecting would be just as
                    # arbitrary), but logged distinctly so it's visible.
                    logger.warning(
                        "[State] Equal-revision write conflict: incoming "
                        "writer=%r, current writer=%r, revision=%d",
                        data.get("writer_id", ""),
                        self.current_state.writer_id,
                        incoming_revision,
                    )

            self.current_state.mood = float(data.get("mood", self.current_state.mood))
            self.current_state.energy = float(
                data.get("energy", self.current_state.energy)
            )
            self.current_state.dominance = float(
                data.get("dominance", self.current_state.dominance)
            )
            self.current_state.trust_benevolence = float(
                data.get("trust_benevolence", self.current_state.trust_benevolence)
            )
            self.current_state.trust_competence = float(
                data.get("trust_competence", self.current_state.trust_competence)
            )
            self.current_state.trust_integrity = float(
                data.get("trust_integrity", self.current_state.trust_integrity)
            )
            self.current_state.attachment = float(
                data.get("attachment", self.current_state.attachment)
            )
            self.current_state.fatigue = float(
                data.get("fatigue", self.current_state.fatigue)
            )
            self.current_state.last_user_interaction = float(
                data.get(
                    "last_user_interaction", self.current_state.last_user_interaction
                )
            )
            try:
                incoming_attempt = float(data.get("last_proactive_attempt", 0.0))
            except (TypeError, ValueError):
                incoming_attempt = 0.0
            self.current_state.last_proactive_attempt = max(
                self.current_state.last_proactive_attempt, incoming_attempt
            )
            interaction_hours = _parse_interaction_hours(
                data.get("user_interaction_hours")
            )
            if interaction_hours is not None:
                self.current_state.user_interaction_hours = interaction_hours
            self.current_state.interaction_count = int(
                data.get("interaction_count", self.current_state.interaction_count)
            )
            self.current_state.user_mental_model.inferred_valence = float(
                data.get(
                    "inferred_valence",
                    self.current_state.user_mental_model.inferred_valence,
                )
            )
            self.current_state.user_mental_model.inferred_arousal = float(
                data.get(
                    "inferred_arousal",
                    self.current_state.user_mental_model.inferred_arousal,
                )
            )
            implied_goals = data.get("implied_goals")
            if isinstance(implied_goals, list):
                self.current_state.user_mental_model.implied_goals = implied_goals
            known_concepts = data.get("known_concepts")
            if isinstance(known_concepts, list):
                self.current_state.user_mental_model.known_concepts = known_concepts
            proactive_goals = _parse_proactive_goals(data.get("proactive_goals"))
            if proactive_goals is not None:
                self.proactive_goals = proactive_goals
            self.current_state.baseline_valence = float(
                data.get("baseline_valence", self.current_state.baseline_valence)
            )
            self.current_state.baseline_arousal = float(
                data.get("baseline_arousal", self.current_state.baseline_arousal)
            )
            self.current_state.baseline_dominance = float(
                data.get("baseline_dominance", self.current_state.baseline_dominance)
            )
            if incoming_revision is not None:
                self.current_state.revision = incoming_revision
                self.current_state.writer_id = data.get(
                    "writer_id", self.current_state.writer_id
                )
            self._refresh_global_controls_locked()

    async def persist_state(self, agent_name: str = "my friend"):
        """Save state to Redis and the local SQLite cache, then broadcast.

        Serialized on `_persist_lock`, which is deliberately *not* `_state_lock`.
        Moving the two writes onto worker threads made `persist_state` yield at
        each `await`, so two overlapping calls could have their writes complete
        in the opposite order and leave an older snapshot on top of a newer one
        -- a regression introduced by the same change that took the blocking
        work off the loop. The lock restores ordering without putting it back:
        it is an asyncio lock, so the rest of the loop keeps running while a
        persist is in flight.

        It cannot be `_state_lock`: callers reach here from methods that already
        hold that lock, so sharing it would deadlock. This one guards *write
        ordering*, not the state fields.
        """
        async with self._persist_lock:
            # Increment revision counter per persist call inside the persist lock,
            # ensuring revision ordering strictly matches disk/cache write ordering.
            self.current_state.revision += 1
            self.current_state.writer_id = self.writer_id
            # One snapshot, taken once, used by both stores. Previously the
            # Redis mapping was built before its await and the SQL parameters
            # after it, so a single call could write two different states to the
            # two backends.
            snapshot = {
                "revision": self.current_state.revision,
                "writer_id": self.current_state.writer_id,
                "mood": self.current_state.mood,
                "energy": self.current_state.energy,
                "dominance": self.current_state.dominance,
                "trust_benevolence": self.current_state.trust_benevolence,
                "trust_competence": self.current_state.trust_competence,
                "trust_integrity": self.current_state.trust_integrity,
                "trust": self.current_state.trust,
                "attachment": self.current_state.attachment,
                "fatigue": self.current_state.fatigue,
                "last_user_interaction": self.current_state.last_user_interaction,
                "last_proactive_attempt": self.current_state.last_proactive_attempt,
                "user_interaction_hours": list(
                    self.current_state.user_interaction_hours
                ),
                "interaction_count": self.current_state.interaction_count,
                "inferred_valence": self.current_state.user_mental_model.inferred_valence,
                "inferred_arousal": self.current_state.user_mental_model.inferred_arousal,
                "baseline_valence": self.current_state.baseline_valence,
                "baseline_arousal": self.current_state.baseline_arousal,
                "baseline_dominance": self.current_state.baseline_dominance,
                "global_controls": self.get_global_controls().model_dump(),
                # Serialized once, here, for all three destinations. Dumped
                # separately before each await, one persist could write three
                # different goal lists if a thought was recorded mid-persist.
                "proactive_goals": _goal_payload(self.proactive_goals),
            }
            goals_json = json.dumps(snapshot["proactive_goals"])

            # 1. Save to Redis
            if self.redis_client:
                try:
                    # Off-thread: `redis.Redis` is the synchronous client and
                    # this runs on every persist, including the per-turn path.
                    # Called directly it blocks the loop for a network round
                    # trip mid-conversation. redis-py holds a connection pool
                    # and is thread-safe for commands.
                    if snapshot["last_proactive_attempt"] > 0:
                        await asyncio.to_thread(
                            self.redis_client.eval,
                            _PROACTIVE_WATERMARK_MAX_SCRIPT,
                            1,
                            f"proactive-watermark:{agent_name}",
                            str(snapshot["last_proactive_attempt"]),
                        )
                    await asyncio.to_thread(
                        self.redis_client.hset,
                        f"state:{agent_name}",
                        mapping={
                            "mood": str(snapshot["mood"]),
                            "energy": str(snapshot["energy"]),
                            "dominance": str(snapshot["dominance"]),
                            "trust_benevolence": str(snapshot["trust_benevolence"]),
                            "trust_competence": str(snapshot["trust_competence"]),
                            "trust_integrity": str(snapshot["trust_integrity"]),
                            "trust": str(snapshot["trust"]),
                            "attachment": str(snapshot["attachment"]),
                            "fatigue": str(snapshot["fatigue"]),
                            "last_user_interaction": str(
                                snapshot["last_user_interaction"]
                            ),
                            "last_proactive_attempt": str(
                                snapshot["last_proactive_attempt"]
                            ),
                            "user_interaction_hours": json.dumps(
                                snapshot["user_interaction_hours"]
                            ),
                            "proactive_goals": goals_json,
                            "interaction_count": str(snapshot["interaction_count"]),
                            "inferred_valence": str(snapshot["inferred_valence"]),
                            "inferred_arousal": str(snapshot["inferred_arousal"]),
                            "baseline_valence": str(snapshot["baseline_valence"]),
                            "baseline_arousal": str(snapshot["baseline_arousal"]),
                            "baseline_dominance": str(snapshot["baseline_dominance"]),
                            "implied_goals": json.dumps(
                                self.current_state.user_mental_model.implied_goals
                            ),
                            "known_concepts": json.dumps(
                                self.current_state.user_mental_model.known_concepts
                            ),
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist state to Redis: {e}")

            # 2. Save to SQLite cache
            sqlite_params = (
                agent_name,
                snapshot["mood"],
                snapshot["energy"],
                snapshot["dominance"],
                snapshot["trust_benevolence"],
                snapshot["trust_competence"],
                snapshot["trust_integrity"],
                snapshot["trust"],
                snapshot["attachment"],
                snapshot["fatigue"],
                snapshot["last_user_interaction"],
                snapshot["interaction_count"],
                snapshot["inferred_valence"],
                snapshot["inferred_arousal"],
                json.dumps(self.current_state.user_mental_model.implied_goals),
                json.dumps(self.current_state.user_mental_model.known_concepts),
                snapshot["baseline_valence"],
                snapshot["baseline_arousal"],
                snapshot["baseline_dominance"],
                snapshot["last_proactive_attempt"],
            )
            try:
                await asyncio.to_thread(
                    self._write_state_row,
                    sqlite_params,
                    json.dumps(
                        snapshot["user_interaction_hours"][-_MAX_INTERACTION_HOURS:]
                    ),
                    goals_json,
                )
            except Exception as e:
                logger.error(f"Failed to persist state to SQLite: {e}")

        # 3. Publish asynchronous NATS broadcast
        if self.publish_cb:
            state_data = {
                "agent_name": agent_name,
                **snapshot,
                "implied_goals": self.current_state.user_mental_model.implied_goals,
                "known_concepts": self.current_state.user_mental_model.known_concepts,
                "timestamp": clock.time(),
            }
            try:
                # Fire and forget publishing. P4-8: spawn_background retains
                # a strong reference so the broadcast cannot be silently
                # cancelled by the garbage collector mid-publish.
                spawn_background(
                    self._background_tasks,
                    self.publish_cb("state.broadcast", state_data),
                )
            except Exception as e:
                logger.warning(f"Failed to trigger NATS state broadcast task: {e}")

        logger.debug(
            f"[State] Persisted to cache (non-blocking Neo4j): V={snapshot['mood']:.2f} Ar={snapshot['energy']:.2f} D={snapshot['dominance']:.2f}"
        )

    def _write_state_row(self, params, hours_json: str, goals_json: str) -> None:
        """Write the agent_state row and the two W9 side tables in one
        transaction. Synchronous; called only via `to_thread`.

        One connection and one commit for all three: written separately, a
        crash between them left a state row whose rhythm or thought history
        belonged to a different persist, and each persist paid three opens,
        three commits and three thread hops on every tick.

        Split out of `persist_state` so the blocking `sqlite3` work happens off
        the event loop -- it ran on every persist, from five call sites
        including the per-turn path, so the loop stalled on a disk write while
        the agent was mid-conversation.

        Opening a connection per call is deliberate rather than lazy: `sqlite3`
        connections are not safe to share across threads by default, so a cached
        one would need its own guard.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO agent_state (
                        agent_name, mood, energy, dominance, trust_benevolence, trust_competence,
                        trust_integrity, trust, attachment, fatigue, last_user_interaction,
                        interaction_count, inferred_valence, inferred_arousal, implied_goals,
                        known_concepts, baseline_valence, baseline_arousal, baseline_dominance,
                        last_proactive_attempt, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(agent_name) DO UPDATE SET
                        mood = excluded.mood,
                        energy = excluded.energy,
                        dominance = excluded.dominance,
                        trust_benevolence = excluded.trust_benevolence,
                        trust_competence = excluded.trust_competence,
                        trust_integrity = excluded.trust_integrity,
                        trust = excluded.trust,
                        attachment = excluded.attachment,
                        fatigue = excluded.fatigue,
                        last_user_interaction = excluded.last_user_interaction,
                        interaction_count = excluded.interaction_count,
                        inferred_valence = excluded.inferred_valence,
                        inferred_arousal = excluded.inferred_arousal,
                        implied_goals = excluded.implied_goals,
                        known_concepts = excluded.known_concepts,
                        baseline_valence = excluded.baseline_valence,
                        baseline_arousal = excluded.baseline_arousal,
                        baseline_dominance = excluded.baseline_dominance,
                        last_proactive_attempt = excluded.last_proactive_attempt,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    params,
                )
                # Rhythm sample and thought history, kept apart from the
                # affect row but committed with it.
                conn.execute(
                    """
                    INSERT INTO agent_user_activity_hours (agent_name, hours_json)
                    VALUES (?, ?)
                    ON CONFLICT(agent_name) DO UPDATE SET hours_json = excluded.hours_json
                    """,
                    (params[0], hours_json),
                )
                conn.execute(
                    """
                    INSERT INTO agent_proactive_goals (agent_name, goals_json)
                    VALUES (?, ?)
                    ON CONFLICT(agent_name) DO UPDATE SET goals_json = excluded.goals_json
                    """,
                    (params[0], goals_json),
                )
        finally:
            # `finally`, not a trailing call: the original leaked the connection
            # whenever the INSERT raised.
            conn.close()

    def record_user_interaction(self):
        """Mark that the user just interacted. Called by BrainAgent on every chat.input."""
        self.current_state.last_user_interaction = clock.time()
        hour = user_hour(self.current_state.last_user_interaction)
        self.current_state.user_interaction_hours.append(hour)
        del self.current_state.user_interaction_hours[:-_MAX_INTERACTION_HOURS]

    def record_proactive_thought(
        self, description: str, *, goal_id: str | None = None
    ) -> tuple[str, float]:
        """Record a raised thought and return its stable id and re-raise rate."""
        normalized = " ".join(description.casefold().split())
        stable_id = goal_id or hashlib.sha256(normalized.encode()).hexdigest()[:16]
        now = clock.time()
        review_due_goals(
            self.proactive_goals,
            now,
            ignore_after_s=Config.PROACTIVE_IGNORE_REVIEW_SECONDS,
        )
        record = next(
            (goal for goal in self.proactive_goals if goal.goal_id == stable_id), None
        )
        if record is None:
            record = GoalRecord(
                goal_id=stable_id,
                type="proactive_thought",
                source="subconscious",
                description=description,
            )
            self.proactive_goals.append(record)
            self.proactive_goals = _bounded_proactive_goals(self.proactive_goals)
        chance = record.re_raise_probability(Config.PROACTIVE_IGNORE_ZERO_AFTER)
        if chance > 0:
            record.record_proactive_raise(now)
        return stable_id, chance

    def resolve_proactive_thoughts(self, user_text: str) -> None:
        """Record clear replies or dismissals; unresolved thoughts expire to ignored."""
        now = clock.time()
        review_due_goals(
            self.proactive_goals,
            now,
            ignore_after_s=Config.PROACTIVE_IGNORE_REVIEW_SECONDS,
        )
        normalized = " ".join(user_text.casefold().split())
        dismissal = any(
            phrase in normalized
            for phrase in ("stop asking", "don't ask", "do not ask", "drop this")
        )
        words = set(normalized.split())
        for goal in self.proactive_goals:
            if goal.proactive_raise_count <= len(goal.proactive_outcomes):
                continue
            goal_words = set(goal.description.casefold().split())
            if dismissal:
                goal.record_proactive_outcome("dismissed")
            elif len(words & goal_words) >= 2:
                goal.record_proactive_outcome("acted_on")

    async def apply_semantic_appraisal(self, new_pad: dict[str, float]):
        """Apply System-2 background semantic-drift results to short-term affect.

        Only the write is serialized under the state lock (A2); the expensive
        LLM inference that produced ``new_pad`` runs upstream, outside the lock.
        """
        async with self._state_lock:
            before = (
                self._affect_snapshot("valence", "arousal", "dominance")
                if self._trace_enabled()
                else None
            )
            if "valence" in new_pad and new_pad["valence"] is not None:
                self.current_state.valence = float(new_pad["valence"])
            if "arousal" in new_pad and new_pad["arousal"] is not None:
                self.current_state.arousal = float(new_pad["arousal"])
            if "dominance" in new_pad and new_pad["dominance"] is not None:
                self.current_state.dominance = float(new_pad["dominance"])
            self._enforce_bounds()
            self._refresh_global_controls_locked()
            if before is not None:
                after = self._affect_snapshot("valence", "arousal", "dominance")
                self._trace_update(
                    "affect.update",
                    "semantic_appraisal",
                    {
                        key: value
                        for key, value in new_pad.items()
                        if key in before and value is not None
                    },
                    before,
                    after,
                )

    async def apply_affect_delta(
        self,
        affect_delta: Mapping[str, float],
        *,
        urgency: float = 0.0,
        prediction_error: float = 0.0,
    ) -> "GlobalControls":
        """Apply one appraisal delta and refresh controls atomically.

        The method owns only PAD affect and the derived control snapshot. It
        deliberately has no reference to memory, beliefs, evidence, identity,
        provenance, or safety state, which preserves content isolation.
        """

        def delta_for(*names: str) -> float:
            for name in names:
                value = affect_delta.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
            return 0.0

        async with self._state_lock:
            before = (
                self._affect_snapshot("valence", "arousal", "dominance")
                if self._trace_enabled()
                else None
            )
            self.current_state.valence += delta_for("pleasure", "valence")
            self.current_state.energy += delta_for("arousal")
            self.current_state.dominance += delta_for("dominance")
            self._enforce_bounds()
            self._refresh_global_controls_locked(
                urgency=urgency, prediction_error=prediction_error
            )
            if before is not None:
                after = self._affect_snapshot("valence", "arousal", "dominance")
                self._trace_update(
                    "affect.update",
                    "appraisal_delta",
                    {
                        "valence": delta_for("pleasure", "valence"),
                        "arousal": delta_for("arousal"),
                        "dominance": delta_for("dominance"),
                        "urgency": urgency,
                        "prediction_error": prediction_error,
                    },
                    before,
                    after,
                )
            return self._global_controls

    async def appraise_and_apply_event(
        self,
        event_metadata: dict[str, Any],
        active_goals: list[str],
        expectation: float = 0.0,
    ) -> tuple["AppraisalRecord", "GlobalControls"]:
        """Appraise one event and atomically apply its genuine control inputs.

        The appraisal reducer remains pure. This bridge owns the stateful step,
        using goal incongruence as urgency and novelty as prediction error
        instead of substituting the legacy relevance proxy for either signal.
        """
        from ..cognitive.appraisal import appraise_event

        appraisal = appraise_event(event_metadata, active_goals, expectation)
        controls = await self.apply_affect_delta(
            appraisal.affect_delta,
            urgency=max(0.0, -appraisal.goal_congruence),
            prediction_error=appraisal.novelty,
        )
        return appraisal, controls

    async def update_from_appraisal(
        self, appraisal, weights: dict[str, float] | None = None
    ):
        """
        PAD + Relational update driven by appraisal vector (section2.3).

        ALMA mood-pull: Each appraisal dimension pulls the corresponding
        PAD dimension toward the appraised value.
        Marsh trust: RI directly modulates trust.
        Bowlby attachment: Trust x interaction frequency.
        """
        G = appraisal.goal_congruence
        RI = appraisal.relationship_impact
        N = appraisal.novelty
        R = appraisal.relevance
        A = appraisal.agency
        NA = appraisal.norm_alignment

        if weights is None:
            weights = {}

        w1 = weights.get("w1_g_to_v", 0.6)
        w2 = weights.get("w2_ri_to_v", 0.4)
        w3 = weights.get("w3_n_to_ar", 0.6)
        w4 = weights.get("w4_r_to_ar", 0.4)
        w5 = weights.get("w5_a_to_d", 0.6)
        w6 = weights.get("w6_na_to_d", 0.4)

        async with self._state_lock:
            tracing = self._trace_enabled()
            if tracing:
                affect_before = self._affect_snapshot(
                    "mood", "arousal", "dominance", energy_arousal=True
                )
                trust_before = self._trust_snapshot(
                    "benevolence", "competence", "integrity", "attachment"
                )
            # PAD mood-pull (section2.3)
            self.current_state.mood = (
                1 - self.alpha
            ) * self.current_state.mood + self.alpha * (w1 * G + w2 * RI)
            self.current_state.energy = (
                1 - self.beta
            ) * self.current_state.energy + self.beta * (w3 * N + w4 * R)
            self.current_state.dominance = (
                1 - self.gamma
            ) * self.current_state.dominance + self.gamma * (w5 * A + w6 * NA)

            # Relational updates (section2.3)
            self.current_state.trust_benevolence = max(
                0.0, min(1.0, self.current_state.trust_benevolence + self.delta * RI)
            )
            self.current_state.trust_competence = max(
                0.0,
                min(
                    1.0,
                    self.current_state.trust_competence
                    + self.delta * (0.6 * G + 0.4 * R),
                ),
            )
            self.current_state.trust_integrity = max(
                0.0, min(1.0, self.current_state.trust_integrity + self.delta * NA)
            )
            self.current_state.interaction_count += 1
            freq = min(1.0, self.current_state.interaction_count / 100.0)
            self.current_state.attachment = max(
                0.0,
                min(
                    1.0,
                    self.current_state.attachment
                    + self.epsilon * self.current_state.trust * freq,
                ),
            )

            self.current_state.last_update = clock.now()
            self._enforce_bounds()
            self._refresh_global_controls_locked(urgency=R, prediction_error=N)
            if tracing:
                affect_after = self._affect_snapshot(
                    "mood", "arousal", "dominance", energy_arousal=True
                )
                trust_after = self._trust_snapshot(
                    "benevolence", "competence", "integrity", "attachment"
                )
                self._trace_update(
                    "affect.update",
                    "appraisal",
                    {
                        "goal_congruence": G,
                        "relationship_impact": RI,
                        "novelty": N,
                        "relevance": R,
                        "agency": A,
                        "norm_alignment": NA,
                    },
                    affect_before,
                    affect_after,
                )
                self._trace_update(
                    "trust.update",
                    "appraisal",
                    {
                        "relationship_impact": RI,
                        "goal_congruence": G,
                        "relevance": R,
                        "norm_alignment": NA,
                        "interaction_count": self.current_state.interaction_count,
                    },
                    trust_before,
                    trust_after,
                    person_id=self.current_state.active_person_id,
                )
        await self.persist_state()

        logger.debug(
            "[State] PAD update: V=%.3f Ar=%.3f D=%.3f T=%.3f At=%.3f",
            self.current_state.mood,
            self.current_state.energy,
            self.current_state.dominance,
            self.current_state.trust,
            self.current_state.attachment,
        )

    async def update_from_event(
        self, event_valence: float, user_trust_delta: float = 0.0
    ):
        """
        Legacy Cognitive Update (backward-compatible).
        Wraps the new appraisal-driven update for code that still uses valence floats.
        """
        async with self._state_lock:
            now = clock.now()
            self.current_state.last_user_interaction = clock.time()
            tracing = self._trace_enabled()
            if tracing:
                affect_before = self._affect_snapshot(
                    "mood", "arousal", energy_arousal=True
                )
                trust_before = self._trust_snapshot("trust", "attachment")

            # Apply Cognitive Weight (0.7)
            self.current_state.mood = (self.current_state.mood * 0.3) + (
                event_valence * 0.7
            )

            self.current_state.trust = max(
                0.0, min(1.0, self.current_state.trust + user_trust_delta)
            )
            self.current_state.attachment += user_trust_delta * 0.1
            self.current_state.energy -= 0.02

            self.current_state.interaction_count += 1
            self.current_state.last_update = now
            self._enforce_bounds()
            self._refresh_global_controls_locked(prediction_error=abs(event_valence))
            if tracing:
                affect_after = self._affect_snapshot(
                    "mood", "arousal", energy_arousal=True
                )
                trust_after = self._trust_snapshot("trust", "attachment")
                self._trace_update(
                    "affect.update",
                    "legacy_event",
                    {"event_valence": event_valence},
                    affect_before,
                    affect_after,
                )
                self._trace_update(
                    "trust.update",
                    "legacy_event",
                    {"user_trust_delta": user_trust_delta},
                    trust_before,
                    trust_after,
                    person_id=self.current_state.active_person_id,
                )
        await self.persist_state()

    async def apply_sensory_perception(self, perception_metadata: dict[str, Any]):
        """
        Acoustic Perception Update (confidence-scaled low weight).
        Triggered by emotional/event cues from an acoustic backend.

        Backends that only transcribe (e.g. Whisper) supply no `emotional_bias`.
        That absence means "no acoustic evidence", NOT "the user sounds neutral" --
        see the note below.
        """
        emotion_bias = perception_metadata.get("emotional_bias")
        confidence = perception_metadata.get("confidence", 1.0)
        events = perception_metadata.get("events", []) or []

        if confidence < self.min_perception_confidence and not events:
            logger.debug(
                "[State] Ignored low-confidence acoustic perception: %.2f",
                confidence,
            )
            return

        # A missing emotion estimate must not be defaulted to 0.0 and blended in.
        # Doing so pulls mood and inferred_valence toward zero on *every*
        # perception, erasing affect that semantic appraisal just established --
        # the agent flattens the more the user speaks. An explicit 0.0 from a
        # model that genuinely predicts emotion is a real neutral reading and is
        # still blended; only absence is skipped. bool is excluded because
        # isinstance(True, int) is True.
        has_emotion_estimate = isinstance(
            emotion_bias, (int, float)
        ) and not isinstance(emotion_bias, bool)

        async with self._state_lock:
            tracing = self._trace_enabled()
            if tracing:
                affect_before = self._affect_snapshot(
                    "mood", "arousal", energy_arousal=True
                )
                trust_before = self._trust_snapshot("trust", "attachment")
            if has_emotion_estimate:
                # Confidence-scaled emotional bias
                weight = self.sensory_weight * max(0.0, min(1.0, confidence))
                self.current_state.mood = (self.current_state.mood * (1 - weight)) + (
                    emotion_bias * weight
                )

                # Drift user mental model's inferred valence based on acoustic cues
                if confidence >= self.min_perception_confidence:
                    user_weight = self.sensory_weight * max(0.0, min(1.0, confidence))
                    self.current_state.user_mental_model.inferred_valence = (
                        (1 - user_weight)
                        * self.current_state.user_mental_model.inferred_valence
                        + user_weight * emotion_bias
                    )

            # Arousal modulation from acoustic events
            for event in events:
                if event == "Laughter":
                    self.current_state.energy = min(
                        1.0, self.current_state.energy + 0.15
                    )
                    self.current_state.trust = min(1.0, self.current_state.trust + 0.05)
                    logger.info(" Agent sensed laughter - Energy/Trust boosted.")
                elif event == "Applause":
                    self.current_state.energy = min(
                        1.0, self.current_state.energy + 0.2
                    )
                    logger.info(" Agent sensed applause - Energy spike.")
                elif event in ["Cough", "Sneeze"]:
                    self.current_state.attachment = min(
                        1.0, self.current_state.attachment + 0.02
                    )
                    logger.debug(
                        f" Agent sensed {event} - Attachment nudged (Empathy)."
                    )

            self._enforce_bounds()
            self._refresh_global_controls_locked()
            if tracing:
                affect_after = self._affect_snapshot(
                    "mood", "arousal", energy_arousal=True
                )
                trust_after = self._trust_snapshot("trust", "attachment")
                if affect_after != affect_before:
                    self._trace_update(
                        "affect.update",
                        "sensory_perception",
                        {"confidence": confidence, "event_count": len(events)},
                        affect_before,
                        affect_after,
                    )
                if trust_after != trust_before:
                    self._trace_update(
                        "trust.update",
                        "sensory_perception",
                        {"event_count": len(events)},
                        trust_before,
                        trust_after,
                        person_id=self.current_state.active_person_id,
                    )
        await self._persist_sensory_state_if_due()

    async def apply_somatic_perception(self, somatic: dict[str, Any]):
        """Visual Somatic Homeostasis -- recognising a comfort object feels good.

        The visual counterpart to `apply_sensory_perception` above: that folds
        *how the user sounds* into mood, this folds *what the agent is looking
        at* into valence and arousal. Together they are the two halves of the
        perception-to-affect path.

        Roadmap sectionC (`docs/FUTURE_FINETUNED_ADAPTER.md`) specifies a dopamine
        spike alongside the valence one, and both now happen literally: the
        valence lift below, and a real phasic burst via `release_dopamine`.
        Before phasic dopamine existed, the burst could only be approximated by
        moving mood and letting the derived term follow, which meant the reward
        vanished the instant valence drifted back.

        Absence of a match must never reach this method as a zero spike. Doing
        so would drag mood toward neutral on every appraisal interval and
        flatten the agent the longer it looks at nothing in particular -- the
        same failure mode documented for a missing acoustic emotion estimate.
        """
        if not somatic:
            return

        valence_spike = somatic.get("valence_spike", 0.0)
        arousal_spike = somatic.get("arousal_spike", 0.0)
        try:
            valence_spike = float(valence_spike)
            arousal_spike = float(arousal_spike)
        except (TypeError, ValueError):
            logger.warning(
                "[State] Ignoring somatic perception with non-numeric spikes: %r",
                somatic,
            )
            return

        if valence_spike <= 0.0 and arousal_spike <= 0.0:
            return

        # Roadmap sectionC: D_t = min(1.0, D_{t-1} + 0.25). Falls back to the valence
        # lift alone when a caller supplies no explicit burst.
        dopamine_spike = somatic.get("dopamine_spike", SOMATIC_DOPAMINE_SPIKE)
        try:
            dopamine_spike = float(dopamine_spike)
        except (TypeError, ValueError):
            dopamine_spike = SOMATIC_DOPAMINE_SPIKE

        entities = somatic.get("entities") or []
        async with self._state_lock:
            before_valence = self.current_state.valence
            tracing = self._trace_enabled()
            before = self._affect_snapshot("valence", "arousal") if tracing else None
            self.current_state.valence = min(
                1.0, self.current_state.valence + valence_spike
            )
            self.current_state.arousal = min(
                1.0, self.current_state.arousal + arousal_spike
            )
            self._enforce_bounds()
            # After bounds, so the burst is measured against the settled tonic.
            self.current_state.release_dopamine(dopamine_spike)
            self._refresh_global_controls_locked()
            after_valence = self.current_state.valence
            if tracing:
                self._trace_update(
                    "affect.update",
                    "somatic_perception",
                    {"valence_spike": valence_spike, "arousal_spike": arousal_spike},
                    before,
                    self._affect_snapshot("valence", "arousal"),
                )

        logger.info(
            "[Vision]  Somatic comfort recognised %s -- valence %.2f -> %.2f (dopamine now %.2f).",
            entities,
            before_valence,
            after_valence,
            self.current_state.dopamine,
        )
        await self._persist_sensory_state_if_due()

    async def apply_facial_reflex(self, reflex: dict[str, Any]):
        """Continuous facial-expression affect (Bucket 13, voice remediation
        Phase 3) -- the reflex-channel counterpart to `apply_somatic_perception`
        above. That folds *recognised scene content* from a slow (5s),
        turn-suspended VLM poll into affect; this folds *facial expression*,
        sampled continuously including mid-turn, via
        `app/vision/reflex.py::score_blendshapes` -- a smile or a flinch the
        VLM path is architecturally too slow to ever see.

        Deltas are *signed*, unlike `apply_somatic_perception`'s always-positive
        spikes: a brow furrow genuinely pulls valence down, not up, and startle
        raises arousal with no valence direction implied either way (see
        `reflex.py`'s docstring for why guessing that direction would be worse
        than not guessing).

        Absence of a signal must never reach this method as an all-zero
        reflex, for the same reason documented on `apply_sensory_perception`
        and `apply_somatic_perception` above -- it would drag affect toward
        neutral on every frame nothing happened, flattening the agent the
        longer a face is simply calm. In practice `score_blendshapes` only
        ever returns signals for a genuine threshold-crossing onset and this
        method is only called once per returned signal, so the failure mode
        is structurally avoided rather than merely checked for -- but the
        check stays, matching the defensive posture of both methods above.
        """
        if not reflex:
            return

        valence_delta = reflex.get("valence_delta", 0.0)
        arousal_delta = reflex.get("arousal_delta", 0.0)
        try:
            valence_delta = float(valence_delta)
            arousal_delta = float(arousal_delta)
        except (TypeError, ValueError):
            logger.warning(
                "[State] Ignoring facial reflex with non-numeric deltas: %r", reflex
            )
            return

        if valence_delta == 0.0 and arousal_delta == 0.0:
            return

        dopamine_spike = reflex.get("dopamine_spike", 0.0)
        try:
            dopamine_spike = float(dopamine_spike)
        except (TypeError, ValueError):
            dopamine_spike = 0.0

        async with self._state_lock:
            before_valence = self.current_state.valence
            tracing = self._trace_enabled()
            before = self._affect_snapshot("valence", "arousal") if tracing else None
            self.current_state.valence = self.current_state.valence + valence_delta
            # `arousal` is a derived property (`energy` + fatigue-restlessness
            # + adrenaline-lift, see its getter above) -- reading it and
            # writing back through its setter (which stores into `energy`
            # alone) would permanently bake whatever fatigue/adrenaline
            # happens to be active right now into the baseline. Apply the
            # delta to `energy` directly instead.
            self.current_state.energy = self.current_state.energy + arousal_delta
            self._enforce_bounds()
            # After bounds, so the burst is measured against the settled tonic --
            # same ordering as apply_somatic_perception, same reason.
            if dopamine_spike > 0.0:
                self.current_state.release_dopamine(dopamine_spike)
            self._refresh_global_controls_locked()
            after_valence = self.current_state.valence
            if tracing:
                self._trace_update(
                    "affect.update",
                    "facial_reflex",
                    {
                        "valence_delta": valence_delta,
                        "arousal_delta": arousal_delta,
                        "dopamine_spike": dopamine_spike,
                    },
                    before,
                    self._affect_snapshot("valence", "arousal"),
                )

        logger.debug(
            "[State] Facial reflex %r -- valence %.3f -> %.3f, arousal delta %+.3f.",
            reflex.get("name", "?"),
            before_valence,
            after_valence,
            arousal_delta,
        )
        await self._persist_sensory_state_if_due()

    async def release_cortisol(self, amount: float, *, reason: str = "") -> float:
        """Fire an acute stress response under the state lock.

        `AgentState.release_cortisol` is the primitive and does no locking. A
        fire-and-forget System-2 appraisal writes affect concurrently with the
        synchronous path (finding A2), and the burst peak is computed *relative
        to the tonic floor* -- so an unlocked release that interleaves with a
        valence write can measure its peak against a floor that no longer
        exists and store a burst of the wrong size.

        This is the API stress channels should call. The dopamine equivalent
        below exists for the same reason.
        """
        async with self._state_lock:
            level = self.current_state.release_cortisol(amount)
        if reason:
            logger.info(
                "[Endocrine] Cortisol released (%s) -- now %.2f.", reason, level
            )
        return level

    async def release_dopamine(self, amount: float, *, reason: str = "") -> float:
        """Fire a reward burst under the state lock. See `release_cortisol`.

        `apply_somatic_perception` does not call this: it is already inside the
        lock and holds it across the valence lift and the burst deliberately,
        so the peak is measured against the settled tonic. Re-entering here
        would deadlock on a non-reentrant `asyncio.Lock`.
        """
        async with self._state_lock:
            level = self.current_state.release_dopamine(amount)
        if reason:
            logger.info(
                "[Endocrine] Dopamine released (%s) -- now %.2f.", reason, level
            )
        return level

    async def release_adrenaline(self, amount: float, *, reason: str = "") -> float:
        """Fire a startle/interruption/shock burst under the state lock.

        See `release_cortisol` for why the lock is mandatory: adrenaline's
        peak is computed relative to its tonic floor exactly like the other
        two (`AgentState.adrenaline_tonic`, always 0.0), so an unlocked
        release racing a concurrent affect write is the same hazard.
        """
        async with self._state_lock:
            level = self.current_state.release_adrenaline(amount)
            self._refresh_global_controls_locked()
        if reason:
            logger.info(
                "[Endocrine] Adrenaline released (%s) -- now %.2f.", reason, level
            )
        return level

    async def handle_system_tick(self, tick_metadata: dict[str, Any]):
        """
        Idle evolution triggered by NATS system.tick.
        Implements ALMA exponential decay (section2.2) and Fatigue updates.

        audit/ROADMAP.md P2-14 (M2-A1): this ran **outside `_state_lock`**,
        mutating fatigue, mood, energy, dominance and all three trust fields
        while every other mutation path in this class held it -- including
        `release_cortisol` and `release_dopamine` directly above, whose
        docstrings spell out why affect mutation must be serialized. That is
        the audit's own stated root cause for this finding: the lock discipline
        is careful everywhere it was thought about, and the tick path was not
        thought about.

        Concretely, the tick arrives on a NATS callback while a fire-and-forget
        System-2 appraisal can be writing affect (finding A2), so an unlocked
        decay could read `mood` before that write and store its result after,
        discarding it. The endocrine layer makes it worse rather than merely
        racy: burst peaks are measured relative to the tonic floor, and the
        tonic floor is a pure function of the valence and arousal this method
        rewrites -- so a release interleaving with an unlocked decay measures
        its peak against a floor that never existed.

        `persist_state` is called from inside the lock deliberately. It
        serializes on `_persist_lock`, which is **not** `_state_lock` precisely
        because callers reach it already holding the state lock (see its own
        docstring); `asyncio.Lock` is not reentrant, so the separation is what
        makes this safe. `_enforce_bounds` and `_update_fatigue_python` are
        synchronous and take no lock.
        """
        now = tick_metadata.get("timestamp", clock.time())
        dt_hours = tick_metadata.get("interval", 60) / 3600.0

        async with self._state_lock:
            tracing = self._trace_enabled()
            if tracing:
                affect_before = self._affect_snapshot(
                    "mood", "arousal", "dominance", energy_arousal=True
                )
                trust_before = self._trust_snapshot(
                    "benevolence", "competence", "integrity"
                )
            # Evolve fatigue
            is_night = in_hour_window(user_hour(now), 22, 6)
            try:
                import cognitive_rust

                rust_state = cognitive_rust.FatigueState(
                    self.current_state.fatigue,
                    self.current_state.last_user_interaction,
                )
                updated_rust = cognitive_rust.update_fatigue(
                    rust_state, now, dt_hours, is_night
                )
                self.current_state.fatigue = updated_rust.fatigue
            except ImportError:
                self._update_fatigue_python(now, dt_hours, is_night)
            except Exception:
                logger.exception(
                    "[State] Unexpected Rust fatigue update error; using Python fallback."
                )
                self._update_fatigue_python(now, dt_hours, is_night)

            # ALMA Decay (section2.2): short-term affect decays back to baseline
            base_v = self.current_state.baseline_valence
            base_ar = self.current_state.baseline_arousal
            base_d = self.current_state.baseline_dominance

            self.current_state.mood = base_v + (
                self.current_state.mood - base_v
            ) * math.exp(-self.lambda_decay * dt_hours)
            self.current_state.energy = base_ar + (
                self.current_state.energy - base_ar
            ) * math.exp(-self.lambda_decay * dt_hours)
            self.current_state.dominance = base_d + (
                self.current_state.dominance - base_d
            ) * math.exp(-self.lambda_decay * dt_hours)

            tb_drift = (
                self.trust_baseline - self.current_state.trust_benevolence
            ) * 0.01
            tc_drift = (
                self.trust_baseline - self.current_state.trust_competence
            ) * 0.01
            ti_drift = (self.trust_baseline - self.current_state.trust_integrity) * 0.01
            self.current_state.trust_benevolence += tb_drift
            self.current_state.trust_competence += tc_drift
            self.current_state.trust_integrity += ti_drift

            self.current_state.last_update = datetime.fromtimestamp(now)
            self._enforce_bounds()
            self._refresh_global_controls_locked()
            if tracing:
                affect_after = self._affect_snapshot(
                    "mood", "arousal", "dominance", energy_arousal=True
                )
                trust_after = self._trust_snapshot(
                    "benevolence", "competence", "integrity"
                )
                self._trace_update(
                    "affect.update",
                    "system_tick_decay",
                    {"interval_s": dt_hours * 3600.0},
                    affect_before,
                    affect_after,
                )
                self._trace_update(
                    "trust.update",
                    "system_tick_drift",
                    {"interval_s": dt_hours * 3600.0},
                    trust_before,
                    trust_after,
                    person_id=self.current_state.active_person_id,
                )
            await self.persist_state()
        logger.debug(
            "[State Heartbeat] V=%.3f Ar=%.3f D=%.3f F=%.3f",
            self.current_state.mood,
            self.current_state.energy,
            self.current_state.dominance,
            self.current_state.fatigue,
        )

    def check_proactive_eligibility(self) -> bool:
        """
        Phase 1: Proactive Engagement.
        Evaluates whether the agent should spontaneously initiate contact.
        """
        if not getattr(Config, "PROACTIVE_ENABLED", False):
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="disabled",
                    idle_s=None,
                    idle_threshold_s=None,
                    cooldown_remaining_s=None,
                    cooldown_s=None,
                    energy=self.current_state.energy,
                    energy_min=None,
                    probability=None,
                    probability_min=None,
                )
            return False

        now = clock.time()

        debug_override = getattr(Config, "PROACTIVE_DEBUG_THRESHOLD_OVERRIDE", None)
        if debug_override is not None:
            try:
                threshold = float(debug_override)
            except (TypeError, ValueError):
                threshold = Config.PROACTIVE_IDLE_THRESHOLD_SECONDS
        else:
            threshold = Config.PROACTIVE_IDLE_THRESHOLD_SECONDS

        idle_duration = now - self.current_state.last_user_interaction
        if idle_duration < threshold:
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="idle_below_threshold",
                    idle_s=idle_duration,
                    idle_threshold_s=threshold,
                    cooldown_remaining_s=max(
                        0.0,
                        self.current_state.last_proactive_attempt
                        + getattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600)
                        - now,
                    ),
                    cooldown_s=getattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600),
                    energy=self.current_state.energy,
                    energy_min=getattr(Config, "PROACTIVE_MIN_ENERGY", 0.2),
                    probability=None,
                    probability_min=getattr(
                        Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.5
                    ),
                )
            return False

        cooldown = getattr(Config, "PROACTIVE_COOLDOWN_SECONDS", 3600)
        cooldown_remaining = max(
            0.0, self.current_state.last_proactive_attempt + cooldown - now
        )
        if cooldown_remaining > 0:
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="cooldown",
                    idle_s=idle_duration,
                    idle_threshold_s=threshold,
                    cooldown_remaining_s=cooldown_remaining,
                    cooldown_s=cooldown,
                    energy=self.current_state.energy,
                    energy_min=getattr(Config, "PROACTIVE_MIN_ENERGY", 0.2),
                    probability=None,
                    probability_min=getattr(
                        Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.5
                    ),
                )
            return False

        if self.is_quiet_hour(now):
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="quiet_hours",
                    idle_s=idle_duration,
                    idle_threshold_s=threshold,
                    cooldown_remaining_s=cooldown_remaining,
                    cooldown_s=cooldown,
                    energy=self.current_state.energy,
                    energy_min=getattr(Config, "PROACTIVE_MIN_ENERGY", 0.2),
                    probability=None,
                    probability_min=getattr(
                        Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.5
                    ),
                )
            return False

        min_energy = getattr(Config, "PROACTIVE_MIN_ENERGY", 0.2)
        if self.current_state.energy < min_energy:
            logger.debug(
                "[State] Proactive SKIPPED: energy %.2f < min %.2f",
                self.current_state.energy,
                min_energy,
            )
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="energy",
                    idle_s=idle_duration,
                    idle_threshold_s=threshold,
                    cooldown_remaining_s=cooldown_remaining,
                    cooldown_s=cooldown,
                    energy=self.current_state.energy,
                    energy_min=min_energy,
                    probability=None,
                    probability_min=getattr(
                        Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.5
                    ),
                )
            return False

        # Roadmap leftovers Item 4b (M3-D2): turn_taking_probability was
        # computed by calculate_pacing_parameters and read by no caller.
        # "Turn taking" -- deciding to take the conversational floor -- is
        # what proactive speech literally is, so this is where it belongs,
        # NOT scaling the pacing sleep above: silence_duration_ms and
        # turn_taking_probability are both driven by the same dominance and
        # fatigue terms, so applying the probability there would apply D and
        # F twice. No other gate here reads dominance or valence, so this
        # does not double-count anything. Verified before wiring
        # (tools/measure/m4b_turn_taking_gate.py): at the default 0.5
        # threshold the gate blocks 16.9% of the (valence, dominance,
        # fatigue) states the persona schema and live-state clamp actually
        # permit -- inside the plan's [15%, 50%] "meaningful minority" pass
        # band, so this is a real discriminator, not decoration.
        turn_probability = (
            0.5
            + 0.3 * self.current_state.dominance
            - 0.1 * self.current_state.fatigue
            + 0.2 * self.current_state.valence
        )
        min_turn_probability = getattr(Config, "PROACTIVE_MIN_TURN_PROBABILITY", 0.5)
        if turn_probability < min_turn_probability:
            logger.debug(
                "[State] Proactive SKIPPED: turn_taking_probability %.2f < min %.2f",
                turn_probability,
                min_turn_probability,
            )
            if self._trace_enabled():
                self._trace_proactive(
                    fired=False,
                    reason="probability_gate",
                    idle_s=idle_duration,
                    idle_threshold_s=threshold,
                    cooldown_remaining_s=cooldown_remaining,
                    cooldown_s=cooldown,
                    energy=self.current_state.energy,
                    energy_min=min_energy,
                    probability=turn_probability,
                    probability_min=min_turn_probability,
                )
            return False

        logger.info(
            "[State] Proactive ELIGIBLE: idle=%.0fs threshold=%.0fs energy=%.2f "
            "turn_taking_probability=%.2f",
            idle_duration,
            threshold,
            self.current_state.energy,
            turn_probability,
        )
        if self._trace_enabled():
            self._trace_proactive(
                fired=True,
                reason="eligible",
                idle_s=idle_duration,
                idle_threshold_s=threshold,
                cooldown_remaining_s=cooldown_remaining,
                cooldown_s=cooldown,
                energy=self.current_state.energy,
                energy_min=min_energy,
                probability=turn_probability,
                probability_min=min_turn_probability,
            )
        return True

    def is_quiet_hour(self, timestamp: float | None = None) -> bool:
        """Infer quiet hours from user turn times, with a night default."""
        now = clock.time() if timestamp is None else timestamp
        hour = user_hour(now)
        quiet = in_hour_window(
            hour, Config.PROACTIVE_QUIET_START_HOUR, Config.PROACTIVE_QUIET_END_HOUR
        )
        hours = self.current_state.user_interaction_hours
        if len(hours) >= Config.PROACTIVE_ACTIVITY_HISTORY_MINIMUM:
            observed = [hours.count(index) for index in range(24)]
            active_counts = sorted(count for count in observed if count)
            if active_counts:
                median_activity = active_counts[len(active_counts) // 2]
                quiet = observed[hour] < max(1, int(median_activity * 0.5))
        return quiet

    def proactive_candidate_eligible(
        self,
        *,
        importance: float,
        category: str,
        description: str,
        goal_id: str | None = None,
    ) -> bool:
        """Apply per-category importance and thought-history gates after generation."""
        if category not in {"useful_to_user", "self_directed"}:
            return False
        threshold = (
            Config.PROACTIVE_SELF_DIRECTED_IMPORTANCE_MIN
            if category == "self_directed"
            else Config.PROACTIVE_USEFUL_IMPORTANCE_MIN
        )
        if not isinstance(importance, (int, float)) or not math.isfinite(importance):
            return False
        if not threshold <= importance <= 1.0:
            return False
        normalized = " ".join(description.casefold().split())
        stable_id = goal_id or hashlib.sha256(normalized.encode()).hexdigest()[:16]
        review_due_goals(
            self.proactive_goals,
            clock.time(),
            ignore_after_s=Config.PROACTIVE_IGNORE_REVIEW_SECONDS,
        )
        goal = next(
            (item for item in self.proactive_goals if item.goal_id == stable_id), None
        )
        return not (
            goal
            and importance
            * goal.re_raise_probability(Config.PROACTIVE_IGNORE_ZERO_AFTER)
            < threshold
        )

    def mark_proactive_attempt(self):
        """Record that a proactive generation was initiated.

        Now on `current_state` (see the field's own comment on why), so it
        persists across a restart and reaches the sibling process's
        `StateService` via the same `state.broadcast` every other affect
        field already rides -- previously a plain instance attribute that
        neither survived a restart nor ever crossed the process boundary.
        """
        self.current_state.last_proactive_attempt = clock.time()

    def _enforce_bounds(self):
        self.current_state.mood = max(-1.0, min(1.0, self.current_state.mood))
        self.current_state.energy = max(0.0, min(1.0, self.current_state.energy))
        self.current_state.dominance = max(0.0, min(1.0, self.current_state.dominance))
        self.current_state.trust_benevolence = max(
            0.0, min(1.0, self.current_state.trust_benevolence)
        )
        self.current_state.trust_competence = max(
            0.0, min(1.0, self.current_state.trust_competence)
        )
        self.current_state.trust_integrity = max(
            0.0, min(1.0, self.current_state.trust_integrity)
        )
        self.current_state.attachment = max(
            0.0, min(1.0, self.current_state.attachment)
        )
        self.current_state.fatigue = max(0.0, min(1.0, self.current_state.fatigue))
        self.current_state.user_mental_model.inferred_valence = max(
            -1.0, min(1.0, self.current_state.user_mental_model.inferred_valence)
        )
        self.current_state.user_mental_model.inferred_arousal = max(
            0.0, min(1.0, self.current_state.user_mental_model.inferred_arousal)
        )

    async def update_theory_of_mind(
        self, user_input: str, tom_inferences: dict[str, Any] | None = None
    ):
        """
        Updates the Theory of Mind mental model of the user.
        Runs a zero-overhead text concepts tracker and digests LLM-inferred parameters.
        """
        from ..cognitive.tom import update_known_concepts

        # 1. Update vocabulary tracker (zero LLM overhead text indexing)
        self.current_state.user_mental_model.known_concepts = update_known_concepts(
            self.current_state.user_mental_model.known_concepts, user_input
        )

        # 2. Update LLM-inferred fields if available
        if tom_inferences:
            if "inferred_valence" in tom_inferences:
                try:
                    self.current_state.user_mental_model.inferred_valence = float(
                        tom_inferences["inferred_valence"]
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "[ToM] Failed to parse inferred_valence: %s (value: %s)",
                        e,
                        tom_inferences["inferred_valence"],
                    )
            if "inferred_arousal" in tom_inferences:
                try:
                    self.current_state.user_mental_model.inferred_arousal = float(
                        tom_inferences["inferred_arousal"]
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "[ToM] Failed to parse inferred_arousal: %s (value: %s)",
                        e,
                        tom_inferences["inferred_arousal"],
                    )
            if "implied_goals" in tom_inferences:
                implied_goals_raw = tom_inferences["implied_goals"]
                if isinstance(implied_goals_raw, list):
                    self.current_state.user_mental_model.implied_goals = (
                        implied_goals_raw
                    )
                else:
                    self.current_state.user_mental_model.implied_goals = []
                    logger.warning(
                        f"[StateService] Unexpected type for implied_goals in tom_inferences: {type(implied_goals_raw)}. Falling back to empty list."
                    )

        self._enforce_bounds()
        await self.persist_state()

    def get_context_snapshot(self) -> dict[str, Any]:
        return {
            "timestamp": clock.time(),
            "emotion": self.get_emotion_label(),
            # Surfaced so `StateUpdate.from_snapshot` (contracts.py) carries
            # revision/writer_id on the `state.update` subject for cross-process tracing.
            "revision": self.current_state.revision,
            "writer_id": self.current_state.writer_id,
            "mood": self.current_state.mood,
            "energy": self.current_state.energy,
            "dominance": self.current_state.dominance,
            "trust": self.current_state.trust,
            "trust_benevolence": self.current_state.trust_benevolence,
            "trust_competence": self.current_state.trust_competence,
            "trust_integrity": self.current_state.trust_integrity,
            "attachment": self.current_state.attachment,
            "interaction_count": self.current_state.interaction_count,
            "active_goals": self.current_state.active_goals,
            "user_interaction_hours": list(self.current_state.user_interaction_hours),
            "proactive_goals": [goal.model_dump() for goal in self.proactive_goals],
            "unresolved_thoughts": [
                goal.model_dump()
                for goal in self.proactive_goals
                if goal.proactive_raise_count > len(goal.proactive_outcomes)
            ],
            # PAD aliases for new consumers
            "valence": self.current_state.mood,
            "arousal": self.current_state.arousal,
            "fatigue": self.current_state.fatigue,
            # Endocrine hormones (Somatic Regulation)
            "cortisol": self.current_state.cortisol,
            "dopamine": self.current_state.dopamine,
            "adrenaline": self.current_state.adrenaline,
            "global_controls": self._global_controls.model_dump(),
            # Theory of Mind snapshot -- dict format
            "user_mental_model": self.current_state.user_mental_model.model_dump(),
        }

    def get_behavioral_directive(self) -> str:
        """Translates internal state into a natural language directive for the LLM."""
        V = self.current_state.mood
        Ar = self.current_state.energy
        D = self.current_state.dominance
        T = self.current_state.trust

        if V < -0.6:
            direct = "You are feeling deeply melancholic and reserved."
        elif V > 0.6:
            direct = "You are in a vibrant, optimistic state."
        else:
            direct = "You are grounded and emotionally stable."

        if Ar < 0.3:
            direct += " Your energy is low; keep replies brief and pacing slow."
        elif Ar > 0.8:
            direct += " You are high-energy; use expressive, dynamic language."

        if D < 0.3:
            direct += " You feel uncertain and deferential -- ask more, assert less."
        elif D > 0.7:
            direct += " You feel confident and in control -- speak with conviction."

        if T < 0.3:
            direct += " You are feeling skeptical and maintaining boundaries."
        elif T > 0.8:
            direct += " You feel a deep, familiar bond with the user."

        return direct

    def get_emotion_label(self) -> str:
        V = self.current_state.mood
        Ar = self.current_state.energy
        D = self.current_state.dominance

        if V > 0.4 and Ar > 0.6:
            return "excited"
        if V > 0.4:
            return "happy"
        if V < -0.4 and Ar > 0.6:
            return "angry"
        if V < -0.4:
            return "sad"
        if Ar > 0.8:
            return "alert"
        if D < 0.3:
            return "uncertain"
        return "neutral"

    async def _persist_sensory_state_if_due(self):
        now = clock.time()
        if now - self._last_sensory_persist < self.sensory_persist_interval:
            return
        self._last_sensory_persist = now
        await self.persist_state()

    def _update_fatigue_python(self, now: float, dt_hours: float, is_night: bool):
        """Fallback fatigue update matching Rust behavior."""
        circadian_multiplier = 1.8 if is_night else 1.0
        idle_duration = now - self.current_state.last_user_interaction
        is_idle = idle_duration > 300.0
        k_drain = 0.15
        k_restore = 0.20
        if is_idle:
            next_fatigue = self.current_state.fatigue - (
                k_restore * dt_hours / circadian_multiplier
            )
        else:
            next_fatigue = self.current_state.fatigue + (
                k_drain * dt_hours * circadian_multiplier
            )
        self.current_state.fatigue = max(0.0, min(1.0, next_fatigue))
