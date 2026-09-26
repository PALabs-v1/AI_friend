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
async def test_agent_consumes_its_subject_through_jetstream_only(
    real_nats, nats_accounts_server
):
    """transport_agent consumes audio.stream the way BaseAgent.subscribe
    does, through a durable push consumer delivering on _INBOX, and that is
    all its grant allows: a core-NATS subscription to the subject itself,
    which no Python agent makes, is refused (least privilege, F-018)."""
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
        sub = await nc.jetstream().subscribe(
            "audio.stream", durable="transport_agent_audio_stream"
        )
        await sub.unsubscribe()
        await asyncio.sleep(0.2)
        assert violations == []

        await nc.subscribe("audio.stream")
        await asyncio.sleep(0.3)
        assert any("audio.stream" in v for v in violations)
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


# --- F-017 and the grant audit -------------------------------------------
#
# The tests below use the real stream-setup code, not a hand-built layout:
# `app/nats_streams.py` is loaded fresh against the real `nats` client, since
# the suite-wide simulator is what the already-imported copy is bound to.


def _real_nats_streams():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "app" / "nats_streams.py"
    spec = importlib.util.spec_from_file_location("app._real_nats_streams", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _grant_audit():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("check_nats_grants")
    finally:
        sys.path.remove(str(scripts))


def _password(user: str) -> str:
    return f"changeme_{user}"


async def _provision_with_setup_streams(streams_module, port: int, monkeypatch):
    monkeypatch.setenv("NATS_URL", f"nats://127.0.0.1:{port}")
    monkeypatch.setenv("NATS_USER", "nats_provisioner")
    monkeypatch.setenv("NATS_PASSWORD", _password("nats_provisioner"))
    await streams_module.setup_streams(retries=3, delay_seconds=0.2)


@pytest.mark.asyncio
async def test_a_denied_publish_costs_the_whole_ack_timeout(
    real_nats, nats_accounts_server
):
    """Mechanism, not regression: why a grant gap stalls an agent. The server
    drops a denied publish without replying, so the JetStream ack wait runs
    to its timeout.
    BaseAgent.publish uses 10 s; this uses 1 s to keep the test short."""
    port = nats_accounts_server
    await _bootstrap_stream(real_nats, port)
    nc = await real_nats.connect(_url(port, "vision_agent", "changeme_vision_agent"))
    try:
        started = time.monotonic()
        with pytest.raises((real_nats.errors.Error, TimeoutError)):
            await nc.jetstream().publish("chat.output", b"forged", timeout=1.0)
        assert time.monotonic() - started >= 0.9
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_every_agent_can_publish_and_consume_what_its_code_uses(
    real_nats, nats_accounts_server, monkeypatch
):
    """Regression: grants written when auth was opt-in never picked up later
    subjects, and W10a made auth the default, so under the shipped config
    the transport could not publish audio.playback.lifecycle (W4), the brain
    could not publish state.update on any turn, and the web chat bridge
    could neither send nor receive. Every subject the grant audit derives
    from the code is exercised here as the agent that uses it."""
    port = nats_accounts_server
    await _provision_with_setup_streams(_real_nats_streams(), port, monkeypatch)
    audit = _grant_audit()
    uses = audit.collect_uses()
    assert not uses.unowned

    denied = []
    for user in sorted(uses.publishes):
        nc = await real_nats.connect(_url(port, user, _password(user)))
        try:
            js = nc.jetstream()
            for subject in sorted(uses.publishes[user]):
                try:
                    await js.publish(subject, b"{}", timeout=2.0)
                except (
                    real_nats.errors.NoRespondersError,
                    real_nats.js.errors.NoStreamResponseError,
                ):
                    pass  # allowed; no stream holds the subject (core NATS)
                except (real_nats.errors.Error, TimeoutError) as error:
                    denied.append(f"{user} publish {subject}: {error!r}")
        finally:
            await nc.close()

    for user in sorted(uses.subscribes):
        if user in audit.RUST_ENTRIES:
            continue  # core-NATS subscribers; covered by the static audit
        nc = await real_nats.connect(_url(port, user, _password(user)))
        try:
            js = nc.jetstream()
            for index, (subject, use) in enumerate(
                sorted(uses.subscribes[user].items())
            ):
                durable = f"audit_{user}_{index}"
                try:
                    sub = await asyncio.wait_for(
                        js.subscribe(subject, durable=durable), timeout=3.0
                    )
                    await sub.unsubscribe()
                    if use.reconciles:
                        # BaseAgent._reconcile_consumer_config: look the
                        # durable up and delete it when its config drifted.
                        stream = await js.find_stream_name_by_subject(subject)
                        await asyncio.wait_for(
                            js._jsm.consumer_info(stream, durable), timeout=3.0
                        )
                        await asyncio.wait_for(
                            js._jsm.delete_consumer(stream, durable), timeout=3.0
                        )
                except real_nats.js.errors.NotFoundError:
                    pass  # no stream holds it; BaseAgent falls back to core
                except (real_nats.errors.Error, TimeoutError) as error:
                    denied.append(f"{user} consume {subject}: {error!r}")
        finally:
            await nc.close()

    assert not denied, "\n".join(denied)


async def _old_layout(real_nats, streams, port: int) -> None:
    """The layout every install before F-017 has: AI_MESSAGES holding state.>
    (with retained state.broadcast snapshots and one state.update work item),
    and the subconscious's durable state.broadcast consumer on it."""
    old_subjects = [
        subject
        for subject in streams.CORE_STREAMS["AI_MESSAGES"]
        if not subject.startswith("state.")
    ] + ["state.>"]
    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", _password("nats_provisioner"))
    )
    try:
        jsm = provisioner.jsm()
        await jsm.add_stream(name="AI_MESSAGES", subjects=old_subjects)
        await jsm.add_stream(name="AI_AUDIO", subjects=["audio.>"])
        await jsm.add_consumer(
            "AI_MESSAGES",
            durable_name="subconscious_agent_state_broadcast",
            filter_subject="state.broadcast",
        )
        await jsm.add_consumer(
            "AI_MESSAGES",
            durable_name="subconscious_agent_session_presence",
            filter_subject="state.presence",
        )
    finally:
        await provisioner.close()
    brain = await real_nats.connect(_url(port, "brain_agent", _password("brain_agent")))
    try:
        js = brain.jetstream()
        await js.publish("state.broadcast", b'{"revision": 0}')
        await js.publish("state.broadcast", b'{"revision": 0}')
        await js.publish("state.update", b'{"work": 1}')
    finally:
        await brain.close()


