"""The routes a person presses: read a signature, answer an expectation, answer an override."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.signature import ExpectationInput, SignatureRefused
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


async def _proposed(app: FastAPI) -> tuple[int, list[int]]:
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
    version, ids = await _proposed(app)
    await make_set_versions(app.state.db, version)

    body = _ok(await client.get(f"/api/profile-versions/{version}/signature"))

    assert body["profile_version_id"] == version
    assert body["phases"] == ["preinfusion", "soak", "ramp", "decline"]
    assert (body["confirmed"], body["proposed"], body["rejected"]) == (0, 3, 0)
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
    assert first["status"] == "proposed" and first["needs_a_new_phase"] is False
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


async def test_confirm_reject_tier_and_confirm_all(app: FastAPI, client: httpx.AsyncClient) -> None:
    version, (measure, reached, free) = await _proposed(app)

    moved = _ok(
        await client.post(f"/api/signature-expectations/{free}/tier", json={"tier": "important"})
    )
    assert moved["changed"][0]["tier"] == "important"
    confirmed = _ok(await client.post(f"/api/signature-expectations/{measure}/confirm"))
    assert confirmed["changed"][0]["status"] == "confirmed"
    assert confirmed["signature"]["confirmed"] == 1
    rejected = _ok(
        await client.post(
            f"/api/signature-expectations/{reached}/reject", json={"reason": "  the cup says it \n"}
        )
    )
    assert rejected["changed"][0]["reject_reason"] == "the cup says it"
    assert rejected["signature"]["rejected"] == 1

    all_ = _ok(await client.post(f"/api/profile-versions/{version}/signature/confirm-all"))
    assert [c["id"] for c in all_["changed"]] == [free]
    assert all_["signature"]["confirmed"] == 2
    # A second press has nothing left to do, and is not an error.
    again = _ok(await client.post(f"/api/profile-versions/{version}/signature/confirm-all"))
    assert again["changed"] == []


async def test_an_answered_expectation_is_a_409_not_a_second_write(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, (measure, *_) = await _proposed(app)
    first, second = await asyncio.gather(
        client.post(f"/api/signature-expectations/{measure}/confirm"),
        client.post(f"/api/signature-expectations/{measure}/confirm"),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert _error(loser)["code"] == "EXPECTATION_ANSWERED"
    # Neither a reject nor a tier change moves a confirmed one.
    assert (
        await client.post(f"/api/signature-expectations/{measure}/reject", json={})
    ).status_code == 409
    assert (
        await client.post(f"/api/signature-expectations/{measure}/tier", json={"tier": "context"})
    ).status_code == 409
    assert (await client.post("/api/signature-expectations/9999/confirm")).status_code == 404


async def test_an_expectation_that_needs_a_phase_cannot_be_confirmed_over_http(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    import copy

    old, rows = await _proposed(app)
    repo = SignatureRepository(app.state.db)
    for row_id in rows:
        await repo.answer(row_id, confirm=True)
    renamed = copy.deepcopy(LEVER_PROFILE)
    renamed["phases"][2]["name"] = "rise"
    new = await _version(app.state.db, renamed)
    await SignatureService(app.state.db).carry(old, new)

    body = _ok(await client.get(f"/api/profile-versions/{new}/signature"))
    stuck = next(e for e in body["expectations"] if e["needs_a_new_phase"])
    refused = await client.post(f"/api/signature-expectations/{stuck['id']}/confirm")
    assert refused.status_code == 409
    assert _error(refused)["code"] == "NEEDS_A_NEW_PHASE"
    assert body["expectations"][0]["carried_from_version_id"] == old


async def test_an_override_is_answered_by_the_set_it_belongs_to(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _proposed(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    assert set_id is not None
    await client.post(f"/api/signature-expectations/{measure}/confirm")
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
    assert (item["phase"], item["tier"], item["status"]) == ("ramp", "critical", "proposed")
    assert (
        await client.post(f"/api/sets/{set_id + 1}/signature-overrides/{proposed.id}/confirm")
    ).status_code == 404

    done = _ok(await client.post(f"/api/sets/{set_id}/signature-overrides/{proposed.id}/confirm"))
    assert done["override"]["status"] == "confirmed"
    again = await client.post(
        f"/api/sets/{set_id}/signature-overrides/{proposed.id}/reject", json={}
    )
    assert again.status_code == 409 and _error(again)["code"] == "OVERRIDE_ANSWERED"
    assert (
        await client.get(f"/api/sets/{set_id}/versions/9999/signature-overrides")
    ).status_code == 404


async def test_a_rejected_override_keeps_its_reason(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _proposed(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    await client.post(f"/api/signature-expectations/{measure}/confirm")
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


async def test_a_confirmed_override_can_be_withdrawn_and_a_new_one_proposed(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    version, (measure, *_) = await _proposed(app)
    first, _ = await make_set_versions(app.state.db, version)
    set_id = await SignatureRepository(app.state.db).set_of_version(first)
    assert set_id is not None
    await client.post(f"/api/signature-expectations/{measure}/confirm")
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

    waiting = await propose(0.2)
    # Only a confirmed one can be withdrawn.
    refused = await client.post(f"/api/sets/{set_id}/signature-overrides/{waiting.id}/withdraw")
    assert refused.status_code == 409 and _error(refused)["code"] == "OVERRIDE_NOT_CONFIRMED"
    await client.post(f"/api/sets/{set_id}/signature-overrides/{waiting.id}/confirm")
    # A confirmed one stands against a proposal ...
    with pytest.raises(SignatureRefused, match="withdraw"):
        await propose(0.3)

    done = _ok(await client.post(f"/api/sets/{set_id}/signature-overrides/{waiting.id}/withdraw"))
    assert done["override"]["status"] == "withdrawn"
    assert (
        await client.post(f"/api/sets/{set_id}/signature-overrides/{waiting.id}/withdraw")
    ).status_code == 409
    assert (
        await client.post(f"/api/sets/{set_id + 1}/signature-overrides/{waiting.id}/withdraw")
    ).status_code == 404
    # ... and once withdrawn, the version reads the profile's limit and a new one can be proposed.
    assert await SignatureRepository(app.state.db).confirmed_overrides([first]) == {}
    again = await propose(0.3)
    assert again.status == "proposed" and again.id != waiting.id
    listed = _ok(await client.get(f"/api/sets/{set_id}/versions/{first}/signature-overrides"))
    assert [i["status"] for i in listed["items"]] == ["proposed", "withdrawn"]


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
    await client.post(f"/api/signature-expectations/{row.id}/confirm")
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
