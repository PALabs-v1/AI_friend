"""Provider-neutral LLM client surface (roadmap Phase 4.2).

Every `app/` construction site builds its client through `build_llm_client`
instead of importing `OllamaClient` directly, so the provider is a deployment
choice (`Config.LLM_PROVIDER`) rather than something baked into each call
site. `LLMClient` is the Protocol both `OllamaClient` and `AnthropicClient`
satisfy -- the four methods every caller in this codebase actually uses.

`backend/evals/` deliberately keeps constructing `OllamaClient` directly and
is not routed through this factory: its reproducibility story
(`runner.reset_model_state`) unloads and reloads a real local Ollama model
between runs, which has no cloud equivalent, and CLAUDE.md documents the
harness as probing "the LLM boundary" with a real `OllamaClient` on purpose.
"""

from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    # Not `async def`: the real implementations are async-generator functions
    # (they `yield`), so calling them returns the generator directly rather
    # than a coroutine that resolves to one.
    def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]: ...

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
    ) -> str: ...

    async def describe_image(
        self,
        image_b64: str,
        prompt: str = "What do you see?",
        model: str | None = None,
    ) -> str | None: ...

    async def check_health(self) -> bool: ...

    async def close(self) -> None: ...


def build_llm_client(*, base_url: str, model: str | None = None) -> LLMClient:
    """Construct the client selected by `Config.LLM_PROVIDER`.

    `base_url`/`model` are passed through exactly as today's direct
    `OllamaClient(...)` call sites pass them -- including `model=None`, which
    already happens whenever `Config.LLM_CHAT_MODEL` is unset and falls back
    to `OllamaClient`'s own default. `base_url` has no meaning for the cloud
    provider and is simply unused there; there is exactly one Anthropic
    endpoint.
    """
    from ..config import Config

    provider = (getattr(Config, "LLM_PROVIDER", None) or "ollama").strip().lower()

    if provider == "ollama":
        from .ollama_client import OllamaClient

        return OllamaClient(base_url=base_url, model=model)

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        api_key = getattr(Config, "ANTHROPIC_API_KEY", None)
        if not api_key:
            raise ValueError(
                "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set."
            )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if model:
            kwargs["model"] = model
        return AnthropicClient(**kwargs)

    if provider == "claude_code":
        from .claude_code_client import ClaudeCodeClient

        kwargs = {}
        if model:
            kwargs["model"] = model
        return ClaudeCodeClient(**kwargs)

    raise ValueError(
        f"Unknown LLM_PROVIDER {provider!r}; expected 'ollama', 'anthropic', or 'claude_code'."
    )
