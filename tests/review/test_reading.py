"""A shot's verdict and reading state, worked out at read time.

Two halves. The first is the pure table: every state of the reading block and the badge text and
verdict that go with it, how a reading's free-text answers are merged into the checks, and what
makes an answer stop counting. The second is the Review sort
over a seeded archive with a shot in every state, in the order the maintainer chose (work to do
above work done), total and stable across pages, and moved at once by a reading finishing or a
claim being answered.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.reviews import (
    ClaimWrite,
    ReadingRecord,
    ReviewBrief,
    ReviewClaimRow,
    ReviewOutcome,
    ReviewStart,
    ShotReviewsRepository,
)
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import REVIEW_SORT, ShotInsert, ShotsRepository
from gaggiclanker.domain.signature import Check, ShotChecks, SignatureState
from gaggiclanker.review.reading import merge_reading, review_key, serve_reading
from tests.review.conftest import Fixture, confirm_free_text

# ── the pure table ──────────────────────────────────────────────────


def _check(
    expectation_id: int,
    tier: str = "critical",
    *,
    phase: str = "decline",
    fault: str = "unstable",
    position: int = 0,
) -> Check:
    """A free-text expectation as the checks list it before any reading: unchecked."""
    return Check(
        kind="free_text",
        status="unchecked",
        tier=tier,
        phase=phase,
        phase_number=2,
        fault=fault,
        sentence=f"expectation {expectation_id}",
        detail="(checked by the reading, not by a number)",
        value=None,
        unit="",
        held=None,
        absent=None,
        at_s=20.0,
        expectation_id=expectation_id,
        rank=7,
        seq=position,
        shot_wide=False,
    )


def _warning(fault: str = "over target", *, phase: str = "Shot") -> Check:
    return Check(
        kind="warning",
        status="warning",
        tier=None,
        phase=phase,
        phase_number=None,
        fault=fault,
        sentence=fault,
        detail=fault,
        value=None,
        unit="",
        held=None,
        absent=None,
        at_s=0.0,
        expectation_id=None,
        rank=2,
        seq=10,
        shot_wide=True,
    )


def _checks(*checks: Check, confirmed: int = 1) -> ShotChecks:
    return ShotChecks(
        checks=tuple(sorted(checks, key=Check.order)),
        state=SignatureState(profile_version_id=1, confirmed=confirmed),
    )


def _brief(status: str = "ok", *, review_id: int = 7, error: str | None = None) -> ReviewBrief:
    return ReviewBrief(
        id=review_id,
        shot_id=1,
        status=status,  # type: ignore[arg-type]
        error=error,
        summary="One sentence.",
        model="m",
        created_at="2026-10-06T08:00:00.000Z",
        finished_at="2026-10-06T08:01:00.000Z",
    )


def _answer(
    expectation_id: int,
    *,
    held: bool = False,
    status: str = "confirmed",
    start_s: float | None = 12.0,
    fault: str | None = "unstable",
    position: int = 0,
) -> ReviewClaimRow:
    return ReviewClaimRow(
        id=100 + position,
        review_id=7,
        position=position,
        kind="free_text",
        text="The reading's answer.",
        expectation_id=expectation_id,
        held=held,
        fault=None if held else fault,
        start_s=start_s,
        end_s=None if start_s is None else start_s + 5,
        status=status,  # type: ignore[arg-type]
    )


def _claim(status: str = "confirmed", position: int = 5) -> ReviewClaimRow:
    return ReviewClaimRow(
        id=100 + position,
        review_id=7,
        position=position,
        kind="claim",
        text="x",
        status=status,  # type: ignore[arg-type]
    )


def _record(*claims: ReviewClaimRow, latest: ReviewBrief | None = None) -> ReadingRecord:
    brief = _brief()
    return ReadingRecord(latest=latest or brief, finished=brief, claims=tuple(claims))


class TestStates:
    """Every row of the reading-states table."""

    def test_a_discarded_or_quarantined_shot_is_not_readable_and_shows_its_checks(self) -> None:
        served = serve_reading(_checks(_warning()), None, readable=False)
        assert served.block.state == "not_readable"
        assert served.badge == "Shot: over target"
        assert served.block.verdict is None

    def test_a_shot_nobody_can_read_with_nothing_wrong_has_no_badge(self) -> None:
        served = serve_reading(_checks(confirmed=0), None, readable=False)
        assert (served.block.state, served.badge) == ("not_readable", None)

    def test_a_shot_not_read_yet_shows_its_checks_or_the_button_word(self) -> None:
        with_entry = serve_reading(_checks(_warning()), None, readable=True)
        assert (with_entry.block.state, with_entry.badge) == ("unread", "Shot: over target")
        without = serve_reading(_checks(confirmed=0), None, readable=True)
        assert (without.block.state, without.badge) == ("unread", "Review")
        assert without.block.review_id is None

    def test_a_running_reading_says_so_whatever_the_checks_say(self) -> None:
        record = ReadingRecord(latest=_brief("running", review_id=9))
        served = serve_reading(_checks(_warning()), record, readable=True)
        assert (served.block.state, served.badge) == ("running", "Reading…")
        assert served.block.review_id == 9 and served.block.verdict is None
        assert [e.fault for e in served.entries] == ["over target"], "the checks still list"

    @pytest.mark.parametrize("status", ["failed", "interrupted"])
    def test_a_failed_reading_says_why(self, status: str) -> None:
        record = ReadingRecord(latest=_brief(status, error="auth: bad key"))
        served = serve_reading(_checks(confirmed=0), record, readable=True)
        assert (served.block.state, served.badge) == ("failed", "Failed to run")
        assert served.block.reason == "auth: bad key"

    def test_a_reading_with_failures_names_them_and_counts_the_rest(self) -> None:
        checks = _checks(_check(1), _warning("fast flow", phase="ramp"))
        served = serve_reading(checks, _record(_answer(1)), readable=True)
        assert served.block.state == "read" and served.block.verdict == "entries"
        assert served.badge == "decline: unstable +1"
        assert served.block.summary == "One sentence."
        assert served.block.finished_at == "2026-10-06T08:01:00.000Z"

    def test_a_read_shot_with_a_confirmed_signature_and_no_failure_is_as_intended(self) -> None:
        served = serve_reading(
            _checks(_check(1), confirmed=3), _record(_answer(1, held=True)), readable=True
        )
        assert (served.block.verdict, served.badge) == ("as_intended", "As intended")

    def test_a_read_shot_with_no_signature_says_so(self) -> None:
        served = serve_reading(_checks(confirmed=0), _record(_claim()), readable=True)
        assert (served.block.verdict, served.badge) == ("no_signature", "No signature")


class TestMerge:
    def test_a_failed_critical_answer_is_red_and_an_important_one_amber(self) -> None:
        checks = _checks(_check(1, "critical"), _check(2, "important", phase="ramp", position=1))
        merged = merge_reading(checks, _record(_answer(1), _answer(2, position=1)))
        entries = [(c.expectation_id, c.color, c.status) for c in merged.badge_entries]
        assert entries == [(1, "red", "failed"), (2, "amber", "failed")]

    def test_a_held_answer_is_in_the_held_group_and_a_context_one_never_in_the_badge(self) -> None:
        checks = _checks(_check(1, "critical"), _check(2, "context", position=1))
        merged = merge_reading(checks, _record(_answer(1, held=True), _answer(2, position=1)))
        assert merged.badge_entries == []
        by_id = {c.expectation_id: c for c in merged.checks}
        assert (by_id[1].status, by_id[1].rank, by_id[1].held) == ("held", 5, True)
        assert (by_id[2].status, by_id[2].rank) == ("failed", 6), "context informs, never raises"

    def test_failures_are_ordered_by_where_their_window_starts(self) -> None:
        checks = _checks(_check(1, position=0), _check(2, position=1))
        late = _answer(1, start_s=20.0, position=0)
        early = _answer(2, start_s=3.0, position=1)
        merged = merge_reading(checks, _record(late, early))
        assert [c.expectation_id for c in merged.badge_entries] == [2, 1]

    def test_a_result_with_no_window_keeps_the_expectations_own_place(self) -> None:
        merged = merge_reading(_checks(_check(1)), _record(_answer(1, start_s=None)))
        assert merged.badge_entries[0].at_s == 20.0

    def test_an_answer_nobody_touched_counts_and_a_rejected_one_does_not(self) -> None:
        checks = _checks(_check(1))
        kept = merge_reading(checks, _record(_answer(1)))
        assert [(c.status, c.rank) for c in kept.badge_entries] == [("failed", 0)]
        rejected = merge_reading(checks, _record(_answer(1, status="rejected")))
        assert rejected.badge_entries == []
        (only,) = rejected.checks
        assert only.status == "unchecked"

    def test_a_rejected_failure_leaves_the_verdict(self) -> None:
        """Rejecting 'decline: unstable' can turn a red badge into a green one."""
        checks = _checks(_check(1), confirmed=4)
        before = serve_reading(checks, _record(_answer(1)), readable=True)
        assert (before.block.verdict, before.badge) == ("entries", "decline: unstable")

        after = serve_reading(checks, _record(_answer(1, status="rejected")), readable=True)
        assert (after.block.verdict, after.badge) == ("as_intended", "As intended")
        # The expectation is back to "checked by the reading": nothing was decided.
        assert [c.status for c in after.checks.checks] == ["unchecked"]

    def test_an_answer_stops_counting_when_its_expectation_is_no_longer_confirmed(self) -> None:
        """Re-proposing or rejecting the expectation takes its check out of the list."""
        record = _record(_answer(1))
        assert merge_reading(_checks(_check(1)), record).badge_entries
        # The expectation was re-proposed under a new id: there is no check 1 to answer.
        retired = merge_reading(_checks(_check(2)), record)
        assert retired.badge_entries == []
        assert [c.expectation_id for c in retired.checks] == [2]
        assert retired.checks[0].status == "unchecked"

    @pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
    def test_a_newer_reading_that_did_not_finish_hides_nothing(self, status: str) -> None:
        """The reading in force is the newest finished one; an attempt since changes no result."""
        checks = _checks(_check(1))
        record = _record(_answer(1), latest=_brief(status, review_id=8))
        merged = merge_reading(checks, record)
        assert [c.expectation_id for c in merged.badge_entries] == [1], status

    def test_only_the_newest_finished_reading_is_in_force(self) -> None:
        """A newer finished reading replaces the one before it; the claims are its own."""
        checks = _checks(_check(1))
        newer = ReviewBrief(
            id=9, shot_id=1, status="ok", summary="s", model="m", created_at="x", finished_at="y"
        )
        held = _answer(1, held=True, status="confirmed")
        record = ReadingRecord(latest=newer, finished=newer, claims=(held,))
        assert merge_reading(checks, record).badge_entries == []

    def test_a_shot_never_read_merges_to_its_own_checks(self) -> None:
        checks = _checks(_check(1))
        assert merge_reading(checks, None) is checks
        assert merge_reading(checks, ReadingRecord()) is checks

    def test_the_other_kinds_of_claim_never_enter_the_checks(self) -> None:
        checks = _checks(_check(1))
        record = _record(_claim(), _claim("rejected", 6))
        assert merge_reading(checks, record) is checks


class TestRunningAgain:
    """A newer reading changes the badge's words and the sort bucket, nothing the person reads."""

    def test_while_a_reading_runs_the_results_and_summary_are_the_one_in_force(
        self,
    ) -> None:
        checks = _checks(_check(1), confirmed=3)
        attempt = _brief("running", review_id=9).model_copy(
            update={"summary": "the attempt's own", "finished_at": None}
        )
        record = _record(_answer(1), _claim(), latest=attempt)
        served = serve_reading(checks, record, readable=True)
        assert (served.block.state, served.badge, served.block.review_id) == (
            "running",
            "Reading…",
            9,
        )
        assert served.block.verdict is None
        assert [c.status for c in served.entries] == ["failed"]
        # Claims are answered through the reading in force, not the attempt.
        assert (served.block.review_id, served.block.in_force_id) == (9, 7)
        assert served.block.summary == "One sentence."
        assert served.block.finished_at == "2026-10-06T08:01:00.000Z"

    @pytest.mark.parametrize("status", ["failed", "interrupted"])
    def test_after_a_failed_re_read_the_reason_is_the_new_attempts(self, status: str) -> None:
        record = _record(_answer(1), latest=_brief(status, review_id=9, error="auth: bad key"))
        served = serve_reading(_checks(_check(1)), record, readable=True)
        assert (served.block.state, served.badge) == ("failed", "Failed to run")
        assert served.block.reason == "auth: bad key"
        assert (served.block.review_id, served.block.in_force_id) == (9, 7)
        assert [c.expectation_id for c in served.entries] == [1]

    def test_the_sort_puts_a_shot_being_read_again_by_its_entries_first_then_its_bucket(
        self,
    ) -> None:
        running = _record(_answer(1), latest=_brief("running", review_id=9))
        with_entry = serve_reading(_checks(_check(1)), running, readable=True)
        assert review_key(with_entry)[0] == 0
        clean = _record(_answer(1, held=True), latest=_brief("running", review_id=9))
        without = serve_reading(_checks(_check(1), confirmed=3), clean, readable=True)
        assert review_key(without)[0] == 2, "Reading… sorts as a shot being read"

    def test_with_no_finished_reading_nothing_is_in_force(self) -> None:
        attempt = ReadingRecord(latest=_brief("running", review_id=9))
        assert serve_reading(_checks(), attempt, readable=True).block.in_force_id is None
        failed = ReadingRecord(latest=_brief("failed", review_id=9, error="x"))
        assert serve_reading(_checks(), failed, readable=True).block.in_force_id is None
        assert serve_reading(_checks(), None, readable=True).block.in_force_id is None

    def test_a_finished_reading_is_its_own_in_force_id(self) -> None:
        block = serve_reading(_checks(), _record(_claim()), readable=True).block
        assert (block.review_id, block.in_force_id) == (7, 7)

    def test_a_discarded_shot_keeps_its_signature_checks_only(self) -> None:
        record = _record(_answer(1))
        served = serve_reading(_checks(_check(1)), record, readable=False)
        assert served.block.state == "not_readable"
        assert served.entries == []


