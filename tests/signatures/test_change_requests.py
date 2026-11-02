"""Signature change requests: an agent asks, a person answers, nothing changes before.

The case is a floor written under an old setpoint. The constructed lever profile states 93 °C;
an expectation "the soak's lowest temperature at least 93" was written under a hotter profile,
and shots whose boiler sags to 91.9-92.4 °C fail it by about a degree, while "at least 91" (a
real sag) is the guard that should stay. The shots are the real fixture's samples with the soak's
`ct` rewritten; nothing is a synthetic trace.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.metric_language import Compare
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
from gaggiclanker.shotinfo.fields import shot_fields
from gaggiclanker.signatures.change_requests import ChangeIn, SignatureRequestService
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import (
    add_shot,
    assert_a_sentence,
    make_recipe_versions,
    make_thread,
    sagging_shot,
)
from tests.signatures.test_carry import _version

LOWS = (91.9, 92.0, 92.4)


def floor(value: float, **extra: Any) -> dict[str, Any]:
    return {
        "channel": "temperature",
        "op": "min",
        "window": {"phase": "soak"},
        "compare": {"op": ">=", "value": value},
        **extra,
    }


def want(kind: str, **fields: Any) -> ChangeIn:
    fields.setdefault("reason", "The 93 was a floor under the old setpoint of 95 degrees.")
    return ChangeIn(kind=kind, **fields)  # type: ignore[arg-type]


def replacing(old_id: int, expression: dict[str, Any], tier: str = "important") -> ChangeIn:
    return want(
        "replace",
        expectation_id=old_id,
        expectation=ExpectationInput(
            tier=tier,  # type: ignore[arg-type]
            kind="measure",
            phase="soak",
            expression=expression,
        ),
    )


@dataclasses.dataclass
class Regio:
    db: Database
    version: int
    recipe_version: int
    recipe_id: int
    thread: int
    high: ExpectationRow  # "at least 93": the one the shots fail
    low: ExpectationRow  # "at least 91": the guard against a real sag
    shots: dict[float, int]

    @property
    def service(self) -> SignatureRequestService:
        return SignatureRequestService(self.db)

    @property
    def repo(self) -> SignatureRepository:
        return SignatureRepository(self.db)

    async def ask(self, *changes: ChangeIn, thread: int | None = None) -> Any:
        return await self.service.request(
            profile_version_id=self.version,
            changes=list(changes),
            reason="Every shot sags under the floor by about a degree.",
            thread_id=self.thread if thread is None else thread,
            recipe_version_id=self.recipe_version,
        )

    async def checks(self, shot: int) -> list[Any]:
        document = await shot_fields(self.db, shot)
        assert document is not None
        return list(document.checks.items)

    async def status(self, shot: int, expectation_id: int) -> str | None:
        found = [c for c in await self.checks(shot) if c.expectation_id == expectation_id]
        return found[0].status if found else None

    async def served(self) -> str:
        """Every shot's served checks, as bytes."""
        out = []
        for shot in self.shots.values():
            document = await shot_fields(self.db, shot)
            assert document is not None
            out.append(document.checks.model_dump_json())
        return "|".join(out)


@pytest.fixture
async def regio(db: Database) -> Regio:
    version = await _version(db, LEVER_PROFILE)
    recipe_version, _ = await make_recipe_versions(db, version)
    recipes = SignatureRepository(db)
    recipe_id = await recipes.recipe_of_version(recipe_version)
    assert recipe_id is not None
    rows = await SignatureService(db).propose(
        version,
        [
            ExpectationInput(tier="important", kind="measure", phase="soak", expression=floor(93)),
            ExpectationInput(tier="critical", kind="measure", phase="soak", expression=floor(91)),
        ],
        reason="What the lever is for.",
    )
    shots: dict[float, int] = {}
    for number, low in enumerate((*LOWS, 90.5), start=1):
        shots[low] = await add_shot(
            db,
            recipe_version_id=recipe_version,
            profile_version_id=version,
            slog=sagging_shot(low),
            device_id=f"0009{number:02d}",
        )
    return Regio(
        db=db,
        version=version,
        recipe_version=recipe_version,
        recipe_id=recipe_id,
        thread=await make_thread(db, recipe_id),
        high=rows[0],
        low=rows[1],
        shots=shots,
    )


async def tables(db: Database, *names: str) -> str:
    """Whole tables, as bytes: what must not move when only the signature does."""
    out = []
    for name in names:
        rows = await db.fetch_all(f"SELECT * FROM {name} ORDER BY 1")  # noqa: S608
        out.append(repr([tuple(r) for r in rows]))
    return "\n".join(out)


# ── the case: nothing before Approve, the amber gone after, the guard kept ──────────


async def test_the_preconditions_the_other_tests_rest_on(regio: Regio) -> None:
    """Without the request the three sagging shots fail the floor, and the guard holds on them."""
    for low in LOWS:
        assert await regio.status(regio.shots[low], regio.high.id) == "failed"
        assert await regio.status(regio.shots[low], regio.low.id) == "held"
    assert await regio.status(regio.shots[90.5], regio.high.id) == "failed"
    assert await regio.status(regio.shots[90.5], regio.low.id) == "failed"


async def test_a_waiting_request_changes_no_check(regio: Regio) -> None:
    before = await regio.served()
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    assert request.status == "waiting"
    assert await regio.served() == before
    # Reading it, which works out its preview, changes nothing either.
    await regio.service.view(request)
    assert await regio.served() == before


