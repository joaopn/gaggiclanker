"""A profile has one file for good: what a sync does around saving over it.

The save itself and its guard are tested where they live (``test_device_writes``,
``test_machine``); this is what the write phase adds. The star, the selection and the startup
setting stay because the firmware keeps them for an existing id; a shot is linked to the
version its file held when the shot was read, so the shots are read before anything is saved
over a file; and nothing is saved over a file when that read did not complete.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import BASE_LABEL
from tests.drafts.helpers import Live, audit, kinds, same_name_draft
from tests.drafts.test_board import (
    adopted,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]

type Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

#: The profile id the fake's first shot was brewed with (its `.slog` header names it).
BREWED_WITH = "pPdHFJ0HBq"
BREWED_SHOT = "000196"


async def shot_version(app: FastAPI, device_id: str = BREWED_SHOT) -> int | None:
    value = await app.state.db.fetch_value(
        "SELECT profile_version_id FROM shots WHERE device_id = ?", (device_id,)
    )
    return None if value is None else int(value)


async def adopted_with_shot_profile(
    writes_on: Live, fake_device: FakeDevice
) -> tuple[FastAPI, httpx.AsyncClient, dict[str, Any]]:
    """The person's profile stands on the id the fake's first shot was brewed with, and no shot
    has been pulled yet."""
    app, client = writes_on
    for profile in fake_device.profiles:
        if profile["label"] == BASE_LABEL:
            profile["id"] = BREWED_WITH
    run = await pull(app)  # adoption
    assert run.status == "ok", run.error
    assert await shot_version(app) is None, "no shot has been pulled"
    return app, client, row_for(await get_board(client), BASE_LABEL)


async def test_a_shot_brewed_on_a_version_and_not_pulled_yet_stays_on_that_version(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    version_a = row["row"]["current_version_id"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    after = row_for(await get_board(client), BASE_LABEL)
    version_b = after["row"]["current_version_id"]
    assert version_b != version_a
    assert after["row"]["device_profile_id"] == BREWED_WITH, "the same file, saved over"
    assert write_frames(fake_device) == ["req:profiles:save"]
    assert await shot_version(app) == version_a, "brewed on A, so linked to A"
    # And one brewed after the save is linked to what the file holds then.
    assert await app.state.db.fetch_value("SELECT count(*) FROM shots") == len(fake_device.shots)


async def test_without_the_pull_before_the_save_the_shot_would_follow_the_file(
    writes_on: Live,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test above bites: a write phase that does not read the shots first links the shot to
    the version the file holds by the time it is read."""
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    version_a = row["row"]["current_version_id"]
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))

    async def nothing_pulled(self: Any) -> bool:
        return True

    monkeypatch.setattr(
        type(app.state.connection.engine), "_pull_shots_before_saving", nothing_pulled
    )
    run = await pull(app)
    assert run.status == "ok", run.error
    await app.state.connection.engine.sync_shots(kind="backfill", trigger="test")

    assert await shot_version(app) != version_a


