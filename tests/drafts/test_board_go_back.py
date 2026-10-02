"""Going back to a board profile's previous version: the edit, and what the sync then does.

The old rollback of a pushed draft is gone with the staged box; this is its replacement, and
every scenario ends in the state the *display* is in after the sync, because what must not
drift is the machine: one copy of the profile, holding the version asked for, and the newer
copy off it through the same guards as any replacement.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.device.fake import FakeDevice
from tests.drafts.conftest import (
    BASE_LABEL,
    base_profile,
    base_version_id,
    data,
    error,
    mirror_only,
)
from tests.drafts.helpers import (
    APP_LABEL,
    audit,
    draft_of,
    ids_labelled,
    kinds,
    make_set_on,
    set_device_ids,
)
from tests.drafts.test_board import (
    adopted,
    app_row,
    approve,
    draft_from,
    get_board,
    pull,
    put,
    row_for,
    summary_of,
    write_frames,
)
from tests.llm.conftest import FakeProvider

__all__ = ["adopted"]  # the fixture, re-exported for this module


def pressure_of(fake: FakeDevice, device_id: str) -> float:
    found = next(p for p in fake.profiles if p["id"] == device_id)
    return float(found["phases"][0]["pump"]["pressure"])


async def go_back(client: httpx.AsyncClient, row_id: int) -> httpx.Response:
    return await client.post(f"/api/profile-board/{row_id}/go-back")


async def two_versions(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, provider: FakeProvider
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """An app profile at 8 bar on the machine, then its next version at 7 bar on the machine."""
    v1 = await app_row(app, client, fake, provider, 8)
    second = await draft_from(app, client, provider, v1, 7)
    v2 = await put(client, second)
    assert v2["id"] == v1["id"] and v2["previous_version_id"] == v1["current_version_id"]
    run = await pull(app)
    assert run.status == "ok", run.error
    [only] = ids_labelled(fake, APP_LABEL)
    assert pressure_of(fake, only) == 7
    return v1, v2, second


async def test_going_back_after_a_sync_leaves_one_copy_holding_the_previous_version(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, second = await two_versions(app, client, fake, provider)
    [newer_file] = ids_labelled(fake, APP_LABEL)
    fake.ws_requests.clear()

    went = data(await go_back(client, v1["id"]))

    assert went["current_version_id"] == v1["current_version_id"]
    assert went["previous_version_id"] is None, "one step back, not a history"
    assert write_frames(fake) == [], "going back edits the board; the machine is not touched"
    planned = [a["kind"] for a in row_for(await get_board(client), APP_LABEL)["planned"]]
    assert planned == ["push", "remove"]

    run = await pull(app)

    assert run.status == "ok", run.error
    [only] = ids_labelled(fake, APP_LABEL)
    assert only != newer_file and pressure_of(fake, only) == 8, "v1 is back, as one copy"
    writes = [t for t in write_frames(fake) if t != "req:profiles:favorite"]
    assert writes == ["req:profiles:save", "req:profiles:delete"], "the copy first, then removal"
    assert kinds(await audit(app), "profile_delete")[-1] == ("profile_delete", newer_file, "ok")
    board = await get_board(client)
    assert board["actions"] == []
    back = row_for(board, APP_LABEL)
    assert back["row"]["device_profile_id"] == only
    assert back["row"]["back_from_set_version_id"] is None
    # The record: the draft that made the newer version is discarded and names no file.
    stored = data(await client.get(f"/api/profile-drafts/{second['id']}"))["draft"]
    assert stored["status"] == "discarded" and stored["pushed_device_profile_id"] is None
    # And it is not a loop: another sync changes nothing.
    again = await pull(app)
    assert summary_of(again)["pushed"] == [] and summary_of(again)["removed"] == []


async def test_going_back_before_the_sync_withdraws_the_waiting_version_and_writes_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    [first_file] = ids_labelled(fake, APP_LABEL)
    second = await draft_from(app, client, provider, v1, 7)
    await put(client, second)
    fake.ws_requests.clear()

    assert (await go_back(client, v1["id"])).status_code == 200

    board = await get_board(client)
    assert board["actions"] == [], "the machine already holds exactly v1"
    run = await pull(app)
    assert run.status == "ok" and write_frames(fake) == []
    assert ids_labelled(fake, APP_LABEL) == [first_file]
    stored = data(await client.get(f"/api/profile-drafts/{second['id']}"))["draft"]
    assert stored["status"] == "discarded"


async def test_the_sets_history_names_the_copies_that_are_where_they_say(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    set_id = await make_set_on(client, "Back set", v1["current_version_id"])
    [first_file] = ids_labelled(fake, APP_LABEL)
    # The Set's first version brews the copy that is on the machine (as a push for the Set
    # would have recorded it).
    await app.state.db.execute(
        "UPDATE set_versions SET pushed_device_profile_id = ? WHERE set_id = ?",
        (first_file, set_id),
    )
    start = await set_device_ids(app, set_id)
    assert start == [first_file]
    newer = await draft_from(app, client, provider, v1, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, newer["id"])
    )
    await put(client, newer, set_id=set_id)
    await pull(app)
    [second_file] = ids_labelled(fake, APP_LABEL)
    versions = await set_device_ids(app, set_id)
    assert versions[-1] == second_file and len(versions) == len(start) + 1
    assert first_file not in versions, "the replaced copy is no longer named"
    assert versions[0] is None and versions[-1] == second_file

    assert (await go_back(client, v1["id"])).status_code == 200
    run = await pull(app)

    assert run.status == "ok", run.error
    [third_file] = ids_labelled(fake, APP_LABEL)
    assert third_file not in (first_file, second_file) and pressure_of(fake, third_file) == 8
    after = await set_device_ids(app, set_id)
    # What the old rollback did: the version that named the removed copy names nothing, and
    # the version the replacement had stopped pointing at the copy that was put back.
    assert after == [third_file, None], "the first version names the copy put back again"
    assert row_for(await get_board(client), APP_LABEL)["row"]["back_from_set_version_id"] is None


async def test_a_set_that_recorded_the_newer_version_does_not_keep_its_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The recording Set's current version names the file being left; it is not a reason to stay."""
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    set_id = await make_set_on(client, "Recording set", v1["current_version_id"])
    newer = await draft_from(app, client, provider, v1, 7)
    await app.state.db.execute(
        "UPDATE profile_drafts SET set_id = ? WHERE id = ?", (set_id, newer["id"])
    )
    await put(client, newer, set_id=set_id)
    await pull(app)
    [second_file] = ids_labelled(fake, APP_LABEL)

    left = row_for(await get_board(client), APP_LABEL)["row"]["current_version_id"]
    went = data(await go_back(client, v1["id"]))
    current_set_version = await app.state.db.fetch_value(
        "SELECT id FROM set_versions WHERE set_id = ? ORDER BY version_no DESC LIMIT 1", (set_id,)
    )
    assert current_set_version is not None and went["back_from_set_version_id"] is None
    assert went["back_from_version_id"] == left != v1["current_version_id"]
    view = row_for(await get_board(client), APP_LABEL)
    assert [a["kind"] for a in view["planned"]] == ["push", "remove"], "nothing is kept for the Set"
    await pull(app)

    assert second_file not in [str(p["id"]) for p in fake.profiles]


