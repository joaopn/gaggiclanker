"""Replacing the override in force on a Recipe version: one approval swaps it for another limit.

A version has one override at a time, so `propose_signature_override` cannot move it to another
expectation. `replace_override` asks the person to: the old override is rejected with the agent's
reason and the new one put in force, in one transaction. Every rule is a test, in the Regio case
(a floor of 93 written under the old setpoint, a guard of 91, and four shots whose soaks sag).
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.domain.signature import SignatureRefused
from gaggiclanker.domain.signature_changes import ReplaceOverrideChange, rows_named
from gaggiclanker.signatures.service import SignatureService
from tests.signatures.helpers import assert_a_sentence
from tests.signatures.test_change_requests import Regio, regio, want

__all__ = ["regio"]

A_BEAN = "This version's beans sag a bit more than the profile assumes."


async def in_force(regio: Regio, expectation_id: int, value: float) -> int:
    """An override in force on the conversation's version: "at least <value>"."""
    row = await SignatureService(regio.db).propose_override(
        recipe_version_id=regio.recipe_version,
        profile_version_id=regio.version,
        expectation_id=expectation_id,
        compare={"op": ">=", "value": value},
        reason=A_BEAN,
        thread_id=regio.thread,
    )
    return row.id


def swap(override_id: int, expectation_id: int, value: float, **extra: Any) -> Any:
    return want(
        "replace_override",
        override_id=override_id,
        expectation_id=expectation_id,
        override={"op": ">=", "value": value},
        **extra,
    )


async def served_failures(regio: Regio, expectation_id: int) -> int:
    return [await regio.status(shot, expectation_id) for shot in regio.shots.values()].count(
        "failed"
    )


# ── what is asked, and what is refused ──────────────────────────────


async def test_the_kind_needs_its_three_fields_and_a_recipe_version(regio: Regio) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    for missing in ("override_id", "expectation_id", "override"):
        fields: dict[str, Any] = {
            "override_id": old,
            "expectation_id": regio.high.id,
            "override": {"op": ">=", "value": 91.5},
        }
        del fields[missing]
        with pytest.raises(SignatureRefused) as refused:
            await regio.ask(want("replace_override", **fields))
        assert f"a replace_override needs {missing}" in str(refused.value)
    with pytest.raises(SignatureRefused) as stray:
        await regio.ask(swap(old, regio.high.id, 91.5, tier="context"))
    assert "takes no tier" in str(stray.value)
    # Recipe chat only: a conversation with no Recipe version has none to swap on.
    with pytest.raises(SignatureRefused) as none:
        await regio.service.request(
            profile_version_id=regio.version,
            changes=[swap(old, regio.high.id, 91.5)],
            reason="No Recipe here.",
            thread_id=regio.thread,
            recipe_version_id=None,
        )
    assert "has none to replace one of" in str(none.value)


async def test_the_override_named_must_be_the_one_in_force_on_this_version(regio: Regio) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.low.id, 90.0))
    assert request.status == "waiting"
    # First half above; now an override that is not in force (rejected), one that is not there,
    # and one of another Recipe version.
    await regio.repo.reject_override(old, reason="no")
    with pytest.raises(SignatureRefused) as rejected:
        await regio.ask(swap(old, regio.low.id, 90.0))
    assert f"override {old} is not the one in force" in str(rejected.value)
    with pytest.raises(SignatureRefused) as missing:
        await regio.ask(swap(999, regio.low.id, 90.0))
    assert "there is no override 999" in str(missing.value)
    other_version = int(
        await regio.db.fetch_value(
            "SELECT id FROM recipe_versions WHERE recipe_id = ? AND id != ?",
            (regio.recipe_id, regio.recipe_version),
        )
    )
    elsewhere = await SignatureService(regio.db).propose_override(
        recipe_version_id=other_version,
        profile_version_id=regio.version,
        expectation_id=regio.low.id,
        compare={"op": ">=", "value": 90.5},
        reason=A_BEAN,
        thread_id=regio.thread,
    )
    with pytest.raises(SignatureRefused) as foreign:
        await regio.ask(swap(elsewhere.id, regio.low.id, 90.0))
    assert "is not one of this conversation's version's" in str(foreign.value)