async def _assert_migrated(real_nats, port: int) -> None:
    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", _password("nats_provisioner"))
    )
    try:
        jsm = provisioner.jsm()
        messages = await jsm.stream_info("AI_MESSAGES")
        assert "state.>" not in messages.config.subjects
        assert {"state.update", "state.subconscious", "state.presence"} <= set(
            messages.config.subjects
        )
        # The retained snapshots left with their subject; the work item stayed.
        assert messages.state.messages == 1
        kept = await jsm.get_last_msg("AI_MESSAGES", "state.update")
        assert kept.data == b'{"work": 1}'
        state = await jsm.stream_info("AI_STATE")
        assert state.config.subjects == ["state.broadcast"]
        assert state.config.max_msgs_per_subject == 1
        consumers = {c.name for c in await jsm.consumers_info("AI_MESSAGES")}
        assert consumers == {"subconscious_agent_session_presence"}
    finally:
        await provisioner.close()


async def _stream_messages(real_nats, port: int, stream: str) -> int:
    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", _password("nats_provisioner"))
    )
    try:
        return (await provisioner.jsm().stream_info(stream)).state.messages
    finally:
        await provisioner.close()


@pytest.mark.asyncio
async def test_state_broadcast_moves_to_a_latest_only_stream(
    real_nats, nats_accounts_server, monkeypatch
):
    """F-017 migration from the pre-F-017 layout: state.broadcast moves to
    AI_STATE, the other state subjects (work items) stay in AI_MESSAGES with
    their messages, the retained snapshots are purged, only the consumer whose
    subject left is dropped, and running setup again changes nothing."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _old_layout(real_nats, streams, port)

    await _provision_with_setup_streams(streams, port, monkeypatch)
    await _provision_with_setup_streams(streams, port, monkeypatch)  # idempotent
    await _assert_migrated(real_nats, port)

    subconscious = await real_nats.connect(
        _url(port, "subconscious_agent", _password("subconscious_agent"))
    )
    brain = await real_nats.connect(_url(port, "brain_agent", _password("brain_agent")))
    try:
        received = []
        sub = await subconscious.jetstream().subscribe(
            "state.broadcast",
            durable="subconscious_agent_state_broadcast",
            cb=lambda msg: received.append(msg.data),
        )
        for revision in (1, 2, 3):
            ack = await brain.jetstream().publish(
                "state.broadcast", f'{{"revision": {revision}}}'.encode()
            )
            assert ack.stream == "AI_STATE"
        work = await brain.jetstream().publish("state.update", b"{}")
        assert work.stream == "AI_MESSAGES"
        deadline = time.monotonic() + 5
        while len(received) < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert received[-1] == b'{"revision": 3}'
        await sub.unsubscribe()
    finally:
        await subconscious.close()
        await brain.close()

    assert await _stream_messages(real_nats, port, "AI_STATE") == 1


@pytest.mark.asyncio
async def test_concurrent_setups_migrate_once_without_failing(
    real_nats, nats_accounts_server, monkeypatch
):
    """Every provisioning run on a redeploy may race another (compose restart,
    a manual setup_nats_streams.py). Two runs deleting the same moved-subject
    consumer used to fail the loser with NotFound."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _old_layout(real_nats, streams, port)

    await asyncio.gather(
        *(_provision_with_setup_streams(streams, port, monkeypatch) for _ in range(4))
    )
    await _assert_migrated(real_nats, port)


