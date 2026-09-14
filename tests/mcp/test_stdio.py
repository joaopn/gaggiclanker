"""``gaggiclanker mcp`` over stdio, as Claude Desktop and ``claude -p`` use it.

A real subprocess speaking the real protocol. That is the point: the stdio entry
point is a *different* process with a different set of services wired (a
database and nothing else), and the failure it exists to avoid — a log line on
stdout, a missing schema, a tool that assumes the running application — only
shows up when something else is driving it.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.mcp.server import SERVER_NAME
from tests.analyzer.conftest import Fixture, build_fixture

#: The subprocess has to import gaggiclanker, and a test run is not necessarily
#: cwd'd at the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def archive_dir(tmp_path: Path) -> AsyncIterator[tuple[Path, Fixture]]:
    """A DATA_DIR holding a migrated, seeded archive — as the server leaves one."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    await run_migrations(db)
    fixture = await build_fixture(db)
    await db.close()
    yield data_dir, fixture


def parameters(data_dir: Path, **env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "gaggiclanker", "mcp", "--data-dir", str(data_dir)],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT), **env},
        cwd=str(REPO_ROOT),
    )


async def session_for(stack: AsyncExitStack, data_dir: Path, **env: str) -> ClientSession:
    read, write = await stack.enter_async_context(stdio_client(parameters(data_dir, **env)))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def test_the_entry_point_serves_the_tools_and_the_resources(
    archive_dir: tuple[Path, Fixture],
) -> None:
    data_dir, _ = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert {"describe_schema", "query_shots", "get_shot", "get_set"} <= tools

        resources = {str(row.uri) for row in (await session.list_resources()).resources}
        assert f"{SERVER_NAME}://knowledge/rules" in resources


async def test_a_query_runs_against_the_archive_the_server_left_behind(
    archive_dir: tuple[Path, Fixture],
) -> None:
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        result = await session.call_tool(
            "query_shots", {"sql": "SELECT COUNT(*) AS n FROM v_shots"}
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["rows"][0][0] == len(fixture.shots)


async def test_the_scope_env_var_makes_an_unqualified_question_answerable(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """`claude -p` passes it in the generated --mcp-config; a client can too."""
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id))

        result = await session.call_tool("get_set", {})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["set"]["id"] == fixture.set_id


async def test_a_tool_that_needs_the_running_application_says_so(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Over stdio there is a database and nothing else, and that has to read well."""
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        result = await session.call_tool("run_analysis", {"shot_id": fixture.shots[0]})

    assert result.is_error is True
    assert "running gaggiclanker application" in str(result.content)


async def test_a_profile_draft_can_be_proposed_over_stdio(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Proposing a draft needs the archive and the safety bounds, not the running app."""
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        result = await session.call_tool(
            "draft_profile",
            {
                "base_version_id": fixture.profile_version_id,
                "patch": {"temperature": 92},
                "reason": "A degree cooler.",
            },
        )

    assert result.is_error is False, result.content
    assert result.structured_content is not None
    assert result.structured_content["status"] == "draft"


async def test_an_archive_that_has_never_been_migrated_is_refused_with_the_fix(
    tmp_path: Path,
) -> None:
    """Not migrated here: a second process migrating a live database is a race."""
    from gaggiclanker.mcp.stdio import serve_stdio

    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(SystemExit) as caught:
        await serve_stdio(empty)

    assert "Start gaggiclanker once" in str(caught.value)
