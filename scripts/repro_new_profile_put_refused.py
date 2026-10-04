#!/usr/bin/env python
"""Reproduce: a renamed draft that moves a stop is listed as a new profile, then refused.

    uv run python scripts/repro_new_profile_put_refused.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

A proposal made from an existing profile but under another label lands as a new profile, so
the Profiles page draws it as a new-profile row, which has no stop-condition checkbox. Putting
it on the board was still refused (409) until the request carried ``acknowledge_stop_changes``,
which that row cannot send: a dead end. Making a draft active takes no confirmation now, so
the plain request with only ``draft_id`` must be accepted.

Through the real app: the draft route (base stop 36 -> 45, label "Other"), the board listing
(the draft is a proposal with no row) and ``POST /api/profile-board``.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.device.fake import build_fake_device
from gaggiclanker.main import create_app
from gaggiclanker.settings import EnvSettings

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BASE_LABEL = "9 Bar Espresso"


def data(response: httpx.Response) -> Any:
    body = response.json()
    if not body.get("ok"):
        print(f"setup: {response.request.url.path} answered {response.status_code}: {body}")
        raise SystemExit(2)  # 1 is the bug itself
    return body["data"]


async def main() -> int:
    fake = build_fake_device(FIXTURES)
    await fake.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            env = EnvSettings(DATA_DIR=str(root / "data"), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
            app = create_app(env, web_dist=root / "no-dist")
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    return await scenario(app, client, fake.address)
    finally:
        await fake.stop()


async def scenario(app: Any, client: httpx.AsyncClient, address: str) -> int:
    data(
        await client.patch(
            "/api/settings", json={"gaggimateHost": address, "deviceWritesEnabled": True}
        )
    )
    if not await app.state.connection.client.wait_connected(5.0):
        print("setup: the fake machine did not connect")
        return 2
    run = await app.state.connection.engine.sync_profiles(trigger="repro")
    if run.status != "ok":
        print(f"setup: the first pull failed: {run.error}")
        return 2  # the first pull adopts the machine's profiles onto the board

    version = await ProfilesRepository(app.state.db).find_version_by_label(BASE_LABEL)
    assert version is not None
    document = dict(version.profile)
    document["label"] = "Other"
    document["phases"] = [dict(p) for p in document["phases"]]
    document["phases"][-1]["targets"] = [{"type": "volumetric", "operator": "gte", "value": 45}]
    base_targets = version.profile["phases"][-1].get("targets")
    draft = data(
        await client.post(
            "/api/profile-drafts", json={"base_version_id": version.id, "profile": document}
        )
    )
    if not draft.get("stop_condition_changes"):
        print(f"setup: the draft moves no stop condition (the base has {base_targets!r})")
        return 2
    board = data(await client.get("/api/profile-board"))
    listed = [p for p in board["proposals"] if p["draft"]["id"] == draft["id"]]
    if len(listed) != 1 or listed[0]["row_id"] is not None:
        print(f"setup: expected one new-profile proposal, got {listed!r}")
        return 2
    response = await client.post("/api/profile-board", json={"draft_id": draft["id"]})
    if response.status_code == 201:
        print("OK: the draft was put on the board with no acknowledgement")
        return 0
    print(f"BUG: put answered {response.status_code}: {response.json()['error']}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
