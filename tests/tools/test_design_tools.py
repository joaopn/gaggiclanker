"""The two tools a design conversation adds: `get_profile` and `propose_initial_recipe`.

`get_profile` is the read that lets an agent fork a profile it has actually
seen, in all three kinds of conversation, with the archive-wide shot count kept
out of a Set's. `propose_initial_recipe` is the design's one proposal: its base
falls back in a stated order, its profile must be new, a policy refusal or a
not-new refusal leaves nothing behind, a newer card retires the older one and
its draft, and the grind is a number only when it is absolute and parses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import SYNTHETIC_BASE_LABEL, ProfilesRepository
from gaggiclanker.db.repos.set_proposals import SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.knowledge.service import KnowledgeService
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, ToolContext, ToolOutcome, registry
from gaggiclanker.tools.scope import ToolScope
from tests.analyzer.conftest import Fixture

#: A recipe the tool accepts, for the tests that are about something else.
RECIPE: dict[str, Any] = {
    "profile": {"label": "Designed in chat", "patch": {"temperature": 92}},
    "grind_setting": "20",
    "grind_is_absolute": True,
    "dose_g": 18,
    "target_yield_g": 40,
    "reason": "A cooler, longer shot for more body.",
}


def _context(db: Database, scope: ToolScope, thread_id: int | None = None) -> ToolContext:
    settings = SettingsService(SettingsRepository(db))
    return ToolContext(
        db=db,
        settings=settings,
        knowledge=KnowledgeService(db),
        drafts=DraftProposals(db, settings),
        scope=scope,
        thread_id=thread_id,
        caller="test",
        permissions=CHAT_PERMISSIONS,
    )


async def _designed(db: Database, bean_id: int, grinder_id: int, **brief: Any) -> SetRow:
    return await SetsRepository(db).create_design(
        SetWrite(name="Kenya, designed", bean_id=bean_id, grinder_id=grinder_id),
        DesignBrief.model_validate(brief),
    )


async def _design_ctx(db: Database, row: SetRow, *, with_thread: bool = False) -> ToolContext:
    thread_id = None
    if with_thread:
        opened = await ChatRepository(db).open_thread(row.id)
        assert opened.thread is not None
        thread_id = opened.thread.id
    return _context(db, await ToolScope.resolve(db, row.id), thread_id)


async def _propose(ctx: ToolContext, **over: Any) -> ToolOutcome:
    return await registry.dispatch(ctx, "propose_initial_recipe", {**RECIPE, **over})


async def _draft_count(db: Database) -> int:
    return len(await ProfileDraftsRepository(db).list_drafts(limit=1000))


# -- get_profile -----------------------------------------------------------------


async def test_get_profile_returns_the_whole_document_and_what_it_states(
    archive: Fixture,
) -> None:
    ctx = _context(archive.db, ToolScope())

    outcome = await registry.dispatch(
        ctx, "get_profile", {"profile_version_id": archive.profile_version_id}
    )

    assert outcome.ok, outcome.data
    data = outcome.data
    stored = await ProfilesRepository(archive.db).get_version(archive.profile_version_id)
    assert stored is not None
    assert (data["label"], data["type"]) == (stored.label, stored.type)
    assert data["document"]["phases"], "the whole document, phases and all"
    assert data["document"]["label"] == stored.label
    assert stored.profile is not None
    assert data["recipe"]["temperature_c"] == stored.profile["temperature"]
    # The whole archive's shots on it: a general conversation may know that.
    assert data["shot_count"] == len(archive.shots)


@pytest.mark.parametrize("designing", [False, True])
async def test_get_profile_in_a_set_s_conversation_keeps_the_archive_s_count_out(
    archive: Fixture, designing: bool
) -> None:
    scope = ToolScope.for_thread(archive.set_id, designing=designing)
    ctx = _context(archive.db, scope)

    outcome = await registry.dispatch(
        ctx, "get_profile", {"profile_version_id": archive.profile_version_id}
    )

    assert outcome.ok, outcome.data
    assert outcome.data["shot_count"] is None


async def test_get_profile_says_a_missing_version_is_missing(archive: Fixture) -> None:
    outcome = await registry.dispatch(
        _context(archive.db, ToolScope()), "get_profile", {"profile_version_id": 999_999}
    )

    assert not outcome.ok
    assert "No profile version 999999" in outcome.data["detail"]


# -- propose_initial_recipe: what it stores --------------------------------------


async def test_the_initial_recipe_is_one_waiting_card_with_a_draft_of_its_own(
    archive: Fixture,
) -> None:
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row, with_thread=True)

    outcome = await _propose(ctx)

    assert outcome.ok, outcome.data
    data = outcome.data
    assert (data["set_id"], data["kind"], data["status"]) == (row.id, "design", "proposed")
    proposal = await SetProposalsRepository(archive.db).get(row.id, data["proposal_id"])
    assert proposal is not None
    assert (proposal.kind, proposal.draft_id, proposal.thread_id) == (
        "design",
        data["draft_id"],
        ctx.thread_id,
    )
    draft = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert draft is not None
    # A design's draft belongs to no Set and predicts nothing: pushing it for
    # the Set must not append a v2 repeating version 1.
    assert (draft.set_id, draft.prediction, draft.status) == (None, "", "draft")
    assert draft.change_summary == RECIPE["reason"]
    assert "Kenya, designed" in draft.notes
    assert data["recipe"]["profile_version_id"] == draft.draft_version_id
    assert data["recipe"]["profile_label"] == "Designed in chat [AI]"
    assert data["recipe"]["profile_temperature_c"] == 92
    assert (data["recipe"]["dose_g"], data["recipe"]["target_yield_g"]) == (18, 40)
    assert "Nothing exists yet" in data["note"]
    # And nothing else moved: the Set is still being designed.
    after = await SetsRepository(archive.db).get(row.id)
    assert after is not None and after.designing is True


@pytest.mark.parametrize(
    ("setting", "absolute", "expected"),
    [
        ("20", True, 20.0),
        ("a little finer than your usual", True, None),
        ("20", False, None),
    ],
)
async def test_the_grind_is_a_number_only_when_absolute_and_numeric(
    archive: Fixture, setting: str, absolute: bool, expected: float | None
) -> None:
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row)

    outcome = await _propose(ctx, grind_setting=setting, grind_is_absolute=absolute)

    assert outcome.ok, outcome.data
    assert outcome.data["recipe"]["grind_value"] == expected
    proposal = await SetProposalsRepository(archive.db).get(row.id, outcome.data["proposal_id"])
    assert proposal is not None and proposal.patch is not None
    assert proposal.patch.grind_setting == setting
    assert proposal.patch.grind_value == expected
    assert ("grind_value" in proposal.patch.model_fields_set) is (expected is not None)


async def test_a_newer_card_retires_the_waiting_one_and_discards_its_draft(
    archive: Fixture,
) -> None:
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row)
    first = (await _propose(ctx)).data

    # The same profile again with another dose is this design revising its
    # own card, not a copy of somebody else's profile.
    second = await _propose(ctx, dose_g=19)

    assert second.ok, second.data
    proposals = SetProposalsRepository(archive.db)
    older = await proposals.get(row.id, first["proposal_id"])
    assert older is not None and older.status == "stale"
    old_draft = await ProfileDraftsRepository(archive.db).get(first["draft_id"])
    assert old_draft is not None and old_draft.status == "discarded"
    waiting = await proposals.waiting(row.id)
    assert waiting is not None and waiting.id == second.data["proposal_id"]


# -- propose_initial_recipe: the base --------------------------------------------


async def _base_of(outcome: ToolOutcome, db: Database) -> int:
    assert outcome.ok, outcome.data
    draft = await ProfileDraftsRepository(db).get(outcome.data["draft_id"])
    assert draft is not None
    return draft.base_version_id


async def test_the_base_is_the_given_one_then_the_fork_source_then_the_library(
    archive: Fixture,
) -> None:
    profiles = ProfilesRepository(archive.db)
    stored = await profiles.get_version(archive.profile_version_id)
    assert stored is not None and stored.profile is not None
    other_doc = {**stored.profile, "label": "Another library profile", "temperature": 90}
    other, _ = await profiles.ensure_version(Profile.model_validate(other_doc))

    forked = await _designed(
        archive.db, archive.bean_id, archive.grinder_id, fork_profile_version_id=other.id
    )
    forked_ctx = await _design_ctx(archive.db, forked)
    given = await _propose(
        forked_ctx,
        profile={
            "label": "Given base",
            "base_version_id": archive.profile_version_id,
            "patch": {},
        },
    )
    assert await _base_of(given, archive.db) == archive.profile_version_id

    from_fork = await _propose(forked_ctx, profile={"label": "From the fork", "patch": {}})
    assert await _base_of(from_fork, archive.db) == other.id

    plain = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    library = await _propose(
        await _design_ctx(archive.db, plain), profile={"label": "From the library", "patch": {}}
    )
    assert await _base_of(library, archive.db) == await profiles.default_draft_base()


@pytest.fixture
async def empty_archive(tmp_path: Path) -> AsyncIterator[Database]:
    """An archive with a bean and a grinder and no profile at all."""
    db = Database(tmp_path / "empty.db")
    await db.connect()
    await run_migrations(db)
    try:
        yield db
    finally:
        await db.close()


async def test_with_an_empty_library_the_base_is_the_synthetic_baseline(
    empty_archive: Database,
) -> None:
    bean = await BeansRepository(empty_archive).create(BeanWrite(name="First bag"))
    grinder = await GrindersRepository(empty_archive).create(GrinderWrite(name="Niche"))
    row = await _designed(empty_archive, bean.id, grinder.id)

    outcome = await _propose(
        await _design_ctx(empty_archive, row), profile={"label": "My first", "patch": {}}
    )

    base = await ProfilesRepository(empty_archive).get_version(
        await _base_of(outcome, empty_archive)
    )
    assert base is not None and base.label == SYNTHETIC_BASE_LABEL


# -- propose_initial_recipe: what it refuses --------------------------------------


async def test_a_profile_the_library_already_has_is_refused_and_nothing_is_left(
    archive: Fixture,
) -> None:
    """Another design's profile is the library's: two Sets on one profile match nothing."""
    first = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    assert (await _propose(await _design_ctx(archive.db, first))).ok
    second = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    before = await _draft_count(archive.db)

    outcome = await _propose(await _design_ctx(archive.db, second))

    assert not outcome.ok
    assert "give it its own label or change something" in outcome.data["detail"]
    assert await _draft_count(archive.db) == before
    assert await SetProposalsRepository(archive.db).for_set(second.id) == []


async def test_a_policy_refusal_is_a_tool_error_and_leaves_nothing(archive: Fixture) -> None:
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    before = await _draft_count(archive.db)
    # More phases than the policy allows: a clamp cannot fix that, because
    # truncating a profile would change what it brews.
    phase = {
        "name": "Step",
        "phase": "brew",
        "valve": 1,
        "duration": 5,
        "pump": {"target": "pressure", "pressure": 9, "flow": 0},
        "targets": [{"type": "volumetric", "operator": "gte", "value": 40}],
    }
    too_many = [dict(phase, name=f"Step {index}") for index in range(12)]

    outcome = await _propose(
        await _design_ctx(archive.db, row),
        profile={"label": "Too many", "patch": {"phases": too_many}},
    )

    assert not outcome.ok
    assert outcome.status == "error"
    assert "phases" in outcome.data["detail"]
    assert await _draft_count(archive.db) == before
    assert await SetProposalsRepository(archive.db).for_set(row.id) == []


async def test_outside_a_design_the_tool_is_refused_by_the_dispatcher_and_by_itself(
    archive: Fixture,
) -> None:
    set_ctx = _context(archive.db, ToolScope.for_thread(archive.set_id))

    dispatched = await _propose(set_ctx)
    spec = registry.get("propose_initial_recipe")
    assert spec is not None
    with pytest.raises(ValueError, match="already has a recipe"):
        await spec.fn(set_ctx, spec.input_model.model_validate(RECIPE))

    assert dispatched.status == "refused"
    assert await SetProposalsRepository(archive.db).for_set(archive.set_id) == []


async def test_a_design_accepted_since_the_turn_began_is_refused(archive: Fixture) -> None:
    """The scope was read at the start of the turn; the Set is read again here."""
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row)
    await SetsRepository(archive.db).add_version(row.id, SetVersionPatch(dose_g=18))

    outcome = await _propose(ctx)

    assert not outcome.ok
    assert "already has a recipe" in outcome.data["detail"]


async def test_a_card_the_repository_refuses_takes_its_draft_with_it(archive: Fixture) -> None:
    """Refused after the draft exists: the draft is discarded, not left open."""
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    # A conversation of another Set: the repository refuses to record a card as
    # argued there, and it only finds out once the draft has been made.
    elsewhere = await ChatRepository(archive.db).open_thread(archive.set_id)
    assert elsewhere.thread is not None
    ctx = _context(archive.db, await ToolScope.resolve(archive.db, row.id), elsewhere.thread.id)
    before = {draft.id for draft in await ProfileDraftsRepository(archive.db).list_drafts()}

    outcome = await _propose(ctx)

    assert not outcome.ok
    assert "not one of this Set's" in outcome.data["detail"]
    assert await SetProposalsRepository(archive.db).for_set(row.id) == []
    made = [
        draft
        for draft in await ProfileDraftsRepository(archive.db).list_drafts()
        if draft.id not in before
    ]
    assert [draft.status for draft in made] == ["discarded"]


async def test_a_revision_may_not_keep_a_profile_another_set_has_taken_up(
    archive: Fixture,
) -> None:
    """The design's own earlier profile, once another Set brews it, is the library's."""
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row)
    first = (await _propose(ctx)).data
    proposals = SetProposalsRepository(archive.db)
    # While the card waits, a person makes a Set by hand naming its profile.
    await SetsRepository(archive.db).create(
        SetWrite(name="By hand", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        SetVersionWrite(profile_version_id=first["recipe"]["profile_version_id"]),
    )
    drafts_before = await _draft_count(archive.db)

    revised = await _propose(ctx, dose_g=19)

    assert not revised.ok
    assert "give it its own label or change something" in revised.data["detail"]
    # Nothing left behind, and the waiting card is as it was.
    assert await _draft_count(archive.db) == drafts_before
    listed = await proposals.for_set(row.id)
    assert [(card.id, card.status) for card in listed] == [(first["proposal_id"], "proposed")]
    draft = await ProfileDraftsRepository(archive.db).get(first["draft_id"])
    assert draft is not None and draft.status == "draft"


async def test_an_archived_set_on_the_profile_does_not_take_it_up(archive: Fixture) -> None:
    """An archived Set receives no shots, so it cannot make the matcher ambiguous."""
    row = await _designed(archive.db, archive.bean_id, archive.grinder_id)
    ctx = await _design_ctx(archive.db, row)
    first = (await _propose(ctx)).data
    sets = SetsRepository(archive.db)
    finished = await sets.create(
        SetWrite(name="Finished with", bean_id=archive.bean_id),
        SetVersionWrite(profile_version_id=first["recipe"]["profile_version_id"]),
    )
    await sets.archive(finished.id)

    revised = await _propose(ctx, dose_g=19)

    assert revised.ok, revised.data
