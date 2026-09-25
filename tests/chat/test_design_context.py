"""The opening context of a conversation designing a Set, and the goldens it renders to.

Two goldens, because the design context has two shapes worth pinning whole: a
Set forked from a profile, of a bean that already has Sets, with a card waiting
(`golden/design-chat-context.txt`), and a Set of a bean nobody has brewed,
forked from nothing (`golden/design-chat-context-bare.txt`). The other tests
each pin one rule: whose Sets may appear, and when the block goes away.

The archive is the starting point's (`tests/starting/conftest.py`): four beans
dialled in on two grinders, which is exactly the evidence a design is built on.

Regenerate with `uv run pytest tests/chat -k golden --update-golden` and read
the diff: that diff is what the model would have been told differently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.chat.context import opening_context
from gaggiclanker.chat.design_context import design_context
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.tools.scope import ToolScope
from tests.sets.conftest import make_profile_version
from tests.starting.conftest import Fixture, build_fixture

GOLDEN = Path(__file__).resolve().parent / "golden"


@pytest.fixture
async def kitchen(tmp_path: Path) -> AsyncIterator[Fixture]:
    db = Database(tmp_path / "design.db")
    await db.connect()
    await run_migrations(db)
    try:
        yield await build_fixture(db, seed_knowledge=False)
    finally:
        await db.close()


async def _bean_of(kitchen: Fixture, label: str) -> int:
    row = await SetsRepository(kitchen.db).get(kitchen.sets[label])
    assert row is not None
    return row.bean_id


async def _forked_design(kitchen: Fixture) -> SetRow:
    """A second Set of the Kenya AA, forked from the library profile, with a card waiting."""
    sets = SetsRepository(kitchen.db)
    bean_id = await _bean_of(kitchen, "kenya")
    # A sibling on the other grinder that is still in use, with two versions.
    mazzer = await sets.create(
        SetWrite(
            name="Kenya AA on the Mazzer", bean_id=bean_id, grinder_id=kitchen.other_grinder_id
        ),
        SetVersionWrite(
            profile_version_id=kitchen.profile_version_id,
            grind_setting="7",
            dose_g=20,
            target_yield_g=44,
            intent="Same bag, flat burrs.",
        ),
        automatch=False,
    )
    await sets.add_version(
        mazzer.id,
        SetVersionPatch(
            grind_setting="6.5",
            intent="A touch finer.",
            prediction="Compared to v1, two seconds longer and sweeter.",
        ),
    )
    # And one that is finished with: archived Sets are not part of the record.
    archived = await sets.create(
        SetWrite(name="Kenya AA, last year", bean_id=bean_id), SetVersionWrite(dose_g=17)
    )
    await sets.archive(archived.id)

    designed = await sets.create_design(
        SetWrite(name="Kenya AA, more body", bean_id=bean_id, grinder_id=kitchen.grinder_id),
        DesignBrief(
            fork_profile_version_id=kitchen.profile_version_id,
            usual_grind="21",
            goal="More body than the Niche Set, and keep the blackcurrant.",
        ),
    )
    drafted = await make_profile_version(kitchen.db, "Kenya AA body [AI]", temperature=94)
    draft = await ProfileDraftsRepository(kitchen.db).create(
        ProfileDraftWrite(base_version_id=kitchen.profile_version_id, draft_version_id=drafted)
    )
    stored = await SetProposalsRepository(kitchen.db).create(
        designed.id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            reason="A longer bloom and a lower peak for body.",
            patch=SetVersionPatch(
                profile_version_id=drafted,
                grind_setting="20",
                grind_value=20,
                dose_g=18,
                target_yield_g=40,
            ),
        ),
    )
    assert stored.proposal is not None, stored.refused
    return designed


async def _bare_design(kitchen: Fixture) -> SetRow:
    return await SetsRepository(kitchen.db).create_design(
        SetWrite(
            name="Kenya Nyeri on the Niche Zero",
            bean_id=kitchen.new_bean_id,
            grinder_id=kitchen.grinder_id,
        ),
        DesignBrief(),
    )


def _check_golden(rendered: str, name: str, update_golden: bool) -> None:
    path = GOLDEN / name
    if update_golden:
        path.write_text(rendered + "\n", encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert path.exists(), "run with --update-golden to create it"
    assert rendered + "\n" == path.read_text(encoding="utf-8")


async def test_the_forked_design_with_siblings_matches_its_golden(
    kitchen: Fixture, update_golden: bool
) -> None:
    designed = await _forked_design(kitchen)

    rendered = await opening_context(kitchen.db, await ToolScope.resolve(kitchen.db, designed.id))

    _check_golden(rendered, "design-chat-context.txt", update_golden)


async def test_the_bare_design_matches_its_golden(kitchen: Fixture, update_golden: bool) -> None:
    designed = await _bare_design(kitchen)

    rendered = await opening_context(kitchen.db, await ToolScope.resolve(kitchen.db, designed.id))

    _check_golden(rendered, "design-chat-context-bare.txt", update_golden)


async def test_the_library_is_never_offered_as_somewhere_to_start(kitchen: Fixture) -> None:
    """The only profile a design starts from is the one the person picked to fork."""
    forked = await design_context(kitchen.db, (await _forked_design(kitchen)).id)
    bare = await design_context(kitchen.db, (await _bare_design(kitchen)).id)

    for rendered in (forked, bare):
        assert "THE PROFILE LIBRARY" not in rendered
    fork_block = bare.split("THE PROFILE TO FORK")[1].split("THE RECIPE")[0]
    assert "written from zero" in fork_block
    assert "Do not start from a profile in their library" in fork_block
    # The keys a whole document needs, read off the schema, and no document.
    assert "preinfusion|brew" in fork_block
    assert "```" not in fork_block
    assert "written from zero" not in forked


async def test_the_same_archive_renders_the_same_text(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)

    first = await design_context(kitchen.db, designed.id)
    second = await design_context(kitchen.db, designed.id)

    assert first == second


async def test_only_this_bean_s_sets_in_use_are_its_siblings(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)

    rendered = await design_context(kitchen.db, designed.id)
    siblings = rendered.split("THIS BEAN'S OTHER SETS")[1].split("SIMILAR SETS")[0]

    assert "Kenya AA on the niche" in siblings
    assert "Kenya AA on the Mazzer" in siblings
    # Archived: finished with, and not part of the record.
    assert "last year" not in rendered
    # Another bean's Set is never a sibling, and this bean's are never
    # "similar": they are listed once, where they belong.
    assert "Ethiopia Guji" not in siblings
    similar = rendered.split("SIMILAR SETS ON THIS GRINDER")[1].split("THE RULES THAT MATCH")[0]
    assert "Kenya AA" not in similar
    assert "Ethiopia Guji" in similar
    # And the Set being designed is not its own sibling.
    assert "Kenya AA, more body —" not in siblings


async def test_the_siblings_are_capped_and_the_cut_is_said(kitchen: Fixture) -> None:
    from gaggiclanker.chat.design_context import SIBLING_SETS

    sets = SetsRepository(kitchen.db)
    bean_id = await _bean_of(kitchen, "kenya")
    for index in range(SIBLING_SETS + 1):
        await sets.create(
            SetWrite(name=f"Kenya AA try {index}", bean_id=bean_id), SetVersionWrite(dose_g=18)
        )
    designed = await sets.create_design(
        SetWrite(name="Kenya AA designed", bean_id=bean_id, grinder_id=kitchen.grinder_id),
        DesignBrief(),
    )

    rendered = await design_context(kitchen.db, designed.id)

    listed = [line for line in rendered.splitlines() if line.startswith("- Set ")]
    assert len(listed) == SIBLING_SETS
    # Newest first: the last one created is the first one listed.
    assert f"Kenya AA try {SIBLING_SETS}" in listed[0]
    assert "- and 2 older Sets of this bean." in rendered


async def test_once_the_recipe_is_written_the_design_block_is_gone(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    await SetsRepository(kitchen.db).add_version(
        designed.id, SetVersionPatch(grind_setting="20", dose_g=18, target_yield_g=40)
    )

    rendered = await opening_context(kitchen.db, await ToolScope.resolve(kitchen.db, designed.id))

    assert rendered.startswith("THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET")
    assert "THIS BEAN'S OTHER SETS" not in rendered
    assert "Kenya AA on the Mazzer" not in rendered
