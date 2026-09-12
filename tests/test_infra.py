"""Infra units that have no HTTP surface of their own: errors, SSE bus, tasks."""

from __future__ import annotations

import asyncio

import pytest

from gaggiclanker.infra.errors import (
    AppError,
    BadRequest,
    NotFound,
    ServiceUnavailable,
    status_to_code,
    to_app_error,
)
from gaggiclanker.infra.request_context import get_request_id, request_context
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry


def test_error_classes_carry_status_and_code() -> None:
    assert (BadRequest("x").status, BadRequest("x").code) == (400, "INVALID_REQUEST")
    assert (NotFound().status, NotFound().code) == (404, "NOT_FOUND")
    assert ServiceUnavailable("device offline").status == 503
    assert status_to_code(405) == "METHOD_NOT_ALLOWED"
    assert status_to_code(409) == "CONFLICT"
    assert status_to_code(504) == "GATEWAY_TIMEOUT"
    assert status_to_code(418) == "INTERNAL_ERROR"


def test_details_survive_on_the_error() -> None:
    error = BadRequest("bad", details=[{"field": "a", "message": "nope"}])
    assert error.details == [{"field": "a", "message": "nope"}]


def test_unexpected_exceptions_become_opaque_500s() -> None:
    """str(exc) can hold a path or a token, so it must not reach the client."""
    converted = to_app_error(ValueError("/data/secret-path/db is corrupt"))
    assert converted.status == 500
    assert "secret-path" not in converted.message


def test_app_errors_pass_through_unchanged() -> None:
    original = AppError("nope", status=409)
    assert to_app_error(original) is original


def test_timeouts_map_to_504_not_408() -> None:
    """408 blames the client for a slow request; a slow dependency is 504."""
    converted = to_app_error(TimeoutError())
    assert converted.status == 504
    assert converted.code == "GATEWAY_TIMEOUT"


def test_request_context_is_scoped() -> None:
    assert get_request_id() is None
    with request_context("abc"):
        assert get_request_id() == "abc"
    assert get_request_id() is None


def test_sse_event_encodes_json() -> None:
    encoded = SseEvent(event="status", data={"tt": 93.5}).encoded()
    assert encoded == {"event": "status", "data": '{"tt": 93.5}'}
    assert SseEvent(event="ping", data="raw").encoded()["data"] == "raw"


async def test_event_bus_fans_out_to_every_subscriber() -> None:
    bus = EventBus[SseEvent]()
    with bus.subscribe() as a, bus.subscribe() as b:
        assert bus.subscriber_count == 2
        bus.publish(SseEvent(event="shot", data={"id": 1}))
        assert (await a.get()).data == {"id": 1}
        assert (await b.get()).data == {"id": 1}
    assert bus.subscriber_count == 0


def test_event_bus_publish_with_no_subscribers_is_a_no_op() -> None:
    EventBus[SseEvent]().publish(SseEvent(event="shot"))


async def test_slow_subscriber_drops_oldest_instead_of_blocking() -> None:
    """A tab that stops reading must never stall the device loop."""
    bus = EventBus[SseEvent](queue_size=2)
    with bus.subscribe() as queue:
        for index in range(5):
            bus.publish(SseEvent(event="status", data=index))
        assert queue.qsize() == 2
        assert bus.dropped == 3
        assert [(await queue.get()).data, (await queue.get()).data] == [3, 4]


async def test_task_registry_cancels_everything() -> None:
    registry = TaskRegistry()
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.Event().wait()

    registry.spawn("forever", forever())
    await started.wait()
    assert registry.names == ["forever"]

    await registry.cancel_all()
    assert len(registry) == 0


async def test_duplicate_task_name_is_refused() -> None:
    registry = TaskRegistry()

    async def forever() -> None:
        await asyncio.Event().wait()

    registry.spawn("device", forever())
    with pytest.raises(RuntimeError, match="already running"):
        registry.spawn("device", forever())
    await registry.cancel_all()


async def test_failed_task_is_dropped_and_does_not_raise() -> None:
    registry = TaskRegistry()

    async def boom() -> None:
        raise ValueError("boom")

    task = registry.spawn("boom", boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task.done()
    assert len(registry) == 0
    await registry.cancel_all()
