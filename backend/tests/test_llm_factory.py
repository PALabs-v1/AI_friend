"""
`build_llm_client` (roadmap Phase 4.2) is the one place `Config.LLM_PROVIDER`
gets turned into a real client. If it silently fell through to Ollama on an
unrecognized provider, or built an `AnthropicClient` with no API key, a
deployer who set `LLM_PROVIDER=anthropic` intending to go cloud-only would
either keep hitting a local Ollama that was never started, or get an
`AnthropicClient` that fails every call with an opaque auth error instead of
a clear message at construction time.
"""

from app import config as config_module
from app.llm import LLMClient, build_llm_client
from app.llm.anthropic_client import _DEFAULT_MODEL, AnthropicClient
from app.llm.claude_code_client import ClaudeCodeClient
from app.llm.ollama_client import OllamaClient


def test_default_provider_builds_an_ollama_client():
    client = build_llm_client(base_url="http://127.0.0.1:11434", model="llama3.2:3b")
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://127.0.0.1:11434"
    assert client.model == "llama3.2:3b"


def test_claude_code_provider_builds_client_and_satisfies_protocol(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "claude_code")
    client = build_llm_client(base_url="unused", model="claude-sonnet-5")
    assert isinstance(client, ClaudeCodeClient)
    assert isinstance(client, LLMClient)
    assert client.model == "claude-sonnet-5"


def test_claude_code_provider_uses_its_default_model_setting(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "claude_code")
    client = build_llm_client(base_url="unused", model=None)
    assert isinstance(client, ClaudeCodeClient)
    assert client.model is None


def test_ollama_client_satisfies_the_llmclient_protocol():
    client = build_llm_client(base_url="http://127.0.0.1:11434")
    assert isinstance(client, LLMClient)


def test_empty_provider_string_falls_back_to_ollama(monkeypatch):
    # `Config.LLM_PROVIDER` is a plain str field (default "ollama"), never
    # None, but an empty string in a real .env is a realistic misconfiguration
    # this must not silently misroute.
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "")
    client = build_llm_client(base_url="http://127.0.0.1:11434")
    assert isinstance(client, OllamaClient)


def test_provider_matching_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "  Ollama  ")
    client = build_llm_client(base_url="http://127.0.0.1:11434")
    assert isinstance(client, OllamaClient)


def test_unknown_provider_raises_value_error(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "openai")
    try:
        build_llm_client(base_url="http://127.0.0.1:11434")
        raise AssertionError("expected ValueError for an unknown provider")
    except ValueError as exc:
        assert "openai" in str(exc)


def test_anthropic_provider_without_api_key_raises_before_any_network_call(
    monkeypatch,
):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config_module.config_instance, "ANTHROPIC_API_KEY", None)
    try:
        build_llm_client(base_url="unused")
        raise AssertionError("expected ValueError when ANTHROPIC_API_KEY is unset")
    except ValueError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)


def test_anthropic_provider_with_api_key_builds_an_anthropic_client(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        config_module.config_instance, "ANTHROPIC_API_KEY", "sk-ant-test"
    )
    client = build_llm_client(base_url="unused", model="claude-sonnet-5")
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-sonnet-5"


def test_anthropic_client_satisfies_the_llmclient_protocol(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        config_module.config_instance, "ANTHROPIC_API_KEY", "sk-ant-test"
    )
    client = build_llm_client(base_url="unused")
    assert isinstance(client, LLMClient)


def test_anthropic_provider_without_explicit_model_uses_the_clients_own_default(
    monkeypatch,
):
    # Mirrors OllamaClient(base_url=..., model=None) already falling back to
    # its own default when Config.LLM_CHAT_MODEL is unset -- the factory must
    # not force a model onto a provider that wasn't asked for one.
    monkeypatch.setattr(config_module.config_instance, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        config_module.config_instance, "ANTHROPIC_API_KEY", "sk-ant-test"
    )
    client = build_llm_client(base_url="unused", model=None)
    assert client.model == _DEFAULT_MODEL
