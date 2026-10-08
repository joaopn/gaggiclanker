"""Write ``golden.json``: the per-phase metrics every fixture shot is stored with.

    python tests/fixtures/phase_metrics/generate.py [out.json]

The golden was generated from the tree from before the per-phase metrics were
computed through the metric language, by this script, and
``tests/domain/test_phase_metrics_language.py`` derives the same shots now and
requires every stored number, and the order it is stored in, to be identical.
The one deliberate change since: the scale's flow is read at zero where the log has it
below zero (cup flow), so the scale-flow numbers of a shot with the old tare glitch moved.
The shots are the real fixtures with and without each constructed profile, as a
machine with no scale and as one with no pressure sensor, and the constructed
lever shot in the same variants.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def stored_metrics() -> dict[str, Any]:
    from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw
    from gaggiclanker.domain.slog import parse_slog
    from gaggiclanker.sync.derive import derive_shot
    from tests.lever_shot import LEVER_PROFILE, lever_shot, without_pressure, without_scale

    fixtures = ROOT / "tests" / "fixtures"

    def profiles(shot: str) -> dict[str, Any]:
        found: dict[str, Any] = {"none": None}
        for variant in ("pressure-first", "flow-first"):
            path = fixtures / "constructed_profiles" / f"{shot}_{variant}.json"
            if path.exists():
                found[variant] = json.loads(path.read_text())
        return found

    cases: list[tuple[str, Any, dict[str, Any]]] = []
    for path in sorted((fixtures / "slog").glob("*.slog")):
        slog = parse_slog(path.read_bytes())
        cases.append((path.stem, slog, profiles("_".join(path.stem.split("_")[:2]))))
        cases.append((path.stem + "-noscale", without_scale(slog), {"none": None}))
        cases.append((path.stem + "-nopressure", without_pressure(slog), {"none": None}))
    for path in sorted((fixtures / "exports").glob("shot-*.json")):
        exported = shot_export_to_slog(ShotExport.model_validate(json.loads(path.read_text())))
        cases.append((path.stem, exported, profiles("shot_129" if "129" in path.stem else "")))
    lever = lever_shot()
    both = {"none": None, "lever": LEVER_PROFILE}
    cases.append(("lever", lever, both))
    cases.append(("lever-noscale", without_scale(lever), both))
    cases.append(("lever-nopressure", without_pressure(lever), both))

    out: dict[str, Any] = {}
    for label, slog, variants in cases:
        raw = slog_to_raw(slog)
        for name, profile in variants.items():
            for has_pressure in (None, False):
                derived = derive_shot(
                    slog, raw, device_id="000001", has_pressure=has_pressure, profile=profile
                )
                phases = json.loads(derived.shot.phases_json or "[]")
                blob = json.loads(derived.shot.diagnostics_json or "null")
                out[f"{label}|{name}|{has_pressure}"] = {
                    "phases": [
                        {"phase_number": p["phase_number"], "metrics": p.get("metrics")}
                        for p in phases
                    ],
                    "shot": blob["metrics"] if blob else None,
                }
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "golden.json"
    target.write_text(json.dumps(stored_metrics(), indent=1) + "\n")
    print(f"wrote {target}")