async def test_approving_the_replacement_clears_the_amber_and_keeps_the_floor_that_still_applies(
    regio: Regio,
) -> None:
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    guarded = ("recipe_versions", "shot_judgements", "shots")
    frozen = await tables(regio.db, *guarded)

    answer = await regio.service.approve(request.id)

    assert answer.refused is None and answer.request is not None
    assert answer.request.status == "approved" and answer.request.answered_at is not None
    (written,) = answer.request.written
    for low in LOWS:
        shot = regio.shots[low]
        assert await regio.status(shot, regio.high.id) is None  # out of force
        assert await regio.status(shot, written) == "held"  # the new floor, 91.5
        assert await regio.status(shot, regio.low.id) == "held"  # #77 still applies
    # A shot edited down to 90.5 still fails #77, and now fails the new floor too.
    assert await regio.status(regio.shots[90.5], regio.low.id) == "failed"
    assert await regio.status(regio.shots[90.5], written) == "failed"
    # Outcomes, grades and the shots themselves are stored rows and did not move.
    assert await tables(regio.db, *guarded) == frozen
    # The old row is a rejection with the agent's reason, and the new one is in force,
    # provenance the thread.
    old = await regio.repo.get(regio.high.id)
    new = await regio.repo.get(written)
    assert old is not None and new is not None
    assert old.status == "rejected"
    assert old.reject_reason.startswith("Replaced at the agent's request: ")
    assert (new.status, new.proposed_by_thread_id) == ("confirmed", regio.thread)
    assert new.reason.startswith("The 93 was a floor")


async def test_restoring_the_old_row_and_rejecting_the_new_one_brings_every_check_back(
    regio: Regio,
) -> None:
    before = await regio.served()
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    answer = await regio.service.approve(request.id)
    assert answer.request is not None
    (written,) = answer.request.written
    assert await regio.served() != before

    assert (await regio.repo.restore(regio.high.id)).row is not None
    assert (await regio.repo.reject(written, reason="no")).row is not None

    assert await regio.served() == before


async def test_the_preview_says_what_the_approval_then_serves(regio: Regio) -> None:
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    view = await regio.service.view(request)
    (change,) = view.changes
    assert change.preview is not None
    assert change.preview.shots == 4
    assert (change.preview.failing_now, change.preview.failing_after) == (4, 1)
    assert view.loosens_open_prediction is None

    answer = await regio.service.approve(request.id)
    assert answer.request is not None
    (written,) = answer.request.written
    served = [await regio.status(shot, written) for shot in regio.shots.values()]
    # The stop condition: the preview's failing_after is what the served checks say.
    assert served.count("failed") == change.preview.failing_after
    # An answered request serves no preview.
    again = await regio.service.view(answer.request)
    assert again.changes[0].preview is None
    assert again.changes[0].written_expectation_id == written


async def test_the_preview_of_a_retirement_counts_it_as_passing_and_a_put_back_as_failing(
    regio: Regio,
) -> None:
    retire = await regio.service.view(await regio.ask(want("retire", expectation_id=regio.high.id)))
    assert retire.changes[0].preview is not None
    assert (retire.changes[0].preview.failing_now, retire.changes[0].preview.failing_after) == (
        4,
        0,
    )

    await regio.repo.reject(regio.low.id, reason="too tight for this bean")
    back = await regio.service.view(await regio.ask(want("put_back", expectation_id=regio.low.id)))
    preview = back.changes[0].preview
    assert preview is not None
    assert (preview.shots, preview.failing_now, preview.failing_after) == (4, 0, 1)
    assert back.changes[0].rejected_reason == "too tight for this bean"
    assert back.changes[0].rejected_at is not None


async def test_a_tier_change_has_no_preview_and_a_draft_requests_none(regio: Regio) -> None:
    view = await regio.service.view(
        await regio.ask(want("tier", expectation_id=regio.high.id, tier="context"))
    )
    (change,) = view.changes
    assert change.preview is None
    assert (change.old_tier, change.new_tier) == ("important", "context")


async def test_the_preview_counts_the_shots_the_checks_are_read_for_and_not_the_discarded(
    regio: Regio,
) -> None:
    await regio.db.execute(
        "INSERT INTO shot_judgements (shot_id, discarded) VALUES (?, 1)", (regio.shots[90.5],)
    )
    view = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    preview = view.changes[0].preview
    assert preview is not None
    assert (preview.shots, preview.failing_now, preview.failing_after) == (3, 3, 0)


# ── the other changes ────────────────────────────────────────────────


async def test_retire_set_tier_and_put_back_use_the_verbs_a_person_has(regio: Regio) -> None:
    await regio.repo.reject(regio.low.id, reason="too tight for this bean")
    request = await regio.ask(
        want("retire", expectation_id=regio.high.id),
        want("put_back", expectation_id=regio.low.id),
    )
    answer = await regio.service.approve(request.id)
    assert answer.refused is None
    old = await regio.repo.get(regio.high.id)
    back = await regio.repo.get(regio.low.id)
    assert old is not None and back is not None
    assert (old.status, old.reject_reason.split(":")[0]) == (
        "rejected",
        "Retired at the agent's request",
    )
    assert back.status == "confirmed"

    moved = await regio.ask(want("tier", expectation_id=regio.low.id, tier="context"))
    assert (await regio.service.approve(moved.id)).refused is None
    assert (await regio.repo.get(regio.low.id)).tier == "context"  # type: ignore[union-attr]


async def test_a_rejected_override_is_put_back_on_the_conversations_own_version(
    regio: Regio,
) -> None:
    service = SignatureService(regio.db)
    override = await service.propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    await regio.repo.reject_override(override.id, reason="no")
    request = await regio.ask(want("put_back_override", override_id=override.id))
    view = await regio.service.view(request)
    assert view.changes[0].new_sentence == "at least 92 °C"
    assert (await regio.service.approve(request.id)).refused is None
    restored = await regio.repo.get_override(override.id)
    assert restored is not None and restored.status == "confirmed"


async def test_an_override_of_another_version_is_not_this_conversations(regio: Regio) -> None:
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    await regio.repo.reject_override(override.id, reason="no")
    with pytest.raises(SignatureRefused, match="not one of this conversation's version's"):
        await regio.service.request(
            profile_version_id=regio.version,
            changes=[want("put_back_override", override_id=override.id)],
            reason="Put it back.",
            thread_id=regio.thread,
            recipe_version_id=regio.recipe_version + 1,
        )
    with pytest.raises(SignatureRefused, match="has none to put it back on"):
        await regio.service.request(
            profile_version_id=regio.version,
            changes=[want("put_back_override", override_id=override.id)],
            reason="Put it back.",
            thread_id=regio.thread,
            recipe_version_id=None,
        )


