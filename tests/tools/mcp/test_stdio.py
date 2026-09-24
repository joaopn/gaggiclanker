"""``gaggiclanker mcp`` over stdio, as the ``claude_code`` chat provider's ``claude -p`` uses it.

A real subprocess speaking the real protocol. That is the point: the stdio entry
point is a *different* process with a different set of services wired (a
database and nothing else), and the failure it exists to avoid — a log line on
stdout, a missing schema, a tool that assumes the running application — only
shows up when something else is driving it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.llm.providers.claude_code import MCP_SERVER_NAME, build_mcp_config
from gaggiclanker.tools.mcp.server import SERVER_NAME
from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS
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


def server_env(config: dict[str, Any]) -> dict[str, str]:
    """The environment the generated config gives the child server."""
    environment: dict[str, str] = config["mcpServers"][MCP_SERVER_NAME]["env"]
    return environment


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
    """With no Set in the environment it is a general conversation."""
    data_dir, _ = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert tools == GENERAL_TOOLS

        resources = {str(row.uri) for row in (await session.list_resources()).resources}
        assert f"{SERVER_NAME}://knowledge/rules" in resources


async def test_a_set_scoped_server_offers_the_set_s_tools_and_no_others(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The scope reaches the child in its environment, and narrows what it has.

    This is the half the dispatcher cannot do: the `claude_code` provider runs
    the tool loop inside the CLI, so a tool this conversation must not have is
    kept from it by not being registered here at all.
    """
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id))

        tools = {tool.name for tool in (await session.list_tools()).tools}

    assert tools == SET_TOOLS
    assert "query_shots" not in tools


async def test_a_set_scoped_server_refuses_another_set_s_shot_and_its_resource(
    archive_dir: tuple[Path, Fixture],
) -> None:
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id))

        refused = await session.call_tool("get_set", {"set_id": fixture.set_id + 1})
        with pytest.raises(MCPError, match="can see no other"):
            await session.read_resource(f"{SERVER_NAME}://sets/{fixture.set_id + 1}")
        # And its own Set still reads, so the refusal is the scope and not the
        # resource being broken.
        assert await session.read_resource(f"{SERVER_NAME}://sets/{fixture.set_id}")

    assert refused.is_error is True
    assert "can see no other" in str(refused.content)


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


