"""Shots derived by an older version are re-derived once at boot, from their bytes.

A shot's phases, diagnostics and execution score are computed once at ingest, so
a better diagnostic reaches only the shots synced after it. The derivation
version says which definition wrote a shot's columns; the boot step brings the
older ones along. What is pinned here: the four derived columns and the version
change and nothing else, the result is what a fresh derive of the same bytes
gives, a second run does nothing, and a shot that cannot be re-derived is left
as it is and not tried again.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.typing import EventDict, WrappedLogger

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw
from gaggiclanker.domain.slog import Slog, parse_slog
from gaggiclanker.settings import EnvSettings
from gaggiclanker.sync import derive
from gaggiclanker.sync.derive import DERIVATION_VERSION, derive_shot, rederive_shots
from tests.conftest import running_app
from tests.domain.helpers import SLOG_FIXTURES, load_export
from tests.sets.conftest import make_profile_version

DERIVED = ("phases_json", "diagnostics_json", "execution_score", "execution_reason")
STALE_REASON = "derived by an older version"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "gaggiclanker.db")
    await database.connect()
    await run_migrations(database)
    try:
        yield database
    finally:
        await database.close()


def _slogs() -> dict[str, tuple[Slog, bytes, str]]:
    """Three real `.slog` files and an imported export: name to (slog, bytes, source)."""
    shots: dict[str, tuple[Slog, bytes, str]] = {}
    for index, path in enumerate(sorted(SLOG_FIXTURES.glob("*.slog"))):
        raw = path.read_bytes()
        shots[f"00030{index}"] = (parse_slog(raw, "x"), raw, "device")
    export = shot_export_to_slog(ShotExport.model_validate(load_export("shot-129.json")))
    shots["000129"] = (export, slog_to_raw(export), "import")
    return shots


async def _store_stale(
    db: Database,
    device_id: str,
    slog: Slog,
    raw: bytes,
    source: str,
    *,
    has_pressure: bool | None = None,
) -> int:
    """A shot as an older derivation left it: the right bytes, wrong derived columns."""
    derived = derive_shot(slog, raw, device_id=device_id, source=source, has_pressure=has_pressure)
    shot = derived.shot
    shot.derivation_version = 0
    shot.phases_json = "[]"
    shot.execution_score = 1.0
    shot.execution_reason = STALE_REASON
    return await ShotsRepository(db).insert(shot, derived.samples)


async def _row(db: Database, shot_id: int) -> dict[str, Any]:
    row = await db.fetch_one("SELECT * FROM shots WHERE id = ?", (shot_id,))
    assert row is not None
    return dict(row)


async def _everything_else(db: Database) -> dict[str, list[tuple[Any, ...]]]:
    """Every table's rows, with the shots table minus the columns a re-derive owns."""
    tables = [
        row["name"]
        for row in await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    snapshot: dict[str, list[tuple[Any, ...]]] = {}
    for table in sorted(tables):
        columns = [row["name"] for row in await db.fetch_all(f"PRAGMA table_info({table})")]
        if table == "shots":
            columns = [c for c in columns if c not in (*DERIVED, "derivation_version")]
        rows = await db.fetch_all(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1, 2"  # noqa: S608 - names from the schema
        )
        snapshot[table] = [tuple(row) for row in rows]
    return snapshot


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The events the derive module logs, caught by a logger of the test's own.

    The app's logging set-up caches a module's logger and filters by its level,
    both of which a capture installed later cannot get past.
    """
    events: list[dict[str, Any]] = []

    def catch(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
        events.append(dict(event))
        raise structlog.DropEvent

    monkeypatch.setattr(
        derive,
        "log",
        structlog.wrap_logger(
            structlog.PrintLogger(),
            processors=[catch],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        ),
    )
    return events


async def test_a_new_derive_carries_the_current_version(db: Database) -> None:
    slog, raw, source = _slogs()["000300"]

    derived = derive_shot(slog, raw, device_id="000300", source=source)
    shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)

    assert derived.shot.derivation_version == DERIVATION_VERSION
    assert (await _row(db, shot_id))["derivation_version"] == DERIVATION_VERSION


async def test_only_the_derived_columns_and_the_version_change(db: Database) -> None:
    shots = ShotsRepository(db)
    ids = {
        device_id: await _store_stale(db, device_id, slog, raw, source)
        for device_id, (slog, raw, source) in _slogs().items()
    }
    # What is ours and must survive: a judgement, a device note, Set membership.
    target = ids["000300"]
    await JudgementsRepository(db).upsert(
        target, JudgementWrite(rating=4, balance="sour", notes="mine", decision="keep")
    )
    await db.execute(
        "INSERT INTO device_shot_notes (shot_id, raw_json, notes) VALUES (?, ?, ?)",
        (target, '{"notes":"from the machine"}', "from the machine"),
    )
    bean = await BeansRepository(db).create(BeanWrite(name="Ethiopia Guji"))
    sets = SetsRepository(db)
    row = await sets.create(
        SetWrite(name="Guji", bean_id=bean.id),
        SetVersionWrite(
            profile_version_id=await make_profile_version(db, "p"), dose_g=18.0, target_yield_g=36.0
        ),
    )
    assert row.current_version_id is not None
    assert await sets.assign_shot(target, row.current_version_id)
    before = await _everything_else(db)
    assert any(before["shot_samples"]) and any(before["shot_judgements"])
    assert any(before["device_shot_notes"])

    assert await rederive_shots(shots) == (4, 0)

    assert await _everything_else(db) == before
    for device_id, shot_id in ids.items():
        stored = await _row(db, shot_id)
        assert stored["derivation_version"] == DERIVATION_VERSION, device_id
        assert stored["execution_reason"] != STALE_REASON
        assert stored["phases_json"] != "[]"


async def test_the_result_is_what_a_fresh_derive_of_the_same_bytes_gives(db: Database) -> None:
    ids = {}
    for device_id, (slog, raw, source) in _slogs().items():
        ids[device_id] = await _store_stale(db, device_id, slog, raw, source)

    await rederive_shots(ShotsRepository(db))

    for device_id, (slog, raw, source) in _slogs().items():
        fresh = derive_shot(slog, raw, device_id=device_id, source=source).shot
        stored = await _row(db, ids[device_id])
        for column in DERIVED:
            assert stored[column] == getattr(fresh, column), (device_id, column)
        # The import derived from the export's own reading; the boot step reads
        # the stored bytes. Both routes reach the same diagnostics.
        from_bytes = derive_shot(parse_slog(raw, device_id), raw, device_id=device_id).shot
        assert from_bytes.diagnostics_json == fresh.diagnostics_json, device_id


async def test_a_second_run_finds_nothing(db: Database, logs: list[dict[str, Any]]) -> None:
    for device_id, (slog, raw, source) in _slogs().items():
        await _store_stale(db, device_id, slog, raw, source)
    shots = ShotsRepository(db)
    assert await rederive_shots(shots) == (4, 0)
    after_first = await _everything_else(db)
    versions = [
        (r["id"], r["derivation_version"], r["diagnostics_json"])
        for r in await db.fetch_all("SELECT * FROM shots ORDER BY id")
    ]

    logs.clear()
    assert await rederive_shots(shots) == (0, 0)

    assert await _everything_else(db) == after_first
    assert versions == [
        (r["id"], r["derivation_version"], r["diagnostics_json"])
        for r in await db.fetch_all("SELECT * FROM shots ORDER BY id")
    ]
    assert next(e for e in logs if e["event"] == "shots_rederived")["count"] == 0


async def test_a_shot_whose_bytes_do_not_parse_is_left_logged_and_not_retried(
    db: Database, monkeypatch: pytest.MonkeyPatch, logs: list[dict[str, Any]]
) -> None:
    shots = ShotsRepository(db)
    slog, raw, source = _slogs()["000300"]
    good = await _store_stale(db, "000300", slog, raw, source)
    bad = await shots.insert(
        ShotInsert(
            device_id="000999",
            raw_slog=b"not a slog",
            phases_json="[]",
            diagnostics_json=json.dumps({"has_pressure": True}),
            execution_score=2.0,
            execution_reason=STALE_REASON,
        )
    )
    before = await _row(db, bad)

    assert await rederive_shots(shots) == (1, 1)

    failures = [e for e in logs if e["event"] == "shot_rederive_failed"]
    assert [e["shot_id"] for e in failures] == [bad]
    assert failures[0]["error"]
    summary = next(e for e in logs if e["event"] == "shots_rederived")
    assert (summary["count"], summary["failed"]) == (1, 1)
    assert isinstance(summary["duration_ms"], int)
    after = await _row(db, bad)
    assert {k: v for k, v in after.items() if k != "derivation_version"} == {
        k: v for k, v in before.items() if k != "derivation_version"
    }
    assert after["derivation_version"] == -DERIVATION_VERSION
    assert (await _row(db, good))["derivation_version"] == DERIVATION_VERSION

    # Not tried again by the same version, tried again when the version moves.
    assert await rederive_shots(shots) == (0, 0)
    monkeypatch.setattr(derive, "DERIVATION_VERSION", DERIVATION_VERSION + 1)
    assert await rederive_shots(shots) == (1, 1)


async def test_a_shot_whose_diagnostics_fail_keeps_what_it_has_and_is_not_retried(
    db: Database, monkeypatch: pytest.MonkeyPatch, logs: list[dict[str, Any]]
) -> None:
    # The bytes parse, so this is not the unparseable case: the diagnostics pass
    # raises, and `derive_shot` answers with an error and no diagnostics. Writing
    # that would replace a shot's blobs with nothing and call it current.
    slog, raw, source = _slogs()["000300"]
    shots = ShotsRepository(db)
    shot_id = await _store_stale(db, "000300", slog, raw, source)
    before = await _row(db, shot_id)

    def refuse(*_: Any, **__: Any) -> None:
        raise ValueError("a diagnostics bug")

    monkeypatch.setattr(derive, "transform_shot", refuse)

    assert await rederive_shots(shots) == (0, 1)

    after = await _row(db, shot_id)
    for column in DERIVED:
        assert after[column] == before[column], column
    assert after["derivation_version"] == -DERIVATION_VERSION
    assert [e["shot_id"] for e in logs if e["event"] == "shot_rederive_failed"] == [shot_id]
    logs.clear()
    assert await rederive_shots(shots) == (0, 0)
    assert not [e for e in logs if e["event"] == "shot_rederive_failed"]


async def test_a_quarantined_shot_is_not_touched(db: Database) -> None:
    shots = ShotsRepository(db)
    quarantined = await shots.insert(
        ShotInsert(
            device_id="000998",
            raw_slog=b"junk",
            quarantined=True,
            quarantine_reason="did not parse",
        )
    )

    assert await rederive_shots(shots) == (0, 0)
    assert (await _row(db, quarantined))["derivation_version"] == 0


async def test_a_shot_derived_without_pressure_stays_without_it(db: Database) -> None:
    # The gate is what the shot was derived with, read back from its stored
    # diagnostics; the trace of this shot has pressure, so deciding afresh would
    # switch the pressure diagnostics on.
    slog, raw, source = _slogs()["000300"]
    shot_id = await _store_stale(db, "000300", slog, raw, source, has_pressure=False)

    await rederive_shots(ShotsRepository(db))

    blob = json.loads((await _row(db, shot_id))["diagnostics_json"])
    assert blob["has_pressure"] is False
    assert blob["diagnostics"]["resistance"] is None
    assert blob["diagnostics"]["has_pressure"] is False


async def test_a_shot_with_no_stored_diagnostics_is_derived_from_its_trace(db: Database) -> None:
    slog, raw, source = _slogs()["000300"]
    shots = ShotsRepository(db)
    derived = derive_shot(slog, raw, device_id="000300", source=source)
    derived.shot.diagnostics_json = None
    derived.shot.phases_json = None
    shot_id = await shots.insert(derived.shot, derived.samples)
    await db.execute("UPDATE shots SET derivation_version = 0 WHERE id = ?", (shot_id,))

    assert await rederive_shots(shots) == (1, 0)

    blob = json.loads((await _row(db, shot_id))["diagnostics_json"])
    assert blob["diagnostics"]["resistance"]["source"] == "machine"


async def test_the_boot_step_brings_the_archive_along(env: EnvSettings) -> None:
    database = Database(env.database_path)
    await database.connect()
    try:
        await run_migrations(database)
        ids = [
            await _store_stale(database, device_id, slog, raw, source)
            for device_id, (slog, raw, source) in _slogs().items()
        ]
    finally:
        await database.close()

    async with running_app(env) as (app, _client):
        for shot_id in ids:
            stored = await _row(app.state.db, shot_id)
            assert stored["derivation_version"] == DERIVATION_VERSION
            assert stored["execution_reason"] != STALE_REASON


async def test_a_shot_derived_at_version_one_gets_the_firmware_block(db: Database) -> None:
    slog, raw, source = _slogs()["000300"]
    derived = derive_shot(slog, raw, device_id="000300", source=source)
    shot = derived.shot
    stored = json.loads(shot.diagnostics_json or "{}")
    assert stored["firmware"]["pr"] is not None
    # What version 1 left: the same diagnostics without the block.
    del stored["firmware"]
    shot.diagnostics_json = json.dumps(stored)
    shot.derivation_version = 1
    shot_id = await ShotsRepository(db).insert(shot, derived.samples)

    assert DERIVATION_VERSION >= 2
    assert await rederive_shots(ShotsRepository(db)) == (1, 0)

    after = await _row(db, shot_id)
    assert after["derivation_version"] == DERIVATION_VERSION
    block = json.loads(after["diagnostics_json"])["firmware"]
    assert block["pr"]["avg"] > 0 and block["lr"]["avg"] > 0
    assert [p["phase_number"] for p in block["phases"]] == [0, 1, 2]
