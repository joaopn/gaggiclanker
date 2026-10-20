"""Duplicating a Set: a new Set that starts from one version of another, with no history.

A duplicate copies a recipe, not a past. The new Set gets the version's grind, dose, yield and
intent, a profile of its own made from the version's profile, a note saying where it came from
and, if the person chose, the source Set's confirmed insights. No version, shot, prediction,
outcome, conversation, proposal or override crosses over.

The new profile is made by the one copy function
(:class:`~gaggiclanker.drafts.copies.ProfileCopier`),
so the safety layers and the name rule apply unchanged, and it is put on the list switched on and
starred, the signature carried over. **Everything is one transaction**: the profile, the Set with
its first version and the insights are written together or not at all.

The copy is made from the profile version the Set version names, never from a pin's copy (a Set
version can not name one), so duplicating a pinned version duplicates the profile the version
brews.

**One function asks every question, the preview and the duplicate alike**
(:meth:`DuplicateService.inspect`). No chat tool, MCP client or background task is handed this
class: it is built over the board service, which only the routes receive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.grinders import GrindersRepository
from gaggiclanker.db.repos.knowledge_insights import (
    InsightSource,
    InsightsRepository,
    InsightWrite,
    grouped_by_version,
)
from gaggiclanker.db.repos.lineage import person_taken_sentence
from gaggiclanker.db.repos.pins import PinsRepository
from gaggiclanker.db.repos.profile_board import BoardRow
from gaggiclanker.db.repos.sets import (
    SetRow,
    SetsRepository,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.drafts.board import BoardService
from gaggiclanker.drafts.copies import (
    SET_GONE,
    VERSION_NOT_OF_SET,
    CopyBeyondPolicy,
    PreparedCopy,
    ProfileCopier,
)
from gaggiclanker.drafts.proposals import PinnedCopyName, TakenName
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable

__all__ = [
    "DuplicateAnswer",
    "DuplicateInsight",
    "DuplicatePreview",
    "DuplicateRecipe",
    "DuplicateRefused",
    "DuplicateRequest",
    "DuplicateService",
    "ProfileDuplicateRequest",
]


#: The longest a Set's name may be (the Set write model's own limit).
SET_NAME_MAX = 200


def _suggested_name(source: str) -> str:
    """ "<source name> (copy)", the source trimmed so the suggestion is a valid Set name."""
    suffix = " (copy)"
    return source[: SET_NAME_MAX - len(suffix)].rstrip() + suffix


class DuplicateRefused(Conflict):
    """A duplicate the request cannot make, with the one code and sentence the dialog shows."""

    def __init__(self, code: str, message: str, *, details: object = None) -> None:
        super().__init__(message, code=code, details=details)


class DuplicateRecipe(BaseModel):
    """The source version's recipe, as the dialog's read-only line says it."""

    model_config = ConfigDict(extra="forbid")

    #: The name of the profile the version brews; ``None`` for a version that names no profile
    #: (an older Set's).
    profile_label: str | None = None
    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None
    intent: str = ""


class DuplicateInsight(BaseModel):
    """One of the source Set's own confirmed insights, offered for copying."""

    model_config = ConfigDict(extra="forbid")

    id: int
    text: str
    source: str


class DuplicatePreview(BaseModel):
    """`GET .../duplicate-preview`: what the dialog starts from. Read-only."""

    model_config = ConfigDict(extra="forbid")

    #: "<source Set's name> (copy)".
    set_name: str
    bean_id: int
    grinder_id: int | None = None
    recipe: DuplicateRecipe
    #: Whether the version names a profile; when it does not, the duplicate has none either and
    #: asks for no profile name.
    has_profile: bool
    #: The source Set's **own** confirmed, not dismissed insights, in the order the Set page lists
    #: them. Never the general ones that also apply to it.
    insights: list[DuplicateInsight]


class DuplicateRequest(BaseModel):
    """`POST .../duplicate`: what the dialog sends."""

    model_config = ConfigDict(extra="forbid")

    set_name: str
    #: Required (and non-blank) when the version has a profile; must be left out when it has none.
    profile_name: str | None = None
    bean_id: int
    grinder_id: int | None = None
    insight_ids: list[int] = []


class ProfileDuplicateRequest(BaseModel):
    """`POST /api/profile-versions/{id}/duplicate`: what the Duplicate Profile dialog sends."""

    model_config = ConfigDict(extra="forbid")

    #: The new profile's name. Required: the dialog offers no default.
    name: str
    #: The document as the person edited it; left out, the copy brews exactly the version.
    profile: dict[str, Any] | None = None


