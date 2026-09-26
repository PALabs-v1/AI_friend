import asyncio
import logging
import os
from collections.abc import Sequence
from typing import cast

import nats
from nats.errors import NoRespondersError
from nats.js.errors import BadRequestError, ServiceUnavailableError

from .errors import AgentError

logger = logging.getLogger("nats_streams")


class StreamReconciliationError(RuntimeError, AgentError):
    """Raised when concurrent stream writers prevent convergence."""


# P1-2: the retention policy each stream is created with. Kept SEPARATE
# from CORE_STREAMS deliberately -- scripts/check_subject_wiring.py parses
# CORE_STREAMS as an annotated dict-of-lists to cross-reference every
# publish/subscribe subject against a declared stream pattern (P1-8), so
# changing that literal's shape would silently break the CI wiring check.
#
# Both streams previously inherited JetStream's defaults: limits retention,
# FILE storage, and unlimited count/bytes/age. NATS' own docs warn that "an
# unbounded stream will eventually fill the disk", and AI_AUDIO carries raw
# PCM (ESTIMATED ~130 KB/s; actual growth NOT TESTED). Nobody chose these
# values -- NATS simply permits a two-field declaration, so the policy
# decision was never made.
#
# The tiers follow ARCHITECTURE.md §28. Note the *control* tier's
# distinguishing settings -- ack_wait and a bounded max_deliver -- are
# CONSUMER settings, not stream settings, and landed with P1-1 at the
# subscription site (subconscious_agent's system.tick). Splitting system.>
# into its own stream would buy only a different max_age for very small
# messages, and would cost a destructive subject migration out of
# AI_MESSAGES. Deferred deliberately; see the ledger.
_MINUTE = 60.0
_DAY = 24 * 60 * _MINUTE

STREAM_POLICIES: dict[str, dict[str, object]] = {
    # Conversational tier: durable, bounded. Long enough that a restart
    # replays real context, short enough to stay bounded.
    "AI_MESSAGES": {
        "storage": "file",
        "max_age": float(os.getenv("NATS_MESSAGES_MAX_AGE_S") or 7 * _DAY),
        "max_bytes": int(os.getenv("NATS_MESSAGES_MAX_BYTES") or 1 * 1024**3),
    },
    # Sensor tier: raw PCM. MEMORY-backed, which also takes a durable disk
    # write off the audio hot path, and aged out in minutes -- audio frames
    # have no value once the utterance they belong to is transcribed.
    # Losing audio.> durability is correct for this data class but is a
    # stated decision, not an accident.
    "AI_AUDIO": {
        "storage": "memory",
        "max_age": float(os.getenv("NATS_AUDIO_MAX_AGE_S") or 5 * _MINUTE),
        "max_bytes": int(os.getenv("NATS_AUDIO_MAX_BYTES") or 256 * 1024**2),
    },
}


CORE_STREAMS: dict[str, Sequence[str]] = {
    "AI_MESSAGES": [
        "chat.>",
        "vision.>",
        "state.>",
        "agent.>",
        "cmd.>",
        "voice.>",
        "system.>",
        "memory.>",
        "identity.>",
        "knowledge.>",
        "cache.>",
        "user.>",
    ],
    "AI_AUDIO": ["audio.>"],
}


def _is_retryable_error(error: Exception) -> bool:
    return isinstance(
        error,
        (
            NoRespondersError,
            ServiceUnavailableError,
            asyncio.TimeoutError,
            OSError,
        ),
    )


def _build_jetstream_error_hint(error: Exception | None) -> str:
    if error is None:
        return ""

    if isinstance(error, ServiceUnavailableError):
        return (
            " JetStream appears unavailable. Ensure NATS is started with JetStream enabled "
            "(for example: 'nats:latest -js -m 8222')."
        )

    if isinstance(error, NoRespondersError):
        return " JetStream has no responders yet; NATS may still be starting."

    return ""


async def _wait_for_jetstream_ready(jsm, retries: int, delay_seconds: float) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await jsm.account_info()
            return
        except Exception as error:
            last_error = error
            if not _is_retryable_error(error) or attempt >= retries:
                break
            logger.warning(
                "JetStream API not ready (attempt %s/%s): %s",
                attempt,
                retries,
                error,
            )
            await asyncio.sleep(delay_seconds)

    raise RuntimeError(
        f"JetStream API not ready after {retries} attempts: {last_error}."
        f"{_build_jetstream_error_hint(last_error)}"
    )