def test_the_sort_key_orders_entries_by_the_checks_own_order_and_then_by_state() -> None:
    def key(*checks: Check, record: ReadingRecord | None = None, readable: bool = True) -> Any:
        return review_key(serve_reading(_checks(*checks, confirmed=0), record, readable=readable))

    red = _check(1, "critical")
    amber = _check(2, "important")
    merged = _record(_answer(1), _answer(2, position=1))
    assert key(red, amber, record=merged) < key(amber, record=_record(_answer(2)))
    # Entries first, whatever state the shot is in.
    assert key(_warning(), record=ReadingRecord(latest=_brief("running"))) < key()
    states = [
        key(record=ReadingRecord(latest=_brief("failed"))),
        key(record=ReadingRecord(latest=_brief("running"))),
        key(record=_record(_claim())),  # read, no signature
        key(),  # unread
        key(readable=False),  # not readable
    ]
    assert states == sorted(states)
    assert len({s[0] for s in states}) == len(states)


# ── the sort, over a seeded archive ─────────────────────────────────


async def _shot(
    fixture: Fixture, number: int, *, profile: bool = True, weight: float = 37.0, **kwargs: Any
) -> int:
    """One more shot of the fixture's Set, started `number` minutes after the others."""
    return await ShotsRepository(fixture.db).insert(
        ShotInsert(
            device_id=f"0009{number:02d}",
            raw_slog=b"fixture",
            started_at=f"2026-03-04T08:{number:02d}:00.000Z",
            duration_ms=25_000,
            profile_version_id=fixture.profile_version_id if profile else None,
            final_weight_g=weight,
            final_exit_reason=1,
            scale_connected=True,
            sample_count=0,
            sample_interval_ms=250,
            **kwargs,
        )
    )


