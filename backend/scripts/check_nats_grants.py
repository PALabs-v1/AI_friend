#!/usr/bin/env python3
"""Assert every agent's NATS grant covers what its code publishes and consumes.

`nats-accounts.conf` gives each agent a least-privilege user. The grants were
written once, when per-agent auth was opt-in (P2-1), and nothing tied them to
the code afterwards: subjects added later (`audio.playback.lifecycle`,
`state.presence`, ...) were never granted, and W10a then made auth the
default (F-018). A denied publish is dropped by the server with no reply, so
the JetStream ack never comes and `BaseAgent.publish` waits out its 10 s
timeout before a core-NATS fallback that is denied too: the message is lost
and the caller stalls. A denied consumer create leaves a quietly deaf
subscription (see the conf's KNOWN LIMITATION note).

Which process runs which code is derived, not declared:

  - Each agent's code is the import closure of its entry module: the modules
    a real import of it loads (so lazy PEP 562 package exports resolve), plus
    a static walk of every import statement from there (so imports inside
    functions count too).
  - A file that defines a `BaseAgent` subclass publishes and subscribes as
    every agent whose closure contains it (`ChatBridge` runs in signaling).
  - Any other file is shared code that publishes through a callback it was
    handed. It publishes as every agent that hands its own publish method or
    itself to shared code (`publish_cb=self.publish`, `agent=self`) and has
    the file in its closure. An agent that hands nothing over is not
    responsible for shared code, and the check re-derives that on every run.
  - A publish or subscribe site no agent owns fails the check.

Grants required:

  - every published subject: the agent's publish allow list;
  - every Python subscription (a JetStream durable push consumer, as
    `BaseAgent.subscribe` creates): `$JS.API.STREAM.NAMES`, and
    `CONSUMER.INFO`, `CONSUMER.DURABLE.CREATE` and `$JS.ACK` on the stream
    that holds the subject; `CONSUMER.DELETE` too when the subscription
    passes `ack_wait` or `max_deliver` (`_reconcile_consumer_config` deletes a
    consumer whose config drifted);
  - a Rust subscription (core NATS): the subject in the subscribe allow list.

Grants broader than the code needs are printed as warnings, not failures.
`deny` lists are not modelled; the check refuses a conf that has one.

Usage: python scripts/check_nats_grants.py   (needs the backend's packages)
Exit 0 = every grant covers its agent's code. Exit 1 = gaps (printed).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_subject_wiring import (
    APP_ROOT,
    BACKEND_ROOT,
    SubjectUsage,
    _resolve_topics_attribute,
    parse_core_streams,
    parse_python_topics,
    parse_rust_topics,
    scan_python,
    scan_rust,
    subject_matches_pattern,
)

ACCOUNTS_CONF = BACKEND_ROOT.parent / "nats-accounts.conf"

# NATS user -> the module its container runs (docker-compose.prod.yml).
PYTHON_ENTRIES: dict[str, str] = {
    "brain_agent": "app/agents/brain_agent.py",
    "subconscious_agent": "app/agents/subconscious_agent.py",
    "surfacing_agent": "app/agents/surfacing_agent.py",
    "system_agent": "app/agents/system_agent.py",
    "transport_agent": "app/agents/transport_agent.py",
    "vision_agent": "app/vision/agent.py",
    "signaling": "main.py",
}
RUST_ENTRIES: dict[str, str] = {
    "stt_agent": "crates/stt-agent/",
    "voice_agent": "crates/voice-agent/",
}

# An agent hands shared code its publish path with one of these.
INJECTS = re.compile(r"publish_cb\s*=\s*self\.publish\b|\bagent\s*=\s*self\b")

# Every Python agent subscribes to cache.sync in BaseAgent.connect; agents/
# base.py is generic transport code the wiring scan skips, so it is declared.
BASE_AGENT_SUBSCRIBES = ("cache.sync",)
SET_STATE_SUBJECT = "state.update"  # BaseAgent.set_state
RECONCILE_KWARGS = {"ack_wait", "max_deliver"}

# Replies and push deliveries arrive on _INBOX subjects. Every Python agent
# consumes through JetStream push consumers and publishes with JetStream acks;
# a Rust agent needs it only if it uses JetStream or request/reply.
INBOX = "_INBOX.>"
RUST_USES_INBOX = re.compile(r"\bjetstream\b|\.request\(")

# Publish calls whose subject is a variable, so the scan cannot read it. Each
# is a pure forwarder: every subject it can carry comes from a literal site
# the scan does see, in a file the same agent runs. Keyed by file and
# enclosing function, not line, so edits elsewhere in the file keep the entry.
# Any other unresolved call is a gap: a subject the audit cannot check.
FORWARDERS: dict[tuple[str, str], str] = {
    (
        "app/cognitive/core.py",
        "publish",
    ): "CognitiveService.publish relays its callers' literal subjects",
    (
        "app/cognitive/core.py",
        "process_event",
    ): 'relays pipeline.py mesh_signal dicts, read as "subject": "literal"',
}


@dataclass
class Grant:
    publish: list[str] = field(default_factory=list)
    subscribe: list[str] = field(default_factory=list)


@dataclass
class Subscription:
    subject: str
    reconciles: bool  # passes ack_wait/max_deliver


@dataclass
class Uses:
    publishes: dict[str, set[str]] = field(default_factory=dict)
    subscribes: dict[str, dict[str, Subscription]] = field(default_factory=dict)
    unowned: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    unresolved_subscribes: list[str] = field(default_factory=list)


def parse_accounts_conf(text: str) -> dict[str, Grant]:
    """Per-user publish/subscribe allow lists from nats-accounts.conf."""
    text = re.sub(r"#[^\n]*", "", text)
    if re.search(r"\bdeny\s*:", text):
        raise SystemExit(
            "check_nats_grants: deny lists are not modelled; extend the check"
        )
    grants: dict[str, Grant] = {}
    users = list(re.finditer(r'user:\s*"([^"]+)"', text))
    for index, match in enumerate(users):
        end = users[index + 1].start() if index + 1 < len(users) else len(text)
        block = text[match.end() : end]
        grant = Grant()
        for kind in ("publish", "subscribe"):
            found = re.search(kind + r":\s*\{\s*allow:\s*\[([^\]]*)\]", block)
            if found:
                getattr(grant, kind).extend(re.findall(r'"([^"]+)"', found.group(1)))
        grants[match.group(1)] = grant
    return grants


def _module_file(name: str) -> Path | None:
    base = BACKEND_ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _static_imports(path: Path) -> set[Path]:
    rel = path.relative_to(BACKEND_ROOT).with_suffix("")
    package = list(rel.parts[:-1])
    found: set[Path] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        for name in names:
            if name == "main" or name.startswith("app"):
                module_file = _module_file(name)
                if module_file is not None:
                    found.add(module_file)
    return found


def _dynamic_imports(entry: str) -> set[Path]:
    """Backend files a real import of `entry` loads (resolves lazy exports)."""
    module = entry.removesuffix(".py").replace("/", ".")
    probe = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps([getattr(m, '__file__', None) for m in list(sys.modules.values())]))\n"
    )
    env = dict(os.environ, CI=os.environ.get("CI", "1"))
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"check_nats_grants: importing {module} failed:\n{result.stderr}"
        )
    files = json.loads(result.stdout.strip().splitlines()[-1])
    return {
        Path(f).resolve()
        for f in files
        if f
        and Path(f).resolve().is_relative_to(BACKEND_ROOT)
        and ".venv" not in Path(f).parts
    }


def import_closure(entry: str) -> set[str]:
    seen: set[Path] = set()
    todo = list(_dynamic_imports(entry) | {(BACKEND_ROOT / entry).resolve()})
    while todo:
        path = todo.pop()
        if path in seen or path.suffix != ".py":
            continue
        seen.add(path)
        todo.extend(_static_imports(path) - seen)
    return {str(path.relative_to(BACKEND_ROOT)) for path in seen}


def _defines_agent(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Name) and base.id == "BaseAgent")
            or (isinstance(base, ast.Attribute) and base.attr == "BaseAgent")
            for base in node.bases
        ):
            return True
    return False


def _owners(rel: str, closures: dict[str, set[str]], injectors: set[str]) -> set[str]:
    """Agents that publish or subscribe through the code in `rel`."""
    for user, prefix in RUST_ENTRIES.items():
        if rel.startswith(prefix):
            return {user}
    own = {user for user, entry in PYTHON_ENTRIES.items() if entry == rel}
    holders = {user for user, closure in closures.items() if rel in closure}
    if _defines_agent(BACKEND_ROOT / rel):
        return holders | own
    return (holders & injectors) | own


def _subject_arg(node: ast.Call, py_topics: dict[str, str]) -> tuple[str | None, bool]:
    """The subject a `.subscribe(...)` call passes, positional or as the
    `subject=` keyword, and whether one was even given. `(None, True)` means
    a subject was passed but this scanner cannot read it (computed, an
    f-string, anything but a literal or `Topics.NAME`); `(None, False)`
    means no subject-shaped argument was found at all, which is not this
    call's business to flag."""
    if node.args:
        arg = node.args[0]
    else:
        keyword = next((kw for kw in node.keywords if kw.arg == "subject"), None)
        if keyword is None:
            return None, False
        arg = keyword.value
    subject = (
        arg.value
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        else _resolve_topics_attribute(arg, py_topics)
    )
    return subject, subject is None


