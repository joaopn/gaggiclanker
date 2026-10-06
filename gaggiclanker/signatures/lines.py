"""What a conversation is told about a profile's signature.

Two pieces, kept apart because they move differently.

* **The signature itself**, in the profile block at the very top of the Set conversation's
  opening context (and the design conversation's fork block): the **confirmed** expectations
  of the profile version, one line each, tier first, with the Set version's confirmed
  override stated beside the limit it changes. It changes only when a person confirms
  something, which changes what the profile *means*, so it is allowed to move the cached
  prefix; a new shot, a grade, a proposal, a version or a revert never does. A profile
  version with no confirmed signature says so and asks the conversation to propose one
  (when it turns to how the shots behave, not at every message).

* **What this conversation proposed**, after the volatile part: its own proposed expectations,
  marked *proposed, not confirmed*, and what the person did with each (a rejection carries the
  reason they gave). Keyed by the thread that wrote the rows, never by version, so no other
  conversation is told what was proposed and nothing unconfirmed is ever read as a check.
"""

from __future__ import annotations

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.metric_language import compare_words
from gaggiclanker.domain.signature import expectation_line

__all__ = [
    "NO_SIGNATURE",
    "NO_SIGNATURE_WAITING",
    "signature_answers_block",
    "signature_block",
    "signature_lines",
]

#: Said when a profile version has no confirmed signature. One sentence, pinned by a test:
#: it asks to propose one when the conversation turns to how the shots behave, and says what
#: a proposal is (not checked until the person confirms it).
NO_SIGNATURE = (
    "This profile version has no confirmed signature, so its shots are read without one. When "
    "the conversation turns to how its shots behave, propose one with propose_signature "
    "(profile version {id}): what the profile is built to do, in a few expectations, never "
    "what one shot did. Nothing you propose is checked against a shot until the person "
    "confirms it."
)


#: Said instead of :data:`NO_SIGNATURE` while the conversation has proposed expectations for the
#: profile version that the person has not answered: it does not ask again, it says they wait.
NO_SIGNATURE_WAITING = (
    "This profile version has no confirmed signature yet, so its shots are still read without "
    "one. You proposed {count} for it: waiting for the person (listed below). Do not propose "
    "again what is waiting, and do not describe a shot as passing or failing one until they "
    "confirm it."
)


async def signature_block(
    db: Database,
    profile_version_id: int,
    set_version_id: int | None,
    thread_id: int | None = None,
) -> list[str]:
    """A profile version's confirmed signature as lines, or the sentence saying there is none."""
    repo = SignatureRepository(db)
    rows = (await repo.confirmed_for_versions([profile_version_id])).get(profile_version_id, [])
    if not rows:
        waiting = (
            [
                r
                for r in await repo.proposed_in_thread(thread_id)
                if r.profile_version_id == profile_version_id and r.status == "proposed"
            ]
            if thread_id is not None
            else []
        )
        if waiting:
            count = f"{len(waiting)} expectation{'' if len(waiting) == 1 else 's'}"
            return ["", NO_SIGNATURE_WAITING.format(count=count)]
        return ["", NO_SIGNATURE.format(id=profile_version_id)]
    override = None
    if set_version_id is not None:
        override = (await repo.confirmed_overrides([set_version_id])).get(set_version_id)
    lines = [
        "",
        f"What profile version {profile_version_id} is FOR (its signature: every line was "
        "confirmed by the person, and every shot on it is checked against them; the number is "
        "the expectation's id):",
    ]
    for row in _tier_order(rows):
        compare = (
            override.compare if override is not None and override.expectation_id == row.id else None
        )
        lines.append(f"- #{row.id} {expectation_line(row, compare)}")
    return lines


async def signature_lines(db: Database, profile_version_id: int) -> list[str]:
    """A profile version's confirmed expectations, one line each, tier first."""
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
        return "confirmed by the person: it is in the signature above."
    return "proposed, not confirmed: the person has not answered, so it is checked against no shot"


async def signature_answers_block(db: Database, thread_id: int | None) -> list[str]:
    """What this conversation proposed as expectations, and what the person did with each.

    **This conversation's only.** A waiting one is *proposed, not confirmed*; a rejected one
    carries the person's reason, so the conversation does not offer it again; a confirmed one
    is also in the signature above and is only said to have been confirmed.
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
            state = "confirmed by the person: it is stated in the signature above"
        elif item.status == "withdrawn":
            state = (
                "confirmed, then withdrawn by the person: this version reads the profile's "
                "limit again, and you may propose another"
            )
        else:
            state = "proposed, not confirmed: the person has not answered"
        words = "an unreadable limit" if item.compare is None else compare_words(item.compare)
        lines.append(f"- a different limit for {about}: {words} ({state})")
    return lines
