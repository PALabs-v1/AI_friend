"""V4 slice 13: no LLM call without a named `ModelRole`.

The gate is an AST walk over `app/`, so a new unrouted call fails here the day
it is added. Implementations under `app/llm/` define the methods and are not
call sites.
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path

import httpx
import pytest

from app.llm.model_roles import ModelRole
from app.llm.ollama_client import OllamaClient
from app.measure_trace import add_listener, remove_listener

APP = Path(__file__).resolve().parents[1] / "app"
LLM_METHODS = {"generate", "generate_stream", "describe_image"}


def _llm_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LLM_METHODS
    ]


def _is_routed(call: ast.Call) -> bool:
    role = next((k.value for k in call.keywords if k.arg == "role"), None)
    return (
        isinstance(role, ast.Attribute)
        and isinstance(role.value, ast.Name)
        and role.value.id == "ModelRole"
        and role.attr in ModelRole.__members__
    )


def _evasions(tree: ast.AST) -> list[str]:
    """Ways to reach an LLM method that the call walk cannot see: binding the
    bound method to a name or passing it to partial, and getattr by string."""
    called = {id(n.func) for n in _llm_calls(tree)}
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in LLM_METHODS
            and id(node) not in called
        ):
            found.append(f"line {node.lineno}: .{node.attr} used without a call")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in LLM_METHODS
        ):
            found.append(f"line {node.lineno}: getattr(..., {node.args[1].value!r})")
    return found


def _imports_real_model_role(tree: ast.AST) -> bool:
    """A local class named ModelRole must not satisfy the gate."""
    return any(
        isinstance(n, ast.ImportFrom)
        and (n.module or "").endswith("llm.model_roles")
        and any(a.name == "ModelRole" and a.asname is None for a in n.names)
        for n in ast.walk(tree)
    )


def _app_trees() -> list[tuple[str, ast.AST]]:
    return [
        (str(path.relative_to(APP.parent)), ast.parse(path.read_text("utf-8")))
        for path in sorted(APP.rglob("*.py"))
        if path.relative_to(APP).parts[0] != "llm"
    ]


def _app_calls() -> list[tuple[str, ast.Call]]:
    return [
        (f"{rel}:{call.lineno}", call)
        for rel, tree in _app_trees()
        for call in _llm_calls(tree)
    ]


def test_the_gate_sees_the_known_call_sites():
    assert len(_app_calls()) >= 15, "the AST walk lost call sites"


def test_every_llm_call_in_app_names_a_role():
    unrouted = [
        f"{where} .{call.func.attr}()"
        for where, call in _app_calls()
        if not _is_routed(call)
    ]
    assert not unrouted, "LLM calls without a ModelRole: " + ", ".join(unrouted)


def test_no_llm_method_is_reached_around_the_gate():
    bad = [f"{rel} {e}" for rel, tree in _app_trees() for e in _evasions(tree)]
    assert not bad, "LLM methods reached without a visible call: " + ", ".join(bad)


def test_files_with_llm_calls_import_the_real_model_role():
    bad = [
        rel
        for rel, tree in _app_trees()
        if _llm_calls(tree) and not _imports_real_model_role(tree)
    ]
    assert not bad, "ModelRole is not imported from app.llm.model_roles in: " + str(bad)


@pytest.mark.parametrize(
    "source",
    [
        "gen = llm.generate\nawait gen('x')",
        "f = functools.partial(llm.generate_stream, prompt='x')",
        "await getattr(llm, 'describe_image')(b64)",
    ],
)
def test_the_gate_flags_evasions(source):
    assert _evasions(ast.parse(source))


def test_the_gate_rejects_a_local_model_role_class():
    tree = ast.parse("class ModelRole:\n    X = 1\nllm.generate('x', role=ModelRole.X)")
    assert not _imports_real_model_role(tree)
    assert _imports_real_model_role(
        ast.parse("from ..llm.model_roles import ModelRole")
    )


@pytest.mark.parametrize(
    "source",
    [
        "async def f(llm):\n    return await llm.generate('x')",
        "def f(llm):\n    return llm.generate_stream(prompt='x', model='m')",
        "async def f(llm):\n    return await llm.describe_image(b64)",
        "async def f(llm):\n    return await llm.generate('x', role='PLANNING')",
        "async def f(llm):\n    return await llm.generate('x', role=ModelRole.NOPE)",
    ],
)
def test_the_gate_rejects_unrouted_calls(source):
    (call,) = _llm_calls(ast.parse(source))
    assert not _is_routed(call)


def test_the_gate_accepts_a_named_role():
    (call,) = _llm_calls(ast.parse("llm.generate('x', role=ModelRole.REALIZATION)"))
    assert _is_routed(call)


@pytest.mark.asyncio
async def test_ollama_records_the_role_in_its_request_trace_and_measure_trace(
    monkeypatch,
):
    from app.config import Config

    monkeypatch.setattr(Config, "MEASURE_TRACE", True)
    seen = []
    listener = lambda component, event, ts, fields: seen.append(
        (component, event, dict(fields))
    )
    add_listener(listener)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "ok"}})

    client = OllamaClient(base_url="http://ollama.test:11434", model="phi4-mini")
    client._client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    try:
        await client.generate("q", role=ModelRole.PLANNING)
        await client.generate("q")
    finally:
        await client.close()
        remove_listener(listener)

    roles = [r["role"] for r in client.request_provenance if r["successful"]]
    assert roles == ["PLANNING", None]
    traced = [f["role"] for c, e, f in seen if (c, e) == ("llm", "role_call")]
    assert traced == ["PLANNING", "UNROUTED"]


def _capture(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "MEASURE_TRACE", True)
    seen: list[dict] = []

    def listener(component, event, ts, fields):
        if (component, event) == ("llm", "role_call"):
            seen.append(dict(fields))

    add_listener(listener)
    return seen, lambda: remove_listener(listener)


async def _drain(stream):
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_name", ["ollama", "anthropic"])
async def test_mock_mode_calls_still_record_their_role(monkeypatch, client_name):
    """A mock-mode run must show the same role events a live run would."""
    from app.config import Config
    from app.llm.anthropic_client import AnthropicClient

    monkeypatch.setattr(Config, "MOCK_LLM_TEXT", True)
    seen, done = _capture(monkeypatch)
    client = (
        OllamaClient(base_url="http://ollama.test:11434", model="m")
        if client_name == "ollama"
        else AnthropicClient(api_key="test-key", model="m")
    )
    try:
        await client.generate("q", role=ModelRole.COMPRESSION)
        await _drain(client.generate_stream("q", role=ModelRole.REALIZATION))
        await client.describe_image("b64", role=ModelRole.INTERPRETATION)
        await client.generate("q")
    finally:
        done()
        await client.close()
    assert [(f["client"], f["role"]) for f in seen] == [
        (client_name, "COMPRESSION"),
        (client_name, "REALIZATION"),
        (client_name, "INTERPRETATION"),
        (client_name, "UNROUTED"),
    ]


@pytest.mark.asyncio
async def test_ollama_stream_and_describe_image_record_their_role(monkeypatch):
    seen, done = _capture(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "ok"}})

    client = OllamaClient(base_url="http://ollama.test:11434", model="m")
    client._client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )
    try:
        await _drain(client.generate_stream("q", role=ModelRole.REALIZATION))
        await client.describe_image("b64", role=ModelRole.INTERPRETATION)
    finally:
        done()
        await client.close()
    assert [f["role"] for f in seen] == ["REALIZATION", "INTERPRETATION"]
    streamed = [r for r in client.request_provenance if r["stream"]]
    assert streamed and all(r["role"] == "REALIZATION" for r in streamed)


@pytest.mark.asyncio
async def test_claude_code_records_the_role_even_when_the_process_fails(monkeypatch):
    from app.llm.claude_code_client import ClaudeCodeClient

    async def popen(*command, **kwargs):
        raise RuntimeError("no executable in this test")

    seen, done = _capture(monkeypatch)
    client = ClaudeCodeClient(model="m", popen=popen)
    try:
        # Failure handling is not under test here, only that the role is
        # recorded before the process is spawned.
        with contextlib.suppress(Exception):
            await client.generate("q", role=ModelRole.PLANNING)
        with contextlib.suppress(Exception):
            await _drain(client.generate_stream("q", role=ModelRole.REALIZATION))
        with contextlib.suppress(Exception):
            await client.generate("q")
    finally:
        done()
        await client.close()
    assert [(f["client"], f["role"]) for f in seen] == [
        ("claude_code", "PLANNING"),
        ("claude_code", "REALIZATION"),
        ("claude_code", "UNROUTED"),
    ]
