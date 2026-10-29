"""
The persona compiler: freeform prose -> a validated `PersonaProfile` + biography.

Phase 2.1 of the community roadmap. A user describes their friend in their own
words ("she's blunt, hates small talk, gets genuinely annoyed when I dodge a
question, grew up somewhere cold") and this module turns that into the fields
`PersonaProfile` actually needs.

## Why numeric inference is split into two steps

The temperament fields (`baseline_valence`, `mood_decay_rate`, half-lives, ...)
are numbers a prose description does not contain. Asking the LLM to invent them
directly would make every inference an unaudited black box: two runs on the
same description could land on different numbers for no visible reason, and
there would be nothing for a preview screen (Phase 2.2) to actually explain.

So the LLM's job is narrower and more checkable: read the description and score
a small set of named, human-legible *dimensions* (warmth, volatility, how
quickly trust opens up, ...), each with a short quote or paraphrase from the
user's own text as evidence. Turning those dimensions into the actual bounded
`PersonaProfile` numbers is then a fixed set of linear formulas *in this file*
— inspectable, and tunable by editing a formula rather than re-prompting a
model and hoping the number moves the way you wanted.

Every dimension score therefore becomes exactly one `Inference`, with the field
it produced and a human-readable reason. `compile_persona` never applies a
numeric change silently.

## The friction requirement

The product decision (see the roadmap) is that a friend's edges come from what
the user wrote, not from a house style the compiler imposes. A description of
someone blunt must produce a blunt `base_tone`/`identity_summary`/`traits`, not
an agreeable default the model reached for out of habit. This is checked by
`backend/scripts/testing/verify_persona_compiler_friction.py`, a live-model
script (per this repo's `evals/`-style convention: it refuses to run silently
against `MOCK_LLM_TEXT`) run manually rather than as part of the hermetic
pytest suite, since it needs a real model's actual output to mean anything.
"""

import dataclasses
import json
import logging
import re
from typing import Any

from ..config import Config
from ..errors import AgentError
from ..llm import LLMClient, build_llm_client
from ..llm.model_roles import ModelRole
from .profile import PersonaProfile

logger = logging.getLogger(__name__)

# A defensive backstop for the extraction prompt's own instruction not to
# return a pronoun as a name -- a small model asked for "a name" from a
# description starting "He is..." has been observed to just echo "He" back.
# Code-level rather than trusted to the prompt alone, since a smaller/weaker
# model is exactly where a prompt instruction is most likely to be ignored.
_PRONOUNS_NOT_NAMES = {
    "he",
    "she",
    "they",
    "him",
    "her",
    "them",
    "his",
    "hers",
    "their",
    "theirs",
    "i",
    "me",
    "you",
    "friend",
}


class PersonaCompilationError(AgentError, Exception):
    """The LLM's output could not be turned into a persona.

    Raised rather than silently falling back to defaults: unlike booting an
    agent (where a bad file must never block startup), compiling a persona is
    an interactive act with a person watching, who would rather be told to
    retry than be handed a stranger described nowhere in what they wrote.
    """


@dataclasses.dataclass(frozen=True)
class Inference:
    """One numeric field, traced back to the description that produced it."""

    field: str
    value: float
    reason: str


@dataclasses.dataclass(frozen=True)
class CompiledPersona:
    """The compiler's full output, before anything is written to disk."""

    profile: PersonaProfile
    biography_markdown: str
    inferences: list[Inference]
    dimensions: dict[str, Any]