async def test_the_new_limit_is_validated_by_the_function_propose_override_uses(
    regio: Regio,
) -> None:
    """Each refusal is `validate_override`'s own sentence, so the two cannot drift apart."""
    old = await in_force(regio, regio.high.id, 92.0)
    service = SignatureService(regio.db)
    cases: list[tuple[Any, dict[str, Any]]] = [
        (
            swap(old, regio.high.id, 91.5).model_copy(
                update={"override": {"op": "<=", "value": 99}}
            ),
            {"op": "<=", "value": 99},
        ),
        (
            want(
                "replace_override",
                override_id=old,
                expectation_id=regio.high.id,
                override={"op": "nope"},
            ),
            {"op": "nope"},
        ),
    ]
    for change, compare in cases:
        with pytest.raises(SignatureRefused) as asked:
            await regio.ask(change)
        with pytest.raises(SignatureRefused) as direct:
            await service.validate_override(
                recipe_version_id=regio.recipe_version,
                profile_version_id=regio.version,
                expectation_id=regio.high.id,
                compare=compare,
            )
        assert str(direct.value).split(":")[0].rstrip(".") in str(asked.value)
    # The person rejected exactly this limit before: only they can restore it.
    await regio.repo.reject_override(old, reason="no")
    rejected = await in_force(regio, regio.low.id, 90.0)
    await regio.repo.reject_override(rejected, reason="no")
    again = await in_force(regio, regio.high.id, 92.25)
    with pytest.raises(SignatureRefused) as exact:
        await regio.ask(swap(again, regio.low.id, 90.0))
    assert "rejected exactly this limit" in str(exact.value)
    # An expectation the person rejected has no limit to replace.
    await regio.repo.reject(regio.low.id, reason="too tight")
    with pytest.raises(SignatureRefused) as gone:
        await regio.ask(swap(again, regio.low.id, 90.5))
    assert "Only an expectation in force" in str(gone.value)


async def test_the_one_at_a_time_rule_is_read_as_if_the_override_were_withdrawn(
    regio: Regio,
) -> None:
    """propose_signature_override refuses another expectation's override while one is in force;
    the swap, which withdraws it first, is not refused by that (first half: the refusal)."""
    old = await in_force(regio, regio.high.id, 92.0)
    with pytest.raises(SignatureRefused) as refused:
        await in_force(regio, regio.low.id, 90.0)
    said = str(refused.value)
    assert "a version has one at a time" in said
    assert "request_signature_change with a replace_override change" in said
    request = await regio.ask(swap(old, regio.low.id, 90.0))
    assert request.status == "waiting"


# ── what approving does ──────────────────────────────────────────────


@pytest.mark.parametrize("same", [True, False])
async def test_approving_withdraws_the_old_override_and_puts_the_new_one_in_force(
    regio: Regio, same: bool
) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    target = regio.high if same else regio.low
    request = await regio.ask(swap(old, target.id, 91.5 if same else 90.0))
    # Waiting changes no check and no override.
    assert [
        o.id
        for o in await regio.repo.overrides_for_recipe_version(
            regio.recipe_version, statuses=["confirmed"]
        )
    ] == [old]

    answer = await regio.service.approve(request.id)

    assert answer.refused is None and answer.request is not None
    (new_id,) = answer.request.written
    old_row = await regio.repo.get_override(old)
    new_row = await regio.repo.get_override(new_id)
    assert old_row is not None and new_row is not None
    assert old_row.status == "rejected"
    assert old_row.reject_reason.startswith("Replaced at the agent's request: ")
    assert new_row.status == "confirmed" and new_row.expectation_id == target.id
    assert new_row.proposed_by_thread_id == regio.thread
    live = await regio.repo.overrides_for_recipe_version(
        regio.recipe_version, statuses=["proposed", "confirmed"]
    )
    assert [o.id for o in live] == [new_id]
    view = await regio.service.view(answer.request)
    assert view.changes[0].written_override_id == new_id


async def test_approving_is_one_transaction(regio: Regio, monkeypatch: pytest.MonkeyPatch) -> None:
    """The last write of the approval fails: the old override is still in force."""
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.low.id, 90.0))

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the last write failed")

    monkeypatch.setattr(type(regio.service.requests), "set_written", broken)
    with pytest.raises(RuntimeError):
        await regio.service.approve(request.id)
    monkeypatch.undo()

    row = await regio.repo.get_override(old)
    assert row is not None and row.status == "confirmed"
    assert (await regio.service.requests.get(request.id)).status == "waiting"  # type: ignore[union-attr]
    assert len(await regio.repo.overrides_for_recipe_version(regio.recipe_version)) == 1


