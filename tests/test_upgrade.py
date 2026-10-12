"""The upgrade at start: steps, the backup, the transaction, the checks, and what it refuses.

The machinery is tested with synthetic steps on a small schema of its own, injected through
the ``steps``/``expected``/``frozen_sql`` parameters; the registry that ships stays empty.
"""

from __future__ import annotations

import errno
import gc
import os
import shutil
import sqlite3
from collections import namedtuple
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db import backup as backup_module
from gaggiclanker.db import schema
from gaggiclanker.db import upgrade as upgrade_module
from gaggiclanker.db.upgrade import (
    UpgradeRefused,
    UpgradeReport,
    clean_stale_backup_temporaries,
    run_steps,
    upgrade_connection,
    upgrade_database_file,
)
from gaggiclanker.db.upgrades import Step

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
STAMP = "20261012T120000Z"

# ── a small schema of our own: a referenced table, a child, an index ────────

V0 = """
CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT NOT NULL) STRICT;
CREATE TABLE child (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER NOT NULL REFERENCES parent(id),
    note TEXT NOT NULL DEFAULT '' CHECK (length(note) < 50)
) STRICT;
CREATE INDEX idx_child_parent ON child(parent_id);
CREATE VIEW v_child AS SELECT id, note FROM child;
INSERT INTO parent (id, name) VALUES (1, 'one'), (2, 'two');
PRAGMA user_version = 0;
"""

#: Level 1 as an added column on the child, with an index on it.
V1_ADD_COLUMN = (
    V0.replace(
        "CHECK (length(note) < 50)\n)",
        "CHECK (length(note) < 50),\n    size INTEGER NOT NULL DEFAULT 0\n)",
    )
    .replace("CREATE VIEW", "CREATE INDEX idx_child_size ON child(size);\nCREATE VIEW")
    .replace("user_version = 0", "user_version = 1")
)

#: Level 1 with the referenced table rebuilt around one more column.
V1_REBUILD = V0.replace(
    "name TEXT NOT NULL) STRICT", "name TEXT NOT NULL, label TEXT NOT NULL DEFAULT '') STRICT"
).replace("user_version = 0", "user_version = 1")

#: Level 1: same structure, another level.
V1_DATA = V0.replace("user_version = 0", "user_version = 1")
V2_DATA = V0.replace("user_version = 0", "user_version = 2")


def fingerprint(sql: str) -> str:
    return schema.fingerprint_of(schema.describe_sql(sql))


def expected_of(sql: str) -> dict[str, str]:
    return schema.describe_sql(sql)


def frozen(level_sql: dict[int, str]) -> Callable[[int], str]:
    return lambda level: level_sql[level]


def add_column_step(*, fill: bool = True) -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("ALTER TABLE child ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX idx_child_size ON child(size)")
        if fill:
            n = conn.execute("UPDATE child SET size = length(note)").rowcount
            return {"filled": n}
        return {}

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)


def data_step(level: int, expects: str) -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        return {"renamed": conn.execute("UPDATE parent SET name = name || '!'").rowcount}

    return Step(level=level, expects=expects, foreign_keys_off=False, run=run)


def rebuild_step() -> Step:
    """The SQLite 12-step on a table other tables refer to."""

    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute(
            "CREATE TABLE parent_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "label TEXT NOT NULL DEFAULT '') STRICT"
        )
        copied = conn.execute(
            "INSERT INTO parent_new (id, name, label) SELECT id, name, upper(name) FROM parent"
        ).rowcount
        conn.execute("DROP TABLE parent")
        conn.execute("ALTER TABLE parent_new RENAME TO parent")
        return {"rebuilt": copied}

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=True, run=run)


def make_database(directory: Path, sql: str = V0, *, name: str = "gaggiclanker.db") -> Path:
    path = directory / name
    conn = sqlite3.connect(path)
    try:
        conn.executescript(sql)
        conn.execute("INSERT INTO child (id, parent_id, note) VALUES (1, 1, 'abc'), (2, 2, 'de')")
        conn.commit()
    finally:
        conn.close()
    return path