# -- dimension -> field mappings --------------------------------------------
#
# Each dimension is scored 0.0-1.0 by the LLM (warmth is -1.0-1.0; see the
# extraction prompt), and each formula below maps it onto exactly one
# `PersonaProfile` field, kept inside that field's declared bounds by
# construction. The comment on each line is what actually ships in the
# `Inference.reason` shown to the user -- see `_infer_temperament`.


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _infer_temperament(
    dimensions: dict[str, float],
) -> tuple[dict[str, float], list[Inference]]:
    """Turn dimension scores into bounded `PersonaProfile` numbers.

    Missing dimensions default to the schema's own defaults' midpoint (0.5, or
    0.0 for warmth) so a description that only mentions some traits does not
    corrupt the fields it never touched.
    """
    warmth = _clamp(dimensions.get("warmth", 0.0), -1.0, 1.0)
    energy = _clamp(dimensions.get("energy", 0.5), 0.0, 1.0)
    assertiveness = _clamp(dimensions.get("assertiveness", 0.5), 0.0, 1.0)
    volatility = _clamp(dimensions.get("volatility", 0.5), 0.0, 1.0)
    resilience = _clamp(dimensions.get("resilience", 0.5), 0.0, 1.0)
    opinion_firmness = _clamp(dimensions.get("opinion_firmness", 0.5), 0.0, 1.0)
    openness_to_trust = _clamp(dimensions.get("openness_to_trust", 0.5), 0.0, 1.0)
    warmth_growth = _clamp(dimensions.get("warmth_growth", 0.5), 0.0, 1.0)
    emotional_lingering = _clamp(dimensions.get("emotional_lingering", 0.5), 0.0, 1.0)

    fields: dict[str, float] = {}
    inferences: list[Inference] = []

    def set_field(
        name: str, value: float, dimension_name: str, score: float, note: str
    ) -> None:
        fields[name] = value
        inferences.append(
            Inference(
                field=name,
                value=value,
                reason=f"{dimension_name}={score:.2f} -> {note}",
            )
        )

    set_field(
        "baseline_valence",
        round(warmth * 0.6, 3),
        "warmth",
        warmth,
        "how warm vs. cold the description reads, scaled into the "
        "±0.6 valence bound (a friend can never be pinned fully positive)",
    )
    set_field(
        "baseline_arousal",
        round(0.15 + energy * 0.70, 3),
        "energy",
        energy,
        "calm/low-key (0) to excitable/high-energy (1), scaled into the 0.15-0.85 bound",
    )
    set_field(
        "baseline_dominance",
        round(0.15 + assertiveness * 0.70, 3),
        "assertiveness",
        assertiveness,
        "yielding (0) to take-charge (1), scaled into the 0.15-0.85 bound",
    )
    set_field(
        "valence_drift_rate",
        round(0.1 + volatility * 0.6, 3),
        "volatility",
        volatility,
        "how much mood swings drives how fast valence itself moves",
    )
    set_field(
        "arousal_response_rate",
        round(0.15 + volatility * 0.65, 3),
        "volatility",
        volatility,
        "a more reactive temperament also means arousal responds to events faster",
    )
    set_field(
        "dominance_stability",
        round(0.05 + opinion_firmness * 0.7, 3),
        "opinion_firmness",
        opinion_firmness,
        "easily swayed (0) to stubborn/consistent (1)",
    )
    set_field(
        "trust_change_rate",
        round(0.05 + openness_to_trust * 0.4, 3),
        "openness_to_trust",
        openness_to_trust,
        "guarded (0) to quick-to-trust (1) shapes how fast trust itself can move",
    )
    set_field(
        "attachment_growth_rate",
        round(0.02 + warmth_growth * 0.25, 3),
        "warmth_growth",
        warmth_growth,
        "standoffish long-term (0) to quickly-attached (1)",
    )
    set_field(
        "mood_decay_rate",
        round(0.02 + resilience * 0.4, 3),
        "resilience",
        resilience,
        "dwells on things (0) to bounces back quickly (1) -- higher means faster "
        "return to baseline mood",
    )
    set_field(
        "dopamine_halflife_s",
        round(30 + emotional_lingering * 300, 1),
        "emotional_lingering",
        emotional_lingering,
        "how long a good moment's glow lasts, in seconds",
    )
    set_field(
        "cortisol_halflife_s",
        round(200 + emotional_lingering * 1000, 1),
        "emotional_lingering",
        emotional_lingering,
        "how long a bad moment's sting lingers, in seconds -- longer than dopamine's "
        "by construction, the same asymmetry every deployment default carries",
    )
    set_field(
        "adrenaline_halflife_s",
        round(100 + emotional_lingering * 500, 1),
        "emotional_lingering",
        emotional_lingering,
        "how long a startle/interruption/shock reaction lingers, in seconds -- by "
        "construction (100 + 500x vs dopamine's 30 + 300x and cortisol's "
        "200 + 1000x) this sits between the other two for every possible "
        "emotional_lingering score, not just the default one",
    )
    set_field(
        "initial_trust",
        round(0.2 + openness_to_trust * 0.6, 3),
        "openness_to_trust",
        openness_to_trust,
        "where the relationship's trust starts (never at the extremes -- a brand "
        "new friend is neither a stranger nor already fully trusted)",
    )
    set_field(
        "initial_attachment",
        round(0.05 + warmth_growth * 0.35, 3),
        "warmth_growth",
        warmth_growth,
        "where attachment starts -- deliberately low; attachment is meant to be earned",
    )

    return fields, inferences


