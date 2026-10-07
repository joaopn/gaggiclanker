"""A shot's Curve check and its review, worked out at read time and kept apart.

Two halves. The first is the pure table: every state of the review block with its badge, verdict
and entries; how the review's faults are built from its claims (never from a rejected one, never
from a deterministic check) and ordered; and, over many shapes of review, that the Curve check is
exactly what it was and the review never carries a deterministic check. The second is both sort
keys over a seeded archive with a shot in every state: total, stable across pages, and moved at
once by a review finishing or a claim being rejected.
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
from gaggiclanker.db.repos.shots import CHECK_SORT, REVIEW_SORT, ShotInsert, ShotsRepository
from gaggiclanker.domain.signature import Check, ShotChecks, SignatureState
from gaggiclanker.review.reading import review_entries, review_key, serve_review
from tests.review.conftest import Fixture, confirm_free_text

# ── the pure table ──────────────────────────────────────────────────


def _check(
    expectation_id: int,
    tier: str = "critical",
    *,
    phase: str = "decline",
    fault: str = "unstable",
    position: int = 0,
    shot_wide: bool = False,
    at_s: float = 20.0,
) -> Check:
    """A free-text expectation as the checks list it: unchecked, the review answers it."""
    return Check(
        kind="free_text",
        status="unchecked",
        tier=tier,
        phase="Shot" if shot_wide else phase,
        phase_number=None if shot_wide else 2,
        fault=fault,
        sentence=f"expectation {expectation_id}",
        detail="(checked by the review, not by a number)",
        value=None,
        unit="",
        held=None,
        absent=None,
        at_s=at_s,
        expectation_id=expectation_id,
        rank=7,
        seq=position,
        shot_wide=shot_wide,
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
    """The review's answer to one free-text expectation."""
    return ReviewClaimRow(
        id=100 + position,
        review_id=7,
        position=position,
        kind="free_text",
        text="The review's answer.",
        expectation_id=expectation_id,
        held=held,
        fault=None if held else fault,
        start_s=start_s,
        end_s=None if start_s is None else start_s + 5,
        status=status,  # type: ignore[arg-type]
    )


def _claim(
    fault: str | None = None,
    *,
    status: str = "confirmed",
    position: int = 5,
    phase: str | None = "ramp",
    start_s: float | None = 4.0,
    supported: bool = True,
) -> ReviewClaimRow:
    return ReviewClaimRow(
        id=100 + position,
        review_id=7,
        position=position,
        kind="claim",
        text="An observation.",
        fault=fault,
        phase=phase,
        start_s=start_s,
        supported=supported,
        status=status,  # type: ignore[arg-type]
    )


def _stance(status: str = "confirmed", position: int = 9) -> ReviewClaimRow:
    return ReviewClaimRow(
        id=100 + position,
        review_id=7,
        position=position,
        kind="prediction",
        text="Faster than predicted.",
        stance="partly",
        status=status,  # type: ignore[arg-type]
    )


def _record(*claims: ReviewClaimRow, latest: ReviewBrief | None = None) -> ReadingRecord:
    brief = _brief()
    return ReadingRecord(latest=latest or brief, finished=brief, claims=tuple(claims))


