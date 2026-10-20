"""A profile copied under a new name: the one way a profile is made from an existing version.

A pinned Recipe version and a duplicated Recipe both need "this version's profile, under another
name, on the profile list". It is made by the draft constructor like every other profile document
(:meth:`~gaggiclanker.drafts.proposals.DraftProposals.prepare` and ``store``), never by a second
one, so the strict schema, the clamp, the check and the name rule apply unchanged; and it is put
on the list by the same put a person's approval runs, in the caller's transaction, so there is no
moment at which a waiting proposal stands on the Profiles page.

A copy is only worth making when it brews what the original brewed. So **if the clamp moves any
number the copy is refused**, with the clamp's list: the numbers it would have changed are the
reason, and the copy would not be the version it is a copy of.

The copy records nothing on any Recipe (the draft is made with no Recipe, so the sync's recording of
a pushed draft on its Recipe never fires). The signature is the caller's choice: a duplicate carries
the original's the way every draft's new version does, a pin carries none because its shots are
judged by the original's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.domain.models import Profile, brew_hash
from gaggiclanker.domain.profile_policy import PolicyChange, outside_bounds_sentence
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.drafts.proposals import PreparedDraft, schema_errors
from gaggiclanker.infra.errors import Conflict, Unprocessable

__all__ = [
    "RECIPE_GONE",
    "VERSION_NOT_OF_RECIPE",
    "CopyBeyondPolicy",
    "PreparedCopy",
    "ProfileCopier",
]

#: What a person is told when the Recipe or version a pin or a duplicate was asked about is not
#: there: one wording for both screens that ask.
RECIPE_GONE = "That Set is not there any more."
VERSION_NOT_OF_RECIPE = "That version is not one of this Set's."


class CopyBeyondPolicy(Conflict):
    """The safety policy would change numbers of the profile being copied, so a copy of it would
    not brew what it brewed."""

    def __init__(self, sentence: str, changes: list[PolicyChange]) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.changes = changes


@dataclass(frozen=True, slots=True)
class PreparedCopy:
    """A copy's document, through every offline layer and not yet stored."""

    base_version_id: int
    prepared: PreparedDraft
    #: The name the copy is stored under (the label given, stripped).
    name: str
    content_hash: str


class ProfileCopier:
    """Copies a profile version under a new name, onto the list, inside the caller's transaction."""

    def __init__(self, board: BoardService) -> None:
        self.board = board
        self.proposals = board.proposals

    async def prepare(
        self,
        base_version_id: int,
        label: str,
        *,
        for_pin: bool = False,
        document: dict[str, Any] | None = None,
    ) -> PreparedCopy:
        """The copy's document, or the reason there is none. Reads only; stores nothing.

        ``document`` is the copy a person edited before saving it (Duplicate Profile's editor);
        without one the copy is the version's own profile. Either way it is stored under
        ``label``, whatever label the document carries.

        Refused, each with its own error: an empty name (422), a document that is not a profile
        (422, with the schema's errors), a name a live profile has (the constructor's
        :class:`TakenName`, 409), and a profile the safety policy would change
        (:class:`CopyBeyondPolicy`, 409, carrying the clamp's list or the check's violations).
        """
        base = await self.proposals.base_profile(base_version_id)
        name = label.strip()
        if not name:
            raise Unprocessable("A profile needs a name")
        source = base
        if document is not None:
            try:
                source = Profile.model_validate(document)
            except ValidationError as exc:
                raise Unprocessable(
                    "That document is not a valid GaggiMate profile",
                    details={"schema_errors": schema_errors(exc)},
                ) from None
        # A copy is a profile of its own whatever its name: it is asked for as a new profile, so
        # a copy named like its original is refused as a taken name, never read as a new version
        # of the original.
        try:
            prepared = await self.proposals.prepare(
                base_version_id,
                base,
                source.model_copy(update={"label": name}),
                new_profile_name=name,
                is_new=True,
                for_pin=for_pin,
            )
        except Unprocessable as rejected:
            details = rejected.details
            if isinstance(details, dict) and "violations" in details:
                raise CopyBeyondPolicy(rejected.message, []) from None
            raise
        if prepared.clamp_changes:
            raise CopyBeyondPolicy(
                outside_bounds_sentence(prepared.clamp_changes), prepared.clamp_changes
            )
        return PreparedCopy(
            base_version_id=base_version_id,
            prepared=prepared,
            name=prepared.profile.label,
            content_hash=brew_hash(prepared.profile),
        )

    async def put(self, copy: PreparedCopy, *, carry_signature: bool) -> BoardRow:
        """Store the copy and put it on the list: switched on, starred, the app's own.

        Inside the caller's transaction (the draft and its put are one write). The draft is made
        with no Recipe, a manual edit's source and the original as its base; it is approved by the
        put in the same step, so it is never a proposal waiting for a person.
        """
        draft = await self.proposals.store(
            base_version_id=copy.base_version_id,
            prepared=copy.prepared,
            change_summary="A copy under a new name.",
            notes="",
            is_new=True,
            made_by="edit",
            carry_signature=carry_signature,
        )
        return await self.board.put_in_transaction(draft.id)

    async def copy_profile(
        self, base_version_id: int, label: str, *, carry_signature: bool
    ) -> BoardRow:
        """The whole copy: :meth:`prepare` then :meth:`put`. Call it inside a transaction."""
        return await self.put(
            await self.prepare(base_version_id, label), carry_signature=carry_signature
        )
