"""The service: a row, a call, an outcome, and the Set an accept creates.

No real provider anywhere. The fake is scripted per test (`tests/llm/conftest.py`)
so "it stored a failed row" and "it created the Set with origin starting_point"
are assertions about the database rather than about a mock library.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.starting import StartingPointRunsRepository
from gaggiclanker.infra.errors import Conflict, Unprocessable
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.service import LlmService
from gaggiclanker.starting.models import StartingPointResult
from gaggiclanker.starting.service import StartingPointService, _grind_value
from tests.llm.conftest import FakeProvider
from tests.starting.conftest import (
    AS_OF,
    GOOD_PROFILE,
    REFUSED_PROFILE,
    THREE_PHASE_PROFILE,
    Fixture,
    option_with,
)


async def _propose(starting: StartingPointService, fixture: Fixture, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "bean_id": fixture.new_bean_id,
        "machine_id": fixture.machine_id,
        "grinder_id": fixture.grinder_id,
        "usual_grind": "22",
        "as_of": AS_OF,
    }
    kwargs.update(overrides)
    return await starting.propose(**kwargs)  # type: ignore[arg-type]


# ── proposing ────────────────────────────────────────────────────────


async def test_a_good_call_stores_an_ok_row_with_three_options(
    starting: StartingPointService, fixture: Fixture
) -> None:
    run = await _propose(starting, fixture)
    assert run.status == "ok"  # type: ignore[attr-defined]
    assert run.error is None  # type: ignore[attr-defined]
    result = StartingPointResult.model_validate(run.output)  # type: ignore[attr-defined]
    assert [option.option for option in result.options] == [
        "conservative",
        "recommended",
        "adventurous",
    ]
    # The context is snapshotted verbatim, so the suggestion stays explainable.
    assert run.input["bean"]["name"] == "Kenya Nyeri"  # type: ignore[attr-defined]
    assert run.input["similar"], "the fixture archive has anchors"  # type: ignore[attr-defined]
    assert run.usual_grind == "22"  # type: ignore[attr-defined]


async def test_a_provider_failure_is_a_stored_row_not_an_exception(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """The row is the handle the page renders; an error with no id explains nothing."""
    provider.script = [LlmApiError("nope", status=401)]
    run = await _propose(starting, fixture)
    assert run.status == "failed"  # type: ignore[attr-defined]
    assert run.error is not None and run.error.startswith("auth:")  # type: ignore[attr-defined]
    assert run.output is None  # type: ignore[attr-defined]


async def test_output_that_does_not_validate_is_the_invalid_output_path(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A fourth option key, twice — the corrective turn is spent and it still fails."""
    broken = option_with()
    broken["options"][2]["option"] = "balanced"
    provider.script = [json.dumps(broken)]
    run = await _propose(starting, fixture)
    assert run.status == "failed"  # type: ignore[attr-defined]
    assert run.error is not None and run.error.startswith("invalid_output:")  # type: ignore[attr-defined]