class DuplicateAnswer(BaseModel):
    """`POST .../duplicate`: the new Set, and the id of the profile-list row of its new profile
    (``None`` for a duplicate of a version that names no profile)."""

    model_config = ConfigDict(extra="forbid")

    set: SetRow
    board_id: int | None = None


@dataclass(slots=True)
class _Source:
    """What one look at the source found."""

    set_row: SetRow
    version: SetVersionRow
    insights: list[DuplicateInsight]
    sources: dict[int, str]
    #: Each offered insight's first original: its own id, or the one it is itself a copy of.
    originals: dict[int, int]
    #: The Set each offered insight's first original was learned in: this Set's, or the one the
    #: insight is itself a copy from.
    home_sets: dict[int, int]


class DuplicateService:
    """Preview and duplicate. Built over the board service the routes hold."""

    def __init__(self, db: Database, board: BoardService) -> None:
        self.db = db
        self.board = board
        self.sets = SetsRepository(db)
        self.insights = InsightsRepository(db)
        self.copier = ProfileCopier(board)

    async def inspect(self, set_id: int, version_id: int) -> _Source:
        """The source version and what can be copied from it. Reads only.

        404 for a Set or version that is not there (or a version of another Set); 409
        ``DESIGNING`` for a Set whose first recipe is not written yet. An archived Set is fine:
        a finished bag is the usual starting point.
        """
        set_row = await self.sets.get(set_id)
        if set_row is None:
            raise NotFound(SET_GONE)
        version = await self.sets.version_of_set(set_id, version_id)
        if version is None:
            raise NotFound(VERSION_NOT_OF_SET)
        if set_row.designing:
            raise DuplicateRefused(
                "DESIGNING",
                "This Set is still being designed: there is no version to copy yet.",
            )
        confirmed = [item for item in await self.insights.own(set_id) if item.confirmed]
        # The order the Set page lists them in: by version, the newest version's group first.
        own = grouped_by_version(confirmed, [row.id for row in await self.sets.versions(set_id)])
        return _Source(
            set_row=set_row,
            version=version,
            insights=[DuplicateInsight(id=i.id, text=i.text, source=i.source) for i in own],
            sources={i.id: i.source for i in own},
            originals={i.id: i.copied_from_insight_id or i.id for i in own},
            home_sets={
                i.id: i.copied_from_set_id if i.copied_from_set_id is not None else set_id
                for i in own
            },
        )

    async def preview(self, set_id: int, version_id: int) -> DuplicatePreview:
        found = await self.inspect(set_id, version_id)
        version = found.version
        return DuplicatePreview(
            set_name=_suggested_name(found.set_row.name),
            bean_id=found.set_row.bean_id,
            grinder_id=found.set_row.grinder_id,
            recipe=DuplicateRecipe(
                profile_label=version.profile_label,
                grind_setting=version.grind_setting,
                grind_value=version.grind_value,
                dose_g=version.dose_g,
                target_yield_g=version.target_yield_g,
                intent=version.intent,
            ),
            has_profile=version.profile_version_id is not None,
            insights=found.insights,
        )

    async def duplicate(
        self, set_id: int, version_id: int, request: DuplicateRequest
    ) -> DuplicateAnswer:
        """Make the new Set: the profile, the Set with its first version and the insights, in one
        transaction. Nothing is sent to the machine: the next sync with Writes on puts the new
        profile there."""
        async with self.db.transaction():
            found = await self.inspect(set_id, version_id)
            version = found.version
            set_name = request.set_name.strip()
            if not set_name:
                raise Unprocessable("A Set needs a name.", details={"field": "set_name"})
            if len(set_name) > SET_NAME_MAX:
                raise Unprocessable(
                    f"A Set name is at most {SET_NAME_MAX} characters.",
                    details={"field": "set_name"},
                )
            has_profile = version.profile_version_id is not None
            if has_profile and (request.profile_name is None or not request.profile_name.strip()):
                raise Unprocessable("A profile needs a name.", details={"field": "profile_name"})
            if not has_profile and request.profile_name is not None:
                raise Unprocessable(
                    f"{version.version_label} names no profile, so there is no profile to name.",
                    details={"field": "profile_name"},
                )
            wanted = list(dict.fromkeys(request.insight_ids))
            if any(i not in found.sources for i in wanted):
                raise Unprocessable(
                    "One of the chosen insights is no longer one of this Set's confirmed insights.",
                    details={"field": "insight_ids"},
                )
            if await BeansRepository(self.db).get(request.bean_id) is None:
                raise Unprocessable(
                    "That bean is not there any more. Pick another.",
                    details={"field": "bean_id", "message": "that bean does not exist"},
                )
            if (
                request.grinder_id is not None
                and await GrindersRepository(self.db).get(request.grinder_id) is None
            ):
                raise Unprocessable(
                    "That grinder is not there any more. Pick another.",
                    details={"field": "grinder_id", "message": "that grinder does not exist"},
                )

            profile_version_id: int | None = None
            board_id: int | None = None
            if version.profile_version_id is not None:
                assert request.profile_name is not None
                copy = await self._prepared(version.profile_version_id, request.profile_name)
                row = await self.copier.put(copy, carry_signature=True)
                board_id = row.id
                profile_version_id = row.current_version_id

            new_id = await self.sets.create_in_transaction(
                SetWrite(name=set_name, bean_id=request.bean_id, grinder_id=request.grinder_id),
                SetVersionWrite(
                    profile_version_id=profile_version_id,
                    grind_setting=version.grind_setting,
                    grind_value=version.grind_value,
                    dose_g=version.dose_g,
                    target_yield_g=version.target_yield_g,
                    intent=version.intent,
                    origin="manual",
                ),
                automatch=True,
                copied_from_version_id=version.id,
            )
            # In the order the source Set lists them, whatever order the request names them in.
            for item in found.insights:
                if item.id in wanted:
                    await self.insights.insert(
                        InsightWrite(
                            text=item.text,
                            source=cast(InsightSource, found.sources[item.id]),
                            confirmed=True,
                            set_id=new_id,
                            copied_from_insight_id=found.originals[item.id],
                            copied_from_set_id=found.home_sets[item.id],
                        )
                    )
        created = await self.sets.get(new_id)
        assert created is not None
        return DuplicateAnswer(set=created, board_id=board_id)

    async def duplicate_profile(
        self, version_id: int, request: ProfileDuplicateRequest
    ) -> BoardRow:
        """Duplicate Profile: a new profile from one version, under the name the person typed, with
        the document as they edited it, put on the list switched on. One transaction, and nothing
        is sent to the machine: the next sync with Writes on puts it there.

        Always a new profile: the original's own name is refused like any other taken name, so a
        duplicate is never read as a new version of the profile it came from. Refused, nothing
        written: 404 for a version that is not there; 422 for an empty name or a document that is
        not a profile; 409 ``PROFILE_IS_A_PIN``, ``PROFILE_NAME_TAKEN`` and
        ``PROFILE_POLICY_REFUSED``.
        """
        async with self.db.transaction():
            if await self.board.profiles.get_version(version_id) is None:
                raise NotFound(f"No profile version {version_id}")
            if await PinsRepository(self.db).is_copy(version_id):
                raise DuplicateRefused(
                    "PROFILE_IS_A_PIN",
                    "That version is the copy of a pin: duplicate the profile it was pinned from.",
                )
            copy = await self._prepared(version_id, request.name, document=request.profile)
            return await self.copier.put(copy, carry_signature=True)

    async def _prepared(
        self, base_version_id: int, name: str, *, document: dict[str, Any] | None = None
    ) -> PreparedCopy:
        """The copy's document, or the one refusal that says why there is none.

        A name that is the copy of a pin (live or since unpinned) is refused by the copy function
        itself, with the sentence the pin screens use.
        """
        try:
            copy = await self.copier.prepare(base_version_id, name, document=document)
        except TakenName as taken:
            raise _name_taken(person_taken_sentence(taken.existing), taken.label) from None
        except PinnedCopyName as named:
            raise _name_taken(named.person, named.label) from None
        except CopyBeyondPolicy as refused:
            raise DuplicateRefused(
                "PROFILE_POLICY_REFUSED",
                refused.sentence,
                details={"clamp_changes": [c.model_dump(mode="json") for c in refused.changes]},
            ) from None
        return copy


def _name_taken(sentence: str, name: str) -> DuplicateRefused:
    return DuplicateRefused("PROFILE_NAME_TAKEN", sentence, details={"name": name})
