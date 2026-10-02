"""Where a draft's version lands: the one lineage rule, shared by the live put and the list fill.

A put of a draft is a new version of the profile it descends from, or a new profile. Which one
is decided here, once, over an abstract lookup so the board's put (which asks the live list) and
the boot step that fills the list from stored history (which replays old drafts against the
groups it has built so far) cannot grow two rules:

* a draft made for a **Set** continues the profile the Set's current version stands on;
* a draft designed from scratch (``is_new``) never continues the profile of its stored base,
  whatever the labels say (the base it is stored against is only a diff anchor);
* otherwise, a draft whose label is **exactly** its base's continues the profile that base
  belongs to (found by the base version, else by the file the base was mirrored under);
* in both cases only a profile the app itself made: a profile of the person's stays as it was and
  a draft of it is a profile of its own beside it.

A renamed or forked draft, a draft of a default and a draft with nothing to continue make a new
profile. The label's ``[AI]`` suffix is never stripped: it marks who made a profile and does not
say which profile a version belongs to.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["LineageLookup", "lineage_owner"]


class LineageLookup[T](Protocol):
    """What the rule asks of whoever holds the profiles. ``T`` is that holder's profile type."""

    async def by_set(self, set_id: int) -> T | None:
        """The profile the Set's current version stands on, if any."""

    async def by_version(self, version_id: int) -> T | None:
        """The profile this stored version belongs to, if any."""

    async def by_device(self, device_id: str) -> T | None:
        """The profile that stands on this machine file, if any."""

    def is_app_made(self, profile: T) -> bool:
        """Whether the app itself made the profile (a person's is never continued)."""


async def lineage_owner[T](
    lookup: LineageLookup[T],
    *,
    set_id: int | None,
    base_label: str | None,
    version_label: str,
    is_new: bool = False,
    base_version_id: int,
    base_device_profile_id: str | None,
) -> T | None:
    """The profile a draft's version continues, or ``None`` for a new profile."""
    if set_id is not None:
        found = await lookup.by_set(set_id)
        return found if found is not None and lookup.is_app_made(found) else None
    if is_new or base_label is None or base_label != version_label:
        return None
    found = await lookup.by_version(base_version_id)
    if found is None and base_device_profile_id is not None:
        found = await lookup.by_device(base_device_profile_id)
    return found if found is not None and lookup.is_app_made(found) else None