def snapshot(path: Path) -> tuple[list[str], int]:
    """Every statement and row of the database, and its level: what 'as it was' means."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return list(conn.iterdump()), int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def backups(directory: Path) -> list[Path]:
    return sorted(directory.glob("upgrade-backup-*"))


def upgrade(
    path: Path,
    steps: list[Step],
    sql: str,
    frozen_sql: Callable[[int], str] | None = None,
) -> UpgradeReport | None:
    return upgrade_database_file(
        path,
        path.parent,
        steps=steps,
        expected=expected_of(sql),
        frozen_sql=frozen_sql or frozen({0: V0}),
        now=NOW,
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


class _Log:
    """Stands in for the module's logger: other tests configure structlog to drop info, and
    ``capture_logs`` only sees what the configured logger lets through."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _record(self, event: str, **fields: object) -> None:
        fields.pop("exc_info", None)
        self.events.append({"event": event, **fields})

    info = error = warning = debug = _record


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    fake = _Log()
    monkeypatch.setattr(upgrade_module, "log", fake)
    return fake.events


# ── 6: the happy paths ───────────────────────────────────────────────────


def test_an_add_column_step_with_a_fill_upgrades_and_keeps_every_row(data_dir: Path) -> None:
    path = make_database(data_dir)
    report = upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert report is not None
    assert (report.from_level, report.to_level) == (0, 1)
    assert [(r.level, r.counts) for r in report.steps] == [(1, {"filled": 2})]
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT id, note, size FROM child ORDER BY id").fetchall() == [
            (1, "abc", 3),
            (2, "de", 2),
        ]
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
        assert schema.schema_differences(conn, expected_of(V1_ADD_COLUMN)) == []
    finally:
        conn.close()


def test_a_data_only_step_moves_the_level_and_leaves_the_structure(data_dir: Path) -> None:
    path = make_database(data_dir)
    step = data_step(1, fingerprint(V0))
    report = upgrade(path, [step], V1_DATA)
    assert report is not None and report.to_level == 1
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT name FROM parent ORDER BY id").fetchall() == [
            ("one!",),
            ("two!",),
        ]
        assert schema.describe(conn)["user_version"] == "1"
        # Only the level differs from before: the data-only step changed no structure.
        assert [k for k in schema.describe(conn) if k != "user_version"] == [
            k for k in expected_of(V0) if k != "user_version"
        ]
    finally:
        conn.close()


def test_a_step_that_rebuilds_a_referenced_table_passes_the_reference_check(
    data_dir: Path,
) -> None:
    path = make_database(data_dir)
    report = upgrade(path, [rebuild_step()], V1_REBUILD)
    assert report is not None
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT id, name, label FROM parent ORDER BY id").fetchall() == [
            (1, "one", "ONE"),
            (2, "two", "TWO"),
        ]
        # The children were not cascaded away by the DROP.
        assert conn.execute("SELECT COUNT(*) FROM child").fetchone() == (2,)
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (0,)  # a new connection's own
        assert schema.schema_differences(conn, expected_of(V1_REBUILD)) == []
    finally:
        conn.close()


def test_the_same_rebuild_destroys_the_children_when_foreign_keys_stay_on(
    data_dir: Path,
) -> None:
    """The reason a step declares ``foreign_keys_off``: with them on, DROP cascades or refuses."""
    path = make_database(data_dir)
    step = rebuild_step()
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.Error):
            step.run(conn)
    finally:
        conn.close()


def test_two_steps_chain_from_level_zero(data_dir: Path) -> None:
    path = make_database(data_dir)
    steps = [add_column_step(), data_step(2, fingerprint(V1_ADD_COLUMN))]
    sql_v2 = V1_ADD_COLUMN.replace("user_version = 1", "user_version = 2")
    report = upgrade(path, steps, sql_v2, frozen({0: V0, 1: V1_ADD_COLUMN}))
    assert report is not None
    assert (report.from_level, report.to_level) == (0, 2)
    assert [r.level for r in report.steps] == [1, 2]
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone() == (2,)
        assert conn.execute("SELECT name FROM parent WHERE id = 1").fetchone() == ("one!",)
    finally:
        conn.close()