class TestStates:
    """Every state of the review block, with its badge, verdict and entries."""

    def test_a_discarded_or_quarantined_shot_has_a_curve_check_and_no_review(self) -> None:
        served = serve_review(_checks(_warning()), None, reviewable=False)
        assert served.review.state == "not_reviewable"
        assert (served.review.badge, served.review.verdict, served.review.entries) == (
            None,
            None,
            [],
        )
        assert served.checks_block.badge == "Shot: over target"

    def test_an_old_review_of_a_discarded_shot_names_nothing(self) -> None:
        record = _record(_claim("fast flow"))
        served = serve_review(_checks(), record, reviewable=False)
        assert served.review.state == "not_reviewable"
        assert served.review.entries == [] and served.review.summary is None

    def test_a_shot_not_reviewed_yet_has_no_review_badge_and_its_checks_untouched(self) -> None:
        served = serve_review(_checks(_warning()), None, reviewable=True)
        assert served.review.state == "unreviewed"
        assert served.review.badge is None and served.review.review_id is None
        assert served.checks_block.badge == "Shot: over target"

    def test_a_running_review_says_so_whatever_the_checks_say(self) -> None:
        record = ReadingRecord(latest=_brief("running", review_id=9))
        served = serve_review(_checks(_warning()), record, reviewable=True)
        assert (served.review.state, served.review.badge) == ("running", "Reviewing…")
        assert served.review.review_id == 9 and served.review.verdict is None
        assert served.review.entries == [], "the warning is the Curve check's, not the review's"
        assert [e.fault for e in served.checks_block.entries] == ["over target"]

    @pytest.mark.parametrize("status", ["failed", "interrupted"])
    def test_a_failed_review_says_why(self, status: str) -> None:
        record = ReadingRecord(latest=_brief(status, error="auth: bad key"))
        served = serve_review(_checks(confirmed=0), record, reviewable=True)
        assert (served.review.state, served.review.badge) == ("failed", "Failed to run")
        assert served.review.reason == "auth: bad key"

    def test_a_review_with_faults_names_the_first_and_counts_the_rest(self) -> None:
        checks = _checks(_check(1))
        record = _record(_answer(1), _claim("fast flow", phase="ramp"))
        served = serve_review(checks, record, reviewable=True)
        assert served.review.state == "reviewed" and served.review.verdict == "entries"
        assert served.review.badge == "decline: unstable +1"
        assert served.review.summary == "One sentence."
        assert [e.claim_id for e in served.review.entries] == [100, 105]

    def test_a_reviewed_shot_with_a_confirmed_signature_and_no_fault_is_as_intended(self) -> None:
        record = _record(_answer(1, held=True), _claim())
        served = serve_review(_checks(_check(1), confirmed=3), record, reviewable=True)
        assert (served.review.verdict, served.review.badge) == ("as_intended", "As intended")

    def test_a_reviewed_shot_with_no_signature_and_no_fault_has_no_faults(self) -> None:
        served = serve_review(_checks(confirmed=0), _record(_claim()), reviewable=True)
        assert (served.review.verdict, served.review.badge) == ("no_faults", "No faults")

    def test_a_deterministic_check_never_makes_the_review_badge(self) -> None:
        """A failed expectation or a warning is the Curve check's; the review says what it says."""
        checks = _checks(_warning("fast flow", phase="ramp"), confirmed=0)
        served = serve_review(checks, _record(_claim()), reviewable=True)
        assert served.review.entries == []
        assert (served.review.verdict, served.review.badge) == ("no_faults", "No faults")
        assert served.checks_block.badge == "ramp: fast flow"


