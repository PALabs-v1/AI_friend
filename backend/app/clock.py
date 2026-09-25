"""Injectable wall-clock seam (see docs/brain-research-v3/06-benchmark-plan.md, Phase 6).

Every timestamp read in the cognitive core that used to call `time.time()` /
`datetime.now()` directly goes through this module instead, so BrainBench can
replay a simulated calendar (lifesim years) through the real brain without
waiting real years for hormone decay, silence detection or proactive cooldowns
to elapse. Production code never touches `use_clock`, so `time()`/`now()` fall
straight through to `SystemClock` and behavior is byte-for-byte unchanged.

Deliberately placed at `app/clock.py`, not `app/cognitive/clock.py` as
`06-benchmark-plan.md` first sketched: `app/cognitive/__init__.py` eagerly
imports `core.py`/`decision.py`, which import back into `app/state/*`, so a
clock module living inside `app/cognitive/` would make `app/state/agent_state.py`
importing it a circular import (`app.state` -> `app.cognitive` -> `app.core`
-> `app.state`, mid-init). `app/__init__.py` is empty and nothing needs to
import from it, so a leaf module here has no cycle to fall into.
"""

from __future__ import annotations

import time as _time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, tzinfo
from typing import Protocol


class Clock(Protocol):
    def time(self) -> float: ...

    def monotonic(self) -> float: ...

    def now(self, tz: tzinfo | None = None) -> datetime: ...


class SystemClock:
    """The real wall clock. The default everywhere; production never overrides it."""

    def time(self) -> float:
        return _time.time()

    def monotonic(self) -> float:
        return _time.monotonic()

    def now(self, tz: tzinfo | None = None) -> datetime:
        return datetime.now(tz)


class ManualClock:
    """A clock a caller drives explicitly. For unit tests and BrainBench.

    Starts at `start` (or the current wall time if omitted) and only moves
    when told to, so a simulated day of hormone decay resolves against
    exactly the timestamps the simulation says elapsed, not whatever the
    test happened to take to run.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else datetime.now()

    def time(self) -> float:
        return self._now.timestamp()

    def monotonic(self) -> float:
        # Already monotonic: `set`/`advance` refuse to move backwards.
        return self._now.timestamp()

    def now(self, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return self._now
        if self._now.tzinfo is None:
            # A naive simulated instant has no real local offset to convert
            # from, so label it with `tz` directly rather than shifting it
            # by the *host machine's* offset the way `astimezone` would.
            return self._now.replace(tzinfo=tz)
        return self._now.astimezone(tz)

    def set(self, when: datetime) -> None:
        if when < self._now:
            raise ValueError(
                f"ManualClock only moves forward: {when} is before current {self._now}"
            )
        self._now = when

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"ManualClock only moves forward: advance({seconds}) is negative")
        self._now = datetime.fromtimestamp(self._now.timestamp() + seconds)


_DEFAULT: Clock = SystemClock()
_current: ContextVar[Clock] = ContextVar("app_clock", default=_DEFAULT)


def time() -> float:
    """Drop-in replacement for `time.time()`."""
    return _current.get().time()


def monotonic() -> float:
    """Drop-in replacement for `time.monotonic()`, for *cognitive cadence*
    only (e.g. the reflection throttle). Timeouts that bound real work -- an
    LLM stream deadline, a rate limiter, a shutdown wait -- must keep calling
    `time.monotonic()` directly: a simulated clock must never make a real
    model call time out early or wait forever.
    """
    return _current.get().monotonic()


def now(tz: tzinfo | None = None) -> datetime:
    """Drop-in replacement for `datetime.now()` / `datetime.now(tz)`."""
    return _current.get().now(tz)


@contextmanager
def use_clock(clock: Clock) -> Iterator[None]:
    """Route every `clock.time()` / `clock.now()` call in this context to `clock`.

    BrainBench wraps each simulated tick in this so a lifesim year's worth of
    hormone decay, silence detection and proactive eligibility resolves
    against the simulated calendar, not the wall clock the harness runs on.
    Never used in production request handling.
    """
    token = _current.set(clock)
    try:
        yield
    finally:
        _current.reset(token)
