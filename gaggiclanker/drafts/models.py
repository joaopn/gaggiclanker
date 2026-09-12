"""The shape the draft call must answer in, and the shapes the API hands back.

:class:`DraftedProfile` is the output contract. Its JSON schema is what the
provider is sent and its validator is what the reply is checked against, so
there is no second copy of the contract in `prompts/draft.yaml` to drift away
from this one — that prompt explains the fields, never their types.

The profile inside it is the **same** :class:`~gaggiclanker.domain.models.Profile`
the device client validates and the sync engine mirrors. That is the point of
layer 1: one definition of "valid profile" across the server, the front end, the
LLM output schema and anything that talks to the machine. A model that invents a
key, spells `operator` as `gt`, or writes `pump: 100.0` fails validation here and
gets exactly one corrective turn, which is what the strict schema buys.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.profile_policy import PolicyChange, StopConditionChange, Violation

__all__ = ["DraftPreview", "DraftedProfile", "ProfileDraftDetail"]


class DraftedProfile(BaseModel):
    """One edited profile plus the sentence a person reads before approving it."""

    model_config = ConfigDict(extra="forbid")

    #: The complete profile, every phase in order. Not a patch: the prompt is
    #: explicit that a phase left out is a phase deleted from the machine, and a
    #: whole document is the only form that cannot half-apply.
    profile: Profile
    #: What changed and which piece of advice it came from. Stored on the draft
    #: row and shown at the top of the diff.
    change_summary: str = ""


class DraftPreview(BaseModel):
    """What the manual editor gets back for a document it has not saved yet.

    The editor validates live against the schema *and* the policy, which is two
    different failure modes with two different fixes, so the response keeps them
    apart: ``schema_errors`` means the document is not a profile, ``violations``
    means it is a profile the policy will not allow, and ``clamp_changes`` means
    it is a profile the policy would quietly move — which is allowed, but only
    once somebody has seen the list.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool
    schema_errors: list[str] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    clamp_changes: list[PolicyChange] = Field(default_factory=list)
    stop_condition_changes: list[StopConditionChange] = Field(default_factory=list)
    #: The document as it would be stored: clamped, with the label suffixed.
    #: ``None`` when it did not validate at all.
    profile: dict[str, Any] | None = None


class ProfileDraftDetail(BaseModel):
    """One draft with both documents, which is what the diff view needs.

    The list route deliberately does not carry these — a page of fifty drafts
    with two profile documents each is a payload nobody reads — so the queue
    lists rows and this answers "show me that one".
    """

    model_config = ConfigDict(extra="forbid")

    draft: ProfileDraftRow
    #: The profile this was derived from, as stored on its version row.
    base_profile: dict[str, Any] | None = None
    #: The profile that would be written to the machine, suffix and all.
    draft_profile: dict[str, Any] | None = None
