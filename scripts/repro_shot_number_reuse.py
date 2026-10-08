#!/usr/bin/env python
"""Reproduce: after a machine reset, new shots reuse archived numbers and are lost.

    uv run python scripts/repro_shot_number_reuse.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

The archive identified a shot only by the number the machine gave it. That
number comes from a counter the firmware keeps in NVS, so a machine whose
settings were erased (a reflash, a factory reset, a replacement board) numbers
its shots from the start again while its clock keeps going. Its new shots then
carry numbers the archive already holds for different shots, and on every pull:

* the diff saw the number in the archive and never fetched the new shot;
* the index row of the new shot was copied onto the archived one (rating,
  volume, temperature, pressure, flow, flags);
* the notes card of the new shot was attached to the archived shot's judgement.

This script archives three shots, resets the fake machine, lets it brew two
shots that reuse numbers 1 and 2 at later times (with different index values
and different notes), pulls, and looks for both halves of the damage: a new
shot that never became a row, and an archived shot whose index fields or
judgement moved.

The fix identifies a shot by its number and its start time together.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

from gaggiclanker.device.fake import FakeDevice, default_notes, synthetic_slog_bytes
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

FIRST_LIFE_EPOCH = 1_760_000_000
SECOND_LIFE_EPOCH = 1_770_000_000

_SHOT_COLUMNS = """
    id, device_id, start_epoch, index_rating, index_volume_g, index_avg_temp_c,
    index_max_pressure_bar, index_avg_flow_ml_s, index_flags
"""


def add_shot(device: FakeDevice, number: int, epoch: int, **notes: Any) -> None:
    """A shot as the firmware writes it: the index row's time is the header's."""
    document = default_notes(number) if notes.pop("with_notes", False) else None
    if document is not None:
        document.update(notes)
    device.add_shot(
        number,
        synthetic_slog_bytes(shot_id=number, start_epoch=epoch),
        timestamp=epoch,
        notes=document,
    )


async def store_machine(env: EnvSettings, address: str) -> None:
    from gaggiclanker.db.connection import Database
    from gaggiclanker.db.schema import create_schema
    from gaggiclanker.db.settings_repo import SettingsRepository
    from gaggiclanker.settings_service import SettingsService

    db = Database(env.database_path)
    await db.connect()
    try:
        await create_schema(db)
        await SettingsService(SettingsRepository(db)).apply(
            {"gaggimateHost": address, "gaggimateTimeoutSeconds": 5}
        )
    finally:
        await db.close()


async def snapshot(db: Any) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """The archive's shots and, per shot, its judgement and the machine's notes card."""
    shots = await db.fetch_all(f"SELECT {_SHOT_COLUMNS} FROM shots ORDER BY id")  # noqa: S608
    judgements = await db.fetch_all(
        "SELECT shot_id, rating, notes, grind_setting, dose_in_g, dose_out_g FROM shot_judgements"
    )
    cards = await db.fetch_all("SELECT shot_id, raw_json FROM device_shot_notes")
    by_shot = {int(row["shot_id"]): dict(row) for row in judgements}
    for row in cards:
        by_shot.setdefault(int(row["shot_id"]), {})["device_notes"] = row["raw_json"]
    return {int(row["id"]): dict(row) for row in shots}, by_shot


async def main() -> int:
    device = FakeDevice()
    await device.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = EnvSettings(DATA_DIR=tmp, LOG_LEVEL="error")
            await store_machine(env, device.address)
            app = create_app(env, web_dist=Path(tmp) / "no-web")
            return await run(app, device)
    finally:
        await device.stop()


async def run(app: Any, device: FakeDevice) -> int:
    for number in (1, 2, 3):
        add_shot(device, number, FIRST_LIFE_EPOCH + number * 600, with_notes=number != 3)
        device.shots[number].entry.rating = 5
        device.shots[number].entry.volume_g = 36.0 + number

    async with app.router.lifespan_context(app):
        state = app.state
        assert await state.connection.client.wait_connected(5.0), "the fake did not connect"
        engine = state.connection.engine
        await engine.sync_shots(trigger="repro")
        before_shots, before_judgements = await snapshot(state.db)
        assert len(before_shots) == 3, before_shots

        # The machine's settings are erased: the counter starts again, the clock
        # has moved on, and the new shots are different shots.
        device.reset_history()
        for number in (1, 2):
            add_shot(
                device,
                number,
                SECOND_LIFE_EPOCH + number * 600,
                with_notes=True,
                rating=1,
                notes="brewed after the reset",
                grindSetting="9.9",
            )
            device.shots[number].entry.rating = 2
            device.shots[number].entry.volume_g = 44.0 + number
            device.shots[number].entry.max_pressure_bar = 6.5
        await engine.sync_shots(trigger="repro")
        after_shots, after_judgements = await snapshot(state.db)

    problems: list[str] = []
    new_epochs = {row["start_epoch"] for row in after_shots.values()} - {
        row["start_epoch"] for row in before_shots.values()
    }
    if len(new_epochs) != 2:
        problems.append(
            f"the two shots brewed after the reset were not archived as their own rows "
            f"({len(after_shots)} rows, expected 5)"
        )
    for shot_id, was in before_shots.items():
        now = after_shots.get(shot_id)
        if now != was:
            changed = {k: (was[k], now[k]) for k in was if now and now[k] != was[k]}
            problems.append(f"archived shot {shot_id}'s index fields changed: {changed}")
        if after_judgements.get(shot_id) != before_judgements.get(shot_id):
            problems.append(
                f"archived shot {shot_id}'s judgement or notes card changed: "
                f"{before_judgements.get(shot_id)} -> {after_judgements.get(shot_id)}"
            )

    for problem in problems:
        print(f"BUG: {problem}")
    if problems:
        return 1
    print("fixed: the reused numbers are new shots and the archived shots are untouched")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
