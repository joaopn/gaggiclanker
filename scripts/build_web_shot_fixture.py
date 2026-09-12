#!/usr/bin/env python
"""Rebuild the front end's shot fixture from the real exported shot.

`web/src/test/fixtures/shot-129.json` is what the front-end tests assert
against: a shot detail row and its samples, in exactly the shape
`GET /api/shots/{id}` and `/samples` answer. It is generated rather than
hand-written on purpose — the phases, the diagnostics, every band label and the
execution score in it come out of `gaggiclanker/domain/diagnostics.py`, so a
component test that renders it is testing the component against the real
pipeline. A hand-written blob would only prove that the test author and the
component agree with each other.

Re-run it whenever the diagnostics, the scoring or the row models change; the
diff in the fixture is the signal that the front end has something new to show.

    uv run python scripts/build_web_shot_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.imports.service import ImportFile, ImportService

REPO = Path(__file__).resolve().parent.parent
EXPORT = REPO / "tests" / "fixtures" / "exports" / "shot-129.json"
TARGET = REPO / "web" / "src" / "test" / "fixtures" / "shot-129.json"

#: "When did the archive touch this row" is the clock, not the shot, and a
#: fixture whose clock moves produces a diff on every regeneration — which
#: buries the diff that means something. Pinned so that re-running this script
#: is a no-op unless the pipeline's output actually changed.
PINNED_CLOCK = "2026-03-04T09:00:00.000Z"
CLOCK_FIELDS = ("synced_at", "updated_at", "fetched_at")


async def build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "fixture.db")
        await db.connect()
        try:
            await run_migrations(db)
            summary = await ImportService(db).import_files(
                [ImportFile(filename=EXPORT.name, data=EXPORT.read_bytes())]
            )
            shot_id = summary.items[0].shot_id
            if shot_id is None:  # pragma: no cover - the export is committed and valid
                raise SystemExit(f"the export did not import: {summary.items[0].message}")

            shots = ShotsRepository(db)
            row = await shots.get(shot_id)
            notes = await notes_for(db, shot_id)
            samples = await shots.samples(shot_id)
            if row is None:  # pragma: no cover - it was just inserted
                raise SystemExit("the shot vanished between write and read")

            for field in CLOCK_FIELDS:
                if notes is not None and field in notes:
                    notes[field] = PINNED_CLOCK

            shot_row = json.loads(row.model_dump_json())
            for field in CLOCK_FIELDS:
                if field in shot_row:
                    shot_row[field] = PINNED_CLOCK

            document = {
                "detail": {"shot": shot_row, "notes": notes},
                "samples": {
                    "shot_id": shot_id,
                    "count": len(samples),
                    "total": len(samples),
                    "sample_interval_ms": row.sample_interval_ms,
                    "downsampled": False,
                    "samples": [json.loads(sample.model_dump_json()) for sample in samples],
                },
            }
        finally:
            await db.close()

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO)} ({len(samples)} samples)")


async def notes_for(db: Database, shot_id: int) -> dict[str, object] | None:
    row = await NotesRepository(db).get(shot_id)
    return None if row is None else json.loads(row.model_dump_json())


if __name__ == "__main__":
    asyncio.run(build())