def _python_subscriptions(
    py_topics: dict[str, str],
) -> tuple[dict[str, list[Subscription]], list[str]]:
    """rel path -> subscriptions made there, with whether they reconcile;
    plus file:line of every `.subscribe(` call whose subject this scanner
    could not read (a gap in the audit, not a gap in the grants -- see
    `FORWARDERS` for the publish-side equivalent)."""
    found: dict[str, list[Subscription]] = {}
    unresolved: list[str] = []
    paths = sorted(APP_ROOT.rglob("*.py")) + [BACKEND_ROOT / "main.py"]
    for path in paths:
        if path.name == "base.py" and path.parent.name == "agents":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "subscribe"
            ):
                continue
            subject, unreadable = _subject_arg(node, py_topics)
            if subject is None:
                if unreadable:
                    rel = str(path.relative_to(BACKEND_ROOT))
                    unresolved.append(f"{rel}:{node.lineno}")
                continue
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            found.setdefault(str(path.relative_to(BACKEND_ROOT)), []).append(
                Subscription(subject, bool(keywords & RECONCILE_KWARGS))
            )
    return found, unresolved


def _set_state_files() -> set[str]:
    files = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == "base.py" and path.parent.name == "agents":
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_state"
            ):
                files.add(str(path.relative_to(BACKEND_ROOT)))
                break
    return files


