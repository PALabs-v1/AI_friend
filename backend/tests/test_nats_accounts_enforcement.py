"""
Proves the default `nats-accounts.conf` actually enforces the
per-agent permissions it declares, against a real `nats-server` -- not just
that the config file parses. Per the roadmap's own words: "A test asserts
the scoping actually denies a subject outside an agent's grant; without
that, the accounts file is decoration."

Needs a real `nats-server` binary on PATH (`brew install nats-server` or
equivalent) and skips loudly without one, the same shape every other
live-infra test in this suite uses (see e.g. stt-agent's
`real_model_loads_and_perceives_audio`, voice-agent's
`publish_pcm_does_not_wait_for_the_jetstream_ack`).
"""

import asyncio
import importlib
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_CONF = REPO_ROOT / "nats-accounts.conf"

pytestmark = pytest.mark.skipif(
    shutil.which("nats-server") is None,
    reason="SKIP: no nats-server binary on PATH -- install it to run these "
    "(e.g. `brew install nats-server`)",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def real_nats():
    """`conftest.py` replaces `sys.modules["nats"]` (and every `nats.*`
    submodule) with an in-memory simulator for the whole suite, so ordinary
    unit tests never need live infra -- see its "HIGH-FIDELITY IN-MEMORY
    NATS SIMULATOR" section. These tests are the deliberate exception: they
    need the real `nats.py` client talking to a real `nats-server`, not the
    simulator. Swaps the fake out only for this fixture's lifetime and
    restores it in `finally`, so no other test in the session is affected
    regardless of run order.
    """
    fake_entries = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "nats" or name.startswith("nats.")
    }
    for name in fake_entries:
        del sys.modules[name]
    try:
        yield importlib.import_module("nats")
    finally:
        for name in [n for n in sys.modules if n == "nats" or n.startswith("nats.")]:
            del sys.modules[name]
        sys.modules.update(fake_entries)


@pytest.fixture
def nats_accounts_server(tmp_path):
    """Boots a real nats-server from the actual shipped `nats-accounts.conf`,
    not a hand-rolled stand-in -- a passing test here means the file this
    repo actually ships enforces what it claims to, not a lookalike."""
    assert ACCOUNTS_CONF.exists(), f"expected {ACCOUNTS_CONF} to exist"
    try:
        port = _free_port()
    except PermissionError as error:
        pytest.skip(f"sandbox denies local TCP listeners: {error}")
    store_dir = tmp_path / "jetstream"
    account_passwords = {
        "NATS_PROVISIONER_PASSWORD": "changeme_nats_provisioner",
        "NATS_SIGNALING_PASSWORD": "changeme_signaling",
        "NATS_BRAIN_PASSWORD": "changeme_brain_agent",
        "NATS_SUBCONSCIOUS_PASSWORD": "changeme_subconscious_agent",
        "NATS_SURFACING_PASSWORD": "changeme_surfacing_agent",
        "NATS_SYSTEM_PASSWORD": "changeme_system_agent",
        "NATS_TRANSPORT_PASSWORD": "changeme_transport_agent",
        "NATS_VISION_PASSWORD": "changeme_vision_agent",
        "NATS_STT_PASSWORD": "changeme_stt_agent",
        "NATS_VOICE_PASSWORD": "changeme_voice_agent",
    }
    server_env = os.environ.copy()
    server_env.update(account_passwords)
    proc = subprocess.Popen(
        [
            "nats-server",
            "-p",
            str(port),
            "-c",
            str(ACCOUNTS_CONF),
            "-sd",
            str(store_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=server_env,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            proc.kill()
            raise RuntimeError("nats-server did not open its port in time")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _url(port: int, user: str, password: str) -> str:
    return f"nats://{user}:{password}@127.0.0.1:{port}"


async def _bootstrap_stream(nats_module, port: int) -> None:
    """Create the streams through the dedicated provisioning identity."""
    nc = await nats_module.connect(
        _url(port, "nats_provisioner", "changeme_nats_provisioner")
    )
    js = nc.jetstream()
    await js.add_stream(
        name="AI_MESSAGES",
        subjects=[
            "chat.output",
            "chat.input",
            "vision.description",
            "vision.frames",
            "state.broadcast",
            "cache.sync",
        ],
    )
    await js.add_stream(name="AI_AUDIO", subjects=["audio.>"])
    await nc.close()


@pytest.mark.asyncio
async def test_agent_can_publish_its_own_declared_subject(
    real_nats, nats_accounts_server
):
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)

    nc = await real_nats.connect(_url(port, "vision_agent", "changeme_vision_agent"))
    try:
        js = nc.jetstream()
        ack = await js.publish("vision.description", b"ok")
        assert ack.stream == "AI_MESSAGES"
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_agent_cannot_publish_a_subject_outside_its_grant(
    real_nats, nats_accounts_server
):
    """The security property this whole file exists for: vision_agent must
    not be able to forge a chat.output message claiming to be the brain."""
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)

    nc = await real_nats.connect(_url(port, "vision_agent", "changeme_vision_agent"))
    try:
        js = nc.jetstream()
        with pytest.raises((real_nats.errors.Error, TimeoutError)):
            await asyncio.wait_for(js.publish("chat.output", b"forged"), timeout=3.0)
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_agent_cannot_subscribe_a_subject_outside_its_grant(
    real_nats, nats_accounts_server
):
    """transport_agent has no business need to see vision.description --
    confirm the denial actually fires (via error_cb; see the accounts file's
    own "KNOWN LIMITATION" note on why this doesn't raise synchronously)."""
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)

    violations = []

    async def _on_error(exc):
        violations.append(str(exc))

    nc = await real_nats.connect(
        _url(port, "transport_agent", "changeme_transport_agent"),
        error_cb=_on_error,
    )
    try:
        await nc.subscribe("vision.description")
        await asyncio.sleep(0.3)
        assert violations, (
            "expected a permissions violation for an out-of-grant subscribe"
        )
        assert "vision.description" in violations[0]
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_agent_can_subscribe_its_own_declared_subject(
    real_nats, nats_accounts_server
):
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)

    violations = []

    async def _on_error(exc):
        violations.append(str(exc))

    nc = await real_nats.connect(
        _url(port, "transport_agent", "changeme_transport_agent"),
        error_cb=_on_error,
    )
    try:
        await nc.subscribe("audio.stream")
        await asyncio.sleep(0.2)
        assert violations == []
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_wrong_password_is_rejected(real_nats, nats_accounts_server):
    port = nats_accounts_server
    with pytest.raises((real_nats.errors.Error, TimeoutError)):
        await asyncio.wait_for(
            real_nats.connect(_url(port, "vision_agent", "not-the-real-password")),
            timeout=3.0,
        )


