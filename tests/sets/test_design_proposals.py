"""The initial recipe: a proposal of the whole first recipe of a Set being designed.

What is pinned here is how it differs from an ordinary change and where it is
the same. It owes no prediction and carries a profile draft; it is refused on a
Set that is not being designed and a change is refused on one that is; a newer
one replaces the one waiting and discards its draft; declining discards the
draft; accepting fills version 1 in place and appends nothing.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.chat import ChatRepository
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
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
from tests.sets.conftest import Fixtures, make_profile_version, make_shot


async def designed(wired: Fixtures) -> SetRow:
    return await wired.sets.create_design(
        SetWrite(name="Guji, designed", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        DesignBrief(goal="More body."),
    )


async def a_draft(wired: Fixtures, label: str) -> tuple[int, int]:
    """A draft of a new profile: its id and the profile version it names."""
    base = await make_profile_version(wired.db, "Library profile")
    drafted = await make_profile_version(wired.db, label)
    row = await ProfileDraftsRepository(wired.db).create(
        ProfileDraftWrite(base_version_id=base, draft_version_id=drafted, change_summary="New.")
    )
    return row.id, drafted


def design(draft_id: int, profile_version_id: int, **over: object) -> ProposalWrite:
    body: dict[str, object] = {
        "kind": "design",
        "draft_id": draft_id,
        "reason": "A longer, gentler shot for more body.",
        "patch": SetVersionPatch(
            profile_version_id=profile_version_id,
            grind_setting="20",
            grind_value=20,
            dose_g=18,
            target_yield_g=40,
        ),
    }
    body.update(over)
    return ProposalWrite.model_validate(body)


async def draft_status(wired: Fixtures, draft_id: int) -> str:
    row = await ProfileDraftsRepository(wired.db).get(draft_id)
    assert row is not None
    return row.status


# -- the write model -----------------------------------------------------------


def test_a_design_needs_its_draft_and_predicts_nothing() -> None:
    with pytest.raises(ValidationError, match="carries its profile draft"):
        ProposalWrite(kind="design", reason="x", patch=SetVersionPatch(dose_g=18))
    with pytest.raises(ValidationError, match="predicts nothing"):
        ProposalWrite(
            kind="design",
            draft_id=1,
            reason="x",
            patch=SetVersionPatch(dose_g=18),
            prediction="It will be sweet, compared to nothing at all.",
        )


def test_a_change_still_needs_its_prediction_and_carries_no_draft() -> None:
    with pytest.raises(ValidationError, match="needs a prediction"):
        ProposalWrite(reason="x", patch=SetVersionPatch(dose_g=18), prediction="   ")
    with pytest.raises(ValidationError, match="carries no draft"):
        ProposalWrite(
            reason="x", patch=SetVersionPatch(dose_g=18), prediction="Faster.", draft_id=1
        )


# -- creating ------------------------------------------------------------------


async def test_an_initial_recipe_waits_on_version_1_with_no_prediction(wired: Fixtures) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)

    result = await proposals.create(row.id, design(draft_id, profile))

    stored = result.proposal
    assert stored is not None, result.refused
    assert (stored.kind, stored.draft_id, stored.status) == ("design", draft_id, "proposed")
    assert stored.base_version_id == row.current_version_id
    assert (stored.prediction, stored.compares_to_version_id) == ("", None)
    # Nothing changed: the Set is still being designed and v1 still empty.
    after = await wired.sets.get(row.id)
    assert after is not None and after.designing is True
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None and v1.profile_version_id is None


async def test_the_preview_is_version_1_as_it_would_be_filled(wired: Fixtures) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None

    preview = await proposals.preview(stored)

    assert preview is not None
    assert (preview.id, preview.version_no) == (row.current_version_id, 1)
    assert (preview.profile_version_id, preview.profile_label) == (profile, "Designed")
    assert (preview.grind_setting, preview.dose_g, preview.target_yield_g) == ("20", 18, 40)
    assert (preview.intent, preview.origin) == ("A longer, gentler shot for more body.", "chat")


async def test_a_first_recipe_is_drawn_against_an_empty_one_before_and_after_accept(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None

    accepted = (await proposals.accept(row.id, stored.id)).proposal
    assert accepted is not None and accepted.status == "accepted"
    base = await proposals.diff_base(accepted)

    # Version 1 is filled now, but the card's other side is still no recipe:
    # the row it was based on, with nothing in it.
    assert base is not None
    assert (base.id, base.version_no) == (row.current_version_id, 1)
    assert (base.profile_version_id, base.profile_label, base.profile_temperature_c) == (
        None,
        None,
        None,
    )
    assert (base.grind_setting, base.grind_value, base.dose_g, base.target_yield_g) == (
        None,
        None,
        None,
        None,
    )


async def test_a_change_is_drawn_against_the_version_it_changes(wired: Fixtures) -> None:
    row = await wired.sets.create(
        SetWrite(name="By hand", bean_id=wired.bean_id),
        SetVersionWrite(grind_setting="18", dose_g=18),
    )
    proposals = SetProposalsRepository(wired.db)
    stored = (
        await proposals.create(
            row.id,
            ProposalWrite(
                reason="Finer, for a slower shot.",
                prediction="Compared to v1, 3 to 5 s longer and less sour.",
                patch=SetVersionPatch(grind_setting="17"),
            ),
        )
    ).proposal
    assert stored is not None

    base = await proposals.diff_base(stored)

    assert base is not None
    assert (base.id, base.grind_setting, base.dose_g) == (row.current_version_id, "18", 18)


async def test_an_initial_recipe_is_refused_on_a_set_that_is_not_being_designed(
    wired: Fixtures,
) -> None:
    row = await wired.sets.create(
        SetWrite(name="By hand", bean_id=wired.bean_id), SetVersionWrite(dose_g=18)
    )
    draft_id, profile = await a_draft(wired, "Designed")

    result = await SetProposalsRepository(wired.db).create(row.id, design(draft_id, profile))

    assert result.refused == "not_designing"


async def test_a_change_is_refused_on_a_set_being_designed(wired: Fixtures) -> None:
    row = await designed(wired)

    result = await SetProposalsRepository(wired.db).create(
        row.id,
        ProposalWrite(
            reason="Finer.",
            prediction="Compared to v1, three seconds longer and less sour.",
            patch=SetVersionPatch(grind_setting="19"),
        ),
    )

    assert result.refused == "designing"


async def test_the_draft_must_be_the_profile_the_recipe_names_and_still_open(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    other = await make_profile_version(wired.db, "Something else")
    proposals = SetProposalsRepository(wired.db)

    mismatched = await proposals.create(row.id, design(draft_id, other))
    assert mismatched.refused == "bad_draft"

    await ProfileDraftsRepository(wired.db).set_status(draft_id, "discarded")
    discarded = await proposals.create(row.id, design(draft_id, profile))
    assert discarded.refused == "bad_draft"

    missing = await proposals.create(row.id, design(draft_id + 100, profile))
    assert missing.refused == "bad_draft"


async def test_a_newer_initial_recipe_replaces_the_waiting_one_and_discards_its_draft(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    first_draft, first_profile = await a_draft(wired, "First try")
    second_draft, second_profile = await a_draft(wired, "Second try")
    proposals = SetProposalsRepository(wired.db)
    first = (await proposals.create(row.id, design(first_draft, first_profile))).proposal
    assert first is not None

    second = (await proposals.create(row.id, design(second_draft, second_profile))).proposal

    assert second is not None
    retired = await proposals.get(row.id, first.id)
    assert retired is not None and retired.status == "stale"
    assert await draft_status(wired, first_draft) == "discarded"
    assert await draft_status(wired, second_draft) == "draft"
    waiting = await proposals.waiting(row.id)
    assert waiting is not None and waiting.id == second.id


async def test_a_refused_replacement_leaves_the_waiting_one_waiting(wired: Fixtures) -> None:
    """The older card is only retired once the newer one is certain to be stored."""
    row = await designed(wired)
    first_draft, first_profile = await a_draft(wired, "First try")
    second_draft, second_profile = await a_draft(wired, "Second try")
    proposals = SetProposalsRepository(wired.db)
    first = (await proposals.create(row.id, design(first_draft, first_profile))).proposal
    assert first is not None

    refused = await proposals.create(
        row.id, design(second_draft, second_profile, thread_id=999_999)
    )

    assert refused.refused == "bad_thread"
    waiting = await proposals.waiting(row.id)
    assert waiting is not None and waiting.id == first.id
    assert await draft_status(wired, first_draft) == "draft"


# -- answering -----------------------------------------------------------------


async def test_declining_an_initial_recipe_discards_its_draft(wired: Fixtures) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None

    result = await proposals.decline(row.id, stored.id, "Not that one.")

    assert result.proposal is not None and result.proposal.status == "declined"
    assert await draft_status(wired, draft_id) == "discarded"
    after = await wired.sets.get(row.id)
    assert after is not None and after.designing is True


async def test_accepting_an_initial_recipe_fills_version_1_and_appends_nothing(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None
    draft_id, profile = await a_draft(wired, "Designed")
    thread = await ChatRepository(wired.db).open_thread(row.id)
    assert thread.thread is not None
    proposals = SetProposalsRepository(wired.db)
    stored = (
        await proposals.create(row.id, design(draft_id, profile, thread_id=thread.thread.id))
    ).proposal
    assert stored is not None

    result = await proposals.accept(row.id, stored.id)

    assert result.refused is None
    assert result.proposal is not None and result.proposal.status == "accepted"
    assert result.proposal.resulting_version_id == v1.id
    version = result.version
    assert version is not None
    assert (version.id, version.version_no, version.origin) == (v1.id, 1, "chat")
    assert (version.profile_version_id, version.grind_setting, version.grind_value) == (
        profile,
        "20",
        20,
    )
    assert (version.dose_g, version.target_yield_g) == (18, 40)
    assert version.intent == "A longer, gentler shot for more body."
    assert version.prediction == ""
    after = await wired.sets.get(row.id)
    assert after is not None
    assert (after.designing, after.version_count) == (False, 1)
    # The draft is the recipe's profile now: it waits for approval, not the bin.
    assert await draft_status(wired, draft_id) == "draft"
    # And the log links version 1 back to the conversation it was argued in.
    assert await proposals.accepted_threads(row.id) == {v1.id: thread.thread.id}


async def test_discuss_on_version_1_leaves_the_design_conversation_once_accepted(
    wired: Fixtures,
) -> None:
    """One conversation is one version's work, and the design was that work.

    While the Set is being designed, opening version 1 continues the design;
    once its card is accepted the agent tells the person to start a new
    conversation, and Discuss must agree with it rather than land back in the
    design. A conversation about version 1 started after that is continued.
    """
    row = await designed(wired)
    chat = ChatRepository(wired.db)
    draft_id, profile = await a_draft(wired, "Designed")
    design_room = (await chat.open_thread(row.id)).thread
    assert design_room is not None
    again = (await chat.open_thread(row.id)).thread
    assert again is not None and again.id == design_room.id
    proposals = SetProposalsRepository(wired.db)
    stored = (
        await proposals.create(row.id, design(draft_id, profile, thread_id=design_room.id))
    ).proposal
    assert stored is not None
    # Still designing: the card waits, and the design is still the room.
    waiting = (await chat.open_thread(row.id)).thread
    assert waiting is not None and waiting.id == design_room.id

    assert (await proposals.accept(row.id, stored.id)).refused is None

    fresh = (await chat.open_thread(row.id)).thread
    assert fresh is not None and fresh.id != design_room.id
    assert fresh.set_version_id == design_room.set_version_id
    continued = (await chat.open_thread(row.id)).thread
    assert continued is not None and continued.id == fresh.id


async def test_discuss_still_continues_an_ordinary_conversation_with_a_change_in_it(
    wired: Fixtures,
) -> None:
    """Only a first-recipe card marks the design: a change argued in a room keeps it."""
    row = await wired.sets.create(
        SetWrite(name="Guji", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(dose_g=18, target_yield_g=36),
    )
    chat = ChatRepository(wired.db)
    room = (await chat.open_thread(row.id)).thread
    assert room is not None
    stored = (
        await SetProposalsRepository(wired.db).create(
            row.id,
            ProposalWrite(
                reason="Longer.",
                patch=SetVersionPatch(target_yield_g=40),
                prediction="Compared to v1, sweeter and 3 s longer.",
                thread_id=room.id,
            ),
        )
    ).proposal
    assert stored is not None

    again = (await chat.open_thread(row.id)).thread
    assert again is not None and again.id == room.id


async def test_accepting_is_refused_once_a_shot_is_filed_on_the_design(wired: Fixtures) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None
    assert row.current_version_id is not None
    shot = await make_shot(wired.db, "000401")
    assert await wired.sets.assign_shot(shot, row.current_version_id)

    result = await proposals.accept(row.id, stored.id)

    assert result.refused == "design_has_shots"
    # Nothing moved: the proposal is still waiting and version 1 still empty.
    assert result.proposal is not None and result.proposal.status == "proposed"
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None and v1.profile_version_id is None
    assert await draft_status(wired, draft_id) == "draft"


async def test_writing_a_version_by_hand_stales_the_waiting_recipe_and_discards_its_draft(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None

    filled = await wired.sets.add_version(row.id, SetVersionPatch(dose_g=18, grind_setting="21"))

    assert filled is not None and filled.version_no == 1
    retired = await proposals.get(row.id, stored.id)
    assert retired is not None and retired.status == "stale"
    assert await draft_status(wired, draft_id) == "discarded"


async def test_discarding_a_design_takes_its_proposals_and_discards_their_drafts(
    wired: Fixtures,
) -> None:
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None

    assert await wired.sets.discard_design(row.id) is None

    assert await proposals.get(row.id, stored.id) is None
    # Discarded, not deleted: the draft keeps its history on the Profiles page.
    assert await draft_status(wired, draft_id) == "discarded"


@pytest.mark.parametrize("status", ["discarded", "superseded", "failed"])
async def test_accepting_is_refused_once_the_card_s_draft_is_closed(
    wired: Fixtures, status: str
) -> None:
    """The twin of the create-time check: version 1 never names a dead draft's profile."""
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None
    await ProfileDraftsRepository(wired.db).set_status(draft_id, status)  # type: ignore[arg-type]

    result = await proposals.accept(row.id, stored.id)

    assert result.refused == "draft_closed"
    assert result.proposal is not None and result.proposal.status == "proposed"
    v1 = await wired.sets.current_version(row.id)
    assert v1 is not None and v1.profile_version_id is None
    after = await wired.sets.get(row.id)
    assert after is not None and after.designing is True


