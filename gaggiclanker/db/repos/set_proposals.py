"""`set_version_proposals` — a change that is waiting for the person.

A proposal is the agent's half of the loop this archive is built around: one
change, with a falsifiable prediction, argued in a conversation and **changing
nothing** until somebody presses Accept. Every method here exists to keep four
properties true.

* **A proposal is not a version.** It names the version it was made against and
  carries the recipe fields it would move, as a patch in exactly the shape
  :meth:`~gaggiclanker.db.repos.sets.SetsRepository.add_version` takes. The
  version appears when :meth:`SetProposalsRepository.accept` runs, and until
  then the next shot is filed under the recipe that is actually in the hopper.

* **One waiting proposal per Set.** A partial unique index says so and
  :meth:`create` refuses before it, so the agent gets a sentence it can read
  rather than a constraint failure. Two proposals waiting at once would be the
  agent talking past the person instead of to them.

* **A proposal stops waiting when the Set moves on.** Any version appended to a
  Set — by the form, a roll back, a pushed draft, an accepted suggestion —
  retires whatever was waiting, in the same transaction
  (:meth:`~gaggiclanker.db.repos.sets.SetsRepository._insert_version`). A
  proposal argued against a recipe nobody is brewing any more is not a question
  the person can answer: Accept could only refuse it, and until somebody
  declined it the agent could propose nothing else.

* **Whatever `create` accepted, `accept` can apply.** Every reference a proposal
  names is checked when it is made, not when it is pressed, because a person's
  press must never be the thing that discovers a dangling id.

  **Accept is one transaction.** The proposal is still waiting, the Set has not
  moved on, the current version's prediction has been graded, the version is
  appended and the proposal is marked accepted — all in the one transaction, or
  a second browser tab accepting the same proposal creates two versions from
  it. That is why the append is
  :meth:`~gaggiclanker.db.repos.sets.SetsRepository.append_version` rather than
  ``add_version``: this connection's ``transaction()`` refuses to nest.

**Nothing here is reachable from a tool.** Accept and decline are routes a
person presses. There is no propose-class tool that calls them, no timer, no
background task, and ``tests/tools/test_no_proposal_is_accepted_by_a_tool.py``
walks the registry and the tool package's own bytecode to keep it that way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.sets import (
    RECIPE_FIELDS,
    TEXT_MAX,
    SetsRepository,
    SetVersionPatch,
    SetVersionRow,
    VersionRefused,
)
from gaggiclanker.db.repository import Repository

log = structlog.get_logger(__name__)

__all__ = [
    "PROPOSAL_STATUSES",
    "ProposalKind",
    "ProposalRefusal",
    "ProposalStatus",
    "ProposalWrite",
    "ProposalWriteResult",
    "SetProposalRow",
    "SetProposalsRepository",
    "change_groups",
]

#: What a proposal can be. `stale` is not something a person chooses: it is what
#: a proposal becomes when Accept finds the Set somewhere else than where the
#: proposal was made.
type ProposalStatus = Literal["proposed", "accepted", "declined", "stale"]

PROPOSAL_STATUSES: tuple[str, ...] = ("proposed", "accepted", "declined", "stale")

#: What a proposal is. A **change** moves one thing on a Set that has a recipe,
#: and owes a prediction. A **design** is the whole first recipe of a Set being
#: designed — a profile draft of its own plus grind, dose and yield — and owes
#: none, because a version 1 is a baseline and not a change to anything. Checked
#: here rather than by a CHECK on the column: see migration 0023.
type ProposalKind = Literal["change", "design"]

#: What counts as **one change**, by the group a recipe field belongs to. The
#: grind's text and its number are one change because they are one movement of
#: one dial written down twice: refusing "22" plus 22 as two changes would be
#: refusing the only honest way to record a grind.
_CHANGE_GROUPS: dict[str, str] = {
    "profile_version_id": "the profile",
    "grind_setting": "the grind",
    "grind_value": "the grind",
    "dose_g": "the dose",
    "target_yield_g": "the target yield",
}


def change_groups(patch: SetVersionPatch) -> list[str]:
    """Which things this patch moves, named as a person would name them.

    Sorted, so the same patch always reads the same way — a refusal that lists
    "the dose and the grind" one time and "the grind and the dose" the next is
    a refusal a model reads as two different rules.
    """
    return sorted(
        {
            _CHANGE_GROUPS[field]
            for field in RECIPE_FIELDS
            if field in patch.model_fields_set and field in _CHANGE_GROUPS
        }
    )


def recipe_patch(patch: SetVersionPatch) -> dict[str, Any]:
    """The recipe half of a patch, as the columns that were actually sent.

    Everything else on :class:`SetVersionPatch` — the intent, the prediction,
    the origin — belongs to the *version* and is supplied by :meth:`accept` from
    the proposal's own columns. Storing them twice is how the two come to
    disagree.
    """
    sent = patch.model_dump(mode="json", exclude_unset=True)
    return {field: sent[field] for field in RECIPE_FIELDS if field in sent}


class ProposalWrite(BaseModel):
    """What :meth:`SetProposalsRepository.create` stores.

    ``compares_to_version_id`` follows :class:`SetVersionPatch`: **omitted** it
    is the base version — the one this change is a change to, which is what
    "less bitter than now" means — and sent as **null** it is nothing, and the
    prediction is then graded on the numbers the new version itself states.
    ``model_fields_set`` is what tells the two apart.
    """

    model_config = ConfigDict(extra="forbid")

    #: The conversation this was argued in, when there was one. Nullable because
    #: a proposal outlives its chat, and because a tool call arriving over the
    #: stdio server may have no thread to name. When one *is* named it has to be
    #: a conversation of this Set: :meth:`SetProposalsRepository.create` refuses
    #: anything else rather than recording a change as having been argued
    #: somewhere it was not.
    thread_id: int | None = None
    #: The change, as recipe fields only. Validated here and again on the way
    #: back out of the database.
    patch: SetVersionPatch
    #: What the agent is trying, which becomes the version's "What are you
    #: trying?" when the proposal is accepted.
    reason: str = Field(min_length=1, max_length=500)
    #: Required and non-empty after stripping on a **change**: a change with no
    #: prediction is the thing this table exists to make impossible, and a row
    #: of three spaces would be one. How *good* a prediction has to be — long
    #: enough to name a direction, a size and a measure — is the tool's rule,
    #: because the tool is where a model reads the refusal and tries again.
    #: Refused on a **design**, which is a baseline and predicts nothing.
    prediction: Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX)] = ""
    compares_to_version_id: int | None = None
    #: Why two or more things have to move together. Empty for the ordinary
    #: one-change proposal.
    combined_reason: str = Field(default="", max_length=500)
    #: A change to a recipe, or a Set's whole first recipe. See
    #: :data:`ProposalKind`.
    kind: ProposalKind = "change"
    #: The profile draft an initial recipe carries — required on a design,
    #: which always brings a profile of its own, and refused on a change.
    draft_id: int | None = None

    @model_validator(mode="after")
    def _kind_decides_what_is_owed(self) -> ProposalWrite:
        """A change owes a prediction; a design owes a draft and predicts nothing.

        Stated on the model so both halves hold for every caller, not only the
        tool that happens to build the row today.
        """
        if self.kind == "change":
            if not self.prediction:
                raise ValueError("a proposed change needs a prediction")
            if self.draft_id is not None:
                raise ValueError("a proposed change carries no draft")
            return self
        if self.draft_id is None:
            raise ValueError("an initial recipe carries its profile draft")
        if self.prediction or self.combined_reason or self.compares_to_version_id is not None:
            raise ValueError("an initial recipe is a baseline and predicts nothing")
        return self


class SetProposalRow(BaseModel):
    """One proposal, with the numbers a reader needs joined in."""

    model_config = ConfigDict(extra="forbid")

    id: int
    set_id: int
    #: `change` for the ordinary one-change proposal, `design` for the initial
    #: recipe of a Set being designed.
    kind: ProposalKind = "change"
    #: The profile draft an initial recipe carries. Empty on a change.
    draft_id: int | None = None
    thread_id: int | None = None
    base_version_id: int
    base_version_no: int | None = None
    #: Re-validated on the way out by the same model that validated it on the
    #: way in. ``None`` when the stored JSON cannot be read as a patch — which
    #: nothing this application does can produce, and a hand-edited row can.
    #: Deliberately not an exception: this row is read by `GET /api/sets/{id}`,
    #: and one damaged proposal must not be a Set page that will not load. What
    #: it costs instead is the change: a proposal nobody can read is shown as
    #: one, refused by Accept, and still declinable.
    patch: SetVersionPatch | None = Field(default=None, validation_alias="patch_json")
    reason: str = ""
    prediction: str = ""
    compares_to_version_id: int | None = None
    compares_to_version_no: int | None = None
    combined_reason: str = ""
    status: ProposalStatus = "proposed"
    decline_note: str = ""
    resulting_version_id: int | None = None
    resulting_version_no: int | None = None
    created_at: str
    decided_at: str | None = None
    #: Whether the version this was proposed against is still the Set's current
    #: one. Computed in SQL rather than stored, because it is a fact about
    #: *now*: a proposal made this morning is stale the moment somebody records
    #: a version by hand, without anything about the proposal changing.
    base_is_current: bool = True

    @field_validator("patch", mode="before")
    @classmethod
    def _decode_patch(cls, value: Any) -> Any:
        """The stored JSON, back as the patch model `add_version` takes.

        Anything it cannot read becomes ``None`` and a log line, rather than a
        validation error on whatever route happened to read the row. The log
        line is the point: this cannot happen by itself, so if it ever does,
        somebody wants to know which proposal and when.
        """
        if not isinstance(value, str):
            return value
        try:
            decoded = json.loads(value)
        except ValueError:
            log.warning("set_proposal_patch_unreadable", reason="not json")
            return None
        if not isinstance(decoded, dict):
            log.warning("set_proposal_patch_unreadable", reason="not an object")
            return None
        try:
            return SetVersionPatch.model_validate(decoded)
        except ValidationError:
            log.warning("set_proposal_patch_unreadable", reason="not a recipe patch")
            return None

    @property
    def readable(self) -> bool:
        """Whether the change itself survived the round trip through the row."""
        return self.patch is not None

    @property
    def changed(self) -> list[str]:
        """Which things this proposal moves, for a refusal or a summary line."""
        return [] if self.patch is None else change_groups(self.patch)


#: Why a guarded write to a proposal was refused. A slug, like the version
#: writes next door: the repository has no opinion about HTTP statuses and the
#: route that does is the one place the mapping is written down.
type ProposalRefusal = Literal[
    "no_set",
    "no_current_version",
    "no_proposal",
    "not_waiting",
    "already_waiting",
    "outcome_open",
    "bad_compare",
    "bad_profile",
    "bad_thread",
    "unreadable",
    "stale",
    "designing",
    "not_designing",
    "bad_draft",
    "draft_closed",
    "design_has_versions",
    "design_has_shots",
]


@dataclass(frozen=True, slots=True)
class ProposalWriteResult:
    """A guarded write: the proposal it produced, or why there is none.

    Not an exception, for the reason the version writes give: every refusal here
    is an ordinary answer — something is already waiting, the last prediction
    has not been graded — and two callers turn them into two different things,
    an HTTP status for the person and a sentence for the model.

    ``version`` is filled only by :meth:`SetProposalsRepository.accept`, and it
    is the version that accepting created.
    """

    proposal: SetProposalRow | None = None
    refused: ProposalRefusal | None = None
    version: SetVersionRow | None = None
    #: The proposal that was already waiting, when ``refused`` is
    #: ``already_waiting``. The refusal describes it, so the agent talks about
    #: the one on screen instead of stacking another beside it.
    waiting: SetProposalRow | None = None


_SELECT = """
    SELECT p.*,
           base.version_no AS base_version_no,
           cmp.version_no AS compares_to_version_no,
           res.version_no AS resulting_version_no,
           NOT EXISTS (SELECT 1 FROM set_versions later
                        WHERE later.set_id = p.set_id
                          AND later.version_no > base.version_no) AS base_is_current
    FROM set_version_proposals p
    LEFT JOIN set_versions base ON base.id = p.base_version_id
    LEFT JOIN set_versions cmp ON cmp.id = p.compares_to_version_id
    LEFT JOIN set_versions res ON res.id = p.resulting_version_id
