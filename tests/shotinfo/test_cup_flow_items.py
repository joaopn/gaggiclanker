"""One meaning for "first drip": when coffee first reached the cup.

With a scale the shot has the cup's own first drip and brew cup flow; without one it has
the puck-flow estimate, labelled as one, and no cup number at all. Built on the real fixture
shot and the same recording with its weight and flow zeroed, as a board with no scale logs it.
"""

from __future__ import annotations

import dataclasses

from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.shotinfo import default_tiers, load_shots, shot_lines
from gaggiclanker.shotinfo.catalogue import CATALOGUE, ITEMS
from gaggiclanker.shotinfo.fields import shot_fields_of
from gaggiclanker.shotinfo.render import render_shot
from tests.shotinfo.conftest import Archive

DRIP = frozenset({"cup_first_drip", "first_drip"})
FLOW = frozenset({"brew_cup_flow", "brew_flow"})


async def test_a_shot_with_a_scale_shows_the_cups_first_drip_and_cup_flow(archive: Archive) -> None:
    [facts] = await load_shots(archive.db, [archive.shot], samples=False)

    assert facts.has_scale
    lines = {line.key: line.value for line in shot_lines(facts, DRIP | FLOW)}
    assert set(lines) == {"cup_first_drip", "brew_cup_flow", "brew_flow"}
    assert lines["cup_first_drip"] == "24.0 s"
    assert lines["brew_cup_flow"].endswith(" g/s")
    assert ITEMS["cup_first_drip"].label == "First drip"
    served = {field.key: field for field in shot_fields_of(facts).shot}
    assert served["cup_first_drip"].value == 24.0
    assert "first_drip" not in served


async def test_a_shot_without_a_scale_shows_the_estimate_and_no_cup_number(
    archive: Archive,
) -> None:
    [facts] = await load_shots(archive.db, [archive.no_scale], samples=False)

    assert not facts.has_scale
    lines = {line.key: line.value for line in shot_lines(facts, DRIP | FLOW)}
    assert set(lines) == {"first_drip", "brew_flow"}
    assert lines["first_drip"].endswith(" s")
    assert ITEMS["first_drip"].label == "First drip (estimated)"
    served = {field.key for field in shot_fields_of(facts).shot}
    assert not served & {"cup_first_drip", "brew_cup_flow"}


async def test_the_cup_items_are_in_the_base_tier_beside_the_puck_ones() -> None:
    tiers = default_tiers()
    assert {tiers[key] for key in DRIP | FLOW} == {"base"}


async def _stored_variant(archive: Archive, change: str, device_id: str) -> int:
    from gaggiclanker.domain.slog import parse_slog
    from tests.shotinfo.conftest import SLOG, _insert

    slog = parse_slog(SLOG.read_bytes())
    if change == "zeroed, flag kept":
        samples = [s.model_copy(update={"v": 0.0, "vf": 0.0}) for s in slog.samples]
    else:  # a scale shot whose connection bit was never set
        samples = [s.model_copy(update={"si": (s.si or 0) & ~0x0004}) for s in slog.samples]
    return await _insert(archive.db, dataclasses.replace(slog, samples=samples), device_id)


CUP_ITEMS = frozenset(
    {
        "cup_first_drip",
        "brew_cup_flow",
        "phase_cup_first_drip",
        "phase_scale_flow",
        "phase_scale_flow_peak",
        "phase_cup_end",
    }
)


async def test_a_board_that_logged_zeros_with_the_flag_set_has_no_scale_anywhere(
    archive: Archive,
) -> None:
    shot = await _stored_variant(archive, "zeroed, flag kept", "000301")
    [facts] = await load_shots(archive.db, [shot], samples=True)
    row = await ShotsRepository(archive.db).get(shot)

    assert facts.shot.scale_connected is True  # the firmware's word, set
    assert not facts.has_scale
    assert row is not None and row.has_scale is False
    assert not {line.key for line in shot_lines(facts, CUP_ITEMS)}
    served = shot_fields_of(facts)
    assert not {f.key for f in served.shot} & CUP_ITEMS
    assert not {f.key for p in served.phases for f in p.fields} & CUP_ITEMS


async def test_a_scale_shot_with_the_flag_cleared_has_every_cup_item(archive: Archive) -> None:
    shot = await _stored_variant(archive, "flag cleared", "000302")
    [facts] = await load_shots(archive.db, [shot], samples=True)
    row = await ShotsRepository(archive.db).get(shot)

    assert facts.shot.scale_connected is False
    assert facts.has_scale
    assert row is not None and row.has_scale is True
    keys = {line.key for line in shot_lines(facts, CUP_ITEMS)}
    assert keys == CUP_ITEMS


async def test_the_phase_holding_the_cup_first_drip_is_the_one_whose_samples_hold_it(
    archive: Archive,
) -> None:
    [facts] = await load_shots(archive.db, [archive.shot], samples=True)

    drips = list(shot_lines(facts, frozenset({"phase_cup_first_drip"})))

    assert len(drips) == 1
    assert drips[0].value == "24.0 s"
    assert facts.phases[drips[0].phase or 0]["name"] == "Extraction"


async def test_the_curve_column_reads_cup_flow_at_zero_where_the_log_is_below(
    archive: Archive,
) -> None:
    """Shot 204 logs -20 g/s on its first sample (the old tare glitch); the model reads 0.00."""
    [facts] = await load_shots(archive.db, [archive.shot], samples=True)
    assert facts.samples is not None and (facts.samples[0].vf or 0.0) < -10
    layout = {item.key: "base" for item in CATALOGUE}

    text = render_shot(facts, "full", layout, curve_points=40)  # type: ignore[arg-type]

    header = next(line for line in text.splitlines() if line.startswith("t (s),"))
    column = header.split(",").index("cup flow (g/s)")
    rows = [line.split(",") for line in text.splitlines()[text.splitlines().index(header) + 1 :]]
    values = [float(row[column]) for row in rows if len(row) > column and row[column]]
    assert values and min(values) >= 0.0
    assert rows[0][column] == "0.00"


def _columns(text: str) -> list[str]:
    return next(line for line in text.splitlines() if line.startswith("t (s),")).split(",")


async def test_a_board_that_logged_zeros_with_the_flag_set_has_no_weight_or_cup_flow_column(
    archive: Archive,
) -> None:
    shot = await _stored_variant(archive, "zeroed, flag kept", "000303")
    [facts] = await load_shots(archive.db, [shot], samples=True)
    layout = {item.key: "base" for item in CATALOGUE}

    columns = _columns(render_shot(facts, "full", layout, curve_points=40))  # type: ignore[arg-type]

    assert "weight (g)" not in columns and "cup flow (g/s)" not in columns


async def test_a_scale_shot_with_the_flag_cleared_has_its_weight_and_cup_flow_columns(
    archive: Archive,
) -> None:
    shot = await _stored_variant(archive, "flag cleared", "000304")
    [facts] = await load_shots(archive.db, [shot], samples=True)
    layout = {item.key: "base" for item in CATALOGUE}

    columns = _columns(render_shot(facts, "full", layout, curve_points=40))  # type: ignore[arg-type]

    assert "weight (g)" in columns and "cup flow (g/s)" in columns
