"""Where a proposed profile version stands, in the server's words.

One read serves every card that shows a proposal, in the chat and on the Profiles page: the
state is derived here from what the board already knows, never composed by the web. Exactly one
of six states holds for every draft, whatever its status and whatever the board looks like:

* ``waiting``: the draft is still a proposal (status ``draft``).
* ``approved``: put on the list, its version is the profile's active one, and the machine does
  not hold it yet (a sync will put it there, or cannot yet; ``reason`` says which).
* ``on_machine``: the machine holds it now (the mirror's file for the profile is at this
  version) and syncs are not paused; ``selected`` says whether it is the selected profile.
* ``not_on_machine``: a sync tried and did not keep it, or the profile is in conflict, or syncs
  are paused for a suspected reset.
* ``declined``: a person turned it down.
* ``replaced``: a refinement or a newer proposal took its place, or another version of the
  profile has become the active one since, or the exact profile is already in the list.

:func:`classify` is the whole rule, a pure function so a test can walk every combination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow

__all__ = ["RowFacts", "Standing", "StandingState", "classify"]

type StandingState = Literal[
    "waiting", "approved", "on_machine", "not_on_machine", "declined", "replaced"
]

WRITES_OFF = "Writes are off, so a sync will not send it."
SWITCHED_OFF = "The profile is switched off, so a sync will not send it."
AT_NEXT_SYNC = "It goes to the machine at the next sync."
SYNC_FAILED = (
    "The last sync of profiles ended in an error. It goes to the machine at the next sync."
)
READ_BACK_DIFFERENTLY = "The machine read it back differently, so it was not kept."
IN_CONFLICT = "Someone edited this profile on the machine: choose which version to keep."
PAUSED = "Syncing is paused because the machine looks reset: resume it from the Profiles page."
NEWER_PROPOSAL = "A newer proposal replaced this one."
NOT_IN_LIST = "That profile is no longer in the list."
ANOTHER_ACTIVE = "Another version of this profile is active now."
NEWER_FIRST_RECIPE = "A newer first recipe replaced this one."
SET_WRITTEN_OTHERWISE = "The Set's first recipe was written another way."
SET_DISCARDED = "That Set was discarded."
TOOK_OFF = "The profile is switched off, so the sync took it off the machine."


def already_in_list(label: str) -> str:
    return f"This exact profile is already in the list as {label}."


@dataclass(frozen=True, slots=True)
class RowFacts:
    """What the board knows about the profile row that lists the draft's version."""

    row_id: int
    label: str
    #: The row's active version is the draft's version.
    active_is_this: bool
    on_machine: bool
    #: A sync tried this version and the machine did not keep it.
    failed_is_this: bool
    in_conflict: bool
    #: The mirror holds the row's file at the active version.
    holds_current: bool
    selected: bool | None


@dataclass(frozen=True, slots=True)
class Standing:
    state: StandingState
    reason: str | None = None


#: The ``outcome.action`` a sync records when it closes a pushed draft because its file went off
#: the machine for good. Such a draft is ``discarded``, but nobody declined it.
RETIRED_BY_SYNC = frozenset({"went_back", "deleted", "removed", "switched_off"})

#: The ``outcome.action`` recorded where the first-recipe paths discard a draft that no person
#: declined, with the sentence each reads as. The draft was never put on the list.
OVERTAKEN_DESIGN = {
    "newer_recipe": NEWER_FIRST_RECIPE,
    "set_written_otherwise": SET_WRITTEN_OTHERWISE,
    "set_discarded": SET_DISCARDED,
}


def classify(
    draft: ProfileDraftRow,
    row: RowFacts | None,
    *,
    writes_enabled: bool,
    paused: bool,
    sync_failed_since: bool,
    already_listed_as: str | None = None,
) -> Standing:
    """The one state of a draft, and the sentence that explains it when it needs one.

    ``row`` is the live profile that lists the draft's version, if any. ``sync_failed_since``:
    the last profile sync ended in error after the draft was approved. ``already_listed_as``:
    the label of the profile that already holds this draft's exact document, for a draft that
    is still waiting.

    A draft a sync closed because its file went off the machine (switched off, went back,
    deleted, removed) is ``discarded`` in the database but is not declined: it is read from the
    board like an approved one, so a version made active again reads as waiting for the next
    sync, and one that is not active any more reads as replaced. A first recipe's draft that
    the design paths discarded (a newer recipe, the Set's first version written another way, the
    Set discarded) records why and reads as replaced; only a discard with no recorded reason is a
    person's decline.
    """
    status = draft.status
    if status == "draft":
        if already_listed_as is not None:
            return Standing("replaced", already_in_list(already_listed_as))
        return Standing("waiting")
    retired = None
    if status == "discarded":
        action = (draft.outcome or {}).get("action")
        if action in OVERTAKEN_DESIGN:
            return Standing("replaced", OVERTAKEN_DESIGN[action])
        if action not in RETIRED_BY_SYNC:
            return Standing("declined")
        retired = action
    elif status == "superseded":
        return Standing("replaced", NEWER_PROPOSAL)
    elif status == "failed":
        # A push that did not verify (the older, direct path): the machine did not keep it.
        return Standing("not_on_machine", READ_BACK_DIFFERENTLY)
    # approved, pushed or closed by a sync: the board row decides.
    if row is None:
        return Standing("replaced", NOT_IN_LIST)
    if not row.active_is_this:
        return Standing("replaced", ANOTHER_ACTIVE)
    if row.in_conflict:
        return Standing("not_on_machine", IN_CONFLICT)
    if row.failed_is_this:
        return Standing("not_on_machine", READ_BACK_DIFFERENTLY)
    if paused:
        return Standing("not_on_machine", PAUSED)
    if row.holds_current:
        return Standing("on_machine")
    if retired == "switched_off" and not row.on_machine:
        return Standing("not_on_machine", TOOK_OFF)
    if not writes_enabled:
        return Standing("approved", WRITES_OFF)
    if not row.on_machine:
        return Standing("approved", SWITCHED_OFF)
    return Standing("approved", SYNC_FAILED if sync_failed_since else AT_NEXT_SYNC)
