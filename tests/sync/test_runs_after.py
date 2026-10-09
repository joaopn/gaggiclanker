"""`GET /api/sync/runs?after=`: every run since one the caller knows, for a wait that covered
several passes (the status ledger keeps only the newest run of each kind)."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.api.sync import RUNS_AFTER_LIMIT
from gaggiclanker.db.repos.sync import SyncRepository, SyncRunUpdate


async def _read(client: httpx.AsyncClient, after: int) -> dict[str, Any]:
    response = await client.get("/api/sync/runs", params={"after": after})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def _pass(repo: SyncRepository, kind: str, **update: Any) -> int:
    run_id = await repo.start_run(kind, "manual")
    await repo.finish_run(run_id, SyncRunUpdate(**update))
    return run_id


async def test_two_passes_come_back_oldest_first_with_what_the_toast_reads(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    repo = SyncRepository(app.state.db)
    known = await _pass(repo, "backfill")
    first_shots = await _pass(repo, "backfill", shots_inserted=1)
    first_profiles = await _pass(repo, "profiles", summary={"profiles_read": 9, "writes": 1})
    second_shots = await _pass(repo, "backfill", shots_inserted=2)
    second_profiles = await _pass(repo, "profiles", error="machine gone", errors=1)

    data = await _read(client, known)

    assert [run["id"] for run in data["runs"]] == [
        first_shots,
        first_profiles,
        second_shots,
        second_profiles,
    ]
    assert data["truncated"] is False
    by_id = {run["id"]: run for run in data["runs"]}
    assert by_id[first_shots]["shots_inserted"] == 1 and by_id[second_shots]["shots_inserted"] == 2
    assert by_id[first_profiles]["summary"] == {"profiles_read": 9, "writes": 1}
    assert by_id[second_profiles]["status"] == "error"
    assert by_id[second_profiles]["error"] == "machine gone"
    assert all(run["finished_at"] for run in data["runs"])


async def test_an_after_beyond_the_newest_run_is_an_empty_list(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    repo = SyncRepository(app.state.db)
    newest = await _pass(repo, "backfill")

    assert await _read(client, newest) == {"runs": [], "truncated": False}
    assert await _read(client, newest + 1000) == {"runs": [], "truncated": False}


async def test_a_running_pass_is_listed_unfinished(app: FastAPI, client: httpx.AsyncClient) -> None:
    repo = SyncRepository(app.state.db)
    running = await repo.start_run("profiles", "manual")

    [only] = (await _read(client, 0))["runs"]

    assert only["id"] == running and only["finished_at"] is None


async def test_it_is_capped_and_says_so(app: FastAPI, client: httpx.AsyncClient) -> None:
    repo = SyncRepository(app.state.db)
    ids = [await repo.start_run("backfill", "manual") for _ in range(RUNS_AFTER_LIMIT + 3)]

    data = await _read(client, 0)

    assert [run["id"] for run in data["runs"]] == ids[:RUNS_AFTER_LIMIT]
    assert data["truncated"] is True


async def test_it_writes_nothing_and_takes_no_other_method(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    repo = SyncRepository(app.state.db)
    await _pass(repo, "backfill")
    before = await app.state.db.fetch_value("SELECT total_changes()")

    await _read(client, 0)

    assert await app.state.db.fetch_value("SELECT total_changes()") == before
    assert (await client.post("/api/sync/runs")).status_code in (404, 405)
