"""What the next version of a Set is called, and how a version's name is read in SQL.

A Set version has an ordinal (`version_no`, "the Nth version", which orders
them) and a name (`version_major`.`version_minor`, "v1.1", which is what a
person and the agent read). The name is assigned when the row is inserted and
shown before that on every card and form that says what pressing its button
will record ("Accept as v1.1"). Both come from :func:`next_numbers` here, so
the number a button promises and the number the insert writes are one query.

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
    "ORDINAL_FIELDS",
    "NextNames",
    "NextNumbers",
    "label_sql",
    "named_dump",
    "next_names",
    "next_numbers",
]

#: The ordinals a row carries beside each version's name. A model handed both
#: "version_no": 3 and "version_label": "v1.2" says "v3" sooner or later, and a
#: person reading that looks for a version that does not exist. What the agent
#: is handed keeps the ids, for anything that refers to a version, and the
#: names; the ordinal only ever ordered the rows, which come in order anyway.
ORDINAL_FIELDS = frozenset(
    {"version_no", "current_version_no", "compares_to_version_no", "restores_version_no"}
)


def named_dump(row: BaseModel) -> dict[str, Any]:
    """A row as the agent is handed it: its versions by name, never by ordinal."""
    dumped: dict[str, Any] = row.model_dump(mode="json")
    return {key: value for key, value in dumped.items() if key not in ORDINAL_FIELDS}


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
    """The ordinal and the name the next version of a Set takes."""

    version_no: int
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


async def _state(db: Database, set_id: int) -> tuple[int, int, int, int]:
    """(highest ordinal, current major, highest minor in it, highest major), zeros for none."""
    row = await db.fetch_one(
        """
        SELECT cur.version_no,
               cur.version_major,
               (SELECT MAX(m.version_minor) FROM set_versions m
                 WHERE m.set_id = cur.set_id AND m.version_major = cur.version_major)
                   AS minor_max,
               (SELECT MAX(h.version_major) FROM set_versions h
                 WHERE h.set_id = cur.set_id) AS major_max
          FROM set_versions cur
         WHERE cur.set_id = ?
         ORDER BY cur.version_no DESC
         LIMIT 1
        """,
        (set_id,),
    )
    if row is None:
        return 0, 0, 0, 0
    return (
        int(row["version_no"]),
        int(row["version_major"]),
        int(row["minor_max"] or 0),
        int(row["major_max"] or 0),
    )


async def next_numbers(db: Database, set_id: int, *, major: bool) -> NextNumbers:
    """The ordinal and name of the version about to be inserted.

    Read inside the inserting transaction by the caller, so nothing can land
    between this read and the insert; the unique index on the name is the
    backstop if something ever did. The current version is the highest
    ordinal, exactly as everywhere else.
    """
    ordinal, current_major, minor_max, major_max = await _state(db, set_id)
    name = next_version_name(
        current_major=current_major,
        current_major_minor_max=minor_max,
        highest_major=major_max,
        major=major,
    )
    return NextNumbers(version_no=ordinal + 1, major=name[0], minor=name[1])


async def next_names(db: Database, set_id: int) -> NextNames:
    """What the next version would be called as a minor and as a major.

    A Set being designed has an empty version 1 that the next write fills in
    place, so both answers are v1 there.
    """
    designing = await db.fetch_value("SELECT designing FROM sets WHERE id = ?", (set_id,))
    if designing:
        return NextNames(minor=version_label(1, 0), major=version_label(1, 0))
    as_minor = await next_numbers(db, set_id, major=False)
    as_major = await next_numbers(db, set_id, major=True)
    return NextNames(minor=as_minor.label, major=as_major.label)
