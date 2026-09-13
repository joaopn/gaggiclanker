"""Draft a profile, approve it, put it on the machine, and check that it stuck.

The shape of the whole feature is five methods and one invariant.

    generate / create_manual  ->  approve  ->  push  ->  (verified | rollback)

**The invariant: every document that leaves this module has been through
:func:`~gaggiclanker.domain.profile_policy.enforce`.** There is exactly one
place a profile is built (:meth:`ProfileDraftService._prepare`) and it clamps,
re-checks and applies the label suffix before anything is stored. A caller
cannot construct a draft that skipped a layer, because there is no other
constructor.

Three decisions worth knowing before reading the code:

**The suffix is applied at draft time, not at push time.** crema appends "[AI]"
as it saves. Doing it earlier means the document a person approves, the document
that goes on the wire and the document the round trip compares against are one
document with one content hash — and the diff shows the rename, which is
honest, because the profile on the machine really will be called that.

**A push never selects and never overwrites.** It saves a *new* profile beside
what the person is brewing with. `save_profile` refuses a document carrying an
id at all, so "never overwrite" is a property of the client rather than a habit
of this service.

**A mismatch is a stored `failed` row with both documents, not an exception.**
"The machine says it saved it" is not the same as "the machine stored what we
sent", and the difference is only visible by reading it back. When they differ,
the draft keeps the evidence and offers one button that deletes the machine's
copy — because the alternative is a profile on somebody's display that nobody
can account for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.analyses import AnalysesRepository, SuggestionsRepository
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.db.repos.profile_drafts import (
    ProfileDraftRow,
    ProfileDraftsRepository,
    ProfileDraftWrite,
)
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionRow
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.domain.models import (
    Profile,
    canonical_profile_json,
    with_app_suffix,
)
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
from gaggiclanker.drafts.models import DraftedProfile, DraftPreview, ProfileDraftDetail
from gaggiclanker.infra.errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from gaggiclanker.llm.prompts import PromptService, RenderedPrompt
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmRequest, Ok
from gaggiclanker.settings_service import SettingsService

__all__ = ["DRAFT_PROMPT", "ProfileDraftService"]

log = structlog.get_logger(__name__)

#: The prompt one draft renders. One file rather than the analysis's two,
#: because the facts here are four short blocks rather than a page of context
#: whose layout is worth editing separately from the persona.
DRAFT_PROMPT = "draft"

#: What the SSE bus carries when a draft moves. The Drafts queue watches it, so
#: a push started in one tab updates the list in another.
DRAFT_EVENT = "draft.updated"


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A document that has been through every offline layer. The only way in."""

    profile: Profile
    clamp_changes: list[PolicyChange]
    stop_condition_changes: list[StopConditionChange]


