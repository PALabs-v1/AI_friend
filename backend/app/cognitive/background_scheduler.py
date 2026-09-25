"""Phase 04 Package B: watermarked, budgeted background work scheduler
(FINAL_HUMANOID_BRAIN_ARCHITECTURE.md Section 19).

Background cognition (due-goal review, contradiction processing,
relationship statistics, episodic clustering, calibration updates) must
never compete with a live user turn for the LLM or event loop. This module
gives that work a priority queue, per-job time/token budgets, idempotency
so a re-enqueued job cannot double-run for the same watermark, and
immediate preemption: a foreground turn always wins mid-flight, not only
between jobs.

`CognitivePipeline.execute` (pipeline.py) calls `preempt()` at the start of
every turn and `resume_foreground_idle()` once the turn is done -- this
module only tracks scheduler state, it never runs its own event loop or
initiates background work on its own.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .. import clock


class BackgroundJobKind(str, Enum):
    DUE_GOAL_REVIEW = "DUE_GOAL_REVIEW"
    CONTRADICTION_QUEUE = "CONTRADICTION_QUEUE"
    RELATIONSHIP_STATISTICS = "RELATIONSHIP_STATISTICS"
    EPISODIC_CLUSTERING = "EPISODIC_CLUSTERING"
    CALIBRATION_UPDATE = "CALIBRATION_UPDATE"


class BackgroundJob(BaseModel):
    """Budgeted, watermarked background maintenance task."""

    job_id: str
    kind: BackgroundJobKind
    watermark: float = 0.0
    budget_tokens: int = 500
    budget_time_s: float = 5.0
    priority: int = 50
    idempotency_key: str
    allowed_writes: list[str] = Field(default_factory=list)


async def _invoke(executor_fn: Callable, job: BackgroundJob) -> Any:
    """Call `executor_fn(job)`, awaiting the result if it is awaitable --
    lets tests and callers pass either a plain function or a coroutine
    function without the scheduler caring which."""
    result = executor_fn(job)
    if inspect.isawaitable(result):
        result = await result
    return result


class BackgroundScheduler:
    """Priority queue of `BackgroundJob`s with budget enforcement and
    immediate foreground preemption."""

    def __init__(self) -> None:
        self._queue: list[BackgroundJob] = []
        self._accepted_keys: set[tuple[str, float]] = set()
        # Reentrant reference count rather than a bool: a nested or
        # overlapping foreground activation (e.g. a second turn arriving,
        # or a caller with its own preempt/resume pair around a sub-scope)
        # must not let an inner `resume_foreground_idle()` flip the
        # scheduler back to idle while an outer preemption is still in
        # effect. `is_foreground_active` below is derived from this count,
        # never set directly.
        self._foreground_depth: int = 0
        self._current_task: asyncio.Task | None = None
        self._current_job: BackgroundJob | None = None
        self.last_watermark_by_kind: dict[BackgroundJobKind, float] = {}
        # Audit trail of budget/preemption failures -- not required by any
        # caller today, but this is the only record of *why* a job never
        # produced a result, so it is kept rather than discarded.
        self.errors: list[dict[str, Any]] = []

    @property
    def is_foreground_active(self) -> bool:
        return self._foreground_depth > 0

    def enqueue(self, job: BackgroundJob) -> bool:
        """Insert `job` into the priority queue (descending priority,
        FIFO among equal priorities). Returns False without inserting when
        `(idempotency_key, watermark)` was already accepted -- the same
        watermark can never enqueue the same logical job twice."""
        dedupe_key = (job.idempotency_key, job.watermark)
        if dedupe_key in self._accepted_keys:
            return False
        self._accepted_keys.add(dedupe_key)

        for index, existing in enumerate(self._queue):
            if job.priority > existing.priority:
                self._queue.insert(index, job)
                return True
        self._queue.append(job)
        return True

    def preempt(self) -> None:
        """Increment the foreground activation count and immediately cancel
        whatever background task is currently running. Reentrant -- calling
        this while already preempted just raises the depth further, and
        `resume_foreground_idle()` must be called an equal number of times
        before `run_next` is allowed to dequeue work again. Safe to call
        with nothing in flight; it just raises the depth."""
        self._foreground_depth += 1
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    def resume_foreground_idle(self) -> None:
        """Decrement the foreground activation count. `run_next` only
        dequeues work again once this has been called as many times as
        `preempt()` was -- the count never drops below zero, so an extra,
        unmatched resume is a no-op rather than going negative."""
        self._foreground_depth = max(0, self._foreground_depth - 1)

    def _record_error(self, job: BackgroundJob, error: str) -> None:
        self.errors.append(
            {
                "job_id": job.job_id,
                "kind": job.kind,
                "error": error,
                "at": clock.time(),
            }
        )

    async def run_next(self, executor_fn: Callable) -> tuple[bool, Any]:
        """Run the highest-priority queued job through `executor_fn`,
        bounded by its own `budget_time_s`.

        Returns `(False, None)` when the foreground is active or the queue
        is empty (nothing was dequeued); `(False, "budget_exceeded")` when
        the job was dequeued but did not finish in time; `(False,
        "preempted")` when a concurrent `preempt()` cancelled it mid-run;
        `(True, result)` on a clean completion, after which the job's
        watermark is stamped with the completion time.
        """
        if self.is_foreground_active or not self._queue:
            return (False, None)

        job = self._queue.pop(0)
        self._current_job = job
        task = asyncio.ensure_future(_invoke(executor_fn, job))
        self._current_task = task
        try:
            result = await asyncio.wait_for(task, timeout=job.budget_time_s)
        except TimeoutError:
            self._record_error(job, "budget_exceeded")
            return (False, "budget_exceeded")
        except asyncio.CancelledError:
            self._record_error(job, "preempted")
            return (False, "preempted")
        finally:
            self._current_task = None
            self._current_job = None

        job.watermark = clock.time()
        self.last_watermark_by_kind[job.kind] = job.watermark
        return (True, result)
