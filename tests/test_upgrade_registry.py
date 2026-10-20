"""The upgrade steps' registry, their frozen schemas, and the rule that steps are frozen code."""

from __future__ import annotations

import ast
import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from gaggiclanker.db import schema
from gaggiclanker.db import upgrades as upgrades_package
from gaggiclanker.db.upgrades import (
    LATEST,
    SCHEMAS_DIR,
    STEPS,
    Step,
    frozen_schema_sql,
    validate_steps,
)

PACKAGE = "gaggiclanker.db.upgrades"

#: One entry per frozen schema, written when a step first moves past the level. Editing a
#: frozen file changes its checksum and fails here; add an entry for a new file, never
#: change one.
FROZEN_CHECKSUMS = {
    0: "4b718f3fa512a6b5575c54325fcbf8ff388787a9cf88cdd2eee5ea25e18b79aa",
    1: "6a38b720f0ef88994909847769a071d6d15c76edf7ec06e2b7c36711da18682d",
    2: "96fa548a8df15905172da557db1decceedab5e5ad6f94472403d999e7f05cc5f",
    3: "14aefa9be3bf6b7c2e432456b5f04d1c1fce1535f5d4925417951b2fc130e4a9",
}


def _step(level: int) -> Step:
    return Step(level=level, expects="x", foreign_keys_off=False, run=lambda conn: {})