class ProfileDraftService:
    """The one entry point. Held on ``app.state.drafts``."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        settings: SettingsService,
        *,
        client: GaggimateClient | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.settings = settings
        self.client = client
        self.drafts = ProfileDraftsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.analyses = AnalysesRepository(db)
        self.suggestions = SuggestionsRepository(db)
        self.sets = SetsRepository(db)
        self.writes = DeviceWritesRepository(db)

    # ── the policy ───────────────────────────────────────────────────

    async def bounds(self) -> PolicyBounds:
        """The safety bounds as currently configured.

        Read per call rather than cached: the bounds are in the settings
        registry precisely so somebody can widen one and try again without a
        restart, and a service holding a copy from boot would make that a lie.
        """
        resolved = await self.settings.resolve_all()
        return bounds_from({key: setting.value for key, setting in resolved.items()})

    async def _prepare(self, base: Profile, candidate: Profile) -> _Prepared:
        """Clamp, re-check, suffix the label, diff the stop conditions.

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
        document = clamped.for_new_device_profile(label=with_app_suffix(clamped.label))
        return _Prepared(
            profile=document,
            clamp_changes=changes,
            stop_condition_changes=diff_stop_conditions(base, document),
        )

    async def preview(self, base_version_id: int, document: dict[str, Any]) -> DraftPreview:
        """Validate a document the editor has not saved yet. Never raises.

        Live validation in a JSON editor has to answer "what is wrong with what
        I have typed so far", and the three answers are different problems with
        different fixes: it is not a profile, it is a profile the policy
        refuses, or it is a profile the policy would move. So this returns all
        three rather than raising on the first.
        """
        base = await self._base_profile(base_version_id)
        try:
            candidate = Profile.model_validate(document)
        except ValidationError as exc:
            return DraftPreview(valid=False, schema_errors=_schema_errors(exc))
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
        base = await self._base_profile(base_version_id)
        try:
            candidate = Profile.model_validate(document)
        except ValidationError as exc:
            raise Unprocessable(
                "That document is not a valid GaggiMate profile",
                details={"schema_errors": _schema_errors(exc)},
            ) from None
        prepared = await self._prepare(base, candidate)
        return await self._store(
            base_version_id=base_version_id,
            prepared=prepared,
            change_summary=change_summary or "Edited by hand.",
            notes=notes,
        )

    async def generate(
        self,
        *,
        base_version_id: int,
        analysis_id: int | None = None,
        suggestion_id: int | None = None,
        notes: str = "",
        parent_draft_id: int | None = None,
        model: str = "",
    ) -> ProfileDraftRow:
        """Ask the model for an edited profile, then put it through the layers.

        A provider failure raises rather than storing a `failed` draft, which is
        the opposite of what an analysis does and deliberately so: an analysis
        row is the handle a page is already rendering, whereas a draft that was
        never drafted is nothing — there is no document, no diff and nothing to
        approve. The caller gets the LLM layer's own message and a button that
        says "try again".
        """
        base = await self._base_profile(base_version_id)
        parent = await self._parent_draft(parent_draft_id)
        rendered = await self._render(
            base=base,
            analysis_id=analysis_id,
            suggestion_id=suggestion_id,
            notes=notes,
            parent=parent,
        )
        result = await self.llm.call_json(
            LlmRequest(
                messages=rendered.messages(),
                output_model=DraftedProfile,
                model=model,
                purpose="draft",
                label="draft profile",
                subject=base.label,
                prompt_name=DRAFT_PROMPT,
                prompt_version=rendered.version,
            )
        )
        if not isinstance(result, Ok):
            raise ServiceUnavailable(
                f"The model could not draft a profile: {result.code}: {result.message}",
                details={"code": result.code},
            )
        prepared = await self._prepare(base, result.data.profile)
        row = await self._store(
            base_version_id=base_version_id,
            prepared=prepared,
            change_summary=result.data.change_summary,
            notes=notes,
            analysis_id=analysis_id,
            suggestion_id=suggestion_id,
            parent_draft_id=parent_draft_id,
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
            analysis_id=parent.source_analysis_id,
            suggestion_id=parent.source_suggestion_id,
            notes=combined,
            parent_draft_id=parent.id,
            model=model,
        )

    async def _store(
        self,
        *,
        base_version_id: int,
        prepared: _Prepared,
        change_summary: str,
        notes: str,
        analysis_id: int | None = None,
        suggestion_id: int | None = None,
        parent_draft_id: int | None = None,
    ) -> ProfileDraftRow:
        version, _ = await self.profiles.ensure_version(prepared.profile, source="draft")
        return await self.drafts.create(
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
                source_analysis_id=analysis_id,
                source_suggestion_id=suggestion_id,
                parent_draft_id=parent_draft_id,
                change_summary=change_summary,
                stop_condition_changes=[
                    change.model_dump(mode="json") for change in prepared.stop_condition_changes
                ],
                clamp_changes=[change.model_dump(mode="json") for change in prepared.clamp_changes],
                notes=notes,
            )
        )

    # ── approving ────────────────────────────────────────────────────

    async def approve(self, draft_id: int, *, acknowledge_stop_changes: bool) -> ProfileDraftRow:
        """Mark a draft ready to push. Refuses an unacknowledged yield change.

        crema's rule, and the one place in this feature where the software
        insists on a human sentence rather than a human click: a draft that
        moves a stop condition changes how much coffee ends up in the cup, and
        the person has to say they know that. The list acknowledged is the list
        stored on the row, which is the list the UI rendered — recomputing it
        here would let a policy change between draft and approval quietly alter
        what was agreed to.
        """
        draft = await self._require(draft_id)
        if draft.status not in ("draft", "approved"):
            raise Conflict(f"A {draft.status} draft cannot be approved")
        changes = draft.stop_condition_changes or []
        if changes and not acknowledge_stop_changes:
            raise Conflict(
                "This draft changes when the machine stops pumping, which changes how much "
                "coffee ends up in the cup. Approve it again with acknowledge_stop_changes "
                "to confirm you meant that.",
                details={
                    "field": "acknowledge_stop_changes",
                    "stop_condition_changes": changes,
                },
            )
        return _require_row(
            await self.drafts.set_status(
                draft_id, "approved", acknowledged=bool(changes and acknowledge_stop_changes)
            ),
            draft_id,
        )

    async def discard(self, draft_id: int) -> ProfileDraftRow:
        """Turn a draft down. Refused once it is on a machine.

        A pushed draft's profile exists on the display, and a row that said
        `discarded` while the file was still there would be the archive lying
        about the machine. Roll it back first, or leave it alone.
        """
        draft = await self._require(draft_id)
        if draft.status == "pushed":
            raise Conflict(
                "That draft is on the machine. Delete the profile from the display, or use "
                "rollback, before discarding the draft."
            )
        return _require_row(await self.drafts.set_status(draft_id, "discarded"), draft_id)

    # ── pushing ──────────────────────────────────────────────────────

    async def push(
        self, draft_id: int, *, set_id: int | None = None, allow_stale_base: bool = False
    ) -> tuple[ProfileDraftRow, SetVersionRow | None]:
        """Save the draft to the machine as a new profile, then read it back.

        Four things happen and the order matters:

        1. `save_profile` — which refuses unless `deviceWritesEnabled` is on,
           refuses a document carrying an id, and leaves an audit row either way;
        2. `load_profile` on the id the firmware assigned — the round trip;
        3. canonical JSON comparison, which tolerates exactly the fields
           `writeProfile` adds for free (`id`, `transition.target`, `favorite`,
           `selected`, a spelled-out phase `temperature` of 0);
        4. the mirror, so `/api/profiles` shows the new profile without waiting
           for the next fifteen-minute sweep.

        A mismatch at 3 is a stored `failed` row carrying both documents, not an
        exception: the profile is on the machine either way and the person needs
        the evidence and the rollback button, not a stack trace.

        Before any of it, one refusal that is not about the document at all: a
        draft whose base profile has been edited on the display since is
        **stale**, and pushing it would silently propose undoing that edit.
        ``allow_stale_base`` is the deliberate override.
        """
        draft = await self._require(draft_id)
        if draft.status != "approved":
            raise Conflict(
                f"A {draft.status} draft cannot be pushed; approve it first."
                if draft.status == "draft"
                else f"A {draft.status} draft cannot be pushed."
            )
        if (draft.stop_condition_changes or []) and not draft.acknowledged_stop_changes:
            raise Conflict(
                "This draft's stop conditions were never acknowledged. Approve it again with "
                "acknowledge_stop_changes."
            )
        if not draft.base_is_current and not allow_stale_base:
            # The profile this was derived from has been edited on the display
            # since. The diff a person approved is a diff against something that
            # no longer exists, and pushing it silently proposes undoing whatever
            # they changed there. Refused rather than merged: this box does not
            # get to decide which of the two edits was meant.
            raise Conflict(
                f"The profile this was drafted from has changed on the machine since "
                f"(device profile {draft.base_device_profile_id!r}). The diff you approved is "
                "against a version the display no longer holds. Draft again from the current "
                "profile, or push anyway with allow_stale_base.",
                details={
                    "field": "allow_stale_base",
                    "base_version_id": draft.base_version_id,
                    "base_device_profile_id": draft.base_device_profile_id,
                },
            )
        client = self._require_client()
        profile = await self._draft_profile(draft)

        stored = await client.save_profile(profile)
        device_id = stored.id or ""
        try:
            served = await client.load_profile(device_id)
        except Exception as exc:
            # The save was acknowledged and we cannot read it back. That is the
            # same class of unknown as a mismatch, and it gets the same
            # treatment: a `failed` row naming a device id, so the rollback
            # button has something to delete.
            failed = await self.drafts.set_status(
                draft_id,
                "failed",
                pushed_device_profile_id=device_id,
                error=f"saved, but could not be read back: {exc}",
                verification={"sent": profile.to_device(), "loaded": None},
            )
            return _require_row(failed, draft_id), None

        sent_canonical = canonical_profile_json(profile)
        loaded_canonical = canonical_profile_json(served)
        if sent_canonical != loaded_canonical:
            log.warning(
                "profile_push_mismatch", draft_id=draft_id, device_id=device_id, host=client.host
            )
            failed = await self.drafts.set_status(
                draft_id,
                "failed",
                pushed_device_profile_id=device_id,
                error="the machine stored something other than what was sent",
                verification={
                    "sent": profile.to_device(),
                    "loaded": served.to_device(),
                    "sent_canonical": json.loads(sent_canonical),
                    "loaded_canonical": json.loads(loaded_canonical),
                },
            )
            return _require_row(failed, draft_id), None

        await self._mirror(device_id, served)
        row = _require_row(
            await self.drafts.set_status(
                draft_id,
                "pushed",
                pushed_device_profile_id=device_id,
                verification={"sent": profile.to_device(), "loaded": served.to_device()},
            ),
            draft_id,
        )
        version = await self.attach_to_set(row, set_id) if set_id is not None else None
        log.info("profile_pushed", draft_id=draft_id, device_id=device_id, label=profile.label)
        return row, version

    async def rollback(self, draft_id: int) -> ProfileDraftRow:
        """Take a pushed profile back off the machine.

        One click, because the alternative is a profile on somebody's display
        that nobody can account for. The delete goes through the same guard as
        any other: the label has to carry the suffix and the audit has to say we
        created that id — which it does, because the save that created it is the
        row immediately before this one.

        **Where the draft ends up depends on where it was**, and the rule is
        that the row must never describe a machine state that is not true:

        * `failed` stays `failed`. What happened, happened, and the verification
          evidence is the reason the row is worth keeping.
        * `pushed` becomes `discarded`. Continuing to say `pushed` would be the
          archive claiming a profile is on the display when it is not — and it
          was also a dead end, because `discard` refuses a pushed draft, so the
          draft could never reach a terminal state at all.

        Either way the device id is cleared from the draft, from the profile
        mirror and from any Set version that named it: a version pointing at a
        file the machine no longer has would resolve to whatever inherits that
        id next.
        """
        draft = await self._require(draft_id)
        if draft.status not in ("failed", "pushed"):
            raise Conflict(
                f"A {draft.status} draft has nothing on the machine to roll back; "
                "only a pushed or failed draft does."
            )
        device_id = draft.pushed_device_profile_id
        if not device_id:
            raise Conflict("That draft has nothing on the machine to roll back")
        client = self._require_client()
        await client.delete_profile(device_id)
        await self.profiles.mark_one_deleted(device_id)
        await self.sets.clear_pushed_device_profile(device_id)
        if draft.status == "pushed":
            await self.drafts.set_status(draft_id, "discarded")
        return _require_row(await self.drafts.clear_pushed_profile(draft_id), draft_id)

    async def attach_to_set(self, draft: ProfileDraftRow, set_id: int) -> SetVersionRow | None:
        """Record a new Set version pointing at what was just pushed.

        Optional and opt-in: a person may push a draft to try it without saying
        that this is now what the Set means. When they do say so, the version
        carries both identities — the content-hashed `profile_version_id` and
        the device id the firmware assigned — because "what did this Set brew"
        and "which profile on the machine is that" are different questions.
        """
        if draft.draft_version_id is None:  # pragma: no cover - a pushed draft has one
            return None
        origin = "analysis" if draft.source_analysis_id is not None else "manual"
        return await self.sets.add_version(
            set_id,
            SetVersionPatch(
                profile_version_id=draft.draft_version_id,
                pushed_device_profile_id=draft.pushed_device_profile_id,
                origin=origin,  # type: ignore[arg-type]
                origin_analysis_id=draft.source_analysis_id,
                intent=draft.change_summary[:500],
            ),
        )

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
            base_profile=base.profile if base else None,
            draft_profile=drafted.profile if drafted else None,
        )

    # ── internals ────────────────────────────────────────────────────

    def _require_client(self) -> GaggimateClient:
        if self.client is None:
            raise ServiceUnavailable(
                "No machine is configured, so there is nowhere to push this. Set gaggimateHost "
                "in Settings."
            )
        return self.client

    async def _require(self, draft_id: int) -> ProfileDraftRow:
        draft = await self.drafts.get(draft_id)
        if draft is None:
            raise NotFound(f"No profile draft {draft_id}")
        return draft

    async def _parent_draft(self, draft_id: int | None) -> ProfileDraftRow | None:
        return None if draft_id is None else await self._require(draft_id)

    async def _base_profile(self, version_id: int) -> Profile:
        version = await self._require_version(version_id)
        return _profile_from_version(version)

    async def _require_version(self, version_id: int) -> ProfileVersionRow:
        version = await self.profiles.get_version(version_id)
        if version is None:
            raise NotFound(f"No profile version {version_id}")
        return version

    async def _draft_profile(self, draft: ProfileDraftRow) -> Profile:
        if draft.draft_version_id is None:
            raise Conflict("That draft has no document to push")
        return _profile_from_version(await self._require_version(draft.draft_version_id))

    async def _mirror(self, device_id: str, served: Profile) -> None:
        """Put the pushed profile into the archive's own mirror straight away.

        Without this the new profile is invisible until the profiles loop next
        runs, which is up to fifteen minutes of a person looking at a Profiles
        page that does not list the thing they just pushed.

        `ensure_version` keys on the content hash, so this finds the row the
        draft already created rather than inserting a second one — the document
        is the same document, and that is the whole point of hashing it.
        """
        version, _ = await self.profiles.ensure_version(
            served, source="draft", device_json=json.dumps(served.to_device())
        )
        await self.profiles.upsert_device_profile(
            device_id=device_id,
            version_id=version.id,
            # The firmware auto-favourites every new profile; the mirror says
            # what the machine says rather than what we would have chosen.
            favorite=served.favorite,
            selected=False,
        )

    async def _render(
        self,
        *,
        base: Profile,
        analysis_id: int | None,
        suggestion_id: int | None,
        notes: str,
        parent: ProfileDraftRow | None,
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
                "current_profile": json.dumps(base.to_device(), indent=2),
                "suggestions": await self._advice(analysis_id, suggestion_id),
                "previous_draft": previous,
                "barista_notes": notes.strip() or "They said nothing beyond the advice above.",
                "policy_bounds": _render_bounds(await self.bounds()),
            },
        )

    async def _advice(self, analysis_id: int | None, suggestion_id: int | None) -> str:
        """The advice block, assembled from whichever handle the caller had.

        Two entry points because the UI has two buttons: "draft from this
        analysis" reads the whole `profile_patch`, and "draft from this
        suggestion" reads one row. Both end up as prose, because that is what a
        model reads, and both name the field they came from so a reader of the
        stored prompt can tell which button was pressed.
        """
        lines: list[str] = []
        if analysis_id is not None:
            analysis = await self.analyses.get(analysis_id)
            if analysis is None:
                raise NotFound(f"No analysis {analysis_id}")
            output = analysis.output or {}
            for patch in output.get("profile_patch") or []:
                lines.append(
                    f"- phase {patch.get('phase_index')}, {patch.get('field')}: "
                    f"{patch.get('from', '?')} -> {patch.get('to', '?')}"
                    + (f" — {patch['reason']}" if patch.get("reason") else "")
                )
            for suggestion in analysis.suggestions:
                if suggestion.variable in ("pressure", "flow", "preinfusion", "profile"):
                    lines.append(f"- {_suggestion_line(suggestion)}")
            diagnosis = str(output.get("diagnosis") or "").strip()
            if diagnosis:
                lines.append(f"\nThe diagnosis this came from: {diagnosis}")
        if suggestion_id is not None:
            asked = await self.suggestions.get(suggestion_id)
            if asked is None:
                raise NotFound(f"No suggestion {suggestion_id}")
            lines.insert(0, f"- {_suggestion_line(asked)}")
        if not lines:
            return (
                "No analysis suggestions were attached. Work from the barista's notes alone, "
                "and if they ask for nothing a profile can change, change nothing."
            )
        return "\n".join(lines)


def _suggestion_line(suggestion: Any) -> str:
    magnitude = "" if suggestion.magnitude is None else f" by {suggestion.magnitude:g}"
    unit = "" if suggestion.unit in ("", "none") else f" {suggestion.unit}"
    reason = f" — {suggestion.reason}" if suggestion.reason else ""
    return f"{suggestion.variable} {suggestion.direction}{magnitude}{unit}{reason}"


def _profile_from_version(version: ProfileVersionRow) -> Profile:
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


def _schema_errors(exc: ValidationError) -> list[str]:
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
