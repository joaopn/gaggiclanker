"""Three Sets on one grinder, two of them sharing a roast level, for the pattern tests.

Real database, real repositories. What is shared is deliberate, so the scope rule has
something to bite on:

* ``a`` (Guji: light, natural, Ethiopia) and ``b`` (Yirgacheffe: light, washed, Ethiopia)
  share the grinder, the roast level and the origin, and not the bean or the process;
* ``c`` (Huila: dark, washed, Colombia) shares only the grinder;
* ``designing`` is a Set still being designed: it never counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.knowledge_insights import (
    InsightScope,
    InsightsRepository,
    InsightWrite,
    RestsOn,
)
from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetVersionWrite, SetWrite


@dataclass(slots=True)
class PatternWorld:
    db: Database
    insights: InsightsRepository
    sets: SetsRepository
    grinder_id: int
    bean_a: int
    bean_b: int
    bean_c: int
    a: int
    b: int
    c: int
    designing: int

    async def own(self, set_id: int, text: str, *, confirmed: bool = True) -> int:
        """A confirmed (or waiting) insight of one Set, written straight into the table."""
        row = await self.sets.get(set_id)
        assert row is not None
        return await self.insights.insert(
            InsightWrite(
                text=text,
                source="chat",
                confirmed=confirmed,
                set_id=set_id,
                set_version_id=row.current_version_id,
            )
        )

    async def general(self, text: str, **scope: object) -> int:
        return await self.insights.insert(
            InsightWrite(
                text=text, scope=InsightScope.model_validate(scope), source="user", confirmed=True
            )
        )


async def build_pattern_world(db: Database) -> PatternWorld:
    beans = BeansRepository(db)
    guji = await beans.create(
        BeanWrite(
            name="Guji",
            roaster="Hasbean",
            roast_level="light",
            process="natural",
            origin="Ethiopia",
        )
    )
    yirg = await beans.create(
        BeanWrite(
            name="Yirgacheffe",
            roaster="Hasbean",
            roast_level="light",
            process="washed",
            origin="Ethiopia",
        )
    )
    huila = await beans.create(
        BeanWrite(
            name="Huila",
            roaster="Square Mile",
            roast_level="dark",
            process="washed",
            origin="Colombia",
        )
    )
    grinder = await GrindersRepository(db).create(
        GrinderWrite(name="Niche Zero", burr_type="conical", step_unit="numbers")
    )
    sets = SetsRepository(db)
    made = []
    for name, bean_id in (("Guji daily", guji.id), ("Yirg daily", yirg.id), ("Huila", huila.id)):
        made.append(
            await sets.create(
                SetWrite(name=name, bean_id=bean_id, grinder_id=grinder.id),
                SetVersionWrite(dose_g=18, target_yield_g=36, grind_setting="22"),
            )
        )
    draft = await sets.create_design(
        SetWrite(name="Being designed", bean_id=guji.id, grinder_id=grinder.id), DesignBrief()
    )
    return PatternWorld(
        db=db,
        insights=InsightsRepository(db),
        sets=sets,
        grinder_id=grinder.id,
        bean_a=guji.id,
        bean_b=yirg.id,
        bean_c=huila.id,
        a=made[0].id,
        b=made[1].id,
        c=made[2].id,
        designing=draft.id,
    )


@dataclass(slots=True)
class Talking:
    """The world once its Sets have said things: ids by name."""

    world: PatternWorld
    #: A's and B's two phrasings of one finding about the grinder.
    a_grinder: int
    b_grinder: int
    #: C's own lesson, and A's one that only A would say.
    c_grinder: int
    a_only: int
    #: A waiting one and one in the Set being designed: neither is ever read.
    waiting: int
    designing: int
    general: int


async def build_talking(world: PatternWorld) -> Talking:
    """Insights with known ids, a version outcome to rest on, and one general insight."""
    version = await world.sets.get(world.a)
    assert version is not None and version.current_version_id is not None
    await world.db.execute(
        "UPDATE set_versions SET outcome = 'held' WHERE id = ?", (version.current_version_id,)
    )
    a_grinder = await world.insights.insert(
        InsightWrite(
            text="Below 9 clicks the Niche channels on this bag.",
            source="chat",
            confirmed=True,
            set_id=world.a,
            set_version_id=version.current_version_id,
            rests_on=[RestsOn(set_version_id=version.current_version_id, outcome="held")],
        )
    )
    a_only = await world.own(world.a, "This bag peaks at 93 degrees.")
    b_grinder = await world.own(world.b, "The Niche gushes under 9 with this one.")
    c_grinder = await world.own(
        world.c, "Anything finer than 9 on the Niche channels for this dark roast."
    )
    waiting = await world.own(world.b, "Not confirmed yet.", confirmed=False)
    designing = await world.own(world.designing, "Still being designed.")
    general = await world.general("Rinse the portafilter between shots.")
    return Talking(
        world=world,
        a_grinder=a_grinder,
        b_grinder=b_grinder,
        c_grinder=c_grinder,
        a_only=a_only,
        waiting=waiting,
        designing=designing,
        general=general,
    )
