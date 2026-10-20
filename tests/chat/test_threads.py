"""What a conversation is about: general, or one version of one Recipe.

The rules are the repository's, so they are asserted there and once more
through the routes, where a refusal has to arrive as a 422 naming the field
rather than as a stack trace. The one that is easy to get wrong is the third
test here: a conversation keeps the version it was created on, so a Recipe's list
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
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.recipes import RecipesRepository, RecipeVersionPatch, RollbackWrite
from gaggiclanker.db.schema import create_schema
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app
from tests.review.conftest import Fixture, build_fixture


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "threads.db")
    await database.connect()
    await create_schema(database)
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


async def test_a_thread_with_no_recipe_is_general(archive: Fixture) -> None:
    result = await ChatRepository(archive.db).create_thread(ChatThreadWrite())

    assert result.thread is not None
    assert result.thread.recipe_id is None
    assert result.thread.recipe_version_id is None


async def test_a_recipe_thread_is_filed_under_the_current_version(archive: Fixture) -> None:
    recipes = RecipesRepository(archive.db)
    current = await recipes.current_version(archive.recipe_id)
    assert current is not None

    result = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(recipe_id=archive.recipe_id)
    )

    assert result.thread is not None
    assert result.thread.recipe_version_id == current.id
    assert result.thread.recipe_version_label == current.version_label


async def test_a_thread_keeps_its_version_when_the_recipe_moves_on(archive: Fixture) -> None:
    """The room where one change was argued, not a window onto the latest one."""
    recipes = RecipesRepository(archive.db)
    before = await recipes.current_version(archive.recipe_id)
    assert before is not None
    created = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(recipe_id=archive.recipe_id)
    )
    assert created.thread is not None

    await recipes.add_version(archive.recipe_id, RecipeVersionPatch(intent="two clicks finer"))

    still = await ChatRepository(archive.db).get_thread(created.thread.id)
    assert still is not None
    assert still.recipe_version_id == before.id


async def test_a_named_version_has_to_belong_to_the_recipe(archive: Fixture) -> None:
    recipes = RecipesRepository(archive.db)
    other = await recipes.create(
        *_other_recipe(archive),
        automatch=False,
    )
    other_version = await recipes.current_version(other.id)
    assert other_version is not None

    result = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(recipe_id=archive.recipe_id, recipe_version_id=other_version.id)
    )

    assert result.thread is None
    assert result.refused == "no_version"


async def test_a_version_without_its_recipe_is_refused(archive: Fixture) -> None:
    current = await RecipesRepository(archive.db).current_version(archive.recipe_id)
    assert current is not None

    result = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(recipe_version_id=current.id)
    )

    assert result.refused == "version_without_recipe"


async def test_a_recipe_that_does_not_exist_is_refused(archive: Fixture) -> None:
    result = await ChatRepository(archive.db).create_thread(ChatThreadWrite(recipe_id=9999))

    assert result.refused == "no_recipe"


async def test_open_continues_the_most_recent_thread_of_a_version(archive: Fixture) -> None:
    repo = ChatRepository(archive.db)
    first = await repo.create_thread(ChatThreadWrite(recipe_id=archive.recipe_id))
    second = await repo.create_thread(ChatThreadWrite(recipe_id=archive.recipe_id))
    assert first.thread is not None
    assert second.thread is not None

    latest = await repo.open_thread(archive.recipe_id)
    assert latest.thread is not None
    assert latest.thread.id == second.thread.id

    # The room somebody was last in, not the one made last. The sleep is the
    # clock's granularity, not a wait for anything: `updated_at` is written to
    # the millisecond, and a touch inside the same one is not "later".
    await asyncio.sleep(0.005)
    await repo.touch_thread(first.thread.id)

    opened = await repo.open_thread(archive.recipe_id)

    assert opened.thread is not None
    assert opened.thread.id == first.thread.id


async def test_open_creates_one_when_the_version_has_none(archive: Fixture) -> None:
    repo = ChatRepository(archive.db)
    current = await RecipesRepository(archive.db).current_version(archive.recipe_id)
    assert current is not None

    opened = await repo.open_thread(archive.recipe_id)

    assert opened.thread is not None
    assert opened.thread.recipe_version_id == current.id
    # And a second press lands in the same room rather than making another.
    again = await repo.open_thread(archive.recipe_id)
    assert again.thread is not None
    assert again.thread.id == opened.thread.id


async def test_a_dead_end_version_says_so_on_the_thread(archive: Fixture) -> None:
    """A revert goes back past a version, and the Recipe's list has to grey its rooms."""
    recipes = RecipesRepository(archive.db)
    first = await recipes.current_version(archive.recipe_id)
    assert first is not None
    stepped_over = await recipes.add_version(archive.recipe_id, RecipeVersionPatch(intent="finer"))
    assert stepped_over is not None
    repo = ChatRepository(archive.db)
    on_the_branch = await repo.create_thread(ChatThreadWrite(recipe_id=archive.recipe_id))
    assert on_the_branch.thread is not None
    assert on_the_branch.thread.dead_end is False

    rolled = await recipes.rollback(archive.recipe_id, RollbackWrite(to_version_id=first.id))
    assert rolled.version is not None

    listed = {row.id: row for row in await repo.list_threads()}
    assert listed[on_the_branch.thread.id].dead_end is True


