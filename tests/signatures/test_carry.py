"""A new profile version carries the previous version's expectations in force.

One whose phases still exist by name comes over in force (confirmed); one whose phase
disappeared comes over as *needs a new phase*: shown and rejectable, never in force. Nothing
is matched by position or by guess, and nothing rejected is carried.
"""

from __future__ import annotations

import copy
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE

RAMP_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "ramp"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.15},
}


async def _version(db: Database, document: dict[str, Any]) -> int:
    version, _ = await ProfilesRepository(db).ensure_version(Profile.model_validate(document))
    return version.id


def _changed(**edit: Any) -> dict[str, Any]:
    document = copy.deepcopy(LEVER_PROFILE)
    document.update(edit)
    return document


async def _confirmed_lever_signature(db: Database) -> tuple[int, list[ExpectationRow]]:
    version = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    rows = await service.propose(
        version,
        [
            ExpectationInput(tier="critical", kind="reached", phase="decline"),
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
            ExpectationInput(
                tier="context",
                kind="free_text",
                text="pressure and flow fall together",
                fault="unstable",
            ),
            ExpectationInput(
                tier="important", kind="expects_warning", warning="fast flow", phase="ramp"
            ),
        ],
        reason="what the lever is for",
    )
    repo = SignatureRepository(db)
    # The last one is rejected: it must never be carried.
    assert (await repo.reject(rows[3].id, reason="no")).row is not None
    return version, rows


async def test_a_new_version_with_the_same_phase_names_carries_every_confirmed_expectation(
    db: Database,
) -> None:
    old, rows = await _confirmed_lever_signature(db)
    new = await _version(db, _changed(temperature=92.0))

    carried = await SignatureService(db).carry(old, new)

    assert [c.carried_from_id for c in carried] == [r.id for r in rows[:3]]
    assert {c.status for c in carried} == {"confirmed"}
    assert not any(c.needs_phase for c in carried)
    assert {c.carried_from_version_id for c in carried} == {old}
    assert [(c.kind, c.phase, c.tier, c.sentence) for c in carried] == [
        (r.kind, r.phase, r.tier, r.sentence) for r in rows[:3]
    ]
    # A carried expectation is in force on the new version at once, with no answer from anyone.
    repo = SignatureRepository(db)
    assert {c.answered_at for c in carried} == {None}
    assert len((await repo.confirmed_for_versions([new]))[new]) == 3


async def test_a_renamed_phase_needs_a_new_phase_and_is_never_matched_by_guess(
    db: Database,
) -> None:
    old, rows = await _confirmed_lever_signature(db)
    renamed = copy.deepcopy(LEVER_PROFILE)
    renamed["phases"][2]["name"] = "rise"  # the ramp, under another name, in the same place
    new = await _version(db, renamed)

    carried = await SignatureService(db).carry(old, new)

    by_old = {c.carried_from_id: c for c in carried}
    assert by_old[rows[1].id].needs_phase is True  # the ramp measure names a phase that is gone
    assert by_old[rows[0].id].needs_phase is False  # "decline" is still there
    assert by_old[rows[2].id].needs_phase is False  # no phase at all
    repo = SignatureRepository(db)
    # The one that needs a phase is never in force; the others are.
    assert by_old[rows[1].id].status == "proposed"
    in_force = (await repo.confirmed_for_versions([new]))[new]
    assert sorted(c.carried_from_id or 0 for c in in_force) == sorted([rows[0].id, rows[2].id])
    # It can be rejected but never restored into force.
    assert (await repo.reject(by_old[rows[1].id].id)).row is not None
    assert (await repo.restore(by_old[rows[1].id].id)).refused == "needs_phase"


async def test_a_removed_phase_needs_a_new_phase(db: Database) -> None:
    old, rows = await _confirmed_lever_signature(db)
    shorter = copy.deepcopy(LEVER_PROFILE)
    shorter["phases"] = shorter["phases"][:3]
    new = await _version(db, shorter)

    carried = await SignatureService(db).carry(old, new)

    by_old = {c.carried_from_id: c for c in carried}
    assert by_old[rows[0].id].needs_phase is True  # "decline" is gone
    assert by_old[rows[0].id].status == "proposed"
    assert by_old[rows[1].id].needs_phase is False
    assert by_old[rows[1].id].status == "confirmed"


async def test_a_phase_named_only_in_a_window_is_a_phase_the_expectation_needs(
    db: Database,
) -> None:
    old = await _version(db, LEVER_PROFILE)
    service = SignatureService(db)
    await service.propose(
        old,
        [
            ExpectationInput(
                tier="critical",
                kind="measure",
                expression={
                    "channel": "pressure",
                    "op": "max",
                    "window": {"from": {"phase_start": "soak"}, "to": {"phase_end": "ramp"}},
                    "compare": {"op": "<=", "value": 9},
                },
            )
        ],
        reason="x",
    )
    renamed = copy.deepcopy(LEVER_PROFILE)
    renamed["phases"][1]["name"] = "hold"
    (carried,) = await service.carry(old, await _version(db, renamed))
    assert carried.needs_phase is True


async def test_carrying_twice_adds_nothing_and_rejected_ones_are_never_carried(
    db: Database,
) -> None:
    old, _ = await _confirmed_lever_signature(db)
    new = await _version(db, _changed(temperature=91.0))
    service = SignatureService(db)
    assert len(await service.carry(old, new)) == 3
    assert await service.carry(old, new) == []
    assert await service.carry(old, old) == []
    # The one expectation the person rejected on the old version was not carried.
    repo = SignatureRepository(db)
    assert {r.kind for r in await repo.for_version(new)} == {"reached", "measure", "free_text"}


async def test_a_draft_carries_from_its_base_and_holds_the_agents_own_proposals(
    db: Database,
) -> None:
    old, rows = await _confirmed_lever_signature(db)
    drafts = DraftProposals(db, SettingsService(SettingsRepository(db)))
    document = copy.deepcopy(LEVER_PROFILE)
    document["temperature"] = 92.0

    draft = await drafts.create_manual(base_version_id=old, document=document)

    repo = SignatureRepository(db)
    assert draft.draft_version_id is not None
    on_draft = await repo.for_version(draft.draft_version_id)
    assert [r.carried_from_id for r in on_draft] == [r.id for r in rows[:3]]
    assert {r.status for r in on_draft} == {"confirmed"}
    # Carrying again for the same stored version adds nothing.
    assert await SignatureService(db).carry(old, draft.draft_version_id) == []
