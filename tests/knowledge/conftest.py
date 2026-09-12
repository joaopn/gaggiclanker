"""A database with tier 2 and tier 3 in it, and a tiny corpus to test against.

Two fixtures, because the tests want two different things. `seeded_docs` is the
**real** shipped corpus — twenty-five documents, thirty thousand words — and it
is what the search, bound and seeding tests use, because a chunker that behaves
on hand-written markdown and falls over on the real files would pass a suite
that only fed it hand-written markdown. `small_docs` is three files written for
this suite, used where a test needs to know exactly what is in the index.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.knowledge.service import KnowledgeService

#: Three documents whose every word is known to the tests here. The headings
#: repeat on purpose (two "Fixes" sections) so the duplicate-slug rule is
#: exercised, and one section is deliberately tiny so the merge is too.
SMALL_DOCS: dict[str, str] = {
    "SOUR_SHOTS.md": """# Sour Shots

Sour espresso is under-extracted espresso.

## Causes

A sour shot is running too fast for the grind it was pulled at. Water leaves
before the sugars do, so what reaches the cup is the acids that come out first
and nothing behind them to balance them. The usual culprits are a grind that is
too coarse, a brew temperature that is too low, and a yield that was cut short.

## Fixes

Grind finer by one or two steps and pull it again. If the shot time is already
where you want it, raise the brew temperature by a degree instead, or take five
more grams of yield. Change one of the three, never two.
""",
    "BITTER_SHOTS.md": """# Bitter Shots

Bitter espresso is over-extracted espresso.

## Causes

A bitter shot has run too long, too hot, or through a grind that was too fine
for the basket. The bitterness creeps up after the sip and dries the mouth,
which is how it is told apart from sourness — sourness hits at once and fades.

## Fixes

Grind coarser, drop the brew temperature by a degree, or cut the yield by five
grams. A declining pressure phase at the end helps a dark roast that turns
harsh in the last few seconds.
""",
    "CHANNELING.md": """# Channeling

## What channeling is

Channeling is water finding a path of least resistance through the puck. Part
of the coffee is over-extracted and part of it is not extracted at all, which is
why a channeled shot tastes sour and bitter at the same time. Grinding finer
makes it worse, because a tighter puck raises the pressure that is driving the
channel in the first place.

## Table of indicators

| Indicator | What it means |
|---|---|
| Pressure cliff | A channel opened mid-shot |
| Flow acceleration | The same, seen from the pump |
| Early first drip | The puck never loaded evenly |

## Short note

Two aligned indicators mean a channel.
""",
}


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "knowledge.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def small_dir(tmp_path: Path) -> Path:
    """:data:`SMALL_DOCS` on disk, as a seed directory."""
    directory = tmp_path / "docs"
    directory.mkdir()
    for name, text in SMALL_DOCS.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


@pytest.fixture
async def knowledge(db: Database) -> KnowledgeService:
    """The service over an empty database. Seed it in the test that needs it."""
    return KnowledgeService(db)


@pytest.fixture
async def small(knowledge: KnowledgeService, small_dir: Path) -> KnowledgeService:
    """The service with the three small documents seeded."""
    await knowledge.seed_docs(small_dir)
    return knowledge


@pytest.fixture
async def seeded_docs(knowledge: KnowledgeService) -> KnowledgeService:
    """The service with the real shipped corpus seeded."""
    await knowledge.seed_docs()
    return knowledge
