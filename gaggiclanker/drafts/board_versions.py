"""What the profile list serves about a profile's versions and what is waiting for a person.

Read models only. A profile (a board row) has the versions it has been, newest first, each with
where it came from and what it is used for, and **proposals**: open drafts that would land on it
(or on no profile: a proposed new one). A proposal is a version waiting for a person; making it
active is ``put_draft`` (with the stop-condition acknowledgement and the Set recording), and
declining it is the draft's own discard.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject
from gaggiclanker.db.repos.profile_board import VersionSource
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow

__all__ = [
    "ActiveVersion",
    "ConflictSummary",
    "ConflictView",
    "ListedVersion",
    "ProfileVersionsView",
    "ProposedVersion",
    "SetBrewing",
    "short_hash",
]


def short_hash(content_hash: str) -> str:
    """The first eight characters, enough to tell two versions of one profile apart."""
    return content_hash[:8]


class SetBrewing(BaseModel):
    """A Set whose current version brews a profile, so a page can warn before it is changed."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    name: str


class ActiveVersion(BaseModel):
    """The active version of a profile, as a row of the list shows it."""

    model_config = ConfigDict(extra="forbid")

    version_id: int
    short_hash: str
    label: str
    type: str
    utility: bool = False
    #: Where this version came from (``agent``, ``edit``, ``machine``, ``edited_on_machine``,
    #: ``import``).
    source: VersionSource
    created_at: str
    shots_brewed: int = 0


class ListedVersion(BaseModel):
    """One version a profile has been."""

    model_config = ConfigDict(extra="forbid")

    version_id: int
    short_hash: str
    label: str
    type: str
    created_at: str
    #: When the profile got this version (what the list is ordered by, newest first).
    added_at: str
    source: VersionSource
    is_active: bool
    #: Whether a file on the machine holds exactly this version, as of the last read of it.
    is_on_machine: bool
    #: Set when this version did not read back as sent, so a sync does not try it again.
    did_not_verify: bool = False
    shots_brewed: int = 0
    sets_brewing: list[SetBrewing] = Field(default_factory=list)
    #: The profile document, for the version's own information and for a diff.
    profile: JsonObject
    #: The version before this one **in this list**, which the page diffs this one against. The
    #: first version has none: it is new, never a diff against anything else.
    previous_version_id: int | None = None


class ProposedVersion(BaseModel):
    """A draft that would become a version of the profile once a person makes it active."""

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    #: The draft's document, for its information and a diff against the active version.
    profile: JsonObject
    #: The version a page diffs it against: the profile's active version.
    compared_to_version_id: int


class ProfileVersionsView(BaseModel):
    """`GET /api/profile-board/{id}/versions`."""

    model_config = ConfigDict(extra="forbid")

    row_id: int
    label: str
    on_machine: bool
    active_version_id: int
    versions: list[ListedVersion]
    proposed: list[ProposedVersion] = Field(default_factory=list)


class ConflictSummary(BaseModel):
    """The machine's side of a conflict, as the list shows it."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    #: The machine's content as a stored version, and its full and short hash. A person resolves
    #: with the hash they saw, so a file that changed since is refused.
    version_id: int | None = None
    content_hash: str
    short_hash: str


class ConflictView(BaseModel):
    """`GET /api/profile-board/{id}/conflict`: both sides, for a side-by-side panel."""

    model_config = ConfigDict(extra="forbid")

    row_id: int
    label: str
    machine: ConflictSummary
    machine_profile: JsonObject
    app_version_id: int
    app_short_hash: str
    app_profile: JsonObject
