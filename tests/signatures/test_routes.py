"""The routes a person presses: read a signature, reject or restore what is in force."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput
from gaggiclanker.signatures.service import SignatureService
from tests.lever_shot import LEVER_PROFILE
from tests.signatures.helpers import make_set_versions
from tests.signatures.test_carry import RAMP_CUP, _version


def _ok(response: httpx.Response) -> Any:
    body = response.json()
    assert body["ok"] is True, body
    return body["data"]


def _error(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert body["ok"] is False, body
    return dict(body["error"])


async def _in_force(app: FastAPI) -> tuple[int, list[int]]:
    version = await _version(app.state.db, LEVER_PROFILE)
    rows = await SignatureService(app.state.db).propose(
        version,
        [
            ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
            ExpectationInput(tier="important", kind="reached", phase="decline"),
            ExpectationInput(
                tier="context", kind="free_text", text="pressure and flow fall", fault="unstable"
            ),
        ],
        reason="what the lever is for",
    )
    return version, [r.id for r in rows]


async def test_the_signature_is_served_with_each_expectation_and_the_sets_that_use_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, ids = await _in_force(app)
    await make_set_versions(app.state.db, version)

    body = _ok(await client.get(f"/api/profile-versions/{version}/signature"))

    assert body["profile_version_id"] == version
    assert body["phases"] == ["preinfusion", "soak", "ramp", "decline"]
    assert (body["confirmed"], body["rejected"]) == (3, 0)
    assert "proposed" not in body
    first = body["expectations"][0]
    assert (first["tier"], first["phase"], first["kind"], first["fault"]) == (
        "critical",
        "ramp",
        "measure",
        "early yield",
    )
    assert first["faults"] == ["early yield"]
    assert first["sentence"].startswith("cup weight at the end of the ramp")
    assert first["expression"]["compare"] == {"op": "<=", "value": 0.15}
    assert first["status"] == "confirmed" and first["needs_a_new_phase"] is False
    assert first["answered_at"] is None
    assert [e["tier"] for e in body["expectations"]] == ["critical", "important", "context"]
    assert [s["set_name"] for s in body["sets"]] == ["Alturas"]
    assert sorted(e["id"] for e in body["expectations"]) == sorted(ids)


async def test_a_profile_with_no_signature_answers_empty_and_a_missing_one_is_a_404(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version = await _version(app.state.db, LEVER_PROFILE)
    body = _ok(await client.get(f"/api/profile-versions/{version}/signature"))
    assert body["expectations"] == [] and body["confirmed"] == 0
    missing = await client.get("/api/profile-versions/9999/signature")
    assert missing.status_code == 404


async def test_reject_restore_and_tier(app: FastAPI, client: httpx.AsyncClient) -> None:
    _, (measure, reached, free) = await _in_force(app)

    moved = _ok(
        await client.post(f"/api/signature-expectations/{free}/tier", json={"tier": "important"})
    )
    assert moved["changed"][0]["tier"] == "important"
    rejected = _ok(
        await client.post(
            f"/api/signature-expectations/{reached}/reject", json={"reason": "  the cup says it \n"}
        )
    )
    assert rejected["changed"][0]["reject_reason"] == "the cup says it"
    assert (rejected["signature"]["confirmed"], rejected["signature"]["rejected"]) == (2, 1)
    # A rejected expectation keeps its tier until it is restored.
    assert (
        await client.post(f"/api/signature-expectations/{reached}/tier", json={"tier": "context"})
    ).status_code == 409

    restored = _ok(await client.post(f"/api/signature-expectations/{reached}/restore"))
    assert restored["changed"][0]["status"] == "confirmed"
    assert restored["changed"][0]["reject_reason"] == ""
    assert (restored["signature"]["confirmed"], restored["signature"]["rejected"]) == (3, 0)
    # The measure was never touched.
    assert restored["signature"]["expectations"][0]["id"] == measure


async def test_the_retired_confirm_routes_are_gone(app: FastAPI, client: httpx.AsyncClient) -> None:
    version, (measure, *_) = await _in_force(app)
    assert (await client.post(f"/api/signature-expectations/{measure}/confirm")).status_code in (
        404,
        405,
    )
    assert (
        await client.post(f"/api/profile-versions/{version}/signature/confirm-all")
    ).status_code in (404, 405)


async def test_a_second_answer_is_a_409_not_a_second_write(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, (measure, *_) = await _in_force(app)
    first, second = await asyncio.gather(
        client.post(f"/api/signature-expectations/{measure}/reject", json={"reason": "a"}),
        client.post(f"/api/signature-expectations/{measure}/reject", json={"reason": "b"}),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert _error(loser)["code"] == "EXPECTATION_REJECTED"
    # Restoring what is in force is refused; so is a second restore of what is restored.
    assert (await client.post(f"/api/signature-expectations/{measure}/restore")).status_code == 200
    again = await client.post(f"/api/signature-expectations/{measure}/restore")
    assert again.status_code == 409 and _error(again)["code"] == "EXPECTATION_NOT_REJECTED"
    assert (
        await client.post("/api/signature-expectations/9999/reject", json={})
    ).status_code == 404
    assert (await client.post("/api/signature-expectations/9999/restore")).status_code == 404


async def test_an_expectation_that_needs_a_phase_can_be_rejected_and_never_restored_over_http(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    import copy

    old, rows = await _in_force(app)
    assert rows
    renamed = copy.deepcopy(LEVER_PROFILE)
    renamed["phases"][2]["name"] = "rise"
    new = await _version(app.state.db, renamed)
    await SignatureService(app.state.db).carry(old, new)

    body = _ok(await client.get(f"/api/profile-versions/{new}/signature"))
    stuck = next(e for e in body["expectations"] if e["needs_a_new_phase"])
    assert stuck["status"] == "proposed"
    assert body["confirmed"] == len(body["expectations"]) - 1
    assert body["expectations"][0]["carried_from_version_id"] == old
    rejected = _ok(await client.post(f"/api/signature-expectations/{stuck['id']}/reject", json={}))
    assert rejected["changed"][0]["status"] == "rejected"
    refused = await client.post(f"/api/signature-expectations/{stuck['id']}/restore")
    assert refused.status_code == 409
    assert _error(refused)["code"] == "NEEDS_A_NEW_PHASE"


async def test_an_override_is_rejected_and_restored_by_the_set_it_belongs_to(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _in_force(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    assert set_id is not None
    proposed = await SignatureService(app.state.db).propose_override(
        set_version_id=first,
        profile_version_id=version,
        expectation_id=measure,
        compare={"op": "<=", "value": 0.2},
        reason="a coarser bean",
        thread_id=None,
    )

    listed = _ok(await client.get(f"/api/sets/{set_id}/versions/{first}/signature-overrides"))
    (item,) = listed["items"]
    assert (item["compare_text"], item["profile_compare_text"]) == ("at most 0.2", "at most 0.15")
    # The limits as a person reads them: a share is a percentage, of what it is a share of.
    assert (item["limit_text"], item["profile_limit_text"]) == (
        "at most 20 % of target",
        "at most 15 % of target",
    )
    assert (item["phase"], item["tier"], item["status"]) == ("ramp", "critical", "confirmed")
    assert item["expectation_status"] == "confirmed"
    assert (
        await client.post(
            f"/api/sets/{set_id + 1}/signature-overrides/{proposed.id}/reject", json={}
        )
    ).status_code == 404

    done = _ok(
        await client.post(
            f"/api/sets/{set_id}/signature-overrides/{proposed.id}/reject", json={"reason": "x"}
        )
    )
    assert done["override"]["status"] == "rejected"
    again = await client.post(
        f"/api/sets/{set_id}/signature-overrides/{proposed.id}/reject", json={}
    )
    assert again.status_code == 409 and _error(again)["code"] == "OVERRIDE_REJECTED"
    assert await SignatureRepository(app.state.db).confirmed_overrides([first]) == {}
    back = _ok(await client.post(f"/api/sets/{set_id}/signature-overrides/{proposed.id}/restore"))
    assert back["override"]["status"] == "confirmed"
    twice = await client.post(f"/api/sets/{set_id}/signature-overrides/{proposed.id}/restore")
    assert twice.status_code == 409 and _error(twice)["code"] == "OVERRIDE_NOT_REJECTED"
    assert (
        await client.get(f"/api/sets/{set_id}/versions/9999/signature-overrides")
    ).status_code == 404


async def test_a_rejected_override_keeps_its_reason(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _in_force(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    proposed = await SignatureService(app.state.db).propose_override(
        set_version_id=first,
        profile_version_id=version,
        expectation_id=measure,
        compare={"op": "<=", "value": 0.2},
        reason="r",
        thread_id=None,
    )
    out = _ok(
        await client.post(
            f"/api/sets/{set_id}/signature-overrides/{proposed.id}/reject",
            json={"reason": "no, keep it tight"},
        )
    )
    assert (out["override"]["status"], out["override"]["reject_reason"]) == (
        "rejected",
        "no, keep it tight",
    )


async def test_a_restore_is_refused_while_another_override_is_in_force_or_its_expectation_is_out(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _in_force(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    assert set_id is not None
    service = SignatureService(app.state.db)

    async def propose(value: float) -> Any:
        return await service.propose_override(
            set_version_id=first,
            profile_version_id=version,
            expectation_id=measure,
            compare={"op": "<=", "value": value},
            reason="r",
            thread_id=None,
        )

    one = await propose(0.2)
    reject = f"/api/sets/{set_id}/signature-overrides/{one.id}/reject"
    restore = f"/api/sets/{set_id}/signature-overrides/{one.id}/restore"
    await client.post(reject, json={})
    two = await propose(0.3)
    refused = await client.post(restore)
    assert refused.status_code == 409 and _error(refused)["code"] == "OVERRIDE_ANOTHER_IN_FORCE"
    await client.post(f"/api/sets/{set_id}/signature-overrides/{two.id}/reject", json={})
    await client.post(f"/api/signature-expectations/{measure}/reject", json={})
    out = await client.post(restore)
    assert out.status_code == 409 and _error(out)["code"] == "EXPECTATION_NOT_CONFIRMED"
    # A replaced override is "withdrawn", listed after the one that replaced it.
    listed = _ok(await client.get(f"/api/sets/{set_id}/versions/{first}/signature-overrides"))
    assert [i["status"] for i in listed["items"]] == ["rejected", "rejected"]


async def test_a_replaced_override_reads_as_withdrawn_and_is_not_a_rejection(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _in_force(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    service = SignatureService(app.state.db)
    for value in (0.2, 0.3):
        await service.propose_override(
            set_version_id=first,
            profile_version_id=version,
            expectation_id=measure,
            compare={"op": "<=", "value": value},
            reason="r",
            thread_id=None,
        )
    listed = _ok(await client.get(f"/api/sets/{set_id}/versions/{first}/signature-overrides"))
    assert [(i["status"], i["reject_reason"]) for i in listed["items"]] == [
        ("confirmed", ""),
        ("withdrawn", ""),
    ]
    withdrawn = listed["items"][1]["id"]
    # Nothing to reject or restore on a replaced one.
    assert (
        await client.post(f"/api/sets/{set_id}/signature-overrides/{withdrawn}/restore")
    ).status_code == 409


async def test_an_override_of_a_limit_in_a_unit_words_both_limits_in_it(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version = await _version(app.state.db, LEVER_PROFILE)
    flow = {
        "channel": "scale_flow",
        "op": "mean",
        "window": {"phase": "ramp"},
        "compare": {"op": "<=", "value": 3.0},
    }
    (row,) = await SignatureService(app.state.db).propose(
        version,
        [ExpectationInput(tier="important", kind="measure", expression=flow)],
        reason="a lever's ramp is gentle",
    )
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    await SignatureService(app.state.db).propose_override(
        set_version_id=first,
        profile_version_id=version,
        expectation_id=row.id,
        compare={"op": "<=", "value": 4.0},
        reason="a coarser bean",
        thread_id=None,
    )

    listed = _ok(await client.get(f"/api/sets/{set_id}/versions/{first}/signature-overrides"))

    (item,) = listed["items"]
    assert (item["limit_text"], item["profile_limit_text"]) == ("at most 4 g/s", "at most 3 g/s")


async def test_a_row_from_before_signatures_were_in_force_is_counted_as_not_in_force(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, reached, _) = await _in_force(app)
    await app.state.db.execute(
        "UPDATE signature_expectations SET status = 'proposed' WHERE id = ?", (reached,)
    )

    body = _ok(await client.get(f"/api/profile-versions/{version}/signature"))

    assert (body["confirmed"], body["not_in_force"], body["rejected"]) == (2, 1, 0)
    old = next(e for e in body["expectations"] if e["id"] == reached)
    assert old["status"] == "proposed" and old["needs_a_new_phase"] is False
    # It can be rejected, never restored into force, has no tier to move, and is no check.
    assert (await client.post(f"/api/signature-expectations/{reached}/restore")).status_code == 409
    moved = await client.post(
        f"/api/signature-expectations/{reached}/tier", json={"tier": "context"}
    )
    assert moved.status_code == 409 and _error(moved)["code"] == "EXPECTATION_NOT_IN_FORCE"
    done = _ok(await client.post(f"/api/signature-expectations/{reached}/reject", json={}))
    assert (done["signature"]["not_in_force"], done["signature"]["rejected"]) == (0, 1)
    # Reject, then Restore, is how an old row is put in force.
    back = _ok(await client.post(f"/api/signature-expectations/{reached}/restore"))
    assert (back["signature"]["confirmed"], back["signature"]["not_in_force"]) == (3, 0)
    assert measure