async def test_a_set_scoped_child_answers_insights_with_this_set_s_confirmed_ones(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The same narrowing over the protocol, because this is the other dispatcher.

    The CLI provider's loop runs in here, so a rule that held only for the
    in-process dispatcher would hold for every provider but the default one.
    """
    data_dir, fixture = archive_dir
    await _seed_insights(data_dir, fixture)
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id))

        confirmed = await session.call_tool("get_insights", {})
        unconfirmed = await session.call_tool("get_insights", {"include_unconfirmed": True})

    assert confirmed.is_error is False
    assert confirmed.structured_content is not None
    texts = {insight["text"] for insight in confirmed.structured_content["insights"]}
    assert "This Set's bean likes it finer." in texts
    assert "Another bean entirely." not in texts
    assert unconfirmed.is_error is True
    assert "nothing unconfirmed is evidence" in str(unconfirmed.content)


async def _seed_insights(data_dir: Path, fixture: Fixture) -> None:
    """Two insights in the archive the child will open: one of each kind."""
    from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
    from gaggiclanker.db.repos.knowledge_insights import (
        InsightScope,
        InsightsRepository,
        InsightWrite,
    )
    from gaggiclanker.db.repos.sets import SetsRepository

    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    try:
        row = await SetsRepository(db).get(fixture.set_id)
        assert row is not None
        stranger = await BeansRepository(db).create(BeanWrite(name="Somebody else's bag"))
        repo = InsightsRepository(db)
        await repo.insert(
            InsightWrite(
                scope=InsightScope(bean_id=row.bean_id),
                text="This Set's bean likes it finer.",
                source="chat",
                confirmed=True,
            )
        )
        await repo.insert(
            InsightWrite(
                scope=InsightScope(bean_id=stranger.id),
                text="Another bean entirely.",
                source="chat",
                confirmed=True,
            )
        )
    finally:
        await db.close()


# -- a scope that does not parse is a server that does not start -----------


def run_server(data_dir: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Start the command as the CLI would and let it reach EOF on stdin.

    A server that starts serves the protocol and exits 0 at end of input; a
    server that refuses its scope exits non-zero having printed one line on
    stderr and nothing at all on stdout, which is what "serves nothing" means
    for a process whose stdout *is* the protocol.
    """
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "gaggiclanker", "mcp", "--data-dir", str(data_dir)],
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT), **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("value", ["abc", "-1", "1e0", "", "  ", "²", "0"])
def test_a_scope_that_does_not_parse_refuses_to_start(
    archive_dir: tuple[Path, Fixture], value: str
) -> None:
    """Fail closed. Anything else serves the archive to a conversation about one Set."""
    data_dir, _ = archive_dir

    result = run_server(data_dir, GAGGICLANKER_MCP_SET_ID=value)

    assert result.returncode != 0, result.stdout
    assert "positive integer" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_flag_that_is_not_a_positive_id_refuses_to_start(
    archive_dir: tuple[Path, Fixture], value: str
) -> None:
    """The flag is checked as the variable is: argparse would take -1 happily."""
    data_dir, _ = archive_dir

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "gaggiclanker",
            "mcp",
            "--data-dir",
            str(data_dir),
            "--set-id",
            value,
        ],
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "positive integer" in result.stderr
    assert result.stdout == ""


def test_a_version_without_its_set_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    data_dir, fixture = archive_dir

    result = run_server(data_dir, GAGGICLANKER_MCP_SET_VERSION_ID=str(fixture.version_id))

    assert result.returncode != 0
    assert "belongs to a Set" in result.stderr
    assert result.stdout == ""


def test_a_version_of_another_set_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Checked against the archive: the ids have to describe one experiment."""
    data_dir, fixture = archive_dir

    result = run_server(
        data_dir,
        GAGGICLANKER_MCP_SET_ID=str(fixture.set_id),
        GAGGICLANKER_MCP_SET_VERSION_ID=str(fixture.version_id + 9999),
    )

    assert result.returncode != 0
    assert "not a version of Set" in result.stderr
    assert result.stdout == ""


def test_a_conversation_of_another_set_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The thread is written onto every change proposed here as its room.

    One belonging to another Set would file this Set's reasoning under somebody
    else's transcript, so it gets the scope variables' answer: serve nothing,
    rather than start and refuse every proposal afterwards.
    """
    data_dir, fixture = archive_dir

    result = run_server(
        data_dir,
        GAGGICLANKER_MCP_SET_ID=str(fixture.set_id),
        GAGGICLANKER_MCP_THREAD_ID="987654",
    )

    assert result.returncode != 0
    assert "not a conversation of Set" in result.stderr
    assert result.stdout == ""


def test_a_conversation_without_a_set_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """A general conversation changes no Set, so it is the room of nothing."""
    data_dir, _ = archive_dir

    result = run_server(data_dir, GAGGICLANKER_MCP_THREAD_ID="1")

    assert result.returncode != 0
    assert "changes no Set" in result.stderr
    assert result.stdout == ""


def test_a_set_that_is_not_in_the_archive_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    data_dir, fixture = archive_dir

    result = run_server(data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id + 9999))

    assert result.returncode != 0
    assert "No Set" in result.stderr
    assert result.stdout == ""


