"""A shot's verdict and reading state, worked out whenever it is read.

Nothing here is stored. The verdict is a function of the shot's checks as they are now (the
confirmed signature's results and the universal warnings, :mod:`gaggiclanker.domain.signature`)
and of the reading in force, so confirming an expectation, rejecting a claim or reading the shot
again changes it at once, with no re-derivation and no migration.

**The reading in force is the shot's newest finished (`ok`) reading, for every viewer.** A newer
reading that is running, failed or was interrupted changes only the badge's state text
(``Reading…``, ``Failed to run``) and where the shot sorts; it never hides or replaces the
results of the reading in force. One that finishes `ok` replaces it.

**Two merges, and the difference between them is the rule that nothing unconfirmed teaches.**
The free-text expectations of a confirmed signature can only be answered by a reading. A
reading's answer becomes a check of the expectation's tier (a failed critical one red, an
important one amber, a held one in the held group):

* ``person``: what the web shows. Every answer not rejected counts, and one nobody has answered
  yet is marked ``unverified`` so the page can draw it outlined. A rejected answer leaves the
  verdict, so rejecting "decline: unstable" can turn a red badge green.
* ``chat``: what an agent is given. Only an answer a person confirmed counts; one nobody
  confirmed leaves its expectation "checked by the reading, not confirmed". The summary is never
  in it.

An answer counts only while its expectation is still confirmed **under the same id** (re-proposing
or rejecting the expectation retires it, because the check it would replace is no longer there).

**The reading state** of a shot (``not_readable``, ``unread``, ``running``, ``failed``, ``read``)
and the badge text that goes with it are built here by code and never by the model.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.repos.reviews import ReadingRecord, ReviewClaimRow
from gaggiclanker.domain.signature import Check, ShotChecks
from gaggiclanker.domain.warnings import badge_text

__all__ = [
    "READING_TEXT",
    "ReadingBlock",
    "Served",
    "Viewer",
    "merge_reading",
    "review_key",
    "serve_reading",
]

type Viewer = Literal["person", "chat"]
type ReadingState = Literal["not_readable", "unread", "running", "failed", "read"]
type Verdict = Literal["entries", "as_intended", "no_signature"]

#: The badge text of every state that has no entry to name, in one place.
READING_TEXT = {
    "unread": "Review",
    "running": "Reading…",
    "failed": "Failed to run",
    "as_intended": "As intended",
    "no_signature": "No signature",
}


class ReadingBlock(BaseModel):
    """The ``reading`` block of a shot, as the list, the detail and the fields serve it."""

    model_config = ConfigDict(extra="forbid")

    #: ``not_readable`` (discarded or quarantined), ``unread``, ``running``, ``failed`` or ``read``.
    state: ReadingState
    #: The newest review's id, whatever its state (the reading in force is the newest finished
    #: one, which `reviews` on the shot detail lists); ``None`` for a shot never read.
    review_id: int | None = None
    #: Once read: ``entries`` (the badge names the failures), ``as_intended`` (a confirmed
    #: signature and nothing failed) or ``no_signature`` (nothing to be checked against).
    verdict: Verdict | None = None
    #: The id of the reading in force: the shot's newest finished (`ok`) review, or ``None`` when
    #: it has none. Claims are answered through this id; ``review_id`` is the newest attempt's and
    #: differs from it while a newer reading runs or after it failed.
    in_force_id: int | None = None
    #: How many claims of the reading in force are still waiting for the person; the badge is
    #: outlined while this is not zero and filled once it is.
    unanswered: int = 0
    #: Why it failed, for a ``failed`` reading.
    reason: str | None = None
    #: The one sentence of the reading in force: the person's, never served to a chat.
    summary: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class Served:
    """One shot as the person reads it: the reading block, the merged checks and the badge."""

    block: ReadingBlock
    checks: ShotChecks
    #: The text of the badge: the failures, ``Reading…``, ``Failed to run``, ``As intended``,
    #: ``No signature``, ``Review``; ``None`` for a shot nobody can read and nothing is wrong with.
    badge: str | None

    @property
    def entries(self) -> list[Check]:
        return self.checks.badge_entries


def _result_check(base: Check, claim: ReviewClaimRow, *, unverified: bool) -> Check:
    """A free-text expectation's check, answered by a reading."""
    held = bool(claim.held)
    if base.tier == "context":
        rank = 6
    elif held:
        rank = 5
    else:
        rank = 0 if base.tier == "critical" else 1
    verdict = "held" if held else "failed"
    return dataclasses.replace(
        base,
        status="held" if held else "failed",
        held=held,
        # Only a failure has a word; the expectation's own, as the reading wrote it down.
        fault=None if held else (claim.fault or base.fault),
        detail=f"{base.sentence}; the reading says it {verdict}: {claim.text}",
        # Ordered by where the reading's window starts, as a measure is by its phase.
        at_s=claim.start_s if claim.start_s is not None else base.at_s,
        rank=rank,
        unverified=unverified,
    )


