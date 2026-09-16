"""Sending notes against the real firmware: a judgement onto the machine's notes card.

Opt-in, like the rest of this directory: ``scripts/sim.sh test``, or
``uv run pytest -m simulator`` against a simulator already on :8080.

**Why this one and not the storage cleanup.** The simulator boots with an empty `/h/`, so
there is no shot history to clean up and the only shot that exists is the one
this test brews. Deleting that would prove `req:history:delete` reaches the
firmware and nothing else — the interesting half of the cleanup is the eligibility
rule, which is pure and covered offline — so the shot is left alone and the
cleanup path stays a fake-device test. What *cannot* be checked offline is the
notes save: the fake reproduces `saveNotes` from the source, and this is what
checks the source.

Three firmware behaviours are the subject, and every one of them is silent when
it is wrong:

* the id is used **verbatim as the filename**, so an unpadded id writes a file
  nothing ever reads back;
* the document is stored **verbatim** — what we send is what `/h/<id>.json`
  contains, extra keys and all;
* `doseOut` overrides the index's `volume` **only as a non-empty string**, which
  is why `ShotNotes` models every numeric field as one.

The brew is driven from a throwaway socket, for the reason
`tests/simulator/test_e2e.py` explains: `GaggimateClient` may not send
`req:change-mode`, and that restriction is the point rather than an obstacle.
Nothing here deletes anything from the machine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.judgements import JudgementsRepository, JudgementWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.ids import pad6
from gaggiclanker.notes.writeback import writeback_task_name
from gaggiclanker.settings import EnvSettings
from tests.conftest import machine_tasks, running_app, seed_settings
from tests.simulator.test_e2e import (
    INGEST_TIMEOUT_S,
    _simulator_is_up,
    _until,
    data,
    trigger_a_brew,
)

pytestmark = pytest.mark.simulator


@pytest.fixture
async def live(env: EnvSettings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The real app against the real firmware, with device writes on.

    Both the machine's address and the write switch go through the settings
    service, the way the Settings page writes them — so a default that had
    drifted to "on" would not be hidden by the fixture.
    """
    from tests.simulator.test_e2e import SIM_HOST

    if not await _simulator_is_up():
        pytest.skip(f"no simulator on http://{SIM_HOST} — start one with `scripts/sim.sh serve`")
    await seed_settings(env, gaggimateHost=SIM_HOST, gaggimateTimeoutSeconds=15)
    async with running_app(env) as (app, client):
        await app.state.settings_service.apply({"deviceWritesEnabled": True})
        yield app, client


async def test_a_judgement_reaches_the_firmware_s_own_notes_card(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, client = live
    assert await app.state.connection.client.wait_connected(20.0), (
        "the simulator did not accept a connection"
    )

    device_shot_id = await trigger_a_brew()
    assert device_shot_id is not None, "the simulator did not save a shot"

    # Ask for the shot. Nothing comes off the machine unasked, and a write-back
    # needs the archive's own row: it is what proves the verdict is this box's
    # rather than something the machine's notes card was seeded with.
    accepted = await client.post("/api/sync/run", json={"kind": "all"})
    assert accepted.status_code == 202, accepted.text
    shot = await _until(
        lambda: _archived(app, device_shot_id), INGEST_TIMEOUT_S, "the shot to be archived"
    )

    await JudgementsRepository(app.state.db).upsert(
        shot,
        JudgementWrite(
            rating=4,
            balance="balanced",
            dose_in_g=18.0,
            dose_out_g=36.5,
            grind_setting="2.4",
        ),
    )

    # The explicit send, as the Sync page makes it: the selected shot, confirmed.
    # Saving the judgement above sent nothing; this request is what does.
    pending: Any = data(await client.get("/api/device/notes/pending"))
    assert shot in {item["shot_id"] for item in pending["items"]}
    accepted = await client.post("/api/device/notes/push", json={"shot_ids": [shot]})
    assert accepted.status_code == 202, accepted.text
    task = machine_tasks(app).get(writeback_task_name())
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 30.0)

    # Read it back **off the machine**, over HTTP, which is the route the device
    # UI itself uses and the one that proves the file is on the filesystem under
    # the padded name rather than somewhere only the socket can see.
    padded = pad6(device_shot_id)
    stored = await app.state.connection.client.fetch_notes_json(device_shot_id)
    assert stored is not None, f"the firmware stored no /h/{padded}.json"
    document = stored.to_device()
    assert document["id"] == padded
    assert document["rating"] == 4
    # A string, and this is the assertion the whole feature turns on: the
    # firmware ignores `doseOut` for the index volume unless it is one.
    assert document["doseOut"] == "36.5"
    assert isinstance(document["doseOut"], str)
    assert isinstance(document["timestamp"], int)

    # And the index entry the firmware rewrote as a side effect: rating, the
    # HAS_NOTES flag, and volume from that string.
    entry = await _until(
        lambda: _index_entry(app, padded), 30.0, "the index entry to carry the new rating"
    )
    assert entry.rating == 4
    assert entry.has_notes
    assert entry.volume_g == pytest.approx(36.5, abs=0.1)

    # Nothing was deleted, and the API agrees the verdict is no longer pending.
    body: Any = data(await client.get("/api/device/notes/pending"))
    assert shot not in {item["shot_id"] for item in body["items"]}


async def _archived(app: FastAPI, device_shot_id: int) -> int | None:
    row = await ShotsRepository(app.state.db).get_by_device_id(pad6(device_shot_id))
    return row.id if row is not None else None


async def _index_entry(app: FastAPI, padded: str) -> Any:
    index = await app.state.connection.client.fetch_index()
    if index is None:
        return None
    for entry in index.entries:
        if pad6(entry.id) == padded and entry.rating:
            return entry
    return None
