"""`propose_signature` and the signature a draft carries: rows a person answers, never checks.

The profile is the review fixture's (phases Pre-infusion, Bloom, Pressurise, Ramp down,
Hammer), and the Set is the fixture's one Set.
"""

from __future__ import annotations

from typing import Any

import pytest

from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.signatures.service import SignatureService
from gaggiclanker.tools.registry import ToolContext, registry
from gaggiclanker.tools.scope import ToolScope
from tests.review.conftest import Fixture
from tests.tools.test_builtin import call, refuse

RAMP_DOWN_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "Ramp down"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.8},
}


def _propose(archive: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "profile_version_id": archive.profile_version_id,
        "expectations": [
            {"tier": "critical", "kind": "measure", "expression": RAMP_DOWN_CUP},
            {"tier": "important", "kind": "reached", "phase": "Hammer"},
        ],
        "reason": "The ramp down is where the cup fills.",
        **extra,
    }


async def test_a_set_chat_proposes_a_signature_as_rows_a_person_answers(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    set_ctx.thread_id = None
    data = await call(set_ctx, "propose_signature", **_propose(archive))

    assert data["status"] == "proposed"
    assert [(p["tier"], p["kind"], p["phase"]) for p in data["proposed"]] == [
        ("critical", "measure", "Ramp down"),
        ("important", "reached", "Hammer"),
    ]
    assert data["proposed"][0]["fault"] == "early yield"
    assert "Nothing is checked" in data["note"]
    repo = SignatureRepository(archive.db)
    rows = await repo.for_version(archive.profile_version_id)
    assert {r.status for r in rows} == {"proposed"}
    assert await repo.confirmed_for_versions([archive.profile_version_id]) == {}


async def test_the_proposer_is_recorded_as_the_thread(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    thread = await _thread(archive)
    set_ctx.thread_id = thread
    await call(set_ctx, "propose_signature", **_propose(archive))
    rows = await SignatureRepository(archive.db).proposed_in_thread(thread)
    assert len(rows) == 2


async def _thread(archive: Fixture) -> int:
    from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(title="t", set_id=archive.set_id)
    )
    assert created.thread is not None
    return created.thread.id


async def test_a_refusal_names_every_problem_and_writes_nothing(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        set_ctx,
        "propose_signature",
        profile_version_id=archive.profile_version_id,
        expectations=[
            {"tier": "critical", "kind": "reached", "phase": "Bloom soak"},
            {"tier": "critical", "kind": "free_text", "text": "x", "fault": "weird"},
        ],
        reason="r",
    )
    assert "expectation 1" in data["detail"] and "expectation 2" in data["detail"]
    assert await SignatureRepository(archive.db).for_version(archive.profile_version_id) == []


async def test_a_profile_outside_the_set_is_refused_in_the_same_words_whether_or_not_it_exists(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    other = await refuse(
        set_ctx, "propose_signature", **_propose(archive, profile_version_id=99999)
    )
    # A real profile version that no version of this Set brews.
    from tests.sets.conftest import make_profile_version

    elsewhere = await make_profile_version(archive.db, "Somebody else's profile")
    foreign = await refuse(
        set_ctx, "propose_signature", **_propose(archive, profile_version_id=elsewhere)
    )
    assert other["detail"] == foreign["detail"]
    assert "not one this conversation is about" in other["detail"]


async def test_a_general_conversation_cannot_propose_one(
    ctx: ToolContext, archive: Fixture
) -> None:
    outcome = await refuse(ctx, "propose_signature", **_propose(archive))
    assert "propose_signature" in str(outcome)


async def test_a_conversation_designing_a_set_proposes_for_the_profile_it_forks(
    ctx: ToolContext, archive: Fixture
) -> None:
    from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetWrite

    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id),
        DesignBrief(fork_profile_version_id=archive.profile_version_id),
    )
    ctx.scope = ToolScope.for_thread(designed.id, designing=True)

    data = await call(ctx, "propose_signature", **_propose(archive))
    assert data["status"] == "proposed"
    # ... and for no other profile.
    other = await refuse(ctx, "propose_signature", **_propose(archive, profile_version_id=99999))
    assert "forks" in other["detail"]


# ── draft_profile carries a signature ────────────────────────────────


def _with_drafts(ctx: ToolContext) -> ToolContext:
    ctx.drafts = DraftProposals(ctx.db, ctx.settings)
    return ctx


async def test_a_draft_carries_the_agents_signature_as_proposed_on_its_own_version(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await call(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        signature=[
            {"tier": "critical", "kind": "measure", "expression": RAMP_DOWN_CUP},
            {"tier": "context", "kind": "free_text", "text": "pressure falls", "fault": "unstable"},
        ],
    )

    assert data["signature_proposed"] == 2
    draft = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert draft is not None and draft.draft_version_id is not None
    rows = await SignatureRepository(archive.db).for_version(draft.draft_version_id)
    assert {(r.status, r.proposed_by_draft_id) for r in rows} == {("proposed", draft.id)}
    assert "Profiles page" in data["note"]


async def test_a_signature_naming_a_phase_the_patch_removes_is_refused_before_a_draft_exists(
    ctx: ToolContext, archive: Fixture
) -> None:
    data = await refuse(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        signature=[{"tier": "critical", "kind": "reached", "phase": "Decline"}],
    )
    assert "no phase 'Decline'" in data["detail"]
    count = await archive.db.fetch_value("SELECT COUNT(*) FROM profile_drafts")
    assert count == 0


async def test_a_signature_may_name_a_phase_the_patch_adds(
    ctx: ToolContext, archive: Fixture
) -> None:
    from gaggiclanker.db.repos.profiles import ProfilesRepository

    base = await ProfilesRepository(archive.db).get_version(archive.profile_version_id)
    assert base is not None
    existing = list(base.profile["phases"])  # type: ignore[index]
    phases = [*existing, {**existing[-1], "name": "Finish"}]
    data = await call(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"phases": phases},
        reason="A finish phase.",
        signature=[{"tier": "critical", "kind": "reached", "phase": "Finish"}],
    )
    assert data["signature_proposed"] == 1


