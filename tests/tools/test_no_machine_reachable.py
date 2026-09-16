"""No tool can reach the machine: not by permission, and not by reference.

The registry already refuses a tool declaring anything but ``read`` or
``propose``. This file pins the other half: the context a tool is handed holds
nothing from which the device client, the connection that owns it or the sync
engine can be reached. A propose tool that creates a draft is given the object
that creates drafts, not the service that also pushes them, so a mistake in a
tool cannot become a write to the machine.

The check is an object-graph walk from the context, bounded, over instance
attributes, container elements, bound methods, closures and — the part that
matters most — **running tasks**. A task is not an opaque handle: it hands out
its coroutine, a coroutine hands out its frame, and a frame's locals hold the
``self`` the coroutine was called on and everything it awaits. The sync
engine's loops keep their engine exactly there, so a registry holding them is a
path to the machine for anybody holding the registry, with no private attribute
touched on the way. The walk descends that path, and three controls prove it
does: one from the push-capable draft service, one from a task spawned on the
registry the tools are given, and one from the registry the machine's owner
keeps to itself.

It runs against an app actually connected to the fake machine, with a cleanup
run and a notes send in flight, so a client, a connection, an engine and two
held machine writes all exist and would be found if anything led to them.
"""

from __future__ import annotations

import asyncio
import functools
import json
import subprocess
import sys
import threading
import types
import weakref
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.analyzer.service import analysis_task_name
from gaggiclanker.cleanup.service import cleanup_task_name
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.connection import DeviceConnection
from gaggiclanker.device.fake import FakeDevice, build_fake_device
from gaggiclanker.domain.ids import pad6
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.notes.writeback import writeback_task_name
from gaggiclanker.settings import EnvSettings
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.starting.service import starting_point_task_name
from gaggiclanker.sync.engine import SyncEngine
from gaggiclanker.tools.mcp.stdio import stdio_tool_context
from gaggiclanker.tools.registry import registry
from tests.analyzer.conftest import Fixture, build_fixture
from tests.conftest import running_app, seed_settings
from tests.sync.conftest import FIRST_ID, SMALL_COUNT, build_archive_device

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: What a tool must never be able to reach.
MACHINE_TYPES: tuple[type, ...] = (GaggimateClient, DeviceConnection, SyncEngine)

#: Not descended into, and why each one. Every entry is either a leaf or a
#: place from which *everything* in the process is reachable, which would make
#: the walk say "the machine is reachable" about a program that merely runs.
#:
#: - ``str``/``bytes``/``bytearray``/``int``/``float``/``complex``/``bool``/
#:   ``None`` — values with no references of their own.
#: - ``type`` — a class is a declaration, not an instance anybody holds; every
#:   class in the process is reachable from any other through ``__mro__`` and
#:   module namespaces.
#: - ``ModuleType`` — a module's namespace is where every service class and
#:   every module-level singleton in the application lives.
#: - ``CodeType`` — constants and names, no live objects.
#: - ``AbstractEventLoop`` — holds every task, callback and transport in the
#:   process, the device client's socket included; reaching it proves nothing
#:   about what the context was given.
#: - ``Thread`` — the same, one layer down: aiosqlite's worker thread holds the
#:   loop it calls back into.
OPAQUE_TYPES: tuple[type, ...] = (
    str,
    bytes,
    bytearray,
    int,
    float,
    complex,
    bool,
    type(None),
    type,
    types.ModuleType,
    types.CodeType,
    asyncio.AbstractEventLoop,
    threading.Thread,
)

#: Frame attributes deliberately left alone: ``f_back`` is the caller's frame,
#: which walks out of the object graph and up the stack into the test runner,
#: and ``f_globals`` is the module namespace ``ModuleType`` is excluded for.
#: Only ``f_locals`` is a reference the coroutine itself holds.

MAX_DEPTH = 40
MAX_NODES = 400_000


