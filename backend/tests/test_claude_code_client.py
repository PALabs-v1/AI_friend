import asyncio
import json

import pytest

from app.llm.claude_code_client import ClaudeCodeClient


def _delta(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                },
            }
        ).encode()
        + b"\n"
    )


class FakeStdin:
    def write(self, value):
        self.value = value

    async def drain(self):
        return None

    def close(self):
        return None


class FakeStdout:
    def __init__(self, lines):
        self.lines = iter(lines)

    async def readline(self):
        return next(self.lines, b"")


class FakeProcess:
    def __init__(self, *, stdout=b"answer", stderr=b"", returncode=0, lines=()):
        self.stdout_bytes = stdout
        self.stderr_bytes = stderr
        self.returncode = returncode
        self.stdout = FakeStdout(lines)
        self.stdin = FakeStdin()
        self.killed = False

    async def communicate(self, input=None):
        self.input = input
        return self.stdout_bytes, self.stderr_bytes

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class QueueStdout:
    def __init__(self, lines, finished):
        self.lines = asyncio.Queue()
        for line in lines:
            self.lines.put_nowait(line)
        self.finished = finished

    async def readline(self):
        return await self.lines.get()


class StreamingProcess(FakeProcess):
    def __init__(self, lines, *, auto_eof=True):
        super().__init__(lines=())
        self.finished = asyncio.Event()
        queued = [*(_delta(line) for line in lines)]
        if auto_eof:
            queued.append(b"")
            self.finished.set()
        self.stdout = QueueStdout(queued, self.finished)
        self.wait_started = False

    async def wait(self):
        self.wait_started = True
        await self.finished.wait()
        return self.returncode


