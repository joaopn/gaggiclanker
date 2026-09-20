"""Accepting and rejecting advice.

The second acceptance criterion for the analyzer lives here: accepting a grind suggestion
produces a new version whose parent is the shot's version and whose only changed
field is the grind. The refusals matter as much — every one of them is a case
where applying the number would have produced a recipe nobody chose.
"""

from __future__ import annotations

import json

import pytest

from gaggiclanker.analyzer.service import AnalyzerService
from gaggiclanker.analyzer.suggestions import accept_suggestion, reject_suggestion
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, version_changes
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from tests.analyzer.conftest import GOOD_OUTPUT, Fixture
from tests.llm.conftest import FakeProvider


async def _advice(analyzer: AnalyzerService, fixture: Fixture) -> list[int]:
    row = await analyzer.run_analysis(fixture.shots[-1])
    return [item.id for item in row.suggestions]


async def test_accepting_a_grind_suggestion_changes_only_the_grind(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    """The chunk's second acceptance criterion, in full."""
    grind_id, *_ = await _advice(analyzer, fixture)

    suggestion, version = await accept_suggestion(fixture.db, grind_id)

    assert suggestion.status == "accepted"
    assert suggestion.resulting_set_version_id == version.id

    assert version.parent_version_id == fixture.version_id, "parent is the shot's own version"
    assert version.version_no == 2
    assert version.origin == "analysis"
    assert version.origin_analysis_id == suggestion.analysis_id

    sets = SetsRepository(fixture.db)
    parent = await sets.get_version(fixture.version_id)
    assert parent is not None
    changed = {change.field for change in version_changes(version, parent)}
    assert changed == {"grind_setting", "grind_value"}

    # "two steps finer" on a grinder that numbers up for coarser.
    assert version.grind_value == 20.0
    # The words are kept: "22 numbers" becomes "20 numbers", not "20".
    assert version.grind_setting == "20 numbers"
    # And everything else is inherited untouched — including the profile, which
    # is where the brew temperature comes from.
    assert (version.dose_g, version.target_yield_g) == (18.0, 36.0)
    assert version.profile_temperature_c == parent.profile_temperature_c

    # The intent says what moved and why, so the timeline reads as a conversation.
    assert version.intent.startswith("grind finer 2 grinder_steps (22 -> 20):")
    assert "28 s target" in version.intent


async def test_accepting_supersedes_its_siblings_for_the_same_variable(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    row = await analyzer.run_analysis(fixture.shots[-1])
    # A second grind suggestion in the same analysis, as a lower-priority backup.
    from gaggiclanker.db.repos.analyses import SuggestionsRepository, SuggestionWrite

    suggestions = SuggestionsRepository(fixture.db)
    extra = await suggestions.insert_many(
        row.id,
        [
            SuggestionWrite(
                variable="grind", direction="coarser", magnitude=1, unit="grinder_steps", priority=4
            )
        ],
    )

    await accept_suggestion(fixture.db, row.suggestions[0].id)

    after = {item.variable: item.status for item in await suggestions.for_analysis(row.id)}
    sibling = await suggestions.get(extra[0])
    assert sibling is not None
    assert sibling.status == "superseded", "overtaken, not rejected — nobody disagreed with it"
    # The other variables are untouched: they are still live advice.
    assert after["yield"] == "open"
    assert after["pressure"] == "open"


async def test_rejecting_leaves_the_backups_open(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    grind_id, yield_id, _ = await _advice(analyzer, fixture)

    rejected = await reject_suggestion(fixture.db, grind_id)
    assert rejected.status == "rejected"

    from gaggiclanker.db.repos.analyses import SuggestionsRepository

    backup = await SuggestionsRepository(fixture.db).get(yield_id)
    assert backup is not None
    assert backup.status == "open"


async def test_a_resolved_suggestion_cannot_be_resolved_again(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    grind_id, *_ = await _advice(analyzer, fixture)
    await accept_suggestion(fixture.db, grind_id)

    with pytest.raises(Conflict, match="already accepted"):
        await accept_suggestion(fixture.db, grind_id)
    with pytest.raises(Conflict, match="already accepted"):
        await reject_suggestion(fixture.db, grind_id)


async def test_a_non_actionable_variable_is_refused_with_a_reason(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    """A profile change has no Set field, and the refusal names the route that has.

    There *is* now somewhere for a pressure suggestion to go — a
    profile draft, four validation layers and a push that never overwrites — so
    the refusal is no longer "this cannot be done" but "not here, and here is
    where". Accepting it on this path would mean guessing a number and writing
    it to the machine in one step, which is the thing the draft flow exists to
    prevent.
    """
    *_, pressure_id = await _advice(analyzer, fixture)

    with pytest.raises(Conflict) as caught:
        await accept_suggestion(fixture.db, pressure_id)

    assert "pressure suggestion cannot be applied" in caught.value.message
    assert "/api/profile-drafts" in str(caught.value.details)


async def test_a_temperature_suggestion_is_a_profile_change_now(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """Good advice with nowhere on the Set to put it.

    The model may still say "a degree hotter" and it may well be right — the
    machine brews at the profile's temperature, so the way to act on it is a
    profile draft, and the refusal says so rather than writing a number that
    would change nothing in the cup.
    """
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                suggestions=[
                    {
                        "variable": "temperature",
                        "direction": "increase",
                        "magnitude": 1,
                        "unit": "c",
                        "reason": "sour on the sweet spot",
                        "confidence": "medium",
                        "priority": 1,
                    }
                ],
            )
        )
    ]
    row = await analyzer.run_analysis(fixture.shots[-1])

    with pytest.raises(Conflict) as caught:
        await accept_suggestion(fixture.db, row.suggestions[0].id)

    assert "temperature suggestion cannot be applied" in caught.value.message
    details = str(caught.value.details)
    assert "the machine brews at the temperature the profile states" in details
    assert "/api/profile-drafts" in details
    # Refused means nothing written: the Set is still on its first version.
    assert len(await SetsRepository(fixture.db).versions(fixture.set_id)) == 1


async def test_a_suggestion_about_a_stale_version_is_refused(
    analyzer: AnalyzerService, fixture: Fixture
) -> None:
    """A delta belongs to the numbers it was given.

    The Set moving on between the analysis and the accept is the case where
    applying it would compound two changes into one.
    """
    grind_id, *_ = await _advice(analyzer, fixture)
    await SetsRepository(fixture.db).add_version(
        fixture.set_id, SetVersionPatch(dose_g=19.0, intent="bigger basket")
    )

    with pytest.raises(Conflict) as caught:
        await accept_suggestion(fixture.db, grind_id)

    assert "version 1" in caught.value.message
    assert "version 2" in caught.value.message


async def test_a_version_with_no_number_to_change_is_refused(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    # A Set version that records no dose at all.
    await SetsRepository(fixture.db).add_version(
        fixture.set_id,
        SetVersionPatch.model_validate({"dose_g": None, "intent": "stopped weighing the dose"}),
    )
    from gaggiclanker.db.repos.sets import SetsRepository as Repo

    current = await Repo(fixture.db).current_version(fixture.set_id)
    assert current is not None
    await Repo(fixture.db).assign_shot(fixture.shots[-1], current.id)

    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                suggestions=[
                    {
                        "variable": "dose",
                        "direction": "increase",
                        "magnitude": 1,
                        "unit": "g",
                        "reason": "thin in the cup",
                        "confidence": "medium",
                        "priority": 1,
                    }
                ],
            )
        )
    ]
    row = await analyzer.run_analysis(fixture.shots[-1])

    with pytest.raises(Conflict, match="no dose"):
        await accept_suggestion(fixture.db, row.suggestions[0].id)


async def test_a_direction_that_does_not_fit_the_variable_is_refused(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """`finer` on a dose is a category error, not a unit mismatch."""
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                suggestions=[
                    {
                        "variable": "dose",
                        "direction": "finer",
                        "magnitude": 1,
                        "unit": "g",
                        "reason": "nonsense",
                        "confidence": "low",
                        "priority": 1,
                    }
                ],
            )
        )
    ]
    row = await analyzer.run_analysis(fixture.shots[-1])

    with pytest.raises(Unprocessable, match="does not apply to dose"):
        await accept_suggestion(fixture.db, row.suggestions[0].id)


