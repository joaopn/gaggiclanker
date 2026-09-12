"""Context assembly, and the golden prompt it renders to.

The golden file is `golden/starting-point-prompt.txt`, and it is the real check
on this module: everything else here asserts one fact at a time, while the
golden asserts that the whole document — the bag, the kit, three similar Sets
with their outcomes, the profile library and the selected rules — comes out
exactly as it did last time. A change to any of it is a diff in review.

Regenerate with `uv run pytest tests/starting -k golden --update-golden` and
read the diff before committing it.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.starting.context import DEFAULT_STYLE, build_context
from tests.starting.conftest import AS_OF, Fixture

GOLDEN = Path(__file__).resolve().parent / "golden" / "starting-point-prompt.txt"


async def _build(fixture: Fixture, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "bean_id": fixture.new_bean_id,
        "machine_id": fixture.machine_id,
        "grinder_id": fixture.grinder_id,
        "usual_grind": "22",
        "dose_hint_g": 18.0,
        "as_of": AS_OF,
    }
    kwargs.update(overrides)
    return await build_context(fixture.db, **kwargs)  # type: ignore[arg-type]


async def test_the_rendered_prompt_matches_the_golden_file(
    fixture: Fixture, update_golden: bool
) -> None:
    context = await _build(fixture)
    rendered = "\n\n".join(
        f"=== {name} ===\n{value}"
        for name, value in sorted(context.render().items())  # type: ignore[attr-defined]
    )

    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered + "\n", encoding="utf-8")
        return

    assert GOLDEN.exists(), "run with --update-golden to create it"
    assert rendered + "\n" == GOLDEN.read_text(encoding="utf-8")


async def test_two_builds_are_identical(fixture: Fixture) -> None:
    """Determinism, asserted directly rather than inferred from the golden."""
    first = await _build(fixture)
    second = await _build(fixture)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")  # type: ignore[attr-defined]
    assert first.render() == second.render()  # type: ignore[attr-defined]


async def test_days_off_roast_comes_from_as_of_not_from_now(fixture: Fixture) -> None:
    """Four days on the fixture's date, whatever day the suite runs on."""
    context = await _build(fixture)
    assert context.bean.days_off_roast == 4  # type: ignore[attr-defined]
    assert context.bean.freshness == "degassing"  # type: ignore[attr-defined]
    assert context.as_of == AS_OF  # type: ignore[attr-defined]


async def test_the_usual_grind_reaches_the_prompt_and_its_absence_is_loud(
    fixture: Fixture,
) -> None:
    """The one input that lets the answer be a number rather than a direction."""
    with_anchor = await _build(fixture)
    assert "22" in with_anchor.render()["hardware_facts"]  # type: ignore[attr-defined]

    without = await _build(fixture, usual_grind="")
    rendered = without.render()["hardware_facts"]  # type: ignore[attr-defined]
    assert "NOT GIVEN" in rendered


async def test_the_planned_style_comes_from_the_closest_similar_set(
    fixture: Fixture,
) -> None:
    """A style so the ratio and time rules are selected at all.

    With nothing comparable in the archive it falls back to `classic`, and it
    says which of the two happened — because "we guessed" and "we read it off
    your own Set" are different amounts of evidence.
    """
    context = await _build(fixture)
    assert context.planned_style != "unknown"  # type: ignore[attr-defined]
    assert context.planned_style_reason  # type: ignore[attr-defined]

    await fixture.db.execute("DELETE FROM shots")
    bare = await _build(fixture)
    assert bare.planned_style == DEFAULT_STYLE  # type: ignore[attr-defined]
    assert "nothing comparable" in bare.planned_style_reason  # type: ignore[attr-defined]


async def test_the_selected_rules_cover_the_categories_the_wizard_needs(
    fixture: Fixture,
) -> None:
    """Temperature, pressure, ratio, time, rest and increments, all present.

    `ratio_by_style` and `time_by_style` are the two that would silently vanish
    if the planned style were `unknown`, which is the whole reason it is not.
    """
    context = await _build(fixture)
    categories = {rule["category"] for rule in context.rules}  # type: ignore[attr-defined]
    assert {
        "temperature_by_roast",
        "pressure_matrix",
        "ratio_by_style",
        "time_by_style",
        "rest_times",
        "increments",
    } <= categories


async def test_the_signals_are_the_bag_not_a_shot(fixture: Fixture) -> None:
    """No channeling band, no taste — there is no shot yet."""
    context = await _build(fixture)
    assert "freshness:degassing" in context.signals  # type: ignore[attr-defined]
    assert "altitude:high" in context.signals  # type: ignore[attr-defined]
    assert not any(signal.startswith("taste:") for signal in context.signals)  # type: ignore[attr-defined]


async def test_the_profile_library_is_offered_most_used_first(fixture: Fixture) -> None:
    context = await _build(fixture)
    assert context.profiles  # type: ignore[attr-defined]
    counts = [entry.shot_count for entry in context.profiles]  # type: ignore[attr-defined]
    assert counts == sorted(counts, reverse=True)
    assert all(entry.shape for entry in context.profiles)  # type: ignore[attr-defined]


async def test_a_missing_bean_or_machine_is_a_lookup_error(fixture: Fixture) -> None:
    """The caller's mistake, so it becomes a 404 rather than a stored failure."""
    for kwargs in (
        {"bean_id": 9999},
        {"machine_id": 9999},
        {"grinder_id": 9999},
    ):
        try:
            await _build(fixture, **kwargs)
        except LookupError:
            continue
        raise AssertionError(f"expected a LookupError for {kwargs}")


async def test_an_empty_archive_says_so_rather_than_rendering_nothing(
    fixture: Fixture,
) -> None:
    """A blank block would read as "there is nothing to say"; this says which."""
    await fixture.db.execute("DELETE FROM shots")
    context = await _build(fixture)
    rendered = context.render()["similar_sets"]  # type: ignore[attr-defined]
    assert "Nothing comparable" in rendered
