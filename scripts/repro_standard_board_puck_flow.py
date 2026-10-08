#!/usr/bin/env python
"""Reproduce: a machine with no pressure sensor shows puck flow and its numbers as zero.

    uv run python scripts/repro_standard_board_puck_flow.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A Standard board has no pressure sensor and no dimmed pump, so the firmware logs
`pf`, `fl`, `tf`, `pr` and `wp` as zero on every sample (see the repro for its flow
adherence: `repro_standard_board_flow_adherence.py`). Nothing about puck flow, pump
flow, the flow target or the water can be measured there, and a zero is never a
measurement. The per-phase puck flow was stored as 0.00 on such a shot, and the
catalogue, gated on the log's field mask (which says the *field* was written, as
zeros) rather than on the pressure gate, showed the average and peak flow, the total
volume, each phase's volume and flow, and the curve's flow and water columns as values.

The check builds a Standard shot from the constructed lever shot, which is a real
fixture rewritten (the pressure, flow and counter columns zeroed, as the firmware
writes them), derives it with `has_pressure=False`, and requires that none of those
numbers is stored, rendered in any tier or served by the fields route.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Catalogue items derived from the puck flow, the pump flow, the flow target or the pump counter.
FLOW_ITEMS = (
    "first_drip",
    "phase_first_drip",
    "brew_flow",
    "average_flow",
    "peak_flow",
    "total_volume",
    "water_pumped",
    "water_minus_weight",
    "flow_adherence",
    "phase_volume",
    "phase_flow",
    "phase_flow_peak",
    "phase_water",
    "phase_flow_error",
    "curve_puck_flow",
    "curve_target_flow",
    "curve_pump_flow",
    "curve_water_pumped",
)
#: The stored per-phase numbers that are puck flow.
STORED_FLOW_KEYS = ("puck_flow_mean_ml_s", "puck_flow_peak_ml_s", "first_drip_s")
CURVE_HEADERS = ("puck flow (ml/s)", "target flow (ml/s)", "pump flow (ml/s)", "water pumped (ml)")


async def main() -> int:
    from gaggiclanker.db.connection import Database
    from gaggiclanker.db.repos.shots import ShotsRepository
    from gaggiclanker.db.schema import create_schema
    from gaggiclanker.domain.exports import slog_to_raw
    from gaggiclanker.shotinfo import default_tiers, load_shots, render_shot, shot_lines
    from gaggiclanker.shotinfo.catalogue import CATALOGUE
    from gaggiclanker.shotinfo.fields import shot_fields_of
    from gaggiclanker.sync.derive import derive_shot
    from tests.lever_shot import lever_shot, without_pressure

    slog = without_pressure(lever_shot())
    assert not slog.has_pressure, "the shot must read as a Standard board"
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        db = Database(Path(scratch) / "repro.db")
        await db.connect()
        await create_schema(db)
        try:
            derived = derive_shot(
                slog, slog_to_raw(slog), device_id="000001", source="import", has_pressure=False
            )
            for phase in json.loads(derived.shot.phases_json or "[]"):
                for key in STORED_FLOW_KEYS:
                    if key in phase.get("metrics", {}):
                        failures.append(f"stored: phase {phase['phase_number']} {key}")
            shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
            [facts] = await load_shots(db, [shot_id], samples=True)

            tiers = default_tiers()
            everything = {item.key: "base" for item in CATALOGUE}
            for name, layout, tier in (
                ("base", tiers, "base"),
                ("extended", tiers, "extended"),
                ("full", tiers, "full"),
                ("every item", everything, "base"),
            ):
                text = render_shot(facts, tier, layout, curve_points=20)  # type: ignore[arg-type]
                for header in CURVE_HEADERS:
                    if header in text:
                        failures.append(f"{name}: curve column {header!r}")
                for line in shot_lines(facts, frozenset(FLOW_ITEMS)):
                    failures.append(f"{name}: {line.key} = {line.value!r}")
            document = shot_fields_of(facts)
            served = [f for f in document.shot if f.key in FLOW_ITEMS]
            for phase_fields in document.phases:
                served += [f for f in phase_fields.fields if f.key in FLOW_ITEMS]
            failures += [f"fields route: {f.key} = {f.value!r}" for f in served]
        finally:
            await db.close()

    for problem in sorted(set(failures)):
        print("BUG:", problem)
    print(f"{len(set(failures))} flow numbers shown for a shot that has no pressure sensor")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