def test_a_database_partway_up_starts_at_its_own_step(data_dir: Path) -> None:
    path = make_database(data_dir, V1_ADD_COLUMN)
    steps = [add_column_step(), data_step(2, fingerprint(V1_ADD_COLUMN))]
    sql_v2 = V1_ADD_COLUMN.replace("user_version = 1", "user_version = 2")
    report = upgrade(path, steps, sql_v2, frozen({0: V0, 1: V1_ADD_COLUMN}))
    assert report is not None
    assert (report.from_level, [r.level for r in report.steps]) == (1, [2])
    assert [p.name for p in backups(data_dir)] == [f"upgrade-backup-L1-{STAMP}.db"]


def test_the_upgrade_is_logged_with_the_levels_the_backup_and_each_steps_counts(
    data_dir: Path, logs: list[dict[str, Any]]
) -> None:
    path = make_database(data_dir)
    upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    event = next(e for e in logs if e["event"] == "database_upgraded")
    assert event["from_level"] == 0 and event["to_level"] == 1
    assert event["backup"] == str(data_dir / f"upgrade-backup-L0-{STAMP}.db")
    assert event["steps"] == {"1": {"filled": 2}}


# ── 6.5: a failure leaves the database as it was ─────────────────────────


def failing_after_writing() -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("UPDATE parent SET name = 'changed'")
        conn.execute("INSERT INTO parent VALUES (9, 'nine')")
        conn.execute("ALTER TABLE child ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        raise RuntimeError("the fill went wrong")

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)


def leaving_a_missing_index() -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("ALTER TABLE child ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE parent SET name = 'changed'")
        return {}  # forgot idx_child_size

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)


def leaving_a_dangling_reference() -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("ALTER TABLE child ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX idx_child_size ON child(size)")
        conn.execute("INSERT INTO child (id, parent_id, note) VALUES (7, 999, 'orphan')")
        return {}

    # Foreign keys off, as a rebuilding step has them: nothing stops the orphan but the check.
    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=True, run=run)


def breaking_a_check_constraint() -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("ALTER TABLE child ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX idx_child_size ON child(size)")
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("INSERT INTO child (id, parent_id, note) VALUES (8, 1, ?)", ("x" * 60,))
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        return {}

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)


def committing_by_itself() -> Step:
    def run(conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("UPDATE parent SET name = 'changed'")
        conn.execute("COMMIT")
        return {}

    return Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        (failing_after_writing(), "the fill went wrong"),
        (leaving_a_missing_index(), "missing index idx_child_size"),
        (leaving_a_dangling_reference(), "refer to missing rows in parent"),
        (breaking_a_check_constraint(), "integrity check"),
    ],
    ids=["raises", "differs from the schema", "dangling reference", "integrity check"],
)
def test_a_failure_leaves_every_row_the_structure_and_the_level_as_they_were(
    data_dir: Path, step: Step, reason: str
) -> None:
    path = make_database(data_dir)
    before = snapshot(path)
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [step], V1_ADD_COLUMN)
    assert reason in str(caught.value)
    assert str(path) in str(caught.value)
    assert snapshot(path) == before
    assert backups(data_dir) == []
    assert sorted(p.name for p in data_dir.glob("gaggiclanker.db*")) == ["gaggiclanker.db"]


def test_a_step_that_commits_by_itself_is_refused_and_named(data_dir: Path) -> None:
    """A step must never end the transaction. Its own COMMIT already made its rows durable,
    which no rollback can undo, so all the upgrade can do is stop, name the step, and keep the
    backup (the level has not moved, and no later step ran)."""
    path = make_database(data_dir)
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [committing_by_itself()], V1_ADD_COLUMN)
    assert caught.value.step == 1
    assert "ended the upgrade's transaction" in str(caught.value)
    assert snapshot(path)[1] == 0
    assert [p.name for p in backups(data_dir)] == [f"upgrade-backup-L0-{STAMP}.db"]
    assert f"upgrade-backup-L0-{STAMP}.db" in str(caught.value)


