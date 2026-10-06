#!/usr/bin/env python
"""Reproduce: a review reads puck flow from a shot whose board has no pressure sensor.

    uv run python scripts/repro_review_puck_flow_without_pressure.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A board with no pressure sensor logs puck flow and pressure as zeros, and a shot derived before
the gate on them (or written by hand) can still hold numbers built on them in its stored
summary. The review's input read that summary directly: telemetry style detection called a fast
shot "turbo" from the average puck flow, and the rule tokens said `avg_flow:high` and
`first_drip:fast`, so the model was handed a style and rules chosen from a flow the machine never
measured.

The check stores a shot flagged `has_pressure: false` whose summary carries a fast puck flow,
builds the review's input as the service does, and requires that neither the style nor any rule
signal came from it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: What a shot derived before the gate holds: puck flow and pressure numbers from zeros.
BLOB = {
    "summary": {
        "flow": {"avg_flow_ml_s": 4.2, "peak_flow_ml_s": 5.0, "time_to_first_drip_s": 1.2},
        "pressure": {"max_bar": 9.1, "min_bar": 0.0, "avg_bar": 6.0, "peak_time_s": 9.0},
        "temperature": {"avg_c": 93.0, "target_avg_c": 93.0},
    },
    "has_pressure": False,
}


async def main() -> int:
    from gaggiclanker.db.connection import Database
    from gaggiclanker.db.migrations import run_migrations
    from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
    from gaggiclanker.review.context import build_review_input

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "repro.db")
        await db.connect()
        try:
            await run_migrations(db)
            shot_id = await ShotsRepository(db).insert(
                ShotInsert(
                    device_id="000001",
                    raw_slog=b"x",
                    started_at="2026-03-04T08:00:00.000Z",
                    duration_ms=16_000,
                    sample_interval_ms=250,
                    diagnostics_json=json.dumps(BLOB),
                )
            )
            review = await build_review_input(db, shot_id)
        finally:
            await db.close()

    problems: list[str] = []
    if review.style == "turbo":
        problems.append(f"style detected as turbo ({'; '.join(review.style_evidence)})")
    leaked = [s for s in review.signals if s.startswith(("avg_flow:", "first_drip:"))]
    if leaked:
        problems.append(f"rule signals read puck flow: {leaked}")

    if problems:
        print("BUG PRESENT: the review read puck flow from a board with no pressure sensor")
        for problem in problems:
            print(" -", problem)
        return 1
    print("ok: style", review.style, "and signals", review.signals, "read no puck flow")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
