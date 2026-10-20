"""The opening context of a conversation designing a Recipe, and the goldens it renders to.

Two goldens, because the design context has two shapes worth pinning whole: a
Recipe forked from a profile, of a bean that already has Recipes, with a card waiting
(`golden/design-chat-context.txt`), and a Recipe of a bean nobody has brewed,
forked from nothing (`golden/design-chat-context-bare.txt`). The other tests
each pin one rule: whose Recipes may appear, and when the block goes away.

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
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
from gaggiclanker.db.repos.recipe_proposals import ProposalWrite, RecipeProposalsRepository
from gaggiclanker.db.repos.recipes import (
    DesignBrief,
    RecipeRow,
    RecipesRepository,
    RecipeVersionPatch,
    RecipeVersionWrite,
    RecipeWrite,
)
from gaggiclanker.db.schema import create_schema
from gaggiclanker.tools.scope import ToolScope
from tests.recipes.conftest import make_profile_version
from tests.starting.conftest import Fixture, build_fixture

GOLDEN = Path(__file__).resolve().parent / "golden"


@pytest.fixture
async def kitchen(tmp_path: Path) -> AsyncIterator[Fixture]:
    db = Database(tmp_path / "design.db")
    await db.connect()
    await create_schema(db)
    try:
        yield await build_fixture(db, seed_knowledge=False)
    finally:
        await db.close()


async def _bean_of(kitchen: Fixture, label: str) -> int:
    row = await RecipesRepository(kitchen.db).get(kitchen.recipes[label])
    assert row is not None
    return row.bean_id


async def _forked_design(kitchen: Fixture) -> RecipeRow:
    """A second Recipe of the Kenya AA, forked from the library profile, with a card waiting."""
    recipes = RecipesRepository(kitchen.db)
    bean_id = await _bean_of(kitchen, "kenya")
    # A sibling on the other grinder that is still in use, with two versions.
    mazzer = await recipes.create(
        RecipeWrite(
            name="Kenya AA on the Mazzer", bean_id=bean_id, grinder_id=kitchen.other_grinder_id
        ),
        RecipeVersionWrite(
            profile_version_id=kitchen.profile_version_id,
            grind_setting="7",
            dose_g=20,
            target_yield_g=44,
            intent="Same bag, flat burrs.",
        ),
        automatch=False,
    )
    await recipes.add_version(
        mazzer.id,
        RecipeVersionPatch(
            grind_setting="6.5",
            intent="A touch finer.",
            prediction="Compared to v1, two seconds longer and sweeter.",
        ),
    )
    # And one that is finished with: archived Recipes are not part of the record.
    archived = await recipes.create(
        RecipeWrite(name="Kenya AA, last year", bean_id=bean_id), RecipeVersionWrite(dose_g=17)
    )
    await recipes.archive(archived.id)

    designed = await recipes.create_design(
        RecipeWrite(name="Kenya AA, more body", bean_id=bean_id, grinder_id=kitchen.grinder_id),
        DesignBrief(
            fork_profile_version_id=kitchen.profile_version_id,
            usual_grind="21",
            goal="More body than the Niche Recipe, and keep the blackcurrant.",
            dose_g=18,
            target_yield_g=40,
        ),
    )
    drafted = await make_profile_version(kitchen.db, "Kenya AA body [AI]", temperature=94)
    draft = await ProfileDraftsRepository(kitchen.db).create(
        ProfileDraftWrite(base_version_id=kitchen.profile_version_id, draft_version_id=drafted)
    )
    stored = await RecipeProposalsRepository(kitchen.db).create(
        designed.id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            reason="A longer bloom and a lower peak for body.",
            patch=RecipeVersionPatch(
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


async def _bare_design(kitchen: Fixture) -> RecipeRow:
    return await RecipesRepository(kitchen.db).create_design(
        RecipeWrite(
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


async def test_the_numbers_the_person_chose_are_kept_and_the_rest_left_open(
    kitchen: Fixture,
) -> None:
    """A grind, dose or yield typed on the form is the person's, and the agent must keep it."""
    recipes = RecipesRepository(kitchen.db)
    bean_id = await _bean_of(kitchen, "kenya")

    async def brief_of(brief: DesignBrief) -> str:
        designed = await recipes.create_design(
            RecipeWrite(name=f"Kenya {brief!r}", bean_id=bean_id, grinder_id=kitchen.grinder_id),
            brief,
        )
        rendered = await design_context(kitchen.db, designed.id)
        return rendered.split("WHAT THEY ASKED FOR")[1].split("THE PROFILE TO FORK")[0]

    every = await brief_of(DesignBrief(grind_setting="18", dose_g=18.5, target_yield_g=40))
    assert (
        "Numbers they chose: grind 18 (numbers), dose 18.5 g, target yield 40 g. "
        "Propose exactly these" in every
    )
    assert "work out only what they left open" in every

    # Only the ones filled in are named, so an empty field is never read as a zero.
    some = await brief_of(DesignBrief(target_yield_g=36))
    assert "Numbers they chose: target yield 36 g. Propose exactly these" in some
    assert "dose" not in some.split("Numbers they chose:")[1].split(".")[0]

    none = await brief_of(DesignBrief())
    assert "Numbers they chose: none. The grind, dose and yield are yours to propose." in (none)
    assert "Propose exactly these" not in none