@pytest.mark.asyncio
async def test_a_resumed_consumer_gets_the_latest_full_snapshot(
    real_nats, nats_accounts_server, monkeypatch
):
    """Why every state.broadcast carries the full snapshot: on a latest-only
    stream a consumer that was away sees only the newest message, so a
    delta ("unchanged since last time") would leave it on a stale list. The
    subconscious reconnecting with its durable gets the newest snapshot and
    nothing older."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _provision_with_setup_streams(streams, port, monkeypatch)

    async def subscribe_as_subconscious(received):
        nc = await real_nats.connect(
            _url(port, "subconscious_agent", _password("subconscious_agent"))
        )
        # The push durable BaseAgent.subscribe creates; cb means auto-ack.
        await nc.jetstream().subscribe(
            "state.broadcast",
            durable="subconscious_agent_state_broadcast",
            cb=lambda msg: received.append(msg.data),
        )
        return nc

    async def wait_for(received, count):
        deadline = time.monotonic() + 5
        while len(received) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    brain = await real_nats.connect(_url(port, "brain_agent", _password("brain_agent")))
    try:
        before = []
        subconscious = await subscribe_as_subconscious(before)
        await brain.jetstream().publish("state.broadcast", b'{"revision": 1}')
        await wait_for(before, 1)
        assert before == [b'{"revision": 1}']
        await subconscious.close()  # away (restart); the durable keeps its cursor

        for revision in (2, 3, 4):
            await brain.jetstream().publish(
                "state.broadcast", f'{{"revision": {revision}}}'.encode()
            )

        after = []
        subconscious = await subscribe_as_subconscious(after)
        await wait_for(after, 1)
        await asyncio.sleep(0.5)  # anything older would have arrived by now
        await subconscious.close()
        assert after == [b'{"revision": 4}']
    finally:
        await brain.close()


def _subconscious_state_broadcast_policy() -> str:
    """The deliver_policy SubconsciousAgent passes when it subscribes to
    state.broadcast, read from its source so this test follows the code."""
    import ast

    path = Path(__file__).resolve().parents[1] / "app/agents/subconscious_agent.py"
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "subscribe"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "state.broadcast"
        ):
            for keyword in node.keywords:
                if keyword.arg == "deliver_policy":
                    return keyword.value.value
            return "all"  # BaseAgent.subscribe's default
    raise AssertionError("SubconsciousAgent no longer subscribes to state.broadcast")


@pytest.mark.asyncio
async def test_a_subconscious_started_after_migration_gets_the_retained_snapshot(
    real_nats, nats_accounts_server, monkeypatch
):
    """The migration deletes the subconscious's durable, so the next start
    creates a new one on AI_STATE. Broadcasts are the subconscious's only
    source of the brain's state; with deliver_policy "new" the new durable
    skipped the snapshot AI_STATE already held and ran on persona defaults
    until the next tick. It must receive that snapshot at once."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _old_layout(real_nats, streams, port)
    await _provision_with_setup_streams(streams, port, monkeypatch)

    brain = await real_nats.connect(_url(port, "brain_agent", _password("brain_agent")))
    try:
        await brain.jetstream().publish("state.broadcast", b'{"revision": 7}')
    finally:
        await brain.close()

    policy = real_nats.js.api.DeliverPolicy(_subconscious_state_broadcast_policy())
    subconscious = await real_nats.connect(
        _url(port, "subconscious_agent", _password("subconscious_agent"))
    )
    try:
        received = []
        await subconscious.jetstream().subscribe(
            "state.broadcast",
            durable="subconscious_agent_state_broadcast",
            deliver_policy=policy,
            cb=lambda msg: received.append(msg.data),
        )
        deadline = time.monotonic() + 3
        while not received and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert received == [b'{"revision": 7}']
    finally:
        await subconscious.close()


