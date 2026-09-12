"""A registry for the long-lived background tasks the app owns.

Once the device client exists, the app holds a device WebSocket reader, a sync loop and a
reconnect supervisor. Each is an :class:`asyncio.Task` that must be cancelled
and awaited at shutdown, or uvicorn exits while a task is mid-write to SQLite.
Holding them in one place is what makes the lifespan's teardown a single call.

A task that raises is logged with its traceback rather than dying silently in a
never-awaited future, which is the default failure mode of ``create_task``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

__all__ = ["TaskRegistry"]

log = structlog.get_logger(__name__)


class TaskRegistry:
    """Owns the app's background tasks: spawn them here, cancel them all at once."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def names(self) -> list[str]:
        return sorted(self._tasks)

    def spawn(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Start ``coro`` as a named task. A duplicate name is a programming error."""
        if name in self._tasks and not self._tasks[name].done():
            coro.close()
            raise RuntimeError(f"background task {name!r} is already running")
        task = asyncio.create_task(coro, name=name)
        self._tasks[name] = task
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        name = task.get_name()
        self._tasks.pop(name, None)
        if task.cancelled():
            log.debug("background_task_cancelled", task=name)
            return
        error = task.exception()
        if error is not None:
            log.error("background_task_failed", task=name, exc_info=error)
        else:
            log.debug("background_task_finished", task=name)

    async def cancel_all(self, timeout: float = 10.0) -> None:  # noqa: ASYNC109
        """Cancel every task and wait for them, bounded by ``timeout``.

        The timeout is a parameter rather than the caller's own
        ``asyncio.timeout`` block (what ASYNC109 asks for) because this runs in
        the lifespan's teardown: a cancellation arriving there is what we are
        already handling, so the bound has to live inside the call.
        """
        tasks = list(self._tasks.values())
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            log.warning(
                "background_tasks_did_not_stop",
                tasks=[t.get_name() for t in pending],
                timeout=timeout,
            )
        log.debug("background_tasks_stopped", count=len(done))
        self._tasks.clear()
