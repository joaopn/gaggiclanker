"""What the shot information says about adherence, by what the profile steers by.

A pressure profile has no flow to follow ("not applicable") and a shot with no
known profile has nothing graded ("not graded"); the shot information shows
neither as a number, and above all never as `0.00`. The item is left out, as
every value the machine did not record is.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.shotinfo import catalogue
from gaggiclanker.shotinfo.catalogue import ITEMS, ShotTier, default_tiers
from gaggiclanker.shotinfo.downsample import CURVE_POINTS
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import load_shots, render_shot, shot_lines
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import constructed_profile
from tests.shotinfo.conftest import SLOG, Archive

FLOW_KEYS = ("flow_adherence", "flow_overshoot_max", "flow_undershoot_max")
PRESSURE_KEYS = ("pressure_adherence", "pressure_overshoot_max", "pressure_undershoot_max")
TIERS: tuple[ShotTier, ...] = ("base", "extended", "full")


async def _facts(archive: Archive, variant: str | None, device_id: str) -> ShotFacts:
    """Shot 204, derived with one of its constructed profiles, or with none."""
    profile = None if variant is None else constructed_profile("shot_204", variant)
    slog = parse_slog(SLOG.read_bytes())
    derived = derive_shot(slog, SLOG.read_bytes(), device_id=device_id, profile=profile)
    shot_id = await ShotsRepository(archive.db).insert(derived.shot, derived.samples)
    [facts] = await load_shots(archive.db, [shot_id])
    return facts


def _keys(facts: ShotFacts) -> set[str]:
    return {line.key for line in shot_lines(facts, frozenset(ITEMS))}


@pytest.mark.parametrize("tier", TIERS)
async def test_a_pressure_profile_shows_pressure_adherence_and_no_flow_line(
    archive: Archive, tier: ShotTier
) -> None:
    facts = await _facts(archive, "pressure-first", "000801")
    keys = _keys(facts)
    assert "pressure_adherence" in keys
    assert not keys & set(FLOW_KEYS)
    assert "phase_flow_error" not in keys

    text = render_shot(facts, tier, default_tiers(), curve_points=CURVE_POINTS)
    assert "Flow adherence" not in text
    assert "flow error" not in text
    assert "0.00 ml/s POOR" not in text


@pytest.mark.parametrize("tier", TIERS)
async def test_a_shot_with_no_known_profile_shows_no_adherence_at_all(
    archive: Archive, tier: ShotTier
) -> None:
    facts = await _facts(archive, None, "000802")
    keys = _keys(facts)
    assert not keys & (set(FLOW_KEYS) | set(PRESSURE_KEYS))
    assert "phase_flow_error" not in keys
    assert "phase_pressure_adherence" not in keys

    text = render_shot(facts, tier, default_tiers(), curve_points=CURVE_POINTS)
    assert "adherence" not in text.lower()
    assert "pressure overshoot" not in text.lower()
    assert "flow overshoot" not in text.lower()


async def test_a_flow_steered_first_phase_shows_its_flow_adherence(archive: Archive) -> None:
    facts = await _facts(archive, "flow-first", "000803")
    lines = {line.key: line.value for line in shot_lines(facts, frozenset(ITEMS)) if not line.phase}
    assert lines["flow_adherence"].endswith("EXCELLENT")
    phase_errors = [
        line for line in shot_lines(facts, frozenset(ITEMS)) if line.key == "phase_flow_error"
    ]
    assert [line.phase for line in phase_errors] == [0]  # the flow phase, and only it


async def test_the_search_bands_are_absent_when_the_profile_did_not_ask_for_them(
    archive: Archive,
) -> None:
    """The search filters on these bands: a shot with no value never matches."""
    pressure = await _facts(archive, "pressure-first", "000804")
    assert catalogue.pressure_adherence_band(pressure) == "POOR"
    assert catalogue.flow_adherence_band(pressure) is None

    ungraded = await _facts(archive, None, "000805")
    assert catalogue.pressure_adherence_band(ungraded) is None
    assert catalogue.flow_adherence_band(ungraded) is None

    flow = await _facts(archive, "flow-first", "000806")
    assert catalogue.flow_adherence_band(flow) == "EXCELLENT"


async def test_the_execution_score_detail_reads_the_new_penalties(archive: Archive) -> None:
    pressure = await _facts(archive, "pressure-first", "000807")
    score: Any = pressure.score
    assert "flow_adherence" not in score["components"]
    assert score["confidence"] == "high"

    ungraded = await _facts(archive, None, "000808")
    assert ungraded.score["confidence"] == "medium"