def test_a_failure_is_logged_with_the_step_and_the_reason(
    data_dir: Path, logs: list[dict[str, Any]]
) -> None:
    path = make_database(data_dir)
    with pytest.raises(UpgradeRefused):
        upgrade(path, [failing_after_writing()], V1_ADD_COLUMN)
    event = next(e for e in logs if e["event"] == "database_upgrade_failed")
    assert event["step"] == 1 and "the fill went wrong" in event["reason"]


def test_a_failure_in_a_later_step_undoes_the_earlier_ones_too(data_dir: Path) -> None:
    path = make_database(data_dir)
    before = snapshot(path)
    second = data_step(2, "not-the-fingerprint-of-level-1")
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(
            path,
            [add_column_step(), second],
            V1_ADD_COLUMN.replace("user_version = 1", "user_version = 2"),
            frozen({0: V0, 1: V1_ADD_COLUMN}),
        )
    assert caught.value.step == 2
    assert snapshot(path) == before
    assert backups(data_dir) == []


def test_a_rollback_restores_the_level_too(data_dir: Path) -> None:
    path = make_database(data_dir)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        with pytest.raises(UpgradeRefused):
            upgrade_connection(
                conn, steps=[failing_after_writing()], expected=expected_of(V1_ADD_COLUMN)
            )
        assert conn.execute("PRAGMA user_version").fetchone() == (0,)
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert not conn.in_transaction
    finally:
        conn.close()


def test_a_reference_that_already_dangled_does_not_stop_the_upgrade(data_dir: Path) -> None:
    path = make_database(data_dir)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO child (id, parent_id, note) VALUES (5, 500, 'old orphan')")
    conn.commit()
    conn.close()
    assert upgrade(path, [add_column_step()], V1_ADD_COLUMN) is not None
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM child WHERE parent_id = 500").fetchone() == (1,)
    finally:
        conn.close()


def test_a_step_run_with_foreign_keys_on_is_refused_by_run_steps(data_dir: Path) -> None:
    path = make_database(data_dir)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(UpgradeRefused, match="needs foreign keys off"):
            run_steps(conn, steps=[rebuild_step()])
    finally:
        conn.close()


# ── 5 and 6.1: refusals before any write ────────────────────────────────


def test_a_database_that_is_not_the_one_the_step_expects_is_refused_with_no_backup(
    data_dir: Path, logs: list[dict[str, Any]]
) -> None:
    path = make_database(data_dir)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert "cannot be upgraded" in str(caught.value) and str(path) in str(caught.value)
    assert path.read_bytes() == before
    assert backups(data_dir) == []
    event = next(e for e in logs if e["event"] == "database_upgrade_refused")
    assert event["db_level"] == 0
    # Against the frozen schema of its own level, not the current one: the extra column is
    # named, and the current schema's own additions are not.
    assert "unexpected column child.surprise" in event["differences"]
    assert not any("size" in d for d in event["differences"])
    assert len(event["differences"]) <= 5


def test_a_step_with_the_wrong_expects_refuses_before_any_write(data_dir: Path) -> None:
    path = make_database(data_dir)
    step = Step(level=1, expects="0" * 12, foreign_keys_off=False, run=lambda c: {})
    before = path.read_bytes()
    with pytest.raises(UpgradeRefused):
        upgrade(path, [step], V1_DATA)
    assert path.read_bytes() == before
    assert backups(data_dir) == []


def test_too_little_room_refuses_with_no_backup_and_the_database_untouched(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, logs: list[dict[str, Any]]
) -> None:
    path = make_database(data_dir)
    usage = namedtuple("usage", "total used free")
    asked: list[Path] = []

    def disk_usage(where: Path) -> object:
        asked.append(Path(where))
        return usage(10**9, 10**9 - 1, 1)

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    before = path.read_bytes()
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert "not enough free disk space" in str(caught.value)
    assert asked == [data_dir]
    assert path.read_bytes() == before
    assert backups(data_dir) == []
    assert any(e["event"] == "database_upgrade_refused" and e["reason"] == "disk" for e in logs)


