"""Draft a profile, and record it on a Set once it is on the machine.

The shape of the whole feature is a path and one invariant.

    generate / create_manual  ->  put on the board (a person)  ->  the next sync (the machine)

**The invariant: every document that leaves this module has been through
:func:`~gaggiclanker.domain.profile_policy.enforce`.** There is exactly one
place a profile is built (:meth:`.proposals.DraftProposals.prepare`) and it
clamps, re-checks and applies the label suffix before anything is stored. A
caller cannot construct a draft that skipped a layer, because there is no other
constructor. That half lives in its own class, built without the machine
connection, so the chat's tools can propose a draft without holding anything
that could write one.

This service holds no machine connection at all. A draft reaches the machine only through
the profile board (:mod:`.board`): a person's "Put on the board" approves it and makes it a
profile's next version, and the write phase of the next sync saves it, reads it back and
removes what it replaces, through the primitives in :mod:`.machine`. What remains here is
what a draft needs before that (drafting, refining, discarding) and
:meth:`ProfileDraftService.attach_to_set`, which the write phase calls once a profile is on
the machine, so a Set records the version the same way whoever put the profile there.

**The suffix is applied at draft time, not at write time.** crema appends "[AI]"
as it saves. Doing it earlier means the document a person approves, the document
that goes on the wire and the document the round trip compares against are one
document with one content hash — and the diff shows the rename, which is
honest, because the profile on the machine really will be called that.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow, ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionRow
from gaggiclanker.domain.models import (
    Profile,
    with_app_suffix,
)
from gaggiclanker.domain.profile_policy import (
    PolicyBounds,
    check,
    clamp,
    diff_stop_conditions,
)
from gaggiclanker.drafts.models import DraftedProfile, DraftPreview, ProfileDraftDetail
from gaggiclanker.drafts.proposals import (
    DraftProposals,
    PreparedDraft,
    schema_errors,
)
from gaggiclanker.infra.errors import Conflict, NotFound, ServiceUnavailable
from gaggiclanker.llm.prompts import PromptService, RenderedPrompt
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmRequest, Ok
from gaggiclanker.settings_service import SettingsService

__all__ = ["DRAFT_PROMPT", "ProfileDraftService"]

log = structlog.get_logger(__name__)

#: The prompt one draft renders. One file rather than a review's two,
#: because the facts here are four short blocks rather than a page of context
#: whose layout is worth editing separately from the persona.
DRAFT_PROMPT = "draft"

#: What the prompt's advice block says. No path passes advice to a draft any
#: more (a profile change the chat argues for is drafted by its own tool), so
#: the block always says to work from the notes alone.
NO_ADVICE = (
    "No suggestions were attached. Work from the barista's notes alone, "
    "and if they ask for nothing a profile can change, change nothing."
)


class ProfileDraftService:
    """The one entry point. Held on ``app.state.drafts``."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        settings: SettingsService,
        *,
        proposals: DraftProposals | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.settings = settings
        #: Where every draft document is built and stored. The same object the
        #: chat's tools are handed, which is why it holds no connection.
        self.proposals = proposals or DraftProposals(db, settings)
        self.drafts = ProfileDraftsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)

    # ── the policy ───────────────────────────────────────────────────

    async def bounds(self) -> PolicyBounds:
        """The safety bounds as currently configured, read per call."""
        return await self.proposals.bounds()

    async def preview(self, base_version_id: int, document: dict[str, Any]) -> DraftPreview:
        """Validate a document the editor has not saved yet. Never raises.

        Live validation in a JSON editor has to answer "what is wrong with what
        I have typed so far", and the three answers are different problems with
        different fixes: it is not a profile, it is a profile the policy
        refuses, or it is a profile the policy would move. So this returns all
        three rather than raising on the first.
        """
        base = await self.proposals.base_profile(base_version_id)
        try:
            candidate = Profile.model_validate(document)
        except ValidationError as exc:
            return DraftPreview(valid=False, schema_errors=schema_errors(exc))
        bounds = await self.bounds()
        clamped, changes = clamp(candidate, bounds)
        violations = check(clamped, bounds)
        final = clamped.for_new_device_profile(label=with_app_suffix(clamped.label))
        return DraftPreview(
            valid=not violations,
            violations=violations,
            clamp_changes=changes,
            stop_condition_changes=diff_stop_conditions(base, final),
            profile=final.to_device(),
        )

    # ── creating drafts ──────────────────────────────────────────────

    async def create_manual(
        self,
        *,
        base_version_id: int,
        document: dict[str, Any],
        change_summary: str = "",
        notes: str = "",
    ) -> ProfileDraftRow:
        """A draft somebody typed. Same four layers, no model involved."""
        return await self.proposals.create_manual(
            base_version_id=base_version_id,
            document=document,
            change_summary=change_summary,
            notes=notes,
        )

    async def generate(
        self,
        *,
        base_version_id: int,
        notes: str = "",
        parent_draft_id: int | None = None,
        model: str = "",
    ) -> ProfileDraftRow:
        """Ask the model for an edited profile, then put it through the layers.

        A provider failure raises rather than storing a `failed` draft, which is
        the opposite of what a review does and deliberately so: a review row is
        the handle a page is already rendering, whereas a draft that was
        never drafted is nothing — there is no document, no diff and nothing to
        approve. The caller gets the LLM layer's own message and a button that
        says "try again".
        """
        base = await self.proposals.base_profile(base_version_id)
        parent = await self._parent_draft(parent_draft_id)
        is_new = parent.is_new if parent is not None else False
        rendered = await self._render(base=base, notes=notes, parent=parent, is_new=is_new)
        result = await self.llm.call_json(
            LlmRequest(
                messages=rendered.messages(),
                output_model=DraftedProfile,
                model=model,
                purpose="draft",
                label="draft profile",
                # A new profile's base is only the one it is stored against.
                subject=(parent.draft_label or base.label) if is_new and parent else base.label,
                prompt_name=DRAFT_PROMPT,
                prompt_version=rendered.version,
            )
        )
        if not isinstance(result, Ok):
            raise ServiceUnavailable(
                f"The model could not draft a profile: {result.code}: {result.message}",
                details={"code": result.code},
            )
        prepared: PreparedDraft = await self.proposals.prepare(
            base, result.data.profile, is_new=is_new
        )
        row = await self.proposals.store(
            base_version_id=base_version_id,
            prepared=prepared,
            change_summary=result.data.change_summary,
            notes=notes,
            parent_draft_id=parent_draft_id,
            # A refinement is the next attempt at the same idea, so it is an
            # attempt on the same experiment: the Set it was made for and the
            # prediction it owes come across from the draft it supersedes.
            # Dropping them would quietly turn a Set's second attempt into a
            # draft belonging to nothing, and the push would record no
            # prediction on the version it creates.
            set_id=parent.set_id if parent is not None else None,
            prediction=parent.prediction if parent is not None else "",
            compares_to_version_id=(parent.compares_to_version_id if parent is not None else None),
            # And so does the agent's suggestion that it is a major version:
            # refining the same idea does not make it a smaller change.
            suggest_major=parent.suggest_major if parent is not None else False,
            major_reason=parent.major_reason if parent is not None else "",
            is_new=is_new,
        )
        if parent is not None:
            await self.drafts.supersede(parent.id)
        return row

    async def refine(self, draft_id: int, *, notes: str, model: str = "") -> ProfileDraftRow:
        """A new draft from an existing one, with something more to go on.

        The parent is superseded rather than edited. A draft is immutable for
        the same reason a profile version is: the advice that produced each
        attempt is what makes the attempts worth reading, and an edit in place
        would leave a summary describing a document that no longer exists.
        """
        parent = await self._require(draft_id)
        if parent.status in ("pushed", "superseded"):
            raise Conflict(
                f"That draft is {parent.status} and cannot be refined; "
                "start a new draft from the profile you want to change."
            )
        combined = "\n".join(part for part in (parent.notes, notes) if part.strip())
        return await self.generate(
            base_version_id=parent.base_version_id,
            notes=combined,
            parent_draft_id=parent.id,
            model=model,
        )

    # ── discarding ───────────────────────────────────────────────────

    async def discard(self, draft_id: int) -> ProfileDraftRow:
        """Turn a draft down. Refused once it is on a machine.

        A pushed draft's profile exists on the display, and a row that said
        `discarded` while the file was still there would be the archive lying
        about the machine. Delete the profile from the board, or go back to its
        previous version, and the sync that takes it off the machine discards the
        draft with it.
        """
        draft = await self._require(draft_id)
        if draft.status == "pushed":
            raise Conflict(
                "That draft's profile is on the machine. Delete it from the board, or go back "
                "to its previous version, before discarding the draft."
            )
        return _require_row(await self.drafts.set_status(draft_id, "discarded"), draft_id)

    async def attach_to_set(
        self, draft: ProfileDraftRow, set_id: int, *, major: bool | None = None
    ) -> SetVersionRow | None:
        """Record a new Set version pointing at what was just pushed.

        Optional and opt-in: a person may push a draft to try it without saying
        that this is now what the Set means. When they do say so, the version
        carries both identities — the content-hashed `profile_version_id` and
        the device id the firmware assigned — because "what did this Set brew"
        and "which profile on the machine is that" are different questions.

        **And it carries the prediction, if this is the Set the draft was made
        for.** A profile change argued in a Set's conversation is a change to
        that experiment and owes the same falsifiable guess a proposed grind
        change owes; this is the moment it becomes one, because until the person
        pushed it nothing about the Set had changed. Pushed for a different Set
        the prediction is left off — it was about the other experiment and says
        nothing about this one — and a draft nobody predicted anything about
        records what it always did.

        **Its name** is a minor version unless the person marks it major: a
        pushed draft is a tuned copy of a profile, which is dialling in
        (``path="draft"`` in :func:`~gaggiclanker.domain.sets.change_is_major`).
        The agent's suggestion only preselects the box on the card.
        """
        if draft.draft_version_id is None:  # pragma: no cover - a pushed draft has one
            return None
        experiment = await self._experiment(draft, set_id)
        # Who proposed this recipe, which is what "did following the advice
        # help" is a GROUP BY on. A draft made from an analysis before the
        # analysis was retired still records it. Otherwise it is
        # `chat` exactly when this push is recording the agent's own prediction
        # — the draft was argued in this Set's conversation and is being pushed
        # for that Set — because a profile change the agent proposed filed under
        # `manual` would credit the person with the model's idea.
        origin = (
            "analysis"
            if draft.source_analysis_id is not None
            else ("chat" if experiment else "manual")
        )
        return await self.sets.add_version(
            set_id,
            SetVersionPatch.model_validate(
                {
                    "profile_version_id": draft.draft_version_id,
                    "pushed_device_profile_id": draft.pushed_device_profile_id,
                    "origin": origin,
                    "origin_analysis_id": draft.source_analysis_id,
                    "intent": draft.change_summary[:500],
                    **experiment,
                }
            ),
            # A pushed draft is a tuned copy of a profile — dialling in — so its
            # default is a minor version; the person can say otherwise.
            major=major,
            path="draft",
        )

    async def _experiment(self, draft: ProfileDraftRow, set_id: int) -> dict[str, Any]:
        """The prediction this push records on the Set version, if it records one.

        Nothing unless the draft was made for **this** Set and carries a
        prediction. When it does, the comparison is the version the prediction
        named, and the current version when that one is no longer a version of
        this Set — a comparison against something that is not there would leave
        the log rendering a dangling reference. Sent explicitly either way, so
        `add_version` never quietly substitutes its own default.
        """
        if draft.set_id != set_id or not draft.prediction:
            return {}
        compares_to = draft.compares_to_version_id
        if compares_to is not None:
            if await self.sets.version_of_set(set_id, compares_to) is None:
                compares_to = None
        if compares_to is None:
            current = await self.sets.current_version(set_id)
            compares_to = current.id if current is not None else None
        return {"prediction": draft.prediction, "compares_to_version_id": compares_to}

    # ── reading ──────────────────────────────────────────────────────

    async def detail(self, draft_id: int) -> ProfileDraftDetail:
        draft = await self._require(draft_id)
        base = await self.profiles.get_version(draft.base_version_id)
        drafted = (
            await self.profiles.get_version(draft.draft_version_id)
            if draft.draft_version_id is not None
            else None
        )
        return ProfileDraftDetail(
            draft=draft,
            is_new=draft.is_new,
            base_profile=None if draft.is_new or base is None else base.profile,
            draft_profile=drafted.profile if drafted else None,
        )

    # ── internals ────────────────────────────────────────────────────

    async def _require(self, draft_id: int) -> ProfileDraftRow:
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        return draft

    async def _parent_draft(self, draft_id: int | None) -> ProfileDraftRow | None:
        return None if draft_id is None else await self._require(draft_id)

    async def _render(
        self,
        *,
        base: Profile,
        notes: str,
        parent: ProfileDraftRow | None,
        is_new: bool = False,
    ) -> RenderedPrompt:
        previous = "This is a first draft; there is no previous one."
        if parent is not None and parent.draft_version_id is not None:
            version = await self.profiles.get_version(parent.draft_version_id)
            if version is not None:
                previous = (
                    f"{parent.change_summary}\n\n{json.dumps(version.profile, indent=2)}"
                    if version.profile
                    else parent.change_summary
                )
        return await self.prompts.load(
            DRAFT_PROMPT,
            {
                # A new profile is not on the machine, and its stored base is only a stand-in:
                # the draft being refined is the document to work from.
                "current_profile": NEW_PROFILE_NOT_ON_MACHINE
                if is_new
                else json.dumps(base.to_device(), indent=2),
                # Nothing passes advice along any more; the variable is still
                # filled because a person's edited copy of this prompt may name
                # it, and an undefined variable refuses to render.
                "suggestions": NO_ADVICE,
                "previous_draft": previous,
                "barista_notes": notes.strip() or "They said nothing beyond the advice above.",
                "policy_bounds": _render_bounds(await self.bounds()),
            },
        )


#: What the prompt's "profile on the machine now" says for a profile that is new.
NEW_PROFILE_NOT_ON_MACHINE = (
    "There is none: this is a new profile that is not on the machine. The previous draft below "
    "is the profile you are refining; return it whole, with only the edits asked for."
)


def _render_bounds(bounds: PolicyBounds) -> str:
    """The policy as a list a model can read, from the same dataclass that enforces it."""
    return "\n".join(
        [
            f"  - temperature: {bounds.temperature_min_c:g}-{bounds.temperature_max_c:g} °C",
            f"  - pump pressure: {bounds.pressure_min_bar:g}-{bounds.pressure_max_bar:g} bar",
            f"  - pump flow: {bounds.flow_min_ml_s:g}-{bounds.flow_max_ml_s:g} ml/s",
            f"  - phase duration: {bounds.phase_duration_min_s:g}-"
            f"{bounds.phase_duration_max_s:g} s",
            f"  - at most {bounds.max_phases} phases",
            "  - a transition is never longer than the phase it ramps into",
        ]
    )


def _require_row(row: ProfileDraftRow | None, draft_id: int) -> ProfileDraftRow:
    if row is None:  # pragma: no cover - the update above guarantees it
        raise NotFound(f"No profile draft {draft_id}")
    return row