async def test_citations_the_run_was_not_given_are_dropped(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A fabricated citation is dropped rather than throwing three options away."""
    invented = option_with(
        rules_used=["light", "a_rule_that_does_not_exist"],
        excerpts_used=["NOWHERE#at-all"],
        similar_set_version_ids=[9999],
        profile_version_id=4242,
    )
    provider.script = [json.dumps(invented)]
    run = await _propose(starting, fixture)
    assert run.status == "ok"  # type: ignore[attr-defined]
    option = StartingPointResult.model_validate(run.output).option("recommended")  # type: ignore[attr-defined]
    assert option is not None
    assert option.rules_used == ["light"]
    assert option.excerpts_used == []
    assert option.similar_set_version_ids == []
    # A profile id the model was never shown is *cleared*, not kept: a Set
    # version pointing at an arbitrary profile would change which shots
    # auto-assignment attaches to it.
    assert option.profile_version_id is None


async def test_a_real_profile_version_id_survives(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [json.dumps(option_with(profile_version_id=fixture.profile_version_id))]
    run = await _propose(starting, fixture)
    option = StartingPointResult.model_validate(run.output).option("recommended")  # type: ignore[attr-defined]
    assert option is not None
    assert option.profile_version_id == fixture.profile_version_id


async def test_a_missing_bean_raises_rather_than_storing_a_failed_row(
    starting: StartingPointService, fixture: Fixture
) -> None:
    with pytest.raises(LookupError):
        await _propose(starting, fixture, bean_id=9999)
    assert await StartingPointRunsRepository(fixture.db).for_bean(9999) == []


# ── accepting ────────────────────────────────────────────────────────


async def test_accepting_creates_the_set_with_origin_starting_point(
    starting: StartingPointService, fixture: Fixture
) -> None:
    run = await _propose(starting, fixture)
    accepted = await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]

    assert accepted.set_row.bean_id == fixture.new_bean_id
    assert accepted.set_row.machine_id == fixture.machine_id
    assert accepted.set_row.grinder_id == fixture.grinder_id
    # Named after the bag and the grinder: the same bean on a second grinder is
    # exactly the comparison this feature invites.
    assert accepted.set_row.name == "Kenya Nyeri on the Niche Zero"

    version = accepted.version
    assert version.version_no == 1
    assert version.origin == "starting_point"
    assert version.dose_g == 18.0
    assert version.target_yield_g == 45.0
    assert version.target_temperature_c == 94.0
    assert version.grind_setting == "20"
    # The numeric half is only filled in for a setting the model called
    # absolute — plotting "two clicks finer" as 2 would draw a meaningless line.
    assert version.grind_value == 20.0
    assert "Light roasts run 93-96" in version.intent

    assert accepted.draft is None
    assert accepted.run.accepted_option == "recommended"
    assert accepted.run.accepted_set_id == accepted.set_row.id
    assert accepted.run.accepted_set_version_id == version.id


async def test_a_relative_grind_is_stored_as_text_with_no_value(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [
        json.dumps(
            option_with(
                grind_setting="two steps finer than your usual espresso setting",
                grind_is_absolute=False,
            )
        )
    ]
    run = await _propose(starting, fixture, usual_grind="")
    accepted = await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]
    assert (accepted.version.grind_setting or "").startswith("two steps finer")
    assert accepted.version.grind_value is None


async def test_an_option_carrying_a_profile_becomes_a_draft_the_set_points_at(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Through `create_manual`, so it gets the same four layers a typed one gets."""
    provider.script = [json.dumps(option_with(profile=GOOD_PROFILE))]
    run = await _propose(starting, fixture)
    accepted = await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]

    assert accepted.draft is not None
    assert accepted.draft.status == "draft"
    # The Set means the drafted profile from now on, even though nobody has
    # pushed it to the machine yet.
    assert accepted.version.profile_version_id == accepted.draft.draft_version_id
    assert accepted.run.accepted_draft_id == accepted.draft.id
    # ... and the person reading the timeline is told a draft is waiting.
    assert f"draft #{accepted.draft.id}" in accepted.version.intent.lower()


