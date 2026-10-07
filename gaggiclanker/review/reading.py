"""A shot's Curve check and its review, worked out whenever it is read, and kept apart.

Nothing here is stored. A shot serves two things that used to be one cell, and they never
change each other:

* **The Curve check** is the deterministic part: the shot's failed critical and important
  expectations and its warnings, from the confirmed signature of its profile version and the
  shot's own numbers (:mod:`gaggiclanker.domain.signature`). It is the same for a shot that was
  reviewed, is being reviewed, whose review failed, or whose claims a person rejected. Free-text
  expectations are not part of it: a number cannot decide them, so the review answers them.
* **The review** is what the model wrote, as the **review in force**: the shot's newest finished
  (`ok`) review, for every viewer. A newer review that is running, failed or was interrupted
  changes only the state and the badge's words (``Reviewing…``, ``Failed to run``) and where the
  shot sorts; it never hides or replaces the claims of the review in force. One that finishes
  `ok` replaces it.

**The review's faults are built by code from its claims, never typed by the model**, and they
come from claims a person did not reject and whose numbers bear them out: a free-text
expectation the model says failed (red for a critical one, amber for an important one, while
the expectation is still confirmed under the same id) and a claim that carries a fault word
(amber). They are ordered by tier rank, then a phase's before a whole-shot one, then by where
the window starts.

The person and the chat see the same claims: every claim of the review in force that is not
rejected, and never the summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.reviews import ReadingRecord, ReviewClaimRow
from gaggiclanker.domain.signature import Check, ShotChecks
from gaggiclanker.domain.warnings import SHOT, badge_text

__all__ = [
    "REVIEW_TEXT",
    "ChecksBlock",
    "EntryOut",
    "ReviewBlock",
    "ReviewEntry",
    "Served",
    "review_entries",
    "review_key",
    "serve_review",
]

type ReviewState = Literal["not_reviewable", "unreviewed", "running", "failed", "reviewed"]
type Verdict = Literal["entries", "as_intended", "no_faults"]

#: The badge text of every state that has no entry to name, in one place.
REVIEW_TEXT = {
    "running": "Reviewing…",
    "failed": "Failed to run",
    "as_intended": "As intended",
    "no_faults": "No faults",
}

#: The ranks of the review's own entries. A failed free-text expectation takes its tier's rank (the
#: same 0 and 1 a failed measure has, so the colours agree); a claim with a fault word follows.
_RANK_CRITICAL, _RANK_IMPORTANT, _RANK_CLAIM = 0, 1, 2


class EntryOut(BaseModel):
    """One entry of a badge: a fault, as the lists, the detail and the fields serve it."""

    model_config = ConfigDict(extra="forbid")

    #: The shot's own phase name, or ``Shot`` for a fault of the whole shot.
    phase: str
    fault: str
    #: ``red`` (a failed critical expectation), ``amber`` (a failed important one, a claim with a
    #: fault word, or a universal warning nothing marks as expected) or ``grey`` (a warning the
    #: signature expects).
    severity: str
    detail: str
    phase_number: int | None
    at_s: float
    #: The tier of the expectation behind it (``critical``, ``important``, ``context``), or
    #: ``None`` for a universal warning nothing marks as expected, and for a claim.
    tier: str | None = None
    #: ``failed`` (an expectation), ``warning`` (a universal one), ``expected`` (one the signature
    #: says is part of the design: grey) or ``claim`` (the review's observation).
    status: str = "warning"
    expectation_id: int | None = None
    #: The review claim behind an entry the review made; ``None`` for a check.
    claim_id: int | None = None


class ChecksBlock(BaseModel):
    """The Curve check of a shot: only the deterministic checks and warnings."""

    model_config = ConfigDict(extra="forbid")

    #: ``ramp: early yield +1``, built by code; ``None`` when nothing failed or was raised.
    badge: str | None = None
    #: Most severe first, then in the order of the shot.
    entries: list[EntryOut]


class ReviewBlock(BaseModel):
    """The review of a shot: what the model wrote, and whether there is one."""

    model_config = ConfigDict(extra="forbid")

    #: ``not_reviewable`` (discarded or quarantined), ``unreviewed``, ``running``, ``failed`` (the
    #: newest review failed or was interrupted) or ``reviewed``.
    state: ReviewState
    #: The Review column's text: the faults, ``Reviewing…``, ``Failed to run``, ``As intended`` or
    #: ``No faults``; ``None`` for a shot nobody can review and for one not reviewed yet.
    badge: str | None = None
    #: The model's faults in "phase: fault" form, from the review in force; never from a claim a
    #: person rejected, and also while a newer review runs or after one failed.
    entries: list[EntryOut]
    #: Once reviewed: ``entries``, ``as_intended`` (a confirmed signature and no fault) or
    #: ``no_faults`` (no confirmed signature to hold it against).
    verdict: Verdict | None = None
    #: The one sentence of the review in force: the person's, never served to a chat.
    summary: str | None = None
    #: Why the newest review failed, for a ``failed`` state.
    reason: str | None = None
    #: The newest review's id, whatever its state; ``None`` for a shot never reviewed.
    review_id: int | None = None
    #: The id of the review in force: the shot's newest finished (`ok`) review, or ``None``. Claims
    #: are answered through this id; ``review_id`` differs from it while a newer review runs or
    #: after it failed.
    in_force_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    """One fault the review made, before it is served."""

    phase: str
    fault: str
    severity: str
    detail: str
    phase_number: int | None
    at_s: float
    tier: str | None
    status: str
    expectation_id: int | None
    claim_id: int
    rank: int
    seq: int
    shot_wide: bool

    @property
    def badge(self) -> str:
        """``ramp: early yield``."""
        return f"{self.phase}: {self.fault}"

    def order(self) -> tuple[int, bool, float, int]:
        return (self.rank, self.shot_wide, self.at_s, self.seq)

    def out(self) -> EntryOut:
        return EntryOut(
            phase=self.phase,
            fault=self.fault,
            severity=self.severity,
            detail=self.detail,
            phase_number=self.phase_number,
            at_s=self.at_s,
            tier=self.tier,
            status=self.status,
            expectation_id=self.expectation_id,
            claim_id=self.claim_id,
        )


@dataclass(frozen=True, slots=True)
class Served:
    """One shot as the person reads it: the Curve check, and the review beside it."""

    #: The checks the Curve check is made of, with the signature state (the whole list, held and
    #: unmeasured and free-text ones too, as the fields serve it).
    checks: ShotChecks
    checks_block: ChecksBlock
    review: ReviewBlock
    #: The review's own entries, in order, for the sort.
    review_entries: tuple[ReviewEntry, ...]


def check_entry_out(check: Check) -> EntryOut:
    return EntryOut.model_validate({**check.as_dict(), "claim_id": None})


def review_entries(
    signature_checks: ShotChecks, claims: tuple[ReviewClaimRow, ...]
) -> list[ReviewEntry]:
    """The faults of the review in force, from the claims a person did not reject.

    A claim the numbers do not bear out (``supported`` false) is no fault either: it stays in the
    review box, marked, and the chat is still told it, but the badge, its "+N" and the sort key
    leave it out.

    A free-text result counts only while its expectation is still confirmed **under the same
    id** (re-proposing or rejecting the expectation retires it: the tier it would be drawn in is
    gone with it), and only a result that failed. A held one, and one on a context expectation,
    raise nothing.
    """
    expectations = {
        c.expectation_id: c
        for c in signature_checks.checks
        if c.kind == "free_text" and c.expectation_id is not None
    }
    found: list[ReviewEntry] = []
    for claim in claims:
        if claim.status == "rejected" or not claim.supported:
            continue
        if claim.kind == "free_text":
            base = expectations.get(claim.expectation_id) if claim.expectation_id else None
            if base is None or claim.held or base.tier not in ("critical", "important"):
                continue
            rank = _RANK_CRITICAL if base.tier == "critical" else _RANK_IMPORTANT
            found.append(
                ReviewEntry(
                    phase=base.phase,
                    fault=claim.fault or base.fault or "",
                    severity="red" if rank == _RANK_CRITICAL else "amber",
                    detail=f"{base.sentence}; the review says it failed: {claim.text}",
                    phase_number=base.phase_number,
                    # Ordered by where the review's window starts, as a measure is by its phase.
                    at_s=claim.start_s if claim.start_s is not None else base.at_s,
                    tier=base.tier,
                    status="failed",
                    expectation_id=base.expectation_id,
                    claim_id=claim.id,
                    rank=rank,
                    seq=claim.position,
                    shot_wide=base.shot_wide,
                )
            )
        elif claim.kind == "claim" and claim.fault:
            found.append(
                ReviewEntry(
                    phase=claim.phase or SHOT,
                    fault=claim.fault,
                    severity="amber",
                    detail=claim.text,
                    phase_number=None,
                    at_s=claim.start_s if claim.start_s is not None else 0.0,
                    tier=None,
                    status="claim",
                    expectation_id=None,
                    claim_id=claim.id,
                    rank=_RANK_CLAIM,
                    seq=claim.position,
                    shot_wide=claim.phase is None,
                )
            )
    return sorted(found, key=ReviewEntry.order)


def _state(record: ReadingRecord | None, *, reviewable: bool) -> ReviewState:
    if not reviewable:
        return "not_reviewable"
    latest = record.latest if record is not None else None
    if latest is None:
        return "unreviewed"
    if latest.status == "running":
        return "running"
    if latest.status != "ok":
        return "failed"
    return "reviewed"


def serve_review(
    signature_checks: ShotChecks,
    record: ReadingRecord | None,
    *,
    reviewable: bool,
) -> Served:
    """The Curve check and the review of one shot.

    ``reviewable`` is false for a shot that was discarded or quarantined: its Curve check is
    served as it is, its review block names no entries and nothing can be started on it. The
    claims, the summary and the entries are those of the review in force (the newest finished
    one), also while a newer one runs or after it failed; only ``state``, ``review_id`` (the
    newest review's) and the badge's words follow the newest attempt. **The Curve check is
    ``signature_checks`` whatever the review does.**
    """
    state = _state(record, reviewable=reviewable)
    latest = record.latest if record is not None else None
    in_force = record.finished if record is not None else None
    claims = record.claims if record is not None and in_force is not None else ()
    entries: list[ReviewEntry] = (
        review_entries(signature_checks, claims) if state != "not_reviewable" else []
    )
    check_entries = signature_checks.badge_entries

    verdict: Verdict | None = None
    reason: str | None = None
    badge: str | None = None
    if state == "reviewed":
        if entries:
            verdict = "entries"
            badge = badge_text(entries)
        else:
            verdict = "as_intended" if signature_checks.state.read_with_signature else "no_faults"
            badge = REVIEW_TEXT[verdict]
    elif state == "running":
        badge = REVIEW_TEXT["running"]
    elif state == "failed":
        assert latest is not None
        reason = latest.error
        badge = REVIEW_TEXT["failed"]
    return Served(
        checks=signature_checks,
        checks_block=ChecksBlock(
            badge=badge_text(check_entries),
            entries=[check_entry_out(c) for c in check_entries],
        ),
        review=ReviewBlock(
            state=state,
            badge=badge,
            entries=[entry.out() for entry in entries],
            verdict=verdict,
            summary=in_force.summary
            if in_force is not None and state != "not_reviewable"
            else None,
            reason=reason,
            review_id=latest.id if latest is not None else None,
            in_force_id=in_force.id if in_force is not None else None,
        ),
        review_entries=tuple(entries),
    )


def _group(served: Served) -> int:
    """Where a shot with no fault to name stands in the Review sort (the entries come first).

    Work to do above work done: ``Failed to run``, then ``Reviewing…``, then ``No faults``, then
    shots not reviewed yet, then ``As intended``, then shots nobody can review.
    """
    state, verdict = served.review.state, served.review.verdict
    if state == "failed":
        return 1
    if state == "running":
        return 2
    if state == "reviewed":
        return 3 if verdict == "no_faults" else 5
    if state == "unreviewed":
        return 4
    return 6


def review_key(served: Served) -> tuple[int, tuple[int, bool, float, int]]:
    """What the shots table's Review column sorts by (smaller is worse, so first when descending).

    What the column shows is what it sorts by. A reviewed shot with a fault sorts by its first
    entry, the one the badge names: its tier's rank, a phase's before a whole-shot one, then the
    time in the shot. Every other shot follows in the order of :func:`_group`. Shots with an equal
    key are put newest first by the caller (`ShotsRepository`), which makes the whole key total.
    """
    if served.review.state == "reviewed" and served.review_entries:
        return (0, served.review_entries[0].order())
    return (_group(served), (0, False, 0.0, 0))
