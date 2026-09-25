"""Per-person social state with outcome-grounded trust and privacy checks."""

from __future__ import annotations

import math
import time
from typing import Any

from pydantic import BaseModel, Field

from .. import clock


class PersonModel(BaseModel):
    """The active agent's bounded model of one distinct person.

    Private facts are never discloseable to a different person. This check is
    intentionally independent of trust so a high-trust relationship cannot
    weaken the privacy boundary.
    """

    person_id: str
    name: str | None = None
    identity_keys: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    current_knowledge: dict[str, Any] = Field(default_factory=dict)
    disclosures: list[dict[str, Any]] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    observed_goals: list[str] = Field(default_factory=list)
    obligations: list[str] = Field(default_factory=list)
    rupture_repair_history: list[dict[str, Any]] = Field(default_factory=list)
    trust_competence: float = Field(default=0.5, ge=0.0, le=1.0)
    trust_benevolence: float = Field(default=0.5, ge=0.0, le=1.0)

    def update_trust_from_reliance(
        self, outcome_success: bool, stake_weight: float = 0.5
    ) -> None:
        """Update separate trust dimensions from an observed reliance outcome."""
        if not math.isfinite(stake_weight):
            return
        if outcome_success:
            competence_delta = 0.05 * stake_weight
            benevolence_delta = 0.02 * stake_weight
        else:
            competence_delta = -0.15 * stake_weight
            benevolence_delta = -0.10 * stake_weight

        self.trust_competence = max(
            0.0, min(1.0, self.trust_competence + competence_delta)
        )
        self.trust_benevolence = max(
            0.0, min(1.0, self.trust_benevolence + benevolence_delta)
        )

    def record_rupture_repair(
        self, kind: str, magnitude: float, notes: str = ""
    ) -> None:
        """Record an asymmetric rupture or repair in the relationship history."""
        kind = kind.lower().strip()
        if kind not in ("rupture", "repair"):
            raise ValueError(f"Invalid rupture/repair kind: {kind}")
        if not math.isfinite(magnitude):
            return
        if kind == "rupture":
            self.trust_benevolence = max(
                0.0, self.trust_benevolence - magnitude * 1.5
            )
        elif kind == "repair":
            self.trust_benevolence = min(
                1.0, self.trust_benevolence + magnitude * 0.5
            )

        self.rupture_repair_history.append(
            {
                "kind": kind,
                "magnitude": magnitude,
                "notes": notes,
                "timestamp": clock.time(),
            }
        )

    def can_disclose(
        self,
        target_person_id: str,
        fact_owner_id: str | None,
        is_private: bool = True,
    ) -> bool:
        """Return whether a fact can be disclosed without crossing ownership."""
        if not is_private:
            return True
        if fact_owner_id is None:
            return False
        return fact_owner_id == target_person_id

    def record_disclosure(self, fact_id: str, context: str = "") -> None:
        """Record that a fact was disclosed to this person in a context."""
        self.disclosures.append(
            {
                "fact_id": fact_id,
                "timestamp": clock.time(),
                "context": context,
            }
        )