async def test_a_draft_and_its_signature_are_one_write(
    ctx: ToolContext, archive: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the expectations could not be written")

    monkeypatch.setattr(SignatureService, "add_valid", boom)
    outcome = await registry.dispatch(
        _with_drafts(ctx),
        "draft_profile",
        {
            "base_version_id": archive.profile_version_id,
            "patch": {"temperature": 92},
            "reason": "A degree cooler.",
            "signature": [{"tier": "critical", "kind": "reached", "phase": "Hammer"}],
        },
    )

    assert not outcome.ok
    # Neither the draft row nor any expectation is left behind.
    assert await archive.db.fetch_value("SELECT COUNT(*) FROM profile_drafts") == 0
    assert await archive.db.fetch_value("SELECT COUNT(*) FROM signature_expectations") == 0


async def test_the_same_expectation_is_refused_by_the_tool_as_already_there(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    await call(set_ctx, "propose_signature", **_propose(archive))
    again = await refuse(set_ctx, "propose_signature", **_propose(archive))
    assert "expectation 1 is already proposed and waiting for the person" in again["detail"]
    rows = await SignatureRepository(archive.db).for_version(archive.profile_version_id)
    assert len(rows) == 2


async def test_a_draft_does_not_store_an_expectation_its_version_already_carries(
    ctx: ToolContext, archive: Fixture
) -> None:
    service = SignatureService(archive.db)
    (hammer,) = await service.propose(
        archive.profile_version_id,
        [ExpectationInput(tier="critical", kind="reached", phase="Hammer")],
        reason="r",
    )
    await SignatureRepository(archive.db).answer(hammer.id, confirm=True)

    data = await call(
        _with_drafts(ctx),
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 92},
        reason="A degree cooler.",
        signature=[
            # Carried from the base already.
            {"tier": "critical", "kind": "reached", "phase": "Hammer"},
            {"tier": "context", "kind": "free_text", "text": "it tapers", "fault": "unstable"},
            # Sent twice.
            {"tier": "context", "kind": "free_text", "text": "It  tapers", "fault": "unstable"},
        ],
    )

    assert data["signature_proposed"] == 1
    assert "2 of the expectations were not stored again" in data["note"]
    draft = await ProfileDraftsRepository(archive.db).get(data["draft_id"])
    assert draft is not None and draft.draft_version_id is not None
    rows = await SignatureRepository(archive.db).for_version(draft.draft_version_id)
    assert sorted((r.kind, r.carried_from_id) for r in rows) == [
        ("free_text", None),
        ("reached", hammer.id),
    ]
    # "Confirm all" confirms each once.
    confirmed = await SignatureRepository(archive.db).confirm_all(draft.draft_version_id)
    assert len(confirmed) == 2


# ── unconfirmed never teaches ────────────────────────────────────────


async def _shot_texts(ctx: ToolContext, archive: Fixture) -> dict[str, str]:
    shot = archive.shots[-1]
    other = archive.shots[-2]
    texts: dict[str, str] = {}
    for name in ("get_shot", "get_shot_extended", "get_shot_full"):
        texts[name] = str(await call(ctx, name, shot_id=shot))
    texts["compare_shots"] = str(await call(ctx, "compare_shots", shot_ids=[shot, other]))
    texts["list_set_shots"] = str(await call(ctx, "list_set_shots"))
    return texts


async def test_no_shot_tool_tells_an_agent_an_expectation_nobody_confirmed(
    set_ctx: ToolContext, archive: Fixture
) -> None:
    waiting = "pressure falls together with flow through the ramp down"
    service = SignatureService(archive.db)
    proposed, rejected, confirmed = await service.propose(
        archive.profile_version_id,
        [
            ExpectationInput(tier="context", kind="free_text", text=waiting, fault="unstable"),
            ExpectationInput(
                tier="context", kind="free_text", text="the rejected one", fault="unstable"
            ),
            ExpectationInput(
                tier="context", kind="free_text", text="the confirmed one", fault="unstable"
            ),
        ],
        reason="r",
    )
    repo = SignatureRepository(archive.db)
    await repo.answer(rejected.id, confirm=False, reject_reason="no")
    texts = await _shot_texts(set_ctx, archive)

    assert proposed.status == "proposed"
    for name, text in texts.items():
        assert waiting not in text, name
        assert "the rejected one" not in text, name
        assert "read without a signature" in text or name == "get_shot_extended", name

    await repo.answer(confirmed.id, confirm=True)
    after = await _shot_texts(set_ctx, archive)
    assert "signature: confirmed, 1 expectation" in after["get_shot"]
    assert "the confirmed one" in after["get_shot_extended"]
    assert "the confirmed one" not in after["get_shot"], "free text is extended, not base"
    for text in after.values():
        assert waiting not in text and "the rejected one" not in text
