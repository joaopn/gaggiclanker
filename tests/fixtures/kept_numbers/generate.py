"""Write ``golden.json``: the numbers the phase-metrics change must not move.

    PYTHONPATH=<an older tree> python tests/fixtures/kept_numbers/generate.py <out.json>

The golden was generated from the tree *before* the per-phase metrics and the
retirement of the score and the bands, by running this very script against it.
Resistance (its level, its slope and where it came from), the adherence of the
pressure and the flow to the profile, and the firmware analyzer's values are
the computations that were kept; ``tests/domain/test_kept_numbers.py`` derives
the same shots on the current tree and requires every number to be identical.

The shots are the real fixtures (derived without a profile, with each of the
two constructed profiles that exist for it, and as a machine without a
pressure sensor), the exported shot, and the constructed lever shot.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent
ROOT = FIXTURES.parent.parent

#: A phase's own kept numbers.
PHASE_KEYS = (
    "resistance_avg",
    "resistance_slope",
    "resistance_source",
    "pressure_rmse_bar",
    "flow_rmse_ml_s",
)
RESISTANCE_KEYS = ("source", "avg", "slope")


def _lever() -> Any:
    spec = importlib.util.spec_from_file_location("lever_shot", ROOT / "tests" / "lever_shot.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lever_shot"] = module
    spec.loader.exec_module(module)
    return module


def kept_numbers() -> dict[str, Any]:
    from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw
    from gaggiclanker.domain.slog import parse_slog
    from gaggiclanker.sync.derive import derive_shot

    lever = _lever()
    constructed = FIXTURES / "constructed_profiles"
    cases: list[tuple[str, Any, bytes, dict[str, Any | None]]] = []

    def profiles(shot: str) -> dict[str, Any | None]:
        found: dict[str, Any | None] = {"none": None}
        for variant in ("pressure-first", "flow-first"):
            path = constructed / f"{shot}_{variant}.json"
            if path.exists():
                found[variant] = json.loads(path.read_text())
        return found

    for path in sorted((FIXTURES / "slog").glob("*.slog")):
        raw = path.read_bytes()
        cases.append(
            (path.stem, parse_slog(raw), raw, profiles("_".join(path.stem.split("_")[:2])))
        )
    for path in sorted((FIXTURES / "exports").glob("shot-*.json")):
        slog = shot_export_to_slog(ShotExport.model_validate(json.loads(path.read_text())))
        cases.append(
            (path.stem, slog, slog_to_raw(slog), profiles("shot_129" if "129" in path.stem else ""))
        )
    shot = lever.lever_shot()
    cases.append(("lever", shot, slog_to_raw(shot), {"none": None, "lever": lever.LEVER_PROFILE}))

    out: dict[str, Any] = {}
    for label, slog, raw, variants in cases:
        for variant, profile in variants.items():
            for has_pressure in (None, False):
                derived = derive_shot(
                    slog, raw, device_id="000001", has_pressure=has_pressure, profile=profile
                )
                blob = json.loads(derived.shot.diagnostics_json or "null")
                phases = json.loads(derived.shot.phases_json or "[]")
                record: dict[str, Any] = {}
                if blob is not None:
                    diagnostics = blob.get("diagnostics") or {}
                    resistance = diagnostics.get("resistance")
                    record["resistance"] = (
                        {key: resistance.get(key) for key in RESISTANCE_KEYS}
                        if resistance
                        else None
                    )
                    compliance = diagnostics.get("profile_compliance")
                    record["compliance"] = (
                        {k: v for k, v in compliance.items() if k != "annotations"}
                        if compliance
                        else None
                    )
                    record["firmware"] = blob.get("firmware")
                record["phases"] = [
                    {"phase_number": p["phase_number"]}
                    | {key: (p.get("diagnostics") or {}).get(key) for key in PHASE_KEYS}
                    for p in phases
                ]
                out[f"{label}|{variant}|has_pressure={has_pressure}"] = record
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "golden.json"
    target.write_text(json.dumps(kept_numbers(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {target}")
