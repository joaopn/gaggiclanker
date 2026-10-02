"""Where a draft's version lands: the one lineage rule, shared by the live put and the list fill.

A put of a draft is a new version of the profile it descends from, or a new profile. Which one
is decided here, once, over an abstract lookup so the board's put (which asks the live list) and
the boot step that fills the list from stored history (which replays old drafts against the
groups it has built so far) cannot grow two rules:

* a draft made with **Edit a copy** continues the profile it was opened on (its target); a
  target the app did not make follows the one open rule below;
* any other draft continues the profile whose **version list** has its base version (not only
  the profile that is currently on that version, which an older base no longer is);
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

from gaggiclanker.domain.models import with_app_suffix

__all__ = [
    "EDIT_CONTINUES_ANY_PROFILE",
    "LineageLookup",
    "edit_continues",
    "edit_label",
    "lineage_owner",
]

#: **The one switch for an open question.** An edit made with "Edit a copy" records the profile it
#: was opened on (``target``). When that profile is one the app made, the edit is always a new
#: version of it, whichever version was edited. When it is one the app did not make (the
#: firmware's own, a display-made one, an imported file), what an edit is has not been decided:
#:
#: * ``False`` (today): a separate profile beside it, named with the app's ``[AI]`` suffix; a
#:   second edit finds that copy and is a new version of it.
#: * ``True``: a new version of that very profile, keeping its name (no suffix).
#:
#: Both halves of the rule read this constant, here and nowhere else: the gate in
#: :func:`lineage_owner` and the label in :func:`edit_label`. Flip it and both follow.
EDIT_CONTINUES_ANY_PROFILE = False


def edit_label(label: str) -> str:
    """The label an edited copy is stored under: the app's suffix, unless the rule above says
    an edit is a version of the profile it came from."""
    return label if EDIT_CONTINUES_ANY_PROFILE else with_app_suffix(label)


class LineageLookup[T](Protocol):
    """What the rule asks of whoever holds the profiles. ``T`` is that holder's profile type."""

    async def by_label(self, label: str) -> T | None:
        """The profile with exactly this label, if any."""

    def label_of(self, profile: T) -> str:
        """The profile's label."""

    async def by_id(self, profile_id: int) -> T | None:
        """The profile a draft was explicitly made for, if it is still there."""

    async def by_set(self, set_id: int) -> T | None:
        """The profile the Set's current version stands on, if any."""

    async def by_version(self, version_id: int) -> T | None:
        """The profile this stored version belongs to, if any."""

    async def by_device(self, device_id: str) -> T | None:
        """The profile that stands on this machine file, if any."""

    def is_app_made(self, profile: T) -> bool:
        """Whether the app itself made the profile (a person's is never continued)."""


async def edit_continues[T](lookup: LineageLookup[T], target_id: int) -> T | None:
    """The profile an edit made on ``target_id`` is a new version of, or ``None`` for a new one.

    A profile the app made continues itself, whichever of its versions was edited. For one it did
    not make: itself when the open rule above says so, else the app's copy of it (the profile
    named with the suffix, made by an earlier edit) when there is one, so a second edit is a
    version of the first copy instead of a second profile with a taken name.
    """
    found = await lookup.by_id(target_id)
    if found is None:
        return None
    if EDIT_CONTINUES_ANY_PROFILE or lookup.is_app_made(found):
        return found
    sibling = await lookup.by_label(edit_label(lookup.label_of(found)))
    return sibling if sibling is not None and lookup.is_app_made(sibling) else None


async def lineage_owner[T](
    lookup: LineageLookup[T],
    *,
    set_id: int | None,
    base_label: str | None,
    version_label: str,
    is_new: bool = False,
    base_version_id: int,
    base_device_profile_id: str | None,
    target_id: int | None = None,
) -> T | None:
    """The profile a draft's version continues, or ``None`` for a new profile."""
    if target_id is not None:
        return await edit_continues(lookup, target_id)
    if set_id is not None:
        found = await lookup.by_set(set_id)
        return found if found is not None and lookup.is_app_made(found) else None
    if is_new or base_label is None or base_label != version_label:
        return None
    found = await lookup.by_version(base_version_id)
    if found is None and base_device_profile_id is not None:
        found = await lookup.by_device(base_device_profile_id)
    return found if found is not None and lookup.is_app_made(found) else None
