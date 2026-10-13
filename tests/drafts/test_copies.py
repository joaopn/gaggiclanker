"""A profile copied under a new name, through the draft constructor and the put.

The copy is the foundation a pinned Set version and a duplicated Set stand on, so the properties
it promises are pinned here: the document is the original's but for the label; the safety policy
refuses a profile it would have to change; the name rule applies; nothing is left waiting; and
nothing is recorded on a Set.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.drafts.copies import CopyBeyondPolicy, ProfileCopier
from gaggiclanker.drafts.proposals import TakenName
from gaggiclanker.infra.errors import Unprocessable
from gaggiclanker.signatures.service import SignatureService
from tests.drafts.conftest import BASE_LABEL, base_version_id
from tests.drafts.test_board import adopted, get_board, pull, row_for

__all__ = ["adopted"]  # the fixture, re-exported for this module

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]


async def _everything_stored(app: FastAPI) -> tuple[int, int, int, int]:
    db = app.state.db
    return (
        int(await db.fetch_value("SELECT COUNT(*) FROM profile_versions")),
        int(await db.fetch_value("SELECT COUNT(*) FROM profile_drafts")),
        int(await db.fetch_value("SELECT COUNT(*) FROM profile_board")),
        int(await db.fetch_value("SELECT COUNT(*) FROM signature_expectations")),
    )


async def _copy(app: FastAPI, label: str, *, carry: bool = False) -> int:
    base = await base_version_id(app, BASE_LABEL)
    async with app.state.db.transaction():
        row = await ProfileCopier(app.state.board).copy_profile(base, label, carry_signature=carry)
    return row.id


async def test_a_copy_is_the_originals_document_under_a_new_name_and_waits_for_nothing(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)

    row_id = await _copy(app, "  Blooming v1.2 ")

    board = await get_board(client)
    row = row_for(board, "Blooming v1.2")
    assert row["row"]["id"] == row_id
    assert (row["on_machine"], row["starred"], row["row"]["origin"]) == (True, True, "draft")
    # The document: the original's, but for the label.
    profiles = ProfilesRepository(app.state.db)
    original = await profiles.get_version(base)
    copy = await profiles.get_version(row["row"]["current_version_id"])
    assert original is not None and copy is not None and copy.id != original.id
    assert original.profile is not None and copy.profile is not None
    assert {**copy.profile, "label": ""} == {**original.profile, "label": ""}
    # Nothing waits for a person: the draft was approved by the put, in the same step.
    assert board["proposals"] == []
    draft = await ProfileDraftsRepository(app.state.db).get(row["row"]["pending_draft_id"])
    assert draft is not None and draft.status == "approved" and draft.set_id is None
    assert [r["row"]["label"] for r in board["rows"]].count("Blooming v1.2") == 1


async def test_a_copy_carries_the_signature_only_when_asked(adopted: Adopted) -> None:
    app, _client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    service = SignatureService(app.state.db)
    phase = (await service.phase_names(base) or [])[0]
    await service.propose(
        base, [ExpectationInput(tier="critical", kind="reached", phase=phase)], reason="r"
    )

    plain = await _copy(app, "Plain copy")
    carried = await _copy(app, "Carried copy", carry=True)

    versions = {}
    for row_id, name in ((plain, "Plain copy"), (carried, "Carried copy")):
        row = await app.state.board.board.get(row_id)
        assert row is not None and row.label == name
        versions[name] = row.current_version_id
    assert await service.repo.confirmed_for_versions([versions["Plain copy"]]) == {}
    assert len(await service.repo.confirmed_for_versions([versions["Carried copy"]])) == 1


async def test_a_copy_the_policy_would_change_is_refused_with_the_numbers_and_stores_nothing(
    adopted: Adopted,
) -> None:
    app, _client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    # The profile was inside the bounds when it was adopted; the person has tightened them since.
    await app.state.settings_service.apply({"profilePolicyPressureMaxBar": 7.5})
    before = await _everything_stored(app)

    with pytest.raises(CopyBeyondPolicy) as refused:
        async with app.state.db.transaction():
            await ProfileCopier(app.state.board).copy_profile(
                base, "Too hot", carry_signature=False
            )

    assert refused.value.changes, "the clamp's list travels with the refusal"
    assert "outside the current safety bounds" in refused.value.sentence
    assert all(change.path in refused.value.sentence for change in refused.value.changes)
    assert await _everything_stored(app) == before


async def test_a_taken_name_and_an_empty_one_are_refused_and_nothing_changes(
    adopted: Adopted,
) -> None:
    app, _client, _fake = adopted
    base = await base_version_id(app, BASE_LABEL)
    before = await _everything_stored(app)
    copier = ProfileCopier(app.state.board)

    with pytest.raises(Unprocessable):
        await copier.prepare(base, "   ")
    # The original's own name is taken too: a copy is never a version of its original.
    for taken in (BASE_LABEL, BASE_LABEL.upper()):
        with pytest.raises(TakenName):
            async with app.state.db.transaction():
                await copier.copy_profile(base, taken, carry_signature=False)

    assert await _everything_stored(app) == before


async def test_a_copy_reaches_the_machine_through_the_ordinary_push(adopted: Adopted) -> None:
    app, client, fake = adopted
    await _copy(app, "Blooming v1.2")

    run = await pull(app)

    assert run.status == "ok", run.error
    assert "Blooming v1.2" in [p["label"] for p in fake.profiles]
    row = row_for(await get_board(client), "Blooming v1.2")
    assert row["machine"]["holds_current"] and row["row"]["pending_draft_id"] is None
