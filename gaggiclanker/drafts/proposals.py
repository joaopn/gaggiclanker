"""Proposing a profile draft, with no way to put it on the machine.

The half of the draft feature that a language model may drive. A draft is a row
in this archive that a person still has to approve and push; making one needs
the database, the safety bounds from the settings, and nothing else. So this
class is built from exactly those two things, and it imports nothing from
:mod:`gaggiclanker.device` — the chat's tools and the stdio MCP server are handed
this rather than :class:`~gaggiclanker.drafts.service.ProfileDraftService`,
which also pushes and rolls back and therefore holds the machine connection.
A tool cannot reach a client it was never given a path to.

**This is the one place a draft document is built.** :meth:`prepare` clamps,
re-checks and names the label; :meth:`store` is the only insert. The
route-facing service delegates to both for a manual draft and for one the model
generated, so a draft typed by hand, proposed in chat, taken from a starting
point or drafted from notes all go through the same offline layers.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import (
    change_destination,
    destination_now,
    name_key,
    pinned_copy_agent_sentence,
    pinned_copy_sentence,
    place_draft,
    taken_name_sentence,
)
from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profile_drafts import (
    ProfileDraftRow,
    ProfileDraftsRepository,
    ProfileDraftWrite,
)
from gaggiclanker.db.repos.profiles import ProfilesRepository, document_for
from gaggiclanker.domain.models import Profile, brew_hash
from gaggiclanker.domain.profile_policy import (
    PolicyBounds,
    PolicyChange,
    ProfileRejected,
    StopConditionChange,
    Violation,
    bounds_from,
    check,
    clamp,
    diff_stop_conditions,
)
from gaggiclanker.drafts.live_lineage import LiveLineage
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.signatures.service import DraftSignature, SignatureService, split_duplicates

__all__ = [
    "DraftProposals",
    "DraftSignature",
    "DraftTarget",
    "PinFrozen",
    "PinnedCopyName",
    "PreparedDraft",
    "TakenName",
    "schema_errors",
]


@dataclass(frozen=True, slots=True)
class DraftTarget:
    """Where a draft goes and what it is called, decided once when it is made and stored on it."""

    #: The profile the draft is a change to (its version is that profile's), or ``None`` for a new
    #: profile, whose version belongs to no profile until it is put.
    destination_board_id: int | None
    #: The draft's own name: a new profile's name, or the name the profile it changes had.
    label: str
    #: A new profile called ``label``; otherwise a change (to ``destination_board_id``, or to the
    #: profile that owns its base when it is read).
    new_profile: bool


@dataclass(frozen=True, slots=True)
class PreparedDraft:
    """A document that has been through every offline layer. The only way in."""

    profile: Profile
    clamp_changes: list[PolicyChange]
    stop_condition_changes: list[StopConditionChange]
    target: DraftTarget

    @property
    def owner_board_id(self) -> int | None:
        return self.target.destination_board_id


class TakenName(Conflict):
    """A new or renamed draft asked for the name of a profile that is already in the list."""

    def __init__(self, label: str, *, existing: str | None = None) -> None:
        super().__init__(taken_name_sentence(label))
        self.label = label
        #: The stored name of the live profile that has it (its case may differ from ``label``):
        #: what a person is told, in their own sentence.
        self.existing = existing if existing is not None else label


class PinFrozen(Conflict):
    """A change to a profile that is a pin's frozen copy: it changes only by unpinning it."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence, code="PIN_FROZEN")


class PinnedCopyName(Conflict):
    """A draft's document is the copy of a pin, which belongs to that pin's Recipe version for ever.

    The message is the agent's wording; ``person`` is the sentence under a Name field.
    """

    def __init__(self, label: str, *, set_name: str, version_label: str) -> None:
        super().__init__(pinned_copy_agent_sentence(label, set_name, version_label))
        self.label = label
        self.person = pinned_copy_sentence(label, set_name, version_label)