async def test_a_draft_never_carries_it(regio: Regio) -> None:
    from gaggiclanker.db.repos.pins import judged_profile_version  # noqa: F401

    old = await in_force(regio, regio.high.id, 92.0)
    with pytest.raises(SignatureRefused) as refused:
        await regio.service.request_for_draft(
            draft_id=1,
            draft_version_id=regio.version + 100,
            base_version_id=regio.version,
            changes=[swap(old, regio.low.id, 90.0)],
            reason="With a draft.",
            thread_id=regio.thread,
        )
    assert "There is no profile version" in str(
        refused.value
    ) or "cannot replace an override" in str(refused.value)


# ── one definition of the rows it names ─────────────────────────────


async def test_it_names_the_old_override_and_both_expectations_once_each(regio: Regio) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.low.id, 90.0))
    assert request.changes is not None
    change = request.changes.changes[0]
    assert isinstance(change, ReplaceOverrideChange)
    assert rows_named(change) == [("o", old), ("e", regio.high.id), ("e", regio.low.id)]
    same = await regio.ask(swap(old, regio.high.id, 91.5))
    assert same.changes is not None
    assert rows_named(same.changes.changes[0]) == [("o", old), ("e", regio.high.id)]


async def test_every_stale_hook_covers_the_rows_it_names(regio: Regio) -> None:
    tries = iter(x / 100 for x in range(9200, 9300, 5))

    async def asked() -> tuple[int, int]:
        old = await in_force(regio, regio.high.id, next(tries))
        return old, (await regio.ask(swap(old, regio.low.id, 90.0))).id

    async def status(request_id: int) -> str:
        row = await regio.service.requests.get(request_id)
        assert row is not None
        if row.status == "stale":
            assert_a_sentence(row.stale_reason)
        return row.status

    # The person rejects or restores the override on the Recipe page.
    old, request = await asked()
    await regio.repo.reject_override(old, reason="no")
    assert await status(request) == "stale"
    # Either expectation rejected, or moved to another tier.
    for mover in ("reject_old_expectation", "reject_new_expectation", "tier_old", "tier_new"):
        await regio.repo.restore(regio.high.id)
        await regio.repo.restore(regio.low.id)
        old, request = await asked()
        assert await status(request) == "waiting", mover
        if mover == "reject_old_expectation":
            await regio.repo.reject(regio.high.id, reason="no")
        elif mover == "reject_new_expectation":
            await regio.repo.reject(regio.low.id, reason="no")
        elif mover == "tier_old":
            await regio.repo.set_tier(regio.high.id, "critical")
        else:
            await regio.repo.set_tier(regio.low.id, "context")
        assert await status(request) == "stale", mover
        await regio.repo.set_tier(regio.high.id, "important")
        await regio.repo.set_tier(regio.low.id, "critical")
    # Another override coming into force on the version (a tool, for the same expectation).
    await regio.repo.restore(regio.high.id)
    await regio.repo.restore(regio.low.id)
    old, request = await asked()
    await in_force(regio, regio.high.id, next(tries))
    assert await status(request) == "stale"
    # A newer request naming any of its rows.
    current = (
        await regio.repo.overrides_for_recipe_version(regio.recipe_version, statuses=["confirmed"])
    )[0]
    older = await regio.ask(swap(current.id, regio.low.id, 90.0))
    await regio.ask(want("retire", expectation_id=regio.low.id))
    assert await status(older.id) == "stale"


# ── loosening, the preview and its stop condition ───────────────────


async def test_it_loosens_when_the_new_limit_is_looser_than_the_one_judging_the_shots(
    regio: Regio,
) -> None:
    await regio.db.execute(
        "UPDATE recipe_versions SET prediction = 'Less sour', outcome = NULL WHERE id = ?",
        (regio.recipe_version,),
    )
    old = await in_force(regio, regio.high.id, 92.0)

    async def loosens(change: Any) -> bool:
        view = await regio.service.view(await regio.ask(change))
        return view.loosens_open_prediction is not None

    # The same expectation: against the override in force (92), not the profile's own 93.
    assert await loosens(swap(old, regio.high.id, 91.5)) is True
    assert await loosens(swap(old, regio.high.id, 92.5)) is False
    # Another expectation: against its own limit (91).
    assert await loosens(swap(old, regio.low.id, 90.0)) is True
    assert await loosens(swap(old, regio.low.id, 91.5)) is False