async def _finish(
    fixture: Fixture, shot: int, status: str = "ok", claims: list[ClaimWrite] | None = None
) -> int:
    repo = ShotReviewsRepository(fixture.db)
    review = await repo.start(ReviewStart(shot_id=shot))
    if status == "running":
        return review
    await repo.finish(
        review,
        ReviewOutcome(
            status=status,  # type: ignore[arg-type]
            summary="s" if status == "ok" else None,
            error="auth: bad key" if status == "failed" else None,
            claims=claims or [],
        ),
    )
    return review


async def _states(fixture: Fixture) -> dict[str, int]:
    """One shot in every state, each filed under the fixture's version (target 36 g)."""
    sets = SetsRepository(fixture.db)
    shots: dict[str, int] = {}
    # What the sort puts first: a failure the checks name, read or not.
    shots["entries"] = await _shot(fixture, 11, weight=45.0)
    shots["failed"] = await _shot(fixture, 12)
    shots["running"] = await _shot(fixture, 13)
    shots["no_signature"] = await _shot(fixture, 14, profile=False)
    shots["unread"] = await _shot(fixture, 15, profile=False)
    shots["as_intended"] = await _shot(fixture, 16)
    shots["quarantined"] = await _shot(fixture, 17, profile=False, quarantined=True)
    shots["discarded"] = await _shot(fixture, 18, profile=False)
    for shot in shots.values():
        await sets.assign_shot(shot, fixture.version_id)
    await fixture.db.execute(
        "INSERT INTO shot_judgements (shot_id, decision) VALUES (?, 'discard')",
        (shots["discarded"],),
    )
    await confirm_free_text(fixture)  # the profile now has a confirmed signature
    await _finish(fixture, shots["failed"], "failed")
    await _finish(fixture, shots["running"], "running")
    await _finish(fixture, shots["no_signature"], claims=[ClaimWrite(kind="claim", text="x")])
    await _finish(fixture, shots["as_intended"], claims=[ClaimWrite(kind="claim", text="x")])
    return shots