async def test_nothing_is_saved_over_a_file_when_the_shots_could_not_be_pulled(
    writes_on: Live,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    new = await put(client, await variant(app, client))

    async def incomplete(self: Any) -> bool:
        return False

    monkeypatch.setattr(type(app.state.connection.engine), "_pull_shots_before_saving", incomplete)
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert run.status == "error"
    [failure] = summary_of(run)["failures"]
    assert failure["reason"] == "push" and "shots" in failure["detail"]
    assert failure["row_id"] == row["row"]["id"]
    # The new profile has no file to save over, so it is not held back by the shots.
    assert write_frames(fake_device) == ["req:profiles:save"]
    assert [i["label"] for i in summary_of(run)["pushed"]] == [new["label"]]
    assert row_for(await get_board(client), BASE_LABEL)["machine"]["holds_current"] is False


async def variant(app: FastAPI, client: httpx.AsyncClient) -> dict[str, Any]:
    from tests.drafts.helpers import manual_draft

    return await manual_draft(app, client, BASE_LABEL, "A brand new one", 6.5)


async def test_the_shots_are_pulled_once_for_all_the_files_a_sync_saves_over(
    adopted: Adopted,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, _fake = adopted
    engine = app.state.connection.engine
    real = type(engine)._pull_shots_before_saving
    calls: list[int] = []

    async def counted(self: Any) -> bool:
        calls.append(1)
        done: bool = await real(self)
        return done

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", counted)
    rows = [r for r in (await get_board(client))["rows"] if not r["utility"]][:2]
    for entry in rows:
        await put(client, await same_name_draft(app, client, provider, entry["row"]["label"], 7))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert len(summary_of(run)["pushed"]) == 2
    assert calls == [1]


async def test_a_sync_that_saves_nothing_over_a_file_does_not_pull_the_shots_for_it(
    adopted: Adopted, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _client, _fake = adopted
    engine = app.state.connection.engine
    calls: list[int] = []

    async def counted(self: Any) -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", counted)

    run = await pull(app)

    assert run.status == "ok" and summary_of(run)["writes"] == 0
    assert calls == []


async def test_a_save_over_a_file_keeps_its_star_its_selection_and_the_startup_setting(
    adopted: Adopted, provider: FakeProvider
) -> None:
    app, client, fake = adopted
    row = row_for(await get_board(client), BASE_LABEL)
    file = row["machine"]["device_id"]
    starred = await client.put(
        f"/api/profile-board/{row['row']['id']}/starred", json={"starred": True}
    )
    assert starred.status_code == 200, starred.text
    fake.selected_profile_id = file
    fake.device_settings["startupProfile"] = file
    await pull(app)
    assert file in fake.favorite_profile_ids
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake) == ["req:profiles:save"], "no select, favourite or delete"
    assert fake.selected_profile_id == file and file in fake.favorite_profile_ids
    assert fake.device_settings["startupProfile"] == file
    assert kinds(await audit(app), "profile_save")[-1] == ("profile_save", file, "ok")
    assert row_for(await get_board(client), BASE_LABEL)["starred"] is True


# ── shots the pull could not see ────────────────────────────────────


def process_a(engine: Any) -> int | None:
    status = engine.client.last_status
    return None if status is None or status.process is None else int(status.process.a)


async def until(condition: Any) -> None:
    for _ in range(100):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the condition never held")


async def test_a_save_waits_while_the_machine_runs_a_shot_and_the_shot_stays_on_its_version(
    writes_on: Live,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running shot is not in the index yet. Saving now would link it to the new version when it
    ends, so nothing is saved over a file while the machine reports a process running; the skip is
    said on the run, is no failure, and the next sync saves."""
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    version_a = row["row"]["current_version_id"]
    engine = app.state.connection.engine
    fake_device.hidden_from_index.add(196)
    await engine.sync_shots(kind="backfill", trigger="test")
    assert await shot_version(app) is None
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    await fake_device.emit_status(process={"a": 1, "s": "brew", "l": "Infusion", "e": 0})
    await until(lambda: process_a(engine) == 1)
    fake_device.ws_requests.clear()
    pulls: list[int] = []
    real_pull = type(engine)._pull_shots_before_saving

    async def counted(self: Any) -> bool:
        pulls.append(1)
        done: bool = await real_pull(self)
        return done

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", counted)

    run = await pull(app)

    monkeypatch.undo()
    assert run.status == "ok", run.error
    assert write_frames(fake_device) == []
    assert pulls == [], "a running shot is seen before the pull is spent on it"
    [left] = summary_of(run)["left"]
    assert left["reason"] == "shot_running" and summary_of(run)["failures"] == []
    # The shot ends: indexed, announced, the machine idle again.
    fake_device.hidden_from_index.discard(196)
    await fake_device.emit_status(process={"a": 0})
    await fake_device.emit_shot_saved(196)
    await until(lambda: process_a(engine) == 0)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake_device) == ["req:profiles:save"]
    after = row_for(await get_board(client), BASE_LABEL)["row"]["current_version_id"]
    assert after != version_a
    assert await shot_version(app) == version_a


async def test_a_shot_announced_after_the_pull_is_pulled_before_the_save(
    writes_on: Live,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    version_a = row["row"]["current_version_id"]
    engine = app.state.connection.engine
    fake_device.hidden_from_index.add(196)
    await engine.sync_shots(kind="backfill", trigger="test")
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    real_pull = type(engine)._pull_shots_before_saving
    calls: list[int] = []

    async def pull_then_the_shot_ends(self: Any) -> bool:
        done: bool = await real_pull(self)
        calls.append(1)
        if len(calls) == 1:
            fake_device.hidden_from_index.discard(196)
            await fake_device.emit_shot_saved(196)
            await until(lambda: self._announced_since_pull)
        return done

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", pull_then_the_shot_ends)

    run = await pull(app)

    assert run.status == "ok", run.error
    assert len(calls) == 2, "pulled again because a shot was announced since"
    assert await shot_version(app) == version_a
    assert row_for(await get_board(client), BASE_LABEL)["row"]["current_version_id"] != version_a


async def test_a_shot_stored_with_no_link_is_linked_to_the_version_the_file_holds_before_the_save(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    """A shot the pull stores unlinked (it did not parse) must not be linked by a later profile
    pass to the version the save made."""
    app, client, row = await adopted_with_shot_profile(writes_on, fake_device)
    version_a = row["row"]["current_version_id"]
    engine = app.state.connection.engine
    await engine.sync_shots(kind="backfill", trigger="test")
    bad = fake_device.add_shot(990, b"\x00garbage" * 40, timestamp=1_760_000_000)
    bad.entry.profile_id = BREWED_WITH
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))

    run = await pull(app)  # the pull inside the write phase stores it
    assert run.status == "ok", run.error
    await pull(app)  # the next profile pass mirrors the file again

    assert await shot_version(app, "000990") in (None, version_a)


async def test_nothing_is_saved_over_a_file_when_the_machine_serves_no_index_and_shots_are_archived(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    """No index (a rebuild under way) reads nothing and says ok: that is not a complete pull when
    the archive holds shots from this machine."""
    app, client, _row = await adopted_with_shot_profile(writes_on, fake_device)
    engine = app.state.connection.engine
    fake_device.hidden_from_index.add(196)
    await engine.sync_shots(kind="backfill", trigger="test")
    fake_device.hidden_from_index.discard(196)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    fake_device.index_missing = True
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert "req:profiles:save" not in fake_device.ws_requests
    [failure] = summary_of(run)["failures"]
    assert failure["reason"] == "push" and "shots" in failure["detail"]


async def test_a_machine_that_never_recorded_a_shot_has_none_to_mislink(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    """A fresh machine serves no index and the archive holds nothing from it: the save in place
    happens."""
    app, client = writes_on
    fake_device.shots.clear()
    run = await pull(app)  # adoption
    assert run.status == "ok", run.error
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    fake_device.index_missing = True
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake_device) == ["req:profiles:save"]


async def test_nothing_is_saved_over_a_file_when_the_history_is_unavailable(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    from aiohttp import web

    app, client, _row = await adopted_with_shot_profile(writes_on, fake_device)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    real = fake_device._quirk

    def history_503(request: Any, *, history: bool) -> Any:
        if history:
            return web.Response(status=503, text="busy")
        return real(request, history=history)

    fake_device._quirk = history_503  # type: ignore[method-assign]
    fake_device.ws_requests.clear()
    try:
        run = await pull(app)
    finally:
        fake_device._quirk = real  # type: ignore[method-assign]

    assert "req:profiles:save" not in fake_device.ws_requests
    assert summary_of(run)["failures"]
    assert await shot_version(app) is None


async def test_nothing_is_saved_over_a_file_when_a_shot_could_not_be_fetched(
    writes_on: Live, fake_device: FakeDevice, provider: FakeProvider
) -> None:
    """The index is served but a shot's file is not: the pass ended in an error, so it did not
    complete."""
    from aiohttp import web

    app, client, _row = await adopted_with_shot_profile(writes_on, fake_device)
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    real = fake_device._quirk

    def no_slog(request: Any, *, history: bool) -> Any:
        if request.path.endswith(".slog"):
            return web.Response(status=500, text="no")
        return real(request, history=history)

    fake_device._quirk = no_slog  # type: ignore[method-assign]
    fake_device.ws_requests.clear()
    try:
        await pull(app)
    finally:
        fake_device._quirk = real  # type: ignore[method-assign]

    assert "req:profiles:save" not in fake_device.ws_requests


async def test_a_shot_that_starts_after_the_pull_stops_the_save(
    writes_on: Live,
    fake_device: FakeDevice,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The machine is idle at the pull and reports a process running by the time of the save: the
    second look right before the save leaves it for the next sync."""
    app, client, _row = await adopted_with_shot_profile(writes_on, fake_device)
    engine = app.state.connection.engine
    await put(client, await same_name_draft(app, client, provider, BASE_LABEL, 7))
    real_pull = type(engine)._pull_shots_before_saving

    async def pull_then_a_shot_starts(self: Any) -> bool:
        done: bool = await real_pull(self)
        await fake_device.emit_status(process={"a": 1, "s": "brew", "l": "Infusion", "e": 0})
        await until(lambda: process_a(engine) == 1)
        return done

    monkeypatch.setattr(type(engine), "_pull_shots_before_saving", pull_then_a_shot_starts)
    fake_device.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert write_frames(fake_device) == []
    [left] = summary_of(run)["left"]
    assert left["reason"] == "shot_running" and summary_of(run)["failures"] == []
