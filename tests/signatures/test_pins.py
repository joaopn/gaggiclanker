"""A shot brewed on a pin's copy is judged by the original's signature.

The copy is the same recipe under another name, so the profile that judges its shots is the one
the pinned Recipe version names: the pinned version's override applies to them, a rejection on the
original reaches them, and nothing is proposed on the copy. One function says whose signature
judges a shot (`judged_profile_version`), and every lookup goes through it.
"""

from __future__ import annotations

import copy

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.pins import PinsRepository, PinWrite
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.review.context import build_review_input
from gaggiclanker.shotinfo.fields import shot_fields
from gaggiclanker.signatures.checks import clear_checks_memo
from gaggiclanker.signatures.lines import signature_block, signature_lines
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import add_shot
from tests.signatures.test_carry import _version
from tests.signatures.test_checks import Lever, _lever, _propose, _row


async def _pinned_lever(db: Database) -> tuple[Lever, int, int]:
    """The lever Recipe with its first version pinned, and a shot brewed on the copy:
    (the lever, the copy's profile version, the shot on the copy)."""
    lever = await _lever(db)
    document = copy.deepcopy(LEVER_PROFILE)
    document["label"] = f"{document['label']} v1"
    copy_version = await _version(db, document)
    row = await ProfileBoardRepository(db).insert(
        BoardRowWrite(label=document["label"], current_version_id=copy_version, origin="draft")
    )
    await PinsRepository(db).insert(
        PinWrite(set_version_id=lever.set_version, board_id=row.id, profile_version_id=copy_version)
    )
    shot = await add_shot(
        db,
        set_version_id=lever.set_version,
        profile_version_id=copy_version,
        profile=document,
        device_id="000950",
    )
    return lever, copy_version, shot


async def test_a_shot_on_the_copy_is_read_against_the_originals_signature(db: Database) -> None:
    lever, copy_version, shot = await _pinned_lever(db)
    await _propose(lever, confirm=True)

    pinned = await _row(db, shot)

    assert (
        pinned.checks.badge == (await _row(db, lever.shot)).checks.badge == ("ramp: early yield +4")
    )
    assert [(w.phase, w.fault, w.tier) for w in pinned.checks.entries][:3] == [
        ("ramp", "early yield", "critical"),
        ("decline", "skipped", "critical"),
        ("soak", "early yield", "important"),
    ]
    fields = await shot_fields(db, shot)
    assert fields is not None
    assert fields.signature.profile_version_id == lever.version != copy_version
    assert fields.signature.confirmed == 4


async def test_the_pinned_versions_override_applies_to_a_shot_on_the_copy(db: Database) -> None:
    lever, _copy, shot = await _pinned_lever(db)
    rows = await _propose(lever, confirm=True)
    ramp = next(r for r in rows if r.kind == "measure" and r.phase == "ramp")
    assert (await _row(db, shot)).checks.badge == "ramp: early yield +4"

    await SignatureService(db).propose_override(
        set_version_id=lever.set_version,
        profile_version_id=lever.version,
        expectation_id=ramp.id,
        compare={"op": "<=", "value": 1.3},
        reason="a coarser bean",
        thread_id=None,
    )

    assert (await _row(db, shot)).checks.badge == "decline: skipped +3"
    # The version the Recipe moved on to is not the pinned one: its shots read the profile's own.
    other = await add_shot(
        db, set_version_id=lever.other_version, profile_version_id=lever.version, device_id="000951"
    )
    assert (await _row(db, other)).checks.badge == "ramp: early yield +4"


async def test_a_rejection_on_the_original_reaches_the_copys_shots_at_once(db: Database) -> None:
    lever, _copy, shot = await _pinned_lever(db)
    rows = await _propose(lever, confirm=True)
    assert (await _row(db, shot)).checks.badge == "ramp: early yield +4"

    ramp = next(r for r in rows if r.kind == "measure" and r.phase == "ramp")
    await SignatureRepository(db).reject(ramp.id, reason="not what it is for")

    assert (await _row(db, shot)).checks.badge == "decline: skipped +3"
    clear_checks_memo()
    assert (await _row(db, shot)).checks.badge == "decline: skipped +3"


async def test_an_unpinned_copy_is_still_judged_by_the_original(db: Database) -> None:
    lever, _copy, shot = await _pinned_lever(db)
    await _propose(lever, confirm=True)
    pin = await PinsRepository(db).live_for_version(lever.set_version)
    assert pin is not None
    await PinsRepository(db).mark_unpinned(pin.id)

    assert (await _row(db, shot)).checks.badge == "ramp: early yield +4"


async def test_the_lines_and_the_review_input_read_the_originals_signature(db: Database) -> None:
    lever, copy_version, shot = await _pinned_lever(db)
    await _propose(lever, confirm=True)

    assert await signature_lines(db, copy_version) == await signature_lines(db, lever.version)
    assert await signature_lines(db, copy_version)
    block = await signature_block(db, copy_version, lever.set_version)
    assert block == await signature_block(db, lever.version, lever.set_version)
    review = await build_review_input(db, shot)
    original = await build_review_input(db, lever.shot)
    assert review.signature_confirmed == original.signature_confirmed == 4
    assert [e.sentence for e in review.expectations] == [e.sentence for e in original.expectations]


async def test_nothing_is_proposed_on_the_copy_and_a_profile_made_from_it_starts_from_the_original(
    db: Database,
) -> None:
    lever, copy_version, _shot = await _pinned_lever(db)
    await _propose(lever, confirm=True)
    service = SignatureService(db)

    with pytest.raises(SignatureRefused) as refused:
        await service.propose(
            copy_version,
            [ExpectationInput(tier="critical", kind="reached", phase="decline")],
            reason="x",
        )
    assert str(refused.value) == f"A pin is judged by {LEVER_PROFILE['label']}'s signature."
    assert await SignatureRepository(db).for_version(copy_version) == []

    document = copy.deepcopy(LEVER_PROFILE)
    document["label"] = "Spun off"
    spun_off = await _version(db, document)
    await service.carry(copy_version, spun_off)
    assert len(await SignatureRepository(db).for_version(spun_off)) == 4
