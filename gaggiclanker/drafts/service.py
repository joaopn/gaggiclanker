"""Draft a profile, approve it, put it on the machine, and check that it stuck.

The shape of the whole feature is five methods and one invariant.

    generate / create_manual  ->  approve  ->  push  ->  (verified | rollback)

**The invariant: every document that leaves this module has been through
:func:`~gaggiclanker.domain.profile_policy.enforce`.** There is exactly one
place a profile is built (:meth:`.proposals.DraftProposals.prepare`) and it
clamps, re-checks and applies the label suffix before anything is stored. A
caller cannot construct a draft that skipped a layer, because there is no other
constructor. That half lives in its own class, built without the machine
connection, so the chat's tools can propose a draft without holding anything
that could push one.

Three decisions worth knowing before reading the code:

**The suffix is applied at draft time, not at push time.** crema appends "[AI]"
as it saves. Doing it earlier means the document a person approves, the document
that goes on the wire and the document the round trip compares against are one
document with one content hash — and the diff shows the rename, which is
honest, because the profile on the machine really will be called that.

**A push never overwrites, and replaces only what this app wrote.** It saves a
*new* profile (or reuses an identical one already on the machine), verifies it,
and only then removes the profile it supersedes, within one lineage (a Set's current
version, or the draft's base under the same label): when this box saved that id, the
label carries the app suffix, a fresh read shows it still holds what the archive
recorded, and no other Set or pushed draft stands on it. A person's own profile is
added beside, never deleted; the
favourite star and the selection move to the new profile first, so the display
looks the same to whoever stands at it. `save_profile` refuses a document
carrying an id at all, so "never overwrite" is a property of the client rather
than a habit of this service.

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
from typing import Any, Literal

import structlog
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.device_writes import DeviceWritesRepository, DeviceWriteWrite
from gaggiclanker.db.repos.profile_board import ProfileBoardRepository
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow, ProfileDraftsRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository, ProfileVersionRow
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionRow
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.connection import DeviceConnection, machine_operation
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.device.writes import DeviceWriteRefused
from gaggiclanker.domain.models import (
    Profile,
    profile_content_hash,
    with_app_suffix,
)
from gaggiclanker.domain.profile_policy import (
    PolicyBounds,
    check,
    clamp,
    diff_stop_conditions,
)
from gaggiclanker.drafts.gate import DISABLED_MESSAGE
from gaggiclanker.drafts.machine import (
    GONE,
    NOT_OURS,
    MachineState,
    Placed,
    Removal,
    can_remove,
    place,
    read_machine,
    remove_if_ours,
)
from gaggiclanker.drafts.models import DraftedProfile, DraftPreview, ProfileDraftDetail
from gaggiclanker.drafts.proposals import (
    DraftProposals,
    PreparedDraft,
    profile_from_version,
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

#: What the SSE bus carries when a draft moves. The Drafts queue watches it, so
#: a push started in one tab updates the list in another.
DRAFT_EVENT = "draft.updated"


class ProfileDraftService:
    """The one entry point. Held on ``app.state.drafts``."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        settings: SettingsService,
        *,
        connection: DeviceConnection[Any] | None = None,
        proposals: DraftProposals | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        self.settings = settings
        #: The app's machine connection. Its client is read when a push or a
        #: rollback happens, never kept: a settings change rebuilds it live.
        self.connection = connection
        #: Where every draft document is built and stored. The same object the
        #: chat's tools are handed, which is why it holds no connection.
        self.proposals = proposals or DraftProposals(db, settings)
        self.drafts = ProfileDraftsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)
        self.writes = DeviceWritesRepository(db)
        self.board = ProfileBoardRepository(db)

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
        rendered = await self._render(base=base, notes=notes, parent=parent)
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
        prepared: PreparedDraft = await self.proposals.prepare(base, result.data.profile)
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
        self,
        draft_id: int,
        *,
        set_id: int | None = None,
        allow_stale_base: bool = False,
        major: bool | None = None,
    ) -> tuple[ProfileDraftRow, SetVersionRow | None]:
        """Save the draft to the machine as a new profile, then read it back.

        Five things happen and the order matters:

        0. the machine's profiles are listed and loaded, and a profile already
           holding this canonical content is used as it is (nothing is saved);
        1. `save_profile` — which refuses unless `deviceWritesEnabled` is on,
           refuses a document carrying an id, and leaves an audit row either way;
        2. `load_profile` on the id the firmware assigned — the round trip;
        3. canonical JSON comparison, which tolerates exactly the fields
           `writeProfile` adds for free (`id`, `transition.target`, `favorite`,
           `selected`, a spelled-out phase `temperature` of 0);
        4. the predecessor (what the push is a new version of, see
           :meth:`_predecessor`) is removed, if it is this app's and unchanged,
           after the favourite star and the selection have
           moved to the new profile; and the mirror is updated, so
           `/api/profiles` shows the result without waiting for the next
           fifteen-minute sweep.

        A mismatch at 3 is a stored `failed` row carrying both documents, not an
        exception: the profile is on the machine either way and the person needs
        the evidence and the rollback button, not a stack trace.

        Before any of it, one refusal that is not about the document at all: a
        draft whose base profile has been edited on the display since is
        **stale**, and pushing it would silently propose undoing that edit.
        ``allow_stale_base`` is the deliberate override.

        ``major`` only matters with ``set_id``: whether the version the push
        records on that Set is a major one. Left out, a pushed draft is a
        minor version (it tunes a profile); the person's answer wins.
        """
        draft = await self._require(draft_id)
        await self._refuse_once_on_the_board()
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
        # Registered with the connection for the whole read, save, replace and
        # mirror: a settings change that would rebuild the connection is
        # refused until the push has finished, rather than cutting it between
        # the save and the verification.
        async with machine_operation(self.connection, "a profile push") as maybe_client:
            client = _require_client(maybe_client)
            # Before the machine is read at all: with writes off a push must refuse
            # exactly as a save would, audited, and not leak a read of the whole machine.
            await self._require_writes_on(client, "profile_save", None)
            machine = await read_machine(client)
            base = await self._require_version(draft.base_version_id)
            if not allow_stale_base and self._base_is_stale(draft, base, machine):
                # The profile this was derived from has been edited on the display
                # since. The diff a person approved is a diff against something that
                # no longer exists, and pushing it silently proposes undoing whatever
                # they changed there. Refused rather than merged: this box does not
                # get to decide which of the two edits was meant.
                raise Conflict(
                    f"The profile this was drafted from has changed on the machine since "
                    f"(device profile {draft.base_device_profile_id!r}). The diff you approved "
                    "is against a version the display no longer holds. Draft again from the "
                    "current profile, or push anyway with allow_stale_base.",
                    details={
                        "field": "allow_stale_base",
                        "base_version_id": draft.base_version_id,
                        "base_device_profile_id": draft.base_device_profile_id,
                    },
                )
            profile = await self._draft_profile(draft)
            return await self._push_to(client, draft, profile, machine, set_id, major)

    async def _refuse_once_on_the_board(self) -> None:
        """Once the board has been adopted, profiles reach the machine only through it.

        A second path would race the pull: a staged push saves and replaces files a board
        profile stands on, and a staged rollback removes the file the board's current version
        is on. Before adoption (writes never switched on, no board) the staged routes work as
        they always did.
        """
        if await self.board.adoption() is not None:
            raise Conflict(
                "Profiles now go to the machine through the profile board: put the draft on "
                "the board and pull. The staged push and rollback are off once the board "
                "has been adopted."
            )

    @staticmethod
    def _base_is_stale(
        draft: ProfileDraftRow, base: ProfileVersionRow, machine: MachineState
    ) -> bool:
        """Whether the base the draft was made from has been changed on the machine.

        Decided by content, from a read made just now: the base is current when the
        machine holds its content under **any** id, and stale only when its own id
        holds something else. A base that is gone (a machine reset by an update) is
        not stale: there is nothing left that could have been edited, and the push
        just adds.
        """
        if draft.base_device_profile_id is None:
            return False
        if machine.id_with_content(base.content_hash) is not None:
            return False
        return draft.base_device_profile_id in machine.profiles

    async def _require_writes_on(
        self,
        client: GaggimateClient,
        kind: Literal["profile_save", "profile_delete"],
        device_id: str | None,
    ) -> None:
        """Refuse, audited, when ``deviceWritesEnabled`` is off, before anything is read.

        The client's gate refuses every write by itself, but a push that finds its
        content already on the machine would otherwise never reach a write, and would
        have read the whole machine first. Same message, same audit row, same 403.
        """
        if await self.settings.get("deviceWritesEnabled"):
            return
        await self.writes.record(
            DeviceWriteWrite(
                kind=kind,
                host=client.host,
                device_id=device_id,
                result="refused",
                error=DISABLED_MESSAGE,
            )
        )
        raise DeviceWriteRefused(DISABLED_MESSAGE)

    async def _predecessor(
        self, draft: ProfileDraftRow, profile: Profile, set_id: int | None
    ) -> _Predecessor | None:
        """The profile on the machine this push supersedes, when there is one.

        Only within one lineage, so a fork or a first draft from another profile never
        removes the profile it was made from:

        * a push **recorded as a Set's next version** replaces what the Set's current
          version has on the machine, whatever the draft was made from;
        * any other push replaces the draft's base profile, and only when the label on
          the machine is the label being pushed (the draft keeps its base's label, so a
          renamed or forked draft is a new profile, not a new version of the old one).
        """
        if set_id is not None:
            found = await self.sets.current_device_profile(set_id)
            if found is None:
                return None
            device_id, version_id = found
            recorded = await self._require_version(version_id)
            return _Predecessor(device_id, recorded.id, recorded.content_hash, None)
        if draft.base_device_profile_id is None:
            return None
        base = await self._require_version(draft.base_version_id)
        return _Predecessor(draft.base_device_profile_id, base.id, base.content_hash, profile.label)

    async def _blocked(
        self, device_id: str, version_id: int | None, *, draft_id: int | None, set_id: int | None
    ) -> str | None:
        """Why this profile is somebody else's to keep, or ``None``.

        A Set's current version names what it brews with, and a pushed draft that has
        not been replaced stands behind the profile it pushed: removing the profile would
        take it from them. ``draft_id`` is the draft asking to remove it (a rollback);
        a push that replaces a profile leaves it ``None``, since the drafts that pushed
        the predecessor are the lineage it supersedes.
        """
        if draft_id is not None and await self.drafts.other_pushed_claims(
            device_id, excluding=draft_id
        ):
            return "still used by another pushed draft"
        using = await self.sets.sets_currently_using(device_id, version_id, excluding=set_id)
        if using:
            return f"still the current version of the Set {using[0]!r}"
        return None

    async def _push_to(
        self,
        client: GaggimateClient,
        draft: ProfileDraftRow,
        profile: Profile,
        machine: MachineState,
        set_id: int | None,
        major: bool | None = None,
    ) -> tuple[ProfileDraftRow, SetVersionRow | None]:
        """The push itself, on the client the connection handed out for it.

        Save (unless the machine already holds this content), read back, move the
        favourite star and the selection, remove the predecessor: in that order, so a
        failure anywhere before the last step leaves both profiles on the machine.
        """
        draft_id = draft.id
        placed = await place(client, profile, machine)
        if placed.problem is not None:
            # The profile is on the machine either way (the save happened); the draft
            # keeps the evidence and the predecessor is left exactly where it was.
            failed = await self.drafts.set_status(
                draft_id,
                "failed",
                pushed_device_profile_id=placed.device_id,
                pushed_saved=True,
                error=placed.error,
                verification=placed.verification,
                outcome={
                    "action": "push",
                    "lines": [
                        f"The profile was saved as {placed.device_id} but did not verify; "
                        "nothing was removed from the machine."
                    ],
                },
            )
            return _require_row(failed, draft_id), None
        served = placed.served
        if served is None:  # pragma: no cover - no problem means it was read back
            raise RuntimeError("a verified placement carries the profile it read back")

        lines: list[str] = []
        outcome: dict[str, Any] = {
            "action": "push",
            "reused_device_profile_id": placed.device_id if placed.reused else None,
            "replaced_device_profile_id": None,
            "kept_device_profile_id": None,
            "kept_reason": None,
            "startup_profile_cleared": False,
        }
        if placed.reused:
            lines.append(
                f"An identical profile was already on the machine ({placed.device_id}); "
                "nothing was saved."
            )
        predecessor = await self._predecessor(draft, profile, set_id)
        removal = Removal()
        cleared: list[int] = []
        if predecessor is not None and predecessor.device_id != placed.device_id:
            removal = await remove_if_ours(
                client,
                self.writes,
                device_id=predecessor.device_id,
                expected_hash=predecessor.content_hash,
                successor=placed.device_id,
                expected_label=predecessor.label,
                blocked=await self._blocked(
                    predecessor.device_id, predecessor.version_id, draft_id=None, set_id=set_id
                ),
            )
            if not removal.unrelated:
                self._describe_removal(predecessor.device_id, removal, outcome, lines)
            if removal.removed:
                await self.profiles.mark_one_deleted(predecessor.device_id)
                cleared = await self.sets.clear_pushed_device_profile(predecessor.device_id)
                await self.drafts.supersede_pushed(predecessor.device_id, by_draft_id=draft_id)
        favorite = served.favorite or removal.favorite_carried
        selected = served.selected or removal.selected_carried
        outcome["lines"] = lines
        await self._mirror(placed.device_id, served, favorite=favorite, selected=selected)
        row = _require_row(
            await self.drafts.set_status(
                draft_id,
                "pushed",
                pushed_device_profile_id=placed.device_id,
                pushed_saved=not placed.reused,
                verification={"sent": profile.to_device(), "loaded": served.to_device()},
                outcome=outcome,
                replaced_device_profile_id=predecessor.device_id
                if predecessor is not None and removal.removed
                else None,
                replaced_version_id=predecessor.version_id
                if predecessor is not None and removal.removed
                else None,
                cleared_set_version_ids=cleared if removal.removed else None,
            ),
            draft_id,
        )
        version: SetVersionRow | None = None
        if set_id is not None:
            version = await self.attach_to_set(row, set_id, major=major)
            await self.drafts.set_recorded_version(draft_id, version.id if version else None)
            # Read again so the answer already says which version of its Set the
            # push recorded, rather than leaving that to the next list read.
            row = _require_row(await self.drafts.get(draft_id), draft_id)
        log.info(
            "profile_pushed",
            draft_id=draft_id,
            device_id=placed.device_id,
            label=profile.label,
            reused=placed.reused,
            replaced=predecessor.device_id if predecessor is not None and removal.removed else None,
        )
        return row, version

    @staticmethod
    def _describe_removal(
        device_id: str, removal: Removal, outcome: dict[str, Any], lines: list[str]
    ) -> None:
        if removal.removed:
            outcome["replaced_device_profile_id"] = device_id
            lines.append(f"Replaced {device_id}: the previous copy is off the machine.")
            if removal.favorite_carried:
                lines.append("The favourite star moved to the new profile.")
            if removal.selected_carried:
                lines.append("The new profile is now the selected one.")
            if removal.startup_cleared:
                outcome["startup_profile_cleared"] = True
                lines.append(
                    "The machine's startup profile was the replaced one and the firmware "
                    "cleared that setting; pick a startup profile on the display if you want one."
                )
        elif removal.gone:
            lines.append(f"The profile this replaces ({device_id}) is {GONE}; nothing to remove.")
        else:
            outcome["kept_device_profile_id"] = device_id
            outcome["kept_reason"] = removal.reason
            lines.append(f"Left {device_id} on the machine: {removal.reason}.")

    async def rollback(self, draft_id: int) -> ProfileDraftRow:
        """Undo a push: bring the profile it replaced back, then remove the pushed one.

        One click, because the alternative is a profile on somebody's display
        that nobody can account for. In this order, and every step reads the machine
        again:

        1. the pushed profile is checked to be removable (this app's, unchanged, not
           still in use by another draft or Set). If it is not, nothing is touched:
           restoring the predecessor beside a profile that then stays would only add
           a copy. A push that **saved nothing** (it found the profile already on
           the machine) has nothing of its own to remove and never removes it;
        2. when the push removed a predecessor, its archived content is put back
           through the same no-duplicate and read-back steps as a push, and the
           favourite star and the selection move to it. If it cannot be put back, the
           pushed profile stays, so the machine never ends up with neither;
        3. the pushed profile is removed under the same checks.

        **Where the draft ends up depends on where it was**, and the rule is
        that the row must never describe a machine state that is not true:

        * a **failed** draft stays `failed`: the evidence of the mismatch is the
          point and a rollback does not erase it;
        * a **pushed** draft becomes `discarded`. It used to stay `pushed` with its
          device id cleared, which described a profile the machine no longer had, and
          was also a dead end, because `discard` refuses a pushed draft, so the
          draft could never reach a terminal state at all;
        * a draft whose profile a **later push replaced** has nothing to roll back
          and is refused.

        Once the pushed profile is off the machine its device id is cleared from the
        draft, from the profile mirror and from any Set version that named it: a
        version pointing at a file the machine no longer has would resolve to
        whatever inherits that id next.
        """
        draft = await self._require(draft_id)
        await self._refuse_once_on_the_board()
        if draft.status not in ("failed", "pushed"):
            raise Conflict(
                f"A {draft.status} draft has nothing on the machine to roll back; "
                "only a pushed or failed draft does."
            )
        if draft.replaced_by_draft_id is not None:
            raise Conflict(
                "A later push replaced this draft's profile on the machine, so there is "
                "nothing left to roll back. Roll back the later push instead."
            )
        device_id = draft.pushed_device_profile_id
        if not device_id:
            raise Conflict("That draft has nothing on the machine to roll back")
        lines: list[str] = []
        outcome: dict[str, Any] = {
            "action": "rollback",
            "restored_device_profile_id": None,
            "removed_device_profile_id": None,
            "kept_device_profile_id": None,
            "kept_reason": None,
            "startup_profile_cleared": False,
        }
        recorded_set = await self._recorded_set(draft)
        async with machine_operation(self.connection, "a profile rollback") as maybe_client:
            client = _require_client(maybe_client)
            await self._require_writes_on(client, "profile_delete", device_id)
            expected: str | None = None
            used_version: int | None = None
            if draft.status == "pushed" and draft.draft_version_id is not None:
                used_version = draft.draft_version_id
                expected = (await self._require_version(used_version)).content_hash
            elif draft.status == "failed":
                # What the machine stored is not what was sent, so the content the
                # archive knows is the document it served back at the time. Without one
                # (the read-back itself failed) there is nothing to compare.
                loaded = (draft.verification or {}).get("loaded")
                if isinstance(loaded, dict):
                    expected = profile_content_hash(Profile.model_validate(loaded))
            removal = Removal()
            own = draft.pushed_saved
            if own:
                blocked = await self._blocked(
                    device_id, used_version, draft_id=draft.id, set_id=recorded_set
                )
                refusal = await can_remove(
                    client,
                    self.writes,
                    device_id=device_id,
                    expected_hash=expected,
                    blocked=blocked,
                )
                if refusal is not None and not refusal.gone:
                    return await self._finish_kept(draft, refusal, outcome, lines)
            restored: Placed | None = None
            if draft.replaced_version_id is not None:
                machine = await read_machine(client)
                restored = await self._bring_back(client, draft.replaced_version_id, machine, lines)
                if restored is None:
                    outcome["lines"] = lines
                    return _require_row(await self.drafts.set_outcome(draft_id, outcome), draft_id)
                outcome["restored_device_profile_id"] = restored.device_id
            if own:
                removal = await remove_if_ours(
                    client,
                    self.writes,
                    device_id=device_id,
                    expected_hash=expected,
                    successor=restored.device_id if restored is not None else None,
                    blocked=await self._blocked(
                        device_id, used_version, draft_id=draft.id, set_id=recorded_set
                    ),
                )
            if restored is not None and restored.served is not None:
                await self._mirror(
                    restored.device_id,
                    restored.served,
                    favorite=restored.served.favorite or removal.favorite_carried,
                    selected=restored.served.selected or removal.selected_carried,
                )
                await self.sets.restore_pushed_device_profile(
                    [int(i) for i in (draft.cleared_set_version_ids or [])], restored.device_id
                )

        if not own:
            lines.append(
                f"This push saved nothing: {device_id} was already on the machine, put there "
                "by another push, so it is left in place."
            )
            outcome["kept_device_profile_id"] = device_id
            outcome["kept_reason"] = "saved by another push"
        elif removal.removed or removal.gone:
            outcome["removed_device_profile_id"] = device_id
            lines.append(
                f"Removed {device_id} from the machine."
                if removal.removed
                else f"{device_id} was already {GONE}."
            )
            if removal.favorite_carried:
                lines.append("The favourite star moved back to the restored profile.")
            if removal.selected_carried:
                lines.append("The restored profile is the selected one again.")
            if removal.startup_cleared:
                outcome["startup_profile_cleared"] = True
                lines.append(
                    "The machine's startup profile was that profile and the firmware cleared "
                    "that setting; pick a startup profile on the display if you want one."
                )
            elif restored is not None:
                lines.append(
                    "If the push cleared the machine's startup profile, it stays cleared: "
                    "this app never writes the machine's settings."
                )
        else:
            outcome["kept_device_profile_id"] = device_id
            outcome["kept_reason"] = removal.reason
            lines.append(f"Left {device_id} on the machine: {removal.reason}.")
        outcome["lines"] = lines

        if own and (removal.removed or removal.gone):
            await self.profiles.mark_one_deleted(device_id)
            await self.sets.clear_pushed_device_profile(device_id)
        return await self._finish(draft, removal if own else None, outcome)

    async def _recorded_set(self, draft: ProfileDraftRow) -> int | None:
        """The Set this draft's push recorded a version on, if it did."""
        if draft.recorded_version_id is None:
            return None
        version = await self.sets.get_version(draft.recorded_version_id)
        return None if version is None else version.set_id

    async def _finish_kept(
        self, draft: ProfileDraftRow, refusal: Removal, outcome: dict[str, Any], lines: list[str]
    ) -> ProfileDraftRow:
        """A rollback that found the pushed profile not removable: nothing was touched."""
        assert draft.pushed_device_profile_id is not None
        outcome["kept_device_profile_id"] = draft.pushed_device_profile_id
        outcome["kept_reason"] = refusal.reason
        lines.append(f"Left {draft.pushed_device_profile_id} on the machine: {refusal.reason}.")
        outcome["lines"] = lines
        return await self._finish(draft, refusal, outcome)

    async def _finish(
        self, draft: ProfileDraftRow, removal: Removal | None, outcome: dict[str, Any]
    ) -> ProfileDraftRow:
        """Move the draft to where a rollback's outcome leaves it.

        Finished when the profile is off the machine, was never this push's to remove,
        or can never be this app's to remove (not created by it). A profile edited on
        the display, or still used elsewhere, stays a `pushed` draft so the person can act.
        """
        finished = removal is None or removal.removed or removal.gone or removal.reason == NOT_OURS
        if not finished:
            return _require_row(await self.drafts.set_outcome(draft.id, outcome), draft.id)
        if draft.status == "pushed":
            await self.drafts.set_status(draft.id, "discarded")
        return _require_row(
            await self.drafts.clear_pushed_profile(draft.id, outcome=outcome), draft.id
        )

    async def _bring_back(
        self,
        client: GaggimateClient,
        version_id: int,
        machine: MachineState,
        lines: list[str],
    ) -> Placed | None:
        """Put the archived predecessor back on the machine. ``None`` when it cannot be.

        A copy that was saved but did not read back as sent is removed again (this box
        just wrote it, so it is ours by construction), or each retry would add one.
        """
        version = await self._require_version(version_id)
        try:
            placed = await place(client, profile_from_version(version), machine)
        except DeviceError as exc:
            lines.append(
                f"The previous profile could not be put back ({exc}); nothing was removed."
            )
            return None
        if placed.problem is not None:
            cleanup = await remove_if_ours(
                client, self.writes, device_id=placed.device_id, expected_hash=None, successor=None
            )
            lines.append(
                f"The previous profile was saved as {placed.device_id} but did not verify "
                f"({placed.error}); "
                + (
                    "that copy was removed again and nothing else was changed."
                    if cleanup.removed
                    else f"that copy could not be removed ({cleanup.reason}); "
                    "nothing else was changed."
                )
            )
            return None
        lines.append(
            f"An identical copy of the previous profile was already on the machine "
            f"({placed.device_id})."
            if placed.reused
            else f"Put the previous profile back as {placed.device_id}."
        )
        return placed

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
            base_profile=base.profile if base else None,
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

    async def _require_version(self, version_id: int) -> ProfileVersionRow:
        version = await self.profiles.get_version(version_id)
        if version is None:
            raise NotFound(f"No profile version {version_id}")
        return version

    async def _draft_profile(self, draft: ProfileDraftRow) -> Profile:
        if draft.draft_version_id is None:
            raise Conflict("That draft has no document to push")
        return profile_from_version(await self._require_version(draft.draft_version_id))

    async def _mirror(
        self,
        device_id: str,
        served: Profile,
        *,
        favorite: bool | None = None,
        selected: bool | None = None,
    ) -> None:
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
            favorite=served.favorite if favorite is None else favorite,
            selected=served.selected if selected is None else selected,
        )

    async def _render(
        self,
        *,
        base: Profile,
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
                # Nothing passes advice along any more; the variable is still
                # filled because a person's edited copy of this prompt may name
                # it, and an undefined variable refuses to render.
                "suggestions": NO_ADVICE,
                "previous_draft": previous,
                "barista_notes": notes.strip() or "They said nothing beyond the advice above.",
                "policy_bounds": _render_bounds(await self.bounds()),
            },
        )


@dataclass(frozen=True)
class _Predecessor:
    """The profile a push supersedes: where it is, and what the archive recorded for it."""

    device_id: str
    version_id: int
    content_hash: str
    #: The label the profile on the machine must carry to be this push's predecessor, or
    #: ``None`` when the Set already says so.
    label: str | None


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


def _require_client(client: GaggimateClient | None) -> GaggimateClient:
    if client is None:
        raise ServiceUnavailable(
            "No machine is configured, so there is nowhere to push this. Set gaggimateHost "
            "in Settings."
        )
    return client
