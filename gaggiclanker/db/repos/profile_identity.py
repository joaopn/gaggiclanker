"""Which profile a profile version belongs to: its entry in the profile list.

A Set version's name is major.minor, and a major means the Set moved to a
**different profile**, not to a newer version of the same one. "The same
profile" is the profile list's own answer (`profile_board` ↔
`profile_board_versions`): every version a list entry has had is that entry's
profile. One definition, here, for every reader: the default of the Add a
version form, an accepted proposal, and the `profile_entry_id` the web is served
beside each version and each profile option so it can preselect the same box
without comparing anything itself.

* a version in a list entry belongs to that entry (the live one when two entries
  hold the same content, else the lowest id: a stable pick, never a guess that
  changes between reads);
* a version in no entry is its own profile: it is the same as itself and as
  nothing else;
* nothing (a Set naming no profile) is the same as nothing, and different from
  any profile.

Imports nothing from the other repositories, like `version_names`.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database

__all__ = ["profile_entry_id", "profile_entry_sql", "same_profile"]


def profile_entry_sql(column: str) -> str:
    """The SQL for the list entry of the profile version in ``column`` (NULL for none).

    ``column`` is a column expression of the calling query (`v.profile_version_id`),
    never input.
    """
    return (
        "(SELECT pbv.board_id FROM profile_board_versions pbv "  # noqa: S608 - a column expression, never input
        "JOIN profile_board pb ON pb.id = pbv.board_id "
        f"WHERE pbv.version_id = {column} "
        "ORDER BY (pb.deleted_at IS NOT NULL), pbv.board_id LIMIT 1)"
    )


async def profile_entry_id(db: Database, profile_version_id: int | None) -> int | None:
    """The list entry a profile version belongs to, or ``None`` when it is in none."""
    if profile_version_id is None:
        return None
    found = await db.fetch_value(
        f"SELECT {profile_entry_sql('?')}",
        (profile_version_id,),
    )
    return None if found is None else int(found)


async def same_profile(db: Database, left: int | None, right: int | None) -> bool:
    """Whether two profile versions (or two "no profile"s) are one profile.

    The same version is the same profile, whether or not a list entry holds it;
    different versions are the same profile only when they belong to one entry.
    """
    if left == right:
        return True
    if left is None or right is None:
        return False
    entry = await profile_entry_id(db, left)
    return entry is not None and entry == await profile_entry_id(db, right)
