"""Accepting a first recipe also puts its profile on the list, in the same transaction.

The accept records version 1 naming the draft's final profile version (a rename stores a new
one) and puts the draft under the typed name, and either both happen or neither does: every
refusal, and a write that fails after the put, leaves the draft, the board and the Set as they
were. The connection's transaction does not nest, so the put runs inside the accept's own.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.lineage import taken_name_sentence
from gaggiclanker.db.repos.profile_board import BoardRowPatch, BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    DesignBrief,
    SetRow,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from tests.sets.conftest import make_profile_version, make_shot
from tests.sets.test_design_api import data, error


async def _designed(app: FastAPI) -> SetRow:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    return await SetsRepository(db).create_design(
        SetWrite(name="Designed", bean_id=bean.id, grinder_id=grinder.id), DesignBrief()
    )


async def _card(app: FastAPI, row: SetRow, *, new: bool = True, name: str = "Soft Bloom") -> Any:
    """A first-recipe card whose profile is a draft (a new profile unless ``new`` is false)."""
    db = app.state.db
    base = await make_profile_version(db, "Library profile")
    document = dict(await _document_of(db, base))
    document["label"] = name if new else "Library profile"
    document["temperature"] = float(document["temperature"]) + 1
    draft = await app.state.draft_proposals.create_manual(
        base_version_id=base, document=document, is_new=new
    )
    proposal = (
        await SetProposalsRepository(db).create(
            row.id,
            ProposalWrite(
                kind="design",
                draft_id=draft.id,
                reason="A longer, gentler shot.",
                patch=SetVersionPatch(
                    profile_version_id=draft.draft_version_id,
                    grind_setting="20",
                    grind_value=20,
                    dose_g=18,
                    target_yield_g=40,
                ),
            ),
        )
    ).proposal
    assert proposal is not None
    return draft, proposal


async def _document_of(db: Any, version_id: int) -> dict[str, Any]:
    version = await ProfilesRepository(db).get_version(version_id)
    assert version is not None and version.profile is not None
    return dict(version.profile)


async def _accept(
    client: httpx.AsyncClient, row: SetRow, proposal: Any, **body: Any
) -> httpx.Response:
    return await client.post(f"/api/sets/{row.id}/proposals/{proposal.id}/accept", json=body)


async def _untouched(app: FastAPI, row: SetRow, draft: Any, proposal: Any) -> None:
    """The draft, the list and the Set are exactly as before the accept."""
    db = app.state.db
    after = await ProfileDraftsRepository(db).get(draft.id)
    assert after is not None
    assert (after.status, after.draft_version_id) == ("draft", draft.draft_version_id)
    assert await ProfileBoardRepository(db).find_live_by_label(after.draft_label or "") is None
    assert await db.fetch_value("SELECT COUNT(*) FROM profile_board") == 0
    stored = await SetProposalsRepository(db).get(row.id, proposal.id)
    assert stored is not None and stored.status == "proposed"
    current = await SetsRepository(db).get(row.id)
    assert current is not None and current.designing is True
    v1 = await SetsRepository(db).current_version(row.id)
    assert v1 is not None and v1.profile_version_id is None


async def test_accepting_with_a_name_records_version_1_and_puts_the_profile_under_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)

    response = await _accept(client, row, proposal, profile_label="  Gentle Bloom ")

    assert response.status_code == 200, response.text
    body = data(response)
    stored = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert stored is not None and stored.status == "approved"
    assert stored.draft_label == "Gentle Bloom"
    assert stored.draft_version_id != draft.draft_version_id, "the rename is a new version"
    board = await ProfileBoardRepository(app.state.db).find_live_by_label("Gentle Bloom")
    assert board is not None and board.on_machine is True
    assert board.current_version_id == stored.draft_version_id
    assert (body["profile_draft_id"], body["profile_row_id"]) == (draft.id, board.id)
    assert body["version"]["profile_version_id"] == stored.draft_version_id
    assert body["proposal"]["status"] == "accepted"
    # Version 1 is filled in place and appends nothing, and the put records nothing on the Set.
    assert board.pending_set_id is None


async def test_accepting_without_a_name_puts_the_draft_under_its_own_name(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)

    response = await _accept(client, row, proposal)

    assert response.status_code == 200, response.text
    body = data(response)
    stored = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert stored is not None and stored.status == "approved"
    assert stored.draft_version_id == draft.draft_version_id, "no rename, no new version"
    board = await ProfileBoardRepository(app.state.db).find_live_by_label("Soft Bloom")
    assert board is not None and board.on_machine is True and board.pending_set_id is None
    assert (body["profile_draft_id"], body["profile_row_id"]) == (draft.id, board.id)
    assert body["version"]["profile_version_id"] == draft.draft_version_id


async def test_a_design_that_continues_a_profile_makes_that_change_active(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row, new=False)
    existing = await ProfileBoardRepository(app.state.db).insert(
        BoardRowWrite(
            label="Library profile", current_version_id=draft.base_version_id, origin="adopted"
        )
    )
    await ProfileBoardRepository(app.state.db).update(existing.id, BoardRowPatch(on_machine=False))

    response = await _accept(client, row, proposal)

    assert response.status_code == 200, response.text
    body = data(response)
    after = await ProfileBoardRepository(app.state.db).get(existing.id)
    assert after is not None
    assert after.current_version_id == draft.draft_version_id and after.on_machine is True
    assert body["profile_row_id"] == existing.id
    assert body["version"]["profile_version_id"] == draft.draft_version_id
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == 1


async def _later_version_active(app: FastAPI, draft: Any) -> tuple[int, int]:
    """Put the card's draft as the Profiles page would, then make a later change active."""
    row = await app.state.board.put_draft(draft.id)
    document = dict(await _document_of(app.state.db, draft.draft_version_id))
    document["temperature"] = float(document["temperature"]) + 1
    later = await app.state.draft_proposals.create_manual(
        base_version_id=draft.draft_version_id, document=document
    )
    moved = await app.state.board.put_draft(later.id)
    assert moved.id == row.id and moved.current_version_id == later.draft_version_id
    return row.id, later.draft_version_id


