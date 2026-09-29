"""Regenerate ``golden.json``: the numbers GaggiMate firmware v1.9.0's own code gives.

    PYTHONPATH=. PATH=/workspace/.tools/node/bin:$PATH \
    uv run python tests/fixtures/firmware_values/generate.py external/gaggimate

``external/gaggimate`` is the read-only checkout of the firmware, detached at
the tag below. The samples handed to the analyzer are the ones the firmware's
web UI would parse from the same shot (``t`` in ms, ``cp``, ``pr``, ``wp``,
``phaseNumber``); ``harness.mjs`` runs the analyzer's own ``puckResistance.js``
and ``waterIntegration.js`` over them. Values are pinned at full precision.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from gaggiclanker.domain.slog import parse_slog
from tests.domain.helpers import SLOG_FIXTURES, slog_from_export

HERE = Path(__file__).resolve().parent
TAG = "v1.9.0"
ANALYZER = "web/src/pages/ShotAnalyzer/services/analyzer"


def shots() -> dict[str, list[dict[str, float | int]]]:
    slogs = {
        p.stem: parse_slog(p.read_bytes(), "000001") for p in sorted(SLOG_FIXTURES.glob("*.slog"))
    }
    slogs["shot-129"] = slog_from_export("shot-129.json")
    slogs["shot-v7-synthetic"] = slog_from_export("shot-v7-synthetic.json")
    out: dict[str, list[dict[str, float | int]]] = {}
    for name, slog in slogs.items():
        rows = []
        for s in slog.samples:
            row: dict[str, float | int] = {"t": s.t or 0, "phaseNumber": s.phase or 0}
            for key in ("cp", "pr", "wp"):
                value = getattr(s, key)
                if value is not None:
                    row[key] = value
            rows.append(row)
        out[name] = rows
    return out


def main() -> None:
    firmware = Path(sys.argv[1]).resolve()
    with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
        json.dump(shots(), handle)
        handle.flush()
        result = subprocess.run(  # noqa: S603
            ["node", str(HERE / "harness.mjs"), str(firmware / ANALYZER), handle.name],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    golden = {
        "generated": {
            "firmware_tag": TAG,
            "command": (
                "PYTHONPATH=. uv run python "
                "tests/fixtures/firmware_values/generate.py external/gaggimate"
            ),
            "analyzer": ["puckResistance.js", "waterIntegration.js"],
            "note": "full precision, as the analyzer computes it; null = the empty stats; "
            "water_pumped_ml is the analyzer's, and gaggiclanker leaves an all-zero counter "
            "absent where the analyzer shows 0",
        },
        "shots": json.loads(result.stdout),
    }
    (HERE / "golden.json").write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
