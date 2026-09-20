"""The SQL sandbox. The one tool that runs text a language model wrote.

Every test here is a refusal except the ones that prove a legitimate query still
works, and that ratio is the point: the interesting behaviour of this module is
what it will not do.
"""

from __future__ import annotations

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.tools.sql import (
    ALLOWED_VIEWS,
    MAX_ROW_LIMIT,
    SqlRefused,
    run_query,
    validate_sql,
)
from tests.analyzer.conftest import Fixture

# -- the textual pre-check -------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO shots (id) VALUES (1)",
        "UPDATE shots SET quarantined = 1",
        "DELETE FROM shots",
        "DROP TABLE shots",
        "PRAGMA table_info(shots)",
        "ATTACH DATABASE '/tmp/evil.db' AS evil",
        "VACUUM",
        "CREATE TABLE x (a INT)",
    ],
)
def test_anything_that_is_not_a_read_is_refused(statement: str) -> None:
    with pytest.raises(SqlRefused):
        validate_sql(statement)


def test_two_statements_are_refused() -> None:
    with pytest.raises(SqlRefused, match="one statement"):
        validate_sql("SELECT 1; DELETE FROM shots")


def test_a_trailing_semicolon_is_fine() -> None:
    """A model that ends its query the way a person would is not a threat."""
    assert validate_sql("SELECT 1;") == "SELECT 1"


def test_a_comment_cannot_smuggle_a_second_statement() -> None:
    """`SELECT 1 -- ; DROP` is one statement, and the check has to agree."""
    assert validate_sql("SELECT 1 -- ; DROP TABLE shots").startswith("SELECT 1")


def test_a_semicolon_inside_a_string_is_not_a_separator() -> None:
    assert validate_sql("SELECT 'a;b' AS x") == "SELECT 'a;b' AS x"


def test_a_with_clause_is_allowed() -> None:
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_an_empty_query_is_refused() -> None:
    with pytest.raises(SqlRefused, match="empty"):
        validate_sql("   ")


# -- the authorizer --------------------------------------------------------


async def test_a_real_query_over_the_views_works(archive: Fixture) -> None:
    result = await run_query(
        archive.db.path,
        "SELECT shot_id, execution_score FROM v_shots ORDER BY shot_id",
    )

    assert result.columns == ["shot_id", "execution_score"]
    assert [row[0] for row in result.rows] == archive.shots


async def test_an_aggregate_across_views_works(archive: Fixture) -> None:
    result = await run_query(
        archive.db.path,
        "SELECT v.set_version_no, COUNT(*) AS n FROM v_shots v GROUP BY v.set_version_no",
    )

    assert result.columns == ["set_version_no", "n"]
    assert result.rows


async def test_a_base_table_is_refused_even_though_the_view_reads_it(
    archive: Fixture,
) -> None:
    """The whole point of the views: `shots` is readable through `v_shots` and not directly."""
    with pytest.raises(SqlRefused) as caught:
        await run_query(archive.db.path, "SELECT id FROM shots")

    assert "v_shots" in str(caught.value)


async def test_the_settings_table_is_invisible(archive: Fixture) -> None:
    """It holds the password hash. A model must not be able to ask for it."""
    with pytest.raises(SqlRefused):
        await run_query(archive.db.path, "SELECT key, value FROM settings")


async def test_sqlite_master_is_invisible(archive: Fixture) -> None:
    with pytest.raises(SqlRefused):
        await run_query(archive.db.path, "SELECT name FROM sqlite_master")


#: The tables that hold something a model must never see: the password hash and
#: the signing secret, the live sessions, the rendered prompts and raw replies,
#: and the audit of everything ever written to the machine.
SECRET_TABLES = ("settings", "runtime_secrets", "auth_sessions", "llm_calls", "device_writes")


