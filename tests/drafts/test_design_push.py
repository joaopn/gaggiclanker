"""A draft pushed for a Set that is still being designed.

The push is one of the paths a person can end a design by hand: the pushed
profile's version is written onto the Set's empty version 1, never appended as
a v2 after it. And when that version 1 can no longer be filled, the push is
refused before anything reaches the machine.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.sets import DesignBrief, SetRow, SetsRepository, SetWrite
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import base_profile, base_version_id, data, error
from tests.drafts.test_api import lower_pressure
from tests.sets.conftest import make_shot


async def _designed(app: FastAPI) -> SetRow:
    db = app.state.db
    bean = await BeansRepository(db).create(BeanWrite(name="Kenya AA", roast_level="light"))
    grinder = await GrindersRepository(db).create(GrinderWrite(name="Niche Zero"))
    return await SetsRepository(db).create_design(
        SetWrite(name="Kenya AA on the Niche Zero", bean_id=bean.id, grinder_id=grinder.id),
        DesignBrief(),
    )


async def _approved_draft(app: FastAPI, client: httpx.AsyncClient) -> int:
    profile = await base_profile(app)
    row = await app.state.draft_proposals.create_manual(
        base_version_id=await base_version_id(app),
        document=lower_pressure(profile, 8.0),
        change_summary="Down to 8 bar.",
    )
    data(await client.post(f"/api/profile-drafts/{row.id}/approve", json={}))
    return int(row.id)


async def test_a_push_for_a_designed_set_fills_its_version_1(
    writes_on: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = writes_on
    designed = await _designed(app)
    draft_id = await _approved_draft(app, client)

    body = data(
        await client.post(f"/api/profile-drafts/{draft_id}/push", json={"set_id": designed.id})
    )

    version = body["set_version"]
    assert (version["id"], version["version_no"]) == (designed.current_version_id, 1)
    assert version["profile_version_id"] == body["draft"]["draft_version_id"]
    assert version["pushed_device_profile_id"] == body["draft"]["pushed_device_profile_id"]
    after = await SetsRepository(app.state.db).get(designed.id)
    assert after is not None
    assert (after.designing, after.version_count) == (False, 1)


async def test_a_push_for_a_design_that_cannot_be_filled_never_reaches_the_machine(
    writes_on: tuple[FastAPI, httpx.AsyncClient], fake_device: FakeDevice
) -> None:
    app, client = writes_on
    designed = await _designed(app)
    assert designed.current_version_id is not None
    shot = await make_shot(app.state.db, "000301")
    await SetsRepository(app.state.db).assign_shot(shot, designed.current_version_id)
    draft_id = await _approved_draft(app, client)
    before = len(fake_device.profiles)

    response = await client.post(
        f"/api/profile-drafts/{draft_id}/push", json={"set_id": designed.id}
    )

    assert response.status_code == 409
    assert error(response)["code"] == "DESIGN_HAS_SHOTS"
    assert len(fake_device.profiles) == before
    assert "req:profiles:save" not in fake_device.ws_requests
    draft = data(await client.get(f"/api/profile-drafts/{draft_id}"))["draft"]
    assert draft["status"] == "approved"