async def test_rejecting_a_row_withdraws_its_override_as_it_does_today(regio: Regio) -> None:
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    request = await regio.ask(want("retire", expectation_id=regio.high.id))
    await regio.service.approve(request.id)
    withdrawn = await regio.repo.get_override(override.id)
    assert withdrawn is not None and withdrawn.status == "withdrawn"


# ── what is refused, each in its own sentence ────────────────────────


async def refusal(regio: Regio, *changes: ChangeIn) -> str:
    with pytest.raises(SignatureRefused) as excinfo:
        await regio.ask(*changes)
    assert await SignatureRequestsRepository(regio.db).for_thread(regio.thread) == []
    return str(excinfo.value)


async def test_a_change_to_a_row_that_is_not_in_force_is_refused_in_its_own_words(
    regio: Regio,
) -> None:
    await regio.repo.reject(regio.high.id, reason="too tight for this bean")
    for kind, extra in (
        (
            "replace",
            {"expectation": ExpectationInput(tier="important", kind="reached", phase="soak")},
        ),
        ("retire", {}),
        ("tier", {"tier": "context"}),
    ):
        said = await refusal(regio, want(kind, expectation_id=regio.high.id, **extra))
        assert "is one the person rejected, so it is not in force" in said
        assert "use a put_back change" in said


async def test_a_row_waiting_on_a_phase_and_one_from_before_are_not_in_force_either(
    regio: Regio,
) -> None:
    await regio.db.execute(
        "UPDATE signature_expectations SET status = 'proposed', needs_phase = 1 WHERE id = ?",
        (regio.high.id,),
    )
    said = await refusal(regio, want("retire", expectation_id=regio.high.id))
    assert "names a phase this profile version no longer has" in said
    await regio.db.execute(
        "UPDATE signature_expectations SET needs_phase = 0 WHERE id = ?", (regio.high.id,)
    )
    said = await refusal(regio, want("retire", expectation_id=regio.high.id))
    assert "from before signatures were in force at once" in said
    # A put back of either is refused too, in its own sentence.
    assert "was never rejected" in await refusal(
        regio, want("put_back", expectation_id=regio.high.id)
    )
    await regio.db.execute(
        "UPDATE signature_expectations SET needs_phase = 1 WHERE id = ?", (regio.high.id,)
    )
    assert "cannot be put back" in await refusal(
        regio, want("put_back", expectation_id=regio.high.id)
    )


async def test_a_put_back_of_a_row_in_force_is_refused(regio: Regio) -> None:
    said = await refusal(regio, want("put_back", expectation_id=regio.high.id))
    assert "is already in force, so there is nothing to put back" in said


async def test_a_replacement_whose_identity_is_already_there_is_refused_with_the_reason(
    regio: Regio,
) -> None:
    # The very guard that is in force.
    said = await refusal(regio, replacing(regio.high.id, floor(91)))
    assert "the replacement is already in force" in said
    # One the person rejected: told to ask for it back, not to propose it.
    await regio.repo.reject(regio.low.id, reason="no")
    said = await refusal(regio, replacing(regio.high.id, floor(91)))
    assert "one the person rejected" in said and "put_back" in said
    # Repeated inside the request.
    said = await refusal(
        regio,
        replacing(regio.high.id, floor(92.5)),
        want(
            "replace",
            expectation_id=regio.low.id,
            expectation=ExpectationInput(
                tier="important", kind="measure", phase="soak", expression=floor(92.5)
            ),
        ),
    )
    assert "repeated in this proposal" in said or "not in force" in said


async def test_a_tier_change_to_the_same_tier_is_refused(regio: Regio) -> None:
    said = await refusal(regio, want("tier", expectation_id=regio.high.id, tier="important"))
    assert "is already important" in said


async def test_two_changes_naming_one_row_are_refused(regio: Regio) -> None:
    said = await refusal(
        regio,
        want("retire", expectation_id=regio.high.id),
        want("tier", expectation_id=regio.high.id, tier="context"),
    )
    assert f"changes 1 and 2 both name expectation #{regio.high.id}" in said


async def test_every_problem_is_named_at_once(regio: Regio) -> None:
    await regio.repo.reject(regio.high.id, reason="no")
    said = await refusal(
        regio,
        want("retire", expectation_id=regio.high.id),
        want("tier", expectation_id=regio.low.id, tier="critical"),
        want("put_back", expectation_id=regio.low.id),
        ChangeIn(kind="retire", reason="short", expectation_id=regio.low.id),
    )
    assert "change 1:" in said and "change 2:" in said and "change 4:" in said
    assert "changes 2 and 3 both name" in said
    assert "its reason is under 20 characters" in said


async def test_a_change_with_the_wrong_fields_is_refused_by_kind(regio: Regio) -> None:
    said = await refusal(
        regio,
        want("retire"),
        want("tier", expectation_id=regio.high.id),
        want("retire", expectation_id=regio.high.id, tier="context"),
    )
    assert "a retire needs expectation_id" in said
    assert "a tier needs tier" in said
    assert "a retire takes no tier" in said


async def test_a_replacement_is_validated_against_the_profiles_phases(regio: Regio) -> None:
    said = await refusal(
        regio,
        want(
            "replace",
            expectation_id=regio.high.id,
            expectation=ExpectationInput(
                tier="important", kind="reached", phase="a phase it does not have"
            ),
        ),
    )
    assert "the profile has no phase" in said


async def test_a_request_needs_a_conversation_and_between_one_and_ten_changes(
    regio: Regio,
) -> None:
    with pytest.raises(SignatureRefused, match="has none"):
        await regio.service.request(
            profile_version_id=regio.version,
            changes=[want("retire", expectation_id=regio.high.id)],
            reason="x",
            thread_id=None,
        )
    with pytest.raises(SignatureRefused, match="at least one"):
        await regio.service.request(
            profile_version_id=regio.version, changes=[], reason="x", thread_id=regio.thread
        )
    with pytest.raises(SignatureRefused, match="at most 10"):
        await regio.service.request(
            profile_version_id=regio.version,
            changes=[want("retire", expectation_id=regio.high.id)] * 11,
            reason="x",
            thread_id=regio.thread,
        )