def merge_reading(checks: ShotChecks, record: ReadingRecord | None, viewer: Viewer) -> ShotChecks:
    """The checks with the reading's free-text answers merged in, for one viewer."""
    if record is None:
        return checks
    # The reading in force, whatever has been started since.
    if record.finished is None:
        return checks
    wanted = "confirmed" if viewer == "chat" else None
    counted = {
        c.expectation_id: c
        for c in record.claims
        if c.kind == "free_text"
        and c.expectation_id is not None
        and (c.status == "confirmed" if wanted else c.status != "rejected")
    }
    answered_unconfirmed = {
        c.expectation_id
        for c in record.claims
        if c.kind == "free_text" and c.status != "confirmed" and c.expectation_id is not None
    }
    changed = False
    merged: list[Check] = []
    for check in checks.checks:
        if check.kind != "free_text" or check.expectation_id is None:
            merged.append(check)
            continue
        claim = counted.get(check.expectation_id)
        if claim is not None:
            merged.append(_result_check(check, claim, unverified=claim.status == "proposed"))
            changed = True
        elif viewer == "chat" and check.expectation_id in answered_unconfirmed:
            merged.append(
                dataclasses.replace(
                    check,
                    detail=f"{check.sentence} (checked by the reading, not confirmed).",
                )
            )
            changed = True
        else:
            merged.append(check)
    if not changed:
        return checks
    merged.sort(key=Check.order)
    return ShotChecks(checks=tuple(merged), state=checks.state)


def _state(record: ReadingRecord | None, *, readable: bool) -> ReadingState:
    if not readable:
        return "not_readable"
    latest = record.latest if record is not None else None
    if latest is None:
        return "unread"
    if latest.status == "running":
        return "running"
    if latest.status != "ok":
        return "failed"
    return "read"


def serve_reading(
    signature_checks: ShotChecks,
    record: ReadingRecord | None,
    *,
    readable: bool,
) -> Served:
    """The reading block, merged checks and badge of one shot for the person.

    ``readable`` is false for a shot that was discarded or quarantined: its checks and badge are
    served as they are and nothing can be started on it. The results, the unanswered count, the
    summary and the time are those of the reading in force (the newest finished one), also while
    a newer one runs or after it failed; only ``state``, ``review_id`` (the newest review's) and
    the badge's words follow the newest attempt.
    """
    state = _state(record, readable=readable)
    checks = (
        merge_reading(signature_checks, record, "person")
        if state in ("read", "running", "failed")
        else signature_checks
    )
    entries = checks.badge_entries
    latest = record.latest if record is not None else None
    in_force = record.finished if record is not None else None

    verdict: Verdict | None = None
    unanswered = 0
    reason: str | None = None
    summary: str | None = None
    finished_at: str | None = None
    badge: str | None
    if record is not None and in_force is not None and state in ("read", "running", "failed"):
        unanswered = sum(1 for claim in record.claims if claim.status == "proposed")
        summary = in_force.summary
        finished_at = in_force.finished_at
    if state == "read":
        verdict = (
            "entries"
            if entries
            else ("as_intended" if checks.state.read_with_signature else "no_signature")
        )
        badge = badge_text(entries) if entries else READING_TEXT[verdict]
    elif state == "running":
        badge = READING_TEXT["running"]
    elif state == "failed":
        assert latest is not None
        reason = latest.error
        badge = READING_TEXT["failed"]
    elif state == "unread":
        badge = badge_text(entries) or READING_TEXT["unread"]
    else:
        badge = badge_text(entries)
    return Served(
        block=ReadingBlock(
            state=state,
            review_id=latest.id if latest is not None else None,
            in_force_id=in_force.id if in_force is not None else None,
            verdict=verdict,
            unanswered=unanswered,
            reason=reason,
            summary=summary,
            finished_at=finished_at,
        ),
        checks=checks,
        badge=badge,
    )


def _group(served: Served) -> int:
    """Where a shot with no badge entry stands in the Review sort (the entries come first).

    Work to do above work done: ``Failed to run``, then ``Reading…``, then ``No signature``, then
    unread shots, then ``As intended``, then shots nobody can read and nothing is wrong with.
    """
    state, verdict = served.block.state, served.block.verdict
    if state == "failed":
        return 1
    if state == "running":
        return 2
    if state == "read":
        return 3 if verdict == "no_signature" else 5
    if state == "unread":
        return 4
    return 6


def review_key(served: Served) -> tuple[int, tuple[int, bool, float, int]]:
    """What the shots table's Review column sorts by (smaller is worse, so first when descending).

    A shot with a badge entry sorts by its first entry, the one the badge names: its group (red,
    amber, an unexpected warning, an expected one), a phase's before a whole-shot one, then the
    time in the shot. The others follow in the order of :func:`_group`. Shots with an equal key
    are put newest first by the caller (`ShotsRepository`), which makes the whole key total.
    """
    entries = served.entries
    if entries:
        return (0, entries[0].order())
    return (_group(served), (0, False, 0.0, 0))