def _enclosing_function(rel: str, line: int) -> str | None:
    """Name of the innermost function containing `line` in `rel`."""
    tree = ast.parse((BACKEND_ROOT / rel).read_text())
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= line <= end and (best is None or node.lineno > best[0]):
                best = (node.lineno, node.name)
    return best[1] if best else None


def find_injectors() -> set[str]:
    """Agents that hand their publish method (or themselves) to shared code."""
    return {
        user
        for user, entry in PYTHON_ENTRIES.items()
        if INJECTS.search((BACKEND_ROOT / entry).read_text())
    }


def collect_uses() -> Uses:
    py_topics = parse_python_topics()
    usage: dict[str, SubjectUsage] = {}
    unresolved_sites: list = []
    scan_python(py_topics, usage, unresolved_sites)
    scan_rust(parse_rust_topics(), usage)

    closures = {user: import_closure(entry) for user, entry in PYTHON_ENTRIES.items()}
    injectors = find_injectors()
    uses = Uses()

    def add_publish(rel: str, subject: str, where: str) -> None:
        owners = _owners(rel, closures, injectors)
        if not owners:
            uses.unowned.append(f"publish {subject} at {where}")
        for user in owners:
            uses.publishes.setdefault(user, set()).add(subject)

    for subject, entry in usage.items():
        for site in entry.publish_sites:
            add_publish(
                site.file.removeprefix("backend/"), subject, f"{site.file}:{site.line}"
            )
        for site in entry.subscribe_sites:  # Rust core-NATS subscribers
            rel = site.file.removeprefix("backend/")
            if not rel.startswith("crates/"):
                continue
            for user in _owners(rel, closures, injectors):
                uses.subscribes.setdefault(user, {})[subject] = Subscription(
                    subject, False
                )
    for rel in _set_state_files():
        add_publish(rel, SET_STATE_SUBJECT, f"{rel} (set_state)")

    for site in unresolved_sites:
        rel = site.file.removeprefix("backend/")
        if (rel, _enclosing_function(rel, site.line)) not in FORWARDERS:
            uses.unresolved.append(f"{rel}:{site.line}")

    subscriptions_by_file, unresolved_subscribes = _python_subscriptions(py_topics)
    uses.unresolved_subscribes.extend(unresolved_subscribes)
    for rel, subscriptions in subscriptions_by_file.items():
        owners = _owners(rel, closures, injectors)
        for sub in subscriptions:
            if not owners:
                uses.unowned.append(f"subscribe {sub.subject} at {rel}")
            for user in owners:
                current = uses.subscribes.setdefault(user, {}).get(sub.subject)
                reconciles = sub.reconciles or (
                    current is not None and current.reconciles
                )
                uses.subscribes[user][sub.subject] = Subscription(
                    sub.subject, reconciles
                )
    for user in PYTHON_ENTRIES:
        for subject in BASE_AGENT_SUBSCRIBES:
            uses.subscribes.setdefault(user, {}).setdefault(
                subject, Subscription(subject, False)
            )
    return uses


