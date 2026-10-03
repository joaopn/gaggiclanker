"""A draft put on the board for a Set that is still being designed.

The put is one of the paths a person can end a design by hand: once the sync has put the
profile on the machine, its version is written onto the Set's empty version 1, never appended
as a v2 after it. And when that version 1 can no longer be filled, the put is refused before
anything is recorded, and nothing reaches the machine.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetWrite,
)
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import base_profile, base_version_id, data, error
from tests.drafts.test_api import lower_pressure
from tests.drafts.test_board import adopted, pull, put
from tests.sets.conftest import make_shot

__all__ = ["adopted"]  # the fixture, re-exported for this module


async def _designed(app: FastAPI) -> SetRow:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    return await SetsRepository(db).create_design(
        SetWrite(name="Kenya AA on the Niche Zero", bean_id=bean.id, grinder_id=grinder.id),
        DesignBrief(),
    )


async def _drafted(app: FastAPI) -> int:
    profile = await base_profile(app)
    row = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document=lower_pressure(profile, 8.0),
        change_summary="Down to 8 bar.",
    )
    return int(row.id)


async def test_a_put_for_a_designed_set_fills_its_version_1_once_the_sync_has_sent_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    designed = await _designed(app)
    draft_id = await _drafted(app)

    await put(client, {"id": draft_id}, set_id=designed.id)
    run = await pull(app)

    assert run.status == "ok", run.error
    draft = await ProfileDraftsRepository(app.state.db).get(draft_id)
    assert draft is not None and draft.status == "pushed"
    version = await SetsRepository(app.state.db).get_version(designed.current_version_id or 0)
    assert version is not None and version.version_label == "v1"
    assert version.profile_version_id == draft.draft_version_id
    assert version.pushed_device_profile_id == draft.pushed_device_profile_id
    after = await SetsRepository(app.state.db).get(designed.id)
    assert after is not None
    assert (after.designing, after.version_count) == (False, 1)


async def test_a_put_for_a_design_that_cannot_be_filled_never_reaches_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake_device = adopted
    designed = await _designed(app)
    assert designed.current_version_id is not None
    shot = await make_shot(app.state.db, "000301")
    await SetsRepository(app.state.db).assign_shot(shot, designed.current_version_id)
    draft_id = await _drafted(app)
    before = len(fake_device.profiles)

    response = await client.post(
        "/api/profile-board", json={"draft_id": draft_id, "set_id": designed.id}
    )
    await pull(app)

    assert response.status_code == 409
    assert error(response)["code"] == "DESIGN_HAS_SHOTS"
    assert len(fake_device.profiles) == before
    assert "req:profiles:save" not in fake_device.ws_requests
    draft = data(await client.get(f"/api/profile-drafts/{draft_id}"))["draft"]
    assert draft["status"] == "draft", "not approved by a refused put"


async def _waiting_design(app: FastAPI, designed: SetRow, draft_id: int) -> int:
    draft = await ProfileDraftsRepository(app.state.db).get(draft_id)
    assert draft is not None
    result = await SetProposalsRepository(app.state.db).create(
        designed.id,
        ProposalWrite(
            kind="design",
            draft_id=draft_id,
            reason="The recipe from the conversation.",
            patch=SetVersionPatch(profile_version_id=draft.draft_version_id, dose_g=18),
        ),
    )
    assert result.proposal is not None, result.refused
    return result.proposal.id


async def test_a_put_for_the_design_stales_the_waiting_recipe_and_discards_its_draft(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    designed = await _designed(app)
    proposed_draft = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document=lower_pressure(await base_profile(app), 7.0),
        change_summary="Down to 7 bar.",
    )
    proposal_id = await _waiting_design(app, designed, proposed_draft.id)
    pushed_draft = await _drafted(app)

    await put(client, {"id": pushed_draft}, set_id=designed.id)
    await pull(app)

    version = await SetsRepository(app.state.db).get_version(designed.current_version_id or 0)
    assert version is not None and version.version_label == "v1"
    proposal = await SetProposalsRepository(app.state.db).get(designed.id, proposal_id)
    assert proposal is not None and proposal.status == "stale"
    other = await ProfileDraftsRepository(app.state.db).get(proposed_draft.id)
    assert other is not None and other.status == "discarded"


async def test_putting_the_recipe_s_own_draft_never_discards_what_is_on_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The retired card's draft is the one just put: it stays `pushed`."""
    app, client, _ = adopted
    designed = await _designed(app)
    draft_id = await _drafted(app)
    proposal_id = await _waiting_design(app, designed, draft_id)

    await put(client, {"id": draft_id}, set_id=designed.id)
    await pull(app)

    proposal = await SetProposalsRepository(app.state.db).get(designed.id, proposal_id)
    assert proposal is not None and proposal.status == "stale"
    draft = await ProfileDraftsRepository(app.state.db).get(draft_id)
    assert draft is not None and draft.status == "pushed"


async def test_after_accept_and_a_sync_shots_on_the_new_profile_are_filed_under_the_set(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Automatch is on from the start, and it needs nothing more than the put and a sync.

    The design's draft belongs to no Set and is put plainly from the Profiles page. What files
    the next shot is the sync verifying the profile against the draft's own document: the
    machine's copy resolves to the version the accepted card put on version 1.
    """
    app, client, _ = adopted
    bean = data(await client.post("/api/beans", json={"name": "Kenya AA"}))
    grinder = data(await client.post("/api/grinders", json={"name": "Niche Zero"}))
    created = data(
        await client.post(
            "/api/sets/design", json={"bean_id": bean["id"], "grinder_id": grinder["id"]}
        )
    )
    set_id = created["set"]["id"]
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document={**lower_pressure(await base_profile(app), 8.5), "label": "Kenya AA body"},
        change_summary="A gentler peak for body.",
        new_profile_only=True,
    )
    proposal = await SetProposalsRepository(app.state.db).create(
        set_id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=created["thread_id"],
            reason="A gentler peak for body.",
            patch=SetVersionPatch(
                profile_version_id=draft.draft_version_id, grind_setting="20", dose_g=18
            ),
        ),
    )
    assert proposal.proposal is not None, proposal.refused
    data(await client.post(f"/api/sets/{set_id}/proposals/{proposal.proposal.id}/accept"))

    await put(client, {"id": draft.id})
    run = await pull(app)

    assert run.status == "ok", run.error
    pushed = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert pushed is not None and pushed.status == "pushed"
    shot = await make_shot(
        app.state.db, "000601", profile_id_on_device=pushed.pushed_device_profile_id or ""
    )
    summary = await SetsRepository(app.state.db).match_unfiled([shot])
    assert summary.matched == 1
    filed = await app.state.db.fetch_value("SELECT set_version_id FROM shots WHERE id = ?", (shot,))
    assert filed == created["version"]["id"]
