"""The NATS grant audit (scripts/check_nats_grants.py, F-018).

Static half: no server needed, so it runs in every suite. The real-server
half, which publishes and consumes every derived subject as its agent, is in
test_nats_accounts_enforcement.py.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"


@pytest.fixture(scope="module")
def audit():
    sys.path.insert(0, str(SCRIPTS))
    try:
        yield importlib.import_module("check_nats_grants")
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.fixture(scope="module")
def uses(audit):
    return audit.collect_uses()


def test_every_grant_covers_its_agents_code(audit, uses):
    assert audit.find_gaps(uses) == []


def test_no_grant_is_wider_than_its_agents_code(audit, uses):
    """Least privilege: no JetStream right beyond what BaseAgent.subscribe
    uses on the streams the agent consumes, no plain subject it never
    publishes."""
    assert audit.find_excess(uses) == []


def test_the_pre_f018_grants_fail_least_privilege(audit, uses):
    """Regression: every runtime user shipped with `$JS.ACK.>` and
    consumer create/delete on streams it never touches."""
    old = subprocess.run(
        ["git", "show", "657c483f:nats-accounts.conf"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    excess = audit.find_excess(uses, conf_text=old)
    assert any(e.startswith("vision_agent: publish grant $JS.ACK.>") for e in excess)
    assert any(
        e.startswith("surfacing_agent: publish grant $JS.API.CONSUMER.DELETE")
        for e in excess
    )


def test_the_pre_f018_grants_fail_the_audit(audit, uses):
    """Regression: the shipped grants before F-018 (657c483f) are caught,
    with the gaps that broke W4 lifecycle, state.update and the web chat."""
    old = subprocess.run(
        ["git", "show", "657c483f:nats-accounts.conf"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    gaps = audit.find_gaps(uses, conf_text=old)
    assert (
        "transport_agent: publishes audio.playback.lifecycle, not in its publish grant"
        in gaps
    )
    assert "brain_agent: publishes state.update, not in its publish grant" in gaps
    assert "signaling: publishes chat.input, not in its publish grant" in gaps
    assert any(g.startswith("signaling: subscribes chat.output") for g in gaps)


def test_shared_code_counts_for_every_agent_that_injects_into_it(audit, monkeypatch):
    """The subconscious runs StateService too. If it ever handed StateService
    its publish method, it would publish state.broadcast; the audit must then
    demand that grant instead of crediting the brain alone."""
    real = audit.find_injectors()
    assert real == {"brain_agent"}
    monkeypatch.setattr(audit, "find_injectors", lambda: real | {"subconscious_agent"})
    gaps = audit.find_gaps(audit.collect_uses())
    assert (
        "subconscious_agent: publishes state.broadcast, not in its publish grant"
        in gaps
    )


def test_lazy_package_exports_are_in_the_closure(audit):
    """cognitive/__init__ re-exports lazily (PEP 562); a static-only walk
    missed core.py and pipeline.py, and with them every brain mesh signal."""
    closure = audit.import_closure(audit.PYTHON_ENTRIES["brain_agent"])
    assert "app/cognitive/pipeline.py" in closure
    assert "app/cognitive/core.py" in closure


def test_reconciling_subscriptions_need_consumer_delete(audit, uses):
    """subconscious_agent's system.tick passes ack_wait/max_deliver, so
    BaseAgent._reconcile_consumer_config may delete its consumer."""
    tick = uses.subscribes["subconscious_agent"]["system.tick"]
    assert tick.reconciles
    assert not uses.subscribes["signaling"]["chat.output"].reconciles


def test_a_deny_list_is_refused_not_ignored(audit):
    with pytest.raises(SystemExit):
        audit.parse_accounts_conf(
            'users = [ { user: "x", permissions: { publish: { deny: ["a"] } } } ]'
        )


def test_a_publish_the_scan_cannot_read_is_a_gap(audit, monkeypatch, tmp_path):
    """The scan reads literal subjects only. A publish with a computed subject
    used to be dropped silently, so an audit pass said nothing about it; now
    only the named forwarders may carry one."""
    uses = audit.collect_uses()
    assert uses.unresolved == []
    monkeypatch.setattr(audit, "FORWARDERS", {})
    gaps = audit.find_gaps(audit.collect_uses())
    assert any(
        g.startswith("unresolved publish subject") and "app/cognitive/core.py" in g
        for g in gaps
    )


def test_a_keyword_only_subscribe_subject_is_not_a_silent_pass(audit, tmp_path):
    """A `.subscribe(subject=..., callback=...)` call has no positional args;
    the scanner used to require `node.args` truthy to look at a call at all,
    so it vanished with no unowned/unresolved flag, and a grant gap on that
    subject would never surface. Reproduces the critic's repro literally:
    a tenant-scoped subject built at runtime, passed by keyword."""
    path = audit.APP_ROOT / "agents" / "_grants_test_probe.py"
    path.write_text(
        "class Probe:\n"
        "    async def go(self, tenant):\n"
        '        await self.subscribe(subject=f"tenant.{tenant}", callback=self.cb)\n'
    )
    try:
        found, unresolved = audit._python_subscriptions(audit.parse_python_topics())
        assert "agents/_grants_test_probe.py" not in found
        assert any(
            u.endswith(":3") and "_grants_test_probe.py" in u for u in unresolved
        )

        gaps = audit.find_gaps(audit.collect_uses())
        assert any(g.startswith("unresolved subscribe subject:") for g in gaps)
    finally:
        path.unlink()