@pytest.mark.asyncio
async def test_a_broadcast_published_during_migration_is_not_left_behind(
    real_nats, nats_accounts_server, monkeypatch
):
    """The brain keeps publishing while setup runs. A state.broadcast that
    landed in AI_MESSAGES after the purge but before state.> left the stream
    stayed there, unreadable, holding its bytes until max_age. Reproduced by
    publishing in exactly that window."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _old_layout(real_nats, streams, port)

    brain = await real_nats.connect(_url(port, "brain_agent", _password("brain_agent")))
    manager = real_nats.js.manager.JetStreamManager
    update_stream = manager.update_stream
    published_in_window = []

    async def publish_then_update(self, config=None, **params):
        # The last moment AI_MESSAGES still holds state.>: after any
        # pre-update cleanup, just before the subject leaves the stream.
        if (
            config is not None
            and config.name == "AI_MESSAGES"
            and not published_in_window
        ):
            ack = await brain.jetstream().publish(
                "state.broadcast", b'{"revision": "in the window"}'
            )
            published_in_window.append(ack.stream)
        return await update_stream(self, config, **params)

    monkeypatch.setattr(manager, "update_stream", publish_then_update)
    try:
        await _provision_with_setup_streams(streams, port, monkeypatch)
    finally:
        await brain.close()

    assert published_in_window == ["AI_MESSAGES"]  # the window was hit
    await _assert_migrated(real_nats, port)


@pytest.mark.asyncio
async def test_a_consumer_keeps_its_filters_that_stay_in_the_stream(
    real_nats, nats_accounts_server, monkeypatch
):
    """A consumer filtering on state.broadcast and a subject AI_MESSAGES
    keeps was deleted whole, discarding its cursor on the subject that did
    not move. It must survive, filtering on what stays, cursor intact."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    # _old_layout already publishes state.broadcast/state.update messages;
    # deliver_policy "new" keeps this consumer's cursor about the chat.output
    # published below, not an artifact of replaying those from the start.
    await _old_layout(real_nats, streams, port)

    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", _password("nats_provisioner"))
    )
    try:
        jsm = provisioner.jsm()
        await jsm.add_consumer(
            "AI_MESSAGES",
            durable_name="mixed",
            filter_subjects=["state.broadcast", "chat.output"],
            deliver_policy=real_nats.js.api.DeliverPolicy.NEW,
        )
        pull = await provisioner.jetstream().pull_subscribe_bind(
            "mixed", stream="AI_MESSAGES"
        )

        brain = await real_nats.connect(
            _url(port, "brain_agent", _password("brain_agent"))
        )
        try:
            await brain.jetstream().publish("chat.output", b'{"text": "hello"}')
        finally:
            await brain.close()

        (msg,) = await pull.fetch(1, timeout=3)
        assert msg.subject == "chat.output"
        before = (await jsm.consumer_info("AI_MESSAGES", "mixed")).delivered.stream_seq
        assert before > 0

        await _provision_with_setup_streams(streams, port, monkeypatch)

        after = await jsm.consumer_info("AI_MESSAGES", "mixed")
        filters = after.config.filter_subjects or [after.config.filter_subject]
        assert filters == ["chat.output"]
        assert after.delivered.stream_seq == before
    finally:
        await provisioner.close()


