"""The machine's own profiles and its ten stars (firmware 1.10), on the profile list.

Firmware 1.10 seeds a backflush (`flush`) and a descale (`descale`) on every boot, refuses to
delete them, and refuses a favourite past ten. The list shows the two as always on the machine,
and never sends a star it knows the machine would refuse. Each rule that something is refused or
left out is written in two halves: with the rule taken away the thing is there, and with it the
thing is not.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from gaggiclanker.db.repos.profile_board import BoardRowPatch, ProfileBoardRepository
from gaggiclanker.db.repos.recipes import RecipesRepository
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.models import document_hash
from gaggiclanker.drafts import board as board_module
from gaggiclanker.drafts import board_plan
from gaggiclanker.drafts.machine import KEPT_BY_MACHINE, read_machine, remove_profile
from tests.drafts.conftest import BASE_LABEL, data, error
from tests.drafts.helpers import make_recipe_on
from tests.drafts.test_board import adopted, get_board, pull, row_for, summary_of

__all__ = ["adopted"]

SYSTEM = ("flush", "descale")
KEPT_SENTENCE = "The machine keeps its own {name} profile; it cannot be switched off."
STAR_SENTENCE = "The machine shows at most 10 starred profiles. Take a star off another first."


def system_rows(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in board["rows"] if r["machine"]["device_id"] in SYSTEM]


async def star(client: httpx.AsyncClient, row_id: int, starred: bool) -> httpx.Response:
    return await client.put(f"/api/profile-board/{row_id}/starred", json={"starred": starred})


async def on_machine(client: httpx.AsyncClient, row_id: int, on: bool) -> httpx.Response:
    return await client.put(f"/api/profile-board/{row_id}/on-machine", json={"on": on})


def deletes(fake: FakeDevice) -> list[str]:
    return [t for t in fake.ws_requests if t == "req:profiles:delete"]


def favourites(fake: FakeDevice) -> list[str]:
    return [t for t in fake.ws_requests if t == "req:profiles:favorite"]


async def more_profiles(
    app: FastAPI, client: httpx.AsyncClient, fake: FakeDevice, count: int
) -> list[dict[str, Any]]:
    """Profiles the machine gained after the list was adopted: they join it, on, unstarred."""
    base = next(p for p in fake.profiles if p["id"] == "9bar")
    for i in range(count):
        extra = copy.deepcopy(base)
        extra.update(id=f"extra{i:02d}", label=f"Extra {i:02d}")
        fake.profiles.append(extra)
    assert (await pull(app)).status == "ok"
    return [r for r in (await get_board(client))["rows"] if r["row"]["label"].startswith("Extra")]


# ── the machine's own profiles ───────────────────────────────────────


async def test_the_machines_own_profiles_are_on_the_list_and_kept_on_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    _, client, _ = adopted
    board = await get_board(client)

    assert sorted(r["machine"]["device_id"] for r in system_rows(board)) == sorted(SYSTEM)
    assert all(
        r["kept_by_machine"] and r["on_machine"] and r["utility"] for r in system_rows(board)
    )
    assert not any(r["kept_by_machine"] for r in board["rows"] if r not in system_rows(board))


async def test_switching_one_off_is_refused_with_the_person_s_sentence(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    _, client, _ = adopted
    [flush] = [
        r for r in system_rows(await get_board(client)) if r["machine"]["device_id"] == "flush"
    ]
    # The other half: an ordinary profile's switch is the same request, and is accepted.
    ordinary = row_for(await get_board(client), BASE_LABEL)
    assert (await on_machine(client, ordinary["row"]["id"], False)).status_code == 200

    refused = await on_machine(client, flush["row"]["id"], False)

    assert refused.status_code == 409
    body = error(refused)
    assert body["code"] == "PROFILE_IS_SYSTEM"
    assert body["message"] == KEPT_SENTENCE.format(name=flush["row"]["label"])
    assert row_for(await get_board(client), flush["row"]["label"])["on_machine"] is True
    # Switching it on is not a change and is allowed.
    assert (await on_machine(client, flush["row"]["id"], True)).status_code == 200


async def test_a_system_row_that_an_earlier_sync_left_off_is_switched_on_not_removed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, fake = adopted
    repo = ProfileBoardRepository(app.state.db)
    rows = system_rows(await get_board(client))
    for row in rows:
        await repo.update(row["row"]["id"], BoardRowPatch(on_machine=False))

    # Without the rule the plan takes both off the machine (the firmware would answer
    # `Delete failed`, again at every sync).
    with monkeypatch.context() as unguarded:
        unguarded.setattr(board_plan, "is_system_profile", lambda _id: False)
        unguarded_board = await get_board(client)
    assert sorted(
        a["device_id"] for a in unguarded_board["actions"] if a["kind"] == "remove"
    ) == sorted(SYSTEM)

    # With it nothing is planned for them, the switch reads on, and the sync stores that.
    board = await get_board(client)
    assert not [a for a in board["actions"] if a["device_id"] in SYSTEM]
    assert all(r["on_machine"] for r in system_rows(board))
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert deletes(fake) == []
    assert summary_of(run)["removed"] == [] and summary_of(run)["failures"] == []
    assert {p["id"] for p in fake.profiles} >= set(SYSTEM)
    for row in rows:
        assert (await repo.get(row["row"]["id"])).on_machine is True  # type: ignore[union-attr]


async def test_no_removal_reaches_a_system_file_not_even_a_deleted_rows(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, _, fake = adopted
    client_device = app.state.connection.client
    machine = await read_machine(client_device)
    held = machine.profiles["flush"]

    # Half one: sent straight to the machine, the delete is refused by the firmware.
    with pytest.raises(Exception, match="Delete failed"):
        await client_device.delete_profile("flush", expected_hash=document_hash(held))
    fake.ws_requests.clear()

    # Half two: through the one removal function it is not sent at all.
    removal = await remove_profile(
        client_device,
        device_id="flush",
        expected_hash=document_hash(held),
        successor=None,
        carry_favorite=False,
    )

    assert not removal.removed and removal.reason == KEPT_BY_MACHINE
    assert fake.ws_requests == []


async def test_a_deleted_row_standing_on_a_system_file_leaves_it(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, fake = adopted
    repo = ProfileBoardRepository(app.state.db)
    [flush] = [
        r for r in system_rows(await get_board(client)) if r["machine"]["device_id"] == "flush"
    ]
    await repo.update(flush["row"]["id"], BoardRowPatch(deleted_at="2026-11-20T00:00:00Z"))

    with monkeypatch.context() as unguarded:
        unguarded.setattr(board_plan, "is_system_profile", lambda _id: False)
        planned = await get_board(client)
    assert any(a["kind"] == "remove" and a["device_id"] == "flush" for a in planned["actions"])

    board = await get_board(client)
    assert [
        a for a in board["actions"] if a["device_id"] == "flush" and a["kind"] == "remove"
    ] == []
    fake.ws_requests.clear()
    run = await pull(app)
    assert run.status == "ok", run.error
    assert deletes(fake) == []
    assert "flush" in {p["id"] for p in fake.profiles}


async def test_a_system_profile_is_starred_renamed_and_versioned_like_any_other(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    [flush] = [
        r for r in system_rows(await get_board(client)) if r["machine"]["device_id"] == "flush"
    ]
    rid = flush["row"]["id"]

    assert (await star(client, rid, True)).status_code == 200
    renamed = await client.put(
        f"/api/profile-board/{rid}/name", json={"label": "Backflush (weekly)"}
    )
    assert renamed.status_code == 200
    run = await pull(app)

    assert run.status == "ok", run.error
    assert "flush" in fake.favorite_profile_ids
    assert next(p for p in fake.profiles if p["id"] == "flush")["label"] == "Backflush (weekly)"
    assert next(p for p in fake.profiles if p["id"] == "flush")["utility"] is True
    versions = data(await client.get(f"/api/profile-board/{rid}/versions"))
    assert versions["versions"], "its versions are listed as any profile's are"


async def test_the_descale_seed_is_outside_the_safety_bounds_so_a_change_to_it_is_not_pushed(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """The firmware's own descale has a 0 degree setpoint and 200 s waits, which the policy
    (60-100 degrees, 120 s a phase) refuses for any profile. Starring it needs no save and works;
    a change to it is reported as not pushed, as a version outside the bounds always is."""
    app, client, fake = adopted
    [descale] = [
        r for r in system_rows(await get_board(client)) if r["machine"]["device_id"] == "descale"
    ]
    rid = descale["row"]["id"]
    assert (await star(client, rid, True)).status_code == 200
    assert (await pull(app)).status == "ok"
    assert "descale" in fake.favorite_profile_ids
    renamed = await client.put(
        f"/api/profile-board/{rid}/name", json={"label": "Descale (monthly)"}
    )
    assert renamed.status_code == 200
    fake.ws_requests.clear()

    run = await pull(app)

    assert "req:profiles:save" not in fake.ws_requests
    [failure] = summary_of(run)["failures"]
    assert failure["reason"] == "policy"
    assert next(p for p in fake.profiles if p["id"] == "descale")["label"] == "[Utility] Descale"

    # The person reads why on the Profiles page: the row's report names the bounds and the
    # numbers (temperature 0, 200 s waits). Nothing is sent for it at any later sync either.
    [report] = [r for r in (await get_board(client))["reports"] if r["row_id"] == rid]
    assert report["reason"] == "policy"
    assert "outside the current safety bounds" in report["detail"]
    assert "temperature 0 -> 60" in report["detail"] and "duration 200 -> 120" in report["detail"]
    fake.ws_requests.clear()
    for _ in range(2):
        await pull(app)
    assert "req:profiles:save" not in fake.ws_requests


async def test_a_system_file_the_machine_does_not_hold_gets_no_new_file(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """A save with no id would make a profile with an id of its own, not `flush`; the firmware
    seeds the fixed one again at its next boot."""
    app, _, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] != "flush"]
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    assert "req:profiles:save" not in fake.ws_requests
    assert [p["id"] for p in fake.profiles].count("flush") == 0


async def test_without_the_plans_guard_a_missing_system_file_would_get_a_save(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first half of the test above: with the plan's guard taken out, a save goes out and
    makes a second backflush under an id of its own."""
    app, _, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] != "flush"]
    fake.ws_requests.clear()
    monkeypatch.setattr(board_plan, "is_system_profile", lambda _id: False)

    await pull(app)

    assert "req:profiles:save" in fake.ws_requests


