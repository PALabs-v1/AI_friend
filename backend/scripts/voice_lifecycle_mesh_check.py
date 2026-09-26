"""Exercise voice playback lifecycle against a running local NATS mesh.

Start NATS, voice-agent, transport-agent, and brain-agent first. Point the
voice-agent at a local GPT-SoVITS compatible PCM stub: the repository's
GPT-SoVITS weights are empty, so this check deliberately measures the mesh
with a deterministic local stub rather than claiming cloned-voice coverage.

The script publishes ordinary chat.output events, then observes lifecycle
messages emitted only after transport has handed frames to LiveKit. It checks
full completion, applied interruption, and that a flush stop leaves the retry
stream alive. It also reports first-lifecycle latency for each utterance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import nats

LIFECYCLE = "audio.playback.lifecycle"
CHAT_OUTPUT = "chat.output"
AUDIO_STOP = "audio.stop"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nats-url", default=os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    endpoint = urlsplit(args.nats_url)
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(endpoint.hostname, endpoint.port or 4222), timeout=1
        )
        writer.close()
        await writer.wait_closed()
        client = await nats.connect(args.nats_url, max_reconnect_attempts=0)
    except Exception as exc:
        raise SystemExit(
            f"Cannot reach NATS at {args.nats_url}; start the local mesh first: {exc}"
        ) from exc
    try:
        await check_completion(client, args.timeout)
        await check_interruption(client, args.timeout)
        await check_flush_retry(client, args.timeout)
    finally:
        await client.drain()


def event_for(turn_id: str, content: str, *, done: bool = False) -> dict[str, Any]:
    return {
        "content": content,
        "done": done,
        "turn_id": turn_id,
        "full_response": content if done else None,
        "latency_metadata": {"source": "voice_lifecycle_mesh_check"},
    }


async def publish(client: Any, subject: str, payload: dict[str, Any]) -> None:
    await client.publish(subject, json.dumps(payload).encode())
    await client.flush(timeout=2)


async def collect_until_terminal(
    sub: Any,
    turn_id: str,
    timeout: float,
    *,
    on_started: Callable[[], Any] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    events: list[dict[str, Any]] = []
    first_audio_at: float | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = await sub.next_msg(timeout=max(0.1, deadline - time.monotonic()))
        body = json.loads(msg.data)
        if body.get("turn_id") != turn_id:
            continue
        if first_audio_at is None and body.get("state") in {"STARTED", "PLAYING"}:
            first_audio_at = time.monotonic()
            if on_started:
                await on_started()
        events.append(body)
        # A flushed INTERRUPTED ends only the rejected self-correction take;
        # the turn's retry is still coming under a new utterance id.
        if body.get("state") in {"COMPLETED", "INTERRUPTED", "FAILED"} and not body.get(
            "flushed"
        ):
            if first_audio_at is None:
                raise AssertionError(
                    f"{turn_id}: terminal arrived without audio start: {events}"
                )
            return events, first_audio_at
    raise TimeoutError(
        f"{turn_id}: no lifecycle terminal before timeout; events={events}"
    )


async def check_completion(client: Any, timeout: float) -> None:
    turn = f"mesh-complete-{uuid.uuid4()}"
    sub = await client.subscribe(LIFECYCLE)
    await client.flush(timeout=2)
    started = time.monotonic()
    await publish(
        client,
        CHAT_OUTPUT,
        event_for(turn, "A short lifecycle completion check.", done=False),
    )
    await publish(client, CHAT_OUTPUT, event_for(turn, "", done=True))
    events, first_audio = await collect_until_terminal(sub, turn, timeout)
    states = [item["state"] for item in events]
    assert states[0] == "STARTED" and states[-1] == "COMPLETED", states
    assert sum(state in {"COMPLETED", "INTERRUPTED", "FAILED"} for state in states) == 1
    print(
        f"completion states={states} first_audio_ms={(first_audio - started) * 1000:.1f}"
    )
    await sub.unsubscribe()


async def check_interruption(client: Any, timeout: float) -> None:
    turn = f"mesh-interrupt-{uuid.uuid4()}"
    sub = await client.subscribe(LIFECYCLE)
    await client.flush(timeout=2)
    await publish(
        client,
        CHAT_OUTPUT,
        event_for(turn, "This utterance is intentionally long. " * 80),
    )

    async def stop() -> None:
        await publish(
            client,
            AUDIO_STOP,
            {"turn_id": turn, "reason": "mesh_check", "flush": False},
        )

    events, _ = await collect_until_terminal(sub, turn, timeout, on_started=stop)
    states = [item["state"] for item in events]
    assert states[0] == "STARTED" and states[-1] == "INTERRUPTED", states
    assert events[-1].get("flushed") is False, events[-1]
    print(f"interruption states={states}")
    await sub.unsubscribe()


async def check_flush_retry(client: Any, timeout: float) -> None:
    turn = f"mesh-flush-retry-{uuid.uuid4()}"
    sub = await client.subscribe(LIFECYCLE)
    await client.flush(timeout=2)
    await publish(
        client,
        CHAT_OUTPUT,
        event_for(turn, "The mistaken first attempt is long. " * 80),
    )

    async def flush_and_retry() -> None:
        await publish(
            client,
            AUDIO_STOP,
            {"turn_id": turn, "reason": "self_correction", "flush": True},
        )
        await publish(
            client, CHAT_OUTPUT, event_for(turn, "Here is the corrected retry.")
        )
        await publish(client, CHAT_OUTPUT, event_for(turn, "", done=True))

    events, _ = await collect_until_terminal(
        sub, turn, timeout, on_started=flush_and_retry
    )
    states = [item["state"] for item in events]
    assert states[-1] == "COMPLETED", f"flush stop aborted retry: {states}"
    flushed = [item for item in events if item.get("flushed")]
    assert [item["state"] for item in flushed] == ["INTERRUPTED"], events
    assert flushed[0]["utterance_id"] != events[-1]["utterance_id"], events
    unflushed_terminals = [
        item
        for item in events
        if item["state"] in {"COMPLETED", "INTERRUPTED", "FAILED"}
        and not item.get("flushed")
    ]
    assert len(unflushed_terminals) == 1, events
    print(f"flush_retry states={states}")
    await sub.unsubscribe()


if __name__ == "__main__":
    asyncio.run(main())