async def test_an_expectation_of_another_profile_version_is_not_on_this_one(regio: Regio) -> None:
    other = await _version(regio.db, {**LEVER_PROFILE, "temperature": 91.0})
    (foreign,) = await SignatureService(regio.db).propose(
        other,
        [ExpectationInput(tier="critical", kind="measure", phase="soak", expression=floor(88))],
        reason="Another profile.",
    )
    said = await refusal(regio, want("retire", expectation_id=foreign.id))
    assert f"expectation #{foreign.id} is not on profile version {regio.version}" in said


async def test_a_put_back_override_that_restore_override_would_refuse_is_refused(
    regio: Regio,
) -> None:
    service = SignatureService(regio.db)
    first = await service.propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    # In force, not rejected.
    assert "is not rejected" in await refusal(
        regio, want("put_back_override", override_id=first.id)
    )
    await regio.repo.reject_override(first.id, reason="no")
    second = await service.propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 91.5},
        reason="Another.",
        thread_id=None,
    )
    # Another one in force now.
    assert "already has an override in force" in await refusal(
        regio, want("put_back_override", override_id=first.id)
    )
    await regio.repo.reject_override(second.id, reason="no")
    await regio.repo.reject(regio.high.id, reason="no")
    # Its expectation is out of force.
    assert "not in force on this profile version" in await refusal(
        regio, want("put_back_override", override_id=first.id)
    )
    assert "there is no override 999" in await refusal(
        regio, want("put_back_override", override_id=999)
    )


async def test_a_pins_copy_carries_no_signature_and_is_refused(regio: Regio) -> None:
    copy = await _version(regio.db, {**LEVER_PROFILE, "label": "a pin copy", "temperature": 92.0})
    board = await regio.db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin) VALUES ('pin', ?, 'draft')",
        (copy,),
    )
    await regio.db.execute(
        "INSERT INTO recipe_version_pins (recipe_version_id, board_id, profile_version_id) "
        "VALUES (?, ?, ?)",
        (regio.recipe_version, board.lastrowid, copy),
    )
    with pytest.raises(SignatureRefused, match="A pin is judged by"):
        await regio.service.request(
            profile_version_id=copy,
            changes=[want("retire", expectation_id=regio.high.id)],
            reason="x",
            thread_id=regio.thread,
        )


# ── the guard and its twin are one function ──────────────────────────


@pytest.mark.parametrize("state", ["confirmed", "rejected", "needs_phase", "from_before"])
@pytest.mark.parametrize("kind", ["replace", "retire", "tier", "put_back"])
async def test_what_the_guard_lets_through_the_button_can_apply(
    regio: Regio, kind: str, state: str
) -> None:
    """For every kind of change on every state of a row: if `problems` is empty, Approve writes
    without a refusal (so a tool can never store what the person's button cannot apply); and
    the states the spec names are the ones it refuses."""
    row = regio.high.id
    if state == "rejected":
        await regio.repo.reject(row, reason="no")
    elif state == "needs_phase":
        await regio.db.execute(
            "UPDATE signature_expectations SET status = 'proposed', needs_phase = 1 WHERE id = ?",
            (row,),
        )
    elif state == "from_before":
        await regio.db.execute(
            "UPDATE signature_expectations SET status = 'proposed' WHERE id = ?", (row,)
        )
    extras: dict[str, dict[str, Any]] = {
        "replace": {"expectation": ExpectationInput(tier="critical", kind="reached", phase="soak")},
        "tier": {"tier": "context"},
    }
    extra = extras.get(kind, {})
    outcome: str
    try:
        request = await regio.ask(want(kind, expectation_id=row, **extra))
    except SignatureRefused:
        outcome = "refused"
    else:
        answer = await regio.service.approve(request.id)
        assert answer.refused is None, (kind, state)
        outcome = "applied"
    allowed = {
        "replace": {"confirmed"},
        "retire": {"confirmed"},
        "tier": {"confirmed"},
        "put_back": {"rejected"},
    }[kind]
    assert (outcome == "applied") == (state in allowed), (kind, state, outcome)


