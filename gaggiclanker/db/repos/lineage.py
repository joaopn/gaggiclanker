"""Where a draft's version lands: the one lineage rule, shared by the live put and the list fill.

A put of a draft is a new version of an existing profile, or a new profile. There is one rule,
for every path (a Set's draft or not, an edit, the agent's change, a profile written from
scratch): **a draft continues the live profile whose name is exactly the draft's label, and
otherwise it is a profile of its own.** A name never changes through a version.

What makes the rule work is the label a draft is stored under (:func:`draft_label`), worked out
when the draft is made: a change to an existing profile keeps that profile's name (the profile the
base belongs to, found through its version list or the machine file it was mirrored under); a
rename, or a profile written from scratch, gets the app's ``[AI]`` suffix. So the suffix marks
profiles the agent wrote and never renames one that existed, and two from-scratch drafts with one
name are versions of one profile. A Set's draft goes to the Set's profile when that is the
profile its name names; renamed, it is a profile of its own and the Set records the change.

A draft stored before this rule that carries the suffix on a base without it names a different
profile than its base, and is grouped as it always was.
"""

from __future__ import annotations

from typing import Protocol

from gaggiclanker.domain.models import with_app_suffix

__all__ = ["LineageLookup", "draft_label", "lineage_owner"]


def draft_label(label: str, continues: str | None) -> str:
    """The label a draft is stored under: unchanged when it is the name of the profile it
    continues (``continues``), else the app's suffix is added, as for any profile it writes."""
    return label if continues is not None and label == continues else with_app_suffix(label)


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