async def test_a_draft_already_approved_is_left_as_it_is_and_the_answer_names_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    put = await app.state.board.put_draft(draft.id)

    accepted = await _accept(client, row, proposal)

    assert accepted.status_code == 200, accepted.text
    body = data(accepted)
    assert (body["profile_draft_id"], body["profile_row_id"]) == (draft.id, put.id)
    assert body["version"]["profile_version_id"] == draft.draft_version_id
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == 1


@pytest.mark.parametrize("status", ["approved", "pushed"])
async def test_a_version_made_active_since_is_not_undone_by_the_accept(
    app: FastAPI, client: httpx.AsyncClient, status: str
) -> None:
    """The card's profile was approved on the Profiles page, then a later version of it was made
    active. Accepting the card records version 1 on the card's profile and leaves the list alone."""
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    row_id, later = await _later_version_active(app, draft)
    if status == "pushed":
        await ProfileDraftsRepository(app.state.db).set_status(draft.id, "pushed")

    accepted = await _accept(client, row, proposal)

    assert accepted.status_code == 200, accepted.text
    body = data(accepted)
    board = await ProfileBoardRepository(app.state.db).get(row_id)
    assert board is not None and board.current_version_id == later, "the older version came back"
    assert body["version"]["profile_version_id"] == draft.draft_version_id
    assert body["profile_draft_id"] == draft.id and body["profile_row_id"] == row_id
    stored = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert stored is not None and stored.status == status


async def test_a_continuation_design_after_a_later_version_is_active_does_not_go_back(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row, new=False)
    await ProfileBoardRepository(app.state.db).insert(
        BoardRowWrite(
            label="Library profile", current_version_id=draft.base_version_id, origin="adopted"
        )
    )
    row_id, later = await _later_version_active(app, draft)

    accepted = await _accept(client, row, proposal)

    assert accepted.status_code == 200, accepted.text
    board = await ProfileBoardRepository(app.state.db).get(row_id)
    assert board is not None and board.current_version_id == later
    assert data(accepted)["version"]["profile_version_id"] == draft.draft_version_id