class TestEntries:
    """The review's faults, from its claims, never a rejected one."""

    def test_a_failed_critical_answer_is_red_and_an_important_one_amber(self) -> None:
        checks = _checks(_check(1, "critical"), _check(2, "important", phase="ramp", position=1))
        found = review_entries(checks, (_answer(1), _answer(2, position=1)))
        assert [(e.expectation_id, e.severity, e.status) for e in found] == [
            (1, "red", "failed"),
            (2, "amber", "failed"),
        ]

    def test_a_claim_with_a_fault_word_is_amber_and_an_observation_is_nothing(self) -> None:
        found = review_entries(_checks(), (_claim("fast flow"), _claim(None, position=6)))
        assert [(e.fault, e.severity, e.status) for e in found] == [("fast flow", "amber", "claim")]

    def test_a_held_answer_a_context_one_and_a_stance_raise_nothing(self) -> None:
        checks = _checks(_check(1, "critical"), _check(2, "context", position=1))
        claims = (_answer(1, held=True), _answer(2, position=1), _stance())
        assert review_entries(checks, claims) == []

    def test_tier_rank_first_then_a_phase_before_the_whole_shot_then_the_start_of_the_window(
        self,
    ) -> None:
        checks = _checks(
            _check(1, "important", phase="ramp", position=0),
            _check(2, "critical", phase="decline", position=1),
            _check(3, "critical", shot_wide=True, position=2),
        )
        claims = (
            _claim("slow flow", phase="soak", start_s=3.0, position=3),
            _claim("unstable", phase="ramp", start_s=1.0, position=4),
            _claim("skipped", phase=None, start_s=None, position=5),
            _answer(1, start_s=9.0, position=0),
            _answer(2, start_s=20.0, position=1),
            _answer(3, start_s=2.0, position=2),
        )
        found = review_entries(checks, claims)
        assert [(e.rank, e.phase, e.fault) for e in found] == [
            (0, "decline", "unstable"),  # critical, in a phase
            (0, "Shot", "unstable"),  # critical, the whole shot, after the phases
            (1, "ramp", "unstable"),  # important
            (2, "ramp", "unstable"),  # claims, by where their window starts
            (2, "soak", "slow flow"),
            (2, "Shot", "skipped"),  # a whole-shot claim last
        ]

    def test_a_claim_the_numbers_do_not_bear_out_is_no_fault_for_the_badge_or_the_count(
        self,
    ) -> None:
        checks = _checks(_check(1), confirmed=3)
        claims = (
            _claim("slow flow", phase="soak", start_s=3.0, position=3, supported=False),
            _claim("fast flow", phase="ramp", start_s=1.0, position=4),
        )
        found = review_entries(checks, claims)
        assert [e.fault for e in found] == ["fast flow"]
        served = serve_review(checks, _record(*claims), reviewable=True)
        assert served.review.badge == "ramp: fast flow"  # no "+1" for the unsupported claim
        assert [e.claim_id for e in served.review.entries] == [104]
        # Only unsupported ones: nothing to name, and the shot reads as without faults.
        only = serve_review(checks, _record(claims[0]), reviewable=True)
        assert (only.review.verdict, only.review.badge) == ("as_intended", "As intended")
        # And the sort key follows what is shown.
        assert review_key(only)[0] == 5
        assert review_key(served)[0] == 0

    def test_a_free_text_answer_with_no_support_is_no_fault_either(self) -> None:
        checks = _checks(_check(1))
        answer = _answer(1).model_copy(update={"supported": False})
        assert review_entries(checks, (answer,)) == []

    def test_a_rejected_claim_is_not_a_fault_and_restoring_it_brings_it_back(self) -> None:
        checks = _checks(_check(1))
        both = (_answer(1), _claim("fast flow"))
        assert len(review_entries(checks, both)) == 2
        rejected = (_answer(1, status="rejected"), _claim("fast flow", status="rejected"))
        assert review_entries(checks, rejected) == []
        one = (_answer(1, status="rejected"), _claim("fast flow"))
        assert [e.fault for e in review_entries(checks, one)] == ["fast flow"]

    def test_rejecting_a_failure_can_turn_a_red_badge_into_a_green_one(self) -> None:
        checks = _checks(_check(1), confirmed=4)
        before = serve_review(checks, _record(_answer(1)), reviewable=True)
        assert (before.review.verdict, before.review.badge) == ("entries", "decline: unstable")
        after = serve_review(checks, _record(_answer(1, status="rejected")), reviewable=True)
        assert (after.review.verdict, after.review.badge) == ("as_intended", "As intended")

    def test_an_answer_stops_counting_when_its_expectation_is_no_longer_confirmed(self) -> None:
        """Re-proposing or rejecting the expectation retires the answer: its tier is gone."""
        record = _record(_answer(1))
        assert serve_review(_checks(_check(1)), record, reviewable=True).review.entries
        retired = serve_review(_checks(_check(2)), record, reviewable=True)
        assert retired.review.entries == []

    def test_an_answer_with_no_window_keeps_the_expectations_own_place(self) -> None:
        found = review_entries(_checks(_check(1)), (_answer(1, start_s=None),))
        assert found[0].at_s == 20.0


