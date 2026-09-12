"""Server-sent events: a fan-out bus and the response helper routes use.

The device gives us one WebSocket (the firmware allows three clients in total,
so exactly one is the budget). Everything the UI needs from it — live status at
2 Hz, "a shot was saved", sync progress — is published once onto an
:class:`EventBus` and fanned out to however many browser tabs are open.

Subscribers are deliberately lossy: a tab that stops reading must not stall the
device reader or grow the process's memory, so its queue drops the oldest event
and the stream carries on. Losing a telemetry frame is invisible; stalling the
device loop is not.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
from sse_starlette.sse import EventSourceResponse

__all__ = ["DEFAULT_PING_SECONDS", "EventBus", "SseEvent", "sse_response"]

log = structlog.get_logger(__name__)

# Proxies and browsers drop an idle event stream; 15 s is comfortably under the
# usual 30-60 s idle timeouts and cheap enough at a handful of clients.
DEFAULT_PING_SECONDS = 15

# Per-subscriber backlog. At 2 Hz telemetry this is a couple of minutes of
# events, far more than a live tab ever falls behind by.
DEFAULT_QUEUE_SIZE = 256


@dataclass(slots=True)
class SseEvent:
    """One event on the wire. ``data`` is JSON-encoded when it is not a string."""

    event: str
    data: Any = None
    id: str | None = None

    def encoded(self) -> dict[str, str]:
        """The kwargs ``EventSourceResponse`` expects for a single message."""
        payload = self.data if isinstance(self.data, str) else json.dumps(self.data, default=str)
        message: dict[str, str] = {"event": self.event, "data": payload}
        if self.id is not None:
            message["id"] = self.id
        return message


@dataclass
class EventBus:
    """An in-process publish/subscribe hub for :class:`SseEvent`.

    One bus per application, held on ``app.state``. Publishing never blocks and
    never raises, so a producer (the device reader) is unaffected by how many
    consumers exist or how fast they read.
    """

    queue_size: int = DEFAULT_QUEUE_SIZE
    _subscribers: set[asyncio.Queue[SseEvent]] = field(default_factory=set, repr=False)
    dropped: int = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: SseEvent) -> None:
        """Deliver ``event`` to every subscriber, dropping the oldest on overflow."""
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:  # pragma: no cover - raced with a reader
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - raced with another publisher
                self.dropped += 1

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[SseEvent]]:
        """Register a subscriber for the duration of the block."""
        queue: asyncio.Queue[SseEvent] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def stream(self) -> AsyncIterator[SseEvent]:
        """An endless stream of events for one subscriber.

        Ends when the consumer is cancelled — which is what happens when the
        browser closes the connection.
        """
        with self.subscribe() as queue:
            while True:
                yield await queue.get()


async def _encode(source: AsyncIterator[SseEvent]) -> AsyncIterator[dict[str, str]]:
    async for event in source:
        yield event.encoded()


def sse_response(
    source: AsyncIterator[SseEvent], *, ping: int = DEFAULT_PING_SECONDS
) -> EventSourceResponse:
    """Wrap an :class:`SseEvent` stream in an SSE response.

    ``X-Accel-Buffering: no`` is set because a reverse proxy that buffers the
    stream turns a live shot view into a batch delivery at the end of the shot.
    """
    return EventSourceResponse(
        _encode(source),
        ping=ping,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