def test_the_room_asked_for_is_twice_the_database(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_database(data_dir)
    size = backup_module.live_size(path)
    usage = namedtuple("usage", "total used free")
    # Exactly twice fits, one byte less does not.
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(0, 0, 2 * size - 1))
    with pytest.raises(UpgradeRefused):
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(0, 0, 2 * size))
    assert upgrade(path, [add_column_step()], V1_ADD_COLUMN) is not None


# ── 6.2: the backup ──────────────────────────────────────────────────────


def test_the_backup_is_a_database_at_the_old_level_with_the_old_rows(data_dir: Path) -> None:
    path = make_database(data_dir)
    before = snapshot(path)
    report = upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert report is not None and report.backup is not None
    assert report.backup == data_dir / f"upgrade-backup-L0-{STAMP}.db"
    assert [p.name for p in backups(data_dir)] == [report.backup.name]
    copy = snapshot(report.backup)
    assert copy[1] == 0
    # VACUUM INTO rewrites the file but not its content.
    conn = sqlite3.connect(report.backup)
    try:
        assert conn.execute("SELECT id, note FROM child ORDER BY id").fetchall() == [
            (1, "abc"),
            (2, "de"),
        ]
        assert conn.execute("SELECT name FROM parent ORDER BY id").fetchall() == [
            ("one",),
            ("two",),
        ]
        assert schema.schema_differences(conn, expected_of(V0)) == []
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        conn.close()
    assert before[1] == 0
    assert sorted(p.name for p in data_dir.glob("upgrade-backup-*")) == [report.backup.name]