async def test_the_approve_check_is_the_tools_function(
    regio: Regio, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both callers go through `problem_list`: replacing it changes what is stored and what is
    approved (the mutant that drops the twin check fails this)."""
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    calls: list[int] = []
    original = SignatureRequestService.problem_list

    async def counting(self: Any, version: int, changes: Any, **kw: Any) -> list[Any]:
        calls.append(version)
        return await original(self, version, changes, **kw)

    monkeypatch.setattr(SignatureRequestService, "problem_list", counting)
    await regio.service.approve(request.id)
    assert calls == [regio.version]
    await regio.ask(want("retire", expectation_id=regio.low.id))
    assert calls == [regio.version, regio.version]


# ── a request that no longer applies is stale, and says why ──────────


async def stale_reason(regio: Regio, request_id: int) -> tuple[str, str]:
    row = await SignatureRequestsRepository(regio.db).get(request_id)
    assert row is not None
    if row.status == "stale":
        assert_a_sentence(row.stale_reason)  # every producer, whichever test reached it
    return row.status, row.stale_reason


async def test_approving_what_no_longer_applies_makes_the_request_stale_and_writes_nothing(
    regio: Regio,
) -> None:
    request = await regio.ask(
        replacing(regio.high.id, floor(91.5)),
        want("tier", expectation_id=regio.low.id, tier="context"),
    )
    # Something the hooks do not see: the row is rejected behind the repository's back.
    await regio.db.execute(
        "UPDATE signature_expectations SET status = 'rejected' WHERE id = ?", (regio.low.id,)
    )
    answer = await regio.service.approve(request.id)
    assert answer.refused == "stale" and answer.request is not None
    assert (
        answer.request.status == "stale"
        and "was rejected, so it is not in force" in answer.request.stale_reason
    )
    # The first change was not applied either: one request, one answer.
    assert (await regio.repo.get(regio.high.id)).status == "confirmed"  # type: ignore[union-attr]
    assert (await regio.service.approve(request.id)).refused == "not_waiting"


def person_facing(reason: str) -> None:
    """What a card shows: the row's words, no #id, no tool name, whole sentences."""
    import re

    assert not re.search(r"#\d", reason), reason
    assert not re.search(r"put_back|request_signature_change|propose_|override \d", reason), reason
    assert ".." not in reason and reason.endswith("."), reason
    assert reason[0].isupper() or reason[0] == "“", reason


async def test_what_no_longer_applies_is_said_to_the_person_in_the_rows_own_words(
    regio: Regio,
) -> None:
    """Behind the repository's back, so only the approve-time guard can notice: every kind of
    problem it names reaches the card as a person's sentence (the agent's own wording, with the
    #id and the tool to call, stays in the refusal a tool gets)."""
    db = regio.db
    cases: list[tuple[ChangeIn, str, tuple[Any, ...], str]] = []
    # The row was rejected behind the back.
    cases.append(
        (
            want("retire", expectation_id=regio.high.id),
            "UPDATE signature_expectations SET status = 'rejected' WHERE id = ?",
            (regio.high.id,),
            "at least 93",
        )
    )
    # The row went to the tier it was asked to move to.
    cases.append(
        (
            want("tier", expectation_id=regio.low.id, tier="context"),
            "UPDATE signature_expectations SET tier = 'context' WHERE id = ?",
            (regio.low.id,),
            "at least 91",
        )
    )
    # The row it names a phase that is gone.
    cases.append(
        (
            replacing(regio.high.id, floor(91.5)),
            "UPDATE signature_expectations SET needs_phase = 1, status = 'proposed' WHERE id = ?",
            (regio.high.id,),
            "at least 93",
        )
    )
    for change, behind_the_back, params, words in cases:
        request = await regio.ask(change)
        await db.execute(behind_the_back, params)
        answer = await regio.service.approve(request.id)
        assert answer.refused == "stale" and answer.request is not None
        reason = answer.request.stale_reason
        person_facing(reason)
        assert words in reason, reason
        # Put it all back for the next case.
        await db.execute(
            "UPDATE signature_expectations SET status = 'confirmed', needs_phase = 0, "
            "tier = CASE id WHEN ? THEN 'important' ELSE 'critical' END",
            (regio.high.id,),
        )
    # A put back of a row someone has already put back.
    await regio.repo.reject(regio.low.id, reason="too tight")
    back = await regio.ask(want("put_back", expectation_id=regio.low.id))
    await db.execute(
        "UPDATE signature_expectations SET status = 'confirmed' WHERE id = ?", (regio.low.id,)
    )
    answer = await regio.service.approve(back.id)
    assert answer.request is not None and answer.refused == "stale"
    person_facing(answer.request.stale_reason)
    assert "at least 91" in answer.request.stale_reason
    # The agent's refusal is another text, with the id and the tool.
    with pytest.raises(SignatureRefused) as refused:
        await regio.ask(want("put_back", expectation_id=regio.low.id))
    assert f"#{regio.low.id}" in str(refused.value)


async def test_two_changes_naming_one_row_are_said_even_when_an_earlier_change_is_malformed(
    regio: Regio,
) -> None:
    with pytest.raises(SignatureRefused) as refused:
        await regio.ask(
            want("tier", expectation_id=regio.low.id),  # no tier: a shape problem
            want("retire", expectation_id=regio.high.id),
            want("tier", expectation_id=regio.high.id, tier="context"),
        )
    said = str(refused.value)
    assert "change 1:" in said and "a tier needs tier" in said
    assert f"changes 2 and 3 both name expectation #{regio.high.id}" in said
    # A malformed change names a row too: it clashes with a later good one.
    with pytest.raises(SignatureRefused) as clash:
        await regio.ask(
            want("tier", expectation_id=regio.high.id),  # malformed
            want("retire", expectation_id=regio.high.id),
        )
    assert f"changes 1 and 2 both name expectation #{regio.high.id}" in str(clash.value)


async def test_an_unreadable_request_and_a_draft_that_lost_its_rows_say_why_in_sentences(
    regio: Regio,
) -> None:
    """The producers no hook reaches: stored JSON that no longer reads, and a draft's change that
    names a row the version the draft became does not carry."""
    request = await regio.ask(want("retire", expectation_id=regio.high.id))
    await regio.db.execute(
        "UPDATE signature_change_requests SET changes_json = '{' WHERE id = ?", (request.id,)
    )
    assert (await regio.service.approve(request.id)).refused == "stale"
    assert (await stale_reason(regio, request.id))[1] == "The request can no longer be read."

    sentence = "Another different limit was put in force for this Recipe version"
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    await regio.repo.reject_override(override.id, reason="no")
    waiting = await regio.ask(want("put_back_override", override_id=override.id))
    await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.low.id,
        compare={"op": ">=", "value": 90.5},
        reason="A coarser bean.",
        thread_id=None,
    )
    status, reason = await stale_reason(regio, waiting.id)
    assert status == "stale" and reason.startswith(sentence)


async def test_the_person_rejecting_restoring_or_moving_a_named_row_stales_the_request(
    regio: Regio,
) -> None:
    for name in ("rejected", "tier"):
        request = await regio.ask(replacing(regio.high.id, floor(91.5)))
        if name == "rejected":
            await regio.repo.reject(regio.high.id, reason="no")
        else:
            await regio.repo.set_tier(regio.high.id, "critical")
        status, reason = await stale_reason(regio, request.id)
        assert status == "stale", name
        assert "on the Profiles page" in reason and "at least 93" in reason, reason
        await regio.repo.restore(regio.high.id)  # the next case starts from a row in force
        await regio.repo.set_tier(regio.high.id, "important")
    # A restore: a put back that names a rejected row.
    await regio.repo.reject(regio.low.id, reason="no")
    back = await regio.ask(want("put_back", expectation_id=regio.low.id))
    await regio.repo.restore(regio.low.id)
    status, reason = await stale_reason(regio, back.id)
    assert status == "stale" and reason.startswith("You restored")


