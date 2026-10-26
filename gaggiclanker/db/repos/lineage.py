"""Where a draft goes: :func:`change_destination` decides it once, :func:`place_draft` answers for
a put, and :func:`lineage_owner` replays history for the one-time list fill.

A draft is either a **change to one profile** or a **new profile** with a name of its own, and
which it is is decided once, when the draft is made, and stored on the draft
(``destination_board_id``, ``label``). A change goes to the profile its base belongs to (the
profile that lists the base version, else the one standing on a machine file that holds it); it
always carries that profile's own name. A name never places a draft: the document's label is
rebuilt from the destination's name, so there is nothing to compare. A new profile is refused
when a live profile already has its name (:func:`taken_name_sentence`): the only name check
there is.

A change whose base belongs to no live profile (a superseded draft's version, a profile that has
been deleted) becomes a new profile under the base's name, or a change to the live profile that
has that name. :func:`place_draft` is what the draft constructor, the name check, the page's
landing and the put all ask, so they cannot disagree; the destination it is given is the stored
one, never worked out again. A profile that is a pin's frozen copy is changed only by unpinning
it, so a draft never goes there.

A name is exactly what the person or the agent gave it, stripped; the app adds nothing to it.

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
    "change_destination",
    "destination_now",
    "free_name",
    "lineage_owner",
    "machine_name_sentence",
    "name_key",
    "person_taken_sentence",
    "pinned_copy_agent_sentence",
    "pinned_copy_sentence",
    "pinned_sentence",
    "place_draft",
    "taken_name_sentence",
]


def name_key(label: str) -> str:
    """What "the same name" means when a new or renamed draft is checked against the live list:
    the name without surrounding whitespace, case ignored. (Matching a machine file to a profile
    in the sync stays exact; this is only the guard against two profiles a person cannot tell
    apart.)"""
    return label.strip().casefold()


def free_name(label: str, taken: set[str]) -> str:
    """The name a profile joins the list under: the file's own, or "<name> (2)" and the next free
    number when a live profile already has it (``taken`` holds the live profiles'
    :func:`name_key` of each). The result's key is added to ``taken``, so a run of files joining one
    after another each get a name of their own."""
    chosen, number = label, 1
    while name_key(chosen) in taken:
        number += 1
        chosen = f"{label.strip()} ({number})"
    taken.add(name_key(chosen))
    return chosen


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


def machine_name_sentence(machine_name: str, holder_label: str) -> str:
    """The refusal for a new or renamed profile whose name is what another live profile is called
    on the machine (its own name and the version of its Recipe)."""
    return f"{machine_name} is what {holder_label} is called on the machine. Choose another name."


def pinned_sentence(profile_name: str, recipe_name: str, version_label: str) -> str:
    """The refusal for a change to a profile that is the frozen copy of a pinned Recipe version."""
    return f"{profile_name} is pinned to {recipe_name} {version_label}: unpin it to change it."


def pinned_copy_sentence(name: str, recipe_name: str, version_label: str) -> str:
    """What a person is told when a name is the copy of a pin (live or since unpinned): the copy
    belongs to that Recipe version for ever, so the name is not free even when no profile has it."""
    return (
        f"{name} is the name of the copy pinned from {recipe_name} {version_label}. "
        "Choose another name."
    )


def pinned_copy_agent_sentence(name: str, recipe_name: str, version_label: str) -> str:
    """The same refusal in the agent's wording (as :func:`taken_name_sentence` is the agent's)."""
    return (
        f"{name} is the name of the copy pinned from {recipe_name} {version_label}. Choose another "
        "name for a new profile."
    )


class LineageLookup[T](Protocol):
    """What the rule asks of whoever holds the profiles. ``T`` is that holder's profile type."""

    def label_of(self, profile: T) -> str:
        """The profile's name."""

    async def by_recipe(self, recipe_id: int) -> T | None:
        """The profile the Recipe's current version stands on, if any."""

    async def by_label(self, label: str) -> T | None:
        """The live profile whose name is exactly this label, if any."""


async def lineage_owner[T](
    lookup: LineageLookup[T], *, recipe_id: int | None, version_label: str
) -> T | None:
    """The profile a draft's version continues, or ``None`` for a new profile."""
    if recipe_id is not None:
        found = await lookup.by_recipe(recipe_id)
        if found is not None and lookup.label_of(found) == version_label:
            return found
    return await lookup.by_label(version_label)