"""


class SetProposalsRepository(Repository):
    """Reads and writes the changes an agent has proposed for a Set."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        #: The versions half. Held rather than constructed per call so that
        #: :meth:`accept` can append a version inside its own transaction.
        self.sets = SetsRepository(db)
        #: The drafts an initial recipe carries, retired with it — a status
        #: write, in the caller's transaction, and never through the draft
        #: service that holds the machine.
        self.drafts = ProfileDraftsRepository(db)

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, set_id: int, proposal_id: int) -> SetProposalRow | None:
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.id = ? AND p.set_id = ?", (proposal_id, set_id)
        )
        return self.to_model(SetProposalRow, row)

    async def waiting(self, set_id: int) -> SetProposalRow | None:
        """The one proposal this Set has waiting, if it has one."""
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.set_id = ? AND p.status = 'proposed'", (set_id,)
        )
        return self.to_model(SetProposalRow, row)

    async def last_decided(self, set_id: int) -> SetProposalRow | None:
        """The most recent proposal somebody has already answered.

        What a new conversation is told when nothing is waiting: a declined
        proposal is information about what the person wants, and an accepted one
        is the change the conversation is now living with.
        """
        row = await self.db.fetch_one(
            f"{_SELECT} WHERE p.set_id = ? AND p.status != 'proposed' "
            "ORDER BY p.decided_at DESC, p.id DESC LIMIT 1",
            (set_id,),
        )
        return self.to_model(SetProposalRow, row)

    async def for_set(self, set_id: int, *, limit: int = 100) -> list[SetProposalRow]:
        """Every proposal this Set has had, newest first."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE p.set_id = ? ORDER BY p.id DESC LIMIT ?", (set_id, limit)
        )
        return self.to_models(SetProposalRow, rows)

    async def accepted_threads(self, set_id: int) -> dict[int, int]:
        """Which conversation each accepted proposal's version came out of.

        Keyed by version id, so the experiment log can offer a way back into the
        room the change was argued in. A proposal whose chat has been deleted
        has no thread and is simply absent.
        """
        rows = await self.db.fetch_all(
            """
            SELECT resulting_version_id AS version_id, thread_id
              FROM set_version_proposals
             WHERE set_id = ? AND status = 'accepted'
               AND resulting_version_id IS NOT NULL AND thread_id IS NOT NULL
             ORDER BY id
            """,
            (set_id,),
        )
        return {int(row["version_id"]): int(row["thread_id"]) for row in rows}

    async def design_profile_versions(self, set_id: int) -> set[int]:
        """The profile versions this Set's initial recipes have drafted, in any state.

        What a design conversation may land on again when it revises its own
        card: the profile it proposed a moment ago is this design's, not the
        library's, so proposing it a second time is not a copy of somebody
        else's profile.

        **Unless another Set has taken it up since.** A version that the
        current version of another Set still in use names is somebody else's
        recipe now, whoever drafted it first: letting the design keep it would
        put two Sets on one profile, and the matcher files nothing under
        either.
        """
        rows = await self.db.fetch_all(
            """
            SELECT d.draft_version_id AS version_id
              FROM set_version_proposals p
              JOIN profile_drafts d ON d.id = p.draft_id
             WHERE p.set_id = :set_id AND p.kind = 'design' AND d.draft_version_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM sets other
                     JOIN set_versions cur
                       ON cur.set_id = other.id
                      AND cur.version_no = (SELECT MAX(v.version_no) FROM set_versions v
                                             WHERE v.set_id = other.id)
                    WHERE other.id != :set_id
                      AND other.archived = 0
                      AND cur.profile_version_id = d.draft_version_id
               )
            """,
            {"set_id": set_id},
        )
        return {int(row["version_id"]) for row in rows}

    async def preview(self, proposal: SetProposalRow) -> SetVersionRow | None:
        """The version this proposal would create, as a row nobody stored.

        Built so the same :func:`~gaggiclanker.db.repos.sets.version_changes`
        that renders a version's diff in the log can render a proposal's — one
        renderer, so "Dose 18 g → 18.5 g" reads identically before and after the
        person presses Accept.

        The brew temperature rides along with the profile, exactly as it does on
        a real version: when the patch names a different profile, that profile's
        own temperature and label are read, because a proposal that moves the
        temperature by switching profiles must say so on the card.

        Built on :meth:`diff_base`, not on the stored row. A first recipe is
        previewed on the empty version 1 whatever fills it now: once somebody
        has filled it another way, a field the card left unset (a relative
        grind has no dial number) would otherwise inherit that recipe's value
        and the card would show a number the agent never proposed.
        """
        base = await self.diff_base(proposal)
        if base is None:  # pragma: no cover - the column is NOT NULL with a reference
            return None
        if proposal.patch is None:
            # Nothing to preview: the stored change cannot be read. The caller
            # renders no diff and says so in words, which is the honest answer
            # and not the same as "it changes nothing".
            return None
        update: dict[str, Any] = {
            field: getattr(proposal.patch, field)
            for field in RECIPE_FIELDS
            if field in proposal.patch.model_fields_set
        }
        if proposal.kind == "design":
            # Version 1 as accepting would fill it: the recipe, and the reason
            # as its intent. The version's own row id and number, because
            # that is the row the fill writes.
            update |= {"intent": proposal.reason, "origin": "chat"}
        candidate = base.model_copy(update=update)
        if candidate.profile_version_id != base.profile_version_id:
            candidate = candidate.model_copy(update=await self._profile_facts(candidate))
        return candidate

    async def diff_base(self, proposal: SetProposalRow) -> SetVersionRow | None:
        """What a proposal's card is drawn against: the other side of its diff.

        For a change it is the version the change was made to, which stays as
        it was when the change is accepted: accepting appends a new version.

        For a first recipe it is version 1 **with no recipe**, whatever the
        card's status. While the card waits, version 1 is exactly that, so
        nothing changes there. Accepting fills version 1 in place with the
        card's own recipe, and diffing against the stored row from then on
        would compare the recipe with itself: the card would lose everything it
        said the moment the person agreed to it. The empty recipe is what the
        card was a change to, before and after.
        """
        base = await self.sets.get_version(proposal.base_version_id)
        if base is None or proposal.kind != "design":
            return base
        return base.model_copy(
            update={
                **dict.fromkeys(RECIPE_FIELDS),
                "profile_label": None,
                "profile_temperature_c": None,
            }
        )

    async def _profile_facts(self, version: SetVersionRow) -> dict[str, Any]:
        """A profile's label and stated brew temperature, read as a version reads them.

        The same expression `_VERSION_SELECT` uses, because the rule for "the
        firmware's 0 means not set" belongs in one place and this is a second
        caller of it, not a second rule.
        """
        if version.profile_version_id is None:
            return {"profile_label": None, "profile_temperature_c": None}
        row = await self.db.fetch_one(
            """
            SELECT label,
                   CASE WHEN json_type(json, '$.temperature') IN ('integer', 'real')
                         AND json_extract(json, '$.temperature') > 0
                        THEN json_extract(json, '$.temperature') END AS temperature_c
              FROM profile_versions WHERE id = ?
            """,
            (version.profile_version_id,),
        )
        if row is None:
            return {"profile_label": None, "profile_temperature_c": None}
        return {"profile_label": row["label"], "profile_temperature_c": row["temperature_c"]}

    # ── writing ──────────────────────────────────────────────────────

    async def create(self, set_id: int, spec: ProposalWrite) -> ProposalWriteResult:
        """Record a change the person has not agreed to yet.

        Every guard is about state another request can change or a reference
        another request can invalidate, so all of them read inside the
        transaction that writes: the Set has a current version to propose
        against, that version's prediction is not still open, nothing is already
        waiting, the comparison and the profile are real, and the conversation
        named is one of this Set's.

        The open-outcome guard is the rule that keeps the loop honest. While
        nobody has said how the last prediction turned out, a new proposal would
        bury the experiment that is still running under the next one — so the
        agent grades what is there, or asks for another shot on the same recipe.

        **Every reference is checked here rather than left to a foreign key**,
        and the reason is Accept. A proposal stored naming a profile version
        that does not exist would pass every check the person's press makes and
        then fail on the insert, as a 500 with a rolled-back transaction and a
        proposal still waiting — a change nobody could accept and nobody could
        get past without declining it. Whatever `create` accepted, `accept`
        must be able to apply.

        **The kind splits on whether the Set is being designed.** A Set being
        designed has no recipe to change, so a change is refused there
        (`designing`) and an initial recipe is refused anywhere else
        (`not_designing`), whichever path asked. An initial recipe owes no
        prediction and is not held up by an open outcome — version 1 has none
        — and a newer one **replaces** the one waiting: the design conversation
        revises its answer, and the last card is the one that counts. The one
        it replaces goes `stale` and its draft is discarded, in this
        transaction, before the new row is inserted, so the one-waiting index
        holds. That is the only place `stale` comes from a newer proposal
        rather than a new version.
        """
        now = utc_now()
        async with self.db.transaction():
            current = await self.sets.current_version(set_id)
            if current is None:
                return ProposalWriteResult(refused="no_current_version")
            designing = bool(
                await self.db.fetch_value("SELECT designing FROM sets WHERE id = ?", (set_id,))
            )
            if designing != (spec.kind == "design"):
                return ProposalWriteResult(refused="designing" if designing else "not_designing")
            existing = await self.waiting(set_id)
            replaced = existing if existing is not None and existing.kind == "design" else None
            if existing is not None and replaced is None:
                return ProposalWriteResult(refused="already_waiting", waiting=existing)
            if spec.kind == "change" and current.outcome_state == "open":
                return ProposalWriteResult(refused="outcome_open")
            compares_to = (
                spec.compares_to_version_id
                if "compares_to_version_id" in spec.model_fields_set
                else (current.id if spec.kind == "change" else None)
            )
            if compares_to is not None:
                # Every existing version is older than the one this proposal
                # would create, so "a version of this Set" is the whole rule
                # here — the same reason `POST /versions` checks only that half.
                if await self.sets.version_of_set(set_id, compares_to) is None:
                    return ProposalWriteResult(refused="bad_compare")
            profile_id = spec.patch.profile_version_id
            if "profile_version_id" in spec.patch.model_fields_set and profile_id is not None:
                known = await self.db.fetch_value(
                    "SELECT 1 FROM profile_versions WHERE id = ?", (profile_id,)
                )
                if known is None:
                    return ProposalWriteResult(refused="bad_profile")
            if spec.thread_id is not None:
                # A conversation of **this Set**, not merely one that exists.
                # This column is the record of where a change was argued, and a
                # proposal pointing at somebody else's room would send a reader
                # to a transcript that says nothing about it.
                mine = await self.db.fetch_value(
                    "SELECT 1 FROM chat_threads WHERE id = ? AND set_id = ?",
                    (spec.thread_id, set_id),
                )
                if mine is None:
                    return ProposalWriteResult(refused="bad_thread")
            if spec.draft_id is not None:
                # The draft is the profile the recipe names: the same version,
                # and still a draft somebody can approve. A card whose profile
                # is some other document than the one on the Profiles page
                # would be accepted as one thing and pushed as another.
                draft = await self.drafts.get(spec.draft_id)
                if (
                    draft is None
                    or draft.status not in ("draft", "approved")
                    or draft.draft_version_id != profile_id
                ):
                    return ProposalWriteResult(refused="bad_draft")
            if replaced is not None:
                await self._retire(replaced, now)
            cursor = await self.db.execute(
                """
                INSERT INTO set_version_proposals
                    (set_id, thread_id, base_version_id, patch_json, reason, prediction,
                     compares_to_version_id, combined_reason, kind, draft_id, status,
                     created_at)
                VALUES (:set_id, :thread_id, :base_version_id, :patch_json, :reason, :prediction,
                        :compares_to_version_id, :combined_reason, :kind, :draft_id, 'proposed',
                        :created_at)
                """,
                {
                    "set_id": set_id,
                    "thread_id": spec.thread_id,
                    "base_version_id": current.id,
                    "patch_json": json.dumps(recipe_patch(spec.patch), sort_keys=True),
                    "reason": spec.reason,
                    "prediction": spec.prediction,
                    "compares_to_version_id": compares_to,
                    "combined_reason": spec.combined_reason,
                    "kind": spec.kind,
                    "draft_id": spec.draft_id,
                    "created_at": now,
                },
            )
            proposal_id = int(cursor.lastrowid or 0)
        return ProposalWriteResult(proposal=await self.get(set_id, proposal_id))

    async def accept(self, set_id: int, proposal_id: int) -> ProposalWriteResult:
        """Make the proposed change the Set's next version. A person's press.

        One transaction, and the order is the order of the questions: is this
        still waiting, is the Set still where it was, has the current
        prediction been graded, and only then the version and the proposal's own
        row. Anything less and two tabs pressing Accept a second apart would
        append the same change twice.

        An **initial recipe** goes through the same append, which is what fills
        a designed Set's version 1 in place (see
        :meth:`~gaggiclanker.db.repos.sets.SetsRepository.append_version`); its
        `resulting_version_id` is therefore version 1's own. It skips the
        open-outcome question, which a version 1 with no prediction cannot
        raise. When version 1 can no longer be filled — a shot was filed on it
        by hand — the whole transaction is rolled back and the refusal says
        why.

        **Nothing is sent to the machine.** A proposal that names a different
        profile records that the Set now brews with that profile, exactly as the
        Add a version form does; putting a profile on the display is a separate
        act, on the Profiles page, by a person.
        """
        now = utc_now()
        try:
            async with self.db.transaction():
                proposal = await self.get(set_id, proposal_id)
                if proposal is None:
                    return ProposalWriteResult(refused="no_proposal")
                if proposal.status != "proposed":
                    return ProposalWriteResult(refused="not_waiting", proposal=proposal)
                current = await self.sets.current_version(set_id)
                if current is None:  # pragma: no cover - the base version proves one exists
                    return ProposalWriteResult(refused="no_current_version")
                if current.id != proposal.base_version_id:
                    # The Set moved on. The change was argued against a recipe
                    # nobody is brewing any more, and quietly applying it to
                    # whatever is current now would be this box deciding what the
                    # agent meant. Recorded as stale so the log says what happened.
                    await self._retire(proposal, now)
                    return ProposalWriteResult(
                        refused="stale", proposal=await self.get(set_id, proposal_id)
                    )
                if proposal.kind == "change" and current.outcome_state == "open":
                    return ProposalWriteResult(refused="outcome_open", proposal=proposal)
                if proposal.kind == "design" and not await self._draft_still_stands(proposal):
                    # The twin of `bad_draft` at create: the card's profile was
                    # discarded or replaced on the Profiles page since, and a
                    # version 1 naming a profile nobody will ever approve would
                    # be a recipe that cannot be brewed.
                    return ProposalWriteResult(refused="draft_closed", proposal=proposal)
                if proposal.patch is None:
                    # The stored change cannot be read, so there is nothing to
                    # apply. Refused rather than applied as "no fields changed",
                    # which would append a version that claims to be the change
                    # and is not. Decline still works: getting rid of it needs no
                    # patch.
                    return ProposalWriteResult(refused="unreadable", proposal=proposal)
                version_id = await self.sets.append_version(
                    set_id, _version_patch(proposal), keep_proposal=proposal_id
                )
                if version_id is None:  # pragma: no cover - current proved there is a parent
                    return ProposalWriteResult(refused="no_current_version")
                await self._decide(proposal_id, "accepted", now, resulting_version_id=version_id)
        except VersionRefused as exc:
            # The only refusals an append raises are a design that can no
            # longer be filled, and the transaction above is already undone.
            return ProposalWriteResult(
                refused="design_has_shots"
                if exc.refused == "design_has_shots"
                else "design_has_versions",
                proposal=await self.get(set_id, proposal_id),
            )
        log.info(
            "set_proposal_accepted",
            proposal_id=proposal_id,
            set_id=set_id,
            kind=proposal.kind,
            set_version_id=version_id,
        )
        return ProposalWriteResult(
            proposal=await self.get(set_id, proposal_id),
            version=await self.sets.get_version(version_id),
        )

    async def decline(self, set_id: int, proposal_id: int, note: str = "") -> ProposalWriteResult:
        """Turn a proposal down, optionally saying why.

        The note is worth asking for: "not that, the last two finer grinds went
        the wrong way" is the sentence that stops the same change being proposed
        again next week, and the next conversation is told it.

        An initial recipe's draft is discarded with it, in the same transaction:
        the draft existed only to be the profile of that card, and a declined
        card's profile left open on the Profiles page is an orphan nobody asked
        for.
        """
        now = utc_now()
        async with self.db.transaction():
            proposal = await self.get(set_id, proposal_id)
            if proposal is None:
                return ProposalWriteResult(refused="no_proposal")
            if proposal.status != "proposed":
                return ProposalWriteResult(refused="not_waiting", proposal=proposal)
            await self._decide(proposal_id, "declined", now, note=note)
            if proposal.draft_id is not None:
                await self.drafts.discard_unsent([proposal.draft_id], now=now)
        return ProposalWriteResult(proposal=await self.get(set_id, proposal_id))

    async def _draft_still_stands(self, proposal: SetProposalRow) -> bool:
        """Whether an initial recipe's draft can still become the profile it names.

        Waiting for approval or approved, as at create — or already pushed and
        verified, which is the same document on the machine: a person who
        approved and pushed the card's profile before pressing Accept has done
        the steps in the other order, not undone anything. Discarded,
        superseded or failed is a profile the card can no longer stand on.
        """
        if proposal.draft_id is None:
            return False
        draft = await self.drafts.get(proposal.draft_id)
        return draft is not None and draft.status in ("draft", "approved", "pushed")

    async def _retire(self, proposal: SetProposalRow, now: str) -> None:
        """Mark a waiting proposal `stale`, and discard the draft it carried."""
        await self._decide(proposal.id, "stale", now)
        if proposal.draft_id is not None:
            await self.drafts.discard_unsent([proposal.draft_id], now=now)

    async def _decide(
        self,
        proposal_id: int,
        status: ProposalStatus,
        now: str,
        *,
        note: str = "",
        resulting_version_id: int | None = None,
    ) -> None:
        """Move a proposal out of `proposed`, inside the caller's transaction."""
        await self.db.execute(
            """
            UPDATE set_version_proposals
               SET status = :status,
                   decline_note = :note,
                   resulting_version_id = :resulting_version_id,
                   decided_at = :now
             WHERE id = :id
            """,
            {
                "status": status,
                "note": note,
                "resulting_version_id": resulting_version_id,
                "now": now,
                "id": proposal_id,
            },
        )


def _version_patch(proposal: SetProposalRow) -> SetVersionPatch:
    """The proposal as the patch that creates its version.

    The recipe comes from the stored patch, and everything else from the
    proposal's own columns: the reason becomes the version's intent, the
    prediction and its comparison come across unchanged, and the origin says
    `chat`, because that is where the change was argued.

    ``compares_to_version_id`` is sent **explicitly**, including as ``None`` for
    a proposal that compares against nothing. Omitting it would let
    ``add_version`` fall back to its own default — the parent — and a proposal
    the person accepted as "graded on its own numbers" would silently acquire a
    comparison nobody agreed to.
    """
    assert proposal.patch is not None  # accept refuses an unreadable one first
    return SetVersionPatch.model_validate(
        {
            **recipe_patch(proposal.patch),
            "intent": proposal.reason,
            "prediction": proposal.prediction,
            "compares_to_version_id": proposal.compares_to_version_id,
            "origin": "chat",
        }
    )
