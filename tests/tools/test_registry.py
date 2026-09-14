"""The registry: schemas per provider, permission checks, and the audit row.

What is being pinned here is the *seam*, not any one tool. A tool declared once
has to produce a valid OpenAI schema, a valid Anthropic schema and an MCP
registration, and every call through the dispatcher has to leave a row behind —
including the calls that were refused, which are the ones somebody debugging
"why did it not do that" comes looking for.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from gaggiclanker.db.repos.chat import ToolCallsRepository
from gaggiclanker.tools.registry import (
    CHAT_PERMISSIONS,
    READ_ONLY,
    ToolContext,
    ToolRegistry,
    registry,
)


class Args(BaseModel):
    value: int = 1


class Out(BaseModel):
    doubled: int


def build_registry() -> ToolRegistry:
    """A registry of this test's own; the process-wide one is not ours to edit."""
    return ToolRegistry()


# -- schemas ---------------------------------------------------------------


def test_every_tool_produces_both_provider_schemas() -> None:
    for spec in registry.specs(CHAT_PERMISSIONS):
        openai = spec.openai_schema()
        assert openai["type"] == "function"
        assert openai["function"]["name"] == spec.name
        assert openai["function"]["description"]
        assert openai["function"]["parameters"]["type"] == "object"

        anthropic = spec.anthropic_schema()
        assert anthropic["name"] == spec.name
        assert anthropic["input_schema"]["type"] == "object"


def test_schemas_are_sorted_so_the_prompt_cache_survives() -> None:
    names = [schema["function"]["name"] for schema in registry.openai_schemas()]
    assert names == sorted(names)


def test_no_tool_asks_for_a_machine() -> None:
    """A property of the whole list, pinned deliberately.

    The archive holds one machine, so a `machine_id` argument would be a number
    with exactly one correct value — and a model asked for one will sometimes
    invent it. This is the assertion that stops one coming back by habit when a
    tool is added.
    """
    for spec in registry.specs(CHAT_PERMISSIONS):
        properties = spec.openai_schema()["function"]["parameters"].get("properties", {})
        assert "machine_id" not in properties, spec.name


def test_every_registered_tool_reads_or_proposes() -> None:
    """Not a policy a caller applies — a property of the whole list.

    The set a caller is handed is irrelevant if no tool outside it exists, which
    is what this pins: every tool, whatever it is, is visible to the chat and to
    MCP alike, and none of them can write to the machine.
    """
    assert CHAT_PERMISSIONS == frozenset({"read", "propose"})
    assert {spec.name for spec in registry.specs(CHAT_PERMISSIONS)} == set(registry.names())
    assert {spec.permission for spec in registry.specs(CHAT_PERMISSIONS)} <= {"read", "propose"}


@pytest.mark.parametrize("permission", ["device_write", "write", "admin"])
def test_a_tool_that_would_write_to_the_machine_cannot_be_registered(permission: str) -> None:
    """The machine is written by the app's own routes, never by a tool a model calls."""
    local = build_registry()

    with pytest.raises(ValueError, match="may only read or propose"):

        @local.tool("push_to_machine", permission=permission)  # type: ignore[arg-type]
        async def push(ctx: ToolContext, args: Args) -> Out:  # pragma: no cover - refused
            return Out(doubled=0)

    assert "push_to_machine" not in local


def test_read_only_is_a_strict_subset() -> None:
    read = {spec.name for spec in registry.specs(READ_ONLY)}
    chat = {spec.name for spec in registry.specs(CHAT_PERMISSIONS)}
    assert read < chat
    assert "propose_set_version" in chat - read


# -- registration ----------------------------------------------------------


def test_a_duplicate_name_is_refused() -> None:
    local = build_registry()

    @local.tool("thing")
    async def one(ctx: ToolContext, args: Args) -> Out:
        return Out(doubled=args.value * 2)

    with pytest.raises(RuntimeError, match="already registered"):

        @local.tool("thing")
        async def two(ctx: ToolContext, args: Args) -> Out:  # pragma: no cover - refused
            return Out(doubled=0)


