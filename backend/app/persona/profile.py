"""
PersonaProfile — the surface through which a user authors their own friend.

Until now the knobs that decide *who the agent is* were split across two places
that were never meant to hold a personality. Narrative identity (name, values,
tone, adaptive traits) lived in `personality.json` and was already well-shaped,
with a genuine immutable/adaptive split. But **temperament** — the numbers that
decide how this particular mind actually feels and moves — lived in `Config`, a
process-global env-var singleton. That made emotional character a *deployment*
setting: one process, one personality, tuned by whoever wrote the `.env`.

This module makes temperament a persona concern. `Config` is demoted to
supplying defaults; a `PersonaProfile` carries the actual values and is injected
into `StateService`.

## The three tiers

Every field belongs to exactly one tier, declared in the schema rather than by
convention, so the boundary is enforceable and self-documenting:

- **IMMUTABLE** — safety and integrity invariants. Not user-settable *at all*.
  These are not in the model's fields; they live in `IMMUTABLE_CORE` below and
  are merged in at read time. A persona file that tries to set them is rejected
  with a warning rather than silently honoured, because a user-editable file
  must never be able to loosen a safety boundary.

- **CONSTITUTIONAL** — who this friend *constitutionally is*. The user sets these
  when creating the friend and they hold for its life: temperament baselines and
  the rates at which feeling moves. Changing them later does not "update" a
  friend so much as replace them with a different person, which is a decision
  worth making deliberately.

- **ADAPTIVE** — seeded by the user, then owned by the friend. The user chooses
  where the relationship *starts*; living together decides where it goes. Trust
  and attachment are the clearest cases: a friend who begins guarded may become
  close, and that trajectory belongs to the relationship, not to a config file.

## Why the ranges are narrower than the maths allows

Total configurability has a failure mode: a user can tune a friend into
something that is not recognisably alive. The bounds here are deliberately
tighter than the underlying variables permit, and each one exists to preserve a
specific property:

- `mood_decay_rate` is strictly positive. At zero, ALMA decay stops and the
  agent's mood locks permanently at whatever it last felt — a friend frozen
  mid-emotion.
- `baseline_valence` is capped at ±0.6, not ±1.0. A friend pinned at maximum
  valence can never be sad *with* you, which is not cheerfulness but absence.
- `baseline_arousal` and `baseline_dominance` keep headroom at both ends, so
  there is always room to be roused or calmed, to lead or to yield.

The through-line: **a personality may be shaped, but it must remain moveable.**
A configured friend that cannot be affected by what happens to it is a puppet,
not a character.
"""

import copy
import json
import logging
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from ..config import Config
from .value_screen import SCREEN_VERSION, ScreenResult, safe_excerpt, screen_value

logger = logging.getLogger(__name__)

# Content-level backstop for adaptive persona updates: prevents degradation
# from reflection models (e.g. writing CJK fragments or literal language names
# into speaking_style). Pydantic type validation alone allows arbitrary strings,
# so this validates adaptive persona content written by learn_traits and evolve_persona.
_META_PLACEHOLDER_VALUES = frozenset(
    {
        "n/a",
        "none",
        "unknown",
        "unspecified",
        "tbd",
        "re-evaluate",
        "english",
        "hindi",
        "hinglish",
        "chinese",
        "spanish",
        "french",
        "japanese",
        "korean",
        "german",
        "italian",
        "portuguese",
        "russian",
        "arabic",
    }
)


def is_plausible_persona_content(text: str) -> bool:
    """Reject the two corruption shapes above: a bare language name standing
    in for a real description, and a string that is mostly non-Latin script
    landing in a persona authored (here) entirely in Latin script.

    Not a complete defense -- no fixed heuristic can be, against a model that
    can emit anything -- but it closes the two specific bypasses actually
    observed in production, the same posture `OllamaClient`'s role-prefix
    regex takes for a different corruption shape.
    """
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped:
        return False
    if stripped.lower() in _META_PLACEHOLDER_VALUES:
        return False

    letters = [ch for ch in stripped if ch.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for ch in letters if "LATIN" not in unicodedata.name(ch, ""))
    return not non_latin / len(letters) > 0.3


