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
from collections.abc import Coroutine, Iterable
from typing import Any, Protocol

import structlog

__all__ = ["TaskRegistry", "TaskSpawner"]

log = structlog.get_logger(__name__)


class TaskSpawner(Protocol):
    """Start a named background task, and nothing else.

    What a caller that only queues work is given — the chat's tools, through
    :class:`~gaggiclanker.tools.registry.ToolContext`. The full registry also
    hands out its tasks (:meth:`TaskRegistry.get`) and cancels them by name, and
    a task is not opaque: ``get_coro().cr_frame`` holds the ``self`` the
    coroutine was called on. Narrowing the type is a statement of intent and no
    more than that — what a model-driven caller can actually reach is decided by
    *which* registry it is handed, and every task that talks to the machine is
    in the one :class:`~gaggiclanker.device.connection.DeviceConnection` keeps
    to itself.
    """

    def spawn(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]: ...


class TaskRegistry:
    """Owns the app's background tasks: spawn them here, cancel them all at once."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def names(self) -> list[str]:
        return sorted(self._tasks)

    def get(self, name: str) -> asyncio.Task[Any] | None:
        """The task under ``name``, if one is still running.

        For a caller that wants to wait for work it queued — `?wait=1` on the
        analysis routes. A finished task has already released its name, so
        ``None`` means "it is over", not "it was never there".
        """
        return self._tasks.get(name)

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
        # Only if the name still points at this task. A name can be claimed
        # again the moment its task is done — the sync engine's loops are, when
        # the machine connection is rebuilt — and a late callback from the old
        # task must not release the new one's claim.
        if self._tasks.get(name) is task:
            del self._tasks[name]
        if task.cancelled():
            log.debug("background_task_cancelled", task=name)
            return
        error = task.exception()
        if error is not None:
            log.error("background_task_failed", task=name, exc_info=error)
        else:
            log.debug("background_task_finished", task=name)

    async def cancel(self, names: Iterable[str], timeout: float = 10.0) -> None:  # noqa: ASYNC109
        """Cancel exactly the named tasks and wait for them, bounded by ``timeout``.

        For an owner that stops its own loops while the rest of the app keeps
        running — the sync engine when the machine connection is rebuilt. A
        name with no running task is skipped. Each name whose task stopped is
        released before this returns, so the owner can claim it again straight
        away.
        """
        tasks = [task for name in names if (task := self._tasks.get(name)) is not None]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            log.warning(
                "background_tasks_did_not_stop",
                tasks=[t.get_name() for t in pending],
                timeout=timeout,
            )
        for task in tasks:
            # Done ones only: a task that ignored its cancellation keeps its
            # name, so nothing can start a second copy beside it.
            if task.done() and self._tasks.get(task.get_name()) is task:
                del self._tasks[task.get_name()]

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
