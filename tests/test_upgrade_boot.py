"""Where the upgrade runs: the app's own start, and only there.

The ordinary start, ``import``, ``mcp`` and ``serve``'s early check are driven against a real
database that is one level behind. No step ships yet, so the target is injected: level 1 is
today's schema at another level, reached by a step that edits a column.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker import main as main_module
from gaggiclanker.db import schema
from gaggiclanker.db import upgrade as upgrade_module
from gaggiclanker.db.reset import RESET_MARKER, complete_reset
from gaggiclanker.db.upgrade import UpgradeRefused, upgrade_database_file
from gaggiclanker.db.upgrades import LATEST, STEPS, Step, frozen_schema_sql
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app

# One level past the latest the code ships, so the tests mean the same with any number of real
# steps: the database starts at LATEST, and the injected step carries it to NEXT.
NEXT = LATEST + 1
SQL_NEXT = schema.schema_sql().replace(
    f"PRAGMA user_version = {LATEST};", f"PRAGMA user_version = {NEXT};"
)
EXPECTED_NEXT = schema.describe_sql(SQL_NEXT)


def _mark_beans(conn: sqlite3.Connection) -> dict[str, int]:
    return {"beans": conn.execute("UPDATE beans SET notes = notes || ' (upgraded)'").rowcount}


def _fails(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute("UPDATE beans SET notes = 'half done'")
    raise RuntimeError("the fill went wrong")


def step(run: Callable[[sqlite3.Connection], dict[str, int]] = _mark_beans) -> Step:
    return Step(level=NEXT, expects=schema.schema_fingerprint(), foreign_keys_off=False, run=run)


def steps_to_next(
    run: Callable[[sqlite3.Connection], dict[str, int]] = _mark_beans,
) -> list[Step]:
    """The steps the code ships, then the injected one that makes NEXT the latest level."""
    return [*STEPS, step(run)]


def frozen_sql(level: int) -> str:
    """The shipped frozen schemas, and the current schema as the one at LATEST."""
    return schema.schema_sql() if level == LATEST else frozen_schema_sql(level)


def aim_at_next_level(monkeypatch: pytest.MonkeyPatch, steps: list[Step]) -> None:
    """The ordinary start, with NEXT as the latest level: upgrade first, then the check."""
    monkeypatch.setattr(
        main_module,
        "upgrade_database_file",
        partial(upgrade_database_file, steps=steps, expected=EXPECTED_NEXT, frozen_sql=frozen_sql),
    )
    monkeypatch.setattr(
        main_module,
        "check_database_file",
        partial(schema.check_database_file, steps=steps, expected=EXPECTED_NEXT),
    )


class _Log:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _record(self, event: str, **fields: Any) -> None:
        fields.pop("exc_info", None)
        self.events.append({"event": event, **fields})

    info = error = warning = debug = _record


UPGRADED_NOTE = "original note (upgraded)"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def database_one_level_behind(env: EnvSettings) -> None:
    async with running_app(env) as (app, _client):
        await app.state.db.execute(
            "INSERT INTO beans (name, notes) VALUES ('Kept', 'original note')"
        )


# ── 5: the ordinary start upgrades, after a backup ──────────────────────


async def test_the_start_upgrades_an_older_database_and_keeps_a_backup(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await database_one_level_behind(env)
    aim_at_next_level(monkeypatch, steps_to_next())
    fake = _Log()
    monkeypatch.setattr(upgrade_module, "log", fake)
    capsys.readouterr()

    async with running_app(env) as (app, _client):
        assert await app.state.db.fetch_value("PRAGMA user_version") == NEXT
        assert await app.state.db.fetch_value("SELECT notes FROM beans") == UPGRADED_NOTE

    copies = sorted(env.data_dir.glob("upgrade-backup-*"))
    assert len(copies) == 1 and copies[0].name.startswith(f"upgrade-backup-L{LATEST}-")
    old = sqlite3.connect(copies[0])
    try:
        assert old.execute("PRAGMA user_version").fetchone() == (LATEST,)
        assert old.execute("SELECT notes FROM beans").fetchone() == ("original note",)
    finally:
        old.close()
    done = next(e for e in fake.events if e["event"] == "database_upgraded")
    assert done["backup"] == str(copies[0]) and done["steps"] == {str(NEXT): {"beans": 1}}
    assert capsys.readouterr().err == ""


async def test_the_second_start_has_nothing_to_upgrade(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await database_one_level_behind(env)
    aim_at_next_level(monkeypatch, steps_to_next())
    async with running_app(env):
        pass
    async with running_app(env) as (app, _client):
        assert await app.state.db.fetch_value("SELECT notes FROM beans") == UPGRADED_NOTE
    assert len(list(env.data_dir.glob("upgrade-backup-*"))) == 1


async def test_a_failed_upgrade_refuses_the_start_and_leaves_the_database_as_it_was(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await database_one_level_behind(env)
    aim_at_next_level(monkeypatch, steps_to_next(_fails))
    path = env.database_path
    before, names = digest(path), sorted(p.name for p in path.parent.iterdir())
    capsys.readouterr()

    with pytest.raises(UpgradeRefused):
        async with running_app(env):
            pass

    err = capsys.readouterr().err.strip()
    assert err.count("\n") == 0
    assert f"failed at step {NEXT}" in err and "the fill went wrong" in err
    assert "was not changed" in err
    assert digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names


async def test_a_database_from_a_newer_version_is_refused_untouched_and_says_so(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    async with running_app(env):
        pass
    conn = sqlite3.connect(env.database_path)
    conn.execute(f"PRAGMA user_version = {LATEST + 7}")
    conn.close()
    path = env.database_path
    before, names = digest(path), sorted(p.name for p in path.parent.iterdir())
    capsys.readouterr()

    with pytest.raises(schema.SchemaMismatch) as refused:
        async with running_app(env):
            pass

    assert refused.value.case == "newer"
    err = capsys.readouterr().err.strip()
    assert err == (
        f"The database at {path} was made by a newer version of gaggiclanker than this one. "
        "Start the newer version again, or restore a backup made by this one."
    )
    assert digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names


async def test_an_old_backup_temporary_is_removed_at_every_start(env: EnvSettings) -> None:
    leftover = env.data_dir / "upgrade-backup-L0-20250101T000000Z.db.tmp"
    leftover.write_bytes(b"half a copy")
    async with running_app(env):
        pass
    assert not leftover.exists()


# ── 8: the reset removes the app's backups ──────────────────────────────


def test_a_reset_removes_the_backups_and_their_temporaries(tmp_path: Path) -> None:
    (tmp_path / "gaggiclanker.db").write_bytes(b"db")
    (tmp_path / "upgrade-backup-L0-20261012T120000Z.db").write_bytes(b"copy")
    (tmp_path / "upgrade-backup-L3-20261012T120000Z.db.tmp").write_bytes(b"half")
    (tmp_path / "notes.txt").write_text("not the app's")
    (tmp_path / RESET_MARKER).write_text("reset\n")

    assert complete_reset(tmp_path) is True

    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]


# ── 9: the other processes never upgrade ────────────────────────────────

SENTENCE = (
    "This database is upgraded the next time the app starts; start the app once, "
    "then run this again."
)


def behind_check(_module: object) -> Any:
    """The real check aimed at level 1, otherwise called exactly as the caller calls it."""
    return partial(schema.check_database_file, steps=steps_to_next(), expected=EXPECTED_NEXT)


def test_import_refuses_a_database_that_is_behind_and_does_not_touch_it(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.__main__ import main
    from gaggiclanker.imports import cli

    asyncio.run(database_one_level_behind(env))
    monkeypatch.setattr(
        cli,
        "check_database_file",
        partial(schema.check_database_file, steps=steps_to_next(), expected=EXPECTED_NEXT),
    )
    monkeypatch.setenv("DATA_DIR", str(env.data_dir))
    path = env.database_path
    before, names = digest(path), sorted(p.name for p in path.parent.iterdir())
    capsys.readouterr()

    status = main(["import", str(env.data_dir / "nothing.json")])

    assert status == 1
    assert capsys.readouterr().err.strip() == SENTENCE
    assert digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names


async def test_mcp_refuses_a_database_that_is_behind_and_does_not_touch_it(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaggiclanker.tools.mcp import stdio

    await database_one_level_behind(env)
    monkeypatch.setattr(
        stdio,
        "check_database_file",
        partial(schema.check_database_file, steps=steps_to_next(), expected=EXPECTED_NEXT),
    )
    path = env.database_path
    before, names = digest(path), sorted(p.name for p in path.parent.iterdir())

    with pytest.raises(SystemExit) as caught:
        await stdio.serve_stdio(env.data_dir)

    assert str(caught.value) == SENTENCE
    assert digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names


async def test_serves_early_check_lets_a_database_that_is_behind_through(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaggiclanker.__main__ import _refuse_foreign_database

    await database_one_level_behind(env)
    monkeypatch.setattr(schema, "check_database_file", behind_check(schema))
    path = env.database_path
    before = digest(path)

    _refuse_foreign_database(env)  # the lifespan upgrades it

    assert digest(path) == before


def test_serves_early_check_still_refuses_a_database_that_differs_or_is_newer(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.__main__ import _refuse_foreign_database

    conn = sqlite3.connect(env.database_path)
    conn.executescript(schema.schema_sql() + f"\nPRAGMA user_version = {LATEST + 3};")
    conn.close()
    with pytest.raises(SystemExit) as exit_:
        _refuse_foreign_database(env)
    assert exit_.value.code == 1
    assert "newer version of gaggiclanker" in capsys.readouterr().err


def test_serves_early_check_refuses_a_database_that_differs_at_the_latest_level(
    env: EnvSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    from gaggiclanker.__main__ import _refuse_foreign_database

    conn = sqlite3.connect(env.database_path)
    conn.executescript(schema.schema_sql() + "\nALTER TABLE sets ADD COLUMN surprise TEXT;")
    conn.close()
    with pytest.raises(SystemExit) as exit_:
        _refuse_foreign_database(env)
    assert exit_.value.code == 1
    assert "was made by a different version of gaggiclanker" in capsys.readouterr().err


async def test_an_older_database_in_the_wrong_state_reaches_the_upgrades_own_refusal(
    env: EnvSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real entry path: serve's early check, then the lifespan. The early check must not
    call an older file 'a different version' and send the person to delete it; the upgrade says
    what is wrong with it, against the schema of its own level."""
    from gaggiclanker.__main__ import _refuse_foreign_database

    await database_one_level_behind(env)
    conn = sqlite3.connect(env.database_path)
    conn.execute("ALTER TABLE sets ADD COLUMN surprise TEXT")
    conn.close()
    aim_at_next_level(monkeypatch, steps_to_next())
    monkeypatch.setattr(schema, "check_database_file", behind_check(schema))
    fake = _Log()
    monkeypatch.setattr(upgrade_module, "log", fake)
    path = env.database_path
    before, names = digest(path), sorted(p.name for p in path.parent.iterdir())
    capsys.readouterr()

    _refuse_foreign_database(env)  # lets it through
    with pytest.raises(UpgradeRefused):
        async with running_app(env):
            pass

    err = capsys.readouterr().err.strip()
    assert "cannot be upgraded" in err and "was not changed" in err
    assert "different version" not in err and "Delete it to start fresh" not in err
    refused = next(e for e in fake.events if e["event"] == "database_upgrade_refused")
    assert refused["db_level"] == LATEST
    assert "unexpected column sets.surprise" in refused["differences"]
    assert not any(e["event"] == "database_schema_differs" for e in fake.events)
    assert digest(path) == before
    assert sorted(p.name for p in path.parent.iterdir()) == names
