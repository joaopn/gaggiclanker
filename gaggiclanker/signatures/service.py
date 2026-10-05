"""Proposing signatures, carrying them to new profile versions, and overriding a limit.

The repository stores rows; this is where meaning is checked. Nothing here confirms
anything: a proposal, a carried expectation and an override are all rows a person
answers on the Profiles and Set pages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.signatures import (
    ExpectationRow,
    ExpectationWrite,
    OverrideRow,
    OverrideWrite,
    SignatureRepository,
)
from gaggiclanker.domain.metric_language import (
    Expression,
    PhaseEndAnchor,
    PhaseStartAnchor,
    canonical_form,
)
from gaggiclanker.domain.phase_names import raw_phase_names
from gaggiclanker.domain.signature import (
    ExpectationInput,
    SignatureRefused,
    ValidExpectation,
    phase_key,
    validate_compare,
    validate_expectation,
)

log = structlog.get_logger(__name__)

__all__ = ["DraftSignature", "SignatureService", "split_duplicates", "validate_all"]


@dataclass(frozen=True, slots=True)
class DraftSignature:
    """Expectations an agent proposes with a draft, already validated against the draft's own
    phases: they are stored as **proposed** on the draft's profile version, never confirmed."""

    expectations: tuple[ValidExpectation, ...]
    reason: str
    thread_id: int | None = None
    #: Filled by the draft's store: the sentences of expectations left out because the draft's
    #: version already had them (carried from its base, or repeated), so the tool can say so.
    skipped: list[str] = field(default_factory=list)


def validate_all(
    items: Sequence[ExpectationInput], profile_phases: Sequence[str]
) -> list[ValidExpectation]:
    """Every expectation validated, and every problem named at once, so a retry fixes them all."""
    valid: list[ValidExpectation] = []
    problems: list[str] = []
    for number, item in enumerate(items, start=1):
        try:
            valid.append(validate_expectation(item, profile_phases, where=f"expectation {number}"))
        except SignatureRefused as refused:
            problems.append(str(refused))
    if problems:
        raise SignatureRefused(" ".join(problems))
    return valid


def _identity(
    kind: str,
    phase: str | None,
    expression: Expression | None,
    text: str,
    warning: str | None,
) -> tuple[str, str, str]:
    """What makes two expectations the same: kind, phase and the expression or text itself.

    The expression's canonical form includes its limit, so the same measure with another limit
    is another expectation; the tier is not part of it (the person can move it).
    """
    what = (
        canonical_form(expression)
        if expression is not None
        else (warning or " ".join(text.split()).casefold())
    )
    return kind, phase_key(phase or ""), what


def split_duplicates(
    valid: Sequence[ValidExpectation], existing: Sequence[ExpectationRow]
) -> tuple[list[ValidExpectation], list[tuple[int, ValidExpectation, str]]]:
    """Split an expectation list into what is new and what is already there.

    "There" is proposed and waiting or confirmed on the profile version, or repeated earlier in
    the same list. Each duplicate comes back as (its 1-based number, it, why).
    """
    seen: dict[tuple[str, str, str], str] = {
        _identity(r.kind, r.phase, r.expression, r.text, r.warning_fault): (
            "already confirmed"
            if r.status == "confirmed"
            else "already proposed and waiting for the person"
        )
        for r in existing
        if r.status in ("proposed", "confirmed")
    }
    fresh: list[ValidExpectation] = []
    duplicates: list[tuple[int, ValidExpectation, str]] = []
    for number, v in enumerate(valid, start=1):
        key = _identity(v.kind, v.phase, v.expression, v.text, v.warning_fault)
        if key in seen:
            duplicates.append((number, v, seen[key]))
        else:
            seen[key] = "repeated in this proposal"
            fresh.append(v)
    return fresh, duplicates


def refuse_duplicates(
    valid: Sequence[ValidExpectation], existing: Sequence[ExpectationRow]
) -> None:
    """Refuse an expectation already there (waiting or confirmed) or repeated in the batch.

    Saying "it is already there" is not teaching: the proposer learns that the person has it or
    will see it, never what a person did not confirm.
    """
    _, duplicates = split_duplicates(valid, existing)
    if duplicates:
        raise SignatureRefused(
            " ".join(
                f"expectation {number} is {why}: leave it out and propose only what is new."
                for number, _, why in duplicates
            )
        )


def _writes(
    valid: Sequence[ValidExpectation],
    *,
    reason: str,
    thread_id: int | None,
    draft_id: int | None,
) -> list[ExpectationWrite]:
    return [
        ExpectationWrite(
            tier=v.tier,  # type: ignore[arg-type]
            phase=v.phase,
            kind=v.kind,  # type: ignore[arg-type]
            expression=v.expression,
            warning_fault=v.warning_fault,
            text=v.text,
            fault=v.fault,
            sentence=v.sentence,
            reason=reason,
            proposed_by_thread_id=thread_id,
            proposed_by_draft_id=draft_id,
        )
        for v in valid
    ]


def _named_phases(row: ExpectationRow) -> list[str]:
    """Every phase an expectation depends on: its own and the ones its window names."""
    names = [] if row.phase is None else [row.phase]
    expr: Expression | None = row.expression
    if expr is not None:
        window = expr.window
        if window.phase is not None:
            names.append(window.phase)
        for anchor in (window.start, window.end):
            if isinstance(anchor, PhaseStartAnchor):
                names.append(anchor.phase_start)
            elif isinstance(anchor, PhaseEndAnchor):
                names.append(anchor.phase_end)
    return names