async def test_a_profile_of_the_persons_cannot_go_back(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, _ = adopted
    person = row_for(await get_board(client), BASE_LABEL)["row"]
    # Even with a version on record (the row's own, as a rigged database would hold).
    await ProfileBoardRepository(app.state.db).update(
        person["id"], BoardRowPatch(previous_version_id=person["current_version_id"])
    )

    refused = await go_back(client, person["id"])

    assert refused.status_code == 409
    assert "profile of yours" in error(refused)["message"]


async def test_a_profile_with_no_earlier_version_cannot_go_back(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)

    refused = await go_back(client, v1["id"])

    assert refused.status_code == 409 and "no earlier version" in error(refused)["message"]
    assert (await go_back(client, 9999)).status_code == 404


async def test_a_deleted_profile_cannot_go_back(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _ = await two_versions(app, client, fake, provider)
    await client.delete(f"/api/profile-board/{v1['id']}")

    assert (await go_back(client, v1["id"])).status_code == 404


async def test_going_back_onto_a_label_another_profile_holds_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    first = await app_row(app, client, fake, provider, 8, name="First")
    second = await app_row(app, client, fake, provider, 7, name="Second")
    # The first profile's previous version was called what the second profile is called now.
    await ProfileBoardRepository(app.state.db).update(
        first["id"], BoardRowPatch(previous_version_id=second["current_version_id"])
    )

    refused = await go_back(client, first["id"])

    assert refused.status_code == 409
    assert "The board already has Second [AI]" in error(refused)["message"]
    assert error(refused)["details"] == {"reason": "duplicate_label"}


async def test_two_go_backs_at_once_make_one_change_and_one_refusal(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _ = await two_versions(app, client, fake, provider)

    responses = await asyncio.gather(*(go_back(client, v1["id"]) for _ in range(4)))

    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    await pull(app)
    [only] = ids_labelled(fake, APP_LABEL)
    assert pressure_of(fake, only) == 8


async def test_a_version_that_does_not_verify_keeps_the_newer_copy_and_the_next_sync_finishes(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _ = await two_versions(app, client, fake, provider)
    [newer_file] = ids_labelled(fake, APP_LABEL)
    await go_back(client, v1["id"])
    fake.mutate_on_save = lambda p: {**p, "temperature": float(p.get("temperature", 90)) + 1}

    failed = await pull(app)

    assert failed.status == "error"
    assert newer_file in ids_labelled(fake, APP_LABEL), "the newer copy stays when v1 did not land"
    fake.mutate_on_save = None
    ok = await pull(app)
    assert ok.status == "ok", ok.error
    [only] = ids_labelled(fake, APP_LABEL)
    assert pressure_of(fake, only) == 8


async def test_putting_a_new_version_after_going_back_can_go_back_again(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _ = await two_versions(app, client, fake, provider)
    await go_back(client, v1["id"])
    await pull(app)
    third = await draft_from(app, client, provider, v1, 6)

    row = await put(client, third)

    assert row["previous_version_id"] == v1["current_version_id"]
    await pull(app)
    assert (await go_back(client, v1["id"])).status_code == 200
    await pull(app)
    [only] = ids_labelled(fake, APP_LABEL)
    assert pressure_of(fake, only) == 8


async def test_a_draft_that_is_approved_but_not_put_is_left_alone_by_going_back(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _ = await two_versions(app, client, fake, provider)
    bystander = await draft_of(app, client, provider, BASE_LABEL, 5)
    await approve(app, bystander)

    await go_back(client, v1["id"])
    await pull(app)

    stored = data(await client.get(f"/api/profile-drafts/{bystander['id']}"))["draft"]
    assert stored["status"] == "approved"


# ── a Set brewing the file being left does not keep it ──


async def recorded_on_set(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, provider: FakeProvider
) -> tuple[dict[str, Any], int, str, list[int]]:
    """v1 on the machine; Set S on v1; v2 put for S and synced: (v1 row, S, F2, S versions)."""
    v1 = await app_row(app, client, fake, provider, 8)
    s = await make_set_on(client, "S", v1["current_version_id"])
    d2 = await draft_from(app, client, provider, v1, 7)
    await app.state.db.execute("UPDATE profile_drafts SET set_id = ? WHERE id = ?", (s, d2["id"]))
    await put(client, d2, set_id=s)
    run = await pull(app)
    assert run.status == "ok", run.error
    [f2] = ids_labelled(fake, APP_LABEL)
    rows = await app.state.db.fetch_all(
        "SELECT id FROM set_versions WHERE set_id = ? ORDER BY version_no", (s,)
    )
    return v1, s, f2, [int(r["id"]) for r in rows]


# ── going back never orphans the newer copy ──


# ── what stays true when something happens during or between syncs ──


async def test_going_back_is_refused_after_a_sync_that_stopped_part_way_and_orphans_nothing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaggiclanker.drafts.board import BoardService

    app, client, fake = adopted
    v1 = await app_row(app, client, fake, provider, 8)
    await put(client, await draft_from(app, client, provider, v1, 7))
    real = BoardService._remove

    async def cut_off(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cut off after the push, before the old copy is removed")

    monkeypatch.setattr(BoardService, "_remove", cut_off)
    await pull(app)
    monkeypatch.setattr(BoardService, "_remove", real)
    assert len(ids_labelled(fake, APP_LABEL)) == 2, "the newer copy is on the machine"
    await mirror_only(app)  # the next pass only mirrors, and learns of the newer copy

    refused = await go_back(client, v1["id"])

    assert refused.status_code == 409
    assert "stopped part-way; sync again, then go back" in error(refused)["message"]
    row = row_for(await get_board(client), APP_LABEL)
    assert row["go_back_blocked"] and "stopped part-way" in row["go_back_blocked"]
    for _ in range(2):
        await pull(app)
    # The next sync finishes the replacement: one copy, the newer version.
    [only] = ids_labelled(fake, APP_LABEL)
    assert pressure_of(fake, only) == 7


async def test_a_later_put_clears_what_a_going_back_left_for_the_sync(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    app, client, fake = adopted
    v1, _, _, _ = await recorded_on_set(app, client, fake, provider)
    went = data(await go_back(client, v1["id"]))
    assert went["back_from_version_id"]

    row = await put(client, await draft_from(app, client, provider, went, 5))

    assert row["back_from_version_id"] is None and row["back_from_set_version_id"] is None
    await pull(app)
    # A Set brewing the v2 copy does not keep it: only the new version is left on the machine.
    assert [pressure_of(fake, i) for i in ids_labelled(fake, APP_LABEL)] == [5]


async def test_every_open_draft_the_page_can_ask_for_gets_a_landing(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], provider: FakeProvider
) -> None:
    """The page lists at most 100 open drafts; the board places 200, so none waits for ever."""
    app, client, _ = adopted
    made = []
    for n in range(105):
        row = await app.state.draft_proposals.create_manual(
            base_version_id=await base_version_id(app, BASE_LABEL),
            document={
                **(await base_profile(app, BASE_LABEL)).model_dump(
                    mode="json", exclude={"annotations", "id"}
                ),
                "label": f"Bulk {n}",
            },
            change_summary="bulk",
        )
        made.append(row.id)
    listed = [d["id"] for d in data(await client.get("/api/profile-drafts?open=true"))["items"]]
    landed = {x["draft_id"] for x in (await get_board(client))["landings"]}

    assert len(listed) == 100 and set(listed) <= landed