class DraftProposals:
    """Create drafts. Never push, never roll back, never hold a machine."""

    def __init__(self, db: Database, settings: SettingsService) -> None:
        self.db = db
        self.settings = settings
        self.drafts = ProfileDraftsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.signatures = SignatureService(db)

    async def bounds(self) -> PolicyBounds:
        """The safety bounds as currently configured.

        Read per call rather than cached: the bounds are in the settings
        registry precisely so somebody can widen one and try again without a
        restart, and a service holding a copy from boot would make that a lie.
        """
        resolved = await self.settings.resolve_all()
        return bounds_from({key: setting.value for key, setting in resolved.items()})

    async def prepare(
        self,
        base_version_id: int,
        base: Profile,
        candidate: Profile,
        *,
        new_profile_name: str | None = None,
        inherit: DraftTarget | None = None,
        is_new: bool = False,
        for_pin: bool = False,
    ) -> PreparedDraft:
        """Decide where the draft goes, clamp, re-check, diff the stop conditions.

        **Where it goes is decided here, once** (:meth:`target_for`) and the document's label is
        rebuilt from it: the candidate's own label decides nothing. ``new_profile_name`` makes a
        new profile of that name (refused when a live profile has it); without it the draft is a
        change to the base's profile and carries that profile's name; ``inherit`` is a
        refinement, which keeps the destination and name of the draft it refines.

        A new profile has no stop-condition changes: there is nothing it changes the stops *of*,
        and the base it is stored against is not a profile anybody brews, so a diff against it
        would warn about stops "removed" from a profile the person never had.

        The single constructor for every draft document, whichever route asked
        for it. Raises :class:`Unprocessable` carrying every violation at once —
        a person fixing a hand-edited profile wants the whole list, not the
        first problem six times.

        A document that is the copy a pin made is refused (:class:`PinnedCopyName`): a copy names
        one Recipe version for ever, so an ordinary draft cannot land on it once its pin is gone.
        ``for_pin`` is the pin's own path, which decides what to do with a copy it finds.
        """
        bounds = await self.bounds()
        clamped, changes = clamp(candidate, bounds)
        violations = check(clamped, bounds)
        if violations:
            raise _rejected(violations)
        target = await self.target_for(
            base_version_id=base_version_id,
            base_label=base.label,
            new_profile_name=new_profile_name,
            inherit=inherit,
            is_new=is_new,
        )
        document = clamped.for_new_device_profile(label=target.label)
        if not for_pin:
            pinned = await self.pinned_copy_of(document)
            if pinned is not None:
                raise pinned
        return PreparedDraft(
            profile=document,
            clamp_changes=changes,
            stop_condition_changes=[] if is_new else diff_stop_conditions(base, document),
            target=target,
        )

    async def target_for(
        self,
        *,
        base_version_id: int,
        base_label: str,
        new_profile_name: str | None = None,
        inherit: DraftTarget | None = None,
        is_new: bool = False,
    ) -> DraftTarget:
        """Where a draft goes and what it is called. Raises the refusal when it cannot go there.

        One rule (`place_draft`, with `change_destination` for a change), asked here and nowhere
        else when a draft is made: a new profile is refused when a live profile has its name
        (:class:`TakenName`), a change to a pin's frozen copy is refused (:class:`PinFrozen`).
        """
        lookup = LiveLineage(self.db)
        if inherit is not None:
            new_profile = inherit.new_profile
            stored = (
                None
                if inherit.destination_board_id is None
                else await lookup.by_id(inherit.destination_board_id)
            )
            name = inherit.label
        elif new_profile_name is not None:
            new_profile, stored, name = True, None, new_profile_name
        else:
            new_profile, stored, name = False, None, base_label
        destination = await destination_now(
            lookup,
            new_profile=new_profile,
            stored=stored,
            base_version_id=base_version_id,
            base_label=base_label,
        )
        if destination is not None:
            name = destination.label
        if not name.strip():
            raise Unprocessable("A profile needs a name")
        placement = await place_draft(
            lookup,
            label=name,
            destination=destination,
            base_version_id=None if is_new else base_version_id,
        )
        if placement.frozen is not None:
            raise PinFrozen(placement.frozen)
        if placement.taken is not None:
            raise TakenName(placement.name, existing=placement.taken.label)
        return DraftTarget(
            destination_board_id=None if placement.owner is None else placement.owner.id,
            label=placement.name,
            new_profile=new_profile,
        )

    async def change_name(self, base_version_id: int) -> str:
        """The name a change to this version's profile carries: the profile's own, else the
        version's. What a refusal says when a draft tries to rename the profile it changes."""
        base = await self.profiles.get_version(base_version_id)
        if base is None:
            raise NotFound(f"No profile version {base_version_id}")
        base_name = await self.profiles.name_of(base)
        destination = await change_destination(
            LiveLineage(self.db),
            base_version_id=base_version_id,
            base_label=base_name,
        )
        return base_name if destination is None else destination.label

    async def editor_name(self, base_version_id: int, label: str) -> str | None:
        """What a hand-edited document is: ``None`` for a change to the base's profile (its label
        is that profile's name, case and surrounding space aside), else the name of the new
        profile it makes. Decided here, at the edge, so the person's experience is unchanged; the
        draft stores the answer and nothing works it out from the label again."""
        if name_key(label) == name_key(await self.change_name(base_version_id)):
            return None
        return label

    async def pinned_copy_of(self, document: Profile) -> PinnedCopyName | None:
        """The refusal for a document that is exactly the copy of a pin (live or unpinned): the
        copy's brew under the name of the profile that carries it. Else ``None``. A change that
        goes back to the pinned brew under the original's name is not the copy. One function for
        the constructor and the name check."""
        pins = PinsRepository(self.db)
        record = await pins.by_copy_content(brew_hash(document), document.label)
        detail = None if record is None else await pins.detail(record.id)
        if detail is None:
            return None
        return PinnedCopyName(
            document.label, set_name=detail.set_name, version_label=detail.version_label
        )

    async def create_manual(
        self,
        *,
        base_version_id: int,
        document: dict[str, Any],
        change_summary: str = "",
        notes: str = "",
        set_id: int | None = None,
        prediction: str = "",
        compares_to_version_id: int | None = None,
        suggest_major: bool = False,
        major_reason: str = "",
        is_new: bool = False,
        new_profile_name: str | None = None,
        made_by: Literal["agent", "edit"] = "agent",
        signature: DraftSignature | None = None,
    ) -> ProfileDraftRow:
        """A draft somebody typed, or a tool proposed. Same layers, no model involved.

        **Where it goes is asked for, never read off the document.** ``new_profile_name`` makes a
        new profile of that name; without it the draft is a change to the base's profile and is
        stored under that profile's own name, whatever label the document carries. A profile
        written from scratch (``is_new``) is a new profile, named by its document's label when
        no name is given.

        ``set_id``, ``prediction`` and ``compares_to_version_id`` are the
        experiment half and travel together (with the agent's ``suggest_major``
        and its ``major_reason``, which the push card shows): a draft proposed inside one Recipe's
        conversation says which Recipe it is for and what it is expected to do
        differently, and the push records that on the Recipe version it creates. A
        draft with none of them — typed by hand, or proposed where there is no
        experiment — is exactly what it was before.

        ``is_new`` records that the document is a profile designed from scratch, so it is read
        as one and not as an edit of the base it has to be stored against.

        """
        base = await self.base_profile(base_version_id)
        try:
            candidate = Profile.model_validate(document)
        except ValidationError as exc:
            raise Unprocessable(
                "That document is not a valid GaggiMate profile",
                details={"schema_errors": schema_errors(exc)},
            ) from None
        if is_new and new_profile_name is None:
            new_profile_name = candidate.label
        prepared = await self.prepare(
            base_version_id,
            base,
            candidate,
            new_profile_name=new_profile_name,
            is_new=is_new,
        )
        return await self.store(
            base_version_id=base_version_id,
            prepared=prepared,
            change_summary=change_summary or "Edited by hand.",
            notes=notes,
            set_id=set_id,
            prediction=prediction,
            compares_to_version_id=compares_to_version_id,
            suggest_major=suggest_major,
            major_reason=major_reason,
            is_new=is_new,
            made_by=made_by,
            signature=signature,
        )

    def _atomic(self) -> AbstractAsyncContextManager[Any]:
        """One transaction for the writes that follow, or none when the caller holds one."""
        return nullcontext() if self.db.in_transaction else self.db.transaction()

    async def store(
        self,
        *,
        base_version_id: int,
        prepared: PreparedDraft,
        change_summary: str,
        notes: str,
        parent_draft_id: int | None = None,
        set_id: int | None = None,
        prediction: str = "",
        compares_to_version_id: int | None = None,
        suggest_major: bool = False,
        major_reason: str = "",
        is_new: bool = False,
        made_by: Literal["agent", "edit"] = "agent",
        signature: DraftSignature | None = None,
        carry_signature: bool = True,
    ) -> ProfileDraftRow:
        """Insert the draft row for a prepared document.

        A draft's version carries the base's confirmed signature as proposals (a draft carries
        from its base), and the agent's own expectations for it, when it sent some, are stored
        as proposed on the same version with the draft named as where they came from.

        ``carry_signature`` false stores the version without its base's signature: a pin's copy
        is judged by the original's, so it carries none of its own.
        """
        version, created = await self.profiles.ensure_version(
            prepared.profile, owner=prepared.owner_board_id, source="draft"
        )
        if created and carry_signature:
            await self.signatures.carry_quietly(base_version_id, version.id)
        # The draft and the expectations it carries are one write: a refusal or a crash between
        # them must not leave a draft whose signature is half there.
        async with self._atomic():
            row = await self.drafts.create(
                ProfileDraftWrite(
                    base_version_id=base_version_id,
                    draft_version_id=version.id,
                    # Where it goes and what it is called, decided once (`prepare`) and kept:
                    # nothing works either out from a name again.
                    label=prepared.target.label,
                    destination_board_id=prepared.target.destination_board_id,
                    new_profile=prepared.target.new_profile,
                    # Resolved now rather than at push time: what this draft was
                    # derived from is a fact about this moment, and by the time
                    # somebody pushes it the mirror may point somewhere else — which
                    # is precisely the staleness the push then refuses.
                    base_device_profile_id=await self.profiles.find_device_id_for_version(
                        base_version_id
                    ),
                    parent_draft_id=parent_draft_id,
                    set_id=set_id,
                    prediction=prediction,
                    compares_to_version_id=compares_to_version_id,
                    suggest_major=suggest_major,
                    major_reason=major_reason,
                    is_new=is_new,
                    change_summary=change_summary,
                    made_by=made_by,
                    stop_condition_changes=[
                        change.model_dump(mode="json") for change in prepared.stop_condition_changes
                    ],
                    clamp_changes=[
                        change.model_dump(mode="json") for change in prepared.clamp_changes
                    ],
                    notes=notes,
                )
            )
            if signature is not None and signature.expectations:
                # What the draft's version already holds (carried from its base, or sent twice)
                # is not stored a second: "Confirm all" would otherwise check it twice.
                fresh, duplicates = split_duplicates(
                    signature.expectations, await self.signatures.repo.for_version(version.id)
                )
                signature.skipped.extend(v.sentence for _, v, _ in duplicates)
                if fresh:
                    await self.signatures.add_valid(
                        version.id,
                        fresh,
                        reason=signature.reason,
                        thread_id=signature.thread_id,
                        draft_id=row.id,
                    )

        return row

    async def base_profile(self, version_id: int) -> Profile:
        """The stored version a draft is derived from, as a document under its profile's name
        (a version with no profile: the name its draft, its file or its first document gave it),
        or a 404."""
        version = await self.profiles.get_version(version_id)
        if version is None:
            raise NotFound(f"No profile version {version_id}")
        return document_for(version, await self.profiles.name_of(version))


def schema_errors(exc: ValidationError) -> list[str]:
    """pydantic's errors as one line each, naming the field and the problem.

    The *input* is deliberately left out: these travel to the client in
    `error.details`, which is echoed verbatim, and the house rule is that
    validation details name the field and the problem and never the value.
    """
    return [
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    ]


def _rejected(violations: list[Violation]) -> Unprocessable:
    rejection = ProfileRejected(violations)
    return Unprocessable(
        str(rejection),
        details={"violations": [violation.model_dump(mode="json") for violation in violations]},
    )
