"""Shipped migrations are frozen: the gate, so a real edit never reaches a database.

The ledger refuses to boot a database whose applied migration now runs
different SQL, and the only way past that refusal is deleting the database. So
the refusal must never be met in the field: this file pins the statement
checksum of every shipped migration, and a change to what one runs fails here,
in the test suite, instead of on somebody's archive. Comments and whitespace
are not part of the checksum (`statements`), so documenting a file stays free.

A new migration is pinned by adding its line below in the same commit. A
changed pin is never the fix: write a new migration that makes the change.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.migrations import load_migrations, statements

SHIPPED: dict[str, str] = {
    "0001": "f8a49816e2da28d1db1f9f1cae82482eee475c7c3c2981d6f936d8570995b300",
    "0002": "ce9b1558f74e7de48f6695594c3720c9a9d7b3f857dc2403fe3690f8fb0b9f36",
    "0003": "280f4540d2f6aa8a41a6935e65f684fbe195571fa68eea3a39d7a740c00f2c02",
    "0004": "29a5e836d528154d26dbbfddb8a8e2c4f09107b3044f8426cb3609ba5606d685",
    "0005": "6bcaf046a0657684e3e79d216babc2f4e5982dee2e50e4f4b07faadb502acc3a",
    "0006": "9896e0dbf3df8cb96de04d08cf32a9562b3480a36e52bfb1ec09ee14e2287978",
    "0007": "3786546b04414abfcb4ae4c58779cb207b34dc58b0e4d3389b8a5f7ed815d385",
    "0008": "9618ed2e9df11a0de7b8a281446ca4b5e6996e8add81cbdc3169691b0b77e559",
    "0009": "3a71bed2924176669dc8fff32c823f0c2cfda0b52314e94907f7a2dea12664c9",
    "0010": "349563dbbc81dc587260dc7ba5a54b8b20dc66873a3ab78d4d31c927ef78f8cb",
    "0011": "af061ead5c60ff5a5f9713dbd944f885d39e247a0e9d29649077fecc6863b036",
    "0012": "b550812eb978964ad833414d90b3ca74b73837d245a0a9876ce1d3d7e8ef8c2b",
    "0013": "9f1dae47f1c9292b7190c409293d57783cf6311a2cb28a21c6139f557579a934",
    "0014": "825c79581ac0aa5117e210ac41b0f51b9fae0d480297becc98fed5cb27982638",
    "0015": "3481c7e345fb6ba68df979ebe088e33cb939833c3bfcd1f7aa1350a484c0aa5c",
    "0016": "33d519c6b939ecd3afdfa182ce6a9616d6b30792f0b0340d43033ab83bba1db0",
    "0017": "259f4a38b06954fbb71f28a1eb170c1e4e11c4f94b11db981073f88caa5408f3",
    "0018": "916a40a25c768fc12ee09935c22d95f90d7ed59fbe013e746aaabb37e22f3db0",
    "0019": "96ea7a1f9b8cffaca298cb649acb19a5c50d97b233660e9097c10b2939a85559",
    "0020": "71bc0723de8eb486e8ed3ab007ce1741594437dc54ac2fd969d76a10f602c55c",
    "0021": "549a9712246148a693f98f6b6c43fc32c8ebbcdbb2cc820fd67171a478db1b28",
    "0022": "717f2d263b950dec6a74b5c9875fc06a7adb827b6a258a8805e46d42eb786df1",
    "0023": "43c3f79cfc932c75a3849ca28a98489c5cb637cf608a57b7aa738ab91db96fc1",
    "0024": "515a690b18c337fc1a86d8c7aaa34ab612484ce6d5efa9bd3cf6c486afdc23c7",
}


def test_no_shipped_migration_runs_different_sql() -> None:
    changed = [
        f"{m.version}_{m.name}"
        for m in load_migrations()
        if m.version in SHIPPED and m.checksum != SHIPPED[m.version]
    ]
    assert not changed, (
        f"shipped migrations edited: {changed}. Every existing database would refuse to "
        "boot. Revert the edit and put the change in a new migration."
    )


def test_no_shipped_migration_is_removed() -> None:
    present = {m.version for m in load_migrations()}
    assert sorted(set(SHIPPED) - present) == []


def test_every_migration_is_pinned() -> None:
    missing = [
        f'    "{m.version}": "{m.checksum}",' for m in load_migrations() if m.version not in SHIPPED
    ]
    assert not missing, "pin the new migration in SHIPPED:\n" + "\n".join(missing)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("CREATE TABLE a (id INTEGER);", "-- why\nCREATE TABLE a (id INTEGER); -- trailing"),
        ("CREATE TABLE a (id INTEGER);", "/* block\n comment */ CREATE TABLE a (id INTEGER);"),
        ("CREATE TABLE a (id INTEGER);", "CREATE   TABLE a(\n    id INTEGER\n);\n\n"),
        ("CREATE TABLE a (x, y);", "CREATE TABLE a ( x ,y ) ;"),
    ],
)
def test_comments_and_layout_are_not_statements(before: str, after: str) -> None:
    assert statements(before) == statements(after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # Data: a space, a comment marker or a quote inside a literal is content.
        ("INSERT INTO t VALUES ('a b');", "INSERT INTO t VALUES ('a  b');"),
        ("INSERT INTO t VALUES ('-- not a comment');", "INSERT INTO t VALUES ('');"),
        ("INSERT INTO t VALUES ('it''s -- x');", "INSERT INTO t VALUES ('it''s');"),
        ('CREATE TABLE "a b" (id);', 'CREATE TABLE "a  b" (id);'),
        ("CREATE TABLE [a b] (id);", "CREATE TABLE [a  b] (id);"),
        # Words that touch or do not are different SQL.
        ("SELECT a b FROM t;", "SELECT ab FROM t;"),
        ("CREATE TABLE a (id INTEGER);", "CREATE TABLE a (id TEXT);"),
    ],
)
def test_what_runs_is_statements(before: str, after: str) -> None:
    assert statements(before) != statements(after)
