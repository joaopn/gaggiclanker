"""Changes to a signature that an agent asks for and a person answers.

The agent writes new expectations in force (``SignatureService.propose``), and the person rejects
what they do not want. What the agent **cannot** do is change what is in force: replace an
expectation, retire it, move its tier, or put back one the person rejected. It asks, in a
conversation, with a reason for each change, and the request waits on a card until the person
presses Approve. Nothing here is reachable from a tool except
:meth:`SignatureRequestService.request`, which writes one waiting row; approving, declining and
applying are routes and a draft's approval.

**One guard, two callers.** :meth:`SignatureRequestService.problems` is the only place that says
whether a list of changes can be applied: the tool asks it before it stores a request, and
Approve asks it again, in the transaction that applies, so what a tool was allowed to store
the person's button can apply without a 500 (and what no longer applies makes the request stale).

**Approving uses the verbs a person already has**: reject, add, set the tier, restore. Nothing
new is stored on an expectation, ``confirmed_for_versions`` stays the one gate of what counts,
and a replaced or retired expectation appears on the Profiles page as a rejection with its
reason and its Restore.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.pins import judged_profile_version
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.signature_requests import (
    SignatureRequestRow,
    SignatureRequestsRepository,
    SignatureRequestWrite,
)
from gaggiclanker.db.repos.signatures import ExpectationRow, ExpectationWrite, SignatureRepository
from gaggiclanker.domain.metric_language import Expression, expression_unit
from gaggiclanker.domain.phase_names import phase_key, raw_phase_names
from gaggiclanker.domain.signature import (
    ExpectationInput,
    SignatureRefused,
    ValidExpectation,
    expectation_identity,
    limit_text,
    sentence_with_unit,
    validate_expectation,
)
from gaggiclanker.domain.signature_changes import (
    CHANGE_REASON_MIN,
    MAX_CHANGES,
    PutBackChange,
    PutBackOverrideChange,
    ReplaceChange,
    RetireChange,
    RowKey,
    StoredChange,
    StoredChanges,
    StoredReplacement,
    TierChange,
    compare_sides,
    replacement_words,
    request_loosens,
    revision_words,
    rows_named,
)
from gaggiclanker.signatures.checks import checks_for_shots
from gaggiclanker.signatures.service import split_duplicates

log = structlog.get_logger(__name__)

__all__ = [
    "ApproveResult",
    "ChangeIn",
    "ChangeView",
    "DraftSignatureChanges",
    "PreviewView",
    "Problem",
    "RequestView",
    "SignatureRequestService",
]

type ChangeTier = Literal["critical", "important", "context"]

_KINDS = ("replace", "retire", "tier", "put_back", "put_back_override")


class ChangeIn(BaseModel):
    """One change an agent asks for. Which fields it takes depends on ``kind``; a field that does
    not belong to the kind is refused in words, with every other problem of the request."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["replace", "retire", "tier", "put_back", "put_back_override"] = Field(
        description=(
            "replace: take an expectation in force out and put a new one in its place "
            "(expectation_id and expectation); retire: take one in force out (expectation_id); "
            "tier: move one in force to another tier (expectation_id and tier); put_back: ask "
            "for an expectation the person rejected to come back (expectation_id); "
            "put_back_override: ask for this version's rejected different limit to come back "
            "(override_id; a Recipe conversation only)."
        )
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description=(
            f"Why this change, in at least {CHANGE_REASON_MIN} characters: what the person "
            "will read on the card beside it."
        ),
    )
    expectation_id: int | None = Field(
        default=None, gt=0, description="The expectation's #id, as in your context."
    )
    expectation: ExpectationInput | None = Field(
        default=None,
        description="For replace: the new expectation, in the shape propose_signature takes.",
    )
    tier: ChangeTier | None = Field(default=None, description="For tier: the tier to move it to.")
    override_id: int | None = Field(
        default=None, gt=0, description="For put_back_override: the override's id."
    )


@dataclass
class DraftSignatureChanges:
    """Changes an agent asks for with a draft: they name **the base version's** expectations and
    are stored as one request, with the draft, applied when the draft is approved."""

    changes: tuple[ChangeIn, ...]
    reason: str
    thread_id: int | None = None
    #: Filled by the draft's store: the id of the request it stored.
    request_id: int | None = None


@dataclass(frozen=True, slots=True)
class Problem:
    """One reason a change cannot be applied, said twice: for the agent, which needs the row's
    #id and what to do next, and for the person, who reads the row's own words on a card."""

    agent: str
    person: str


@dataclass(frozen=True, slots=True)
class ApproveResult:
    """A person's answer, or why there is none."""

    request: SignatureRequestRow | None = None
    refused: Literal["no_request", "not_waiting", "stale", "draft"] | None = None


# ── what is served ───────────────────────────────────────────────────


class PreviewView(BaseModel):
    """What a change does to the version's checks, over the shots they are read for."""

    model_config = ConfigDict(extra="forbid")

    shots: int
    #: How many of those shots fail the expectation now, and how many would fail the one that
    #: stands in force after the whole request (a retired one counts as passing).
    failing_now: int
    failing_after: int


class StayingView(BaseModel):
    """An expectation in force that a replacement or retirement leaves where it is."""

    model_config = ConfigDict(extra="forbid")

    expectation_id: int
    #: What it says, as a card says a row ("lowest value of temperature in the soak, at least
    #: 91 °C"): the measure as well as the limit, so two rows that differ in it read differently.
    words: str


class ChangeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["replace", "retire", "tier", "put_back", "put_back_override"]
    reason: str
    expectation_id: int | None = None
    override_id: int | None = None
    #: The tier a card's header names: the replacement's, else the row's own now (a tier move
    #: says where it goes in ``new_tier``).
    tier: ChangeTier | None = None
    phase: str | None = None
    old_sentence: str = ""
    new_sentence: str = ""
    #: A measure's limit as the Profiles page words it, before and after ("at least 93 °C",
    #: "at least 91.5 °C"); empty for a row that is not a measure.
    old_limit_text: str = ""
    new_limit_text: str = ""
    old_tier: ChangeTier | None = None
    new_tier: ChangeTier | None = None
    rejected_reason: str = ""
    rejected_at: str | None = None
    #: The expectation an approved replacement wrote.
    written_expectation_id: int | None = None
    #: Waiting requests only, for replace, retire and put back.
    preview: PreviewView | None = None
    #: What the change says in one clause, for a line that lists changes.
    summary: str = ""
    #: Waiting requests only, for replace and retire: the other expectations in force on the same
    #: phase and the same channel, which the request leaves alone ("#77 “at least 91 °C” stays
    #: in force."). Computed on the server from the rows in force now.
    stays_in_force: list[StayingView] = Field(default_factory=list)


class RequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    status: Literal["waiting", "approved", "declined", "stale"]
    stale_reason: str
    reason: str
    answer_reason: str
    created_at: str
    answered_at: str | None
    profile_version_id: int
    #: The profile's name.
    profile_label: str
    #: How the Profiles page's Signature card names this version ("v3"); ``None`` when no profile
    #: lists it.
    version_label: str | None
    thread_id: int | None
    #: Set when the request came with a profile draft: the draft answers it, not these routes.
    draft_id: int | None
    #: ``False`` when the stored request can no longer be read.
    readable: bool
    #: The name of a Recipe version whose open prediction is graded under a check a change
    #: loosens; ``None`` when nothing loosens or no prediction is open.
    loosens_open_prediction: str | None
    changes: list[ChangeView]


# ── the service ──────────────────────────────────────────────────────


def _words(row: ExpectationRow) -> str:
    """A row as a person reads it on a card: its sentence, the limit with its unit."""
    return sentence_with_unit(row.sentence, row.expression)


def _desc(row: ExpectationRow) -> str:
    return f"#{row.id} “{row.sentence}”"


def _valid(replacement: StoredReplacement) -> ValidExpectation:
    return ValidExpectation(
        tier=replacement.tier,
        kind=replacement.kind,
        phase=replacement.phase,
        expression=replacement.expression,
        warning_fault=replacement.warning_fault,
        text=replacement.text,
        fault=replacement.fault,
        sentence=replacement.sentence,
    )