def value_screen_enabled() -> bool:
    """Whether `PERSONA_VALUE_SCREEN_ENABLED` is on. Read at call time."""
    return bool(getattr(Config, "PERSONA_VALUE_SCREEN_ENABLED", False))


def reject_by_value_screen(field: str, value: Any) -> ScreenResult | None:
    """Screen one value about to be written to `field`.

    Returns the blocking `ScreenResult` (after logging it once at WARNING with
    at most 80 characters of the value) or None when the value may be written.
    Callers check `value_screen_enabled()` first, so with the flag off this is
    never reached and the write paths are unchanged.
    """
    result = screen_value(value)
    if not result.blocked:
        return None
    logger.warning(
        "[Persona] Value screen %s blocked %s write (categories=%s): %s",
        SCREEN_VERSION,
        field,
        ",".join(result.categories),
        safe_excerpt(value),
    )
    return result


class Tier(str, Enum):
    """Who owns a value, and for how long."""

    IMMUTABLE = "immutable"
    CONSTITUTIONAL = "constitutional"
    ADAPTIVE = "adaptive"


# Not model fields: these are invariants, and a field is by definition settable.
# `IdentityManager` already keeps an `immutable` block inside personality.json,
# but that file is user-editable, so it cannot be the authority on safety. These
# are merged over whatever a persona file supplies.
IMMUTABLE_CORE: dict[str, Any] = {
    "values": ["Honesty", "Privacy"],
    "boundaries": [
        "Will never share user data",
        "Will not adopt toxic behavior",
    ],
}


def _constitutional(**kwargs) -> Any:
    return Field(json_schema_extra={"tier": Tier.CONSTITUTIONAL.value}, **kwargs)


def _adaptive(**kwargs) -> Any:
    return Field(json_schema_extra={"tier": Tier.ADAPTIVE.value}, **kwargs)


