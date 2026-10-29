import logging
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from ..config import Config
from .model_roles import ModelRole
from .role_trace import trace_role

logger = logging.getLogger("anthropic_client")

_DEFAULT_MODEL = "claude-sonnet-5"
# Ollama's chat/appraisal calls ask for short completions (num_predict caps
# of 40-250, see action.py::_compute_endocrine_options); this is a safe
# ceiling for the same kind of turn when no `num_predict` override arrives.
_DEFAULT_MAX_TOKENS = 1024


def _translate_options(options_override: dict[str, Any] | None) -> dict[str, Any]:
    """Ollama-shaped sampling options -> Anthropic-shaped ones.

    `temperature`/`top_p` carry over unchanged (cortisol/dopamine mapping,
    see action.py::_compute_endocrine_options). `num_predict` (Ollama's
    output-length cap) becomes `max_tokens`. `num_ctx`/`num_thread` are
    Ollama runtime knobs (context window sizing, CPU threads) with no
    Anthropic equivalent and are dropped rather than mistranslated.
    """
    if not options_override:
        return {}
    translated: dict[str, Any] = {}
    if "temperature" in options_override:
        translated["temperature"] = options_override["temperature"]
    if "top_p" in options_override:
        translated["top_p"] = options_override["top_p"]
    if "num_predict" in options_override:
        translated["max_tokens"] = options_override["num_predict"]
    return translated


class AnthropicClient:
    """Cloud fallback satisfying `app.llm.LLMClient` (roadmap Phase 4.2).

    Built on the official `anthropic` SDK, not raw HTTP -- unlike
    `OllamaClient`, which hand-rolls its HTTP calls because Ollama has no
    SDK, Anthropic does, so this goes through it.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL):
        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def close(self) -> None:
        await self._client.close()

    async def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
        role: ModelRole | None = None,
    ) -> AsyncGenerator[str, None]:
        trace_role("anthropic", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            # MOCK_LLM_TEXT must block every real network call regardless of
            # provider -- see OllamaClient's own guard, which this mirrors at
            # the "never hit the network" level without replicating its
            # corpus-tuned branching (that mock is tuned to the eval corpus,
            # which is Ollama-specific).
            yield "I'm thinking about our conversation, my friend."
            return

        options = _translate_options(options_override)
        max_tokens = options.pop("max_tokens", _DEFAULT_MAX_TOKENS)
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            **options,
        }
        if system:
            kwargs["system"] = system
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as exc:
            logger.warning("[Anthropic] generate_stream failed: %s", exc)
            yield "I'm having trouble thinking right now..."

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
        role: ModelRole | None = None,
    ) -> str:
        trace_role("anthropic", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            return "I am glad we are chatting, my friend. What should we work on next?"

        options = _translate_options(options_override)
        max_tokens = options.pop("max_tokens", _DEFAULT_MAX_TOKENS)
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            **options,
        }
        if system:
            kwargs["system"] = system
        try:
            response = await self._client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content if block.type == "text"
            )
        except anthropic.APIError as exc:
            logger.warning("[Anthropic] generate failed: %s", exc)
            return "Error generating response."

    async def describe_image(
        self,
        image_b64: str,
        prompt: str = "What do you see?",
        model: str | None = None,
        role: ModelRole | None = None,
    ) -> str | None:
        """Mirrors OllamaClient.describe_image's H8 contract: `None` means the
        call itself failed, `""` means the model looked and saw nothing worth
        describing -- callers (VisualAppraisalService) tell these apart.
        """
        trace_role("anthropic", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            ai_name = getattr(Config, "AI_NAME", "AI Friend")
            return f"A user sitting at a desk pair-programming with their AI friend {ai_name}."

        try:
            response = await self._client.messages.create(
                model=model or self.model,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    # vision/links.py encodes every capture as
                                    # JPEG (cv2.imencode(".jpg", ...)) before
                                    # this ever gets called.
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""
        except anthropic.APIError as exc:
            logger.warning(
                "[Anthropic] describe_image failed for model %s (%s); "
                "returning no description.",
                model or self.model,
                exc,
            )
            return None

    async def check_health(self) -> bool:
        try:
            await self._client.models.retrieve(self.model)
            return True
        except Exception:
            return False