async def test_the_person_answering_a_named_override_stales_the_request(regio: Regio) -> None:
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92},
        reason="A coarser bean.",
        thread_id=None,
    )
    await regio.repo.reject_override(override.id, reason="no")
    request = await regio.ask(want("put_back_override", override_id=override.id))
    await regio.repo.restore_override(override.id)
    status, reason = await stale_reason(regio, request.id)
    assert status == "stale" and "You restored the different limit" in reason
    await regio.repo.reject_override(override.id, reason="no")
    again = await regio.ask(want("put_back_override", override_id=override.id))
    # Rejecting an override that is already rejected is a refusal and changes nothing.
    assert (await regio.repo.reject_override(override.id, reason="no")).refused
    assert (await stale_reason(regio, again.id))[0] == "waiting"


async def test_another_path_adding_the_replacement_stales_the_request(regio: Regio) -> None:
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    # An unrelated expectation does not.
    await SignatureService(regio.db).propose(
        regio.version,
        [ExpectationInput(tier="context", kind="reached", phase="decline")],
        reason="Another.",
    )
    assert (await stale_reason(regio, request.id))[0] == "waiting"
    await SignatureService(regio.db).propose(
        regio.version,
        [ExpectationInput(tier="important", kind="measure", phase="soak", expression=floor(91.5))],
        reason="The same limit by another path.",
    )
    status, reason = await stale_reason(regio, request.id)
    assert status == "stale" and "was added another way" in reason


async def test_a_newer_request_naming_the_same_row_replaces_the_older(regio: Regio) -> None:
    older = await regio.ask(replacing(regio.high.id, floor(91.5)))
    other = await regio.ask(want("tier", expectation_id=regio.low.id, tier="context"))
    newer = await regio.ask(replacing(regio.high.id, floor(92)))
    assert await stale_reason(regio, older.id) == ("stale", "A newer request replaced it.")
    assert (await stale_reason(regio, other.id))[0] == "waiting"
    assert (await stale_reason(regio, newer.id))[0] == "waiting"


async def test_each_stale_hook_is_in_the_transaction_of_its_cause(regio: Regio) -> None:
    """The cause fails after the hook ran: the request is waiting still, not stale."""
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    with pytest.raises(RuntimeError, match="boom"):
        async with regio.db.transaction():
            assert (await regio.repo.reject(regio.high.id, reason="no")).row is not None
            assert (await stale_reason(regio, request.id))[0] == "stale"
            raise RuntimeError("boom")
    assert (await stale_reason(regio, request.id))[0] == "waiting"
    assert (await regio.repo.get(regio.high.id)).status == "confirmed"  # type: ignore[union-attr]


async def test_a_newer_request_stales_the_older_in_the_same_transaction(regio: Regio) -> None:
    older = await regio.ask(replacing(regio.high.id, floor(91.5)))
    with pytest.raises(RuntimeError, match="boom"):
        async with regio.db.transaction():
            await regio.ask(replacing(regio.high.id, floor(92)))
            assert (await stale_reason(regio, older.id))[0] == "stale"
            raise RuntimeError("boom")
    assert (await stale_reason(regio, older.id))[0] == "waiting"


async def test_approving_is_one_transaction_the_last_write_failing_undoes_the_first(
    regio: Regio, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = await regio.ask(
        replacing(regio.high.id, floor(91.5)),
        want("tier", expectation_id=regio.low.id, tier="context"),
    )
    real = SignatureRepository.set_tier

    async def failing(self: SignatureRepository, *args: Any, **kwargs: Any) -> Any:
        await real(self, *args, **kwargs)
        raise RuntimeError("the last write failed")

    monkeypatch.setattr(SignatureRepository, "set_tier", failing)
    with pytest.raises(RuntimeError, match="the last write failed"):
        await regio.service.approve(request.id)
    # The reject and the add of the first change are undone, and the request still waits.
    assert (await regio.repo.get(regio.high.id)).status == "confirmed"  # type: ignore[union-attr]
    assert len(await regio.repo.for_version(regio.version)) == 2
    assert (await regio.repo.get(regio.low.id)).tier == "critical"  # type: ignore[union-attr]
    row = await SignatureRequestsRepository(regio.db).get(request.id)
    assert row is not None and row.status == "waiting" and row.written == []


# ── decline, and asking again ────────────────────────────────────────


async def test_decline_changes_nothing_and_the_agent_may_ask_again(regio: Regio) -> None:
    before = await regio.served()
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    answer = await regio.service.decline(request.id, "keep it, the boiler should hold 93")
    assert answer.request is not None
    assert (answer.request.status, answer.request.answer_reason) == (
        "declined",
        "keep it, the boiler should hold 93",
    )
    assert await regio.served() == before
    assert (await regio.repo.get(regio.high.id)).status == "confirmed"  # type: ignore[union-attr]
    assert (await regio.service.decline(request.id)).refused == "not_waiting"
    assert (await regio.service.approve(request.id)).refused == "not_waiting"
    # D4: a second request after a Decline is accepted.
    again = await regio.ask(replacing(regio.high.id, floor(91.5)))
    assert again.status == "waiting"


async def test_asking_again_after_a_restore_is_accepted(regio: Regio) -> None:
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    answer = await regio.service.approve(request.id)
    assert answer.request is not None
    await regio.repo.restore(regio.high.id)
    await regio.repo.reject(answer.request.written[0], reason="no")
    # The same replacement is now one the person rejected: it is asked for back, not proposed.
    with pytest.raises(SignatureRefused, match="put_back"):
        await regio.ask(replacing(regio.high.id, floor(91.5)))
    assert (await regio.ask(want("put_back", expectation_id=answer.request.written[0]))).status == (
        "waiting"
    )
    assert (await regio.ask(replacing(regio.high.id, floor(92)))).status == "waiting"


async def test_an_unknown_request_is_none_and_a_draft_s_is_not_answered_here(
    regio: Regio,
) -> None:
    assert (await regio.service.approve(999)).refused == "no_request"
    assert (await regio.service.decline(999)).refused == "no_request"


# ── what is served ───────────────────────────────────────────────────


async def test_a_request_serves_what_the_card_reads(regio: Regio) -> None:
    await regio.repo.reject(regio.low.id, reason="too tight for this bean")
    request = await regio.ask(
        replacing(regio.high.id, floor(91.5)),
        want("put_back", expectation_id=regio.low.id),
    )
    view = await regio.service.view(request)
    assert (view.id, view.status, view.stale_reason, view.answer_reason) == (
        request.id,
        "waiting",
        "",
        "",
    )
    assert view.profile_version_id == regio.version
    assert view.profile_label == "Constructed lever decline"
    # How the Profiles page names it: the first version its profile has had.
    assert view.version_label == "v1"
    assert view.thread_id == regio.thread and view.draft_id is None
    replace_view, back_view = view.changes
    assert replace_view.kind == "replace" and replace_view.tier == "important"
    assert replace_view.phase == "soak"
    assert replace_view.old_sentence == regio.high.sentence + " °C"
    assert replace_view.new_sentence.endswith("at least 91.5 °C")
    assert (replace_view.old_limit_text, replace_view.new_limit_text) == (
        "at least 93 °C",
        "at least 91.5 °C",
    )
    assert replace_view.expectation_id == regio.high.id
    assert replace_view.reason.startswith("The 93 was a floor")
    assert back_view.kind == "put_back" and back_view.rejected_reason == "too tight for this bean"


async def test_the_card_reads_a_request_after_the_row_it_was_about_has_moved_on(
    regio: Regio,
) -> None:
    await regio.repo.reject(regio.low.id, reason="too tight for this bean")
    request = await regio.ask(want("put_back", expectation_id=regio.low.id))
    await regio.service.approve(request.id)
    answered = await SignatureRequestsRepository(regio.db).get(request.id)
    assert answered is not None
    view = await regio.service.view(answered)
    # The snapshot, not the row as it is now (restore cleared its reason).
    assert view.changes[0].rejected_reason == "too tight for this bean"
    assert view.changes[0].old_sentence == regio.low.sentence + " °C"


async def test_the_open_prediction_a_loosening_is_graded_under_is_served(regio: Regio) -> None:
    await regio.db.execute(
        "UPDATE recipe_versions SET prediction = 'Less sour', outcome = NULL WHERE id = ?",
        (regio.recipe_version,),
    )
    tightening = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(94))))
    assert tightening.loosens_open_prediction is None
    loosening = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    assert loosening.loosens_open_prediction == "Alturas v1"
    # With the outcome recorded it is no longer open.
    await regio.db.execute(
        "UPDATE recipe_versions SET outcome = 'held' WHERE id = ?", (regio.recipe_version,)
    )
    again = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    assert again.loosens_open_prediction is None