class TestTheCurveCheckNeverMoves:
    """Whatever a review does, the Curve check is exactly what it was without one."""

    CHECKS = _checks(
        _check(1), _check(2, "important", position=1), _warning("fast flow", phase="ramp")
    )

    def _shapes(self) -> list[tuple[str, ReadingRecord | None, bool]]:
        answers = (_answer(1), _answer(2, position=1), _claim("fast flow"))
        rejected = (_answer(1, status="rejected"), _claim("fast flow", status="rejected"))
        running = _brief("running", review_id=9)
        shapes: list[tuple[str, ReadingRecord | None, bool]] = [
            ("never reviewed", None, True),
            ("not reviewable", _record(*answers), False),
            ("running", ReadingRecord(latest=running), True),
            ("failed", ReadingRecord(latest=_brief("failed", error="x")), True),
            ("reviewed", _record(*answers), True),
            ("reviewed, claims rejected", _record(*rejected), True),
            ("reviewed, nothing held", _record(_answer(1, held=True)), True),
            ("running again", _record(*answers, latest=running), True),
            ("failed again", _record(*answers, latest=_brief("failed", review_id=9)), True),
            (
                "interrupted again",
                _record(*answers, latest=_brief("interrupted", review_id=9)),
                True,
            ),
        ]
        return shapes

    def test_the_curve_check_is_the_same_for_every_shape_of_review(self) -> None:
        baseline = serve_review(self.CHECKS, None, reviewable=True).checks_block
        assert baseline.badge == "ramp: fast flow"
        for name, record, reviewable in self._shapes():
            served = serve_review(self.CHECKS, record, reviewable=reviewable)
            assert served.checks_block == baseline, name
            assert served.checks == self.CHECKS, name

    def test_the_review_never_carries_a_deterministic_check(self) -> None:
        deterministic = {
            e.fault for e in serve_review(self.CHECKS, None, reviewable=True).checks_block.entries
        }
        for name, record, reviewable in self._shapes():
            review = serve_review(self.CHECKS, record, reviewable=reviewable).review
            claim_ids = {c.id for c in (record.claims if record else ())}
            # Every entry of the review is a claim of the review in force and nothing else.
            assert all(e.claim_id in claim_ids for e in review.entries), name
            assert deterministic <= {"fast flow"}
        warning_only = serve_review(
            _checks(_warning(), confirmed=0), _record(_claim()), reviewable=True
        )
        assert warning_only.review.entries == []


class TestRunningAgain:
    """A newer review changes the badge's words and the sort bucket, nothing the person reads."""

    def test_while_a_review_runs_the_claims_and_summary_are_the_one_in_force(self) -> None:
        checks = _checks(_check(1), confirmed=3)
        attempt = _brief("running", review_id=9).model_copy(
            update={"summary": "the attempt's own", "finished_at": None}
        )
        record = _record(_answer(1), _claim(), latest=attempt)
        served = serve_review(checks, record, reviewable=True)
        assert (served.review.state, served.review.badge, served.review.review_id) == (
            "running",
            "Reviewing…",
            9,
        )
        assert served.review.verdict is None
        assert [e.status for e in served.review.entries] == ["failed"]
        # Claims are answered through the review in force, not the attempt.
        assert (served.review.review_id, served.review.in_force_id) == (9, 7)
        assert served.review.summary == "One sentence."

    @pytest.mark.parametrize("status", ["failed", "interrupted"])
    def test_after_a_failed_attempt_the_reason_is_the_new_attempts(self, status: str) -> None:
        record = _record(_answer(1), latest=_brief(status, review_id=9, error="auth: bad key"))
        served = serve_review(_checks(_check(1)), record, reviewable=True)
        assert (served.review.state, served.review.badge) == ("failed", "Failed to run")
        assert served.review.reason == "auth: bad key"
        assert (served.review.review_id, served.review.in_force_id) == (9, 7)
        assert [e.expectation_id for e in served.review.entries] == [1]

    def test_with_no_finished_review_nothing_is_in_force(self) -> None:
        attempt = ReadingRecord(latest=_brief("running", review_id=9))
        assert serve_review(_checks(), attempt, reviewable=True).review.in_force_id is None
        failed = ReadingRecord(latest=_brief("failed", review_id=9, error="x"))
        assert serve_review(_checks(), failed, reviewable=True).review.in_force_id is None
        assert serve_review(_checks(), None, reviewable=True).review.in_force_id is None

    def test_a_finished_review_is_its_own_in_force_id(self) -> None:
        block = serve_review(_checks(), _record(_claim()), reviewable=True).review
        assert (block.review_id, block.in_force_id) == (7, 7)

    def test_only_the_newest_finished_review_is_in_force(self) -> None:
        newer = ReviewBrief(
            id=9, shot_id=1, status="ok", summary="s", model="m", created_at="x", finished_at="y"
        )
        held = _answer(1, held=True)
        record = ReadingRecord(latest=newer, finished=newer, claims=(held,))
        served = serve_review(_checks(_check(1)), record, reviewable=True)
        assert served.review.entries == [] and served.review.in_force_id == 9


