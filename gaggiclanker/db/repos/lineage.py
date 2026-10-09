"""Where a draft's version lands: :func:`place_draft` for the live paths, :func:`lineage_owner`
for the one-time list fill.

A put of a draft is a new version of an existing profile, or a new profile. For every live path
(a Set's draft or not, an edit, the agent's change, a profile written from scratch) one function
decides, and the draft constructor, Make active and the page's landing all ask it: **a draft
continues the live profile whose name is its label (case and surrounding whitespace ignored),
and otherwise it is a profile of its own.** A name never changes through a version. The fill
replays stored history through :func:`lineage_owner`, which matches names exactly, as that
history was written.

A name is exactly what the person or the agent gave it; the app adds nothing to it. A change to
an existing profile carries that profile's name (the profile the base belongs to, found through its
version list or the machine file it was mirrored under). A rename, or a profile written from
scratch, must not take a name another live profile has, or its put would silently become a version
of that profile: :func:`taken_name_sentence` is the refusal. :func:`place_draft` is the one
function that decides, and it is asked wherever the answer matters: when a draft is made, when it
is put, and for the landing the Profiles page shows beside the Make active button. A Set's draft
goes to the Set's profile when that is the profile its name names; renamed, it is a profile of its
own and the Set records the change.

Stored drafts from before this carry an ``[AI]`` suffix on some names; they are grouped as they
always were, by the one-time list fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "DraftLookup",
    "LineageLookup",
    "Placement",
    "lineage_owner",
    "name_key",
    "person_taken_sentence",
    "place_draft",
    "taken_name_sentence",
]


def name_key(label: str) -> str:
    """What "the same name" means when a new or renamed draft is checked against the live list:
    the name without surrounding whitespace, case ignored. (Matching a machine file to a profile
    in the sync stays exact; this is only the guard against two profiles a person cannot tell
    apart.)"""
    return label.strip().casefold()


def taken_name_sentence(label: str) -> str:
    """The one refusal for a new or renamed draft whose name is already a live profile."""
    return (
        f"{label} is already a profile: draft a change from it, or choose another name for a "
        "new one."
    )


def person_taken_sentence(existing_label: str) -> str:
    """The refusal a person reads on a card's Name field: the profile's own stored name, not what
    was typed (the typed case may differ), and no advice about drafting. The agent keeps
    :func:`taken_name_sentence`."""
    return f"There is already a profile called {existing_label}. Choose another name."


class LineageLookup[T](Protocol):
    """What the rule asks of whoever holds the profiles. ``T`` is that holder's profile type."""

    def label_of(self, profile: T) -> str:
        """The profile's name."""

    async def by_set(self, set_id: int) -> T | None:
        """The profile the Set's current version stands on, if any."""

    async def by_label(self, label: str) -> T | None:
        """The live profile whose name is exactly this label, if any."""


async def lineage_owner[T](
    lookup: LineageLookup[T], *, set_id: int | None, version_label: str
) -> T | None:
    """The profile a draft's version continues, or ``None`` for a new profile."""
    if set_id is not None:
        found = await lookup.by_set(set_id)
        if found is not None and lookup.label_of(found) == version_label:
            return found
    return await lookup.by_label(version_label)


class DraftLookup[T](LineageLookup[T], Protocol):
    """What :func:`place_draft` asks of whoever holds the live profiles."""

    async def by_name_key(self, label: str) -> T | None:
        """A live profile whose name is this label by :func:`name_key`, if any."""

    async def base_profile(self, version_id: int) -> T | None:
        """The live profile this version belongs to (listed in it, or mapped to its file)."""


@dataclass(frozen=True, slots=True)
class Placement[T]:
    """Where a draft's version lands, and the name it is stored under."""

    #: The live profile the draft continues, or ``None`` (a new profile, or a refused one).
    owner: T | None
    #: A live profile that already has the name of a new or renamed draft: the draft is refused.
    taken: T | None
    #: The label to store: the continued profile's own name, else the name stripped.
    name: str

    @property
    def refused(self) -> bool:
        return self.taken is not None


async def place_draft[T](
    lookup: DraftLookup[T],
    *,
    label: str,
    base_version_id: int | None,
    base_label: str | None,
    set_id: int | None = None,
) -> Placement[T]:
    """The one rule for where a draft goes, used when it is made, when it is put and by the
    landing the page shows.

    ``base_version_id`` and ``base_label`` are ``None`` for a profile written from scratch.

    * A draft **continues** its base's live profile when its label is that profile's name
      (:func:`name_key`). The version's own name decides nothing there: a profile may list a
      version under another name (renamed on the display), and a draft from it that restores the
      profile's name continues the profile; one that keeps the other name is a rename.
    * A base that belongs to no live profile (a superseded draft's version, say) has nothing but
      its own label: a draft that keeps it continues the live profile of that name, if there is
      one, and is not a rename.
    * Anything else is new or renamed. Its name is **taken** when a live profile has it, and
      the draft is refused; otherwise it is a profile of its own.

    A Set's draft goes to the Set's profile when that is the profile its name names.
    """
    key = name_key(label)
    own = None if base_version_id is None else await lookup.base_profile(base_version_id)
    if own is not None:
        continues = key == name_key(lookup.label_of(own))
    else:
        continues = base_label is not None and key == name_key(base_label)
    if not continues:
        return Placement(owner=None, taken=await lookup.by_name_key(label), name=label.strip())
    owner = own
    if set_id is not None:
        theirs = await lookup.by_set(set_id)
        if theirs is not None and name_key(lookup.label_of(theirs)) == key:
            owner = theirs
    if owner is None:
        owner = await lookup.by_name_key(label)
    # A name never changes through a version: whatever profile is continued, its own name is
    # what the draft is stored under. Only a continuation with no live profile behind it (a
    # base outside every list, nothing of that name yet) keeps the base's label.
    stored = lookup.label_of(owner) if owner is not None else (base_label or label.strip())
    return Placement(owner=owner, taken=None, name=stored)