def _children(obj: Any) -> list[tuple[str, Any]]:
    """The references an object holds, by the names a reader would follow."""
    found: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.append((f"[{key!r}]", key))
            found.append((f"[{key!r}]", value))
        return found
    if isinstance(obj, list | tuple | set | frozenset | deque):
        return [(f"[{index}]", item) for index, item in enumerate(obj)]
    if isinstance(obj, asyncio.Task):
        # The whole point of the walk. `get_coro()` is public, and from there
        # the frame's locals are where a loop keeps the engine it runs.
        found.append((".get_coro()", obj.get_coro()))
        found.extend(_settled(obj))
        return found
    if isinstance(obj, asyncio.Future):
        # A future is how one caller hands a finished object to another: the
        # analyzer parks the row it opened in one.
        return _settled(obj)
    if isinstance(obj, types.CoroutineType):
        return [(".cr_frame", obj.cr_frame), (".cr_await", obj.cr_await)]
    if isinstance(obj, types.AsyncGeneratorType):
        return [(".ag_frame", obj.ag_frame), (".ag_await", obj.ag_await)]
    if isinstance(obj, types.GeneratorType):
        return [(".gi_frame", obj.gi_frame), (".gi_yieldfrom", obj.gi_yieldfrom)]
    if isinstance(obj, types.FrameType):
        # Locals only; see the note beside OPAQUE_TYPES for f_back and f_globals.
        return [(f".f_locals[{name!r}]", value) for name, value in obj.f_locals.items()]
    if isinstance(obj, functools.partial):
        return [
            (".func", obj.func),
            *((f".args[{index}]", item) for index, item in enumerate(obj.args)),
            *((f".keywords[{key!r}]", value) for key, value in obj.keywords.items()),
        ]
    if isinstance(obj, weakref.ReferenceType):
        return [("()", obj())]
    if isinstance(obj, types.MethodType):
        return [("__self__", obj.__self__), ("__func__", obj.__func__)]
    if isinstance(obj, types.FunctionType):
        # The closure, not the globals: a module's namespace is where every
        # class lives, and a class is not an instance anybody holds.
        for name, cell in zip(obj.__code__.co_freevars, obj.__closure__ or (), strict=False):
            try:
                found.append((f"<closure {name}>", cell.cell_contents))
            except ValueError:  # an empty cell
                continue
        found.append(("__defaults__", obj.__defaults__))
        found.append(("__kwdefaults__", obj.__kwdefaults__))
        return found
    instance = getattr(obj, "__dict__", None)
    if isinstance(instance, dict):
        found.extend((f".{name}", value) for name, value in instance.items())
    for klass in type(obj).__mro__:
        slots = klass.__dict__.get("__slots__", ())
        for name in (slots,) if isinstance(slots, str) else slots:
            if name in ("__dict__", "__weakref__"):
                continue
            try:
                found.append((f".{name}", getattr(obj, name)))
            except AttributeError:
                continue
    return found


def _settled(future: asyncio.Future[Any]) -> list[tuple[str, Any]]:
    """What a finished future is holding, without raising on one that failed."""
    if not future.done() or future.cancelled():
        return []
    error = future.exception()
    if error is not None:
        return [(".exception()", error)]
    return [(".result()", future.result())]


def machine_paths(root: Any, *, label: str) -> list[str]:
    """Every path from ``root`` to an instance of :data:`MACHINE_TYPES`."""
    seen: set[int] = set()
    found: list[str] = []
    queue: deque[tuple[Any, str, int]] = deque([(root, label, 0)])
    visited = 0
    while queue:
        obj, path, depth = queue.popleft()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        visited += 1
        assert visited <= MAX_NODES, f"the walk from {label} did not finish in {MAX_NODES} nodes"
        if isinstance(obj, MACHINE_TYPES):
            found.append(f"{path} -> {type(obj).__name__}")
            continue
        if isinstance(obj, OPAQUE_TYPES) or depth >= MAX_DEPTH:
            continue
        for name, child in _children(obj):
            queue.append((child, f"{path}{name}", depth + 1))
    return found


@pytest.fixture
async def fake_device() -> AsyncIterator[FakeDevice]:
    device = build_fake_device(FIXTURES)
    await device.start()
    try:
        yield device
    finally:
        await device.stop()


@pytest.fixture
async def connected(
    env: EnvSettings, fake_device: FakeDevice
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, Fixture]]:
    """The app, connected to the fake machine, with the analyzer's archive in it."""
    await seed_settings(env, gaggimateHost=fake_device.address, gaggimateTimeoutSeconds=5)
    async with running_app(env) as (app, client):
        connection = app.state.connection
        assert await connection.client.wait_connected(5.0), "the fake machine did not connect"
        assert connection.engine is not None
        fixture = await build_fixture(app.state.db)
        yield app, client, fixture