class FakeOllamaLikeClient:
    async def generate(self, *args, **kwargs):
        return "answer"

    async def generate_stream(self, *args, **kwargs):
        yield "first "
        yield "second"

    async def describe_image(self, *args, **kwargs):
        return None

    async def check_health(self):
        return True

    async def close(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama_like", "claude_code"])
async def test_llm_client_generation_contract(kind):
    processes = []

    async def popen(*command, **kwargs):
        if "stream-json" in command:
            process = StreamingProcess(["first ", "second"])
        else:
            process = FakeProcess(stdout=b"answer")
        processes.append(process)
        return process

    client = (
        FakeOllamaLikeClient()
        if kind == "ollama_like"
        else ClaudeCodeClient(popen=popen)
    )
    assert await client.generate("prompt") == "answer"
    assert [chunk async for chunk in client.generate_stream("prompt")] == [
        "first ",
        "second",
    ]
    assert await client.describe_image("base64") is None
    assert await client.check_health() is True
    await client.close()


@pytest.mark.asyncio
async def test_stream_yields_before_process_exits():
    process = StreamingProcess(["early"], auto_eof=False)
    client = ClaudeCodeClient(popen=lambda *args, **kwargs: _return(process))
    stream = client.generate_stream("prompt")

    assert await asyncio.wait_for(anext(stream), timeout=0.2) == "early"
    assert process.wait_started is False
    process.stdout.lines.put_nowait(_delta("late"))
    process.stdout.lines.put_nowait(b"")
    process.finished.set()
    assert await anext(stream) == "late"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_health_reports_missing_or_failed_cli_as_false():
    unavailable = ClaudeCodeClient(
        popen=lambda *args, **kwargs: _return(FakeProcess(returncode=1))
    )
    missing = ClaudeCodeClient(popen=lambda *args, **kwargs: _raise_file_not_found())
    assert await unavailable.check_health() is False
    assert await missing.check_health() is False


async def _raise_file_not_found():
    raise FileNotFoundError("claude")


@pytest.mark.asyncio
async def test_generate_timeout_kills_process_and_returns_standard_fallback():
    class SlowProcess(FakeProcess):
        def __init__(self):
            super().__init__(returncode=None)

        async def communicate(self, input=None):
            await asyncio.sleep(1)
            return b"", b""

    process = SlowProcess()
    client = ClaudeCodeClient(
        timeout=0.1, popen=lambda *args, **kwargs: _return(process)
    )
    assert await client.generate("prompt") == "Error generating response."
    assert process.killed is True


@pytest.mark.asyncio
async def test_generate_timeout_is_a_total_deadline_across_process_stages():
    class SlowProcess(FakeProcess):
        def __init__(self):
            super().__init__(returncode=None)

        async def communicate(self, input=None):
            await asyncio.sleep(0.13)
            return b"", b""

    process = SlowProcess()

    async def slow_popen(*args, **kwargs):
        await asyncio.sleep(0.13)
        return process

    client = ClaudeCodeClient(timeout=0.2, popen=slow_popen)
    assert await client.generate("prompt") == "Error generating response."
    assert process.killed is True


@pytest.mark.asyncio
async def test_nonzero_exit_returns_standard_fallback():
    process = FakeProcess(stderr=b"failure", returncode=2)
    client = ClaudeCodeClient(popen=lambda *args, **kwargs: _return(process))
    assert await client.generate("prompt") == "Error generating response."


@pytest.mark.asyncio
async def test_stream_nonzero_exit_yields_standard_fallback():
    process = StreamingProcess([], auto_eof=True)
    process.returncode = 2
    client = ClaudeCodeClient(popen=lambda *args, **kwargs: _return(process))
    assert [chunk async for chunk in client.generate_stream("prompt")] == [
        "I'm having trouble thinking right now..."
    ]


@pytest.mark.asyncio
async def test_stream_timeout_kills_process_and_yields_standard_fallback():
    class StallingStdout:
        async def readline(self):
            await asyncio.Event().wait()

    process = FakeProcess(returncode=None)
    process.stdout = StallingStdout()
    client = ClaudeCodeClient(
        timeout=0.1, popen=lambda *args, **kwargs: _return(process)
    )
    assert [chunk async for chunk in client.generate_stream("prompt")] == [
        "I'm having trouble thinking right now..."
    ]
    assert process.killed is True


@pytest.mark.asyncio
async def test_unsupported_options_are_logged_once(caplog):
    client = ClaudeCodeClient(popen=lambda *args, **kwargs: _return(FakeProcess()))
    caplog.set_level("DEBUG", logger="claude_code_client")
    await client.generate("prompt", options_override={"temperature": 0.2})
    await client.generate("prompt", options_override={"temperature": 0.2})
    messages = [
        record.message
        for record in caplog.records
        if "ignores unsupported" in record.message
    ]
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_command_uses_only_documented_flags_and_forwards_system_model():
    client = ClaudeCodeClient(model="default-model")
    command = client._command(
        system="system text", model="chosen-model", streaming=True
    )
    assert command[:6] == [
        "claude",
        "--print",
        "--no-session-persistence",
        "--tools",
        "",
        "--permission-prompts",
    ]
    assert "none" in command
    assert command[command.index("--system-prompt") + 1] == "system text"
    assert command[command.index("--model") + 1] == "chosen-model"
    assert command[-4:] == [
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    # Non-streaming calls must not carry the streaming-only flags.
    plain = client._command(system=None, model=None, streaming=False)
    assert "--verbose" not in plain and "--output-format" not in plain


@pytest.mark.asyncio
async def test_cli_runs_from_an_empty_neutral_directory_and_close_removes_it():
    """Run from the repo, Claude Code loads CLAUDE.md and project settings into
    the model's context; a test tier must not inherit them."""
    import os
    from pathlib import Path

    seen = {}

    async def popen(*args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return FakeProcess(stdout=b"hi")

    client = ClaudeCodeClient(popen=popen)
    assert await client.generate("x") == "hi"
    cwd = seen["cwd"]
    assert cwd and os.path.isdir(cwd) and os.listdir(cwd) == []
    repo_root = Path(__file__).resolve().parents[2]
    assert repo_root not in Path(cwd).resolve().parents
    await client.close()
    assert not os.path.exists(cwd)