def needed_subscribes(user: str, uses: Uses) -> set[str]:
    """Subscribe grants the agent's code needs. Python agents subscribe only
    through JetStream push consumers (BaseAgent.subscribe has no core-NATS
    path), so deliveries arrive on _INBOX and no plain subject is needed.
    Rust agents subscribe over core NATS, one grant per subject."""
    if user in PYTHON_ENTRIES:
        return {INBOX}
    needed = set(uses.subscribes.get(user, {}))
    crate = BACKEND_ROOT / RUST_ENTRIES[user]
    if any(RUST_USES_INBOX.search(f.read_text()) for f in crate.rglob("*.rs")):
        needed.add(INBOX)
    return needed


def _stream_of(subject: str, streams: dict[str, list[str]]) -> str | None:
    for name, patterns in streams.items():
        if any(subject_matches_pattern(subject, pattern) for pattern in patterns):
            return name
    return None


def _covered(subject: str, allow: list[str]) -> bool:
    return any(subject_matches_pattern(subject, pattern) for pattern in allow)


def _needed_for(sub: Subscription, stream: str) -> list[str]:
    needed = [
        "$JS.API.STREAM.NAMES",
        f"$JS.API.CONSUMER.INFO.{stream}.c",
        f"$JS.API.CONSUMER.DURABLE.CREATE.{stream}.c",
        f"$JS.ACK.{stream}.c.1.1.1.1.1",
    ]
    if sub.reconciles:
        needed.append(f"$JS.API.CONSUMER.DELETE.{stream}.c")
    return needed


def find_gaps(uses: Uses | None = None, conf_text: str | None = None) -> list[str]:
    grants = parse_accounts_conf(conf_text or ACCOUNTS_CONF.read_text())
    streams = parse_core_streams()
    uses = uses or collect_uses()
    gaps = [f"unowned site (no agent runs it): {site}" for site in uses.unowned]
    gaps += [
        f"unresolved publish subject (not a known forwarder): {site}"
        for site in uses.unresolved
    ]
    gaps += [
        f"unresolved subscribe subject: {site}" for site in uses.unresolved_subscribes
    ]
    for user in sorted(set(uses.publishes) | set(uses.subscribes)):
        grant = grants.get(user)
        if grant is None:
            gaps.append(f"{user}: no user in nats-accounts.conf")
            continue
        for subject in sorted(uses.publishes.get(user, ())):
            if not _covered(subject, grant.publish):
                gaps.append(f"{user}: publishes {subject}, not in its publish grant")
        if INBOX in needed_subscribes(user, uses) and INBOX not in grant.subscribe:
            gaps.append(f"{user}: needs {INBOX} to receive replies and deliveries")
        for subject, sub in sorted(uses.subscribes.get(user, {}).items()):
            if user in RUST_ENTRIES:
                if not _covered(subject, grant.subscribe):
                    gaps.append(
                        f"{user}: subscribes {subject} over core NATS, not granted"
                    )
                continue
            stream = _stream_of(subject, streams)
            if stream is None:
                gaps.append(f"{user}: subscribes {subject}, which no stream holds")
                continue
            for api in _needed_for(sub, stream):
                if not _covered(api, grant.publish):
                    gaps.append(
                        f"{user}: subscribes {subject} (stream {stream}), missing {api}"
                    )
    return gaps