class DraftLookup[T](Protocol):
    """What the placement functions ask of whoever holds the live profiles."""

    def label_of(self, profile: T) -> str:
        """The profile's name."""

    async def by_name_key(self, label: str) -> T | None:
        """A live profile whose name is this label by :func:`name_key`, if any."""

    async def base_profile(self, version_id: int) -> T | None:
        """The live profile this version belongs to (listed in it, or mapped to its file)."""

    async def frozen_sentence(self, profile: T) -> str | None:
        """Why this profile may not be continued (a pin's frozen copy), else ``None``."""


@dataclass(frozen=True, slots=True)
class Placement[T]:
    """What a put of a draft does, and the name it is stored under."""

    #: The live profile the draft is a change to, or ``None`` (a new profile, or a refused one).
    owner: T | None
    #: A live profile that already has the name of a new profile: the draft is refused.
    taken: T | None
    #: The name to store: the destination's own name, else the new profile's name, stripped.
    name: str
    #: Why a draft that is a change to a profile is refused: that profile is the frozen copy of a
    #: pinned Recipe version (the person's sentence), else ``None``. A pinned profile is changed
    #: only by unpinning it, so a draft never goes there, exactly as it never takes a taken name.
    frozen: str | None = None

    @property
    def refused(self) -> bool:
        return self.taken is not None or self.frozen is not None


async def change_destination[T](
    lookup: DraftLookup[T],
    *,
    base_version_id: int,
    base_label: str | None,
) -> T | None:
    """The live profile a change to the base's profile is a change to; ``None`` when no live
    profile has the base.

    * The base's own profile (:meth:`DraftLookup.base_profile`).
    * A base that belongs to no live profile has only its own label: the live profile with that
      name is the destination. With none, the change is a new profile.
    """
    own = await lookup.base_profile(base_version_id)
    if own is not None:
        return own
    if base_label is None:
        return None
    return await lookup.by_name_key(base_label)


async def destination_now[T](
    lookup: DraftLookup[T],
    *,
    new_profile: bool,
    stored: T | None,
    base_version_id: int | None,
    base_label: str | None,
) -> T | None:
    """Where a draft goes **now**: the one answer the constructor, the landing, the standing and
    the put all use.

    A new profile goes nowhere (it is a row of its own). A change goes to the profile stored on
    it while that profile is live; with none stored, or once that profile is gone, to whichever
    live profile its base belongs to when it is read (:func:`change_destination`), so a change
    drafted before its profile was on the list is a version of it when the list has it.
    """
    if new_profile:
        return None
    if stored is not None:
        return stored
    if base_version_id is None:
        return None
    return await change_destination(lookup, base_version_id=base_version_id, base_label=base_label)


async def place_draft[T](
    lookup: DraftLookup[T],
    *,
    label: str,
    destination: T | None,
    base_version_id: int | None = None,
) -> Placement[T]:
    """What a put of a draft does, from the draft's stored destination and name: the one rule the
    constructor, the name check, the page's landing and the put all ask.

    * a **destination** (a live profile): the draft is a change to it, stored under its own name,
      unless it is a pin's frozen copy (:attr:`Placement.frozen`);
    * **none**: a new profile under ``label``, refused when a live profile has that name
      (:attr:`Placement.taken`). When that profile is a pin's frozen copy that the draft comes
      from (the profile its base belongs to: ``base_version_id``), the refusal says the profile
      is pinned, as it would for a change to it.
    """
    if destination is None:
        taken = await lookup.by_name_key(label)
        frozen = None
        if taken is not None:
            frozen = await _frozen_origin(lookup, taken, base_version_id)
        return Placement(owner=None, taken=taken, name=label.strip(), frozen=frozen)
    stored = lookup.label_of(destination)
    frozen = await lookup.frozen_sentence(destination)
    if frozen is not None:
        return Placement(owner=None, taken=None, name=stored, frozen=frozen)
    return Placement(owner=destination, taken=None, name=stored)


async def _frozen_origin[T](
    lookup: DraftLookup[T], taken: T, base_version_id: int | None
) -> str | None:
    """Why the profile that has a new profile's name may not be changed, when the draft comes from
    it: the sentence about its pin, else ``None``."""
    sentence = await lookup.frozen_sentence(taken)
    if sentence is None:
        return None
    if base_version_id is not None and await lookup.base_profile(base_version_id) == taken:
        return sentence
    return None