def _apply_policy_to_existing(config, stream_name: str) -> bool:
    """Update an existing stream's limits in place. Returns True if anything
    changed.

    `storage` is deliberately NOT updated: NATS rejects a storage change on
    a live stream, so AI_AUDIO moving file->memory needs the stream deleted
    and recreated. Attempting it here would turn every bootstrap into a
    failed update. Logged loudly instead, so the migration is a decision
    someone makes rather than an error they hit.
    """
    policy = STREAM_POLICIES.get(stream_name)
    if policy is None:
        return False

    from nats.js.api import StorageType

    changed = False
    if config.max_age != policy["max_age"]:
        config.max_age = policy["max_age"]
        changed = True
    if config.max_bytes != policy["max_bytes"]:
        config.max_bytes = policy["max_bytes"]
        changed = True

    desired_storage = (
        StorageType.MEMORY if policy["storage"] == "memory" else StorageType.FILE
    )
    if config.storage != desired_storage:
        logger.warning(
            "Stream %s storage is %s but policy wants %s. NATS cannot change "
            "storage on a live stream -- delete and recreate it to apply "
            "this. Limits below are still being applied.",
            stream_name,
            config.storage,
            desired_storage,
        )

    return changed


async def reconcile_existing_stream(
    jsm, stream_name: str, subjects: list[str], max_retries: int = 3
) -> bool:
    """Read-modify-write an existing stream's subjects and policy, with a
    verify-and-retry loop in place of compare-and-set.

    P4-11b: up to six agent processes can reach `_bootstrap_mesh` (or the
    bootstrap script) for the same stream at startup. Each fetches a
    `stream_info()` snapshot, computes its own desired subject union from
    it, and writes back with `update_stream()`. Between one caller's read
    and its write, another caller's write can already have landed --
    `update_stream` has no compare-and-set parameter (unlike JetStream's KV
    API, which does support revision-checked writes), so the second write
    silently overwrites the first based on a snapshot that no longer
    reflects the server's state. The subject that snapshot never saw is
    dropped, not merged -- the stream ends up wired for whichever caller
    wrote last, not for the union of everyone who tried.

    Rather than assume the write landed, this re-reads the stream
    immediately after writing and checks that the CALLER's own desired
    subjects actually made it into the saved config. If they did not --
    someone else's write raced in between -- it recomputes from the fresh
    state and retries. Every retry starts from a real read, so concurrent
    callers converge on the union rather than compounding the race.

    Policy fields (max_age/max_bytes/storage) are not re-verified the same
    way: every caller computes the identical desired policy from the same
    `STREAM_POLICIES` constant, so a lost policy update just means someone
    else already applied the same values -- there is nothing to converge
    that isn't already converged. Only `subjects` can legitimately differ
    between concurrent callers, so only `subjects` needs the retry.

    Returns True if the stream ended up changed by this call (even if a
    retry was needed), False if it was already synchronized. Raises
    StreamReconciliationError if the retry budget is exhausted.
    """
    desired_subjects = set(subjects)
    last_seen_subjects: set[str] = set()

    for attempt in range(1, max_retries + 1):
        info = await jsm.stream_info(stream_name)
        current_subjects = set(info.config.subjects or [])
        config = info.config
        changed = False

        if not desired_subjects.issubset(current_subjects):
            config.subjects = list(current_subjects.union(desired_subjects))
            changed = True

        changed |= _apply_policy_to_existing(config, stream_name)

        if not changed:
            return False

        await jsm.update_stream(config)

        verify = await jsm.stream_info(stream_name)
        last_seen_subjects = set(verify.config.subjects or [])
        if desired_subjects.issubset(last_seen_subjects):
            return True

        logger.warning(
            "Stream %s: update lost a concurrent race (attempt %s/%s); "
            "retrying against fresh state.",
            stream_name,
            attempt,
            max_retries,
        )

    logger.error(
        "Stream %s: could not reconcile subjects after %s attempts "
        "(concurrent writers kept racing). Last observed subjects: %s",
        stream_name,
        max_retries,
        last_seen_subjects,
    )
    raise StreamReconciliationError(
        f"Stream {stream_name} could not be reconciled after {max_retries} attempts"
    )