async def test_after_a_revert_the_targets_conversation_is_the_live_one(
    archive: Fixture,
) -> None:
    """One conversation per version stays: going back reopens that version's room."""
    recipes = RecipesRepository(archive.db)
    first = await recipes.current_version(archive.recipe_id)
    assert first is not None
    repo = ChatRepository(archive.db)
    on_first = await repo.open_thread(archive.recipe_id)
    assert on_first.thread is not None and on_first.thread.recipe_version_id == first.id
    second = await recipes.add_version(archive.recipe_id, RecipeVersionPatch(intent="finer"))
    assert second is not None
    on_second = await repo.open_thread(archive.recipe_id)
    assert on_second.thread is not None and on_second.thread.recipe_version_id == second.id

    await recipes.rollback(archive.recipe_id, RollbackWrite(to_version_id=first.id))

    live = await repo.open_thread(archive.recipe_id)
    assert live.thread is not None and live.thread.id == on_first.thread.id
    fresh = await repo.create_thread(ChatThreadWrite(recipe_id=archive.recipe_id))
    assert fresh.thread is not None and fresh.thread.recipe_version_id == first.id
    # The conversation left behind is on a dead end now.
    listed = {row.id: row for row in await repo.list_threads()}
    assert listed[on_second.thread.id].dead_end is True
    assert listed[on_first.thread.id].dead_end is False


async def test_deleting_a_recipe_takes_its_conversations_with_it(archive: Fixture) -> None:
    """Fail closed. `SET NULL` would turn them into general conversations.

    Nothing deletes a Recipe through the API — they are archived — so this is
    about what the schema guarantees if anything ever does: the scope is the
    thread's two columns, and a thread that lost them would be a general
    conversation holding one coffee's whole history.
    """
    repo = ChatRepository(archive.db)
    scoped = await repo.create_thread(ChatThreadWrite(recipe_id=archive.recipe_id))
    general = await repo.create_thread(ChatThreadWrite())
    assert scoped.thread is not None
    assert general.thread is not None

    await archive.db.execute("DELETE FROM recipes WHERE id = ?", (archive.recipe_id,))

    assert await repo.get_thread(scoped.thread.id) is None
    assert await repo.get_thread(general.thread.id) is not None


async def test_both_halves_of_the_scope_cascade(archive: Fixture) -> None:
    """Each column on its own, which the behaviour above cannot separate.

    Deleting a Recipe already takes its versions with it, so a thread would go
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


def _other_recipe(archive: Fixture) -> tuple[Any, Any]:
    from gaggiclanker.db.repos.recipes import RecipeVersionWrite, RecipeWrite

    return (
        RecipeWrite(name="Another bag", bean_id=archive.bean_id),
        RecipeVersionWrite(grind_setting="20"),
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
    current = await RecipesRepository(app.state.db).current_version(fixture.recipe_id)
    assert current is not None

    created = body(await client.post("/api/chat/threads", json={"recipe_id": fixture.recipe_id}))

    assert created["recipe_version_id"] == current.id
    assert created["recipe_version_label"] == current.version_label
    assert created["dead_end"] is False


async def test_the_list_and_the_detail_both_carry_the_version_and_the_dead_end(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """The Chat page labels a row from the list and the card from the detail."""
    _app, client, fixture = app_client
    created = body(await client.post("/api/chat/threads", json={"recipe_id": fixture.recipe_id}))

    listed = body(await client.get("/api/chat/threads"))
    detail = body(await client.get(f"/api/chat/threads/{created['id']}"))

    assert listed[0]["recipe_version_label"] == created["recipe_version_label"]
    assert listed[0]["dead_end"] is False
    assert detail["thread"]["recipe_version_label"] == created["recipe_version_label"]
    assert detail["thread"]["dead_end"] is False


async def test_the_served_version_stays_the_thread_s_after_the_recipe_moves_on(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    """Served, not just stored: the Chat page labels the row from what it is sent."""
    app, client, fixture = app_client
    created = body(await client.post("/api/chat/threads", json={"recipe_id": fixture.recipe_id}))

    await RecipesRepository(app.state.db).add_version(
        fixture.recipe_id, RecipeVersionPatch(intent="two clicks finer")
    )

    listed = body(await client.get("/api/chat/threads"))
    detail = body(await client.get(f"/api/chat/threads/{created['id']}"))

    assert listed[0]["recipe_version_id"] == created["recipe_version_id"]
    assert listed[0]["recipe_version_label"] == created["recipe_version_label"]
    assert detail["thread"]["recipe_version_label"] == created["recipe_version_label"]


async def test_the_route_answers_422_for_a_version_of_another_recipe(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    _app, client, fixture = app_client

    response = await client.post(
        "/api/chat/threads",
        json={"recipe_id": fixture.recipe_id, "recipe_version_id": 9999},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "recipe_version_id"


async def test_open_is_the_route_discuss_presses(
    app_client: tuple[FastAPI, httpx.AsyncClient, Fixture],
) -> None:
    _app, client, fixture = app_client

    first = body(await client.post("/api/chat/threads/open", json={"recipe_id": fixture.recipe_id}))
    again = body(await client.post("/api/chat/threads/open", json={"recipe_id": fixture.recipe_id}))

    assert first["id"] == again["id"]
    assert first["recipe_version_id"] == again["recipe_version_id"]
