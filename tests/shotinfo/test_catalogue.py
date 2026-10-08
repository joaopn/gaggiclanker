"""The catalogue itself: exactly the agreed rows, in order, with stable keys.

The rows are a design decision the maintainer agreed item by item — every
metric in its group, with its default tier — so the counts, the keys and the
base tier are pinned here rather than inferred. An item added, moved or
renamed fails this file until somebody says so on purpose; a renamed key would
otherwise silently drop whatever choice a person had made about it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.db.schema import create_schema
from gaggiclanker.shotinfo import catalogue
from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    GROUPS,
    ITEMS,
    default_tiers,
    effective_tiers,
    keys_in,
)

#: Rows per group, in the order the groups are rendered.
EXPECTED_GROUPS: tuple[tuple[str, int], ...] = (
    ("Checks", 2),
    ("Identity and status", 7),
    ("Outcome", 6),
    ("Timing", 4),
    ("Temperature", 4),
    ("Pressure", 4),
    ("Flow and volume", 6),
    ("Weight", 2),
    ("Puck resistance", 4),
    ("Profile compliance", 3),
    ("Phases", 32),
    ("Curve", 13),
    ("Your judgement", 8),
    ("The version's recipe", 5),
    ("The note typed on the machine", 6),
    ("Review", 3),
)

#: Every key, in catalogue order. A key is what a person's choice is stored
#: against, so it never changes once shipped.
EXPECTED_KEYS: tuple[str, ...] = (
    "checks",
    "checks_more",
    "shot_id",
    "started_at",
    "set_version",
    "label",
    "counted",
    "profile_as_brewed",
    "machine_shot_number",
    "shot_time",
    "yield",
    "yield_share",
    "exit_reason",
    "phases_not_reached",
    "phase_log_note",
    "cup_first_drip",
    "first_drip",
    "preinfusion_time",
    "main_extraction_time",
    "average_temperature",
    "target_temperature",
    "minimum_temperature",
    "maximum_temperature",
    "peak_pressure",
    "average_pressure",
    "minimum_pressure",
    "peak_pressure_time",
    "brew_cup_flow",
    "brew_flow",
    "average_flow",
    "peak_flow",
    "total_volume",
    "water_pumped",
    "water_minus_weight",
    "weight_rate",
    "resistance_level",
    "resistance_slope",
    "machine_puck_resistance",
    "liquid_resistance",
    "pressure_adherence",
    "flow_adherence",
    "pressure_undershoot_max",
    "phase_name",
    "phase_type",
    "phase_start",
    "phase_duration",
    "phase_ended_by",
    "phase_pressure",
    "phase_pressure_peak",
    "phase_pressure_end",
    "phase_temperature",
    "phase_temperature_min",
    "phase_temperature_target",
    "phase_volume",
    "phase_flow",
    "phase_flow_peak",
    "phase_scale_flow",
    "phase_scale_flow_peak",
    "phase_cup_end",
    "phase_cup_gained",
    "phase_cup_share",
    "phase_water",
    "phase_cup_first_drip",
    "phase_first_drip",
    "phase_pressure_adherence",
    "phase_flow_error",
    "phase_ramp",
    "phase_saturation",
    "phase_taper",
    "phase_resistance",
    "phase_resistance_slope",
    "phase_machine_resistance",
    "phase_liquid_resistance",
    "phase_samples",
    "curve_pressure",
    "curve_target_pressure",
    "curve_puck_flow",
    "curve_target_flow",
    "curve_weight",
    "curve_temperature",
    "curve_phase",
    "curve_pump_flow",
    "curve_scale_flow",
    "curve_estimated_weight",
    "curve_target_temperature",
    "curve_resistance",
    "curve_water_pumped",
    "rating",
    "balance",
    "taste_notes",
    "aroma_notes",
    "written_notes",
    "dose_in",
    "dose_out",
    "ratio",
    "recipe_grind",
    "recipe_dose",
    "recipe_yield",
    "recipe_profile",
    "profile_temperature",
    "note_rating",
    "note_balance",
    "note_doses",
    "note_grind",
    "note_bean",
    "note_text",
    "review_state",
    "review_claims",
    "review_prediction",
)

#: The per-phase items a chat sees without asking: each phase's name, duration, how it
#: ended, and the cup at its end (with its share of the target, in one item).
PHASE_LINE_ITEMS = frozenset({"phase_name", "phase_duration", "phase_ended_by", "phase_cup_end"})

#: The items a chat sees without asking.
EXPECTED_BASE: frozenset[str] = frozenset(
    {
        "checks",
        "review_state",
        "review_claims",
        "review_prediction",
        *PHASE_LINE_ITEMS,
        "shot_id",
        "started_at",
        "set_version",
        "label",
        "counted",
        "profile_as_brewed",
        "machine_shot_number",
        "shot_time",
        "yield",
        "exit_reason",
        "cup_first_drip",
        "first_drip",
        "peak_pressure",
        "brew_cup_flow",
        "brew_flow",
        "resistance_level",
        "pressure_adherence",
        "flow_adherence",
        "rating",
        "balance",
        "taste_notes",
        "aroma_notes",
        "written_notes",
        "dose_in",
        "dose_out",
        "ratio",
    }
)

#: The items no chat sees by default.
EXPECTED_EXCLUDED: frozenset[str] = frozenset(
    {
        "phase_samples",
        # Read in the chat as part of the cup at the phase's end; served to the page apart.
        "phase_cup_share",
        "curve_pump_flow",
        "curve_scale_flow",
        "curve_estimated_weight",
        "curve_target_temperature",
        "curve_resistance",
        "curve_water_pumped",
        "recipe_grind",
        "recipe_dose",
        "recipe_yield",
        "recipe_profile",
        "profile_temperature",
        "note_rating",
        "note_balance",
        "note_doses",
        "note_grind",
        "note_bean",
        "note_text",
    }
)


def test_the_groups_are_the_agreed_ones_with_the_agreed_row_counts() -> None:
    counted = tuple((group, sum(item.group == group for item in CATALOGUE)) for group in GROUPS)

    assert counted == EXPECTED_GROUPS
    assert len(CATALOGUE) == sum(count for _, count in EXPECTED_GROUPS)


def test_every_key_is_the_pinned_one_unique_and_snake_case() -> None:
    keys = tuple(item.key for item in CATALOGUE)

    assert keys == EXPECTED_KEYS
    assert len(set(keys)) == len(keys)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in keys)
    assert set(ITEMS) == set(keys)


def test_a_group_s_rows_are_together() -> None:
    """Iterating in order must visit each group once, as the renderer does."""
    seen: list[str] = []
    for item in CATALOGUE:
        if not seen or seen[-1] != item.group:
            seen.append(item.group)

    assert seen == list(GROUPS)


def test_the_default_tiers_are_the_agreed_ones() -> None:
    tiers = default_tiers()

    assert {key for key, tier in tiers.items() if tier == "base"} == EXPECTED_BASE
    assert {key for key, tier in tiers.items() if tier == "excluded"} == EXPECTED_EXCLUDED
    assert set(tiers) == set(EXPECTED_KEYS)


def test_the_firmware_analyzer_s_values_are_extended_never_base() -> None:
    keys = (
        "machine_puck_resistance",
        "liquid_resistance",
        "water_pumped",
        "water_minus_weight",
        "phase_machine_resistance",
        "phase_liquid_resistance",
    )

    for key in keys:
        assert ITEMS[key].default_tier == "extended", key
        assert not ITEMS[key].locked, key
    assert not set(keys) & keys_in("base", default_tiers())


def test_the_locked_items_are_the_three_the_agent_cannot_work_without_and_are_base() -> None:
    locked = {item.key for item in CATALOGUE if item.locked}

    assert locked == {"shot_id", "set_version", "counted"}
    assert all(ITEMS[key].default_tier == "base" for key in locked)


def test_every_item_can_render_and_is_named_and_explained() -> None:
    for item in CATALOGUE:
        renderers = [item.shot, item.phase, item.channel]
        assert sum(renderer is not None for renderer in renderers) == 1, item.key
        assert item.name.strip(), item.key
        assert item.label.strip(), item.key
        assert item.meaning.strip(), item.key


def test_phase_and_curve_items_live_in_their_own_groups() -> None:
    for item in CATALOGUE:
        assert (item.kind == "phase") == (item.group == "Phases"), item.key
        assert (item.kind == "curve") == (item.group == "Curve"), item.key


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "tiers.db")
    await database.connect()
    await create_schema(database)
    try:
        yield database
    finally:
        await database.close()


async def test_effective_tiers_are_the_defaults_until_somebody_moves_an_item(
    db: Database,
) -> None:
    assert dict(await effective_tiers(db)) == dict(default_tiers())


async def test_effective_tiers_lay_the_person_s_choices_over_the_defaults(db: Database) -> None:
    repo = ShotInfoTiersRepository(db)
    await repo.set_tier(ShotInfoTierWrite(item_key="peak_flow", tier="base"))
    await repo.set_tier(ShotInfoTierWrite(item_key="rating", tier="excluded"))

    tiers = await effective_tiers(db)

    assert dict(tiers) == {**default_tiers(), "peak_flow": "base", "rating": "excluded"}
    assert "peak_flow" in keys_in("base", tiers)
    assert "rating" not in keys_in("full", tiers)


@pytest.mark.parametrize(("key", "reason"), [("retired_item", "unknown"), ("shot_id", "locked")])
async def test_a_stored_choice_the_catalogue_cannot_honour_is_ignored_and_logged_once(
    db: Database, key: str, reason: str
) -> None:
    """A key a later release removed, or a locked item a hand-written row moved."""
    # Past the write model on purpose: the route and the model both refuse
    # these, and the reader must still survive a row that got in another way.
    await db.execute("INSERT INTO shot_info_tiers (item_key, tier) VALUES (?, 'excluded')", (key,))
    await ShotInfoTiersRepository(db).set_tier(
        ShotInfoTierWrite(item_key="rating", tier="extended")
    )
    catalogue._IGNORED_LOGGED.discard(key)

    with capture_logs() as logged:
        first = await effective_tiers(db)
        second = await effective_tiers(db)

    assert dict(first) == dict(second) == {**default_tiers(), "rating": "extended"}
    assert [entry for entry in logged if entry["event"] == "shot_info_override_ignored"] == [
        {
            "event": "shot_info_override_ignored",
            "item_key": key,
            "reason": reason,
            "log_level": "warning",
        }
    ]


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("base", EXPECTED_BASE),
        ("extended", frozenset(EXPECTED_KEYS) - EXPECTED_BASE - EXPECTED_EXCLUDED),
        ("full", frozenset(EXPECTED_KEYS) - EXPECTED_EXCLUDED),
    ],
)
def test_a_rendering_s_keys_follow_its_tier(tier: str, expected: frozenset[str]) -> None:
    assert keys_in(tier, default_tiers()) == expected  # type: ignore[arg-type]


def test_an_item_moved_to_another_tier_moves_with_it() -> None:
    """The tiers are an argument, not a constant: a person's overrides just work."""
    moved = {**default_tiers(), "peak_flow": "base", "rating": "excluded"}

    assert "peak_flow" in keys_in("base", moved)
    assert "rating" not in keys_in("full", moved)


