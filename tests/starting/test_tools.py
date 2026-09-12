"""The `starting_point` tools, through the registry the chat and MCP both use.

Through the registry rather than by calling the function, because the three
things worth pinning are the registry's job: the permission is checked before
the function runs, the dispatch is audited, and a missing service is an error
*value* the model can read rather than an exception that ends the turn.
"""

from __future__ import annotations

import json

from gaggiclanker.db.repos.starting import StartingPointRunsRepository
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.service import LlmService
from gaggiclanker.starting.service import (
    StartingPointService,
    starting_point_task_name,
)
from gaggiclanker.tools.registry import READ_ONLY, ToolContext, registry
from tests.starting.conftest import Fixture


def _context(fixture: Fixture, **overrides: object) -> ToolContext:
    base: dict[str, object] = {
        "db": fixture.db,
        "settings": None,
        "caller": "chat",
    }
    base.update(overrides)
    return ToolContext(**base)  # type: ignore[arg-type]


def test_the_tool_is_registered_as_propose_class() -> None:
    """It creates nothing a person has to decide about, and it spends money.

    `read` would have been the tidy answer; handing a read-only agent a button
    that queues provider calls is not a read.
    """
    spec = registry.get("starting_point")
    assert spec is not None
    assert spec.permission == "propose"
    assert registry.get("get_starting_point") is not None
    assert registry.get("get_starting_point").permission == "read"  # type: ignore[union-attr]


async def test_a_read_only_caller_is_refused_and_the_refusal_is_audited(
    fixture: Fixture, llm: LlmService
) -> None:
    """A refused tool and a tool nobody called look identical in a transcript."""
    ctx = _context(fixture, settings=llm.settings, permissions=READ_ONLY)
    outcome = await registry.dispatch(
        ctx,
        "starting_point",
        {"bean_id": fixture.new_bean_id, "machine_id": fixture.machine_id},
    )
    assert outcome.status == "refused"
    assert outcome.ok is False

    rows = await fixture.db.fetch_all("SELECT tool, permission, status FROM tool_calls ORDER BY id")
    assert [dict(row) for row in rows] == [
        {"tool": "starting_point", "permission": "propose", "status": "refused"}
    ]


async def test_a_connection_with_no_service_says_so_rather_than_crashing(
    fixture: Fixture, llm: LlmService
) -> None:
    """The stdio MCP entry point opens a database and nothing else."""
    ctx = _context(fixture, settings=llm.settings)
    outcome = await registry.dispatch(
        ctx,
        "starting_point",
        {"bean_id": fixture.new_bean_id, "machine_id": fixture.machine_id},
    )
    assert outcome.status == "error"
    assert "running gaggiclanker application" in outcome.error


async def test_the_tool_queues_a_run_and_the_read_tool_gets_it_back(
    fixture: Fixture, llm: LlmService, starting: StartingPointService
) -> None:
    tasks = TaskRegistry()
    ctx = _context(fixture, settings=llm.settings, starting=starting, tasks=tasks)
    try:
        outcome = await registry.dispatch(
            ctx,
            "starting_point",
            {
                "bean_id": fixture.new_bean_id,
                "machine_id": fixture.machine_id,
                "grinder_id": fixture.grinder_id,
                "usual_grind": "22",
            },
        )
        assert outcome.ok is True
        assert outcome.data["started"] is True
        run_id = outcome.data["run_id"]
        assert outcome.data["status"] == "running"

        task = tasks.get(
            starting_point_task_name(fixture.new_bean_id, fixture.machine_id, fixture.grinder_id)
        )
        assert task is not None
        await task
    finally:
        await tasks.cancel_all()

    stored = await StartingPointRunsRepository(fixture.db).get(run_id)
    assert stored is not None and stored.status == "ok"

    read = await registry.dispatch(ctx, "get_starting_point", {"run_id": run_id})
    assert read.ok is True
    assert read.data["status"] == "ok"
    assert len(json.loads(read.as_content())["output"]["options"]) == 3

    audited = await fixture.db.fetch_all("SELECT tool, status FROM tool_calls ORDER BY id")
    assert [row["tool"] for row in audited] == ["starting_point", "get_starting_point"]
    assert all(row["status"] == "ok" for row in audited)


async def test_a_second_call_for_the_same_bag_gets_the_running_row(
    fixture: Fixture, llm: LlmService, starting: StartingPointService, provider: object
) -> None:
    """The registry name is the idempotency rule, and it is claimed synchronously."""
    tasks = TaskRegistry()
    ctx = _context(fixture, settings=llm.settings, starting=starting, tasks=tasks)
    args = {
        "bean_id": fixture.new_bean_id,
        "machine_id": fixture.machine_id,
        "grinder_id": fixture.grinder_id,
    }
    try:
        first = await registry.dispatch(ctx, "starting_point", args)
        second = await registry.dispatch(ctx, "starting_point", args)
        assert first.data["started"] is True
        assert second.data["started"] is False
        assert second.data["run_id"] == first.data["run_id"]
    finally:
        await tasks.cancel_all()


async def test_a_bean_that_does_not_exist_is_an_error_value(
    fixture: Fixture, llm: LlmService, starting: StartingPointService
) -> None:
    tasks = TaskRegistry()
    ctx = _context(fixture, settings=llm.settings, starting=starting, tasks=tasks)
    try:
        outcome = await registry.dispatch(
            ctx, "starting_point", {"bean_id": 9999, "machine_id": fixture.machine_id}
        )
    finally:
        await tasks.cancel_all()
    assert outcome.ok is False
    assert "9999" in outcome.error


async def test_reading_a_run_that_does_not_exist_is_an_error_value(
    fixture: Fixture, llm: LlmService
) -> None:
    outcome = await registry.dispatch(
        _context(fixture, settings=llm.settings), "get_starting_point", {"run_id": 9999}
    )
    assert outcome.ok is False