# -- extraction ---------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """You turn a freeform description of a person into structured JSON. \
Output ONLY a single JSON object, no prose before or after it, no markdown code fences.

The JSON object must have exactly these top-level keys:

"name": a proper name for this person, ONLY if one is actually given in the \
description (e.g. "Mira", "my friend Alex"). A pronoun ("he", "she", "they") \
or a role ("my friend", "my roommate") is NOT a name -- if no actual proper \
name appears anywhere in the description, use exactly "Friend".
"base_tone": one sentence describing their overall tone/manner (<=200 chars).
"identity_summary": 2-4 sentences describing who they are, in the voice of a \
character description, preserving the description's own edges and bluntness \
rather than softening them (<=1000 chars).
"traits": a JSON list of at most 8 short adjectives/phrases.
"speech_patterns": a JSON list of at most 6 characteristic turns of phrase or \
verbal habits, only if the description implies any -- else an empty list.
"avoid": a JSON list of at most 10 things this person would never say or do, \
only if the description implies any -- else an empty list.
"relationship": a short phrase (<=64 chars) for how they relate to the user, \
e.g. "childhood friend", "protective older sibling", "Friend" if unclear.
"speaking_style": a one-sentence description (<=200 chars) of the register \
they use with the user specifically.
"biography": a JSON list of {"heading": str, "text": str} objects for any \
concrete biographical facts, backstory, relationships or history the \
description mentions (their past, family, where they grew up, how they met \
the user, specific incidents). Omit entirely (empty list) if the description \
contains no such concrete material -- do not invent any.
"dimensions": a JSON object scoring these temperament dimensions from the \
description. Every score must be a plain float. For each, also give a short \
"_evidence" quote or paraphrase (<=100 chars) from the description that \
justifies the score, or "" if the description does not address that \
dimension (in which case use 0.5, or 0.0 for warmth, as a neutral default):
  "warmth": -1.0 (cold/harsh/critical) to 1.0 (warm/affectionate)
  "warmth_evidence": string
  "energy": 0.0 (calm/low-key) to 1.0 (excitable/high-energy)
  "energy_evidence": string
  "assertiveness": 0.0 (yielding/soft-spoken) to 1.0 (dominant/take-charge)
  "assertiveness_evidence": string
  "volatility": 0.0 (even-keeled) to 1.0 (moody/reactive)
  "volatility_evidence": string
  "resilience": 0.0 (dwells on things) to 1.0 (bounces back quickly)
  "resilience_evidence": string
  "opinion_firmness": 0.0 (easily swayed) to 1.0 (stubborn/consistent)
  "opinion_firmness_evidence": string
  "openness_to_trust": 0.0 (guarded) to 1.0 (quick to trust)
  "openness_to_trust_evidence": string
  "warmth_growth": 0.0 (standoffish long-term) to 1.0 (quickly attached)
  "warmth_growth_evidence": string
  "emotional_lingering": 0.0 (feelings fade fast) to 1.0 (feelings linger)
  "emotional_lingering_evidence": string

Preserve bluntness, edge, and friction in the description exactly as written. \
Do not soften a critical or blunt person into an agreeable one -- if the \
description says they get annoyed or are harsh, "traits", "base_tone" and \
"identity_summary" must say so plainly, not euphemistically."""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first top-level JSON object out of an LLM response.

    Models asked for "only JSON" still sometimes wrap it in a code fence or a
    sentence; this looks for the first `{` and its matching `}` by brace
    depth rather than assuming the whole response is clean JSON.
    """
    start = text.find("{")
    if start == -1:
        raise PersonaCompilationError("LLM response contained no JSON object")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise PersonaCompilationError(
                        f"LLM response was not valid JSON: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise PersonaCompilationError(
                        "LLM response's JSON was not an object"
                    )
                return parsed
    raise PersonaCompilationError("LLM response's JSON object was never closed")