async def test_a_profile_the_policy_refuses_never_becomes_an_ok_run(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Eleven phases fails the output model, so the run fails rather than misleads.

    Before the output model checked the policy this came back `ok` with a card
    that could not be taken. The user has paid for the call either way; this way
    they are told.
    """
    provider.script = [json.dumps(option_with(profile=REFUSED_PROFILE))]
    run = await _propose(starting, fixture)
    assert run.status == "failed"  # type: ignore[attr-defined]
    assert run.error is not None and run.error.startswith("invalid_output:")  # type: ignore[attr-defined]

    rows = await SetsRepository(fixture.db).list_sets(include_archived=True)
    assert not any(row.bean_id == fixture.new_bean_id for row in rows), "nothing was created"


async def test_a_profile_the_configured_bounds_refuse_is_a_422_and_creates_nothing(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider, llm: LlmService
) -> None:
    """The accept path still re-checks, against the bounds as they are *now*.

    The output model validates against the policy's defaults, because a pydantic
    validator cannot reach the settings service. Somebody who narrows the policy
    afterwards must still get their refusal — and nothing may be half-created
    when they do: a Set pointing at a profile that was rejected is worse than no
    Set.
    """
    provider.script = [json.dumps(option_with(profile=THREE_PHASE_PROFILE))]
    run = await _propose(starting, fixture)
    assert run.status == "ok"  # type: ignore[attr-defined]

    await llm.settings.apply({"profilePolicyMaxPhases": 2})

    with pytest.raises(Unprocessable) as raised:
        await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]
    assert raised.value.status == 422
    assert raised.value.details, "the refusal names every violation at once"

    rows = await SetsRepository(fixture.db).list_sets(include_archived=True)
    assert not any(row.bean_id == fixture.new_bean_id for row in rows), "no half-created Set"
    assert rows, "the fixture's own Sets are untouched"

    # And the run is acceptable again: the other two options may be fine, and a
    # claim that could not be kept must not lock them out for ever.
    reread = await StartingPointRunsRepository(fixture.db).get(run.id)  # type: ignore[attr-defined]
    assert reread is not None and reread.accepted_option is None


async def test_two_accepts_at_once_make_one_set_and_one_409(
    starting: StartingPointService, fixture: Fixture
) -> None:
    """The claim runs before anything is created, which is the whole fix.

    With the guard as the *last* write both requests created a Set and only the
    second was told off. Gathered rather than sequential because the race needs
    both to get past the "is it accepted yet" read before either writes.
    """
    run = await _propose(starting, fixture)
    results = await asyncio.gather(
        starting.accept(run.id, "recommended"),  # type: ignore[attr-defined]
        starting.accept(run.id, "conservative"),  # type: ignore[attr-defined]
        return_exceptions=True,
    )

    accepted = [item for item in results if not isinstance(item, BaseException)]
    refused = [item for item in results if isinstance(item, Conflict)]
    assert len(accepted) == 1, results
    assert len(refused) == 1, results
    assert refused[0].status == 409

    sets = [
        row
        for row in await SetsRepository(fixture.db).list_sets(include_archived=True)
        if row.bean_id == fixture.new_bean_id
    ]
    assert len(sets) == 1, "one run, one Set — whatever the interleaving"

    # The loser names the option that won. It may *not* yet name the Set: the
    # claim is written before the Set exists, so a refusal that arrives inside
    # that window has nothing to point at. That is the right trade — a lost race
    # with a thin error beats two Sets with a helpful one — and every later
    # press gets the ids, which is the case a person actually hits.
    assert refused[0].details["option"] in ("recommended", "conservative")

    with pytest.raises(Conflict) as later:
        await starting.accept(run.id, "adventurous")  # type: ignore[attr-defined]
    assert later.value.details["set_id"] == sets[0].id


async def test_accepting_twice_is_a_409_carrying_what_the_first_one_made(
    starting: StartingPointService, fixture: Fixture
) -> None:
    run = await _propose(starting, fixture)
    first = await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]

    with pytest.raises(Conflict) as raised:
        await starting.accept(run.id, "conservative")  # type: ignore[attr-defined]
    assert raised.value.status == 409
    assert raised.value.details["set_id"] == first.set_row.id
    assert raised.value.details["option"] == "recommended"


async def test_accepting_a_run_that_is_not_finished_is_a_409(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [LlmApiError("nope", status=401)]
    run = await _propose(starting, fixture)
    with pytest.raises(Conflict):
        await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]


async def test_accepting_an_option_key_that_is_not_one_of_the_three_is_a_422(
    starting: StartingPointService, fixture: Fixture
) -> None:
    run = await _propose(starting, fixture)
    with pytest.raises(Unprocessable):
        await starting.accept(run.id, "balanced")  # type: ignore[attr-defined]


async def test_a_draft_with_no_profile_in_the_library_gets_a_synthetic_base(
    starting: StartingPointService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A draft is always derived from a version, because the diff is how it is read."""
    await fixture.db.execute("UPDATE shots SET profile_version_id = NULL")
    await fixture.db.execute("UPDATE set_versions SET profile_version_id = NULL")
    await fixture.db.execute("DELETE FROM profile_versions")
    provider.script = [json.dumps(option_with(profile=GOOD_PROFILE))]

    run = await _propose(starting, fixture)
    accepted = await starting.accept(run.id, "recommended")  # type: ignore[attr-defined]
    assert accepted.draft is not None
    assert accepted.draft.base_version_id != accepted.draft.draft_version_id


# ── the grind-value parser ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("setting", "absolute", "expected"),
    [
        ("20", True, 20.0),
        ("7.5", True, 7.5),
        # The first number wins: the text is what the user reads back and 3 is
        # what the chart plots. See `_grind_value` for why not 3.5.
        ("between 3 and 4", True, 3.0),
        ("22 numbers", True, 22.0),
        ("two steps finer", True, None),
        ("20", False, None),
    ],
)
def test_grind_value_only_parses_a_setting_the_model_called_absolute(
    setting: str, absolute: bool, expected: float | None
) -> None:
    from gaggiclanker.starting.models import StartingPointOption

    option = StartingPointOption(
        option="recommended",
        headline="x",
        grind_setting=setting,
        grind_is_absolute=absolute,
        dose_g=18,
        yield_g=36,
        ratio=2,
        temperature_c=93,
        rationale="x",
    )
    assert _grind_value(option) == expected