async def test_the_same_archive_renders_the_same_text(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)

    first = await design_context(kitchen.db, designed.id)
    second = await design_context(kitchen.db, designed.id)

    assert first == second


async def test_only_this_bean_s_recipes_in_use_are_its_siblings(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)

    rendered = await design_context(kitchen.db, designed.id)
    siblings = rendered.split("THIS BEAN'S OTHER RECIPES")[1].split("SIMILAR RECIPES")[0]

    assert "Kenya AA on the niche" in siblings
    assert "Kenya AA on the Mazzer" in siblings
    # Archived: finished with, and not part of the record.
    assert "last year" not in rendered
    # Another bean's Recipe is never a sibling, and this bean's are never
    # "similar": they are listed once, where they belong.
    assert "Ethiopia Guji" not in siblings
    similar = rendered.split("SIMILAR RECIPES ON THIS GRINDER")[1].split("THE RULES THAT MATCH")[0]
    assert "Kenya AA" not in similar
    assert "Ethiopia Guji" in similar
    # And the Recipe being designed is not its own sibling.
    assert "Kenya AA, more body —" not in siblings


async def test_the_siblings_are_capped_and_the_cut_is_said(kitchen: Fixture) -> None:
    from gaggiclanker.chat.design_context import SIBLING_RECIPES

    recipes = RecipesRepository(kitchen.db)
    bean_id = await _bean_of(kitchen, "kenya")
    for index in range(SIBLING_RECIPES + 1):
        await recipes.create(
            RecipeWrite(name=f"Kenya AA try {index}", bean_id=bean_id),
            RecipeVersionWrite(dose_g=18),
        )
    designed = await recipes.create_design(
        RecipeWrite(name="Kenya AA designed", bean_id=bean_id, grinder_id=kitchen.grinder_id),
        DesignBrief(),
    )

    rendered = await design_context(kitchen.db, designed.id)

    listed = [line for line in rendered.splitlines() if line.startswith("- Recipe ")]
    assert len(listed) == SIBLING_RECIPES
    # Newest first: the last one created is the first one listed.
    assert f"Kenya AA try {SIBLING_RECIPES}" in listed[0]
    assert "- and 2 older Recipes of this bean." in rendered


async def test_once_the_recipe_is_written_the_design_block_is_gone(kitchen: Fixture) -> None:
    designed = await _forked_design(kitchen)
    await RecipesRepository(kitchen.db).add_version(
        designed.id, RecipeVersionPatch(grind_setting="20", dose_g=18, target_yield_g=40)
    )

    rendered = await opening_context(kitchen.db, await ToolScope.resolve(kitchen.db, designed.id))

    assert rendered.startswith("THE PROFILE v")
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE RECIPE" in rendered
    assert "THIS BEAN'S OTHER RECIPES" not in rendered
    assert "Kenya AA on the Mazzer" not in rendered


async def test_the_design_is_told_what_the_forked_profile_is_for_and_what_it_proposed(
    kitchen: Fixture,
) -> None:
    from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
    from gaggiclanker.db.repos.signatures import SignatureRepository
    from gaggiclanker.domain.signature import ExpectationInput
    from gaggiclanker.signatures.service import SignatureService

    designed = await _forked_design(kitchen)
    created = await ChatRepository(kitchen.db).create_thread(
        ChatThreadWrite(title="design", recipe_id=designed.id)
    )
    assert created.thread is not None
    thread = created.thread.id
    profile_id = kitchen.profile_version_id
    plain = await design_context(kitchen.db, designed.id, thread_id=thread)
    assert "This profile version has no signature in force" in plain
    assert f"propose_signature (profile version {profile_id})" in plain

    first, second = await SignatureService(kitchen.db).propose(
        profile_id,
        [
            ExpectationInput(
                tier="critical", kind="free_text", text="the bloom holds", fault="unstable"
            ),
            ExpectationInput(
                tier="context", kind="free_text", text="the finish tapers", fault="unstable"
            ),
        ],
        reason="what the fork is for",
        thread_id=thread,
    )
    repo = SignatureRepository(kitchen.db)
    # In force at once: told to every reader of the design, and to the proposer as its own.
    told = await design_context(kitchen.db, designed.id, thread_id=thread)
    assert "the bloom holds (in force unless the person rejects it" in told
    assert "This profile version has no signature in force" not in told
    assert "the bloom holds" in await design_context(kitchen.db, designed.id)

    await repo.reject(second.id, reason="not for a fork")
    answered = await design_context(kitchen.db, designed.id, thread_id=thread)
    assert f"What profile version {profile_id} is FOR" in answered
    assert (
        f"- #{first.id} critical, whole shot, free_text "
        "(checked by the review, fails as unstable): the bloom holds" in answered
    )
    assert 'the finish tapers (rejected by the person. They said: "not for a fork"' in answered
    # The signature in force is told to every reader of the design, the rejection to nobody else.
    other = await design_context(kitchen.db, designed.id)
    assert "the bloom holds" in other and "the finish tapers" not in other
