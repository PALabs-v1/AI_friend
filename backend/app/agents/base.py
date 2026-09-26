import asyncio
import hashlib
import json
import logging
import os
import random
import signal
import time
from typing import Any

import nats
import orjson

from ..errors import AgentError
from ..metrics import SubjectMetrics
from ..utils.background_tasks import spawn_background

logger = logging.getLogger(__name__)


class JetStreamPublishFailed(RuntimeError, AgentError):
    """Raised by `BaseAgent.publish(..., allow_core_fallback=False)` when the
    JetStream publish itself failed. Distinguished from a generic publish
    failure so it can be let through the outer `except Exception` in
    `publish()` instead of being logged-and-swallowed like every other
    publish error -- a caller that opted out of the core-NATS downgrade is
    explicitly asking to know when durable delivery did not happen."""


def install_shutdown_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Make SIGTERM trigger the same graceful-shutdown path SIGINT already
    reaches by accident (#153).

    Worker agent processes run via plain `asyncio.run(main())`, not under a
    framework like uvicorn (which installs its own signal handling for
    main.py's FastAPI server for free). Python's default disposition for
    SIGTERM is to kill the process outright - no exception is raised, so no
    `except`/`finally` block runs, unlike SIGINT, which Python's default
    handler turns into a catchable `KeyboardInterrupt`. Docker/Kubernetes
    send SIGTERM to stop a container, not SIGINT, so every agent's `stop()`
    (which unsubscribes from NATS, closes GraphDB - see L7's in-flight-query
    drain - and cancels background tasks) was unreachable in that exact,
    everyday-deployment scenario.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # add_signal_handler is POSIX-only (e.g. unavailable on Windows'
            # default proactor loop); fall back to the plain signal module.
            signal.signal(sig, lambda *_args: shutdown_event.set())


# M6: base/cap for exponential backoff with jitter on NATS reconnect. A static
# `reconnect_time_wait` means every agent process in the mesh (brain, system,
# subconscious, surfacing, transport) retries at the same fixed interval, so a
# NATS restart makes them all hammer it in lockstep on every attempt rather
# than spreading out.
_RECONNECT_BASE_DELAY_SECONDS = 1.0
_RECONNECT_MAX_DELAY_SECONDS = 30.0


def _reconnect_delay_with_backoff(servers, _server_info):
    """`reconnect_to_server_handler` callback for `nats.connect`.

    Must return `(selected_server_or_None, delay_seconds)`; `None` keeps the
    client's own default server selection (there is only ever one configured
    server here) and we only take over the delay computation.
    """
    attempt = servers[0].reconnects if servers else 0
    delay = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * (2**attempt),
    ) + random.uniform(0, 1)  # nosec B311 - reconnect backoff jitter, not cryptographic
    return None, delay


class BaseAgent:
    """
    The blueprint for all AI Friend Micro-Agents.
    Communicates via NATS JetStream.
    """

    def __init__(self, name: str, nats_url: str | None = None):
        self.name = name
        self.nats_url = nats_url or os.getenv("NATS_URL", "nats://127.0.0.1:4222")
        # Each process has one scoped NATS identity supplied by Compose. The
        # credentials are mandatory so a missing .env value cannot silently
        # downgrade an authenticated mesh to an anonymous connection.
        self.nats_user = os.getenv("NATS_USER")
        self.nats_password = os.getenv("NATS_PASSWORD")
        # Any, not a nats-py type: self.js._jsm is accessed directly below,
        # which isn't in JetStreamContext's public stub either.
        self.nc: Any = None
        self.js: Any = None
        tracked_subjects_raw = os.getenv(
            "MESH_OBSERVED_SUBJECTS",
            "system.tick,memory.surfaced,audio.stop,audio.resume,chat.output",
        )
        self._tracked_subjects = {
            subject.strip()
            for subject in tracked_subjects_raw.split(",")
            if subject.strip()
        }
        self._metrics_log_every = max(
            1, int(os.getenv("SUBJECT_METRICS_LOG_EVERY", "25"))
        )
        # P3-2: shared implementation (percentiles, jitter, off-thread
        # aggregation) instead of a hand-rolled dict -- see app/metrics.py.
        self._metrics = SubjectMetrics(
            tracked_subjects=self._tracked_subjects,
            log_every=self._metrics_log_every,
            tag="BaseAgent",
        )
        # P4-8: strong-reference holder for fire-and-forget tasks spawned via
        # self.spawn(); see app/utils/background_tasks.py for why this exists.
        self._background_tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        """Fire `coro` off as a background task without losing it to GC.
        Prefer this over a bare `asyncio.create_task(...)` whose result is
        discarded -- see `app/utils/background_tasks.py`."""
        return spawn_background(self._background_tasks, coro)

    async def _on_nats_disconnected(self):
        logger.warning(
            f"⚠️ Agent '{self.name}' NATS connection disconnected! Attempting automatic recovery..."
        )

    async def _on_nats_reconnected(self):
        logger.info(
            f"✅ Agent '{self.name}' NATS connection successfully re-established."
        )

    async def _on_nats_error(self, err):
        logger.error(f"❌ Agent '{self.name}' NATS connection error: {err}")

    async def _on_nats_closed(self):
        logger.info(f"🔌 Agent '{self.name}' NATS connection closed.")

    async def connect(self):
        """Connect to the NATS Mesh and bootstrap streams."""
        try:
            if not self.nats_user or not self.nats_password:
                raise RuntimeError(
                    f"Agent '{self.name}' requires NATS_USER and NATS_PASSWORD"
                )
            # Connect with infinite auto-reconnection parameters for maximum reliability
            connect_kwargs = {
                "connect_timeout": 10,
                "max_reconnect_attempts": -1,
                "reconnect_to_server_handler": _reconnect_delay_with_backoff,
                "disconnected_cb": self._on_nats_disconnected,
                "reconnected_cb": self._on_nats_reconnected,
                "error_cb": self._on_nats_error,
                "closed_cb": self._on_nats_closed,
            }
            connect_kwargs["user"] = self.nats_user
            connect_kwargs["password"] = self.nats_password
            self.nc = await nats.connect(self.nats_url, **connect_kwargs)
            self.js = self.nc.jetstream()
            # Runtime identities intentionally lack stream-administration
            # rights; the authenticated provisioner prepares streams first.
            logger.info(
                "Agent '%s' connected with scoped NATS credentials; skipping stream administration.",
                self.name,
            )
            logger.info(f"Agent '{self.name}' connected to mesh at {self.nats_url}")

            # Auto-subscribe active agents to cache synchronization broadcasts.
            # P3-8: deliver_policy="new", matching every other liveness/control
            # signal in the mesh (see vision/agent.py's chat.input/chat.output
            # subscriptions for the same reasoning). Under the default "all" a
            # freshly (re)started agent -- or one recovering from a deleted
            # durable -- replays the subject's ENTIRE retained history before
            # it sees anything current. An invalidation broadcast from an hour
            # ago says nothing about whether a local cache is stale *now*; it
            # only wastes a startup invalidating caches that were never built
            # yet, and does so once per cache.sync ever published, on every
            # restart, forever, because the stream default retains it all.
            if self.name != "test_publisher" and not self.name.startswith("test_"):
                try:
                    await self.subscribe(
                        "cache.sync",
                        self._on_cache_sync_received,
                        deliver_policy="new",
                    )
                except Exception as se:
                    logger.debug("Cache sync auto-subscribe skipped: %s", se)
        except Exception as e:
            logger.error(f"Failed to connect agent '{self.name}': {e}")
            raise

    async def _on_cache_sync_received(self, data: dict[str, Any]):
        """Receives cross-process cache invalidation broadcast signals."""
        try:
            store_name = data.get("store")
            action = data.get("action")
            if store_name == "identity_core" and action == "invalidate":
                logger.info(
                    f"🔄 [CacheSync] Invalidation signal received for {store_name}. Reloading local caches."
                )
                from ..state.identity_core_store import IdentityCoreStore

                IdentityCoreStore.invalidate_all_local_caches()
        except Exception as e:
            logger.warning(f"Error handling cache sync: {e}")

    async def _bootstrap_mesh(self):
        """Ensure core streams exist on the mesh (AI Friend Hardened)."""
        from ..nats_streams import (
            CORE_STREAMS,
            StreamReconciliationError,
            build_stream_config,
        )

        core_streams = {name: list(subjects) for name, subjects in CORE_STREAMS.items()}

        try:
            # Modern nats-py pattern
            jsm = self.nc.jsm()
        except Exception as e:
            logger.warning(
                f"NATS Management not available: {e}. Ensure streams are pre-configured."
            )
            return

        for stream_name, subjects in core_streams.items():
            try:
                # P1-2: the same config the bootstrap script uses. This path
                # runs on every agent start and usually reaches a fresh mesh
                # first, so if it declared streams with name+subjects only
                # the retention policy would never be applied in practice.
                await jsm.add_stream(config=build_stream_config(stream_name, subjects))
                logger.info(f"Created NATS Stream: {stream_name} {subjects}")
            except nats.js.errors.BadRequestError:
                # Stream likely already exists, verify subjects.
                # P4-11b: reconcile_existing_stream both applies P1-2's
                # retention policy to a pre-existing stream and retries
                # against a concurrent writer -- up to six agent processes
                # (plus the bootstrap script) can all reach this branch for
                # the same stream at startup, and JetStream's STREAM.UPDATE
                # has no compare-and-set, so a naive read-modify-write here
                # could silently drop another agent's subject addition.
                try:
                    from ..nats_streams import reconcile_existing_stream

                    if await reconcile_existing_stream(jsm, stream_name, subjects):
                        logger.info(
                            f"✅ Stream '{stream_name}' synchronized successfully."
                        )
                except Exception as update_err:
                    logger.error(
                        "Stream reconciliation failed for %s: %s",
                        stream_name,
                        update_err,
                    )
                    raise
            except StreamReconciliationError:
                raise
            except Exception as e:
                logger.debug("Stream bootstrap note: %s", e)

    async def publish(
        self,
        subject: str,
        data: Any,
        metadata: dict[str, Any] | None = None,
        *,
        allow_core_fallback: bool = True,
    ):
        """Publish an event to the mesh with latency tracking and binary support.

        P3-5: a JetStream publish failure has always fallen through to core
        NATS -- best-effort, no durability, no replay. That is the right
        default for most subjects (the mesh should stay up over a transient
        JetStream hiccup), but it is a real downgrade and was previously only
        a `warning` log line, indistinguishable from routine noise. Set
        ``allow_core_fallback=False`` for a subject where silent best-effort
        delivery is wrong -- the failure then propagates to the caller instead
        of being swallowed as a successful publish. The downgrade is also
        counted through the same subject-metrics path as everything else, so
        it is visible in aggregate, not just in a log line someone has to be
        watching for.
        """
        if not self.js:
            await self.connect()

        # Resolve Enum subjects to their string values (e.g. Topics.CHAT_OUTPUT -> "chat.output")
        from enum import Enum

        if isinstance(subject, Enum):
            subject = subject.value

        from ..config import Config

        # 1. Prepare Metadata
        if metadata:
            meta = dict(metadata)
            meta.setdefault("start_time", time.time())
            meta.setdefault("hops", [])
            meta.setdefault("source", self.name)
        else:
            meta = {"start_time": time.time(), "hops": [], "source": self.name}

        # Propagate existing meta if present in dict data
        if isinstance(data, dict) and "latency_metadata" in data:
            meta = dict(data["latency_metadata"] or {})
            meta.setdefault("start_time", time.time())
            meta.setdefault("hops", [])
            meta.setdefault("source", self.name)

        meta["hops"].append(
            {"agent": self.name, "subject": subject, "timestamp": time.time()}
        )

        # 2. Determine Transport Mode
        is_binary = subject in getattr(Config, "BINARY_SUBJECTS", [])

        try:
            if is_binary and isinstance(data, (bytes, bytearray)):
                # Direct Binary Transport with Headers
                headers = {
                    "X-Latency-Meta": orjson.dumps(meta).decode(),
                    "X-Payload-Format": "binary/raw-pcm",
                }
                try:
                    await self.js.publish(subject, data, headers=headers, timeout=10)
                except Exception as js_err:
                    if not allow_core_fallback:
                        raise JetStreamPublishFailed(str(js_err)) from js_err
                    raise
            else:
                # Standard JSON Transport
                if isinstance(data, dict):
                    data["latency_metadata"] = meta
                payload = orjson.dumps(data)

                # Use JetStream with explicit timeout and core NATS fallback
                try:
                    await self.js.publish(subject, payload, timeout=10)
                except Exception as js_err:
                    if not allow_core_fallback:
                        raise JetStreamPublishFailed(str(js_err)) from js_err
                    logger.warning(
                        f"JetStream publish to {subject} failed ({js_err}), falling back to core NATS"
                    )
                    self._record_subject_metric(subject, direction="downgrade")
                    await self.nc.publish(subject, payload)

            self._record_subject_metric(
                subject,
                direction="publish",
                latency_ms=self._extract_latency_ms(meta),
            )

            logger.debug(
                f"Agent '{self.name}' published to {subject} ({'binary' if is_binary else 'json'})"
            )
        except JetStreamPublishFailed:
            # Let it through: `allow_core_fallback=False` means the caller
            # wants to know durable delivery failed, not have it logged as
            # routine and treated as a successful publish.
            raise
        except Exception as e:
            logger.error(f"Failed to publish to {subject}: {e}")

    def _extract_latency_ms(self, metadata: dict[str, Any] | None) -> float | None:
        if not metadata:
            return None
        start_time = metadata.get("start_time")
        if start_time is None:
            return None
        try:
            return max(0.0, (time.time() - float(start_time)) * 1000)
        except (TypeError, ValueError):
            return None

    def _record_subject_metric(
        self,
        subject: str,
        direction: str,
        latency_ms: float | None = None,
    ):
        self._metrics.record(subject, direction=direction, latency_ms=latency_ms)

    async def _ack_heartbeat(self, msg, interval: float = 15.0):
        """Periodically signal JetStream that a long callback is still working.

        Resets the consumer AckWait timer so long cognitive turns are not
        redelivered mid-flight (A1). Cancelled by the handler once the callback
        returns.
        """
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await msg.in_progress()
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    async def _reconcile_consumer_config(
        self,
        subject: str,
        durable: str,
        ack_wait: float | None,
        max_deliver: int | None,
    ) -> None:
        """Delete a durable consumer whose stored config no longer matches
        what the caller is asking for, so it gets recreated with the new one.

        P1-1: without this, sizing `ack_wait` is a no-op on every deployment
        that has run before. `JetStreamContext.subscribe` looks up an
        existing durable and, on a hit, does `config = consumer_info.config`
        -- it *discards* the ConsumerConfig passed in and adopts whatever the
        server already stored. nats-py marks the spot itself with
        `# TODO: Detect configuration drift with any present durable
        consumer.` So the new ack deadline would apply on a fresh mesh (and
        in every test), and silently not apply anywhere that matters, while
        still logging a successful subscribe.

        That is precisely the failure shape this audit keeps finding: the
        half that fails still compiles, still passes its own tests, and still
        logs as though it worked. Detect the drift explicitly rather than
        trusting the client to.

        Deleting a durable consumer discards its delivery cursor, so the
        recreated one starts from `deliver_policy` rather than resuming.
        That is acceptable for the control tier this is used on -- ticks are
        periodic and a missed one is picked up by the next -- but it is the
        reason this only runs when an explicit config was requested, never
        by default.
        """
        try:
            stream = await self.js.find_stream_name_by_subject(subject)
            info = await self.js._jsm.consumer_info(stream, durable)
        except Exception:
            # No such consumer (the normal first-run case), or JetStream is
            # not answering yet -- either way there is nothing to reconcile
            # and the subscribe below will create or retry as appropriate.
            return

        current = info.config
        drifted = (ack_wait is not None and current.ack_wait != ack_wait) or (
            max_deliver is not None and current.max_deliver != max_deliver
        )
        if not drifted:
            return

        logger.warning(
            "Agent '%s': durable '%s' on %s has drifted config "
            "(ack_wait=%s->%s, max_deliver=%s->%s). Deleting so it is "
            "recreated with the requested settings.",
            self.name,
            durable,
            subject,
            current.ack_wait,
            ack_wait,
            current.max_deliver,
            max_deliver,
        )
        try:
            await self.js._jsm.delete_consumer(stream, durable)
        except Exception as e:
            # Not fatal: the subscribe still succeeds, just with the old
            # config. Loud, because the requested deadline is not in force.
            logger.error(
                "Agent '%s': could not delete drifted durable '%s': %s. "
                "Subscription will use the STALE stored config.",
                self.name,
                durable,
                e,
            )

    async def subscribe(
        self,
        subject: str,
        callback,
        durable: str | None = None,
        deliver_policy: str = "all",
        pending_msgs_limit: int | None = None,
        pending_bytes_limit: int | None = None,
        ack_wait: float | None = None,
        max_deliver: int | None = None,
    ):
        """
        Subscribe to events on the mesh with header validation and fallback.

        P1-1: `ack_wait`/`max_deliver` let a caller size the JetStream
        consumer explicitly instead of inheriting server defaults (an
        unbounded 30s ack_wait with unlimited max_deliver, mesh-wide). Passed
        straight through as a `ConsumerConfig`. Note `MESH_MAX_DELIVER`
        (below, in `_handler`'s except branch) is a *different*, client-side
        mechanism -- it counts deliveries after the fact to drop a poison
        message, it does not configure the server's own redelivery limit.
        The two are complementary, not redundant: this one bounds how many
        times JetStream will attempt redelivery at all; that one decides what
        happens once redelivery has already happened `MESH_MAX_DELIVER`
        times.
        """
        if not self.js:
            await self.connect()

        async def _handler(msg):
            # A1: Long-running cognitive turns (chat.*) can exceed JetStream's
            # default AckWait, causing mid-generation redelivery and duplicate
            # turns. Keep the message "in progress" until the callback returns.
            hb_task = None
            if subject.startswith("chat.") and hasattr(msg, "in_progress"):
                hb_task = asyncio.create_task(self._ack_heartbeat(msg))
            try:
                # 1. Check for Binary Payload + Headers
                if msg.headers and "X-Latency-Meta" in msg.headers:
                    try:
                        meta = json.loads(msg.headers["X-Latency-Meta"])
                        self._record_subject_metric(
                            subject,
                            direction="consume",
                            latency_ms=self._extract_latency_ms(meta),
                        )
                        # Return binary data as-is if it's the target format
                        # or wrap it if the agent expects a dict with meta
                        await callback(msg.data, metadata=meta)
                    except Exception as he:
                        logger.warning(
                            f"Header validation failed on {subject}: {he}. Using fallback."
                        )
                        await callback(msg.data)
                else:
                    # 2. Standard JSON Fallback
                    data = json.loads(msg.data.decode())
                    if isinstance(data, dict):
                        self._record_subject_metric(
                            subject,
                            direction="consume",
                            latency_ms=self._extract_latency_ms(
                                data.get("latency_metadata")
                            ),
                        )
                    await callback(data)

                if hb_task:
                    hb_task.cancel()
                    hb_task = None
                await msg.ack()
            except Exception as e:
                logger.error(f"Subscription handler error on {subject}: {e}")
                # P3-5: every subject now gets bounded redelivery and an
                # explicit dead-letter, not just chat./state.. The original
                # split -- "auto-ACK fast-moving media, NACK critical
                # state/chat flows" -- meant a handler exception on any other
                # subject (audio.*, vision.*, memory.*, ...) acked and
                # silently discarded the message on its *first* failure, with
                # no redelivery and no record beyond a log line. That is
                # exactly the failure shape this audit keeps finding: the
                # half that fails still compiles, still passes its own tests,
                # and still looks like it worked.
                #
                # The media/control tier keeps a materially smaller
                # redelivery budget than chat./state. -- a poison frame on a
                # hot audio path should not sit in redelivery as long as a
                # poison chat message legitimately can -- but it is bounded
                # and dead-lettered rather than unconditionally discarded on
                # attempt one. A3's original reasoning (bound redelivery so a
                # persistently malformed payload cannot spin forever) applies
                # to every subject, not only two prefixes.
                is_conversational = subject.startswith(("chat.", "state."))
                max_deliver_env = (
                    "MESH_MAX_DELIVER"
                    if is_conversational
                    else "MESH_MEDIA_MAX_DELIVER"
                )
                max_deliver_default = "5" if is_conversational else "2"
                max_deliver = max(
                    1, int(os.getenv(max_deliver_env, max_deliver_default))
                )
                num_delivered = None
                try:
                    num_delivered = msg.metadata.num_delivered
                except Exception:
                    num_delivered = None
                if num_delivered is not None and num_delivered >= max_deliver:
                    payload_digest = hashlib.sha256(msg.data).hexdigest()
                    logger.error(
                        "[DeadLetter] Dropping poison message on %s after %s "
                        "deliveries: %s | payload_bytes=%s | payload_sha256=%s",
                        subject,
                        num_delivered,
                        e,
                        len(msg.data),
                        payload_digest,
                    )
                    if hasattr(msg, "term"):
                        await msg.term()
                    else:
                        await msg.ack()
                else:
                    await msg.nak()
            finally:
                if hb_task:
                    hb_task.cancel()

        # 3. Durable Management
        # Generate a unique durable name based on the subject if not provided
        if not durable:
            # Replace dots with underscores for NATS-friendly durable name
            subject_suffix = (
                subject.replace(".", "_").replace(">", "all").replace("*", "any")
            )
            durable = f"{self.name}_{subject_suffix}"

        # Mapping string policy to nats.js.api.DeliverPolicy
        from nats.js.api import ConsumerConfig, DeliverPolicy

        policy_map = {
            "all": DeliverPolicy.ALL,
            "new": DeliverPolicy.NEW,
            "last": DeliverPolicy.LAST,
        }
        policy = policy_map.get(deliver_policy, DeliverPolicy.ALL)

        subscribe_kwargs = {
            "cb": _handler,
            "durable": durable,
            "deliver_policy": policy,
        }
        if pending_msgs_limit is not None:
            subscribe_kwargs["pending_msgs_limit"] = pending_msgs_limit
        if pending_bytes_limit is not None:
            subscribe_kwargs["pending_bytes_limit"] = pending_bytes_limit
        if ack_wait is not None or max_deliver is not None:
            # `deliver_policy` above still wins: the client library applies
            # the top-level `deliver_policy` kwarg on top of this `config`
            # unconditionally, so the two do not fight over that field.
            subscribe_kwargs["config"] = ConsumerConfig(
                ack_wait=ack_wait, max_deliver=max_deliver
            )
            await self._reconcile_consumer_config(
                subject=subject,
                durable=durable,
                ack_wait=ack_wait,
                max_deliver=max_deliver,
            )

        # 4. Reliable Subscription (Retry for JetStream availability)
        max_retries = 10
        retry_delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                await self.js.subscribe(subject, **subscribe_kwargs)
                logger.info(
                    f"Agent '{self.name}' subscribed to {subject} with durable '{durable}' and policy '{deliver_policy}'"
                )
                return
            except Exception as e:
                # nats.js.errors.NotFoundError: happens when the subject is not mapped to any stream
                from nats.js.errors import NotFoundError

                if isinstance(e, NotFoundError) and attempt < max_retries:
                    logger.warning(
                        f"Agent '{self.name}' subscription to {subject} failed: stream not found (attempt {attempt}/{max_retries}). Retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.error(
                        f"Agent '{self.name}' failed to subscribe to {subject}: {e}"
                    )
                    raise

    async def set_state(self, state: str):
        """Broadcast agent state to the mesh (e.g., 'thinking', 'speaking', 'idle')"""
        await self.publish(
            "state.update",
            {"agent": self.name, "state": state, "timestamp": time.time()},
        )
        logger.debug("Agent '%s' state set to: %s", self.name, state)

    async def _prepare_stop(self) -> None:
        """Cancel retained tasks before subclasses close their resources."""
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def stop(self):
        """Shutdown the agent after unwinding retained background work."""
        await self._prepare_stop()
        # P3-4: deferred from Cluster 2 (P3-2/telemetry) -- self._metrics'
        # background aggregation thread was never stopped anywhere, on any
        # agent, since every agent inherits this method. Harmless in a
        # container about to exit, genuinely wrong in a test process or
        # anything that restarts an agent in-process. `shutdown()` is
        # synchronous (a plain `threading.Thread.join`), and briefly
        # blocking the loop here is the shutdown path, not a hot one.
        self._metrics.shutdown()
        if self.nc:
            await self.nc.drain()
            logger.info(f"Agent '{self.name}' shut down.")