@pytest.mark.parametrize("table", SECRET_TABLES)
@pytest.mark.parametrize(
    "shape",
    [
        "SELECT COUNT(*) FROM {table}",
        "SELECT COUNT(*) FROM main.{table}",
        "SELECT COUNT(*) FROM {table} AS t",
        "SELECT EXISTS(SELECT 1 FROM {table})",
    ],
)
async def test_a_column_less_read_of_a_secret_table_is_refused(
    archive: Fixture, table: str, shape: str
) -> None:
    """The leak the first cut had, in every form that reaches it.

    SQLite reports `SELECT COUNT(*) FROM t` as a read with an empty column name
    **and no database name** — the same shape a CTE arrives in. Treating "no
    database name" as "a CTE" let the row count of the password table out; the
    authorizer now resolves the name against the file's real relations.
    """
    with pytest.raises(SqlRefused):
        await run_query(archive.db.path, shape.format(table=table))


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT COUNT(*) FROM v_shots",
        "SELECT EXISTS(SELECT 1 FROM v_sets)",
        "WITH recent AS (SELECT shot_id FROM v_shots) SELECT COUNT(*) FROM recent",
    ],
)
async def test_the_same_shapes_still_work_over_the_views(archive: Fixture, statement: str) -> None:
    """The counterpart: the fix must not refuse the queries it exists to allow."""
    assert (await run_query(archive.db.path, statement)).rows


async def test_json_each_can_unpack_a_json_column(archive: Fixture) -> None:
    """`diagnostics_json` is a document, and unpacking it is a table-valued function.

    `json_each` arrives as a read of a name that is not a relation in this file,
    which is the same door a CTE comes through — so the rule that closed the
    row-count leak has to leave it open.
    """
    result = await run_query(
        archive.db.path,
        "SELECT s.shot_id, d.key FROM v_shots s, json_each(s.diagnostics_json) d LIMIT 5",
    )

    assert result.columns == ["shot_id", "key"]
    assert result.rows


async def test_a_gigabyte_allocation_fails_fast(archive: Fixture) -> None:
    """Neither the row cap nor the deadline helps: the blob is built for row one."""
    import time

    started = time.monotonic()
    with pytest.raises(SqlRefused, match="too big"):
        await run_query(archive.db.path, "SELECT randomblob(1000000000)")

    assert time.monotonic() - started < 1.0


async def test_every_allowed_view_actually_exists(archive: Fixture) -> None:
    """The allow-list and migration 0013 have to name the same things."""
    for view in sorted(ALLOWED_VIEWS):
        result = await run_query(archive.db.path, f"SELECT * FROM {view} LIMIT 1")  # noqa: S608
        assert result.columns


async def test_the_version_view_carries_the_experiment_log(archive: Fixture) -> None:
    """The chat reads the ledger through this view, so the columns are API.

    Without the prediction and the outcome here, a model asked "which of my
    guesses held" would answer from the intent field, which is what you were
    trying rather than what you expected.
    """
    result = await run_query(archive.db.path, "SELECT * FROM v_set_versions LIMIT 1")

    assert {
        "prediction",
        "compares_to_version_no",
        "restores_version_no",
        "outcome",
        "outcome_note",
    } <= set(result.columns)


# -- the bounds ------------------------------------------------------------


async def test_the_row_cap_is_enforced_and_flagged(archive: Fixture) -> None:
    result = await run_query(archive.db.path, "SELECT * FROM v_samples", limit=5)

    assert len(result.rows) == 5
    assert result.truncated


async def test_a_limit_above_the_ceiling_is_clamped(archive: Fixture) -> None:
    result = await run_query(archive.db.path, "SELECT * FROM v_samples", limit=10_000)

    assert len(result.rows) <= MAX_ROW_LIMIT


async def test_a_two_second_recursive_cte_is_cut_off(archive: Fixture) -> None:
    """ "It is only a SELECT" says nothing: a recursive CTE is a loop.

    Ten million rows of counting is several seconds of CPU; the progress
    handler has to stop it, and the message has to tell the model what to do
    instead.
    """
    spin = (
        "WITH RECURSIVE spin(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM spin WHERE n < 100000000)"
        " SELECT COUNT(*) FROM spin"
    )

    with pytest.raises(SqlRefused) as caught:
        await run_query(archive.db.path, spin, timeout_s=0.3)

    assert "stopped" in str(caught.value)


async def test_a_blob_column_never_reaches_the_model(archive: Fixture) -> None:
    """`raw_slog` is not in any view, and a bytes cell would be rendered as a size."""
    result = await run_query(archive.db.path, "SELECT * FROM v_shots LIMIT 1")
    assert "raw_slog" not in result.columns


async def test_the_connection_is_query_only(archive: Fixture, tool_db: Database) -> None:
    """Belt and braces behind the authorizer: even a permitted read cannot write."""
    with pytest.raises(SqlRefused):
        await run_query(archive.db.path, "UPDATE v_shots SET rating = 5")