async def test_a_pins_copy_s_open_prediction_is_one_a_loosening_is_graded_under(
    regio: Regio,
) -> None:
    """A Recipe version that brews a pin's copy of this profile version is graded under this
    version's signature, so its open prediction counts. The first half: a Recipe on the copy with
    an open prediction is named; the second: the same Recipe with the prediction answered is not."""
    copy = await _version(regio.db, {**LEVER_PROFILE, "label": "a pin copy", "temperature": 92.0})
    board = await regio.db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin) VALUES ('pin', ?, 'draft')",
        (copy,),
    )
    await regio.db.execute(
        "INSERT INTO recipe_version_pins (recipe_version_id, board_id, profile_version_id) "
        "VALUES (?, ?, ?)",
        (regio.recipe_version, board.lastrowid, copy),
    )
    elsewhere = await _version(regio.db, {**LEVER_PROFILE, "label": "elsewhere", "temperature": 91})
    on_copy, _ = await make_recipe_versions(regio.db, elsewhere)
    # The repository refuses a Recipe version on a copy; an older database may hold one.
    await regio.db.execute(
        "UPDATE recipe_versions SET profile_version_id = ?, prediction = 'Less sour', "
        "outcome = NULL WHERE id = ?",
        (copy, on_copy),
    )
    assert on_copy != regio.recipe_version
    view = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    assert view.loosens_open_prediction == "Alturas v1"
    await regio.db.execute("UPDATE recipe_versions SET outcome = 'held' WHERE id = ?", (on_copy,))
    again = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    assert again.loosens_open_prediction is None


async def test_a_rejected_row_that_needs_a_new_phase_cannot_be_put_back(regio: Regio) -> None:
    await regio.repo.reject(regio.low.id, reason="too tight")
    # First half: rejected and with its phase, it can be asked for.
    request = await regio.ask(want("put_back", expectation_id=regio.low.id))
    assert request.status == "waiting"
    await regio.service.decline(request.id)
    # The version's phase went away (carried onto a profile without it, say).
    await regio.db.execute(
        "UPDATE signature_expectations SET needs_phase = 1 WHERE id = ?", (regio.low.id,)
    )
    with pytest.raises(SignatureRefused) as refused:
        await regio.ask(want("put_back", expectation_id=regio.low.id))
    said = str(refused.value)
    assert "names a phase this profile version no longer has" in said
    assert "cannot be put back" in said


async def test_a_floors_replacement_lists_the_other_floors_never_a_ceiling(regio: Regio) -> None:
    """Two halves on the same phase and channel: a ceiling is not listed under a floor's
    replacement, a floor (and a between's low end) is; under a ceiling it is the other way."""
    ceiling, band = await SignatureService(regio.db).propose(
        regio.version,
        [
            ExpectationInput(
                tier="context",
                kind="measure",
                phase="soak",
                expression={**floor(95.0), "op": "max", "compare": {"op": "<=", "value": 95.0}},
            ),
            ExpectationInput(
                tier="context",
                kind="measure",
                phase="soak",
                expression={
                    **floor(0),
                    "op": "mean",
                    "compare": {"op": "between", "low": 90.0, "high": 96.0},
                },
            ),
        ],
        reason="A ceiling and a band.",
    )
    under_floor = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    listed = [s.expectation_id for s in under_floor.changes[0].stays_in_force]
    assert regio.low.id in listed and band.id in listed  # a floor, and the band's low end
    assert ceiling.id not in listed
    # The other side: a ceiling's retirement lists the other ceilings and the band's high end.
    under_ceiling = await regio.service.view(
        await regio.ask(want("retire", expectation_id=ceiling.id))
    )
    listed = [s.expectation_id for s in under_ceiling.changes[0].stays_in_force]
    assert listed == [band.id]