# One representative subject per JetStream API right, per stream, in the same
# shape _needed_for uses. A grant is excess if it matches any probe the
# agent's code does not need, so `$JS.ACK.>` fails for an agent that consumes
# from one stream, not only a grant that matches nothing needed.
_STREAM_API_PROBES = (
    "$JS.API.STREAM.INFO.{s}",
    "$JS.API.STREAM.CREATE.{s}",
    "$JS.API.STREAM.UPDATE.{s}",
    "$JS.API.STREAM.DELETE.{s}",
    "$JS.API.STREAM.PURGE.{s}",
    "$JS.API.STREAM.MSG.GET.{s}",
    "$JS.API.STREAM.MSG.DELETE.{s}",
    "$JS.API.CONSUMER.INFO.{s}.c",
    "$JS.API.CONSUMER.CREATE.{s}.c",
    "$JS.API.CONSUMER.DURABLE.CREATE.{s}.c",
    "$JS.API.CONSUMER.DELETE.{s}.c",
    "$JS.API.CONSUMER.MSG.NEXT.{s}.c",
    "$JS.API.CONSUMER.NAMES.{s}",
    "$JS.API.CONSUMER.LIST.{s}",
    "$JS.ACK.{s}.c.1.1.1.1.1",
)
_GLOBAL_API_PROBES = (
    "$JS.API.INFO",
    "$JS.API.STREAM.NAMES",
    "$JS.API.STREAM.LIST",
)


def _api_probes(streams: dict[str, list[str]]) -> list[str]:
    return list(_GLOBAL_API_PROBES) + [
        probe.format(s=stream) for stream in streams for probe in _STREAM_API_PROBES
    ]


def find_excess(uses: Uses | None = None, conf_text: str | None = None) -> list[str]:
    """Grants wider than the code needs. Least privilege: any is a failure."""
    grants = parse_accounts_conf(conf_text or ACCOUNTS_CONF.read_text())
    streams = parse_core_streams()
    probes = _api_probes(streams)
    uses = uses or collect_uses()
    excess = []
    for user in sorted(set(PYTHON_ENTRIES) | set(RUST_ENTRIES)):
        grant = grants.get(user)
        if grant is None:
            continue
        published = uses.publishes.get(user, set())
        subscribed = uses.subscribes.get(user, {})
        needed_api = {
            api
            for sub in subscribed.values()
            if (stream := _stream_of(sub.subject, streams))
            for api in _needed_for(sub, stream)
        }
        for pattern in grant.publish:
            if pattern.startswith("$JS."):
                extra = sorted(
                    probe
                    for probe in probes
                    if probe not in needed_api
                    and subject_matches_pattern(probe, pattern)
                )
                if extra:
                    excess.append(
                        f"{user}: publish grant {pattern} also allows {extra[0]}"
                        + (f" (+{len(extra) - 1} more)" if len(extra) > 1 else "")
                    )
                elif not any(subject_matches_pattern(a, pattern) for a in needed_api):
                    excess.append(f"{user}: publish grant {pattern} is never needed")
            elif not any(subject_matches_pattern(s, pattern) for s in published):
                excess.append(
                    f"{user}: may publish {pattern}, which its code never does"
                )
        needed_sub = needed_subscribes(user, uses)
        for pattern in grant.subscribe:
            if pattern == INBOX:
                if INBOX not in needed_sub:
                    excess.append(f"{user}: subscribe grant {INBOX} is never needed")
            elif not any(
                subject_matches_pattern(s, pattern) for s in needed_sub if s != INBOX
            ):
                excess.append(
                    f"{user}: may subscribe {pattern}, which its code never does"
                    " over core NATS"
                )
    return excess


def main() -> int:
    uses = collect_uses()
    gaps = find_gaps(uses)
    excess = find_excess(uses)
    for gap in gaps:
        print(f"FAIL: {gap}")
    for extra in excess:
        print(f"FAIL (excess): {extra}")
    if gaps or excess:
        print(
            f"\n{len(gaps)} grant gap(s), {len(excess)} excess grant(s). "
            "Fix nats-accounts.conf or the code."
        )
        return 1
    print(
        "OK: every agent's NATS grant covers exactly what its code publishes "
        "and consumes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