def _key(*checks: Check, record: ReadingRecord | None = None, reviewable: bool = True) -> Any:
    return review_key(serve_review(_checks(*checks, confirmed=0), record, reviewable=reviewable))


def test_the_review_key_orders_faults_first_and_then_by_state_as_shown() -> None:
    red = _check(1, "critical")
    amber = _check(2, "important")
    both = _record(_answer(1), _answer(2, position=1))
    assert _key(red, amber, record=both) < _key(amber, record=_record(_answer(2)))
    # A shot being reviewed again sorts as the "Reviewing…" it shows, not by the faults it held.
    running_again = _record(_answer(1), latest=_brief("running", review_id=9))
    assert _key(red, record=running_again)[0] == 2
    # And a Curve check entry says nothing about the review's place.
    assert _key(_warning(), record=None)[0] == 4
    states = [
        _key(record=_record(_claim("fast flow"))),  # a fault
        _key(record=ReadingRecord(latest=_brief("failed"))),
        _key(record=ReadingRecord(latest=_brief("running"))),
        _key(record=_record(_claim())),  # reviewed, no faults
        _key(),  # not reviewed
        _key(reviewable=False),  # nobody can review it
    ]
    assert [s[0] for s in states] == [0, 1, 2, 3, 4, 6]
    assert states == sorted(states)
    asintended = review_key(serve_review(_checks(confirmed=2), _record(_claim()), reviewable=True))
    assert asintended[0] == 5 and states[4][0] < asintended[0] < states[5][0]


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
    # A fault the review names, on a shot whose Curve check names one too (45 g over a 36 g target).
    shots["entries"] = await _shot(fixture, 11, weight=45.0)
    shots["failed"] = await _shot(fixture, 12)
    shots["running"] = await _shot(fixture, 13)
    shots["no_faults"] = await _shot(fixture, 14, profile=False)
    shots["unreviewed"] = await _shot(fixture, 15, profile=False)
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
    fault = ClaimWrite(kind="claim", text="x", fault="unstable", phase=None)
    await _finish(fixture, shots["entries"], claims=[fault])
    await _finish(fixture, shots["failed"], "failed")
    await _finish(fixture, shots["running"], "running")
    await _finish(fixture, shots["no_faults"], claims=[ClaimWrite(kind="claim", text="x")])
    await _finish(fixture, shots["as_intended"], claims=[ClaimWrite(kind="claim", text="x")])
    return shots


async def _order(
    fixture: Fixture, *, sort: str = REVIEW_SORT, descending: bool = True, limit: int = 100
) -> list[int]:
    page = await ShotsRepository(fixture.db).list_shots(
        limit=limit, sort=sort, descending=descending, set_id=fixture.set_id
    )
    return [row.id for row in page.items]


async def test_the_review_sort_puts_work_to_do_above_work_done(fixture: Fixture) -> None:
    shots = await _states(fixture)
    order = await _order(fixture)
    ours = [shot for shot in order if shot in shots.values()]
    named = {number: name for name, number in shots.items()}
    assert [named[shot] for shot in ours] == [
        "entries",
        "failed",
        "running",
        "no_faults",
        "unreviewed",
        "as_intended",
        # Nobody can review them: last, newest first (the discarded one started later).
        "discarded",
        "quarantined",
    ]


@pytest.mark.parametrize("sort", [CHECK_SORT, REVIEW_SORT])
async def test_each_key_is_total_and_stable_across_pages_and_ascending_is_its_reverse(
    fixture: Fixture, sort: str
) -> None:
    await _states(fixture)
    everything = await _order(fixture, sort=sort)
    assert len(everything) == len(set(everything)) >= 8

    for size in (1, 3):
        paged: list[int] = []
        repo = ShotsRepository(fixture.db)
        for offset in range(0, len(everything), size):
            page = await repo.list_shots(
                limit=size, offset=offset, sort=sort, set_id=fixture.set_id
            )
            paged += [row.id for row in page.items]
        assert paged == everything, (sort, size)
    assert await _order(fixture, sort=sort, descending=False) == everything[::-1]


