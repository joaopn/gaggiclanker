#!/usr/bin/env python
"""Reproduce: a logged phase is typed from its curve although its profile says what it is.

    uv run python scripts/repro_phase_type_from_profile.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A shot whose first phase ended before the machine logged a sample opens in the profile's
second phase. The diagnostics typed that phase by its name (which says nothing) and then by
its curve, whose rule reads "the first logged phase, at low pressure and rising, is a
pre-infusion": so a ramp the profile marks as a brew phase was called a pre-infusion, and the
review input printed "type preinfusion" beside a pre-infusion time of 0.0 s. The profile's own
``phase`` field was never consulted, and the curve rule counted the logged order, not the
profile's phase number.

The check derives the real shot (fixture 225) with its profile and requires the ramp to be
typed ``brew``, the profile's word, while the decline keeps its type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLOG = ROOT / "tests" / "fixtures" / "slog_edge" / "shot_225_fill_ended_at_start.slog"
PROFILE = ROOT / "tests" / "fixtures" / "slog_edge" / "alma_lever_16_32.json"


def main() -> int:
    from gaggiclanker.domain.slog import parse_slog
    from gaggiclanker.sync.derive import derive_shot

    raw = SLOG.read_bytes()
    profile = json.loads(PROFILE.read_text())
    derived = derive_shot(
        parse_slog(raw, "000225"), raw, device_id="000225", source="import", profile=profile
    )
    types = {
        phase["name"]: phase.get("diagnostics", {}).get("phase_type")
        for phase in json.loads(derived.shot.phases_json or "[]")
    }
    print("phase types:", types)
    failures = []
    if types.get("Ramp") != "brew":
        failures.append(f"the Ramp is typed {types.get('Ramp')!r}, the profile says 'brew'")
    if types.get("Decline") != "decline":
        failures.append(f"the Decline is typed {types.get('Decline')!r}, expected 'decline'")
    for problem in failures:
        print("BUG:", problem)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
