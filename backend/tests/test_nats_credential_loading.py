"""
P2-1 (opt-in): `BaseAgent.connect` must add `user`/`password` to its
`nats.connect` call only when both `NATS_USER`/`NATS_PASSWORD` are set in
the environment, and must be byte-for-byte unchanged (no such kwargs at
all) when they are not -- an unconfigured deployment must connect exactly
as it did before this feature existed. The end-to-end enforcement itself
(a scoped user really is denied an out-of-grant subject) is covered
separately in test_nats_accounts_enforcement.py against a real
nats-server; this is the narrower, infra-free unit check on the client-side
wiring alone.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import nats_streams
from app.agents.base import BaseAgent
from main import AIBackend


class _StubConn:
    def jetstream(self):
        return AsyncMock()


@pytest.mark.asyncio
async def test_connect_fails_closed_when_credentials_are_unset(monkeypatch):
    monkeypatch.delenv("NATS_USER", raising=False)
    monkeypatch.delenv("NATS_PASSWORD", raising=False)
    agent = BaseAgent(name="test_agent")
    agent._bootstrap_mesh = AsyncMock()

    mock_connect = AsyncMock(return_value=_StubConn())
    with patch("app.agents.base.nats.connect", mock_connect):
        with pytest.raises(RuntimeError, match="requires NATS_USER and NATS_PASSWORD"):
            await agent.connect()

    mock_connect.assert_not_awaited()
    agent._bootstrap_mesh.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_adds_credentials_when_both_env_vars_are_set(monkeypatch):
    monkeypatch.setenv("NATS_USER", "vision_agent")
    monkeypatch.setenv("NATS_PASSWORD", "changeme_vision_agent")
    agent = BaseAgent(name="test_agent")
    agent._bootstrap_mesh = AsyncMock()

    mock_connect = AsyncMock(return_value=_StubConn())
    with patch("app.agents.base.nats.connect", mock_connect):
        await agent.connect()

    _, kwargs = mock_connect.await_args
    assert kwargs["user"] == "vision_agent"
    assert kwargs["password"] == "changeme_vision_agent"
    agent._bootstrap_mesh.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_fails_closed_when_only_one_credential_is_set(monkeypatch):
    """A partial identity must not silently fall back to anonymous access."""
    monkeypatch.setenv("NATS_USER", "vision_agent")
    monkeypatch.delenv("NATS_PASSWORD", raising=False)
    agent = BaseAgent(name="test_agent")
    agent._bootstrap_mesh = AsyncMock()

    mock_connect = AsyncMock(return_value=_StubConn())
    with patch("app.agents.base.nats.connect", mock_connect):
        with pytest.raises(RuntimeError, match="requires NATS_USER and NATS_PASSWORD"):
            await agent.connect()

    mock_connect.assert_not_awaited()
    agent._bootstrap_mesh.assert_not_awaited()


def test_constructor_reads_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("NATS_USER", "brain_agent")
    monkeypatch.setenv("NATS_PASSWORD", "changeme_brain_agent")
    agent = BaseAgent(name="brain_agent")
    assert agent.nats_user == "brain_agent"
    assert agent.nats_password == "changeme_brain_agent"


def test_constructor_credentials_default_to_none(monkeypatch):
    monkeypatch.delenv("NATS_USER", raising=False)
    monkeypatch.delenv("NATS_PASSWORD", raising=False)
    agent = BaseAgent(name="brain_agent")
    assert agent.nats_user is None
    assert agent.nats_password is None


@pytest.mark.asyncio
async def test_stream_provisioner_uses_credentials_when_configured(monkeypatch):
    monkeypatch.setenv("NATS_USER", "nats_provisioner")
    monkeypatch.setenv("NATS_PASSWORD", "changeme_nats_provisioner")

    jsm = AsyncMock()
    connection = MagicMock()
    connection.jsm.return_value = jsm
    connection.close = AsyncMock()
    mock_connect = AsyncMock(return_value=connection)

    with patch.object(nats_streams.nats, "connect", mock_connect):
        await nats_streams.setup_streams(retries=1, delay_seconds=0.1)

    _, kwargs = mock_connect.await_args
    assert kwargs["user"] == "nats_provisioner"
    assert kwargs["password"] == "changeme_nats_provisioner"


@pytest.mark.asyncio
async def test_signaling_uses_credentials_when_configured(monkeypatch):
    monkeypatch.setenv("NATS_USER", "signaling")
    monkeypatch.setenv("NATS_PASSWORD", "changeme_signaling")

    connection = MagicMock()
    connection.close = AsyncMock()
    mock_connect = AsyncMock(return_value=connection)
    with (
        patch("main.ensure_models_provisioned"),
        patch("main.nats.connect", mock_connect),
    ):
        backend = AIBackend()
        await backend.initialize()

    _, kwargs = mock_connect.await_args
    assert kwargs["user"] == "signaling"
    assert kwargs["password"] == "changeme_signaling"