def test_every_group_note_belongs_to_a_group() -> None:
    assert set(GROUP_NOTES) <= set(GROUPS)
    assert all(note.strip() for note in GROUP_NOTES.values())


#: What origin/dev's catalogue showed a chat without asking: every one of these
#: is still shown, except the two that were retired outright (the execution score,
#: which asked only whether the machine followed its profile, and the channeling
#: risk, which has no number behind it). A band that was one of them is its number.
ORIGIN_DEV_BASE = frozenset(
    {
        "shot_id",
        "started_at",
        "set_version",
        "label",
        "counted",
        "profile_as_brewed",
        "machine_shot_number",
        "shot_time",
        "yield",
        "exit_reason",
        "execution_score",
        "first_drip",
        "peak_pressure",
        "brew_flow",
        "resistance_level",
        "channeling_risk",
        "pressure_adherence",
        "flow_adherence",
        "rating",
        "balance",
        "taste_notes",
        "aroma_notes",
        "written_notes",
        "dose_in",
        "dose_out",
        "ratio",
    }
)
RETIRED_FROM_BASE = frozenset({"execution_score", "channeling_risk"})


def test_nothing_a_chat_saw_without_asking_is_gone_but_the_score_and_the_channeling_risk() -> None:
    now = {key for key, tier in default_tiers().items() if tier == "base"}

    assert ORIGIN_DEV_BASE - RETIRED_FROM_BASE <= now
    assert (ORIGIN_DEV_BASE - now) == RETIRED_FROM_BASE
    # What base gained is the checks, one line per phase and what a person confirmed of a
    # reading (it was extended before: the claims are short and are the chat's to weigh), and
    # the two cup numbers (first drip and brew flow as the scale measured them), and nothing else.
    assert (
        now - ORIGIN_DEV_BASE
        == {
            "checks",
            "review_state",
            "review_claims",
            "review_prediction",
            "cup_first_drip",
            "brew_cup_flow",
        }
        | PHASE_LINE_ITEMS
    )