def test_a_flag_that_disagrees_with_the_environment_refuses_to_start(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Two ways of saying the same thing; a disagreement means one is stale."""
    data_dir, fixture = archive_dir

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "gaggiclanker",
            "mcp",
            "--data-dir",
            str(data_dir),
            "--set-id",
            str(fixture.set_id),
        ],
        cwd=str(REPO_ROOT),
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT),
            "GAGGICLANKER_MCP_SET_ID": str(fixture.set_id + 1),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "they must agree" in result.stderr
    assert result.stdout == ""


def test_no_scope_at_all_is_a_general_server_and_starts(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The control: absent is the one way to ask for the whole archive."""
    data_dir, _ = archive_dir

    result = run_server(data_dir)

    assert result.returncode == 0, result.stderr


async def test_a_tool_that_needs_the_running_application_says_so(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Over stdio there is a database and nothing else, and that has to read well."""
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)

        result = await session.call_tool("starting_point", {"bean_id": fixture.bean_id})

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


async def test_a_change_proposed_over_stdio_waits_and_creates_no_version(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The CLI's own tool loop obeys the same rule the dispatcher does.

    The child is a second dispatcher, so "a proposal changes nothing" has to be
    true here as well — and, because the loop runs inside the CLI, it can only
    be true by the tool behaving that way rather than by anything this process
    checks afterwards.
    """
    data_dir, fixture = archive_dir
    thread_id = await _make_thread(data_dir, fixture)
    async with AsyncExitStack() as stack:
        session = await session_for(
            stack,
            data_dir,
            GAGGICLANKER_MCP_SET_ID=str(fixture.set_id),
            GAGGICLANKER_MCP_THREAD_ID=str(thread_id),
        )

        refused = await session.call_tool(
            "propose_set_version", {"reason": "Two clicks finer.", "grind_setting": "20"}
        )
        result = await session.call_tool(
            "propose_set_version",
            {
                "reason": "Two clicks finer.",
                "grind_setting": "20",
                "prediction": "Compared to v1: two to four seconds longer and less sour.",
            },
        )

    assert refused.is_error is True
    assert "without a prediction" in str(refused.content)

    assert result.is_error is False, result.content
    assert result.structured_content is not None
    assert result.structured_content["status"] == "proposed"
    assert "Nothing has changed yet" in result.structured_content["note"]

    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    try:
        # Still one version, and the proposal names the conversation it came from.
        versions = await SetsRepository(db).versions(fixture.set_id)
        assert [version.version_no for version in versions] == [1]
        waiting = await SetProposalsRepository(db).waiting(fixture.set_id)
        assert waiting is not None
        assert waiting.thread_id == thread_id
    finally:
        await db.close()


async def _make_thread(data_dir: Path, fixture: Fixture) -> int:
    """A conversation in the archive the child will open, to be named on the proposal."""
    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    try:
        cursor = await db.execute(
            "INSERT INTO chat_threads (title, set_id) VALUES (?, ?)", ("About v1", fixture.set_id)
        )
        return int(cursor.lastrowid or 0)
    finally:
        await db.close()


async def test_the_child_is_never_offered_a_way_to_accept_a_proposal(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """Accept and decline are routes a person presses, on both dispatchers."""
    data_dir, fixture = archive_dir
    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir, GAGGICLANKER_MCP_SET_ID=str(fixture.set_id))
        tools = {tool.name for tool in (await session.list_tools()).tools}

    assert not [name for name in tools if "accept" in name or "decline" in name]


async def test_the_generated_config_forwards_the_conversation(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The thread travels beside the scope, and only when there is one."""
    data_dir, _ = archive_dir
    env = server_env(
        json.loads(
            build_mcp_config(
                data_dir=str(data_dir), executable=sys.executable, set_id=3, thread_id=9
            )
        )
    )
    assert env["GAGGICLANKER_MCP_THREAD_ID"] == "9"

    plain = server_env(
        json.loads(build_mcp_config(data_dir=str(data_dir), executable=sys.executable))
    )
    assert "GAGGICLANKER_MCP_THREAD_ID" not in plain


async def test_the_claude_code_provider_s_generated_config_starts_this_server(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The document the provider hands the CLI, run exactly as written.

    `python -m gaggiclanker mcp` is what reaches the server wherever the package
    lives, and the server's name is half of the provider's `--allowedTools` glob,
    so the two names must agree or the CLI is allowed nothing.
    """
    data_dir, fixture = archive_dir
    assert MCP_SERVER_NAME == SERVER_NAME
    config = json.loads(
        build_mcp_config(
            data_dir=str(data_dir),
            executable=sys.executable,
            set_id=fixture.set_id,
            set_version_id=fixture.version_id,
        )
    )
    # The scope travels as the child's environment, which is the only channel
    # there is: the CLI spawns this server itself.
    assert server_env(config)["GAGGICLANKER_MCP_SET_ID"] == str(fixture.set_id)
    server = config["mcpServers"][MCP_SERVER_NAME]
    params = StdioServerParameters(
        command=server["command"],
        args=server["args"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT), **server["env"]},
        cwd=str(REPO_ROOT),
    )
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        initialized = await session.initialize()

        result = await session.call_tool("get_set", {})

    assert initialized.server_info.name == SERVER_NAME
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["set"]["id"] == fixture.set_id


async def test_an_archive_that_has_never_been_migrated_is_refused_with_the_fix(
    tmp_path: Path,
) -> None:
    """Not migrated here: a second process migrating a live database is a race."""
    from gaggiclanker.tools.mcp.stdio import serve_stdio

    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(SystemExit) as caught:
        await serve_stdio(empty)

    assert "Start gaggiclanker once" in str(caught.value)


async def _designed_set(data_dir: Path, fixture: Fixture) -> tuple[int, int]:
    """A Set of the fixture's bean being designed, and its conversation."""
    from gaggiclanker.db.repos.chat import ChatRepository
    from gaggiclanker.db.repos.sets import DesignBrief, SetWrite

    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    try:
        row = await SetsRepository(db).create_design(
            SetWrite(name="Designed", bean_id=fixture.bean_id, grinder_id=fixture.grinder_id),
            DesignBrief(fork_profile_version_id=fixture.profile_version_id),
        )
        opened = await ChatRepository(db).open_thread(row.id)
        assert opened.thread is not None
        return row.id, opened.thread.id
    finally:
        await db.close()


async def test_a_child_for_a_set_being_designed_serves_the_design_tools_only(
    archive_dir: tuple[Path, Fixture],
) -> None:
    """The flag is read from the archive by the child, with the runner's own rule."""
    data_dir, fixture = archive_dir
    set_id, thread_id = await _designed_set(data_dir, fixture)
    async with AsyncExitStack() as stack:
        session = await session_for(
            stack,
            data_dir,
            GAGGICLANKER_MCP_SET_ID=str(set_id),
            GAGGICLANKER_MCP_THREAD_ID=str(thread_id),
        )

        tools = {tool.name for tool in (await session.list_tools()).tools}
        changed = await session.call_tool(
            "propose_set_version", {"reason": "Finer.", "grind_setting": "18"}
        )
        drafted = await session.call_tool(
            "draft_profile",
            {"base_version_id": fixture.profile_version_id, "patch": {}, "reason": "x"},
        )
        proposed = await session.call_tool(
            "propose_initial_recipe",
            {
                "profile": {"label": "Designed over stdio", "patch": {"temperature": 92}},
                "grind_setting": "20",
                "grind_is_absolute": True,
                "dose_g": 18,
                "target_yield_g": 40,
                "reason": "A cooler, longer shot.",
            },
        )

    assert tools == DESIGN_TOOLS
    assert changed.is_error is True
    assert drafted.is_error is True
    assert proposed.is_error is False, proposed.content
    assert proposed.structured_content is not None
    assert proposed.structured_content["kind"] == "design"
    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    try:
        waiting = await SetProposalsRepository(db).waiting(set_id)
    finally:
        await db.close()
    assert waiting is not None and waiting.thread_id == thread_id
