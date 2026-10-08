"""A phase that ended before the machine logged a sample of it, in the shot's checks.

The real shot: a profile's Fill (exit on 2.8 bar) met a group still at 4.8 bar, so the log
opens in the Ramp. With a signature in force, "the Fill begins" fails and says why;
a measure windowed on the Fill is not measured for that reason, at the Fill's time and not at
the end of the shot. Without one the universal warning is there all the same.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.shotinfo.fields import shot_fields
from gaggiclanker.signatures.service import SignatureService
from tests.domain.helpers import fill_ended_shot
from tests.signatures.helpers import add_shot
from tests.signatures.test_carry import _version

FILL_PEAK = {"channel": "pressure", "op": "max", "window": {"phase": "Fill"}}
RAMP_PEAK = {"channel": "pressure", "op": "max", "window": {"phase": "Ramp"}}


async def _shot(db: Database, items: list[ExpectationInput] | None = None) -> int:
    slog, _, profile = fill_ended_shot()
    version = await _version(db, profile)
    shot = await add_shot(
        db, set_version_id=None, profile_version_id=version, slog=slog, profile=profile
    )
    if items:
        await SignatureService(db).propose(version, items, reason="what the lever is for")
    return shot


async def _checks(db: Database, shot: int) -> list[Any]:
    fields = await shot_fields(db, shot)
    assert fields is not None
    return fields.checks.items


async def test_without_a_signature_the_warning_is_the_only_check_and_the_badge_names_the_fill(
    db: Database,
) -> None:
    shot = await _shot(db)

    [check] = await _checks(db, shot)
    row = next(r for r in (await ShotsRepository(db).list_shots(limit=5)).items if r.id == shot)

    assert (check.kind, check.status, check.phase, check.fault, check.at_s) == (
        "warning",
        "warning",
        "Fill",
        "skipped",
        0.0,
    )
    assert "pressure was already 4.8 bar" in check.detail
    assert row.checks.badge == "Fill: skipped"


async def test_a_reached_expectation_on_the_fill_fails_and_supersedes_the_warning(
    db: Database,
) -> None:
    shot = await _shot(db, [ExpectationInput(tier="critical", kind="reached", phase="Fill")])

    [check] = await _checks(db, shot)

    assert (check.kind, check.status, check.phase, check.fault, check.at_s) == (
        "reached",
        "failed",
        "Fill",
        "skipped",
        0.0,
    )
    assert check.detail == (
        "the Fill begins; it ended on its pressure target before the first sample."
    )


async def test_a_reached_expectation_on_a_phase_that_was_sampled_still_holds(
    db: Database,
) -> None:
    shot = await _shot(db, [ExpectationInput(tier="critical", kind="reached", phase="Ramp")])

    held = [c for c in await _checks(db, shot) if c.kind == "reached"]

    assert [(c.phase, c.status) for c in held] == [("Ramp", "held")]


async def test_a_measure_on_the_fill_is_not_measured_for_that_reason_at_the_fill_s_time(
    db: Database,
) -> None:
    shot = await _shot(
        db,
        [
            ExpectationInput(
                tier="important",
                kind="measure",
                expression=FILL_PEAK | {"compare": {"op": "<=", "value": 3.0}},
            ),
            ExpectationInput(
                tier="important",
                kind="measure",
                expression=RAMP_PEAK | {"compare": {"op": "<=", "value": 9.5}},
            ),
        ],
    )

    [fill] = [c for c in await _checks(db, shot) if c.phase == "Fill" and c.kind == "measure"]

    assert fill.status == "unmeasured"
    assert fill.absent == "the Fill ended on its pressure target before the first sample"
    assert "did not reach" not in fill.detail
    assert fill.absent_reason == "ended_before_sampled"
    assert fill.detail.endswith(
        "ended before it was measured (the Fill ended on its pressure target before the first "
        "sample)."
    )
    assert fill.at_s == 0.0
    # Another absence keeps its own words and its own code.
    [other] = [c for c in await _checks(db, shot) if c.phase == "Ramp" and c.kind == "measure"]
    assert other.status == "held" and other.absent_reason is None
