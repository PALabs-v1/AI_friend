"""LLM client backed by the local Claude Code command line."""

import asyncio
import json
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator, Callable
from typing import Any

from ..config import Config

logger = logging.getLogger("claude_code_client")

_IGNORED_OPTION_KEYS: set[str] = set()


class ClaudeCodeClient:
    """Run non-interactive Claude Code requests without a hosted SDK."""

    supports_incremental_streaming = True

    def __init__(
        self,
        model: str | None = None,
        *,
        executable: str | None = None,
        timeout: float | None = None,
        popen: Callable[..., Any] | None = None,
    ):
        self.model = model
        self.executable = executable or Config.CLAUDE_CODE_EXECUTABLE
        self.timeout = timeout or Config.CLAUDE_CODE_TIMEOUT_SECONDS
        self._popen = popen
        self._workdir: str | None = None

    def _command(
        self,
        *,
        system: str | None = None,
        model: str | None = None,
        streaming: bool = False,
    ) -> list[str]:
        command = [
            self.executable,
            "--print",
            "--no-session-persistence",
            "--tools",
            "",
            "--permission-prompts",
            "none",
        ]
        if system:
            command.extend(["--system-prompt", system])
        if model or self.model:
            command.extend(["--model", model or self.model or ""])
        if streaming:
            # Verified against the installed CLI: stream-json in print mode is
            # rejected without --verbose ("requires --verbose").
            command.extend(
                [
                    "--output-format",
                    "stream-json",
                    "--include-partial-messages",
                    "--verbose",
                ]
            )
        return command

    def _log_ignored_options(self, options_override: dict[str, Any] | None) -> None:
        if not options_override:
            return
        keys = set(options_override)
        new_keys = keys - _IGNORED_OPTION_KEYS
        if new_keys:
            logger.debug(
                "Claude Code CLI ignores unsupported options_override keys: %s",
                ", ".join(sorted(new_keys)),
            )
            _IGNORED_OPTION_KEYS.update(new_keys)

    def _neutral_cwd(self) -> str:
        """An empty directory to run the CLI from.

        Claude Code reads CLAUDE.md and project settings from its working
        directory. Run from the repo, the model answers under this project's
        rules (it opened a reply with the triage block) and a "workspace not
        trusted" warning reaches stdout. A frontier test tier must see only
        the system prompt and the prompt it is given.
        """
        if self._workdir is None:
            self._workdir = tempfile.mkdtemp(prefix="claude-code-client-")
        return self._workdir

    async def _create_process(self, *args: Any, **kwargs: Any) -> Any:
        popen = self._popen or asyncio.create_subprocess_exec
        kwargs.setdefault("cwd", self._neutral_cwd())
        return await popen(*args, **kwargs)

    def _deadline(self) -> float:
        return asyncio.get_running_loop().time() + self.timeout

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    async def _terminate(self, process: Any) -> None:
        if process.returncode is None:
            process.kill()
            await process.wait()

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
    ) -> str:
        self._log_ignored_options(options_override)
        process = None
        deadline = self._deadline()
        try:
            process = await asyncio.wait_for(
                self._create_process(
                    *self._command(system=system, model=model),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self._remaining(deadline),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self._remaining(deadline)
            )
            if process.returncode != 0:
                logger.warning(
                    "[Claude Code] generate failed with exit code %s: %s",
                    process.returncode,
                    stderr.decode(errors="replace")[-400:],
                )
                return "Error generating response."
            return stdout.decode(errors="replace").strip()
        except (TimeoutError, OSError) as exc:
            if process is not None:
                await self._terminate(process)
            logger.warning("[Claude Code] generate failed: %s", type(exc).__name__)
            return "Error generating response."

    async def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        self._log_ignored_options(options_override)
        process = None
        deadline = self._deadline()
        try:
            process = await asyncio.wait_for(
                self._create_process(
                    *self._command(system=system, model=model, streaming=True),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                ),
                timeout=self._remaining(deadline),
            )
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()

            while True:
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=self._remaining(deadline)
                )
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "stream_event":
                    continue
                detail = event.get("event", {})
                delta = detail.get("delta", {})
                if (
                    detail.get("type") == "content_block_delta"
                    and delta.get("type") == "text_delta"
                    and delta.get("text")
                ):
                    yield delta["text"]

            await asyncio.wait_for(process.wait(), timeout=self._remaining(deadline))
            if process.returncode != 0:
                logger.warning(
                    "[Claude Code] generate_stream failed with exit code %s",
                    process.returncode,
                )
                yield "I'm having trouble thinking right now..."
        except (TimeoutError, OSError) as exc:
            if process is not None:
                await self._terminate(process)
            logger.warning(
                "[Claude Code] generate_stream failed: %s", type(exc).__name__
            )
            yield "I'm having trouble thinking right now..."

    async def describe_image(
        self,
        image_b64: str,
        prompt: str = "What do you see?",
        model: str | None = None,
    ) -> str | None:
        """Return None because this text-only CLI adapter has no image input path."""
        return None

    async def check_health(self) -> bool:
        process = None
        deadline = self._deadline()
        try:
            process = await asyncio.wait_for(
                self._create_process(
                    self.executable,
                    "--version",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                ),
                timeout=self._remaining(deadline),
            )
            await asyncio.wait_for(process.wait(), timeout=self._remaining(deadline))
            return process.returncode == 0
        except (TimeoutError, OSError):
            if process is not None:
                await self._terminate(process)
            return False

    async def close(self) -> None:
        """No persistent subprocesses are kept; remove the neutral directory."""
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None