def test_an_unannotated_tool_is_refused_at_import_time() -> None:
    """A schema derived from annotations has to insist on them.

    The alternative is a tool that registers fine and produces an empty
    parameter schema, which the model then calls with nothing.
    """
    local = build_registry()

    with pytest.raises(TypeError, match="pydantic model"):

        @local.tool("bare")
        async def bare(ctx: ToolContext, args: int) -> Out:
            return Out(doubled=args)


# -- the dispatcher --------------------------------------------------------


async def test_a_successful_call_returns_the_model_and_audits_it(ctx: ToolContext) -> None:
    local = build_registry()

    @local.tool("double", permission="read")
    async def double(context: ToolContext, args: Args) -> Out:
        return Out(doubled=args.value * 2)

    outcome = await local.dispatch(ctx, "double", {"value": 21})

    assert outcome.ok
    assert outcome.data == {"doubled": 42}
    rows = await ToolCallsRepository(ctx.db).recent()
    assert [(row.tool, row.status, row.caller) for row in rows] == [("double", "ok", "test")]
    assert rows[0].input_hash  # the arguments are hashed, never stored verbatim


async def test_an_unknown_tool_is_refused_and_names_the_real_ones(ctx: ToolContext) -> None:
    outcome = await registry.dispatch(ctx, "delete_everything", {})

    assert not outcome.ok
    assert outcome.status == "refused"
    assert "query_shots" in outcome.data["detail"]


async def test_a_tool_outside_the_permission_set_is_refused_and_audited(
    ctx: ToolContext,
) -> None:
    narrowed = ToolContext(db=ctx.db, settings=ctx.settings, caller="test", permissions=READ_ONLY)

    outcome = await registry.dispatch(narrowed, "record_insight", {"text": "x"})

    assert outcome.status == "refused"
    assert "propose" in outcome.data["detail"]
    rows = await ToolCallsRepository(ctx.db).recent()
    assert rows[0].status == "refused"
    assert rows[0].permission == "propose"


async def test_bad_arguments_come_back_as_a_message_the_model_can_act_on(
    ctx: ToolContext,
) -> None:
    outcome = await registry.dispatch(ctx, "get_shot", {"shot_id": "not a number"})

    assert outcome.status == "error"
    assert outcome.data["error"] == "invalid_arguments"
    assert "shot_id" in outcome.data["detail"]


async def test_a_tool_that_raises_is_a_value_not_an_exception(ctx: ToolContext) -> None:
    local = build_registry()

    @local.tool("explode")
    async def explode(context: ToolContext, args: Args) -> Out:
        raise RuntimeError("the kettle exploded")

    outcome = await local.dispatch(ctx, "explode", {})

    assert not outcome.ok
    assert outcome.data["detail"] == "the kettle exploded"
    assert (await ToolCallsRepository(ctx.db).recent())[0].status == "error"


async def test_a_slow_tool_is_cut_off_at_its_own_timeout(ctx: ToolContext) -> None:
    local = build_registry()

    @local.tool("stall", timeout_s=0.05)
    async def stall(context: ToolContext, args: Args) -> Out:
        await asyncio.sleep(5)
        return Out(doubled=0)  # pragma: no cover - never reached

    outcome = await local.dispatch(ctx, "stall", {})

    assert outcome.status == "timeout"
    assert "0.05s" in outcome.data["detail"]
    assert (await ToolCallsRepository(ctx.db).recent())[0].status == "timeout"


async def test_cancellation_is_not_swallowed_as_a_tool_failure(ctx: ToolContext) -> None:
    """A cancelled run is not a failed tool, and the runner owns the status."""
    local = build_registry()

    @local.tool("wait")
    async def wait(context: ToolContext, args: Args) -> Out:
        await asyncio.sleep(5)
        return Out(doubled=0)  # pragma: no cover - never reached

    task = asyncio.create_task(local.dispatch(ctx, "wait", {}))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