@pytest.mark.asyncio
async def test_default_mesh_config_refuses_an_unauthenticated_client(
    real_nats, nats_accounts_server
):
    with pytest.raises((real_nats.errors.Error, TimeoutError)):
        await asyncio.wait_for(
            real_nats.connect(f"nats://127.0.0.1:{nats_accounts_server}"),
            timeout=3.0,
        )


@pytest.mark.asyncio
async def test_provisioner_can_administer_jetstream(real_nats, nats_accounts_server):
    """Only the dedicated identity can create a stream."""
    port = nats_accounts_server
    nc = await real_nats.connect(
        _url(port, "nats_provisioner", "changeme_nats_provisioner")
    )
    try:
        await nc.jetstream().add_stream(
            name="PROVISIONED_TEST", subjects=["provisioned.test"]
        )
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_runtime_agent_cannot_administer_jetstream(
    real_nats, nats_accounts_server
):
    """Runtime credentials cannot create streams."""
    port = nats_accounts_server
    nc = await real_nats.connect(_url(port, "vision_agent", "changeme_vision_agent"))
    try:
        with pytest.raises((real_nats.errors.Error, TimeoutError)):
            await asyncio.wait_for(
                nc.jetstream().add_stream(
                    name="FORBIDDEN_TEST", subjects=["forbidden.test"]
                ),
                timeout=3.0,
            )
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_runtime_agent_cannot_retrieve_an_unauthorized_stream_consumer(
    real_nats, nats_accounts_server
):
    """A message-only runtime identity cannot inspect audio consumers."""
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)
    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", "changeme_nats_provisioner")
    )
    try:
        await provisioner.jetstream().add_consumer(
            "AI_AUDIO",
            durable_name="audio_consumer",
            filter_subject="audio.>",
        )
    finally:
        await provisioner.close()

    nc = await real_nats.connect(_url(port, "vision_agent", "changeme_vision_agent"))
    try:
        with pytest.raises((real_nats.errors.Error, TimeoutError)):
            await asyncio.wait_for(
                nc.jetstream()._jsm.consumer_info("AI_AUDIO", "audio_consumer"),
                timeout=3.0,
            )
    finally:
        await nc.close()
