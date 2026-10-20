"""A Set's "copied from" note is a record for the person's page; no agent ever reads it.

The new Set's chat context, every tool output that describes a Set and the MCP Set resource are
rendered for a Set that was started as a copy, and none of them names the Set it came from or
says it was copied. The insights that were copied are another matter: they are the new Set's own
confirmed insights and are read as such.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.chat.context import opening_context
from gaggiclanker.chat.design_context import design_context
from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.schema import create_schema
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.mcp.server import SERVER_NAME
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.review.conftest import Fixture, build_fixture
from tests.tools.mcp.test_stdio import session_for

SOURCE_NAME = "Zebra Source Roast"
COPY_NAME = "Fresh Start"
COPIED_TEXT = "Grind finer than 2.3 runs sour on this bag"


@dataclass(slots=True)
class Copied:
    db: Database
    fixture: Fixture
    new_set_id: int
    new_version_id: int


async def _make(db: Database) -> Copied:
    fixture = await build_fixture(db)
    await db.execute("UPDATE recipes SET name = ? WHERE id = ?", (SOURCE_NAME, fixture.set_id))
    sets = SetsRepository(db)
    async with db.transaction():
        new_id = await sets.create_in_transaction(
            SetWrite(name=COPY_NAME, bean_id=fixture.bean_id, grinder_id=fixture.grinder_id),
            SetVersionWrite(dose_g=18, target_yield_g=36),
            copied_from_version_id=fixture.version_id,
        )
        await InsightsRepository(db).insert(
            InsightWrite(text=COPIED_TEXT, source="chat", confirmed=True, set_id=new_id)
        )
    current = await sets.current_version(new_id)
    assert current is not None
    return Copied(db, fixture, new_id, current.id)


@pytest.fixture
async def copied(tmp_path: Path) -> AsyncIterator[Copied]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    await create_schema(db)
    yield await _make(db)
    await db.close()


def _clean(text: str) -> None:
    lowered = text.lower()
    assert SOURCE_NAME.lower() not in lowered
    assert "copied from" not in lowered and "copied_from" not in lowered


async def test_the_note_is_served_to_the_person_so_the_test_proves_something(
    copied: Copied,
) -> None:
    row = await SetsRepository(copied.db).get(copied.new_set_id)
    assert row is not None and row.copied_from is not None
    assert row.copied_from.set_name == SOURCE_NAME
    assert "copied_from" in row.model_dump(mode="json")


async def test_the_new_sets_chat_context_never_says_where_it_came_from(copied: Copied) -> None:
    scope = ToolScope.for_thread(copied.new_set_id, copied.new_version_id)

    told = await opening_context(copied.db, scope)

    _clean(told)
    assert COPY_NAME in told
    # What was copied is the Set's own confirmed insight, read as one.
    assert COPIED_TEXT in told


async def test_a_design_context_beside_it_never_says_it_either(copied: Copied) -> None:
    sets = SetsRepository(copied.db)
    designing = await sets.create_design(
        SetWrite(
            name="Designing", bean_id=copied.fixture.bean_id, grinder_id=copied.fixture.grinder_id
        ),
        DesignBrief(),
    )

    told = await design_context(copied.db, designing.id)

    assert "copied from" not in told.lower() and "copied_from" not in told.lower()


async def test_every_tool_output_that_describes_a_set_leaves_the_note_out(
    copied: Copied,
) -> None:
    ctx = ToolContext(
        db=copied.db,
        settings=SettingsService(SettingsRepository(copied.db)),
        knowledge=KnowledgeService(copied.db),
        scope=ToolScope(),
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )
    outputs: dict[str, Any] = {}
    for name, arguments in (
        ("get_set", {"set_id": copied.new_set_id}),
        ("list_sets", {"include_archived": True}),
    ):
        outcome = await registry.dispatch(ctx, name, arguments)
        assert outcome.ok, outcome.data
        outputs[name] = json.dumps(outcome.data)

    _clean(outputs["get_set"])
    # The list names every Set, the source included, so only the note itself is absent.
    assert "copied from" not in outputs["list_sets"].lower()
    assert "copied_from" not in outputs["list_sets"] and COPY_NAME in outputs["list_sets"]
    assert COPIED_TEXT in outputs["get_set"]


async def test_the_mcp_set_resource_leaves_the_note_out(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    await create_schema(db)
    made = await _make(db)
    await db.close()

    async with AsyncExitStack() as stack:
        session = await session_for(stack, data_dir)
        resource = await session.read_resource(f"{SERVER_NAME}://sets/{made.new_set_id}")

    text = "".join(str(getattr(part, "text", "")) for part in resource.contents)
    assert COPY_NAME in text
    _clean(text)