async def test_the_answer_names_the_proposals_draft_when_the_row_has_no_pending_one(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    put = await app.state.board.put_draft(draft.id)
    await ProfileDraftsRepository(app.state.db).set_status(draft.id, "pushed")
    # The sync clears the row's pending draft once the profile is on the machine.
    await ProfileBoardRepository(app.state.db).update(
        put.id, BoardRowPatch(pending_draft_id=None, pending_set_id=None, pending_major=None)
    )

    body = data(await _accept(client, row, proposal))

    assert (body["profile_draft_id"], body["profile_row_id"]) == (draft.id, put.id)


async def test_a_name_for_a_draft_that_is_no_longer_waiting_is_refused(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    await app.state.board.put_draft(draft.id)

    for typed in ("Something else", "soft bloom"):
        refused = await _accept(client, row, proposal, profile_label=typed)
        assert refused.status_code == 422
        assert (
            error(refused)["message"]
            == "That profile was already approved; its name cannot change here."
        )
    stored = await SetProposalsRepository(app.state.db).get(row.id, proposal.id)
    assert stored is not None and stored.status == "proposed"


async def test_a_taken_name_refuses_the_whole_accept(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    other = await app.state.draft_proposals.create_manual(
        base_version_id=draft.base_version_id,
        document={
            **(await _document_of(app.state.db, draft.draft_version_id)),
            "label": "Taken One",
        },
        is_new=True,
    )
    await app.state.board.put_draft(other.id)
    rows = await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board")

    response = await _accept(client, row, proposal, profile_label="taken one")

    assert response.status_code == 409
    assert error(response)["message"] == taken_name_sentence("taken one")
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == rows
    stored = await SetProposalsRepository(app.state.db).get(row.id, proposal.id)
    assert stored is not None and stored.status == "proposed"
    assert (await SetsRepository(app.state.db).get(row.id)).designing is True  # type: ignore[union-attr]
    after = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert after is not None and after.status == "draft"
    assert after.draft_version_id == draft.draft_version_id


async def test_an_empty_name_refuses_the_whole_accept(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)

    response = await _accept(client, row, proposal, profile_label="   ")

    assert response.status_code == 422
    assert error(response)["message"] == "A profile needs a name"
    await _untouched(app, row, draft, proposal)


async def test_a_name_for_a_change_to_an_existing_profile_is_refused(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row, new=False)
    # The profile it continues is on the list.
    await ProfileBoardRepository(app.state.db).insert(
        BoardRowWrite(
            label="Library profile", current_version_id=draft.base_version_id, origin="adopted"
        )
    )

    response = await _accept(client, row, proposal, profile_label="Another")

    assert response.status_code == 422
    assert error(response)["message"] == "A change to an existing profile keeps its name"
    after = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert after is not None and after.status == "draft"
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == 1
    stored = await SetProposalsRepository(app.state.db).get(row.id, proposal.id)
    assert stored is not None and stored.status == "proposed"


async def test_a_name_for_an_ordinary_change_is_refused(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA"))
    row = await SetsRepository(db).create(
        SetWrite(name="Plain", bean_id=bean.id), SetVersionWrite(dose_g=18, target_yield_g=36)
    )
    created = await SetProposalsRepository(db).create(
        row.id,
        ProposalWrite(
            patch=SetVersionPatch(grind_setting="21"),
            reason="finer",
            prediction="Compared to v1: longer.",
        ),
    )
    assert created.proposal is not None

    response = await _accept(client, row, created.proposal, profile_label="Nope")

    assert response.status_code == 422
    still = await SetProposalsRepository(db).get(row.id, created.proposal.id)
    assert still is not None and still.status == "proposed"


async def test_a_closed_draft_refuses_before_anything_is_put(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    await ProfileDraftsRepository(app.state.db).set_status(draft.id, "discarded")

    response = await _accept(client, row, proposal, profile_label="Gentle Bloom")

    assert response.status_code == 409
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == 0


async def test_a_design_that_can_no_longer_be_filled_undoes_the_put_too(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The refusal comes from the append, after the put: the put is undone with it."""
    row = await _designed(app)
    draft, proposal = await _card(app, row)
    v1 = await SetsRepository(app.state.db).current_version(row.id)
    assert v1 is not None
    shot = await make_shot(app.state.db, "000401")
    assert await SetsRepository(app.state.db).assign_shot(shot, v1.id)

    response = await _accept(client, row, proposal, profile_label="Gentle Bloom")

    assert response.status_code == 409, response.text
    assert error(response)["code"] == "DESIGN_HAS_SHOTS"
    assert await app.state.db.fetch_value("SELECT COUNT(*) FROM profile_board") == 0
    after = await ProfileDraftsRepository(app.state.db).get(draft.id)
    assert after is not None and after.status == "draft"
    assert after.draft_version_id == draft.draft_version_id


@pytest.mark.parametrize("fails", ["the_put", "the_record", "the_decision"])
async def test_it_is_one_transaction_whichever_write_fails(
    app: FastAPI,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fails: str,
) -> None:
    """Make each write raise in turn (the last one included): nothing is left of the others."""
    row = await _designed(app)
    draft, proposal = await _card(app, row)

    async def boom(*_a: object, **_k: object) -> Any:
        raise RuntimeError("the write failed")

    if fails == "the_put":
        monkeypatch.setattr(type(app.state.board), "_rename", boom)
    elif fails == "the_record":
        monkeypatch.setattr(SetsRepository, "append_version", boom)
    else:
        monkeypatch.setattr(SetProposalsRepository, "_decide", boom)

    with pytest.raises(RuntimeError, match="the write failed"):
        await SetProposalsRepository(app.state.db).accept(
            row.id,
            proposal.id,
            put_profile=lambda draft_id: app.state.board.put_in_transaction(
                draft_id, label="Gentle Bloom"
            ),
        )

    await _untouched(app, row, draft, proposal)