async def test_without_the_executors_guard_a_missing_system_file_would_end_in_a_crash(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row has neither a file that holds its content nor a push: without the executor's own
    guard the sync's "a row holds or has just pushed" assertion fails: the run says it crashed."""
    app, _, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] != "flush"]
    monkeypatch.setattr(board_module, "is_system_profile", lambda _id: False)

    run = await pull(app)

    assert any(f["reason"] == "crashed" for f in summary_of(run)["failures"])


async def test_a_wiped_machine_still_looks_reset_though_it_seeds_its_own_profiles(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every boot puts `flush` and `descale` back under the ids the list holds, so they say
    nothing about a reset: the other files are what the guard looks for."""
    app, client, fake = adopted
    fake.profiles = [p for p in fake.profiles if p["id"] in SYSTEM]

    # Half one: counted as the list's own files, the two make a wiped machine read as in order.
    with monkeypatch.context() as counted:
        counted.setattr(board_plan, "is_system_profile", lambda _id: False)
        assert not (await get_board(client))["paused"]

    # Half two: they are not counted, and the machine is seen to have lost everything else.
    assert (await get_board(client))["paused"]
    run = await pull(app)
    assert "looks reset" in summary_of(run)["paused"]


# ── at most ten stars ────────────────────────────────────────────────


async def starred_ids(client: httpx.AsyncClient) -> list[int]:
    return [r["row"]["id"] for r in (await get_board(client))["rows"] if r["starred"]]


async def test_an_eleventh_star_is_refused_naming_the_limit(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    rows = (await get_board(client))["rows"]
    assert len(rows) >= 12
    for row in rows[:10]:
        assert (await star(client, row["row"]["id"], True)).status_code == 200
    eleventh = rows[10]["row"]["id"]

    # The other half: with the limit out of the way the same request is accepted.
    with monkeypatch.context() as unlimited:
        unlimited.setattr(board_module, "MAX_STARRED", 99)
        assert (await star(client, eleventh, True)).status_code == 200
        assert (await star(client, eleventh, False)).status_code == 200

    refused = await star(client, eleventh, True)

    assert refused.status_code == 409
    body = error(refused)
    assert (body["code"], body["message"]) == ("STAR_LIMIT", STAR_SENTENCE)
    assert eleventh not in await starred_ids(client)
    # A star already set is not a new one, and taking one off makes room.
    assert (await star(client, rows[0]["row"]["id"], True)).status_code == 200
    assert (await star(client, rows[0]["row"]["id"], False)).status_code == 200
    assert (await star(client, eleventh, True)).status_code == 200


async def test_a_starred_profile_that_is_off_the_machine_does_not_use_up_a_star(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    rows = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    for row in rows[:10]:
        assert (await star(client, row["row"]["id"], True)).status_code == 200
    assert (await star(client, rows[10]["row"]["id"], True)).status_code == 409

    assert (await on_machine(client, rows[0]["row"]["id"], False)).status_code == 200

    assert (await star(client, rows[10]["row"]["id"], True)).status_code == 200


async def test_the_sync_stars_the_first_ten_and_never_sends_an_eleventh(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    repo = ProfileBoardRepository(app.state.db)
    rows = (await get_board(client))["rows"]
    # Old data: twelve stored stars, which the route would not have allowed.
    for row in rows[:12]:
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))
    fake.favorite_profile_ids.clear()
    fake.ws_requests.clear()

    # Half one: the cap taken out of the plan, all twelve are sent and the machine answers the
    # last two with `Favorite limit reached`; that is said on the items and is no failure.
    with monkeypatch.context() as uncapped:
        uncapped.setattr(board_plan, "MAX_STARRED", 99)
        run = await pull(app)
    assert len(favourites(fake)) == 12
    assert run.status == "ok", run.error
    assert len(summary_of(run)["failures"]) == 0
    assert sorted(i["reason"] for i in summary_of(run)["left"]) == ["star_limit", "star_limit"]
    assert len(fake.favorite_profile_ids) == 10

    # Half two: with the cap, ten stars go out and the other two are left, in the person's words.
    fake.favorite_profile_ids.clear()
    fake.ws_requests.clear()
    run = await pull(app)

    assert run.status == "ok", run.error
    assert len(favourites(fake)) == 10
    assert len(fake.favorite_profile_ids) == 10
    left = [i for i in summary_of(run)["left"] if i["reason"] == "star_limit"]
    assert [i["detail"] for i in left] == [STAR_SENTENCE, STAR_SENTENCE]
    assert {i["row_id"] for i in left} == {r["row"]["id"] for r in rows[10:12]}


async def test_stars_come_off_before_any_goes_on_so_a_full_machine_can_swap_one(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    repo = ProfileBoardRepository(app.state.db)
    rows = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    # The machine holds ten stars on the last ten rows; the list wants the first row starred and
    # the last one not.
    holders = rows[-10:]
    for row in holders:
        fake.favorite_profile_ids.add(row["machine"]["device_id"])
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))
    await repo.update(holders[-1]["row"]["id"], BoardRowPatch(on_home_screen=False))
    await repo.update(rows[0]["row"]["id"], BoardRowPatch(on_home_screen=True))
    fake.ws_requests.clear()

    run = await pull(app)

    assert run.status == "ok", run.error
    stars = [
        t for t in fake.ws_requests if t in ("req:profiles:favorite", "req:profiles:unfavorite")
    ]
    assert stars == ["req:profiles:unfavorite", "req:profiles:favorite"]
    assert rows[0]["machine"]["device_id"] in fake.favorite_profile_ids
    assert holders[-1]["machine"]["device_id"] not in fake.favorite_profile_ids
    assert summary_of(run)["failures"] == []


async def test_a_refused_star_is_the_machines_word_not_a_stop(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Three refused stars in a row must not read as a machine that has gone: the rest of the
    sync (a push after them, here) still happens."""
    app, client, fake = adopted
    # Stars the list does not count: the machine already holds ten of them, on profiles the list
    # has not seen (so the plan's own cap cannot know).
    fake.favorite_profile_ids = {f"unseen{i}" for i in range(10)}
    ordinary = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    repo = ProfileBoardRepository(app.state.db)
    for row in ordinary[:4]:
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))

    run = await pull(app)

    assert run.status == "ok", run.error
    assert sorted(i["reason"] for i in summary_of(run)["left"]) == ["star_limit"] * 4
    assert summary_of(run)["failures"] == []