@pytest.mark.parametrize("same", [True, False])
async def test_the_preview_reads_the_recipe_versions_shots_and_agrees_with_the_served_checks(
    regio: Regio, same: bool
) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    target = regio.high if same else regio.low
    request = await regio.ask(swap(old, target.id, 91.5 if same else 90.0))
    view = await regio.service.view(request)
    preview = view.changes[0].preview
    assert preview is not None and preview.shots == 4
    # Worked out by hand: lows 91.9 / 92.0 / 92.4 / 90.5. Same expectation, "at least 92" now
    # fails 91.9 and 90.5; "at least 91.5" fails 90.5. Another expectation: its own 91 fails
    # 90.5 now, and "at least 90" fails nothing.
    expected = (2, 1) if same else (1, 0)
    assert (preview.failing_now, preview.failing_after) == expected
    assert await served_failures(regio, target.id) == preview.failing_now

    answer = await regio.service.approve(request.id)
    assert answer.refused is None
    # The stop condition: after the approval, what the shots' served checks say.
    assert await served_failures(regio, target.id) == preview.failing_after
    answered = await regio.service.requests.get(request.id)
    assert answered is not None
    assert (await regio.service.view(answered)).changes[0].preview is None


async def test_the_card_is_served_with_the_recipe_the_limits_and_the_sentences(
    regio: Regio,
) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    view = await regio.service.view(await regio.ask(swap(old, regio.low.id, 90.0)))
    change = view.changes[0]
    assert change.kind == "replace_override"
    assert change.recipe_version_label == "Alturas v1"
    assert (change.override_id, change.old_expectation_id, change.expectation_id) == (
        old,
        regio.high.id,
        regio.low.id,
    )
    assert (change.old_limit_text, change.new_limit_text) == ("at least 92 °C", "at least 90 °C")
    assert change.old_sentence == "lowest value of temperature in the soak, at least 93 °C"
    assert change.new_sentence == "lowest value of temperature in the soak, at least 91 °C"
    assert change.summary == "at least 90 °C"


async def test_the_agent_is_told_what_became_of_it(regio: Regio) -> None:
    from gaggiclanker.signatures.lines import signature_requests_block

    old = await in_force(regio, regio.high.id, 92.0)
    waiting = await regio.ask(swap(old, regio.low.id, 90.0))
    block = await signature_requests_block(regio.db, regio.thread)
    assert block[1].startswith(
        f"- request #{waiting.id}: replace the different limit {old} (at least 92 °C) for "
        f"#{regio.high.id} “lowest value of temperature in the soak, at least 93 °C” with "
        f"at least 90 °C for #{regio.low.id} "
    )
    assert block[1].endswith("(waiting: nothing changes until the person presses Approve)")
    answer = await regio.service.approve(waiting.id)
    assert answer.request is not None
    (made,) = answer.request.written
    approved = (await signature_requests_block(regio.db, regio.thread))[1]
    assert approved.endswith(f"(approved: in force now as limit #{made})")
    declined = await regio.ask(swap(made, regio.high.id, 91.5))
    await regio.service.decline(declined.id, "keep it")
    assert (await signature_requests_block(regio.db, regio.thread))[-1].endswith(
        '(declined by the person: "keep it")'
    )


# ── round 5: the profile it is for, no-ops, what loosens and what is counted ──────────────


async def _another_profile_brewed_by_the_other_version(regio: Regio) -> tuple[int, int, int, int]:
    """The Recipe's second version brews L2 (a carried copy of L1's signature): returns (L2, that
    Recipe version, the override in force on it, L2's copy of the 93 floor)."""
    from gaggiclanker.domain.signature import ExpectationInput  # noqa: F401
    from tests.lever_shot import LEVER_PROFILE
    from tests.signatures.test_carry import _version

    l2 = await _version(regio.db, {**LEVER_PROFILE, "temperature": 92.0})
    await SignatureService(regio.db).carry(regio.version, l2)
    copies = {r.carried_from_id: r.id for r in await regio.repo.for_version(l2)}
    other = int(
        await regio.db.fetch_value(
            "SELECT id FROM recipe_versions WHERE recipe_id = ? AND id != ?",
            (regio.recipe_id, regio.recipe_version),
        )
    )
    await regio.db.execute(
        "UPDATE recipe_versions SET profile_version_id = ? WHERE id = ?", (l2, other)
    )
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=other,
        profile_version_id=l2,
        expectation_id=copies[regio.high.id],
        compare={"op": ">=", "value": 92.5},
        reason=A_BEAN,
        thread_id=regio.thread,
    )
    return l2, other, override.id, copies[regio.high.id]


