"""The bare-model gate arm (D-021, slice 2a).

Same model, the persona, and the full conversation history -- and nothing
else. No `CognitiveService`, no appraisal, no stored affect, no memory
retrieval, no output validation. A V4 change counts as closer only if it beats
this arm as well as V3, because only this arm can return "the architecture adds
nothing".

This module imports no `app.cognitive` or `app.state` code on purpose;
`tests/test_bare_model_arm.py` fails if it ever does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.model_roles import ModelRole

Turn = tuple[str, str]  # (speaker, text); speaker is "user" or "assistant"


@dataclass(frozen=True)
class BareModelArm:
    llm: Any
    persona_text: str
    model: str | None = None
    options_override: dict[str, Any] | None = None

    def build_prompt(self, history: Sequence[Turn], user_text: str) -> tuple[str, str]:
        """(system, prompt). The whole history goes in, in order, untruncated."""
        lines = [f"{speaker}: {text}" for speaker, text in history]
        lines.append(f"user: {user_text}")
        lines.append("assistant:")
        return self.persona_text, "\n".join(lines)

    async def reply(self, history: Sequence[Turn], user_text: str) -> str:
        system, prompt = self.build_prompt(history, user_text)
        return await self.llm.generate(
            prompt,
            system=system,
            model=self.model,
            options_override=self.options_override,
            role=ModelRole.REALIZATION,
        )