_NUM = re.compile(r"-?\d+(\.\d+)?")


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = _NUM.search(value)
        if match:
            return float(match.group())
    return default


def _as_str_list(
    value: Any, *, max_items: int, max_len: int | None = None
) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            text = item.strip()
            out.append(text[:max_len] if max_len else text)
        if len(out) >= max_items:
            break
    return out


def _biography_markdown(entries: Any) -> str:
    """Render the LLM's `{heading, text}` list into the markdown
    `parse_biography` expects: one `##` heading per entry, blank-line
    separated so each becomes its own memory paragraph."""
    if not isinstance(entries, list):
        return ""
    blocks: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        heading = str(entry.get("heading") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        if heading:
            blocks.append(f"## {heading}\n\n{text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


async def compile_persona(
    description: str,
    *,
    llm: LLMClient | None = None,
) -> CompiledPersona:
    """Compile a freeform description into a `CompiledPersona`.

    Raises `PersonaCompilationError` if the model's output could not be turned
    into valid JSON, or `pydantic.ValidationError` if the assembled fields
    still fail `PersonaProfile`'s own bounds after clamping (should not
    normally happen, since every numeric field here is produced already
    in-range, but string length caps are enforced defensively rather than
    trusted from the model).
    """
    if not description or not description.strip():
        raise PersonaCompilationError("description is empty")

    client = llm or build_llm_client(
        base_url=Config.OLLAMA_URL, model=Config.LLM_CHAT_MODEL
    )
    response = await client.generate(
        prompt=description.strip(),
        system=_EXTRACTION_SYSTEM_PROMPT,
        # OllamaClient.generate()'s own default (num_predict=64) is sized for a
        # short chat reply, not this schema: 13 temperament dimensions (each
        # with an evidence string), narrative fields, and a variable-length
        # biography list routinely need several hundred tokens. Too low a
        # budget doesn't fail loudly -- it truncates the JSON object mid-field,
        # which _extract_json_object then reports as "never closed".
        options_override={"num_predict": 1024, "num_ctx": 4096},
        role=ModelRole.INTERPRETATION,
    )
    data = _extract_json_object(response)

    dimensions_raw = data.get("dimensions")
    dimensions_raw = dimensions_raw if isinstance(dimensions_raw, dict) else {}
    dimension_names = [
        "warmth",
        "energy",
        "assertiveness",
        "volatility",
        "resilience",
        "opinion_firmness",
        "openness_to_trust",
        "warmth_growth",
        "emotional_lingering",
    ]
    defaults = {"warmth": 0.0}
    scores = {
        name: _as_float(dimensions_raw.get(name), defaults.get(name, 0.5))
        for name in dimension_names
    }
    temperament_fields, inferences = _infer_temperament(scores)

    speaking_style_text = str(data.get("speaking_style") or "").strip()[:200]
    name_candidate = str(data.get("name") or "").strip()
    if name_candidate.lower() in _PRONOUNS_NOT_NAMES:
        name_candidate = ""

    narrative_fields: dict[str, Any] = {
        "name": (name_candidate or "Friend")[:64],
        "base_tone": (str(data.get("base_tone") or "").strip() or "Warm and direct")[
            :200
        ],
        "identity_summary": str(data.get("identity_summary") or "").strip()[:1200],
        "traits": _as_str_list(data.get("traits"), max_items=8),
        "speech_patterns": _as_str_list(data.get("speech_patterns"), max_items=20),
        "avoid": _as_str_list(data.get("avoid"), max_items=64),
        "relationship": (str(data.get("relationship") or "Friend").strip() or "Friend")[
            :64
        ],
        "speaking_style": {"style_description": speaking_style_text}
        if speaking_style_text
        else {},
    }

    merged = PersonaProfile.from_config().model_dump()
    merged.update(temperament_fields)
    merged.update(narrative_fields)
    profile = PersonaProfile(**merged)

    biography_markdown = _biography_markdown(data.get("biography"))

    return CompiledPersona(
        profile=profile,
        biography_markdown=biography_markdown,
        inferences=inferences,
        dimensions=dimensions_raw,
    )