def _built(sql: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    return conn


# ── 2: the registry ──────────────────────────────────────────────────────


def test_levels_must_be_exactly_one_to_n_in_order() -> None:
    assert validate_steps([]) == ()
    assert [s.level for s in validate_steps([_step(1), _step(2), _step(3)])] == [1, 2, 3]
    for bad in ([_step(0)], [_step(2)], [_step(1), _step(3)], [_step(2), _step(1)]):
        with pytest.raises(ValueError, match="levels 1"):
            validate_steps(bad)
    with pytest.raises(ValueError, match="levels 1"):
        validate_steps([_step(1), _step(1)])


def test_the_registry_that_ships_is_valid_and_sets_the_latest_level() -> None:
    assert validate_steps(STEPS) == STEPS
    assert LATEST == len(STEPS)


def test_the_schema_file_ends_at_the_latest_level() -> None:
    """``schema.sql``'s level is the number of registered steps, whatever is added."""
    match = re.search(r"^PRAGMA user_version = (\d+);$", schema.schema_sql(), re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) == LATEST
    assert schema.describe_sql(schema.schema_sql())["user_version"] == str(LATEST)


# ── 4: the frozen schemas ────────────────────────────────────────────────


def test_there_is_a_frozen_schema_for_every_level_and_no_other_file() -> None:
    names = sorted(p.name for p in SCHEMAS_DIR.iterdir())
    assert names == [f"level_{n:03d}.sql" for n in range(len(names))]
    # Level 0 is always there; one more per step that has moved past a level.
    assert len(names) >= LATEST


def test_every_frozen_schema_is_pinned_by_checksum() -> None:
    found = {
        int(p.stem.removeprefix("level_")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SCHEMAS_DIR.iterdir()
    }
    assert found == FROZEN_CHECKSUMS


def test_a_frozen_schema_builds_a_database_at_its_own_level() -> None:
    for level in FROZEN_CHECKSUMS:
        conn = _built(frozen_schema_sql(level))
        try:
            assert schema.describe(conn)["user_version"] == str(level), level
        finally:
            conn.close()


def test_each_frozen_schema_builds_the_database_the_next_step_expects() -> None:
    """Level N's file, built and fingerprinted, is what step N+1 says it upgrades from."""
    for step in STEPS:
        built = schema.describe_sql(frozen_schema_sql(step.level - 1))
        assert schema.fingerprint_of(built) == step.expects, step.level


def test_the_frozen_schema_at_the_latest_level_is_the_schema_file() -> None:
    """The latest level's frozen file, once the next step has frozen it, is ``schema.sql``."""
    latest = SCHEMAS_DIR / f"level_{LATEST:03d}.sql"
    if not latest.exists():  # a step has moved past it; the file is committed with the next one
        pytest.skip("the latest level has no frozen file yet")
    conn = _built(latest.read_text(encoding="utf-8"))
    try:
        assert schema.schema_differences(conn) == []
    finally:
        conn.close()


# ── 3: steps are frozen code ─────────────────────────────────────────────


def _dotted(node: ast.AST) -> str | None:
    """``a.b.c`` for a chain of attribute accesses on a name; ``None`` for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def _inside(name: str) -> bool:
    return name == PACKAGE or name.startswith(f"{PACKAGE}.")


def _ours(name: str) -> bool:
    return name == "gaggiclanker" or name.startswith("gaggiclanker.")


#: Names a step has no use for: each is a way to reach the application, or to hide that it does.
_DYNAMIC = frozenset({"importlib", "exec", "eval", "__import__", "import_module"})


def foreign_imports(source: str, package: str) -> list[str]:
    """What ``source`` (a module of the package named ``package``) reaches outside this package.

    Imports of the application's own modules (relative ones resolved), any attribute access
    through ``gaggiclanker.`` beyond ``gaggiclanker.db.upgrades``, and the means of importing
    or running code by name (``importlib``, ``__import__``, ``exec``, ``eval``,
    ``sys.modules``): an accidental import is the threat, not a determined bypass.
    """
    tree = ast.parse(source)
    found: list[str] = []
    inner = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if _ours(a.name) and not _inside(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if _ours(target) and not _inside(target):
                found.append(target)
            if target == "importlib":
                found.append(target)
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and id(node) not in inner:
            dotted = _dotted(node)
            if dotted == "sys.modules" or (
                dotted
                and (dotted.split(".")[-1] in _DYNAMIC or (_ours(dotted) and not _inside(dotted)))
            ):
                found.append(dotted)
    return found


_TRANSACTION_SQL = re.compile(
    r"^\s*(BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE)\b|user_version", re.IGNORECASE
)


def transaction_control(source: str) -> list[str]:
    """Where a step ends the upgrade's transaction or moves the level, which only the runner may.

    ``executescript`` commits first; ``commit``/``rollback`` and the SQL statements do the same
    thing by name; ``user_version`` is set by the runner after the step, never by the step.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"executescript", "commit", "rollback"}:
                found.append(f"{node.func.attr}(...)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _TRANSACTION_SQL.search(node.value):
                found.append(node.value.strip()[:40])
    return found


def test_the_import_walk_finds_what_a_step_must_not_import() -> None:

    def walk(source: str) -> list[str]:
        return foreign_imports(source, PACKAGE)

    assert walk("from gaggiclanker.db.repos.profiles import X")
    assert walk("import gaggiclanker.domain.profile")
    assert walk("from gaggiclanker import settings")
    assert walk("from ..schema import describe")
    assert walk("from ... import domain")
    assert walk("import importlib\nimportlib.import_module('x')")
    assert walk("from importlib import import_module")
    assert walk("import sys\nsys.modules['x']")
    assert walk("exec('x')") and walk("eval('x')") and walk("__import__('x')")
    assert walk("def f():\n    from gaggiclanker.db.schema import describe")
    # An attribute reached through the package name without an import of its own.
    assert walk("import gaggiclanker.db.upgrades.step\ngaggiclanker.db.schema.describe()")
    # What a step may import: the standard library, and its own package.
    assert not walk("import sqlite3, json\nfrom pathlib import Path")
    assert not walk("from gaggiclanker.db.upgrades.step import Step")
    assert not walk("from .step import Step\nfrom . import step")
    assert not walk("import gaggiclanker.db.upgrades.step\ngaggiclanker.db.upgrades.step.Step")
    # A subpackage resolves its relative imports from its own package.
    assert foreign_imports("from .. import step", f"{PACKAGE}.sub") == []
    assert foreign_imports("from ... import db", f"{PACKAGE}.sub")


def _modules() -> list[Path]:
    root = Path(upgrades_package.__file__).parent
    return sorted(root.rglob("*.py"))


def _package_of(path: Path) -> str:
    root = Path(upgrades_package.__file__).parent
    # A module's package is its directory, and an ``__init__`` is its directory's own module.
    parts = path.relative_to(root).parts[:-1]
    return ".".join([PACKAGE, *parts])


def test_no_module_in_the_upgrades_package_reaches_the_application() -> None:
    modules = _modules()
    assert {m.name for m in modules} >= {"__init__.py", "step.py"}
    for path in modules:
        found = foreign_imports(path.read_text(encoding="utf-8"), _package_of(path))
        assert found == [], path.name


def test_the_transaction_control_walk_finds_what_a_step_must_not_do() -> None:
    assert transaction_control("conn.executescript('x')")
    assert transaction_control("conn.commit()") and transaction_control("conn.rollback()")
    assert transaction_control("conn.execute('COMMIT')")
    assert transaction_control("conn.execute('  begin immediate')")
    assert transaction_control("conn.execute('END')")
    assert transaction_control("conn.execute('SAVEPOINT a')")
    assert transaction_control("conn.execute('PRAGMA user_version = 3')")
    assert not transaction_control(
        "conn.execute('UPDATE t SET committed = 1')\n"
        "conn.execute('INSERT INTO beginnings VALUES (1)')"
    )


def test_no_step_ends_the_transaction_or_sets_the_level() -> None:
    """Every ``level_*.py``: the runner owns the transaction and the level."""
    for path in (m for m in _modules() if m.name.startswith("level_")):
        assert transaction_control(path.read_text(encoding="utf-8")) == [], path.name