class SignatureService:
    """The signature writes that need a profile's phases."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = SignatureRepository(db)
        self.profiles = ProfilesRepository(db)

    async def phase_names(self, profile_version_id: int) -> list[str] | None:
        """The profile version's phase names, or ``None`` when it is not stored."""
        version = await self.profiles.get_version(profile_version_id)
        if version is None:
            return None
        return raw_phase_names(version.profile) or []

    async def propose(
        self,
        profile_version_id: int,
        items: Sequence[ExpectationInput],
        *,
        reason: str,
        thread_id: int | None = None,
        draft_id: int | None = None,
        phases: Sequence[str] | None = None,
    ) -> list[ExpectationRow]:
        """Validate and store proposed expectations on a profile version.

        ``phases`` are the phase names to validate against when they are not the stored
        version's (a draft's, before it is stored). Raises :class:`SignatureRefused` naming
        every problem, and writes nothing then.
        """
        if not items:
            raise SignatureRefused("Propose at least one expectation.")
        names = list(phases) if phases is not None else await self.phase_names(profile_version_id)
        if names is None:
            raise SignatureRefused(f"There is no profile version {profile_version_id}.")
        valid = validate_all(items, names)
        refuse_duplicates(valid, await self.repo.for_version(profile_version_id))
        return await self.repo.add(
            profile_version_id,
            _writes(valid, reason=reason, thread_id=thread_id, draft_id=draft_id),
        )

    async def add_valid(
        self,
        profile_version_id: int,
        valid: Sequence[ValidExpectation],
        *,
        reason: str,
        thread_id: int | None,
        draft_id: int | None,
    ) -> list[ExpectationRow]:
        """Store expectations that were validated already (a draft's, with the draft)."""
        return await self.repo.add(
            profile_version_id,
            _writes(valid, reason=reason, thread_id=thread_id, draft_id=draft_id),
        )

    async def carry(self, from_version_id: int, to_version_id: int) -> list[ExpectationRow]:
        """Carry the previous version's **confirmed** expectations to a new version, as proposals.

        One whose phases (its own and every one its window names) all still exist by name
        becomes *proposed (carried)*: one click to confirm. One whose phase is gone becomes
        *needs a new phase* and cannot be confirmed; it is never matched to another phase by
        guess, whatever its position. Carrying twice adds nothing.
        """
        if from_version_id == to_version_id:
            return []
        confirmed = (await self.repo.confirmed_for_versions([from_version_id])).get(
            from_version_id, []
        )
        if not confirmed:
            return []
        names = await self.phase_names(to_version_id)
        if names is None:
            return []
        have = {phase_key(n) for n in names}
        already = await self.repo.carried_ids(to_version_id)
        writes: list[ExpectationWrite] = []
        for row in confirmed:
            if row.id in already:
                continue
            gone = any(phase_key(n) not in have for n in _named_phases(row))
            writes.append(
                ExpectationWrite(
                    tier=row.tier,
                    phase=row.phase,
                    kind=row.kind,
                    expression=row.expression,
                    warning_fault=row.warning_fault,
                    text=row.text,
                    fault=row.fault,
                    sentence=row.sentence,
                    reason=row.reason,
                    needs_phase=gone,
                    carried_from_id=row.id,
                )
            )
        return await self.repo.add(to_version_id, writes)

    async def carry_quietly(self, from_version_id: int | None, to_version_id: int) -> None:
        """:meth:`carry`, for the paths that store a profile version (the mirror, a draft).

        A failure here must never stop a profile from being stored or a sync from finishing:
        the carried proposals are a convenience for the person, and a missing one is a
        signature to propose again, so the failure is logged and the caller goes on.
        """
        if from_version_id is None:
            return
        try:
            await self.carry(from_version_id, to_version_id)
        except Exception:
            log.exception(
                "signature_carry_failed",
                from_version_id=from_version_id,
                to_version_id=to_version_id,
            )

    # ── overrides ────────────────────────────────────────────────────

    async def propose_override(
        self,
        *,
        set_version_id: int,
        profile_version_id: int | None,
        expectation_id: int,
        compare: Mapping[str, Any],
        reason: str,
        thread_id: int | None,
    ) -> OverrideRow:
        """Propose a different limit for one confirmed measure on one Set version.

        Changes the compare values only: the same comparison and the same kind of bound, so
        the expression, the tier and the phase are the profile's. Raises
        :class:`SignatureRefused` with what was wrong.
        """
        target = await self.repo.get(expectation_id)
        if (
            target is None
            or profile_version_id is None
            or target.profile_version_id != profile_version_id
        ):
            raise SignatureRefused(
                "That expectation is not one of this version's profile signature."
            )
        if target.status != "confirmed":
            raise SignatureRefused(
                "Only a confirmed expectation can be overridden: the person has not confirmed "
                "this one."
            )
        if (
            target.kind != "measure"
            or target.expression is None
            or target.expression.compare is None
        ):
            raise SignatureRefused("Only a measure has a limit to override.")
        new = validate_compare(compare)
        old = target.expression.compare
        if new.op != old.op:
            raise SignatureRefused(
                f"An override changes the limit's numbers, not its comparison: this one is "
                f"{old.op!r}, so use {old.op!r} with other values."
            )
        stored = await self.repo.add_override(
            OverrideWrite(
                set_version_id=set_version_id,
                expectation_id=expectation_id,
                compare=new,
                reason=reason,
                proposed_by_thread_id=thread_id,
            )
        )
        if stored is None:
            raise SignatureRefused(
                "This version already has a confirmed override, which is the person's answer: they "
                "can withdraw it, and then you can propose another. Say what you would change "
                "instead of proposing one now."
            )
        return stored