async def test_a_tier_row_s_header_names_the_tier_it_has_now_and_the_line_says_where_it_goes(
    regio: Regio,
) -> None:
    view = await regio.service.view(
        await regio.ask(want("tier", expectation_id=regio.low.id, tier="context"))
    )
    (change,) = view.changes
    assert (change.tier, change.old_tier, change.new_tier) == ("critical", "critical", "context")


async def test_a_replacement_or_retirement_says_which_other_limits_stay_in_force(
    regio: Regio,
) -> None:
    replace = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    (staying,) = replace.changes[0].stays_in_force
    # The whole row, so two limits of different measures read differently.
    assert (staying.expectation_id, staying.words) == (
        regio.low.id,
        "lowest value of temperature in the soak, at least 91 °C",
    )
    retire = await regio.service.view(await regio.ask(want("retire", expectation_id=regio.low.id)))
    assert [s.expectation_id for s in retire.changes[0].stays_in_force] == [regio.high.id]
    # What the same request takes out does not stay; a tier move leaves everything where it is.
    both = await regio.service.view(
        await regio.ask(
            want("retire", expectation_id=regio.low.id),
            replacing(regio.high.id, floor(92)),
        )
    )
    assert [c.stays_in_force for c in both.changes] == [[], []]
    tier = await regio.service.view(
        await regio.ask(want("tier", expectation_id=regio.low.id, tier="context"))
    )
    assert tier.changes[0].stays_in_force == []
    # A different channel or phase is not "the same thing": a pressure row says nothing here,
    # and neither does a temperature row of another phase (the first half: same phase, listed).
    mean_here, elsewhere = await SignatureService(regio.db).propose(
        regio.version,
        [
            ExpectationInput(
                tier="context",
                kind="measure",
                phase="soak",
                expression={
                    "channel": "temperature",
                    "op": "mean",
                    "window": {"phase": " SOAK "},
                    "compare": {"op": ">=", "value": 91},
                },
            ),
            ExpectationInput(
                tier="context",
                kind="measure",
                phase="ramp",
                expression={
                    "channel": "temperature",
                    "op": "min",
                    "window": {"phase": "ramp"},
                    "compare": {"op": ">=", "value": 91},
                },
            ),
        ],
        reason="Two more.",
    )
    # One phase spelt two ways: the same phase, whatever the case or the spaces around it.
    await regio.db.execute(
        "UPDATE signature_expectations SET phase = ' SoaK ' WHERE id = ?", (mean_here.id,)
    )
    listed = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    by_id = {s.expectation_id: s.words for s in listed.changes[0].stays_in_force}
    assert elsewhere.id not in by_id
    assert by_id[mean_here.id] == "mean of temperature over the soak, at least 91 °C"
    assert by_id[regio.low.id].startswith("lowest value of temperature in the soak")
    other = await SignatureService(regio.db).propose(
        regio.version,
        [
            ExpectationInput(
                tier="context",
                kind="measure",
                phase="soak",
                expression={
                    "channel": "pressure",
                    "op": "mean",
                    "window": {"phase": "soak"},
                    "compare": {"op": ">=", "value": 5},
                },
            )
        ],
        reason="A pressure floor.",
    )
    after = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    assert other[0].id not in [s.expectation_id for s in after.changes[0].stays_in_force]
    # An answered request makes no claim about now.
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    await regio.service.approve(request.id)
    answered = await regio.service.requests.get(request.id)
    assert answered is not None
    assert (await regio.service.view(answered)).changes[0].stays_in_force == []


async def test_the_compare_in_a_replacement_reads_in_the_words_of_its_offset(regio: Regio) -> None:
    request = await regio.ask(
        replacing(regio.high.id, floor(-2.0, offset_from="profile_temperature"))
    )
    view = await regio.service.view(request)
    assert view.changes[0].new_sentence.endswith("at least 2 °C below the setpoint")
    preview = view.changes[0].preview
    assert preview is not None and (preview.failing_now, preview.failing_after) == (4, 1)
    answer = await regio.service.approve(request.id)
    assert answer.request is not None
    (written,) = answer.request.written
    # Follows the setpoint: the floor is 91 under this profile's 93, and the 90.5 shot fails it.
    served = [await regio.status(shot, written) for shot in regio.shots.values()]
    assert served.count("failed") == 1


async def test_compare_is_what_the_request_stores(regio: Regio) -> None:
    request = await regio.ask(replacing(regio.high.id, floor(91.5)))
    assert request.changes is not None
    change = request.changes.changes[0]
    assert change.kind == "replace"
    assert change.expectation.expression is not None
    assert change.expectation.expression.compare == Compare(op=">=", value=91.5)


async def test_the_preview_counts_the_shots_of_a_pins_copy_as_the_original_s(regio: Regio) -> None:
    """A pin's copy is judged by the signature of the profile it was copied from, so its shots
    are among the ones the checks of this version are read for."""
    copy = await _version(regio.db, {**LEVER_PROFILE, "label": "a pin copy", "temperature": 92.0})
    board = await regio.db.execute(
        "INSERT INTO profile_board (label, current_version_id, origin) VALUES ('pin', ?, 'draft')",
        (copy,),
    )
    await regio.db.execute(
        "INSERT INTO recipe_version_pins (recipe_version_id, board_id, profile_version_id) "
        "VALUES (?, ?, ?)",
        (regio.recipe_version, board.lastrowid, copy),
    )
    on_copy = await add_shot(
        regio.db,
        recipe_version_id=None,
        profile_version_id=copy,
        slog=sagging_shot(91.0),
        device_id="000990",
    )
    # The shot's served checks are the original's: it fails the 93 floor and holds the 91 one.
    assert await regio.status(on_copy, regio.high.id) == "failed"
    view = await regio.service.view(await regio.ask(replacing(regio.high.id, floor(91.5))))
    preview = view.changes[0].preview
    assert preview is not None and preview.shots == 5
    assert (preview.failing_now, preview.failing_after) == (5, 2)
