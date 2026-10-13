"""The starting-point wizard never offers a pin's copy, and refuses one it is handed.

A Set version never names a pin's copy, so the library the wizard shortlists from leaves copies
out (pinned or since unpinned), and a suggestion that names one anyway is refused as a 409 with
its own code before anything is claimed or made, never as a server error.
"""

from __future__ import annotations

import json

import pytest

from gaggiclanker.db.repos.pins import PinsRepository, PinWrite
from gaggiclanker.db.repos.profile_board import BoardRowWrite, ProfileBoardRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.infra.errors import Conflict
from gaggiclanker.starting.context import profile_candidates
from gaggiclanker.starting.models import StartingPointResult
from gaggiclanker.starting.service import StartingPointService
from tests.llm.conftest import FakeProvider
from tests.starting.conftest import GOOD_PROFILE, Fixture, option_with
from tests.starting.test_service import _propose


async def _a_lookalike(fixture: Fixture) -> int:
    """A profile no Set has that is the fixture's under another name: its active version."""
    profiles = ProfilesRepository(fixture.db)
    original = await profiles.get_version(fixture.profile_version_id)
    assert original is not None and original.profile is not None
    copy, _ = await profiles.ensure_version(
        Profile.model_validate({**original.profile, "label": original.label + " v1"}), owner=None
    )
    await ProfileBoardRepository(fixture.db).insert(
        BoardRowWrite(label=copy.label, current_version_id=copy.id, origin="draft")
    )
    return copy.id


async def _pin_it(fixture: Fixture, copy_id: int) -> int:
    """Pin that profile version to the first Set version: the pin."""
    profiles = ProfilesRepository(fixture.db)
    copy = await profiles.get_version(copy_id)
    assert copy is not None
    owner = await profiles.owner_of(copy.id)
    if owner is None:
        owner = (
            await ProfileBoardRepository(fixture.db).insert(
                BoardRowWrite(label=copy.label, current_version_id=copy.id, origin="draft")
            )
        ).id
    first = await fixture.db.fetch_value("SELECT id FROM set_versions ORDER BY id LIMIT 1")
    pin = await PinsRepository(fixture.db).insert(
        PinWrite(set_version_id=int(first), board_id=owner, profile_version_id=copy.id)
    )
    return pin.id


async def test_a_copy_is_never_a_candidate_pinned_or_not(fixture: Fixture) -> None:
    copy = await _a_lookalike(fixture)
    before = {c.profile_version_id for c in await profile_candidates(fixture.db)}
    assert copy in before
    pin = await _pin_it(fixture, copy)
    # Busy copies would rank first by use; the shot count is not what keeps them out.
    await fixture.db.execute(
        "INSERT INTO shots (device_id, raw_slog, profile_version_id, synced_at, updated_at) "
        "VALUES ('009999', x'00', ?, 'x', 'x')",
        (copy,),
    )

    assert copy not in {c.profile_version_id for c in await profile_candidates(fixture.db)}
    assert {c.profile_version_id for c in await profile_candidates(fixture.db)} == before - {copy}

    await PinsRepository(fixture.db).mark_unpinned(pin)
    assert copy not in {c.profile_version_id for c in await profile_candidates(fixture.db)}


@pytest.mark.parametrize("same_temperature", [True, False])
async def test_accepting_an_option_that_names_a_copy_is_a_409_and_changes_nothing(
    starting: StartingPointService,
    fixture: Fixture,
    provider: FakeProvider,
    same_temperature: bool,
) -> None:
    copy = await _a_lookalike(fixture)
    original = await ProfilesRepository(fixture.db).get_version(fixture.profile_version_id)
    assert original is not None and original.profile is not None
    temperature = original.profile.get("temperature")
    if not same_temperature:
        temperature = float(temperature or 90) - 3
    provider.script = [json.dumps(option_with(profile_version_id=copy, temperature_c=temperature))]
    run = await _propose(starting, fixture)
    # The suggestion was made while the profile was an ordinary one; it is pinned before the
    # person answers.
    await _pin_it(fixture, copy)
    sets_before = int(await fixture.db.fetch_value("SELECT COUNT(*) FROM sets"))
    drafts_before = int(await fixture.db.fetch_value("SELECT COUNT(*) FROM profile_drafts"))

    with pytest.raises(Conflict) as refused:
        await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]

    assert refused.value.code == "PROFILE_IS_A_PIN"
    assert int(await fixture.db.fetch_value("SELECT COUNT(*) FROM sets")) == sets_before
    assert int(await fixture.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")) == drafts_before
    again = await starting.get(run.id)  # type: ignore[attr-defined]
    assert again.accepted_option is None, "the run stays acceptable"


async def test_the_set_creation_step_answers_a_conflict_too(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The guard's twin at application: even a copy that got past the first look is a 409."""
    copy = await _a_lookalike(fixture)
    provider.script = [json.dumps(option_with(profile_version_id=copy))]
    run = await _propose(starting, fixture)
    await _pin_it(fixture, copy)
    option = StartingPointResult.model_validate(run.output).option("recommended")  # type: ignore[attr-defined]
    assert option is not None

    with pytest.raises(Conflict) as refused:
        await starting._create_set(run, option, None)  # type: ignore[arg-type]

    assert refused.value.code == "PROFILE_IS_A_PIN"


async def test_an_authored_option_that_is_exactly_a_copy_answers_the_wizards_409(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The wizard has no name field, so it says what the person can do: ask for another."""
    profiles = ProfilesRepository(fixture.db)
    authored, _ = await profiles.ensure_version(Profile.model_validate(GOOD_PROFILE), owner=None)
    # That very document is a pin's copy, and the pin has since been removed: no live profile
    # has the name, so only the pin's claim on the copy stands in the way.
    pin = await _pin_it(fixture, authored.id)
    assert await PinsRepository(fixture.db).unpin(pin)
    provider.script = [json.dumps(option_with(profile=GOOD_PROFILE))]
    run = await _propose(starting, fixture)
    sets_before = int(await fixture.db.fetch_value("SELECT COUNT(*) FROM sets"))

    with pytest.raises(Conflict) as refused:
        await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]

    assert "belongs to a pinned copy" in refused.value.message
    assert GOOD_PROFILE["label"] in refused.value.message
    assert int(await fixture.db.fetch_value("SELECT COUNT(*) FROM sets")) == sets_before
    assert (await starting.get(run.id)).accepted_option is None  # type: ignore[attr-defined]