async def test_a_swap_is_for_the_profile_version_the_recipe_version_brews(regio: Regio) -> None:
    l2, other, override, high_on_l2 = await _another_profile_brewed_by_the_other_version(regio)

    async def ask(profile_version_id: int, expectation_id: int) -> Any:
        return await regio.service.request(
            profile_version_id=profile_version_id,
            changes=[swap(override, expectation_id, 92.0)],
            reason="Swap it.",
            thread_id=regio.thread,
            recipe_version_id=other,
        )

    # The other half first: the profile version it does brew, and its own expectation.
    request = await ask(l2, high_on_l2)
    assert request.status == "waiting"
    # An older profile version it does not brew (still in scope for the conversation), and an
    # expectation of that one for the new limit.
    with pytest.raises(SignatureRefused) as refused:
        await ask(regio.version, regio.high.id)
    said = str(refused.value)
    assert f"brews profile version {l2}" in said and f"ask about profile version {l2}" in said
    # Nothing was written: the override named is still the one in force, on its own expectation.
    live = await regio.repo.overrides_for_recipe_version(other, statuses=["confirmed"])
    assert [(o.id, o.expectation_id) for o in live] == [(override, high_on_l2)]


async def test_approve_reads_the_profile_the_recipe_version_brews_now(regio: Regio) -> None:
    l2, other, override, high_on_l2 = await _another_profile_brewed_by_the_other_version(regio)
    request = await regio.service.request(
        profile_version_id=l2,
        changes=[swap(override, high_on_l2, 92.0)],
        reason="Swap it.",
        thread_id=regio.thread,
        recipe_version_id=other,
    )
    # Behind the back, the Recipe version is moved to brew the first profile version.
    await regio.db.execute(
        "UPDATE recipe_versions SET profile_version_id = ? WHERE id = ?", (regio.version, other)
    )
    answer = await regio.service.approve(request.id)
    assert answer.refused == "stale" and answer.request is not None
    assert_a_sentence(answer.request.stale_reason)
    assert "this request is about another version" in answer.request.stale_reason
    live = await regio.repo.overrides_for_recipe_version(other, statuses=["confirmed"])
    assert [o.id for o in live] == [override]


async def test_a_swap_to_the_limit_already_in_force_is_refused_as_nothing_to_replace(
    regio: Regio,
) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    with pytest.raises(SignatureRefused) as same:
        await regio.ask(swap(old, regio.high.id, 92.0))
    assert "that is the limit already in force" in str(same.value)
    # The other half: another number, or another expectation with the same number.
    assert (await regio.ask(swap(old, regio.high.id, 92.5))).status == "waiting"
    assert (await regio.ask(swap(old, regio.low.id, 92.0))).status == "waiting"


async def test_the_loosening_line_names_only_the_swapped_recipe_version(regio: Regio) -> None:
    """An override judges only its own Recipe version's shots: another version's open
    prediction is not graded under it."""
    other = int(
        await regio.db.fetch_value(
            "SELECT id FROM recipe_versions WHERE recipe_id = ? AND id != ?",
            (regio.recipe_id, regio.recipe_version),
        )
    )
    await regio.db.execute(
        "UPDATE recipe_versions SET prediction = 'Less sour', outcome = NULL WHERE id = ?",
        (regio.recipe_version,),
    )
    override = await SignatureService(regio.db).propose_override(
        recipe_version_id=other,
        profile_version_id=regio.version,
        expectation_id=regio.high.id,
        compare={"op": ">=", "value": 92.0},
        reason=A_BEAN,
        thread_id=regio.thread,
    )

    async def loosens() -> str | None:
        request = await regio.service.request(
            profile_version_id=regio.version,
            changes=[swap(override.id, regio.high.id, 91.5)],
            reason="Looser.",
            thread_id=regio.thread,
            recipe_version_id=other,
        )
        return (await regio.service.view(request)).loosens_open_prediction

    # Only the first version has an open prediction, and it is not the one swapped.
    assert await loosens() is None
    await regio.db.execute(
        "UPDATE recipe_versions SET prediction = 'Rounder', outcome = NULL WHERE id = ?", (other,)
    )
    named = await loosens()
    assert named is not None and named == await regio.service.requests.recipe_version_label(other)
    assert named != await regio.service.requests.recipe_version_label(regio.recipe_version)