async def test_a_star_past_the_tenth_comes_off_the_machine_in_one_sync_and_nothing_is_refused(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The machine already stars the row past the tenth (a path the route allows: see below)."""
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    repo = ProfileBoardRepository(app.state.db)
    rows = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    for row in rows[:12]:
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))
    ids_of = [r["machine"]["device_id"] for r in rows]
    # Nine of the first ten and the eleventh are starred on the machine: ten stars, one of them
    # on the row past the tenth.
    fake.favorite_profile_ids = set(ids_of[:9]) | {ids_of[10]}
    fake.ws_requests.clear()

    # Half one: the round-two plan left the eleventh's star alone, so the first ten's last star
    # was refused by the full machine at every sync.
    with monkeypatch.context() as left_alone:
        left_alone.setattr(
            board_plan.PlanBuilder, "_wants_star", lambda _self, row, over: row.on_home_screen
        )
        await pull(app)
    assert ids_of[10] in fake.favorite_profile_ids and ids_of[9] not in fake.favorite_profile_ids

    fake.ws_requests.clear()
    run = await pull(app)

    assert run.status == "ok", run.error
    assert fake.favorite_profile_ids == set(ids_of[:10])
    assert summary_of(run)["failures"] == []
    assert [t for t in fake.ws_requests if "favorite" in t] == [
        "req:profiles:unfavorite",
        "req:profiles:favorite",
    ]
    fake.ws_requests.clear()
    await pull(app)
    assert [t for t in fake.ws_requests if "favorite" in t] == []


async def test_the_path_that_left_an_eleventh_star_on_the_machine_ends_with_the_first_ten(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    """Ten stars applied; the lowest row off and synced; an eleventh star (nine count, so the route
    allows it) synced; the first row back on. The sync then holds the first ten and refuses
    nothing, however many times it runs."""
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    rows = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    ids_of = [r["row"]["id"] for r in rows]
    for rid in ids_of[:10]:
        assert (await star(client, rid, True)).status_code == 200
    assert (await pull(app)).status == "ok"
    assert len(fake.favorite_profile_ids) == 10

    assert (await on_machine(client, ids_of[0], False)).status_code == 200
    assert (await pull(app)).status == "ok"
    assert (await star(client, ids_of[10], True)).status_code == 200
    assert (await pull(app)).status == "ok"
    assert len(fake.favorite_profile_ids) == 10

    assert (await on_machine(client, ids_of[0], True)).status_code == 200
    for _ in range(3):
        fake.ws_requests.clear()
        run = await pull(app)
        assert run.status == "ok", run.error
        assert summary_of(run)["failures"] == []

    board = await get_board(client)
    on_machine_files = {
        r["row"]["id"]: r["machine"]["device_id"] for r in board["rows"] if r["machine"]["present"]
    }
    assert fake.favorite_profile_ids == {on_machine_files[rid] for rid in ids_of[:10]}
    assert on_machine_files[ids_of[10]] not in fake.favorite_profile_ids
    assert [t for t in fake.ws_requests if "favorite" in t] == []


async def test_a_row_past_the_tenth_star_says_so_on_the_page(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    await more_profiles(app, client, fake, 3)
    repo = ProfileBoardRepository(app.state.db)
    rows = [r for r in (await get_board(client))["rows"] if not r["kept_by_machine"]]
    for row in rows[:11]:
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))

    board = await get_board(client)

    over = next(r for r in board["rows"] if r["row"]["id"] == rows[10]["row"]["id"])
    [line] = [a for a in over["planned"] if a["reason"] == "star_limit"]
    assert line["detail"] == STAR_SENTENCE
    inside = next(r for r in board["rows"] if r["row"]["id"] == rows[9]["row"]["id"])
    assert not [a for a in inside["planned"] if a["reason"] == "star_limit"]
    # Nothing is written for it: it is not among the sync's actions.
    assert not [a for a in board["actions"] if a["reason"] == "star_limit"]


# ── a Recipe on one of the machine's own profiles ────────────────────


async def test_archive_does_not_say_a_system_profile_leaves_the_machine(
    adopted: tuple[FastAPI, httpx.AsyncClient, FakeDevice],
) -> None:
    app, client, fake = adopted
    board = await get_board(client)
    [flush] = [r for r in system_rows(board) if r["machine"]["device_id"] == "flush"]
    ordinary = row_for(board, BASE_LABEL)
    on_flush = await make_recipe_on(client, "Backflush recipe", flush["row"]["current_version_id"])
    on_ordinary = await make_recipe_on(
        client, "Ordinary recipe", ordinary["row"]["current_version_id"]
    )

    standing = data(await client.get(f"/api/recipes/{on_flush}"))["profile"]
    other = data(await client.get(f"/api/recipes/{on_ordinary}"))["profile"]

    # The other half: an ordinary profile is on, not kept, and is what Archive takes off.
    assert (other["on_machine"], other["kept_by_machine"]) == (True, False)
    assert (standing["on_machine"], standing["kept_by_machine"]) == (True, True)

    await RecipesRepository(app.state.db).archive(on_flush)
    fake.ws_requests.clear()
    run = await pull(app)

    assert run.status == "ok", run.error
    assert "flush" in {p["id"] for p in fake.profiles}
    assert [t for t in fake.ws_requests if t == "req:profiles:delete"] == []
    assert next(
        r for r in system_rows(await get_board(client)) if r["machine"]["device_id"] == "flush"
    )["on_machine"]


async def test_a_row_past_the_tenth_star_that_the_policy_refuses_still_loses_its_star(
    writes_on: Any, fake_device: FakeDevice
) -> None:
    """The machine's descale joins the list late (a high row id, so past the tenth), holds a star,
    and is renamed in the app: the policy refuses it, which ends its planning. Its star must come
    off all the same, or the full machine refuses a first-ten row's star at every sync."""
    app, client = writes_on
    fake = fake_device
    # The list is adopted while the machine holds none of its own profiles; they join afterwards.
    fake.profiles = [p for p in fake.profiles if p["id"] not in SYSTEM]
    base = next(p for p in fake.profiles if p["id"] == "9bar")
    for i in range(4):  # enough profiles for ten stars before the descale
        fake.profiles.append(
            copy.deepcopy(base) | {"id": f"extra{i:02d}", "label": f"Extra {i:02d}"}
        )
    assert (await pull(app)).status == "ok"
    fake.ensure_system_profiles()
    assert (await pull(app)).status == "ok"
    repo = ProfileBoardRepository(app.state.db)
    board = await get_board(client)
    ordinary = [r for r in board["rows"] if not r["kept_by_machine"]]
    [descale] = [r for r in system_rows(board) if r["machine"]["device_id"] == "descale"]
    assert descale["row"]["id"] > ordinary[-1]["row"]["id"], "it joined last"
    for row in ordinary[:10]:
        await repo.update(row["row"]["id"], BoardRowPatch(on_home_screen=True))
    await repo.update(descale["row"]["id"], BoardRowPatch(on_home_screen=True))
    renamed = await client.put(
        f"/api/profile-board/{descale['row']['id']}/name", json={"label": "Descale (monthly)"}
    )
    assert renamed.status_code == 200
    files = [r["machine"]["device_id"] for r in ordinary[:10]]
    # The machine stars nine of the first ten and the descale: ten, a full machine.
    fake.favorite_profile_ids = set(files[:9]) | {"descale"}
    fake.ws_requests.clear()

    run = await pull(app)

    # The run is an error for the policy's refusal of the descale, and for nothing else.
    assert [f["reason"] for f in summary_of(run)["failures"]] == ["policy"]
    assert fake.favorite_profile_ids == set(files)
    assert next(p for p in fake.profiles if p["id"] == "descale")["label"] == "[Utility] Descale"
    fake.ws_requests.clear()
    await pull(app)
    assert [t for t in fake.ws_requests if "favorite" in t] == []