def test_the_walk_finds_the_machine_when_a_path_exists(
    connected: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """The control: without it, a walk that never looked would also pass."""
    app, _, _ = connected

    paths = machine_paths(app.state.drafts, label="drafts")

    assert any(path.endswith("DeviceConnection") for path in paths), paths


async def test_nothing_the_chat_hands_a_tool_reaches_the_machine(
    connected: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    app, _, fixture = connected

    ctx = app.state.chat.tool_context(set_id=fixture.set_id, run_id=None)

    assert isinstance(ctx.drafts, DraftProposals)
    assert ctx.starting is app.state.starting
    assert machine_paths(ctx, label="chat") == []
    # The runner itself, too: it is what builds a context per run.
    assert machine_paths(app.state.chat, label="runner") == []


@pytest.fixture
async def machine_busy(env: EnvSettings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    """The app with both machine writes actually in flight, and held there.

    A cleanup run and a notes send are the two background tasks whose coroutines
    hold the device client, and they only exist while somebody is deleting or
    sending. The walk has to be run while they are: a graph checked with nothing
    running would miss exactly the tasks this arrangement is about. Both are
    stopped inside the client method they call, so neither finishes until the
    test lets it.
    """
    device = build_archive_device(SMALL_COUNT, header_only=False)
    await device.start()
    await seed_settings(env, gaggimateHost=device.address, gaggimateTimeoutSeconds=5)
    release = asyncio.Event()
    try:
        async with running_app(env) as (app, _client):
            machine = app.state.connection.client
            assert await machine.wait_connected(5.0), "the fake machine did not connect"
            await app.state.connection.engine.sync_shots(trigger="test")
            await app.state.settings_service.apply(
                {
                    "deviceWritesEnabled": True,
                    "deviceCleanupMode": "keep_newest",
                    "deviceCleanupKeepNewest": SMALL_COUNT - 3,
                }
            )
            app.state.cleanup.pace_seconds = 0.0

            deleting = asyncio.Event()
            sending = asyncio.Event()

            async def held_delete(shot_id: Any) -> None:
                deleting.set()
                await release.wait()

            async def held_save(shot_id: Any, notes: Any) -> None:
                sending.set()
                await release.wait()

            monkeypatch.setattr(machine, "delete_shot", held_delete)
            monkeypatch.setattr(machine, "save_shot_notes", held_save)

            shot = await ShotsRepository(app.state.db).get_by_device_id(pad6(FIRST_ID))
            assert shot is not None
            await JudgementsRepository(app.state.db).upsert(
                shot.id, JudgementWrite(rating=4, dose_in_g=18.0, dose_out_g=36.5)
            )
            plan = await app.state.cleanup.plan()
            assert plan.planned, "the policy must want something deleted for this to mean anything"

            tasks = app.state.connection.tasks
            assert app.state.cleanup.spawn(tasks, plan) is True
            assert app.state.notes_writeback.spawn_push(tasks, [shot.id]) is True
            async with asyncio.timeout(10):
                await deleting.wait()
                await sending.wait()
            assert {cleanup_task_name(), writeback_task_name()} <= set(tasks.names)

            try:
                yield app
            finally:
                release.set()
                await tasks.cancel([cleanup_task_name(), writeback_task_name()])
    finally:
        release.set()
        await device.stop()


async def test_nothing_reaches_the_machine_while_both_machine_writes_are_running(
    machine_busy: FastAPI,
    tmp_path: Path,
) -> None:
    """The three contexts, checked with the machine's own registry at its fullest.

    Four sync loops, a cleanup run stopped inside `delete_shot` and a notes send
    stopped inside `save_shot_notes` — every coroutine frame in the process that
    holds the client exists right now, and none of them is reachable from what a
    model drives.
    """
    app = machine_busy
    ctx = app.state.chat.tool_context(set_id=None, run_id=None)

    assert machine_paths(ctx, label="chat") == []
    assert machine_paths(app.state.chat, label="runner") == []
    # The stdio server's context over the same live archive, for the same reason.
    stdio = stdio_tool_context(app.state.db, app.state.settings_service, set_id=None)
    assert machine_paths(stdio, label="stdio") == []
    # And the control still holds with everything running: the walk is looking.
    assert machine_paths(app.state.drafts, label="drafts") != []


async def test_the_walk_follows_a_task_into_its_coroutine_frame(
    connected: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """The control for the half the walk used to skip, and the reason for the split.

    One task on the registry the tools are handed, holding the connection the
    way the sync engine's loops hold theirs, and the chat's context leads
    straight to it: `app.state.tasks` -> the task -> its coroutine -> the
    frame's locals. Nothing private is touched on the way, which is why keeping
    machine work off that registry is a design rule and not a tidy-up.
    """
    app, _, _ = connected
    running = asyncio.Event()

    async def talks_to_the_machine(connection: DeviceConnection[Any]) -> None:
        running.set()
        await asyncio.Event().wait()

    app.state.tasks.spawn("test-machine-task", talks_to_the_machine(app.state.connection))
    try:
        await asyncio.wait_for(running.wait(), 5.0)

        paths = machine_paths(app.state.chat.tool_context(set_id=None, run_id=None), label="chat")

        assert any(path.endswith("DeviceConnection") for path in paths), paths
    finally:
        await app.state.tasks.cancel(["test-machine-task"])


async def test_the_walk_finds_the_machine_through_the_connections_own_registry(
    connected: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """The second control: handing tools the wrong registry is caught, not missed.

    The connection's registry holds the sync engine's four loops, and each loop
    is a bound method of the engine. Wiring it into a context — the mistake this
    whole arrangement exists to prevent — is found through the same frames.
    """
    app, _, _ = connected
    ctx = app.state.chat.tool_context(set_id=None, run_id=None)
    assert app.state.connection.tasks.names, (
        "the sync loops must be running for this to mean anything"
    )

    ctx.tasks = app.state.connection.tasks

    paths = machine_paths(ctx, label="chat")
    assert any(path.endswith("SyncEngine") for path in paths), paths


async def test_nothing_the_stdio_server_hands_a_tool_reaches_the_machine(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "gaggiclanker.db")
    await db.connect()
    try:
        await run_migrations(db)
        settings = SettingsService(SettingsRepository(db))

        ctx = stdio_tool_context(db, settings, set_id=None)

        assert isinstance(ctx.drafts, DraftProposals)
        assert machine_paths(ctx, label="stdio") == []
    finally:
        await db.close()


#: Module prefixes that put a machine within reach of whatever loaded them: the
#: client and its write gate, the sync engine, and the draft service that
#: pushes. `gaggiclanker.infra.outbound` is deliberately not here — it is how
#: `gaggiclanker.settings` refuses to let the environment smuggle a credential
#: into an outbound request, every command needs the settings, and it knows
#: nothing about the machine.
DEVICE_MODULE_PREFIXES = (
    "gaggiclanker.device",
    "gaggiclanker.sync",
    "gaggiclanker.drafts.service",
    "gaggiclanker.drafts.gate",
    "gaggiclanker.imports.service",
    "gaggiclanker.main",
)

#: Printed by each probe below and compared with ``[]``.
_REPORT = (
    "import sys, json\n"
    f"_bad = {DEVICE_MODULE_PREFIXES!r}\n"
    "print(json.dumps(sorted(m for m in sys.modules if m.startswith(_bad))))\n"
)


def _probe(source: str) -> list[str]:
    """Run ``source`` in a fresh interpreter and read back the module list it printed.

    A separate process every time, because this test's own imports would
    otherwise answer the question for it: by the time pytest has collected this
    file the device layer is loaded in *this* interpreter.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", source + _REPORT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return list(json.loads(result.stdout.strip().splitlines()[-1]))


def test_the_chat_and_the_stdio_server_do_not_import_the_device_layer() -> None:
    """Importing what a tool call needs must not load what a machine write needs."""
    loaded = _probe(
        "import gaggiclanker.tools, gaggiclanker.tools.mcp.stdio, gaggiclanker.chat.runner\n"
        "import gaggiclanker.drafts.proposals, gaggiclanker.starting.service\n"
    )

    assert loaded == []


def test_the_mcp_command_itself_does_not_import_the_device_layer() -> None:
    """The real entry point, not only the module the chat's provider points at.

    ``python -m gaggiclanker mcp`` goes through ``__main__``, which registers
    every subcommand's arguments to build one parser — so a module imported for
    the sake of ``gaggiclanker import`` is loaded by the MCP server too, and the
    chat spawns one of those per turn. The probe runs the actual module with the
    actual argument vector and stubs only ``asyncio.run``, so everything the
    command imports has been imported by the time it would have served: the
    parser, the dispatch and the coroutine are all real.
    """
    loaded = _probe(
        "import asyncio, runpy, sys\n"
        "asyncio.run = lambda coro, **kwargs: (coro.close(), 0)[1]\n"
        "sys.argv = ['gaggiclanker', 'mcp']\n"
        # The module ends in `sys.exit(main())`, which is the process exiting
        # before anything could be reported; caught so the report still runs.
        "try:\n"
        "    runpy.run_module('gaggiclanker', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
    )

    assert loaded == []


async def _await_named(app: FastAPI, name: str) -> None:
    """Let a queued run reach its stored outcome before the app shuts down.

    Through the app's registry rather than through ``ctx.tasks``: a context only
    spawns, and reading a task back out of one is the thing a tool must not be
    able to do.
    """
    task = app.state.tasks.get(name)
    if task is not None:
        async with asyncio.timeout(10):
            await asyncio.gather(task, return_exceptions=True)


async def test_every_propose_tool_still_works_from_the_chat_context(
    connected: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    app, client, fixture = connected
    ctx = app.state.chat.tool_context(set_id=fixture.set_id, run_id=None)
    proposers = {spec.name for spec in registry.specs(frozenset({"propose"}))}
    assert proposers == {
        "draft_profile",
        "propose_set_version",
        "record_insight",
        "run_analysis",
        "starting_point",
    }

    drafted = await registry.dispatch(
        ctx,
        "draft_profile",
        {
            "base_version_id": fixture.profile_version_id,
            "patch": {"temperature": 92},
            "reason": "A degree cooler.",
        },
    )
    assert drafted.ok, drafted.data
    stored = await ProfileDraftsRepository(app.state.db).get(drafted.data["draft_id"])
    assert stored is not None and stored.status == "draft"
    # The draft the chat proposed is the one the route-facing service reads.
    response = await client.get(f"/api/profile-drafts/{stored.id}")
    assert response.status_code == 200

    versioned = await registry.dispatch(
        ctx, "propose_set_version", {"reason": "Half a gram more.", "dose_g": 18.5}
    )
    assert versioned.ok, versioned.data

    learned = await registry.dispatch(
        ctx,
        "record_insight",
        {"text": "This grinder wants finer for naturals.", "grinder_id": fixture.grinder_id},
    )
    assert learned.ok, learned.data
    assert learned.data["confirmed"] is False

    analysed = await registry.dispatch(ctx, "run_analysis", {"shot_id": fixture.shots[-1]})
    assert analysed.ok, analysed.data
    await _await_named(app, analysis_task_name(fixture.shots[-1]))

    started = await registry.dispatch(
        ctx, "starting_point", {"bean_id": fixture.bean_id, "grinder_id": fixture.grinder_id}
    )
    assert started.ok, started.data
    await _await_named(app, starting_point_task_name(fixture.bean_id, fixture.grinder_id))


async def test_the_stdio_context_proposes_drafts_and_says_what_it_cannot_queue(
    tmp_path: Path,
) -> None:
    """Drafting needs the archive and the bounds; queueing a provider call needs the app."""
    db = Database(tmp_path / "gaggiclanker.db")
    await db.connect()
    try:
        await run_migrations(db)
        fixture = await build_fixture(db)
        settings = SettingsService(SettingsRepository(db))
        ctx = stdio_tool_context(db, settings, set_id=fixture.set_id)

        drafted = await registry.dispatch(
            ctx,
            "draft_profile",
            {"base_version_id": fixture.profile_version_id, "patch": {}, "reason": "As it is."},
        )
        assert drafted.ok, drafted.data
        assert (
            await registry.dispatch(ctx, "propose_set_version", {"reason": "More.", "dose_g": 19})
        ).ok
        assert (await registry.dispatch(ctx, "record_insight", {"text": "Noted."})).ok

        for name, arguments in (
            ("run_analysis", {"shot_id": fixture.shots[-1]}),
            ("starting_point", {"bean_id": fixture.bean_id}),
        ):
            outcome = await registry.dispatch(ctx, name, arguments)
            assert outcome.status == "error", name
            assert "running gaggiclanker application" in outcome.error
    finally:
        await db.close()
