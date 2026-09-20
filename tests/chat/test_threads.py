"""What a conversation is about: general, or one version of one Set.

The rules are the repository's, so they are asserted there and once more
through the routes, where a refusal has to arrive as a 422 naming the field
rather than as a stack trace. The one that is easy to get wrong is the third
test here: a conversation keeps the version it was created on, so a folder
reads as a history of what was argued rather than as a pile of rooms all
claiming to be about today's recipe.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.sets import RollbackWrite, SetsRepository, SetVersionPatch
from gaggiclanker.settings import EnvSettings
from tests.analyzer.conftest import Fixture, build_fixture
from tests.conftest import running_app


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "threads.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def archive(db: Database) -> Fixture:
    return await build_fixture(db)


def body(response: httpx.Response) -> Any:
    payload = response.json()
    assert payload["ok"], payload
    return payload["data"]


async def test_a_thread_with_no_set_is_general(archive: Fixture) -> None:
    result = await ChatRepository(archive.db).create_thread(ChatThreadWrite())

    assert result.thread is not None
    assert result.thread.set_id is None
    assert result.thread.set_version_id is None


async def test_a_set_thread_is_filed_under_the_current_version(archive: Fixture) -> None:
    sets = SetsRepository(archive.db)
    current = await sets.current_version(archive.set_id)
    assert current is not None

    result = await ChatRepository(archive.db).create_thread(ChatThreadWrite(set_id=archive.set_id))

    assert result.thread is not None
    assert result.thread.set_version_id == current.id
    assert result.thread.set_version_no == current.version_no


async def test_a_thread_keeps_its_version_when_the_set_moves_on(archive: Fixture) -> None:
    """The room where one change was argued, not a window onto the latest one."""
    sets = SetsRepository(archive.db)
    before = await sets.current_version(archive.set_id)
    assert before is not None
    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite(set_id=archive.set_id))
    assert created.thread is not None

    await sets.add_version(archive.set_id, SetVersionPatch(intent="two clicks finer"))

    still = await ChatRepository(archive.db).get_thread(created.thread.id)
    assert still is not None
    assert still.set_version_id == before.id


async def test_a_named_version_has_to_belong_to_the_set(archive: Fixture) -> None:
    sets = SetsRepository(archive.db)
    other = await sets.create(
        *_other_set(archive),
        activate=False,
    )
    other_version = await sets.current_version(other.id)
    assert other_version is not None

    result = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(set_id=archive.set_id, set_version_id=other_version.id)
    )

    assert result.thread is None
    assert result.refused == "no_version"


async def test_a_version_without_its_set_is_refused(archive: Fixture) -> None:
    current = await SetsRepository(archive.db).current_version(archive.set_id)
    assert current is not None

    result = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(set_version_id=current.id)
    )

    assert result.refused == "version_without_set"


async def test_a_set_that_does_not_exist_is_refused(archive: Fixture) -> None:
    result = await ChatRepository(archive.db).create_thread(ChatThreadWrite(set_id=9999))

    assert result.refused == "no_set"


async def test_open_continues_the_most_recent_thread_of_a_version(archive: Fixture) -> None:
    repo = ChatRepository(archive.db)
    first = await repo.create_thread(ChatThreadWrite(set_id=archive.set_id))
    second = await repo.create_thread(ChatThreadWrite(set_id=archive.set_id))
    assert first.thread is not None
    assert second.thread is not None

    latest = await repo.open_thread(archive.set_id)
    assert latest.thread is not None
    assert latest.thread.id == second.thread.id

    # The room somebody was last in, not the one made last. The sleep is the
    # clock's granularity, not a wait for anything: `updated_at` is written to
    # the millisecond, and a touch inside the same one is not "later".
    await asyncio.sleep(0.005)
    await repo.touch_thread(first.thread.id)

    opened = await repo.open_thread(archive.set_id)

    assert opened.thread is not None
    assert opened.thread.id == first.thread.id


async def test_open_creates_one_when_the_version_has_none(archive: Fixture) -> None:
    repo = ChatRepository(archive.db)
    current = await SetsRepository(archive.db).current_version(archive.set_id)
    assert current is not None

    opened = await repo.open_thread(archive.set_id)

    assert opened.thread is not None
    assert opened.thread.set_version_id == current.id
    # And a second press lands in the same room rather than making another.
    again = await repo.open_thread(archive.set_id)
    assert again.thread is not None
    assert again.thread.id == opened.thread.id


async def test_a_dead_end_version_says_so_on_the_thread(archive: Fixture) -> None:
    """A roll back steps over a version, and the folder has to grey its rooms."""
    sets = SetsRepository(archive.db)
    first = await sets.current_version(archive.set_id)
    assert first is not None
    stepped_over = await sets.add_version(archive.set_id, SetVersionPatch(intent="finer"))
    assert stepped_over is not None
    repo = ChatRepository(archive.db)
    on_the_branch = await repo.create_thread(ChatThreadWrite(set_id=archive.set_id))
    assert on_the_branch.thread is not None
    assert on_the_branch.thread.dead_end is False

    rolled = await sets.rollback(archive.set_id, RollbackWrite(to_version_id=first.id))
    assert rolled.version is not None

    listed = {row.id: row for row in await repo.list_threads()}
    assert listed[on_the_branch.thread.id].dead_end is True


async def test_deleting_a_set_takes_its_conversations_with_it(archive: Fixture) -> None:
    """Fail closed. `SET NULL` would turn them into general conversations.

    Nothing deletes a Set through the API — they are archived — so this is
    about what the schema guarantees if anything ever does: the scope is the
    thread's two columns, and a thread that lost them would be a general
    conversation holding one coffee's whole history.
    """
    repo = ChatRepository(archive.db)
    scoped = await repo.create_thread(ChatThreadWrite(set_id=archive.set_id))
    general = await repo.create_thread(ChatThreadWrite())
    assert scoped.thread is not None
    assert general.thread is not None

    await archive.db.execute("DELETE FROM sets WHERE id = ?", (archive.set_id,))

    assert await repo.get_thread(scoped.thread.id) is None
    assert await repo.get_thread(general.thread.id) is not None


async def test_both_halves_of_the_scope_cascade(archive: Fixture) -> None:
    """Each column on its own, which the behaviour above cannot separate.

    Deleting a Set already takes its versions with it, so a thread would go
    even if only one of the two references cascaded — and the one that did not
    would be the one that, some other day, leaves a thread half scoped.
    """
    sql = await archive.db.fetch_value(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'chat_threads'"
    )
    assert isinstance(sql, str)

    references = [line.strip() for line in sql.splitlines() if "REFERENCES" in line]

    assert len(references) == 2, references
    assert all("ON DELETE CASCADE" in line for line in references), references


def _other_set(archive: Fixture) -> tuple[Any, Any]:
    from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite

    return (
        SetWrite(name="Another bag", bean_id=archive.bean_id),
        SetVersionWrite(grind_setting="20"),
    )


# -- through the routes ----------------------------------------------------


@pytest.fixture
async def app_client(env: EnvSettings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, Fixture]]:
    async with running_app(env) as (app, client):
        yield app, client, await build_fixture(app.state.db)


async def test_the_route_files_a_new_thread_under_the_current_version(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    app, client, fixture = app_client
    current = await SetsRepository(app.state.db).current_version(fixture.set_id)
    assert current is not None

    created = body(await client.post("/api/chat/threads", json={"set_id": fixture.set_id}))

    assert created["set_version_id"] == current.id
    assert created["set_version_no"] == current.version_no
    assert created["dead_end"] is False


async def test_the_list_and_the_detail_both_carry_the_version_and_the_dead_end(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """The folder labels a row from the list and the card from the detail."""
    _app, client, fixture = app_client
    created = body(await client.post("/api/chat/threads", json={"set_id": fixture.set_id}))

    listed = body(await client.get("/api/chat/threads"))
    detail = body(await client.get(f"/api/chat/threads/{created['id']}"))

    assert listed[0]["set_version_no"] == created["set_version_no"]
    assert listed[0]["dead_end"] is False
    assert detail["thread"]["set_version_no"] == created["set_version_no"]
    assert detail["thread"]["dead_end"] is False


async def test_the_served_version_stays_the_thread_s_after_the_set_moves_on(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """Served, not just stored: the folder labels the row from what it is sent."""
    app, client, fixture = app_client
    created = body(await client.post("/api/chat/threads", json={"set_id": fixture.set_id}))

    await SetsRepository(app.state.db).add_version(
        fixture.set_id, SetVersionPatch(intent="two clicks finer")
    )

    listed = body(await client.get("/api/chat/threads"))
    detail = body(await client.get(f"/api/chat/threads/{created['id']}"))

    assert listed[0]["set_version_id"] == created["set_version_id"]
    assert listed[0]["set_version_no"] == created["set_version_no"]
    assert detail["thread"]["set_version_no"] == created["set_version_no"]


async def test_the_route_answers_422_for_a_version_of_another_set(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    _app, client, fixture = app_client

    response = await client.post(
        "/api/chat/threads",
        json={"set_id": fixture.set_id, "set_version_id": 9999},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "set_version_id"


async def test_open_is_the_route_discuss_presses(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    _app, client, fixture = app_client

    first = body(await client.post("/api/chat/threads/open", json={"set_id": fixture.set_id}))
    again = body(await client.post("/api/chat/threads/open", json={"set_id": fixture.set_id}))

    assert first["id"] == again["id"]
    assert first["set_version_id"] == again["set_version_id"]
