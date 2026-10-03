"""A Set with graded versions, shots, conversations and insights to rest on, for the insight tests.

Real database, real repositories. Set A has v1 and v2 graded ("held") and v3 open
(current), one shot on each, and two conversations; Set B is a stranger with one
graded version, one shot and one conversation. Nothing here is a mock: what the
tests pin is mostly a constraint or a read inside the transaction that writes.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.insight_deletions import InsightDeletionsRepository
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.knowledge_insights import InsightsRepository, InsightWrite
from gaggiclanker.db.repos.sets import (
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
    VersionOutcomeWrite,
    VersionPredictionWrite,
)
from tests.sets.conftest import make_shot


@dataclass(slots=True)
class ProvenanceWorld:
    db: Database
    sets: SetsRepository
    insights: InsightsRepository
    deletions: InsightDeletionsRepository
    set_id: int
    v1: int
    v2: int
    v3: int
    shot1: int
    shot2: int
    shot3: int
    thread: int
    other_thread: int
    stranger_set: int
    stranger_version: int
    stranger_thread: int

    async def own(
        self,
        text: str,
        *,
        confirmed: bool = True,
        version_id: int | None = None,
        set_id: int | None = None,
        thread_id: int | None = None,
        shots: list[int] | None = None,
    ) -> int:
        """A Set insight written straight into the table (a seeded, answered one)."""
        return await self.insights.insert(
            InsightWrite(
                text=text,
                source="chat",
                confirmed=confirmed,
                set_id=self.set_id if set_id is None else set_id,
                set_version_id=self.v2 if version_id is None else version_id,
                thread_id=thread_id,
                evidence_shot_ids=shots or [],
            )
        )

    async def regrade(self, version_id: int, outcome: str) -> None:
        await self.sets.set_outcome(
            self.set_id, version_id, VersionOutcomeWrite.model_validate({"outcome": outcome})
        )


async def _graded(
    sets: SetsRepository, db: Database, set_id: int, version_id: int, device_id: str, outcome: str
) -> int:
    await sets.set_prediction(
        set_id,
        version_id,
        VersionPredictionWrite.model_validate(
            {"prediction": "Expect about 30 s.", "compares_to_version_id": None}
        ),
    )
    shot = await make_shot(db, device_id)
    assert await sets.assign_shot(shot, version_id)
    await JudgementsRepository(db).upsert(shot, JudgementWrite.model_validate({"decision": "keep"}))
    await sets.set_outcome(
        set_id, version_id, VersionOutcomeWrite.model_validate({"outcome": outcome})
    )
    return shot


async def build_provenance_world(db: Database) -> ProvenanceWorld:
    bean = await BeansRepository(db).create(BeanWrite(name="Guji", roast_level="light"))
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
    )
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="A", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    assert row.current_version_id is not None
    v1 = row.current_version_id
    shot1 = await _graded(sets, db, row.id, v1, "000601", "held")
    second = await sets.add_version(
        row.id,
        SetVersionPatch.model_validate({"grind_setting": "21", "prediction": "Expect 32 s."}),
    )
    assert second is not None
    shot2 = await _graded(sets, db, row.id, second.id, "000602", "held")
    third = await sets.add_version(
        row.id,
        SetVersionPatch.model_validate({"grind_setting": "20", "prediction": "Expect 34 s."}),
    )
    assert third is not None
    shot3 = await make_shot(db, "000603")
    assert await sets.assign_shot(shot3, third.id)

    stranger = await sets.create(
        SetWrite(name="B", bean_id=bean.id, grinder_id=grinder.id),
        SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
    )
    assert stranger.current_version_id is not None
    await _graded(sets, db, stranger.id, stranger.current_version_id, "000604", "failed")

    chat = ChatRepository(db)
    threads = []
    for set_id in (row.id, row.id, stranger.id):
        made = await chat.create_thread(ChatThreadWrite(set_id=set_id))
        assert made.thread is not None
        threads.append(made.thread.id)
    return ProvenanceWorld(
        db=db,
        sets=sets,
        insights=InsightsRepository(db),
        deletions=InsightDeletionsRepository(db),
        set_id=row.id,
        v1=v1,
        v2=second.id,
        v3=third.id,
        shot1=shot1,
        shot2=shot2,
        shot3=shot3,
        thread=threads[0],
        other_thread=threads[1],
        stranger_set=stranger.id,
        stranger_version=stranger.current_version_id,
        stranger_thread=threads[2],
    )


async def rows_containing(db: Database, text: str) -> list[str]:
    """Every row in the whole database with ``text`` in any text cell, as ``table#rowid``.

    A dump-style check: "removed means removed" is a claim about the file, not about
    the tables one remembered to look in.
    """
    found: list[str] = []
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    for table in tables:
        name = table["name"]
        rows: list[Any] = []
        # Virtual or WITHOUT ROWID tables have nothing to search here.
        with contextlib.suppress(Exception):
            rows = await db.fetch_all(f'SELECT rowid AS _rid, * FROM "{name}"')  # noqa: S608
        for row in rows:
            if any(isinstance(cell, str) and text in cell for cell in tuple(row)[1:]):
                found.append(f"{name}#{row['_rid']}")
    return found
