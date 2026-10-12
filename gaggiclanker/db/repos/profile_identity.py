"""Which profile a profile version belongs to: its owner.

A Set version's name is major.minor, and a major means the Set moved to a
**different profile**, not to a newer version of the same one. "The same
profile" is the version's owner (`profile_versions.board_id`): every version a profile
has had belongs to it, and no version belongs to two profiles, so two profiles that brew the same
thing have two versions (a Duplicate, a pin's copy). One definition, here, for every reader: the
default of the Add a version form, an accepted proposal, and the `profile_entry_id` the web is
served beside each version and each profile option so it can preselect the same box without
comparing anything itself.

* a version that belongs to a profile belongs to that profile;
* a version on no profile (a draft for a new one, an imported file) is its own profile: it is
  the same as itself and as nothing else;
* nothing (a Set naming no profile) is the same as nothing, and different from any profile.

Imports nothing from the other repositories, like `version_names`.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database

__all__ = ["profile_entry_id", "profile_entry_sql", "same_profile"]


def profile_entry_sql(column: str) -> str:
    """The SQL for the profile the version in ``column`` belongs to (NULL for none).

    ``column`` is a column expression of the calling query (`v.profile_version_id`),
    never input.
    """
    return f"(SELECT pv_owner.board_id FROM profile_versions pv_owner WHERE pv_owner.id = {column})"  # noqa: S608 - a column expression, never input


async def profile_entry_id(db: Database, profile_version_id: int | None) -> int | None:
    """The profile a profile version belongs to, or ``None`` when it is on none."""
    if profile_version_id is None:
        return None
    found = await db.fetch_value(
        f"SELECT {profile_entry_sql('?')}",
        (profile_version_id,),
    )
    return None if found is None else int(found)


async def same_profile(db: Database, left: int | None, right: int | None) -> bool:
    """Whether two profile versions (or two "no profile"s) are one profile.

    The same version is the same profile, whether or not it belongs to one;
    different versions are the same profile only when they belong to one profile.
    """
    if left == right:
        return True
    if left is None or right is None:
        return False
    entry = await profile_entry_id(db, left)
    return entry is not None and entry == await profile_entry_id(db, right)
