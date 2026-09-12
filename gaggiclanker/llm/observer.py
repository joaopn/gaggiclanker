"""The live-call ring: what the LLM is doing, right now, in fifty entries.

An analysis call takes thirty seconds to two minutes. Without this the UI has
one bit of information — a spinner — and no answer to "is it stuck, is it
rate-limited, or is it just slow?". The observer gives the header a running
count and the sheet behind it a list: what was asked, about which shot, on
which model, how long it has been going, and what it cost.

Two properties are load-bearing:

* **In-flight records are never evicted.** The ring drops the oldest *finished*
  entry, so a backfill that fires sixty calls cannot push the running ones off
  the list the user is watching them on.
* **Publishing never fails a call.** The bus is optional and every publish is
  best-effort; observability that can break the thing it observes is worse than
  none.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.llm.types import Usage

__all__ = ["LLM_CALL_EVENT", "CallHandle", "LlmCallObserver", "LlmCallRecord"]

#: The SSE event name the front end subscribes to on `/api/llm/calls/stream`.
LLM_CALL_EVENT = "llm.call"

#: How many finished calls are kept. Matches cvclanker's queue: enough to cover
#: a backfill's worth of history, small enough to serialise on every update.
RING_SIZE = 50

type CallStatus = Literal["running", "succeeded", "failed"]


@dataclass
class LlmCallRecord:
    """One call, from registration to outcome."""

    id: str
    label: str
    subject: str
    provider: str
    model: str
    purpose: str
    status: CallStatus = "running"
    started_at: str = ""
    completed_at: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    mode: str | None = None
    error: str | None = None

    def to_api(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CallHandle:
    """The token a caller holds while its call is in flight."""

    record: LlmCallRecord
    observer: LlmCallObserver
    _started: float = field(default_factory=time.monotonic)

    def note(self, *, provider: str | None = None, model: str | None = None) -> None:
        """Fill in what was only decided after the call started."""
        if provider is not None:
            self.record.provider = provider
        if model is not None:
            self.record.model = model
        self.observer.publish(self.record)

    def succeed(self, usage: Usage, *, mode: str = "") -> LlmCallRecord:
        self.record.status = "succeeded"
        self._record_usage(usage)
        self.record.mode = mode or self.record.mode
        return self._finish()

    def fail(self, message: str, *, mode: str = "", usage: Usage | None = None) -> LlmCallRecord:
        """Finish a call that did not produce an answer.

        ``usage`` is not decoration. A reply that arrived and then failed
        validation cost the same as one that passed, so a failed row with no
        tokens on it makes the sheet look as though the mistakes were free.
        """
        self.record.status = "failed"
        self.record.error = message
        if usage is not None:
            self._record_usage(usage)
        self.record.mode = mode or self.record.mode
        return self._finish()

    def _record_usage(self, usage: Usage) -> None:
        self.record.prompt_tokens = usage.prompt_tokens
        self.record.completion_tokens = usage.completion_tokens
        self.record.total_tokens = usage.total_tokens

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def _finish(self) -> LlmCallRecord:
        self.record.completed_at = utc_now()
        self.record.duration_ms = self.duration_ms
        self.observer.publish(self.record)
        return self.record


class LlmCallObserver:
    """The ring, and the fan-out onto the browser's event stream."""

    def __init__(self, bus: SseEventBus | None = None, size: int = RING_SIZE) -> None:
        self._records: deque[LlmCallRecord] = deque()
        self._size = size
        self.bus = bus

    def register(
        self,
        *,
        label: str,
        subject: str = "",
        provider: str = "",
        model: str = "",
        purpose: str = "default",
    ) -> CallHandle:
        record = LlmCallRecord(
            id=uuid.uuid4().hex,
            label=label,
            subject=subject,
            provider=provider,
            model=model,
            purpose=purpose,
            started_at=utc_now(),
        )
        self._records.append(record)
        self._evict()
        handle = CallHandle(record=record, observer=self)
        self.publish(record)
        return handle

    def _evict(self) -> None:
        """Trim to size, skipping anything still running.

        Walks from the oldest end and removes the first *finished* record. A
        ring full of running calls simply grows, which is the right failure:
        sixty in-flight calls is a real thing to see, and dropping the ones the
        user is waiting on would hide it.
        """
        while len(self._records) > self._size:
            victim = next((r for r in self._records if r.status != "running"), None)
            if victim is None:
                return
            self._records.remove(victim)

    def snapshot(self) -> list[LlmCallRecord]:
        """Newest first — the order the sheet renders."""
        return list(reversed(self._records))

    @property
    def running(self) -> int:
        return sum(1 for record in self._records if record.status == "running")

    def publish(self, record: LlmCallRecord) -> None:
        """Best-effort fan-out. An observer must never fail the call it watches."""
        if self.bus is None:
            return
        self.bus.publish(
            SseEvent(
                event=LLM_CALL_EVENT,
                data={"call": record.to_api(), "running": self.running},
            )
        )
