"""`request_signature_change`: the agent asks, the tool writes one waiting row and nothing else.

The profile is the review fixture's (phases Pre-infusion, Bloom, Pressurise, Ramp down,
Hammer) and the Recipe is the fixture's one Recipe.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite
from gaggiclanker.db.repos.signature_requests import SignatureRequestsRepository
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.drafts.proposals import DraftProposals
from gaggiclanker.signatures.service import SignatureService
from gaggiclanker.tools.registry import ToolContext
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
SAME = "The cup was over its limit on every shot, and the limit was written for a bigger cup."


async def _thread(archive: Fixture) -> int:
    created = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(title="t", recipe_id=archive.recipe_id)
    )
    assert created.thread is not None
    return created.thread.id


async def _in_force(archive: Fixture) -> list[ExpectationRow]:
    return await SignatureService(archive.db).propose(
        archive.profile_version_id,
        [
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_DOWN_CUP),
            ExpectationInput(tier="important", kind="reached", phase="Hammer"),
        ],
        reason="What it is for.",
    )


def _ask(archive: Fixture, *changes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "profile_version_id": archive.profile_version_id,
        "changes": list(changes),
        "reason": "The limit is wrong for the profile as it is now.",
        **extra,
    }


def retire(row: ExpectationRow) -> dict[str, Any]:
    return {"kind": "retire", "expectation_id": row.id, "reason": SAME}


async def test_the_tool_writes_one_waiting_request_and_changes_nothing(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    rows = await _in_force(archive)
    recipe_ctx.thread_id = await _thread(archive)
    before = [
        r.model_dump_json()
        for r in await SignatureRepository(archive.db).for_version(archive.profile_version_id)
    ]

    data = await call(recipe_ctx, "request_signature_change", **_ask(archive, retire(rows[0])))

    assert data["status"] == "waiting" and data["profile_version_id"] == archive.profile_version_id
    assert data["changes"] == [
        {"kind": "retire", "description": f"retire #{rows[0].id} “{rows[0].sentence}”"}
    ]
    assert "Nothing has changed" in data["note"] and "Approve" in data["note"]
    # What the agent is told about the answer, which it would otherwise wait for or ask about.
    assert "You are told their answer at your next message" in data["note"]
    assert [
        r.model_dump_json()
        for r in await SignatureRepository(archive.db).for_version(archive.profile_version_id)
    ] == before
    stored = await SignatureRequestsRepository(archive.db).for_thread(recipe_ctx.thread_id)
    assert [(r.id, r.status) for r in stored] == [(data["request_id"], "waiting")]


async def test_the_tool_needs_a_conversation(recipe_ctx: ToolContext, archive: Fixture) -> None:
    rows = await _in_force(archive)
    recipe_ctx.thread_id = None
    said = await refuse(recipe_ctx, "request_signature_change", **_ask(archive, retire(rows[0])))
    assert "answered on a card in a conversation" in said["detail"]


async def test_the_scope_refusal_is_propose_signatures_sentence_for_a_version_outside(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    from tests.recipes.conftest import make_profile_version
    from tests.tools.test_signature_tools import _propose

    recipe_ctx.thread_id = await _thread(archive)
    elsewhere = await make_profile_version(archive.db, "Somebody else's profile")
    asked = await refuse(
        recipe_ctx,
        "request_signature_change",
        profile_version_id=elsewhere,
        changes=[{"kind": "retire", "expectation_id": 1, "reason": SAME}],
        reason="x",
    )
    proposed = await refuse(
        recipe_ctx, "propose_signature", **_propose(archive, profile_version_id=elsewhere)
    )
    assert "not one this conversation is about" in asked["detail"]
    # The same words but the verb: "Ask about" a profile one of this Recipe's versions brews.
    assert asked["detail"].split("brews")[1:] == proposed["detail"].split("brews")[1:]
    absent = await refuse(
        recipe_ctx,
        "request_signature_change",
        profile_version_id=99999,
        changes=[{"kind": "retire", "expectation_id": 1, "reason": SAME}],
        reason="x",
    )
    assert absent["detail"] == asked["detail"], "the same words whether or not it exists"


async def test_a_design_conversation_asks_for_the_profile_it_forks_but_has_no_override_to_put_back(
    ctx: ToolContext, archive: Fixture
) -> None:
    from gaggiclanker.db.repos.recipes import DesignBrief, RecipesRepository, RecipeWrite

    rows = await _in_force(archive)
    designed = await RecipesRepository(archive.db).create_design(
        RecipeWrite(name="Designed", bean_id=archive.bean_id),
        DesignBrief(fork_profile_version_id=archive.profile_version_id),
    )
    ctx.scope = ToolScope.for_thread(designed.id, designing=True)
    created = await ChatRepository(archive.db).create_thread(
        ChatThreadWrite(title="d", recipe_id=designed.id)
    )
    assert created.thread is not None
    ctx.thread_id = created.thread.id

    data = await call(ctx, "request_signature_change", **_ask(archive, retire(rows[0])))
    assert data["status"] == "waiting"
    said = await refuse(
        ctx,
        "request_signature_change",
        **_ask(archive, {"kind": "put_back_override", "override_id": 1, "reason": SAME}),
    )
    assert "has none to put it back on" in said["detail"]


async def test_every_refusal_is_a_sentence_and_writes_nothing(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    rows = await _in_force(archive)
    thread = await _thread(archive)
    recipe_ctx.thread_id = thread
    await SignatureRepository(archive.db).reject(rows[1].id, reason="no")
    said = await refuse(
        recipe_ctx,
        "request_signature_change",
        **_ask(
            archive,
            retire(rows[1]),
            {"kind": "put_back", "expectation_id": rows[0].id, "reason": SAME},
            {"kind": "tier", "expectation_id": rows[0].id, "tier": "critical", "reason": SAME},
        ),
    )
    detail = said["detail"]
    assert "change 1:" in detail and "one the person rejected" in detail
    assert "change 2:" in detail and "already in force" in detail
    assert "changes 2 and 3 both name" in detail
    assert await SignatureRequestsRepository(archive.db).for_thread(thread) == []


async def test_a_second_request_after_a_decline_is_accepted(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    from gaggiclanker.signatures.change_requests import SignatureRequestService

    rows = await _in_force(archive)
    recipe_ctx.thread_id = await _thread(archive)
    first = await call(recipe_ctx, "request_signature_change", **_ask(archive, retire(rows[0])))
    await SignatureRequestService(archive.db).decline(first["request_id"], "keep it")
    second = await call(recipe_ctx, "request_signature_change", **_ask(archive, retire(rows[0])))
    assert second["request_id"] != first["request_id"]


async def test_propose_signature_points_a_rejected_duplicate_to_the_request(
    recipe_ctx: ToolContext, archive: Fixture
) -> None:
    from tests.tools.test_signature_tools import _propose

    rows = await _in_force(archive)
    await SignatureRepository(archive.db).reject(rows[0].id, reason="no")
    said = await refuse(recipe_ctx, "propose_signature", **_propose(archive))
    assert (
        "only they can restore it; to ask for it back, call request_signature_change"
        in (said["detail"])
    )


async def test_draft_profile_serves_the_flags_and_stores_the_changes(
    ctx: ToolContext, archive: Fixture
) -> None:
    rows = await SignatureService(archive.db).propose(
        archive.profile_version_id,
        [
            ExpectationInput(
                tier="important",
                kind="measure",
                phase="Ramp down",
                expression={
                    "channel": "temperature",
                    "op": "min",
                    "window": {"phase": "Ramp down"},
                    "compare": {"op": ">=", "value": 90},
                },
            )
        ],
        reason="What it is for.",
    )
    ctx.drafts = DraftProposals(ctx.db, ctx.settings)
    ctx.thread_id = await _thread(archive)

    first = await call(
        ctx,
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 88},
        reason="A cooler profile.",
    )
    assert first["affected_expectations"] == [
        {
            "expectation_id": rows[0].id,
            # The limit in its unit, as the request card says it.
            "sentence": rows[0].sentence + " °C",
            "cause": first["affected_expectations"][0]["cause"],
            "revised": False,
            "revision": None,
        }
    ]
    assert first["affected_expectations"][0]["cause"].startswith("the temperature changed from")
    assert first["signature_request_id"] is None

    revised = await call(
        ctx,
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 87},
        reason="A cooler profile again.",
        signature_changes=[
            {
                "kind": "replace",
                "expectation_id": rows[0].id,
                "reason": "The floor was written under the old setpoint.",
                "expectation": {
                    "tier": "important",
                    "kind": "measure",
                    "phase": "Ramp down",
                    "expression": {
                        "channel": "temperature",
                        "op": "min",
                        "window": {"phase": "Ramp down"},
                        "offset_from": "profile_temperature",
                        "compare": {"op": ">=", "value": -2},
                    },
                },
            }
        ],
    )
    assert revised["affected_expectations"][0]["revised"] is True
    assert revised["affected_expectations"][0]["revision"].endswith(
        "at least 2 °C below the setpoint"
    )
    request = await SignatureRequestsRepository(archive.db).get(revised["signature_request_id"])
    assert request is not None and request.draft_id == revised["draft_id"]
    assert "nothing changes until the person approves the draft" in revised["note"]

    refused = await refuse(
        ctx,
        "draft_profile",
        base_version_id=archive.profile_version_id,
        patch={"temperature": 86},
        reason="Again.",
        signature_changes=[
            {"kind": "retire", "expectation_id": 999999, "reason": SAME},
        ],
    )
    assert "does not carry expectation" in refused["detail"] or "is not one of" in refused["detail"]


async def test_the_tool_descriptions_say_you_cannot_change_a_signature_yourself() -> None:
    from gaggiclanker.tools.registry import registry

    spec = next(s for s in registry.specs() if s.name == "request_signature_change")
    assert (
        "You cannot change, retire or put back an expectation yourself; this writes one request "
        "and NOTHING changes until the person presses Approve on its card in this conversation."
    ) in spec.description