class SignatureRequestService:
    """Asking for signature changes, and a person's answer to them."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = SignatureRepository(db)
        self.requests = SignatureRequestsRepository(db)
        self.profiles = ProfilesRepository(db)

    def _atomic(self) -> AbstractAsyncContextManager[Any]:
        """One transaction for the check and the write, or none when the caller holds one."""
        return nullcontext() if self.db.in_transaction else self.db.transaction()

    # ── asking (the one thing a tool reaches) ────────────────────────

    async def request(
        self,
        *,
        profile_version_id: int,
        changes: Sequence[ChangeIn],
        reason: str,
        thread_id: int | None,
        recipe_version_id: int | None = None,
    ) -> SignatureRequestRow:
        """Store one waiting request, or refuse it naming every problem. Writes nothing else.

        ``recipe_version_id`` is the Recipe version the conversation is about, which is the only
        one a rejected override can be put back on.
        """
        if thread_id is None:
            raise SignatureRefused(
                "A signature change is answered on a card in a conversation, and this connection "
                "has none."
            )
        async with self._atomic():
            phases = await self.phase_names(profile_version_id)
            if phases is None:
                raise SignatureRefused(f"There is no profile version {profile_version_id}.")
            stored = await self._stored(
                profile_version_id, changes, phases=phases, recipe_version_id=recipe_version_id
            )
            return await self._store(
                profile_version_id, stored, reason=reason, thread_id=thread_id, draft_id=None
            )

    async def request_for_draft(
        self,
        *,
        draft_id: int,
        draft_version_id: int,
        base_version_id: int,
        changes: Sequence[ChangeIn],
        reason: str,
        thread_id: int | None,
    ) -> SignatureRequestRow:
        """Store the request that comes with a draft, in the draft's own transaction.

        The agent names **the base version's** expectations; the draft's version carries copies
        of them (``carried_from_id``), and the request is stored against those copies. Nothing
        on the draft's version changes until the draft is approved.
        """
        phases = await self.phase_names(draft_version_id)
        if phases is None:
            raise SignatureRefused(f"There is no profile version {draft_version_id}.")
        base = await judged_profile_version(self.db, base_version_id)
        if draft_version_id in (base_version_id, base):
            # A patch that leaves the document as it is makes no new version: the draft's version
            # is the base itself, and its rows are the base's own. A change of those is not
            # "with a draft" (nothing is approved that would carry it): it is asked on the base.
            raise SignatureRefused(
                "This draft changes nothing in the profile, so it makes no new version and "
                "signature_changes cannot go with it: ask for a change to the base version's "
                f"signature with request_signature_change (profile_version_id {base})."
            )
        copies = {
            r.carried_from_id: r
            for r in await self.repo.for_version(draft_version_id)
            if r.carried_from_id is not None
        }
        mapped: list[ChangeIn] = []
        problems: list[str] = []
        for number, change in enumerate(changes, start=1):
            if change.kind == "put_back_override":
                problems.append(
                    f"change {number}: a draft cannot put back an override; that is for a "
                    "Recipe conversation, with request_signature_change."
                )
                continue
            if change.expectation_id is None:
                mapped.append(change)
                continue
            # The row must be one of the base's own: a row of an older version that the base
            # carried (or any other version's) is not this draft's to change.
            named = await self.repo.get(change.expectation_id)
            if named is None or named.profile_version_id != base:
                problems.append(
                    f"change {number}: expectation #{change.expectation_id} is not one of "
                    f"the base version's (profile version {base})."
                )
                continue
            copy = copies.get(change.expectation_id)
            if copy is None:
                problems.append(
                    f"change {number}: this draft's version does not carry expectation "
                    f"#{change.expectation_id} (only expectations in force on the base "
                    "are carried)."
                )
                continue
            mapped.append(change.model_copy(update={"expectation_id": copy.id}))
        if problems:
            raise SignatureRefused(" ".join(problems))
        stored = await self._stored(draft_version_id, mapped, phases=phases, recipe_version_id=None)
        return await self._store(
            draft_version_id, stored, reason=reason, thread_id=thread_id, draft_id=draft_id
        )

    async def _store(
        self,
        profile_version_id: int,
        stored: tuple[list[StoredChange | None], list[str], dict[int, list[RowKey]]],
        *,
        reason: str,
        thread_id: int | None,
        draft_id: int | None,
    ) -> SignatureRequestRow:
        built, shape_problems, left_out = stored
        good = [c for c in built if c is not None]
        problems = list(shape_problems)
        if good or left_out:
            problems += await self.problems(
                profile_version_id, good, numbers=_numbers(built), left_out=left_out
            )
        if problems:
            raise SignatureRefused(" ".join(problems))
        return await self.requests.create(
            SignatureRequestWrite(
                profile_version_id=profile_version_id,
                thread_id=thread_id,
                draft_id=draft_id,
                reason=reason.strip(),
                changes=StoredChanges(changes=good),
            )
        )

    async def phase_names(self, profile_version_id: int) -> list[str] | None:
        version = await self.profiles.get_version(profile_version_id)
        if version is None:
            return None
        return raw_phase_names(version.profile) or []

    async def _stored(
        self,
        profile_version_id: int,
        changes: Sequence[ChangeIn],
        *,
        phases: Sequence[str],
        recipe_version_id: int | None,
    ) -> tuple[list[StoredChange | None], list[str], dict[int, list[RowKey]]]:
        """The changes as they are stored: shapes checked, replacements validated against the
        profile's phases, and a snapshot of what each is about. Every problem is named, and the
        rows a malformed change names are returned by its position (``left_out``) so the guard
        can still say that two changes name one row."""
        problems: list[str] = []
        left_out: dict[int, list[RowKey]] = {}
        if not changes:
            raise SignatureRefused("Ask for at least one change.")
        if len(changes) > MAX_CHANGES:
            raise SignatureRefused(f"Ask for at most {MAX_CHANGES} changes in one request.")
        rows = {r.id: r for r in await self.repo.for_version(profile_version_id)}
        built: list[StoredChange | None] = []
        for number, change in enumerate(changes, start=1):
            where = f"change {number}"
            reason = " ".join(change.reason.split())
            wrong: list[str] = []
            if len(reason) < CHANGE_REASON_MIN:
                wrong.append(f"its reason is under {CHANGE_REASON_MIN} characters")
            given = {
                "expectation_id": change.expectation_id is not None,
                "expectation": change.expectation is not None,
                "tier": change.tier is not None,
                "override_id": change.override_id is not None,
            }
            takes = {
                "replace": {"expectation_id", "expectation"},
                "retire": {"expectation_id"},
                "tier": {"expectation_id", "tier"},
                "put_back": {"expectation_id"},
                "put_back_override": {"override_id"},
            }[change.kind]
            missing = sorted(takes - {k for k, v in given.items() if v})
            stray = sorted({k for k, v in given.items() if v} - takes)
            if missing:
                wrong.append(f"a {change.kind} needs {', '.join(missing)}")
            if stray:
                wrong.append(f"a {change.kind} takes no {', '.join(stray)}")
            if change.kind == "put_back_override" and recipe_version_id is None:
                wrong.append(
                    "an override belongs to a Recipe version, and this conversation has none "
                    "to put it back on"
                )
            replacement: StoredReplacement | None = None
            if change.kind == "replace" and change.expectation is not None:
                try:
                    valid = validate_expectation(change.expectation, phases, where=where)
                except SignatureRefused as refused:
                    wrong.append(str(refused).removeprefix(f"{where}: ").rstrip("."))
                else:
                    replacement = StoredReplacement(
                        tier=valid.tier,  # type: ignore[arg-type]
                        kind=valid.kind,  # type: ignore[arg-type]
                        phase=valid.phase,
                        expression=valid.expression,
                        warning_fault=valid.warning_fault,
                        text=valid.text,
                        fault=valid.fault,
                        sentence=valid.sentence,
                    )
            if wrong:
                problems.append(f"{where}: {'; '.join(wrong)}.")
                built.append(None)
                left_out[number] = await self._named_by(change)
                continue
            old = rows.get(change.expectation_id) if change.expectation_id is not None else None
            snapshot: dict[str, Any] = {
                "reason": reason,
                "phase": old.phase if old is not None else None,
                "old_sentence": _words(old) if old is not None else "",
                "old_tier": old.tier if old is not None else None,
                "old_limit_text": _limit_of(old.expression) if old is not None else "",
            }
            if change.kind == "replace":
                assert replacement is not None and change.expectation_id is not None
                built.append(
                    ReplaceChange(
                        expectation_id=change.expectation_id,
                        expectation=replacement,
                        **snapshot,
                    )
                )
            elif change.kind == "retire":
                assert change.expectation_id is not None
                built.append(RetireChange(expectation_id=change.expectation_id, **snapshot))
            elif change.kind == "tier":
                assert change.expectation_id is not None and change.tier is not None
                built.append(
                    TierChange(expectation_id=change.expectation_id, tier=change.tier, **snapshot)
                )
            elif change.kind == "put_back":
                assert change.expectation_id is not None
                built.append(
                    PutBackChange(
                        expectation_id=change.expectation_id,
                        rejected_reason=old.reject_reason if old is not None else "",
                        rejected_at=old.answered_at if old is not None else None,
                        **snapshot,
                    )
                )
            else:
                assert change.override_id is not None and recipe_version_id is not None
                override = await self.repo.get_override(change.override_id)
                target = (
                    await self.repo.get(override.expectation_id) if override is not None else None
                )
                built.append(
                    PutBackOverrideChange(
                        override_id=change.override_id,
                        recipe_version_id=recipe_version_id,
                        expectation_id=override.expectation_id if override is not None else None,
                        new_sentence=(
                            _limit_of(
                                target.expression.model_copy(update={"compare": override.compare})
                            )
                            if override is not None
                            and override.compare is not None
                            and target is not None
                            and target.expression is not None
                            else ""
                        ),
                        rejected_reason=override.reject_reason if override is not None else "",
                        rejected_at=override.answered_at if override is not None else None,
                        reason=reason,
                        phase=target.phase if target is not None else None,
                        old_sentence=_words(target) if target is not None else "",
                        old_tier=target.tier if target is not None else None,
                    )
                )
        return built, problems, left_out

    async def _named_by(self, change: ChangeIn) -> list[RowKey]:
        """The rows a change as asked names, whether or not it could be stored."""
        keys: list[RowKey] = []
        if change.expectation_id is not None:
            keys.append(("e", change.expectation_id))
        if change.override_id is not None:
            keys.append(("o", change.override_id))
            override = await self.repo.get_override(change.override_id)
            if override is not None:
                keys.append(("e", override.expectation_id))
        return keys

    # ── the guard ────────────────────────────────────────────────────

    async def problems(
        self,
        profile_version_id: int,
        changes: Sequence[StoredChange],
        *,
        numbers: Sequence[int] | None = None,
        left_out: Mapping[int, Sequence[RowKey]] | None = None,
    ) -> list[str]:
        """Why these changes cannot be applied to this version as it stands, every reason in its
        own sentence, as the agent is told (empty: they can)."""
        found = await self.problem_list(
            profile_version_id, changes, numbers=numbers, left_out=left_out
        )
        return [p.agent for p in found]

    async def problem_list(
        self,
        profile_version_id: int,
        changes: Sequence[StoredChange],
        *,
        numbers: Sequence[int] | None = None,
        left_out: Mapping[int, Sequence[RowKey]] | None = None,
        twins: Mapping[int, Sequence[int]] | None = None,
    ) -> list[Problem]:
        """The guard: why these changes cannot be applied to this version as it stands, each
        problem once for the agent (naming the row by its #id and saying what to do next) and
        once for the person (the row's words, no ids, no tool names).

        **The one guard.** The tool asks it before a request is stored; Approve asks it again, in
        the transaction that applies. ``numbers`` are the changes' positions in the agent's list
        when some of it was left out for a shape problem, and ``left_out`` the rows those left-out
        changes named, so "two changes name one row" is still said when an earlier change is
        malformed.
        """
        found: list[Problem] = []
        try:
            await self._refuse_a_pins_copy(profile_version_id)
        except SignatureRefused as refused:
            return [Problem(str(refused), str(refused))]
        rows = {r.id: r for r in await self.repo.for_version(profile_version_id)}
        numbered = list(enumerate(changes, start=1))
        if numbers is not None:
            numbered = list(zip(numbers, changes, strict=True))

        # Two changes naming one row. Left-out changes name theirs too.
        named: dict[int, list[RowKey]] = {n: rows_named(c) for n, c in numbered}
        named.update({n: list(keys) for n, keys in (left_out or {}).items()})
        seen: dict[RowKey, int] = {}
        for number in sorted(named):
            for key in named[number]:
                if key in seen:
                    if key[0] == "e":
                        row = rows.get(key[1])
                        agent = f"expectation #{key[1]}"
                        person = f"“{_words(row)}”" if row is not None else "one expectation"
                    else:
                        agent = f"override #{key[1]}"
                        person = "one different limit"
                    found.append(
                        Problem(
                            f"changes {seen[key]} and {number} both name {agent}: ask for one "
                            "change to a row.",
                            f"Two changes ask for something different of {person}.",
                        )
                    )
                else:
                    seen[key] = number

        replaced = {c.expectation_id for _, c in numbered if isinstance(c, ReplaceChange)}
        for row_id in list(replaced):
            replaced.update((twins or {}).get(row_id, ()))
        replacements: list[tuple[int, ReplaceChange]] = []
        for number, change in numbered:
            where = f"change {number}"
            if isinstance(change, PutBackOverrideChange):
                found += await self._override_problems(where, change, profile_version_id)
                continue
            row = rows.get(change.expectation_id)
            if row is None:
                found.append(
                    Problem(
                        f"{where}: expectation #{change.expectation_id} is not on profile "
                        f"version {profile_version_id}.",
                        f"“{change.old_sentence}” is not on this profile version.",
                    )
                )
                continue
            if isinstance(change, PutBackChange):
                found += _put_back_problem(where, row)
                continue
            found += _in_force_problem(where, row, change)
            if isinstance(change, TierChange) and row.status == "confirmed":
                if row.tier == change.tier:
                    found.append(
                        Problem(
                            f"{where}: {_desc(row)} is already {change.tier}.",
                            f"“{_words(row)}” is already {change.tier}.",
                        )
                    )
            if isinstance(change, ReplaceChange):
                replacements.append((number, change))

        # A replacement whose identity is already there. The rows this request replaces are
        # going, so they are not "there"; repeats inside the request are.
        existing = [r for r in rows.values() if r.id not in replaced]
        if replacements:
            _, duplicates = split_duplicates(
                [_valid(c.expectation) for _, c in replacements], existing
            )
            for position, valid_one, why in duplicates:
                number = replacements[position - 1][0]
                replaced_words = sentence_with_unit(valid_one.sentence, valid_one.expression)
                found.append(
                    Problem(
                        f"change {number}: the replacement is {why}: ask for a different one, "
                        "or leave it out.",
                        f"The replacement “{replaced_words}” "
                        f"{_person_why_there(valid_one, existing)}.",
                    )
                )
        return found

    async def _override_problems(
        self, where: str, change: PutBackOverrideChange, profile_version_id: int
    ) -> list[Problem]:
        """Everything that would make ``restore_override`` refuse, said before it is asked."""
        override = await self.repo.get_override(change.override_id)
        if override is None:
            return [
                Problem(
                    f"{where}: there is no override {change.override_id}.",
                    "That different limit no longer exists.",
                )
            ]
        about = f"“{change.new_sentence}”" if change.new_sentence else "that different limit"
        if override.recipe_version_id != change.recipe_version_id:
            return [
                Problem(
                    f"{where}: override {change.override_id} is not one of this conversation's "
                    "version's.",
                    f"{about} belongs to another Recipe version.",
                )
            ]
        if override.status != "rejected":
            return [
                Problem(
                    f"{where}: override {change.override_id} is not rejected, so there is "
                    "nothing to put back.",
                    f"{about} is not rejected any more, so there is nothing to put back.",
                )
            ]
        target = await self.repo.get(override.expectation_id)
        if (
            target is None
            or target.status != "confirmed"
            or (target.profile_version_id != profile_version_id)
        ):
            return [
                Problem(
                    f"{where}: override {change.override_id} is a limit for an expectation that "
                    "is not in force on this profile version, so it cannot be put back; ask for "
                    "the expectation first.",
                    f"{about} is a limit for “{change.old_sentence}”, which is not in force on "
                    "this profile version.",
                )
            ]
        live = await self.repo.overrides_for_recipe_version(
            override.recipe_version_id, statuses=["proposed", "confirmed"]
        )
        if live:
            return [
                Problem(
                    f"{where}: this version already has an override in force, and a version has "
                    "one at a time; the person can reject it first.",
                    f"{about} cannot come back while this Recipe version has another different "
                    "limit in force.",
                )
            ]
        return []

    async def _refuse_a_pins_copy(self, profile_version_id: int) -> None:
        """A pin's copy carries no expectations of its own (see ``SignatureService``)."""
        from gaggiclanker.signatures.service import SignatureService

        await SignatureService(self.db)._refuse_a_pins_copy(profile_version_id)

    # ── a person's answer ────────────────────────────────────────────

    async def approve(self, request_id: int) -> ApproveResult:
        """Check, then write, in one transaction: the changes are checked again by the guard
        the tool used; if any no longer applies the request becomes stale and nothing is
        written; otherwise they are applied with the repository's existing verbs."""
        async with self.db.transaction():
            row = await self.requests.get(request_id)
            if row is None:
                return ApproveResult(refused="no_request")
            if row.draft_id is not None:
                return ApproveResult(request=row, refused="draft")
            if row.status != "waiting":
                return ApproveResult(request=row, refused="not_waiting")
            if row.changes is None:
                await self.requests.mark_stale(request_id, "The request can no longer be read.")
                return ApproveResult(request=await self.requests.get(request_id), refused="stale")
            problems = await self.problem_list(row.profile_version_id, row.changes.changes)
            if problems:
                await self.requests.mark_stale(request_id, _said(problems))
                return ApproveResult(request=await self.requests.get(request_id), refused="stale")
            await self._apply_changes(row, row.profile_version_id, row.changes.changes)
            return ApproveResult(request=await self.requests.get(request_id))

    async def decline(self, request_id: int, reason: str = "") -> ApproveResult:
        """Nothing changes but the request: it is answered, with the reason when given."""
        async with self.db.transaction():
            row = await self.requests.get(request_id)
            if row is None:
                return ApproveResult(refused="no_request")
            if row.draft_id is not None:
                return ApproveResult(request=row, refused="draft")
            if not await self.requests.decide(request_id, "declined", answer_reason=reason):
                return ApproveResult(request=row, refused="not_waiting")
            return ApproveResult(request=await self.requests.get(request_id))

    async def apply_for_draft(
        self, draft_id: int, final_version_id: int
    ) -> SignatureRequestRow | None:
        """The approval of a draft applies the request that came with it, in the approval's own
        transaction and after any carrying that transaction does.

        ``final_version_id`` is the profile version the draft became. A change that no longer
        applies makes the request stale and the approval goes on without it.
        """
        row = await self.requests.for_draft(draft_id)
        if row is None or row.status != "waiting":
            return row
        if row.changes is None:
            await self.requests.mark_stale(row.id, "The request can no longer be read.")
            return await self.requests.get(row.id)
        resolved, unresolved, twins = await self._resolved_on(final_version_id, row.changes.changes)
        if unresolved:
            await self.requests.mark_stale(row.id, " ".join(unresolved))
            return await self.requests.get(row.id)
        problems = await self.problem_list(final_version_id, resolved, twins=twins)
        if problems:
            await self.requests.mark_stale(row.id, _said(problems))
            return await self.requests.get(row.id)
        await self._apply_changes(row, final_version_id, resolved, twins=twins)
        return await self.requests.get(row.id)

    async def _resolved_on(
        self, version_id: int, changes: Sequence[StoredChange]
    ) -> tuple[list[StoredChange], list[str], dict[int, list[int]]]:
        """A draft's changes name its own version's copies; the version it ended up as carries
        copies of those (or is the same version). Find each by following ``carried_from_id``.

        The person approved a change to *a thing the signature says*, so no row that says it
        stays in force: when the version the draft became already held a row with the copy's
        identity (a profile revived on a version of its own that carried onto it), the change
        applies to every such row in force, not to the copy alone. Those other rows are the
        third value, by the row that stands for them in the resolved change.
        """
        rows = await self.repo.for_version(version_id)
        by_chain: dict[int, ExpectationRow] = {}
        for row in rows:
            cursor: ExpectationRow | None = row
            hops = 0
            while cursor is not None and hops < 8:
                by_chain.setdefault(cursor.id, row)
                cursor = (
                    await self.repo.get(cursor.carried_from_id)
                    if cursor.carried_from_id is not None
                    else None
                )
                hops += 1
        # An id that is itself a row of this version stands for that row first.
        for row in rows:
            by_chain[row.id] = row
        resolved: list[StoredChange] = []
        unresolved: list[str] = []
        twins: dict[int, list[int]] = {}
        for change in changes:
            if isinstance(change, PutBackOverrideChange):
                unresolved.append("A draft cannot put back a different limit.")
                continue
            found = by_chain.get(change.expectation_id)
            if found is None:
                unresolved.append(
                    f"“{change.old_sentence}” is not on the version the draft became."
                )
                continue
            resolved.append(change.model_copy(update={"expectation_id": found.id}))
            if found.status == "confirmed" and not isinstance(change, PutBackChange):
                same = _identity_of(found)
                twins[found.id] = [
                    r.id
                    for r in rows
                    if r.id != found.id
                    and r.status == "confirmed"
                    and _identity_of(r) == same
                    # A tier move is "this one, from here to there": a twin the person had put
                    # at another tier is somewhere else already, and stays.
                    and (not isinstance(change, TierChange) or r.tier == found.tier)
                ]
        return resolved, unresolved, {k: v for k, v in twins.items() if v}

    async def _apply_changes(
        self,
        request: SignatureRequestRow,
        version_id: int,
        changes: Sequence[StoredChange],
        *,
        twins: Mapping[int, Sequence[int]] | None = None,
    ) -> list[int]:
        """Apply the changes with the verbs a person already has. The caller holds the
        transaction and has checked them with :meth:`problems`; the request is decided first, so
        the staleness hooks of those verbs (which watch *waiting* requests) leave it alone."""
        if not await self.requests.decide(request.id, "approved"):
            raise RuntimeError("signature request is not waiting")
        written: list[int] = []
        for change in changes:
            # The row the change names, and any other in force that says the same thing.
            rows = (
                [change.expectation_id, *(twins or {}).get(change.expectation_id, ())]
                if not isinstance(change, PutBackOverrideChange)
                else []
            )
            if isinstance(change, ReplaceChange):
                for row_id in rows:
                    _must(
                        await self.repo.reject(
                            row_id, reason=f"Replaced at the agent's request: {change.reason}"
                        )
                    )
                new = change.expectation
                added = await self.repo.add(
                    version_id,
                    [
                        ExpectationWrite(
                            status="confirmed",
                            tier=new.tier,
                            phase=new.phase,
                            kind=new.kind,
                            expression=new.expression,
                            warning_fault=new.warning_fault,
                            text=new.text,
                            fault=new.fault,
                            sentence=new.sentence,
                            reason=change.reason,
                            proposed_by_thread_id=request.thread_id,
                            proposed_by_draft_id=request.draft_id,
                        )
                    ],
                )
                written.append(added[0].id)
            elif isinstance(change, RetireChange):
                for row_id in rows:
                    _must(
                        await self.repo.reject(
                            row_id, reason=f"Retired at the agent's request: {change.reason}"
                        )
                    )
            elif isinstance(change, TierChange):
                for row_id in rows:
                    _must(await self.repo.set_tier(row_id, change.tier))
            elif isinstance(change, PutBackChange):
                _must(await self.repo.restore(change.expectation_id))
            else:
                _must(await self.repo.restore_override(change.override_id))
        await self.requests.set_written(request.id, written)
        return written

    # ── what is served ───────────────────────────────────────────────

    async def views(self, rows: Sequence[SignatureRequestRow]) -> list[RequestView]:
        return [await self.view(row) for row in rows]

    async def view(self, row: SignatureRequestRow) -> RequestView:
        version = await self.profiles.get_version(row.profile_version_id)
        label = await self.profiles.name_of(version) if version is not None else ""
        version_label = await self.requests.version_label(row.profile_version_id)
        changes: list[ChangeView] = []
        loosens: str | None = None
        if row.changes is not None:
            previews = (
                await self._previews(row)
                if row.status == "waiting" and row.draft_id is None
                else {}
            )
            written = iter(row.written)
            staying = (
                await self._staying(row) if row.status == "waiting" and row.draft_id is None else {}
            )
            for index, change in enumerate(row.changes.changes):
                changes.append(
                    _change_view(
                        change,
                        written_id=next(written, None)
                        if isinstance(change, ReplaceChange)
                        else None,
                        preview=previews.get(index),
                        staying=staying.get(index, []),
                    )
                )
            if row.draft_id is None:
                rows = {r.id: r for r in await self.repo.for_version(row.profile_version_id)}
                if request_loosens(row.changes, rows):
                    loosens = await self.requests.open_prediction_label(row.profile_version_id)
        return RequestView(
            id=row.id,
            status=row.status,
            stale_reason=row.stale_reason,
            reason=row.reason,
            answer_reason=row.answer_reason,
            created_at=row.created_at,
            answered_at=row.answered_at,
            profile_version_id=row.profile_version_id,
            profile_label=label,
            version_label=version_label,
            thread_id=row.thread_id,
            draft_id=row.draft_id,
            readable=row.readable,
            loosens_open_prediction=loosens,
            changes=changes,
        )

    async def _staying(self, row: SignatureRequestRow) -> dict[int, list[StayingView]]:
        """For each replace and retire of a waiting request: the expectations in force, besides
        the ones this request takes out, that read the same channel in the same phase and bound
        the same side (floors under a floor, ceilings under a ceiling, both under a between)."""
        assert row.changes is not None
        in_force = (await self.repo.confirmed_for_versions([row.profile_version_id])).get(
            row.profile_version_id, []
        )
        going = {
            c.expectation_id
            for c in row.changes.changes
            if isinstance(c, ReplaceChange | RetireChange)
        }
        by_id = {r.id: r for r in in_force}
        staying: dict[int, list[StayingView]] = {}
        for index, change in enumerate(row.changes.changes):
            if not isinstance(change, ReplaceChange | RetireChange):
                continue
            target = by_id.get(change.expectation_id)
            if target is None or target.expression is None:
                continue
            channel = target.expression.channel
            sides = compare_sides(target.expression.compare)
            found = [
                StayingView(expectation_id=other.id, words=_words(other))
                for other in in_force
                if other.id not in going
                and other.expression is not None
                and other.expression.channel == channel
                and compare_sides(other.expression.compare) & sides
                and phase_key(other.phase or "") == phase_key(target.phase or "")
            ]
            if found:
                staying[index] = found
        return staying

    async def _previews(self, row: SignatureRequestRow) -> dict[int, PreviewView]:
        """For each replace, retire and put back of a waiting request: over the shots the
        version's checks are read for, how many fail the expectation now and how many would after
        the whole request. The same check code, with the signature as it would then be."""
        from gaggiclanker.shotinfo.render import load_shots

        assert row.changes is not None
        version_id = row.profile_version_id
        shot_ids = await self.requests.shots_judged_by(version_id)
        facts = await load_shots(self.db, shot_ids)
        subjects = [f.check_subject for f in facts]
        now = await checks_for_shots(self.db, subjects)

        in_force = (await self.repo.confirmed_for_versions([version_id])).get(version_id, [])
        after_rows: dict[int, ExpectationRow] = {r.id: r for r in in_force}
        stand_in: dict[int, int] = {}
        for index, change in enumerate(row.changes.changes):
            if isinstance(change, ReplaceChange):
                old = await self.repo.get(change.expectation_id)
                after_rows.pop(change.expectation_id, None)
                fake_id = -(index + 1)
                stand_in[index] = fake_id
                new = change.expectation
                after_rows[fake_id] = ExpectationRow(
                    id=fake_id,
                    signature_id=old.signature_id if old is not None else 0,
                    profile_version_id=version_id,
                    position=old.position if old is not None else 0,
                    tier=new.tier,
                    phase=new.phase,
                    kind=new.kind,
                    expression=new.expression,
                    warning_fault=new.warning_fault,
                    text=new.text,
                    fault=new.fault,
                    sentence=new.sentence,
                    status="confirmed",
                    proposed_at=row.created_at,
                )
            elif isinstance(change, RetireChange):
                after_rows.pop(change.expectation_id, None)
            elif isinstance(change, TierChange):
                if change.expectation_id in after_rows:
                    after_rows[change.expectation_id] = after_rows[
                        change.expectation_id
                    ].model_copy(update={"tier": change.tier})
            elif isinstance(change, PutBackChange):
                back = await self.repo.get(change.expectation_id)
                if back is not None:
                    after_rows[back.id] = back.model_copy(
                        update={"status": "confirmed", "reject_reason": ""}
                    )
        after = await checks_for_shots(
            self.db,
            subjects,
            signatures={version_id: sorted(after_rows.values(), key=lambda r: (r.position, r.id))},
        )

        def failing(checks: Mapping[int, Any], expectation_id: int) -> int:
            return sum(
                1
                for shot in checks.values()
                if any(
                    c.expectation_id == expectation_id and c.status == "failed" for c in shot.checks
                )
            )

        previews: dict[int, PreviewView] = {}
        for index, change in enumerate(row.changes.changes):
            if isinstance(change, ReplaceChange):
                previews[index] = PreviewView(
                    shots=len(subjects),
                    failing_now=failing(now, change.expectation_id),
                    failing_after=failing(after, stand_in[index]),
                )
            elif isinstance(change, RetireChange):
                previews[index] = PreviewView(
                    shots=len(subjects),
                    failing_now=failing(now, change.expectation_id),
                    failing_after=0,
                )
            elif isinstance(change, PutBackChange):
                previews[index] = PreviewView(
                    shots=len(subjects),
                    failing_now=0,
                    failing_after=failing(after, change.expectation_id),
                )
        return previews


def _identity_of(row: ExpectationRow) -> tuple[str, str, str]:
    return expectation_identity(row.kind, row.phase, row.expression, row.text, row.warning_fault)


def _said(problems: Sequence[Problem]) -> str:
    """The reason a card shows: the person's sentences, each ending as it ends."""
    return " ".join(p.person for p in problems)


def _must(result: Any) -> None:
    """A verb refused inside the transaction that checked it: roll the whole answer back."""
    if result.refused is not None:
        raise RuntimeError(f"signature change refused after the check: {result.refused}")


def _limit_of(expression: Expression | None) -> str:
    if expression is None or expression.compare is None:
        return ""
    return limit_text(expression, expression_unit(expression))


def _numbers(built: Sequence[StoredChange | None]) -> list[int]:
    return [n for n, c in enumerate(built, start=1) if c is not None]


def _in_force_problem(
    where: str, row: ExpectationRow, change: ReplaceChange | RetireChange | TierChange
) -> list[Problem]:
    if row.status == "confirmed":
        return []
    verb = {
        "replace": "replaced",
        "retire": "retired",
        "tier": "moved to another tier",
    }[change.kind]
    if row.status == "rejected":
        return [
            Problem(
                f"{where}: {_desc(row)} is one the person rejected, so it is not in force and "
                f"cannot be {verb}; to ask for it back use a put_back change.",
                f"“{_words(row)}” was rejected, so it is not in force and cannot be {verb}.",
            )
        ]
    if row.needs_phase:
        return [
            Problem(
                f"{where}: {_desc(row)} names a phase this profile version no longer has, so it "
                f"is not in force and cannot be {verb}.",
                f"“{_words(row)}” names a phase this profile version no longer has, so it is "
                f"not in force and cannot be {verb}.",
            )
        ]
    return [
        Problem(
            f"{where}: {_desc(row)} is from before signatures were in force at once, so it is "
            f"not in force and cannot be {verb}; the person can reject it and then restore it.",
            f"“{_words(row)}” is from before signatures were in force at once, so it is not "
            f"in force and cannot be {verb}.",
        )
    ]


def _put_back_problem(where: str, row: ExpectationRow) -> list[Problem]:
    if row.status == "rejected" and not row.needs_phase:
        return []
    if row.status == "confirmed":
        return [
            Problem(
                f"{where}: {_desc(row)} is already in force, so there is nothing to put back.",
                f"“{_words(row)}” is already in force, so there is nothing to put back.",
            )
        ]
    if row.needs_phase:
        return [
            Problem(
                f"{where}: {_desc(row)} names a phase this profile version no longer has, so it "
                "cannot be put back.",
                f"“{_words(row)}” names a phase this profile version no longer has, so it "
                "cannot be put back.",
            )
        ]
    return [
        Problem(
            f"{where}: {_desc(row)} was never rejected (it is from before signatures were in "
            "force at once), so there is nothing to put back.",
            f"“{_words(row)}” was never rejected (it is from before signatures were in force at "
            "once), so there is nothing to put back.",
        )
    ]


def _person_why_there(valid: ValidExpectation, existing: Sequence[ExpectationRow]) -> str:
    """Why a replacement cannot be added, in the person's words: the row that is already there."""
    key = expectation_identity(
        valid.kind, valid.phase, valid.expression, valid.text, valid.warning_fault
    )
    for row in existing:
        if (
            expectation_identity(row.kind, row.phase, row.expression, row.text, row.warning_fault)
            != key
        ):
            continue
        if row.status == "confirmed":
            return "is already in force, so it cannot be added again"
        if row.status == "rejected":
            return "is one you rejected, so it cannot be added again"
        if row.needs_phase:
            return "is already there but names a phase this profile version no longer has"
        return "is already there from before signatures were in force at once"
    return "is asked for twice in the request"


def _change_view(
    change: StoredChange,
    *,
    written_id: int | None,
    preview: PreviewView | None,
    staying: list[StayingView],
) -> ChangeView:
    common: dict[str, Any] = {
        "kind": change.kind,
        "reason": change.reason,
        "phase": change.phase,
        "old_sentence": change.old_sentence,
        "old_tier": change.old_tier,
        "old_limit_text": change.old_limit_text,
        "preview": preview,
        "summary": revision_words(change),
        "stays_in_force": staying,
    }
    if isinstance(change, ReplaceChange):
        return ChangeView(
            expectation_id=change.expectation_id,
            tier=change.expectation.tier,
            new_sentence=replacement_words(change),
            new_limit_text=_limit_of(change.expectation.expression),
            new_tier=change.expectation.tier,
            written_expectation_id=written_id,
            **common,
        )
    if isinstance(change, RetireChange):
        return ChangeView(expectation_id=change.expectation_id, tier=change.old_tier, **common)
    if isinstance(change, TierChange):
        # The header names the tier the row has now; the line under it says "critical → important".
        return ChangeView(
            expectation_id=change.expectation_id,
            tier=change.old_tier,
            new_tier=change.tier,
            **common,
        )
    if isinstance(change, PutBackChange):
        return ChangeView(
            expectation_id=change.expectation_id,
            tier=change.old_tier,
            rejected_reason=change.rejected_reason,
            rejected_at=change.rejected_at,
            **common,
        )
    return ChangeView(
        override_id=change.override_id,
        tier=change.old_tier,
        new_sentence=change.new_sentence,
        rejected_reason=change.rejected_reason,
        rejected_at=change.rejected_at,
        **common,
    )
