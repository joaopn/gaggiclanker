"""What the next version of a Set is called, and how a version's name is read in SQL.

A Set version has a name (`version_major`.`version_minor`, "v1.1") and nothing
else of the kind: the name is an identifier, like a tag, not a position. What
order the versions came in is `created_at`, and which one the Set is on is the
Set's `current_version_id`. The name is assigned when the row is inserted and
shown before that on every card and form that says what pressing its button
will record ("Accept as v1.1"). Both come from :func:`next_numbers` here, so
the number a button promises and the number the insert writes are one query.
A minor continues the **current** version's major, so after a revert to v1.2 it
is v1.3 (the next free minor of major 1), whatever else exists.

This module imports nothing from the other repositories: the Sets repository
writes versions, and the drafts and proposals repositories — which the Sets
repository itself imports — read the same numbers for their cards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from gaggiclanker.db.connection import Database
from gaggiclanker.domain.sets import next_version_name, version_label

__all__ = [
    "NextNames",
    "NextNumbers",
    "label_sql",
    "named_dump",
    "names_from_state",
    "next_names",
    "next_numbers",
    "state_sql",
]


def named_dump(row: BaseModel) -> dict[str, Any]:
    """A row as the agent is handed it: its versions by name (there is no ordinal)."""
    dumped: dict[str, Any] = row.model_dump(mode="json")
    return dumped


def label_sql(alias: str) -> str:
    """The SQL for a version's label, off the `set_versions` row called ``alias``.

    `domain/sets.py::version_label` in SQL: 'v' and the major, plus '.' and the
    minor when the minor is above 0. NULL when the row is NULL (a LEFT JOIN
    that found nothing), because 'v' || NULL is NULL — "compared to nothing"
    stays nothing. The views in migration 0027 repeat this text, and a test
    pins both to the Python rule.
    """
    return (
        f"('v' || {alias}.version_major || CASE WHEN {alias}.version_minor > 0 "
        f"THEN '.' || {alias}.version_minor ELSE '' END)"
    )


@dataclass(frozen=True, slots=True)
class NextNumbers:
    """The name the next version of a Set takes."""

    major: int
    minor: int

    @property
    def label(self) -> str:
        return version_label(self.major, self.minor)


@dataclass(frozen=True, slots=True)
class NextNames:
    """What the next version would be called either way, for a button to say so."""

    minor: str
    major: str


def state_sql(current: str) -> str:
    """What numbering the next version needs, off the current version called ``current``.

    Three columns: the current version's major, the highest minor within that
    major, and the Set's highest major. One text for the insert's own read
    (:func:`next_numbers`) and for the Sets list, which serves every Set's next
    names in its one query, so what a button promises and what the insert
    writes are the same arithmetic on the same columns.
    """
    return f"""
           {current}.version_major AS current_major,
           (SELECT MAX(m.version_minor) FROM set_versions m
             WHERE m.set_id = {current}.set_id AND m.version_major = {current}.version_major)
               AS current_major_minor_max,
           (SELECT MAX(h.version_major) FROM set_versions h
             WHERE h.set_id = {current}.set_id) AS highest_major"""  # noqa: S608 - an alias, never input


def names_from_state(
    *,
    designing: bool,
    current_major: int | None,
    current_major_minor_max: int | None,
    highest_major: int | None,
) -> NextNames:
    """What the next version would be called as a minor and as a major.

    A Set being designed has an empty version 1 that the next write fills in
    place, so both answers are v1 there.
    """
    if designing:
        return NextNames(minor=version_label(1, 0), major=version_label(1, 0))
    state = {
        "current_major": current_major or 0,
        "current_major_minor_max": current_major_minor_max or 0,
        "highest_major": highest_major or 0,
    }
    return NextNames(
        minor=version_label(*next_version_name(**state, major=False)),
        major=version_label(*next_version_name(**state, major=True)),
    )


async def _state(db: Database, set_id: int) -> tuple[int, int, int]:
    """(current major, highest minor in it, highest major), zeros for none.

    Off the Set's pointer, so a Set that went back to v1.2 numbers from v1.2.
    """
    row = await db.fetch_one(
        f"""
        SELECT {state_sql("cur")}
          FROM sets s
          JOIN set_versions cur ON cur.id = s.current_version_id
         WHERE s.id = ?
        """,  # noqa: S608 - the interpolation is the module's own SQL text, the id is bound
        (set_id,),
    )
    if row is None:
        return 0, 0, 0
    return (
        int(row["current_major"]),
        int(row["current_major_minor_max"] or 0),
        int(row["highest_major"] or 0),
    )


async def next_numbers(db: Database, set_id: int, *, major: bool) -> NextNumbers:
    """The name of the version about to be inserted.

    Read inside the inserting transaction by the caller, so nothing can land
    between this read and the insert; the unique index on the name is the
    backstop if something ever did.
    """
    current_major, minor_max, major_max = await _state(db, set_id)
    name = next_version_name(
        current_major=current_major,
        current_major_minor_max=minor_max,
        highest_major=major_max,
        major=major,
    )
    return NextNumbers(major=name[0], minor=name[1])


async def next_names(db: Database, set_id: int) -> NextNames:
    """What the next version of one Set would be called as a minor and as a major."""
    designing = await db.fetch_value("SELECT designing FROM sets WHERE id = ?", (set_id,))
    current_major, minor_max, major_max = await _state(db, set_id)
    return names_from_state(
        designing=bool(designing),
        current_major=current_major,
        current_major_minor_max=minor_max,
        highest_major=major_max,
    )