async def test_a_hold_has_nothing_to_apply(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                suggestions=[
                    {
                        "variable": "grind",
                        "direction": "hold",
                        "magnitude": None,
                        "unit": "none",
                        "reason": "the grind is right, look elsewhere",
                        "confidence": "high",
                        "priority": 1,
                    }
                ],
            )
        )
    ]
    row = await analyzer.run_analysis(fixture.shots[-1])

    with pytest.raises(Conflict, match="nothing to apply"):
        await accept_suggestion(fixture.db, row.suggestions[0].id)


async def test_a_change_outside_the_bounds_is_refused_not_clamped(
    analyzer: AnalyzerService, fixture: Fixture, provider: FakeProvider
) -> None:
    """A clamped value is a number nobody chose, and the next shot would use it."""
    provider.script = [
        json.dumps(
            dict(
                GOOD_OUTPUT,
                suggestions=[
                    {
                        "variable": "dose",
                        "direction": "decrease",
                        "magnitude": 80,
                        "unit": "g",
                        "reason": "wildly wrong",
                        "confidence": "low",
                        "priority": 1,
                    }
                ],
            )
        )
    ]
    row = await analyzer.run_analysis(fixture.shots[-1])

    with pytest.raises(Unprocessable, match="outside its allowed range"):
        await accept_suggestion(fixture.db, row.suggestions[0].id)

    versions = await SetsRepository(fixture.db).versions(fixture.set_id)
    assert len(versions) == 1, "a refused accept writes nothing"


async def test_a_missing_suggestion_is_a_404(fixture: Fixture) -> None:
    with pytest.raises(NotFound):
        await accept_suggestion(fixture.db, 999_999)
    with pytest.raises(NotFound):
        await reject_suggestion(fixture.db, 999_999)