class PersonaProfile(BaseModel):
    """A single friend's authored character.

    Construct via `from_config()` (deployment defaults, lenient) or `load()`
    (a user-authored file, strict).
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # -- constitutional: temperament ---------------------------------------
    name: str = _constitutional(default="AI Friend", min_length=1, max_length=64)

    baseline_valence: float = _constitutional(default=0.0, ge=-0.6, le=0.6)
    baseline_arousal: float = _constitutional(default=0.5, ge=0.15, le=0.85)
    baseline_dominance: float = _constitutional(default=0.5, ge=0.15, le=0.85)

    # -- constitutional: how feeling moves ---------------------------------
    # These map onto StateService's alpha/beta/gamma/delta/epsilon/lambda. Named
    # for what they *do*, because a persona author is not reading the ALMA paper.
    valence_drift_rate: float = _constitutional(default=0.3, gt=0.0, le=0.8)
    arousal_response_rate: float = _constitutional(default=0.5, gt=0.0, le=0.9)
    dominance_stability: float = _constitutional(default=0.2, gt=0.0, le=0.8)
    trust_change_rate: float = _constitutional(default=0.1, gt=0.0, le=0.5)
    attachment_growth_rate: float = _constitutional(default=0.03, gt=0.0, le=0.3)
    # Strictly positive: see the module docstring. Zero is a mood lock.
    mood_decay_rate: float = _constitutional(default=0.05, gt=0.0, le=0.5)

    # -- constitutional: how long feeling lingers --------------------------
    # Half-lives of the phasic hormone bursts, in seconds. These are as much a
    # part of temperament as the baselines: how long a good moment glows, and
    # how long a bad one keeps its grip, are among the most recognisable things
    # about a person. Cortisol's default is deliberately the longer of the two
    # -- a fright has a hangover, a pleasure mostly does not.
    #
    # Both are floored well above zero. At a near-zero half-life a burst is
    # gone before it can influence anything, which is not a fast temperament
    # but a broken hormone: the release fires and nothing downstream ever sees
    # it. The ceilings stop the opposite failure, a burst that outlives the
    # conversation that caused it and colours a session it has nothing to do
    # with.
    dopamine_halflife_s: float = _constitutional(default=90.0, ge=5.0, le=1800.0)
    # Cortisol plasma half-life default is 4500s (~75 minutes, within empirical
    # human plasma half-life of 66-90 minutes). Still CONSTITUTIONAL --
    # an authored persona may configure this differently based on temperament.
    cortisol_halflife_s: float = _constitutional(default=4500.0, ge=5.0, le=7200.0)
    # Adrenaline has no tonic term (see AgentState.adrenaline_tonic) -- it is purely
    # reactive, firing on startle/interruption/shock rather than tracking ambient mood.
    # 120s (2 minutes) sits between dopamine's 90s and cortisol's 4500s, reflecting
    # short-timescale reactive burst recovery.
    adrenaline_halflife_s: float = _constitutional(default=120.0, ge=5.0, le=1800.0)

    # -- constitutional: narrative character --------------------------------
    # These were `IdentityManager`'s half of the persona, read straight out of
    # personality.json with no schema and no tier. They live here now so that
    # every authored field has exactly one owner and one set of bounds. The
    # tiering is the same judgement applied to prose as to numbers: what the
    # friend fundamentally *is* holds for its life, what the relationship
    # becomes belongs to the relationship.
    base_tone: str = _constitutional(
        default="Warm, intellectual, and slightly protective",
        min_length=1,
        max_length=200,
    )

    # A few sentences that must colour *every* reply — not a biography.
    #
    # The length cap is the point of this field, not a formality. Whatever goes
    # here occupies the context window on every single turn, so it is paid for
    # on each one in latency and in room taken from actual conversation. The
    # long material belongs in `config/biography.md`, which is seeded into
    # episodic memory and surfaces only when it is relevant.
    #
    # 1200 characters is roughly two paragraphs: enough for who someone is,
    # short of enough to start listing what happened to them.
    identity_summary: str = _constitutional(default="", max_length=1200)

    # Turns of phrase that are recognisably hers. Kept separate from
    # `speaking_style` because these are constitutional — the way a person
    # talks is not something the agent should reflect its way out of, whereas
    # the register it adopts with you is.
    speech_patterns: list[str] = _constitutional(default_factory=list, max_length=20)
    traits: list[str] = _constitutional(default_factory=list, max_length=8)
    # Phrases the friend must not say. Constitutional rather than adaptive: a
    # user asking never to hear something is not a preference the agent gets to
    # outgrow through reflection.
    avoid: list[str] = _constitutional(default_factory=list, max_length=64)

    # -- adaptive: seeded here, then owned by the friend --------------------
    relationship: str = _adaptive(default="Friend", max_length=64)
    initial_trust: float = _adaptive(default=0.5, ge=0.0, le=1.0)
    initial_attachment: float = _adaptive(default=0.1, ge=0.0, le=1.0)
    # The cap is the schema's, and only the schema's. `IdentityManager` used to
    # re-implement it as a `[-5:]` slice in its constructor -- one rule with two
    # implementations, which is how the prosody and affect duplications both
    # began. Homeostatic: a friend that accumulates traits without bound
    # eventually has no character at all, just a list.
    adaptive_traits: list[str] = _adaptive(default_factory=list, max_length=5)
    # `Any`, not `str`. The schema originally said `Dict[str, str]`, which the
    # real personality.json has never satisfied: `common_vocabulary` is a list
    # of words. Nothing noticed because nothing read this field. The moment
    # IdentityManager did, one list-valued key failed validation and took the
    # entire narrative persona down with it — name, tone and traits discarded
    # over a vocabulary entry.
    speaking_style: dict[str, Any] = _adaptive(default_factory=dict)

    # The one field in the per-turn prompt that reflection writes *free text*
    # into. `Dict[str, Any]` cannot bound its own values, so the ceiling has to
    # be applied where the value is assigned -- see
    # `IdentityManager.evolve_persona`. Without it, `style_description` is the
    # only part of the persona prompt with no upper size, and it is the part the
    # agent rewrites by itself: an LLM that returns a paragraph instead of a
    # phrase permanently enlarges every subsequent turn's prompt, and the next
    # reflection reads that bloated prompt and can grow it again.
    #
    # 400 characters is a sentence or two -- the register the friend adopts with
    # you, which is what this field is for. Anything longer is the reflection
    # model explaining itself rather than describing a voice.
    MAX_STYLE_DESCRIPTION: ClassVar[int] = 400

    # Slice 10a: values the value screen refused to write. Private state, never
    # serialized, always empty while the flag is off.
    _value_screen_drops: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    # -- tier introspection -------------------------------------------------

    @classmethod
    def tier_of(cls, field_name: str) -> Tier:
        """Which tier a field belongs to. Raises KeyError for unknown fields."""
        field = cls.model_fields[field_name]
        extra = field.json_schema_extra
        if not isinstance(extra, dict):
            extra = {}
        return Tier(extra["tier"])

    @classmethod
    def fields_in(cls, tier: Tier) -> list[str]:
        return sorted(n for n in cls.model_fields if cls.tier_of(n) is tier)

    @classmethod
    def adaptive_trait_limit(cls) -> int:
        """The homeostatic cap, read off the schema rather than restated.

        This number had been written out by hand in three places — the
        IdentityManager constructor, `evolve_persona`, and the field itself —
        so changing it required finding all three. Now there is one.
        """
        for meta in cls.model_fields["adaptive_traits"].metadata:
            limit = getattr(meta, "max_length", None)
            if limit is not None:
                return limit
        raise RuntimeError("adaptive_traits lost its max_length constraint")

    def learn_traits(self, new_traits: list[str]) -> list[str]:
        """Adopt new adaptive traits, keeping only the newest within the cap.

        The one place a trait list grows. Dropping the *oldest* is the point:
        this is the mechanism by which a friend can change over time rather than
        accrete forever, and a friend who is fifteen adjectives at once is not a
        character. Assignment is validated, so the cap cannot be exceeded even
        if this method is wrong.
        """
        merged = list(self.adaptive_traits)
        screen_on = value_screen_enabled()
        for trait in new_traits or []:
            if trait and trait not in merged and is_plausible_persona_content(trait):
                if screen_on:
                    blocked = reject_by_value_screen("adaptive_traits", trait)
                    if blocked is not None:
                        self._value_screen_drops.append(
                            {
                                "field": "adaptive_traits",
                                "categories": blocked.categories,
                                "version": SCREEN_VERSION,
                            }
                        )
                        continue
                merged.append(trait)
        self.adaptive_traits = merged[-self.adaptive_trait_limit() :]
        return list(self.adaptive_traits)

    @property
    def value_screen_drops(self) -> tuple[dict[str, Any], ...]:
        """Values the slice 10a screen refused to write, oldest first."""
        return tuple(self._value_screen_drops)

    @property
    def immutable(self) -> dict[str, Any]:
        """The safety core. Always the code constant, never the file."""
        # A copy, so a caller mutating what it receives cannot edit the boundary
        # list every other caller reads. deepcopy rather than a JSON round-trip:
        # the latter silently coerces types (a tuple would come back a list), so
        # the "copy" could differ from the original it is meant to reproduce.
        return copy.deepcopy(IMMUTABLE_CORE)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_config(cls) -> "PersonaProfile":
        """Build from `Config`, preserving today's behaviour exactly.

        Deliberately lenient: an out-of-range env var is clamped with a warning
        rather than raised. A deployment that has been running with an unusual
        `PSYCH_*` value should not fail to boot because persona bounds arrived —
        it should be told its value was pulled into range. File-authored personas
        get the strict treatment instead (see `load`).
        """
        raw = {
            "name": getattr(Config, "AI_NAME", "AI Friend"),
            "valence_drift_rate": getattr(Config, "PSYCH_ALPHA", 0.3),
            "arousal_response_rate": getattr(Config, "PSYCH_BETA", 0.5),
            "dominance_stability": getattr(Config, "PSYCH_GAMMA", 0.2),
            "trust_change_rate": getattr(Config, "PSYCH_DELTA", 0.1),
            "attachment_growth_rate": getattr(Config, "PSYCH_EPSILON", 0.03),
            "mood_decay_rate": getattr(Config, "PSYCH_LAMBDA_DECAY", 0.05),
            "dopamine_halflife_s": getattr(Config, "DOPAMINE_PHASIC_HALFLIFE_S", 90.0),
            "cortisol_halflife_s": getattr(
                Config, "CORTISOL_PHASIC_HALFLIFE_S", 4500.0
            ),
            "adrenaline_halflife_s": getattr(
                Config, "ADRENALINE_PHASIC_HALFLIFE_S", 120.0
            ),
        }
        return cls._clamped(raw, origin="Config")

    @classmethod
    def _clamped(cls, raw: dict[str, Any], *, origin: str) -> "PersonaProfile":
        """Pull numeric values into their declared bounds, warning on each."""
        fixed = dict(raw)
        for name, value in raw.items():
            field = cls.model_fields.get(name)
            if field is None or not isinstance(value, (int, float)):
                continue
            if isinstance(value, bool):
                continue
            low, high = cls._bounds_of(field)
            pulled = value
            if low is not None and pulled <= low:
                pulled = low + 1e-6 if cls._is_exclusive_low(field) else low
            if high is not None and pulled > high:
                pulled = high
            if pulled != value:
                logger.warning(
                    "[Persona] %s value for '%s' (%s) is outside its persona "
                    "bounds; clamped to %s.",
                    origin,
                    name,
                    value,
                    pulled,
                )
                fixed[name] = pulled
        return cls(**fixed)

    @staticmethod
    def _bounds_of(field) -> tuple:
        # Explicit `is not None` rather than `or`: a bound of 0.0 is falsy, and
        # `gt=0.0` is precisely the mood-lock guard, so `or` would drop the one
        # bound that matters most.
        low = high = None
        for meta in field.metadata:
            for attr in ("ge", "gt"):
                value = getattr(meta, attr, None)
                if value is not None:
                    low = value
            for attr in ("le", "lt"):
                value = getattr(meta, attr, None)
                if value is not None:
                    high = value
        return low, high

    @staticmethod
    def _is_exclusive_low(field) -> bool:
        return any(getattr(m, "gt", None) is not None for m in field.metadata)

    @classmethod
    def load(cls, path: str | None = None) -> "PersonaProfile":
        """Load a user-authored persona file, falling back to `Config`.

        Strict by design. A persona file is someone deliberately describing the
        friend they want; a silently-corrected value there would hand them a
        different friend than the one they wrote down. So validation errors are
        reported and the profile falls back to defaults rather than guessing.
        """
        path = path or getattr(Config, "PERSONA_PROFILE_PATH", "") or ""
        if not path:
            return cls.from_config()

        file = Path(path)
        if not file.exists():
            logger.info("[Persona] No persona file at %s; using Config defaults.", path)
            return cls.from_config()

        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(
                "[Persona] Could not read persona file %s (%s); "
                "falling back to Config defaults.",
                path,
                exc,
            )
            return cls.from_config()

        if not isinstance(data, dict):
            logger.error(
                "[Persona] Persona file %s is not a JSON object; "
                "falling back to Config defaults.",
                path,
            )
            return cls.from_config()

        data = cls._reject_immutable_overrides(data, origin=str(file))
        # Accepts both this schema's flat keys and IdentityManager's nested
        # personality.json layout, so an existing authored file keeps working.
        data = cls.flatten_personality_shape(data, origin=str(file))

        # Anything the file omits inherits the deployment default, so a persona
        # may specify only the handful of traits its author actually cares about.
        merged = cls.from_config().model_dump()
        merged.update(data)

        try:
            profile = cls(**merged)
        except ValidationError as exc:
            logger.error(
                "[Persona] Persona file %s is invalid and was NOT applied; "
                "using Config defaults instead. Problems:\n%s",
                path,
                exc,
            )
            return cls.from_config()

        logger.info("[Persona] Loaded persona '%s' from %s.", profile.name, path)
        return profile

    @classmethod
    def _reject_immutable_overrides(
        cls, data: dict[str, Any], *, origin: str
    ) -> dict[str, Any]:
        """Drop any attempt to set the safety core from a file."""
        cleaned = dict(data)
        for key in ("immutable", *IMMUTABLE_CORE.keys()):
            if key in cleaned:
                logger.warning(
                    "[Persona] '%s' in %s targets the immutable safety core and "
                    "was ignored. These values are fixed in code and cannot be "
                    "overridden by a persona file.",
                    key,
                    origin,
                )
                cleaned.pop(key)
        return cleaned

    @classmethod
    def flatten_personality_shape(
        cls, data: dict[str, Any], *, origin: str = "personality.json"
    ) -> dict[str, Any]:
        """Translate the nested `personality.json` layout onto these fields.

        `IdentityManager`'s file groups things under `core_personality`,
        `conversation_rules` and so on. That layout predates this schema and is
        what every existing install has on disk, so it is read rather than
        migrated: a user who authored a friend should not have to rewrite the
        file to keep it.

        Flat keys win where both are present, so a file may be written either
        way and a partially-migrated one still loads.

        The nested `immutable` block is dropped here for the same reason
        `_reject_immutable_overrides` drops the flat one — it is the exact path
        that let a user-editable file empty the safety boundaries. Only
        `base_tone` survives it, which is authorable by design.
        """
        if not isinstance(data, dict):
            return {}

        flat = {k: v for k, v in data.items() if k in cls.model_fields}

        core = data.get("core_personality")
        core = core if isinstance(core, dict) else {}

        immutable = core.get("immutable")
        immutable = immutable if isinstance(immutable, dict) else {}
        smuggled = [k for k in immutable if k in IMMUTABLE_CORE]
        if smuggled:
            logger.warning(
                "[Persona] core_personality.immutable in %s tried to set %s; "
                "ignored. Safety invariants are fixed in code.",
                origin,
                " and ".join(sorted(smuggled)),
            )

        nested = {
            "traits": core.get("traits"),
            "adaptive_traits": core.get("adaptive_traits"),
            "base_tone": immutable.get("base_tone"),
            "avoid": (data.get("conversation_rules") or {}).get("avoid")
            if isinstance(data.get("conversation_rules"), dict)
            else None,
        }
        for key, value in nested.items():
            if value is not None and key not in flat:
                flat[key] = value

        return flat

    # -- consumption --------------------------------------------------------

    def coefficients(self) -> dict[str, float]:
        """The psychological coefficients, under StateService's own names."""
        return {
            "alpha": self.valence_drift_rate,
            "beta": self.arousal_response_rate,
            "gamma": self.dominance_stability,
            "delta": self.trust_change_rate,
            "epsilon": self.attachment_growth_rate,
            "lambda_decay": self.mood_decay_rate,
        }

    def hormone_halflives(self) -> dict[str, float]:
        """Phasic decay half-lives, under AgentState's own field names."""
        return {
            "dopamine_halflife_s": self.dopamine_halflife_s,
            "cortisol_halflife_s": self.cortisol_halflife_s,
            "adrenaline_halflife_s": self.adrenaline_halflife_s,
        }

    def baseline_affect(self) -> dict[str, float]:
        return {
            "valence": self.baseline_valence,
            "arousal": self.baseline_arousal,
            "dominance": self.baseline_dominance,
        }