async def test_a_card_whose_draft_was_already_pushed_can_still_be_accepted(
    wired: Fixtures,
) -> None:
    """Approved and pushed before Accept is the same profile, in the other order."""
    row = await designed(wired)
    draft_id, profile = await a_draft(wired, "Designed")
    proposals = SetProposalsRepository(wired.db)
    stored = (await proposals.create(row.id, design(draft_id, profile))).proposal
    assert stored is not None
    await ProfileDraftsRepository(wired.db).set_status(draft_id, "pushed")

    result = await proposals.accept(row.id, stored.id)

    assert result.refused is None
    assert result.version is not None and result.version.profile_version_id == profile


async def test_the_accept_route_answers_a_discarded_draft_with_its_own_409(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    row = await SetsRepository(db).create_design(
        SetWrite(name="Designed", bean_id=bean.id, grinder_id=grinder.id), DesignBrief()
    )
    base = await make_profile_version(db, "Library profile")
    drafted = await make_profile_version(db, "Designed")
    draft = await ProfileDraftsRepository(db).create(
        ProfileDraftWrite(base_version_id=base, draft_version_id=drafted)
    )
    stored = (await SetProposalsRepository(db).create(row.id, design(draft.id, drafted))).proposal
    assert stored is not None
    discarded = await client.post(f"/api/profile-drafts/{draft.id}/discard")
    assert discarded.status_code == 200, discarded.text

    response = await client.post(f"/api/sets/{row.id}/proposals/{stored.id}/accept")

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "PROPOSAL_DRAFT_CLOSED"
    assert "ask the agent" in body["details"]["message"]
    page = (await client.get(f"/api/sets/{row.id}")).json()["data"]
    assert page["set"]["designing"] is True
    assert page["proposal"]["status"] == "proposed"