async def _order(fixture: Fixture, *, descending: bool = True, limit: int = 100) -> list[int]:
    page = await ShotsRepository(fixture.db).list_shots(
        limit=limit, sort=REVIEW_SORT, descending=descending, set_id=fixture.set_id
    )
    return [row.id for row in page.items]


async def test_the_sort_puts_work_to_do_above_work_done(fixture: Fixture) -> None:
    shots = await _states(fixture)
    order = await _order(fixture)
    ours = [shot for shot in order if shot in shots.values()]
    named = {number: name for name, number in shots.items()}
    assert [named[shot] for shot in ours] == [
        "entries",
        "failed",
        "running",
        "no_signature",
        "unread",
        "as_intended",
        # Nobody can read them: last, newest first (the discarded one started later).
        "discarded",
        "quarantined",
    ]


async def test_the_key_is_total_and_stable_across_pages_and_ascending_is_its_reverse(
    fixture: Fixture,
) -> None:
    await _states(fixture)
    everything = await _order(fixture)
    assert len(everything) == len(set(everything))

    paged: list[int] = []
    repo = ShotsRepository(fixture.db)
    for offset in range(0, len(everything), 3):
        page = await repo.list_shots(
            limit=3, offset=offset, sort=REVIEW_SORT, set_id=fixture.set_id
        )
        paged += [row.id for row in page.items]
    assert paged == everything
    assert await _order(fixture, descending=False) == everything[::-1]