@pytest.mark.asyncio
async def test_a_broad_filter_consumer_is_left_alone_when_it_still_overlaps(
    real_nats, nats_accounts_server, monkeypatch
):
    """A consumer filtering on the wildcard state.> (broader than any single
    exact subject the migration re-adds) is not literally covered by any one
    of state.update/state.subconscious/state.presence, but it still overlaps
    them -- and NATS itself keeps delivering it correctly once state.broadcast
    moves elsewhere, since a subject is captured by only one stream. It must
    be left completely alone: not deleted, not narrowed, cursor intact."""
    streams = _real_nats_streams()
    port = nats_accounts_server
    await _old_layout(real_nats, streams, port)

    provisioner = await real_nats.connect(
        _url(port, "nats_provisioner", _password("nats_provisioner"))
    )
    try:
        jsm = provisioner.jsm()
        await jsm.add_consumer(
            "AI_MESSAGES",
            durable_name="broadfilter",
            filter_subject="state.>",
            deliver_policy=real_nats.js.api.DeliverPolicy.NEW,
        )
        pull = await provisioner.jetstream().pull_subscribe_bind(
            "broadfilter", stream="AI_MESSAGES"
        )

        brain = await real_nats.connect(
            _url(port, "brain_agent", _password("brain_agent"))
        )
        try:
            await brain.jetstream().publish("state.update", b'{"seen": "before"}')
        finally:
            await brain.close()
        (msg,) = await pull.fetch(1, timeout=3)
        assert msg.data == b'{"seen": "before"}'
        before = (
            await jsm.consumer_info("AI_MESSAGES", "broadfilter")
        ).delivered.stream_seq

        await _provision_with_setup_streams(streams, port, monkeypatch)

        after = await jsm.consumer_info("AI_MESSAGES", "broadfilter")
        assert after.config.filter_subject == "state.>"
        assert after.delivered.stream_seq == before

        brain = await real_nats.connect(
            _url(port, "brain_agent", _password("brain_agent"))
        )
        try:
            await brain.jetstream().publish("state.update", b'{"seen": "after"}')
        finally:
            await brain.close()
        (msg,) = await pull.fetch(1, timeout=3)
        assert msg.data == b'{"seen": "after"}'
    finally:
        await provisioner.close()