def test_the_final_name_is_absent_while_the_backup_is_being_flushed(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_database(data_dir)
    final = data_dir / f"upgrade-backup-L0-{STAMP}.db"
    temporary = data_dir / f"upgrade-backup-L0-{STAMP}.db.tmp"
    seen: list[tuple[str, bool, bool]] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        seen.append((target.name, final.exists(), temporary.exists()))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    flushes = [entry for entry in seen if entry[0] in {temporary.name, data_dir.name}]
    assert flushes[0] == (temporary.name, False, True)  # the file, before the rename
    assert flushes[-1] == (data_dir.name, True, False)  # the directory, after it
    assert len(flushes) == 2


def test_a_failed_flush_leaves_no_backup_under_either_name(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, logs: list[dict[str, Any]]
) -> None:
    path = make_database(data_dir)
    before = path.read_bytes()

    def broken(fd: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert "could not be backed up" in str(caught.value)
    assert backups(data_dir) == []
    assert path.read_bytes() == before
    assert any(e["event"] == "database_upgrade_backup_failed" for e in logs)


def test_a_failed_directory_flush_after_the_rename_removes_both_names(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_database(data_dir)
    before = path.read_bytes()
    real_fsync = os.fsync

    def flaky(fd: int) -> None:
        if Path(os.readlink(f"/proc/self/fd/{fd}")) == data_dir:
            raise OSError(errno.EIO, "I/O error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky)
    with pytest.raises(UpgradeRefused) as caught:
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert "could not be backed up" in str(caught.value)
    assert backups(data_dir) == []
    assert path.read_bytes() == before


def test_a_failed_rename_leaves_no_backup_under_either_name(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_database(data_dir)

    def broken(src: object, dst: object) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(UpgradeRefused):
        upgrade(path, [add_column_step()], V1_ADD_COLUMN)
    assert backups(data_dir) == []


def test_the_transaction_is_immediate_so_no_other_writer_gets_in_during_a_step(
    data_dir: Path,
) -> None:
    """A deferred BEGIN would let a second writer in until the step's first write; in the
    app's WAL mode only an immediate one locks it out from the start."""
    path = make_database(data_dir)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.close()
    seen: dict[str, str] = {}

    def run(conn: sqlite3.Connection) -> dict[str, int]:
        other = sqlite3.connect(path, timeout=0)
        try:
            other.execute("INSERT INTO parent (id, name) VALUES (50, 'intruder')")
            other.commit()
            seen["write"] = "went through"
        except sqlite3.OperationalError as exc:
            seen["write"] = str(exc)
        finally:
            other.close()
        return add_column_step().run(conn)

    step = Step(level=1, expects=fingerprint(V0), foreign_keys_off=False, run=run)
    assert upgrade(path, [step], V1_ADD_COLUMN) is not None
    assert seen["write"] == "database is locked"


def test_foreign_keys_are_off_for_a_step_that_needs_it_and_back_on_afterwards(
    data_dir: Path,
) -> None:
    during: list[int] = []

    def watching(inner: Step) -> Step:
        def run(conn: sqlite3.Connection) -> dict[str, int]:
            during.append(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            return inner.run(conn)

        return Step(inner.level, inner.expects, inner.foreign_keys_off, run)

    for step, expected_sql, want in (
        (watching(rebuild_step()), V1_REBUILD, 0),
        (watching(add_column_step()), V1_ADD_COLUMN, 1),
    ):
        during.clear()
        path = make_database(data_dir, name=f"fk{want}.db")
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            upgrade_connection(conn, steps=[step], expected=expected_of(expected_sql))
            assert during == [want]
            assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert not conn.in_transaction
        finally:
            conn.close()


# ── 7: a crash, and the leftovers of one ────────────────────────────────


def test_a_crash_between_a_steps_writes_and_the_commit_starts_again_from_the_same_level(
    data_dir: Path,
) -> None:
    """The step is killed by an exception after it wrote rows (a crash is the same: the
    transaction never commits). The next start finds the same level and the same rows."""
    path = make_database(data_dir)
    before = snapshot(path)
    with pytest.raises(UpgradeRefused):
        upgrade(path, [failing_after_writing()], V1_ADD_COLUMN)
    assert snapshot(path) == before
    assert upgrade(path, [add_column_step()], V1_ADD_COLUMN) is not None


def test_a_hard_stop_inside_a_transaction_leaves_the_level_where_it_was(data_dir: Path) -> None:
    """The real thing, without an exception to catch: the connection just goes away."""
    path = make_database(data_dir)
    before = snapshot(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    add_column_step().run(conn)
    conn.execute("PRAGMA user_version = 1")
    del conn  # never committed, never rolled back
    gc.collect()
    assert snapshot(path) == before


def test_a_temporary_left_by_a_crash_in_the_backup_is_deleted_at_every_start(
    data_dir: Path,
) -> None:
    path = make_database(data_dir)
    leftover = data_dir / "upgrade-backup-L0-20250101T000000Z.db.tmp"
    leftover.write_bytes(b"half a copy")
    kept = data_dir / "upgrade-backup-L0-20250101T000001Z.db"
    kept.write_bytes(b"a whole one")
    # Nothing to upgrade, and it still goes.
    assert upgrade(path, [], V0) is None
    assert not leftover.exists()
    assert kept.exists()
    leftover.write_bytes(b"again")
    assert clean_stale_backup_temporaries(data_dir) == 1
    assert not leftover.exists() and kept.exists()


def test_a_temporary_is_deleted_even_when_there_is_no_database(data_dir: Path) -> None:
    leftover = data_dir / "upgrade-backup-L0-20250101T000000Z.db.tmp"
    leftover.write_bytes(b"x")
    assert upgrade(data_dir / "gaggiclanker.db", [], V0) is None
    assert not leftover.exists()


def test_a_numeric_level_in_a_log_event_survives_the_apps_real_logging(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The app's processors write ``level`` themselves (the severity), so a field of that name
    is overwritten; the events name theirs ``db_level``, ``from_level`` and ``to_level``.
    Run with the real configuration, not a stand-in logger, so a collision cannot hide."""
    import json

    from gaggiclanker.infra.logging import configure_logging

    refused = make_database(data_dir, name="refused.db")
    conn = sqlite3.connect(refused)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    newer = make_database(data_dir, V0.replace("= 0", "= 9"), name="newer.db")
    done = make_database(data_dir, name="done.db")
    configure_logging("info", json_output=True)
    try:
        with pytest.raises(UpgradeRefused):
            upgrade(refused, [add_column_step()], V1_ADD_COLUMN)
        with pytest.raises(schema.SchemaMismatch):
            schema.check_database_file(newer, steps=[], expected=expected_of(V0))
        upgrade(done, [add_column_step()], V1_ADD_COLUMN)
        out = capsys.readouterr().out
    finally:
        configure_logging("warning", json_output=True)
    events = {e["event"]: e for e in (json.loads(x) for x in out.splitlines() if x.startswith("{"))}
    assert events["database_upgrade_refused"]["db_level"] == 0
    assert events["database_upgrade_refused"]["level"] == "error"
    assert events["database_schema_differs"]["db_level"] == 9
    assert events["database_upgraded"]["from_level"] == 0
    assert events["database_upgraded"]["to_level"] == 1


# ── what it leaves alone ─────────────────────────────────────────────────


async def test_a_database_at_the_latest_level_is_not_touched(data_dir: Path) -> None:
    from gaggiclanker.db.connection import Database

    db = Database(data_dir / "gaggiclanker.db")
    await db.connect()
    await schema.create_schema(db)
    await db.close()
    path = data_dir / "gaggiclanker.db"
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert upgrade_database_file(path, data_dir) is None
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert backups(data_dir) == []
    assert sorted(p.name for p in data_dir.glob("gaggiclanker.db*")) == ["gaggiclanker.db"]


def test_a_missing_empty_or_blank_file_and_a_newer_or_other_database_are_left_to_the_check(
    data_dir: Path,
) -> None:
    assert upgrade(data_dir / "absent.db", [add_column_step()], V1_ADD_COLUMN) is None
    (data_dir / "empty.db").write_bytes(b"")
    assert upgrade(data_dir / "empty.db", [add_column_step()], V1_ADD_COLUMN) is None
    sqlite3.connect(data_dir / "blank.db").close()
    assert upgrade(data_dir / "blank.db", [add_column_step()], V1_ADD_COLUMN) is None
    newer = make_database(data_dir, V0.replace("user_version = 0", "user_version = 9"), name="n.db")
    before = newer.read_bytes()
    assert upgrade(newer, [add_column_step()], V1_ADD_COLUMN) is None
    assert newer.read_bytes() == before
    other = make_database(data_dir, V1_ADD_COLUMN, name="o.db")
    conn = sqlite3.connect(other)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    before = other.read_bytes()
    assert upgrade(other, [add_column_step()], V1_ADD_COLUMN) is None  # at the latest level
    assert other.read_bytes() == before
    assert backups(data_dir) == []


# ── 9: one function decides the case ─────────────────────────────────────


def test_classify_names_each_case(data_dir: Path) -> None:
    steps = [add_column_step()]
    expected = expected_of(V1_ADD_COLUMN)

    def case(path: Path) -> str:
        return schema.classify(path, steps=steps, expected=expected)

    assert case(data_dir / "absent.db") == "missing"
    (data_dir / "empty.db").write_bytes(b"")
    assert case(data_dir / "empty.db") == "missing"
    sqlite3.connect(data_dir / "blank.db").close()
    assert case(data_dir / "blank.db") == "missing"

    assert case(make_database(data_dir, V1_ADD_COLUMN, name="current.db")) == "current"
    assert case(make_database(data_dir, V0, name="behind.db")) == "upgradable"
    assert (
        case(make_database(data_dir, V1_ADD_COLUMN.replace("= 1", "= 4"), name="newer.db"))
        == "newer"
    )

    # Behind, but not the structure its step expects.
    odd = make_database(data_dir, V0, name="odd.db")
    conn = sqlite3.connect(odd)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    assert case(odd) == "differs"
    # At the latest level, with another structure.
    other = make_database(data_dir, V1_ADD_COLUMN, name="other.db")
    conn = sqlite3.connect(other)
    conn.execute("DROP INDEX idx_child_size")
    conn.commit()
    conn.close()
    assert case(other) == "differs"
    # The right structure at a level no step leads from.
    assert case(make_database(data_dir, V0.replace("= 0", "= 1"), name="lvl.db")) == "differs"


def test_classify_leaves_no_sidecar_and_writes_nothing(data_dir: Path) -> None:
    path = make_database(data_dir)
    before = path.read_bytes()
    schema.classify(path, steps=[add_column_step()], expected=expected_of(V1_ADD_COLUMN))
    assert path.read_bytes() == before
    assert sorted(p.name for p in data_dir.glob("gaggiclanker.db*")) == ["gaggiclanker.db"]


def test_the_check_refuses_a_database_that_is_behind_with_the_sentence(data_dir: Path) -> None:
    path = make_database(data_dir)
    kwargs = {"steps": [add_column_step()], "expected": expected_of(V1_ADD_COLUMN)}
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(path, **kwargs)  # type: ignore[arg-type]
    assert caught.value.case == "upgradable"
    assert str(caught.value) == (
        "This database is upgraded the next time the app starts; start the app once, "
        "then run this again."
    )
    # The app's own early check lets it through so the start can upgrade it.
    schema.check_database_file(path, allow_older=True, **kwargs)  # type: ignore[arg-type]


def test_the_early_check_lets_every_older_file_through_and_refuses_the_rest(
    data_dir: Path,
) -> None:
    """Older than the latest level: the start upgrades it, or refuses it with its own line
    (an older file in the wrong state is not for the early check to call 'different').
    At the latest level, or newer, the early check refuses as ever."""
    kwargs: dict[str, Any] = {
        "steps": [add_column_step()],
        "expected": expected_of(V1_ADD_COLUMN),
        "allow_older": True,
    }
    behind = make_database(data_dir, V0, name="behind.db")
    wrong = make_database(data_dir, V0, name="wrong.db")
    conn = sqlite3.connect(wrong)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    assert schema.classify(wrong, steps=kwargs["steps"], expected=kwargs["expected"]) == "differs"
    current = make_database(data_dir, V1_ADD_COLUMN, name="current.db")
    for passes in (behind, wrong, current, data_dir / "absent.db"):
        schema.check_database_file(passes, **kwargs)

    latest_wrong = make_database(data_dir, V1_ADD_COLUMN, name="latest_wrong.db")
    conn = sqlite3.connect(latest_wrong)
    conn.execute("DROP INDEX idx_child_size")
    conn.commit()
    conn.close()
    newer = make_database(data_dir, V0.replace("= 0", "= 5"), name="newer.db")
    for refused, case in ((latest_wrong, "differs"), (newer, "newer")):
        with pytest.raises(schema.SchemaMismatch) as caught:
            schema.check_database_file(refused, **kwargs)
        assert caught.value.case == case

    # Without the allowance (import, mcp) the older file in the wrong state is refused too.
    kwargs["allow_older"] = False
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(wrong, **kwargs)
    assert caught.value.case == "differs"


def test_a_database_from_a_newer_version_is_refused_and_says_so(data_dir: Path) -> None:
    path = make_database(data_dir, V0.replace("= 0", "= 9"))
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(path, steps=[], expected=expected_of(V0))
    assert caught.value.case == "newer"
    assert "newer version of gaggiclanker" in str(caught.value)
    assert "Delete it" not in str(caught.value)


def test_a_database_that_differs_is_refused_as_before(data_dir: Path) -> None:
    path = make_database(data_dir)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE child ADD COLUMN surprise TEXT")
    conn.commit()
    conn.close()
    with pytest.raises(schema.SchemaMismatch) as caught:
        schema.check_database_file(path, steps=[], expected=expected_of(V0))
    assert caught.value.case == "differs"
    assert "Delete it to start fresh" in str(caught.value)