async def test_a_reading_finishing_or_a_claim_being_rejected_moves_the_sort_at_once(
    fixture: Fixture,
) -> None:
    """Nothing is remembered about a reading that a change could leave stale."""
    shots = await _states(fixture)
    claims = [ClaimWrite(kind="claim", text="x")]

    before = await _order(fixture)  # warms every remembered result
    assert await _order(fixture) == before
    # The unread shot is read: it moves from "unread" to "No signature" (above it, now).
    await _finish(fixture, shots["unread"], claims=claims)
    after_reading = await _order(fixture)
    assert after_reading.index(shots["unread"]) < before.index(shots["unread"])

    # A reading started again: the shot with the verdict is "Reading…", above where it was.
    await _finish(fixture, shots["as_intended"], "running")
    assert (await _order(fixture)).index(shots["as_intended"]) < after_reading.index(
        shots["as_intended"]
    )


async def test_a_failed_free_text_answer_enters_the_sort_and_leaves_it_when_rejected(
    fixture: Fixture,
) -> None:
    clean = await _shot(fixture, 30)
    other = await _shot(fixture, 31)
    for shot in (clean, other):
        await SetsRepository(fixture.db).assign_shot(shot, fixture.version_id)
    expectation = await confirm_free_text(fixture)
    failed = ClaimWrite(
        kind="free_text",
        expectation_id=expectation,
        held=False,
        fault="unstable",
        text="It was not stable.",
        start_s=12.0,
    )
    review = await _finish(fixture, clean, claims=[failed])
    repo = ShotReviewsRepository(fixture.db)

    page = await ShotsRepository(fixture.db).list_shots(
        limit=100, sort=REVIEW_SORT, set_id=fixture.set_id
    )
    first = page.items[0]
    assert first.id == clean
    assert first.badge == "Pressurise: unstable"
    assert [(w.status, w.severity) for w in first.warnings] == [("failed", "red")]

    (claim,) = (await repo.get(review)).claims  # type: ignore[union-attr]
    await repo.answer(review, claim.id, keep=False)
    page = await ShotsRepository(fixture.db).list_shots(
        limit=100, sort=REVIEW_SORT, set_id=fixture.set_id
    )
    assert page.items[0].id != clean or page.items[0].warnings == []
    row = next(item for item in page.items if item.id == clean)
    assert (row.badge, row.reading.verdict) == ("As intended", "as_intended")