def build_stream_config(stream_name: str, subjects: list[str]):
    """The single source of truth for how a core stream is declared.

    P1-2: there are TWO stream-creation paths in this codebase, and only one
    is obvious. `setup_streams()` below is the bootstrap script's path;
    `BaseAgent._bootstrap_mesh()` (agents/base.py) is the other default path,
    and it runs on every anonymous agent start. Authenticated runtime agents
    intentionally skip that administrative path; the dedicated provisioner
    must call this setup function first. Whichever enabled path reaches a
    fresh mesh first decides the policy -- both carry the same config.

    A stream whose name has no policy entry gets subjects only, exactly as
    before -- so an unknown stream degrades to today's behaviour instead of
    inheriting limits meant for something else.
    """
    from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

    policy = STREAM_POLICIES.get(stream_name)
    if policy is None:
        return StreamConfig(name=stream_name, subjects=list(subjects))

    storage = StorageType.MEMORY if policy["storage"] == "memory" else StorageType.FILE
    return StreamConfig(
        name=stream_name,
        subjects=list(subjects),
        retention=RetentionPolicy.LIMITS,
        storage=storage,
        max_age=cast(float, policy["max_age"]),
        max_bytes=cast(int, policy["max_bytes"]),
        # Drop the oldest messages at the limit rather than refusing new
        # ones: for both tiers, rejecting a publish would stall a live
        # conversation or the audio path, which is worse than losing the
        # oldest history.
        discard=DiscardPolicy.OLD,
    )


async def _ensure_stream(
    jsm, stream_name: str, subjects: list[str], retries: int, delay_seconds: float
) -> None:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            await jsm.add_stream(config=build_stream_config(stream_name, subjects))
            logger.info("Created %s stream", stream_name)
            return
        except BadRequestError:
            # P4-11b: reconcile_existing_stream both applies P1-2's
            # retention policy to a pre-existing stream and retries against
            # a concurrent writer (this script and up to six agent
            # processes can all reach this same branch at startup).
            changed = await reconcile_existing_stream(jsm, stream_name, subjects)
            if changed:
                logger.info("Updated %s configuration", stream_name)
            else:
                logger.info("%s already synchronized", stream_name)
            return
        except Exception as error:
            if not _is_retryable_error(error) or attempt >= retries:
                last_error = error
                break
            logger.warning(
                "JetStream stream %s not ready (attempt %s/%s): %s",
                stream_name,
                attempt,
                retries,
                error,
            )
            await asyncio.sleep(delay_seconds)

    raise RuntimeError(f"Failed to synchronize stream '{stream_name}': {last_error}")


async def setup_streams(
    nats_url: str | None = None,
    retries: int | None = None,
    delay_seconds: float | None = None,
) -> None:
    """Ensure core JetStream streams are available with startup-race tolerance."""
    nats_url = nats_url or os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    retries = max(
        1,
        int(
            retries
            if retries is not None
            else os.getenv("NATS_STREAM_SETUP_RETRIES", "30")
        ),
    )
    delay_seconds = max(
        0.1,
        float(
            delay_seconds
            if delay_seconds is not None
            else os.getenv("NATS_STREAM_SETUP_DELAY_SECONDS", "1.5")
        ),
    )
    logger.info("Connecting to NATS at %s", nats_url)

    nats_user = os.getenv("NATS_USER")
    nats_password = os.getenv("NATS_PASSWORD")
    if not nats_user or not nats_password:
        raise RuntimeError(
            "NATS stream provisioning requires NATS_USER and NATS_PASSWORD"
        )
    connect_kwargs = {"user": nats_user, "password": nats_password}

    nc = await nats.connect(cast(str, nats_url), **connect_kwargs)
    try:
        jsm = nc.jsm()
        await _wait_for_jetstream_ready(
            jsm, retries=retries, delay_seconds=delay_seconds
        )

        for stream_name, subjects in CORE_STREAMS.items():
            await _ensure_stream(
                jsm,
                stream_name=stream_name,
                subjects=list(subjects),
                retries=retries,
                delay_seconds=delay_seconds,
            )
    finally:
        await nc.close()

    logger.info("NATS mesh infrastructure ready")
