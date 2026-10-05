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
re-checks and applies the label suffix; :meth:`store` is the only insert. The
route-facing service delegates to both for a manual draft and for one the model
generated, so a draft typed by hand, proposed in chat, taken from a starting
point or drafted from notes all go through the same offline layers.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.lineage import draft_label
from gaggiclanker.db.repos.profile_board import ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import (
    ProfileDraftRow,
    ProfileDraftsRepository,
    ProfileDraftWrite,
)
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.domain.models import Profile, profile_content_hash
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
from gaggiclanker.domain.signature import ValidExpectation
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.signatures.service import SignatureService

__all__ = [
    "DraftProposals",
    "DraftSignature",
    "PreparedDraft",
    "profile_from_version",
    "schema_errors",
]


@dataclass(frozen=True, slots=True)
class DraftSignature:
    """Expectations an agent proposes with a draft, already validated against the draft's own
    phases: they are stored as **proposed** on the draft's profile version, never confirmed."""

    expectations: tuple[ValidExpectation, ...]
    reason: str
    thread_id: int | None = None


@dataclass(frozen=True, slots=True)
class PreparedDraft:
    """A document that has been through every offline layer. The only way in."""

    profile: Profile
    clamp_changes: list[PolicyChange]
    stop_condition_changes: list[StopConditionChange]


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

    async def label_for(self, base_version_id: int, label: str) -> str:
        """The label a draft of this base is stored under: the profile's own name when the draft
        continues the profile the base belongs to (a version never renames a profile), else the
        app's ``[AI]`` suffix is added, as for any profile it writes."""
        board = ProfileBoardRepository(self.db)
        row = await board.find_live_by_listed_version(base_version_id)
        if row is None:
            device = await self.profiles.find_device_id_for_version(base_version_id)
            row = None if device is None else await board.find_live_by_device(device)
        return draft_label(label, None if row is None else row.label)

    async def prepare(
        self, base_version_id: int, base: Profile, candidate: Profile, *, is_new: bool = False
    ) -> PreparedDraft:
        """Clamp, re-check, name the label, diff the stop conditions.

        A new profile has no stop-condition changes: there is nothing it changes the stops *of*,
        and the base it is stored against is not a profile anybody brews, so a diff against it
        would warn about stops "removed" from a profile the person never had.

        The single constructor for every draft document, whichever route asked
        for it. Raises :class:`Unprocessable` carrying every violation at once —
        a person fixing a hand-edited profile wants the whole list, not the
        first problem six times.
        """
        bounds = await self.bounds()
        clamped, changes = clamp(candidate, bounds)
        violations = check(clamped, bounds)
        if violations:
            raise _rejected(violations)
        # A profile written from scratch is a profile of its own: it is marked as the agent's.
        label = (
            draft_label(clamped.label, None)
            if is_new
            else await self.label_for(base_version_id, clamped.label)
        )
        document = clamped.for_new_device_profile(label=label)
        return PreparedDraft(
            profile=document,
            clamp_changes=changes,
            stop_condition_changes=[] if is_new else diff_stop_conditions(base, document),
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
        new_profile_only: bool = False,
        reusable_version_ids: Collection[int] = (),
        made_by: Literal["agent", "edit"] = "agent",
        signature: DraftSignature | None = None,
    ) -> ProfileDraftRow:
        """A draft somebody typed, or a tool proposed. Same layers, no model involved.

        ``set_id``, ``prediction`` and ``compares_to_version_id`` are the
        experiment half and travel together (with the agent's ``suggest_major``
        and its ``major_reason``, which the push card shows): a draft proposed inside one Set's
        conversation says which Set it is for and what it is expected to do
        differently, and the push records that on the Set version it creates. A
        draft with none of them — typed by hand, or proposed where there is no
        experiment — is exactly what it was before.

        ``is_new`` records that the document is a profile designed from scratch, so it is read
        as one and not as an edit of the base it has to be stored against.

        ``new_profile_only`` refuses a document that, **as it would be stored**
        — clamped, suffixed — is a profile version the archive already has. A
        new Set's recipe carries a profile of its own, because two Sets naming
        one profile version make the matcher ambiguous and nothing is filed.
        Checked on the prepared document's content hash, before anything is
        stored, rather than inferred afterwards from whether the store inserted.
        ``reusable_version_ids`` are the exceptions the caller vouches for: a
        design conversation revising its own card may land on the profile it
        proposed a moment ago.
        """
        base = await self.base_profile(base_version_id)
        try:
            candidate = Profile.model_validate(document)
        except ValidationError as exc:
            raise Unprocessable(
                "That document is not a valid GaggiMate profile",
                details={"schema_errors": schema_errors(exc)},
            ) from None
        prepared = await self.prepare(base_version_id, base, candidate, is_new=is_new)
        if new_profile_only:
            existing = await self.profiles.get_version_by_hash(
                profile_content_hash(prepared.profile)
            )
            if existing is not None and existing.id not in reusable_version_ids:
                raise Conflict(
                    f"This profile is identical to one already in the library "
                    f"({existing.label!r}, profile version {existing.id}). A new recipe carries "
                    "a profile of its own: give it its own label or change something in it.",
                    details={"field": "profile", "message": "the profile is not new"},
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
    ) -> ProfileDraftRow:
        """Insert the draft row for a prepared document.

        A draft's version carries the base's confirmed signature as proposals (a draft carries
        from its base), and the agent's own expectations for it, when it sent some, are stored
        as proposed on the same version with the draft named as where they came from.
        """
        version, created = await self.profiles.ensure_version(prepared.profile, source="draft")
        if created:
            await self.signatures.carry_quietly(base_version_id, version.id)
        row = await self.drafts.create(
            ProfileDraftWrite(
                base_version_id=base_version_id,
                draft_version_id=version.id,
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
                clamp_changes=[change.model_dump(mode="json") for change in prepared.clamp_changes],
                notes=notes,
            )
        )
        if signature is not None and signature.expectations:
            await self.signatures.add_valid(
                version.id,
                signature.expectations,
                reason=signature.reason,
                thread_id=signature.thread_id,
                draft_id=row.id,
            )
        return row

    async def base_profile(self, version_id: int) -> Profile:
        """The stored version a draft is derived from, or a 404."""
        version = await self.profiles.get_version(version_id)
        if version is None:
            raise NotFound(f"No profile version {version_id}")
        return profile_from_version(version)


def profile_from_version(version: ProfileVersionRow) -> Profile:
    """The stored canonical document, back as a :class:`Profile`.

    The canonical form drops `id`, `favorite` and `selected` and collapses the
    firmware's default transition, all of which are optional on the model — so
    it re-validates without help. It is also the right starting point for a
    diff: two profiles compared in canonical form differ only where they brew
    differently.
    """
    if not version.profile:  # pragma: no cover - the column is NOT NULL
        raise Unprocessable(f"Profile version {version.id} has no document")
    return Profile.model_validate(version.profile)


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