async def test_a_review_finishing_or_a_claim_being_rejected_moves_the_review_sort_at_once(
    fixture: Fixture,
) -> None:
    """Nothing is remembered about a review that a change could leave stale."""
    shots = await _states(fixture)

    before = await _order(fixture)  # warms every remembered result
    assert await _order(fixture) == before
    # The unreviewed shot is reviewed with no fault: it moves up from "not reviewed" to "No faults".
    await _finish(fixture, shots["unreviewed"], claims=[ClaimWrite(kind="claim", text="x")])
    after_review = await _order(fixture)
    assert after_review.index(shots["unreviewed"]) < before.index(shots["unreviewed"])

    # A review started again: the shot that was "As intended" is "Reviewing…", above where it was.
    await _finish(fixture, shots["as_intended"], "running")
    after_again = await _order(fixture)
    assert after_again.index(shots["as_intended"]) < after_review.index(shots["as_intended"])

    # Rejecting the only fault of the first shot takes it out of the entries: it is "No faults".
    repo = ShotReviewsRepository(fixture.db)
    review = await repo.finished_for_shot(shots["entries"])
    assert review is not None
    await repo.answer(review.id, review.claims[0].id, keep=False)
    after_reject = await _order(fixture)
    assert after_reject.index(shots["entries"]) > after_again.index(shots["entries"])
    await repo.answer(review.id, review.claims[0].id, keep=True)
    assert (await _order(fixture)).index(shots["entries"]) == after_again.index(shots["entries"])


async def test_no_review_moves_the_curve_check_or_its_sort(fixture: Fixture) -> None:
    """The Curve check column reads the same before and after every review, finished or not."""
    sets = SetsRepository(fixture.db)
    shots = [await _shot(fixture, number, weight=45.0 - number) for number in (21, 22, 23, 24)]
    for shot in shots:
        await sets.assign_shot(shot, fixture.version_id)
    expectation = await confirm_free_text(fixture)
    repo = ShotsRepository(fixture.db)

    async def curve_check() -> tuple[list[int], dict[int, Any]]:
        page = await repo.list_shots(limit=100, sort=CHECK_SORT, set_id=fixture.set_id)
        return (
            [row.id for row in page.items],
            {row.id: row.checks.model_dump() for row in page.items},
        )

    before = await curve_check()
    assert any(entries["entries"] for entries in before[1].values())

    failed = ClaimWrite(
        kind="free_text", expectation_id=expectation, held=False, fault="unstable", text="no"
    )
    await _finish(
        fixture, shots[0], claims=[failed, ClaimWrite(kind="claim", text="a", fault="slow flow")]
    )
    await _finish(fixture, shots[1], "failed")
    await _finish(fixture, shots[2], "running")
    assert await curve_check() == before, "a review finishing, failing or running"

    reviews = ShotReviewsRepository(fixture.db)
    review = await reviews.finished_for_shot(shots[0])
    assert review is not None
    for claim in review.claims:
        await reviews.answer(review.id, claim.id, keep=False)
    assert await curve_check() == before, "every claim rejected"
    for claim in review.claims:
        await reviews.answer(review.id, claim.id, keep=True)
    assert await curve_check() == before, "every claim restored"


async def test_a_failed_free_text_answer_is_a_review_fault_until_rejected(
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
    shots = ShotsRepository(fixture.db)

    page = await shots.list_shots(limit=100, sort=REVIEW_SORT, set_id=fixture.set_id)
    first = page.items[0]
    assert first.id == clean
    assert first.review.badge == "Pressurise: unstable"
    assert [(e.status, e.severity) for e in first.review.entries] == [("failed", "red")]
    # The Curve check does not list the answer: it is the review's.
    assert [e.fault for e in first.checks.entries if e.status == "failed"] == []

    (claim,) = (await repo.get(review)).claims  # type: ignore[union-attr]
    await repo.answer(review, claim.id, keep=False)
    page = await shots.list_shots(limit=100, sort=REVIEW_SORT, set_id=fixture.set_id)
    row = next(item for item in page.items if item.id == clean)
    assert (row.review.badge, row.review.verdict) == ("As intended", "as_intended")
    assert row.review.entries == []
