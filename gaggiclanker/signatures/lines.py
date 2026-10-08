"""What a conversation is told about a profile's signature.

Two pieces, kept apart because they move differently.

* **The signature itself**, in the profile block at the very top of the Set conversation's
  opening context (and the design conversation's fork block): the expectations **in force**
  for the profile version, one line each, tier first, with the Set version's override in
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
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.metric_language import compare_words
from gaggiclanker.domain.signature import expectation_line

__all__ = [
    "NO_SIGNATURE",
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
    set_version_id: int | None,
) -> list[str]:
    """A profile version's signature in force as lines, or the sentence saying there is none."""
    repo = SignatureRepository(db)
    rows = (await repo.confirmed_for_versions([profile_version_id])).get(profile_version_id, [])
    if not rows:
        return ["", NO_SIGNATURE.format(id=profile_version_id)]
    override = None
    if set_version_id is not None:
        override = (await repo.confirmed_overrides([set_version_id])).get(set_version_id)
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
    """A profile version's expectations in force, one line each, tier first."""
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


async def signature_answers_block(db: Database, thread_id: int | None) -> list[str]:
    """What this conversation proposed as expectations, and what the person did with each.

    **This conversation's only.** One in force is also in the signature above and is only said
    to be in force; a rejected one carries the person's reason, so the conversation does not
    offer it again.
    """
    if thread_id is None:
        return []
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
        words = "an unreadable limit" if item.compare is None else compare_words(item.compare)
        lines.append(f"- a different limit for {about}: {words} ({state})")
    return lines