async def test_a_swap_to_another_expectation_counts_the_old_one_going_back_to_its_profile_limit(
    regio: Regio,
) -> None:
    await regio.db.execute(
        "UPDATE recipe_versions SET prediction = 'Less sour', outcome = NULL WHERE id = ?",
        (regio.recipe_version,),
    )

    async def loosens(old_value: float, new_value: float) -> bool:
        # Fresh override each time: reject the one before.
        for live in await regio.repo.overrides_for_recipe_version(
            regio.recipe_version, statuses=["confirmed"]
        ):
            await regio.repo.reject_override(live.id, reason="no")
        old = await in_force(regio, regio.high.id, old_value)
        request = await regio.ask(swap(old, regio.low.id, new_value))
        return (await regio.service.view(request)).loosens_open_prediction is not None

    # The new limit for #2 (profile 91) is tighter in both. An override of 94 on the 93 floor
    # handed back to 93 is looser; one of 92 handed back to 93 is tighter.
    assert await loosens(94.0, 91.5) is True
    assert await loosens(92.25, 91.5) is False


async def test_a_swap_to_another_expectation_previews_both_and_the_stop_condition_holds_for_each(
    regio: Regio,
) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.low.id, 90.0))
    change = (await regio.service.view(request)).changes[0]
    assert change.preview is not None and change.old_preview is not None
    # Lows 91.9 / 92.0 / 92.4 / 90.5. The new limit's expectation (91, then "at least 90"):
    # fails 1, then 0. The old one ("at least 92" now, back to the profile's 93): fails 2, then 4.
    assert (change.preview.failing_now, change.preview.failing_after) == (1, 0)
    assert (change.old_preview.failing_now, change.old_preview.failing_after) == (2, 4)
    assert change.preview.shots == change.old_preview.shots == 4
    assert await served_failures(regio, regio.low.id) == 1
    assert await served_failures(regio, regio.high.id) == 2

    assert (await regio.service.approve(request.id)).refused is None
    assert await served_failures(regio, regio.low.id) == change.preview.failing_after
    assert await served_failures(regio, regio.high.id) == change.old_preview.failing_after
    # The same expectation has no second line.
    current = (
        await regio.repo.overrides_for_recipe_version(regio.recipe_version, statuses=["confirmed"])
    )[0]
    same = await regio.ask(swap(current.id, regio.low.id, 89.5))
    assert (await regio.service.view(same)).changes[0].old_preview is None


async def test_the_swap_preview_leaves_out_discarded_shots(regio: Regio) -> None:
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.high.id, 91.5))
    assert (await regio.service.view(request)).changes[0].preview.shots == 4  # type: ignore[union-attr]
    await regio.db.execute(
        "INSERT INTO shot_judgements (shot_id, discarded) VALUES (?, 1)", (regio.shots[90.5],)
    )
    view = await regio.service.view(request)
    preview = view.changes[0].preview
    assert preview is not None and preview.shots == 3
    # The discarded one was the one that failed after.
    assert (preview.failing_now, preview.failing_after) == (1, 0)


async def test_what_the_guard_reads_again_at_approve(regio: Regio) -> None:
    """Behind the repository's back, so only the approve-time guard can see: the person having
    rejected exactly the new limit. (A second override in force is not reachable: a unique index
    on the Recipe version refuses the row.)"""
    old = await in_force(regio, regio.high.id, 92.0)
    request = await regio.ask(swap(old, regio.low.id, 90.0))
    await regio.db.execute(
        "INSERT INTO recipe_version_signature_overrides "
        "(recipe_version_id, expectation_id, compare_json, reason, status, reject_reason, "
        " proposed_at) VALUES (?, ?, ?, 'x', 'rejected', 'no', '2026-11-02T00:00:00Z')",
        (regio.recipe_version, regio.low.id, '{"op": ">=", "value": 90.0}'),
    )
    answer = await regio.service.approve(request.id)
    assert answer.refused == "stale" and answer.request is not None
    assert_a_sentence(answer.request.stale_reason)
    assert "rejected exactly this limit" in answer.request.stale_reason


async def test_a_draft_never_carries_a_swap_for_a_version_it_could_make(regio: Regio) -> None:
    from tests.lever_shot import LEVER_PROFILE
    from tests.signatures.test_carry import _version

    old = await in_force(regio, regio.high.id, 92.0)
    drafted = await _version(regio.db, {**LEVER_PROFILE, "temperature": 90.0})
    await SignatureService(regio.db).carry(regio.version, drafted)
    with pytest.raises(SignatureRefused) as refused:
        await regio.service.request_for_draft(
            draft_id=1,
            draft_version_id=drafted,
            base_version_id=regio.version,
            changes=[swap(old, regio.low.id, 90.0)],
            reason="With a draft.",
            thread_id=regio.thread,
        )
    assert "cannot replace an override" in str(refused.value)
