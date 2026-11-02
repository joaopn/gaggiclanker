"""What a conversation is told about a profile's signature.

Two pieces, kept apart because they move differently.

* **The signature itself**, in the profile block at the very top of the Recipe conversation's
  opening context (and the design conversation's fork block): the expectations **in force**
  for the profile version, one line each, tier first, with the Recipe version's override in
  force stated beside the limit it changes. An agent's proposal is in force at once, so the
  block moves when one is proposed, when a person rejects or restores one and when a profile
  version is carried; a new shot, a grade, a proposal of another kind, a version or a revert
  never moves it. A profile version with nothing in force says so and asks the conversation
  to propose one (when it turns to how the shots behave, not at every message).

* **What this conversation proposed**, after the volatile part: its own expectations and
  what the person did with each (a rejection carries the reason they gave). Keyed by the
  thread that wrote the rows, never by version, so no other conversation is told what was
  proposed or rejected.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.pins import judged_profile_version
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.metric_language import compare_words, limit_words
from gaggiclanker.domain.signature import expectation_line
from gaggiclanker.domain.signature_changes import (
    PutBackChange,
    ReplaceChange,
    RetireChange,
    StoredChange,
    TierChange,
)

__all__ = [
    "NO_SIGNATURE",
    "SIGNATURE_REQUESTS_TITLE",
    "signature_answers_block",
    "signature_block",
    "signature_lines",
]

#: Said when a profile version has no expectation in force. One sentence, pinned by a test: it
#: asks to propose one when the conversation turns to how the shots behave, and says what a
#: proposal is (in force at once, until the person rejects it).
NO_SIGNATURE = (
    "This profile version has no signature in force, so its shots are read without one. When "
    "the conversation turns to how its shots behave, propose one with propose_signature "
    "(profile version {id}): what the profile is built to do, in a few expectations, never "
    "what one shot did. What you propose is in force at once, every shot on the profile is "
    "checked against it, and the person can reject any expectation."
)


async def signature_block(
    db: Database,
    profile_version_id: int,
    recipe_version_id: int | None,
) -> list[str]:
    """A profile version's signature in force as lines, or the sentence saying there is none.

    A pin's copy is read as the profile it was copied from: that signature judges its shots.
    """
    profile_version_id = await judged_profile_version(db, profile_version_id)
    repo = SignatureRepository(db)
    rows = (await repo.confirmed_for_versions([profile_version_id])).get(profile_version_id, [])
    if not rows:
        return ["", NO_SIGNATURE.format(id=profile_version_id)]
    override = None
    if recipe_version_id is not None:
        override = (await repo.confirmed_overrides([recipe_version_id])).get(recipe_version_id)
    lines = [
        "",
        f"What profile version {profile_version_id} is FOR (its signature: every line is in "
        "force unless the person rejects it, and every shot on it is checked against them; the "
        "number is the expectation's id):",
    ]
    for row in _tier_order(rows):
        compare = (
            override.compare if override is not None and override.expectation_id == row.id else None
        )
        lines.append(f"- #{row.id} {expectation_line(row, compare)}")
    return lines


async def signature_lines(db: Database, profile_version_id: int) -> list[str]:
    """A profile version's expectations in force, one line each, tier first (a pin's copy reads
    as the profile it was copied from)."""
    profile_version_id = await judged_profile_version(db, profile_version_id)
    rows = (await SignatureRepository(db).confirmed_for_versions([profile_version_id])).get(
        profile_version_id, []
    )
    return [f"#{row.id} {expectation_line(row)}" for row in _tier_order(rows)]


def _tier_order(rows: list[ExpectationRow]) -> list[ExpectationRow]:
    rank = {"critical": 0, "important": 1, "context": 2}
    return sorted(rows, key=lambda r: (rank[r.tier], r.position))


def _answer_words(row: ExpectationRow) -> str:
    if row.status == "rejected":
        reason = f' They said: "{row.reject_reason}".' if row.reject_reason else ""
        return f"rejected by the person.{reason} Do not propose it again as it was."
    if row.status == "confirmed":
        return "in force unless the person rejects it: it is in the signature above."
    return "not in force: nothing is checked against it"


SIGNATURE_REQUESTS_TITLE = "SIGNATURE CHANGES YOU REQUESTED IN THIS CONVERSATION"


async def signature_answers_block(db: Database, thread_id: int | None) -> list[str]:
    """What this conversation proposed as expectations, what it asked to change, and what the
    person did with each.

    **This conversation's only.** One in force is also in the signature above and is only said
    to be in force; a rejected one carries the person's reason, so the conversation does not
    offer it again. The changes it asked for follow, one line each with the request's state.
    """
    if thread_id is None:
        return []
    proposals = await _expectation_answers(db, thread_id)
    requests = await signature_requests_block(db, thread_id)
    if proposals and requests:
        return [*proposals, "", *requests]
    return proposals or requests


async def signature_requests_block(db: Database, thread_id: int) -> list[str]:
    """The changes this conversation asked for, one line each, with the request's state.

    Waiting: nothing changes until the person presses Approve. Approved: in force now, naming
    the row it wrote. Declined, with the reason when given. No longer open, with the reason.
    """
    requests = await SignatureRequestsRepository(db).proposed_in(thread_id)
    if not requests:
        return []
    lines = [SIGNATURE_REQUESTS_TITLE]
    for request in requests:
        if request.changes is None:
            lines.append(f"- request #{request.id}: it can no longer be read")
            continue
        written = iter(request.written)
        for change in request.changes.changes:
            if request.status == "waiting":
                state = "waiting: nothing changes until the person presses Approve" + (
                    " on the draft" if request.draft_id is not None else ""
                )
            elif request.status == "approved":
                made = next(written, None) if isinstance(change, ReplaceChange) else None
                state = "approved: in force now" + (f" as #{made}" if made is not None else "")
            elif request.status == "declined":
                said = f': "{request.answer_reason}"' if request.answer_reason else ""
                state = f"declined by the person{said}"
            else:
                state = f"no longer open. {request.stale_reason}"
            lines.append(f"- request #{request.id}: {_change_words(change)} ({state})")
    return lines


def _change_words(change: StoredChange) -> str:
    old = f"“{change.old_sentence}”" if change.old_sentence else ""
    if isinstance(change, ReplaceChange):
        return f"replace #{change.expectation_id} {old} with “{change.expectation.sentence}”"
    if isinstance(change, RetireChange):
        return f"retire #{change.expectation_id} {old}"
    if isinstance(change, TierChange):
        return f"move #{change.expectation_id} {old} from {change.old_tier} to {change.tier}"
    if isinstance(change, PutBackChange):
        return f"put back #{change.expectation_id} {old}"
    return f"put back the different limit {change.override_id} ({change.new_sentence}) for {old}"


async def _expectation_answers(db: Database, thread_id: int) -> list[str]:
    repo = SignatureRepository(db)
    proposed = await repo.proposed_in_thread(thread_id)
    overrides = await repo.overrides_proposed_in_thread(thread_id)
    if not proposed and not overrides:
        return []
    lines = ["SIGNATURE EXPECTATIONS YOU PROPOSED IN THIS CONVERSATION"]
    for row in proposed:
        lines.append(f"- #{row.id} {expectation_line(row)} ({_answer_words(row)})")
    for item in overrides:
        target = await repo.get(item.expectation_id)
        about = f"expectation #{item.expectation_id}" + (
            f" ({target.phase or 'whole shot'})" if target is not None else ""
        )
        if item.status == "rejected":
            reason = f' They said: "{item.reject_reason}".' if item.reject_reason else ""
            state = f"rejected by the person.{reason}"
        elif item.status == "confirmed":
            state = "in force unless the person rejects it: it is stated in the signature above"
        elif item.status == "proposed":
            state = "not in force: this version reads the profile's limit"
        else:
            # `withdrawn`: a newer limit replaced it, its expectation was rejected, or (on an
            # older version) a person took it back. Why is not claimed.
            state = "no longer in force"
        if item.compare is None:
            words = "an unreadable limit"
        elif target is not None and target.expression is not None:
            words = limit_words(target.expression, item.compare)
        else:
            words = compare_words(item.compare)
        lines.append(f"- a different limit for {about}: {words} ({state})")
    return lines
