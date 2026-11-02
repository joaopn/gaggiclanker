"""`/api/signature-requests`: reading a conversation's requests, and a person's Approve and Decline.

Approve is check, then write, in one transaction; a request that no longer applies answers 409
`SIGNATURE_REQUEST_STALE` and changes nothing; one already answered, 409
`SIGNATURE_REQUEST_DECIDED`.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.signatures.change_requests import ChangeIn, SignatureRequestService
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import make_recipe_versions, make_thread
from tests.signatures.test_carry import _version
from tests.signatures.test_change_requests import floor, replacing


def _ok(response: httpx.Response) -> Any:
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def _error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    return dict(body["error"])


async def _asked(app: FastAPI, *makers: Any) -> tuple[int, int, int, int]:
    """A version with two expectations in force and a request in a conversation:
    (version, thread, expectation id, request id)."""
    db = app.state.db
    version = await _version(db, LEVER_PROFILE)
    recipe_version, _ = await make_recipe_versions(db, version)
    recipe_id = await SignatureRepository(db).recipe_of_version(recipe_version)
    rows = await SignatureService(db).propose(
        version,
        [
            ExpectationInput(tier="important", kind="measure", phase="soak", expression=floor(93)),
            ExpectationInput(tier="critical", kind="measure", phase="soak", expression=floor(91)),
        ],
        reason="What it is for.",
    )
    thread = await make_thread(db, recipe_id)
    changes: list[ChangeIn] = [m(rows) for m in makers] or [replacing(rows[0].id, floor(91.5))]
    request = await SignatureRequestService(db).request(
        profile_version_id=version,
        changes=changes,
        reason="Every shot sags under the floor.",
        thread_id=thread,
        recipe_version_id=recipe_version,
    )
    return version, thread, rows[0].id, request.id


async def test_a_conversation_reads_its_own_requests_with_what_the_card_needs(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, thread, expectation, request = await _asked(app)

    body = _ok(await client.get("/api/signature-requests", params={"thread_id": thread}))

    (item,) = body["items"]
    assert item["id"] == request and item["status"] == "waiting"
    assert set(item) == {
        "id",
        "status",
        "stale_reason",
        "reason",
        "answer_reason",
        "created_at",
        "answered_at",
        "profile_version_id",
        "profile_label",
        "version_label",
        "thread_id",
        "draft_id",
        "readable",
        "loosens_open_prediction",
        "changes",
    }
    assert item["profile_version_id"] == version and item["version_label"] == "v1"
    (change,) = item["changes"]
    assert change["kind"] == "replace" and change["expectation_id"] == expectation
    assert (
        change["old_limit_text"] == "at least 93 °C"
        and change["new_limit_text"] == "at least 91.5 °C"
    )
    assert change["preview"] == {"shots": 0, "failing_now": 0, "failing_after": 0}
    assert set(change) >= {
        "kind",
        "reason",
        "expectation_id",
        "override_id",
        "tier",
        "phase",
        "old_sentence",
        "new_sentence",
        "old_tier",
        "new_tier",
        "rejected_reason",
        "rejected_at",
        "written_expectation_id",
        "preview",
    }
    # By profile version too, and a conversation with none answers empty.
    by_version = _ok(
        await client.get("/api/signature-requests", params={"profile_version_id": version})
    )
    assert [i["id"] for i in by_version["items"]] == [request]
    other = await make_thread(app.state.db)
    assert (
        _ok(await client.get("/api/signature-requests", params={"thread_id": other}))["items"] == []
    )
    assert (await client.get("/api/signature-requests")).status_code == 422


async def test_approve_writes_and_a_second_press_is_a_409(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, _, expectation, request = await _asked(app)

    body = _ok(await client.post(f"/api/signature-requests/{request}/approve"))

    assert body["request"]["status"] == "approved"
    (change,) = body["request"]["changes"]
    assert change["written_expectation_id"] is not None and change["preview"] is None
    repo = SignatureRepository(app.state.db)
    assert (await repo.get(expectation)).status == "rejected"  # type: ignore[union-attr]
    again = await client.post(f"/api/signature-requests/{request}/approve")
    assert again.status_code == 409 and _error(again)["code"] == "SIGNATURE_REQUEST_DECIDED"
    after_decline = await client.post(f"/api/signature-requests/{request}/decline", json={})
    assert after_decline.status_code == 409
    assert _error(after_decline)["code"] == "SIGNATURE_REQUEST_DECIDED"


async def test_decline_takes_an_optional_reason_and_changes_nothing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, _, expectation, request = await _asked(app)
    body = _ok(
        await client.post(
            f"/api/signature-requests/{request}/decline",
            json={"reason": "  keep it,   the boiler should hold 93 "},
        )
    )
    assert body["request"]["status"] == "declined"
    assert body["request"]["answer_reason"] == "keep it, the boiler should hold 93"
    assert (await SignatureRepository(app.state.db).get(expectation)).status == "confirmed"  # type: ignore[union-attr]
    quiet = _ok(await client.post(f"/api/signature-requests/{(await _asked(app))[3]}/decline"))
    assert quiet["request"]["answer_reason"] == ""


async def test_approving_a_stale_request_is_a_409_that_says_why_and_writes_nothing(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, _, expectation, request = await _asked(app)
    await app.state.db.execute(
        "UPDATE signature_expectations SET status = 'rejected' WHERE id = ?", (expectation,)
    )
    response = await client.post(f"/api/signature-requests/{request}/approve")
    error = _error(response)
    assert response.status_code == 409 and error["code"] == "SIGNATURE_REQUEST_STALE"
    assert "was rejected, so it is not in force" in error["message"]
    listed = _ok(await client.get("/api/signature-requests", params={"thread_id": 1}))
    assert listed["items"][0]["status"] == "stale"
    assert "was rejected, so it is not in force" in listed["items"][0]["stale_reason"]
    assert len(await SignatureRepository(app.state.db).for_version(_version_of(listed))) == 2


def _version_of(listed: dict[str, Any]) -> int:
    return int(listed["items"][0]["profile_version_id"])


async def test_the_person_s_own_press_on_the_profiles_page_stales_the_card(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, thread, expectation, request = await _asked(app)
    assert (
        await client.post(
            f"/api/signature-expectations/{expectation}/reject", json={"reason": "no"}
        )
    ).status_code == 200
    item = _ok(await client.get("/api/signature-requests", params={"thread_id": thread}))["items"][
        0
    ]
    assert item["status"] == "stale"
    assert (
        item["stale_reason"].startswith("You rejected")
        and "on the Profiles page" in item["stale_reason"]
    )
    # A request the cause staled answers with its own code and the cause, never "answered".
    for answer in ("approve", "decline"):
        response = await client.post(f"/api/signature-requests/{request}/{answer}")
        error = _error(response)
        assert response.status_code == 409 and error["code"] == "SIGNATURE_REQUEST_STALE", answer
        assert item["stale_reason"] in error["message"] and "answered" not in error["message"]


async def test_a_request_that_was_answered_is_decided_not_stale(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, _, _, request = await _asked(app)
    _ok(await client.post(f"/api/signature-requests/{request}/approve"))
    for answer in ("approve", "decline"):
        response = await client.post(f"/api/signature-requests/{request}/{answer}")
        assert response.status_code == 409
        assert _error(response)["code"] == "SIGNATURE_REQUEST_DECIDED"


async def test_an_unknown_request_is_a_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/signature-requests/999/approve")).status_code == 404
    assert (await client.post("/api/signature-requests/999/decline", json={})).status_code == 404


async def test_the_answer_reaches_the_agent_in_the_next_turns_context(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    from gaggiclanker.signatures.lines import signature_answers_block

    _, thread, expectation, request = await _asked(app)
    waiting = await signature_answers_block(app.state.db, thread)
    assert waiting[0] == "SIGNATURE CHANGES YOU REQUESTED IN THIS CONVERSATION"
    assert waiting[1].endswith("(waiting: nothing changes until the person presses Approve)")
    await client.post(f"/api/signature-requests/{request}/approve")
    block = await signature_answers_block(app.state.db, thread)
    line = block[block.index("SIGNATURE CHANGES YOU REQUESTED IN THIS CONVERSATION") + 1]
    assert line.startswith(f"- request #{request}: replace #{expectation} ")
    assert line.endswith(")") and "approved: in force now as #" in line
