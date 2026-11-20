"""The fake device's own guarantees.

A fake that drifts from the firmware is worse than no fake: every test above it
keeps passing while the real machine breaks. These are the properties the rest
of the suite silently relies on.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
from websockets.asyncio.client import connect

from gaggiclanker.device.fake import (
    MAX_WS_CLIENTS,
    FakeDevice,
    build_fake_device,
    header_only_bytes,
    main,
    synthetic_slog_bytes,
)
from gaggiclanker.domain.index import parse_index
from gaggiclanker.domain.slog import parse_slog


def test_the_synthetic_shot_is_a_real_slog() -> None:
    """Built through the encoder, so it cannot rot away from the codec."""
    slog = parse_slog(synthetic_slog_bytes(12))
    assert slog.header.version == 8
    assert len(slog.samples) == 12
    assert slog.samples[-1].wp is not None


def test_the_synthetic_shot_says_whether_its_scale_was_on_the_way_firmware_1_10_does() -> None:
    """v8's "active scale is healthy" bit (0x0100) is on every sample of a shot with a scale and
    on none of one without."""
    with_scale = parse_slog(synthetic_slog_bytes(12))
    without = parse_slog(synthetic_slog_bytes(12, scale=False))
    assert all((s.si or 0) & 0x0100 for s in with_scale.samples)
    assert not any((s.si or 0) & 0x0100 for s in without.samples)
    assert all((s.si or 0) & 0x0020 for s in with_scale.samples)  # a brew process


def test_header_only_bytes_is_what_a_half_written_file_looks_like() -> None:
    """Header present, samples not yet flushed, count not yet patched in."""
    full = synthetic_slog_bytes(40)
    partial = header_only_bytes(full)
    assert len(partial) == 512
    assert partial[:4] == b"SHOT"
    assert int.from_bytes(partial[16:20], "little") == 0


def test_the_fake_falls_back_to_a_synthetic_shot_without_fixtures(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`python -m gaggiclanker.device.fake` has to work from an installed wheel."""
    device = build_fake_device(tmp_path / "not-here")
    assert len(device.shots) == 1
    # Nothing but the profiles the firmware seeds on every boot.
    assert [p["id"] for p in device.profiles] == ["flush", "descale"]


def test_the_index_it_serves_round_trips_through_our_parser(
    fake_device: FakeDevice,
) -> None:
    from gaggiclanker.domain.index import encode_index

    parsed = parse_index(encode_index(fake_device.index()))
    assert {e.id for e in parsed.entries} == set(fake_device.shots)


async def test_the_fake_refuses_to_be_written_to(fake_device: FakeDevice) -> None:
    """A test that tries to write device settings must fail loudly.

    `POST /api/settings` on the real machine changes WiFi and PID, and up to
    firmware v1.8.x it cleared every checkbox-style boolean key the body omits
    (v1.9.0 made it a partial update). gaggiclanker never sends one; the fake
    makes sure a future version that does cannot pass its tests.
    """
    async with httpx.AsyncClient(base_url=f"http://{fake_device.address}") as http:
        response = await http.post("/api/settings", data={"mdnsName": "nope"})
    assert response.status_code == 405


async def test_an_unknown_path_gets_the_spa_like_the_real_firmware(
    fake_device: FakeDevice,
) -> None:
    """`serveWebAsset` answers any unrecognised path with index.html.

    The `.slog` route under `/api/history/` keeps its 404 for a file that is
    not there, but a notes file that is not there answers the web page with a
    200: the route is a static mount that declines a missing file, and the
    catch-all serves the SPA. A browser-shaped path is the SPA too, which is
    how a mistyped URL turns into "expected binary, got HTML".
    """
    async with httpx.AsyncClient(base_url=f"http://{fake_device.address}") as http:
        response = await http.get("/shots/42")
    assert response.status_code == 200
    assert response.text.lower().startswith("<!doctype")


def test_the_client_limit_matches_the_firmware_build_flag() -> None:
    """`-DDEFAULT_MAX_WS_CLIENTS=3` — and the machine's own UI is one of them."""
    assert MAX_WS_CLIENTS == 3


def test_the_cli_parses_its_arguments(capsys) -> None:  # type: ignore[no-untyped-def]
    """Only the parsing; actually serving would block for ever."""
    try:
        main(["--help"])
    except SystemExit as exit_code:
        assert exit_code.code == 0
    out = capsys.readouterr().out
    assert "--port" in out
    assert "--host" in out


async def test_a_brew_ends_with_a_saved_shot() -> None:
    """The firmware's own save sequence: frames, then the file, then the event.

    The order is the firmware's and the test pins it: the last thing to happen
    is `evt:history-shot-saved`, *after* the file exists — a client that
    fetched on the stats frame would get a header (report §2.6). Nothing in
    this application acts on that event any more, but the fake still has to
    play it in the right order, because the tests that assert "a pull started
    the moment the machine beeped still finds the shot" depend on the window
    between the event and the index being real.
    """
    device = FakeDevice()
    frames: list[dict[str, object]] = []

    async def capture(frame: dict[str, object]) -> None:
        frames.append(frame)

    device.broadcast = capture  # type: ignore[method-assign]

    await device.run_brew(7)

    active = [f for f in frames if f.get("tp") == "evt:status" and "process" in f]
    assert active, "a brew has to push process frames"
    first_process = cast(dict[str, Any], active[0]["process"])
    assert first_process["a"] == 1
    last_process = cast(dict[str, Any], active[-1]["process"])
    assert last_process["a"] == 0

    # Elapsed only ever goes forwards.
    elapsed = [cast(dict[str, Any], f["process"])["e"] for f in active]
    assert elapsed == sorted(elapsed)

    assert frames[-1] == {"tp": "evt:history-shot-saved", "id": 7}
    assert 7 in device.shots
    saved = device.shots[7]
    assert parse_slog(saved.slog_bytes).header.version == 8
    # The header and the index entry have to name the same profile: the archive
    # reads the first, the device's own list reads the second, and a shot that
    # disagrees with itself looks like a sync bug.
    assert parse_slog(saved.slog_bytes).header.profile_name == saved.entry.profile_name


async def _frame(fake: FakeDevice, tp: str, **payload: Any) -> dict[str, Any]:
    """One request frame on a socket of this test's own, and the machine's answer to it."""
    async with connect(f"ws://{fake.address}/ws") as socket:
        await socket.send(json.dumps({"tp": tp, "rid": "r1", **payload}))
        while True:
            reply = json.loads(await socket.recv())
            if reply.get("rid") == "r1":
                return cast(dict[str, Any], reply)


async def test_a_save_with_an_existing_id_rewrites_that_file_and_keeps_its_star_and_selection() -> (
    None
):
    """`ProfileManager::saveProfile` upserts on the id: only a new one is starred, and the
    selection and the startup setting stay where they were."""
    device = build_fake_device(None)
    device.profiles = [{"id": "aaa", "label": "First", "temperature": 90, "phases": []}]
    device.favorite_profile_ids = {"aaa"}
    device.selected_profile_id = "aaa"
    device.device_settings["startupProfile"] = "aaa"
    await device.start()
    try:
        reply = await _frame(
            device,
            "req:profiles:save",
            profile={"id": "aaa", "label": "Renamed", "temperature": 92, "phases": []},
        )
    finally:
        await device.stop()

    assert reply["profile"]["id"] == "aaa" and reply["profile"]["label"] == "Renamed"
    assert [p["label"] for p in device.profiles] == ["Renamed"]
    assert device.favorite_profile_ids == {"aaa"}
    assert device.selected_profile_id == "aaa"
    assert device.device_settings["startupProfile"] == "aaa"
    assert reply["profile"]["selected"] is True and reply["profile"]["favorite"] is True


async def test_a_save_with_an_unknown_id_creates_that_file_without_starring_it() -> None:
    """The firmware writes `/p/<id>.json` whatever the id; only a generated id is "new"."""
    device = build_fake_device(None)
    device.profiles = [{"id": "aaa", "label": "First", "phases": []}]
    await device.start()
    try:
        reply = await _frame(
            device, "req:profiles:save", profile={"id": "zzz", "label": "Made up", "phases": []}
        )
    finally:
        await device.stop()

    assert reply["profile"]["id"] == "zzz"
    assert sorted(p["id"] for p in device.profiles) == ["aaa", "zzz"]
    assert "zzz" not in device.favorite_profile_ids


async def test_a_save_without_an_id_gets_a_generated_one_and_a_star() -> None:
    device = build_fake_device(None)
    await device.start()
    try:
        reply = await _frame(
            device, "req:profiles:save", profile={"label": "Brand new", "phases": []}
        )
    finally:
        await device.stop()

    assert reply["profile"]["id"] and reply["profile"]["id"] in device.favorite_profile_ids


# ── the status frame names the selected profile ─────────────────────


async def _status(socket: Any) -> dict[str, Any]:
    """The next `evt:status` frame carrying the selected profile (`puid`)."""
    while True:
        frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
        if frame.get("tp") == "evt:status" and "puid" in frame:
            return cast(dict[str, Any], frame)


async def _request(socket: Any, tp: str, **payload: Any) -> dict[str, Any]:
    """Send one request and return its reply, skipping the frames pushed in between."""
    await socket.send(json.dumps({"tp": tp, "rid": "q", **payload}))
    while True:
        frame = json.loads(await asyncio.wait_for(socket.recv(), 3))
        if frame.get("rid") == "q":
            return cast(dict[str, Any], frame)


def _two_profiles() -> FakeDevice:
    device = FakeDevice()
    device.profiles = [
        {"id": "aaa", "label": "First", "temperature": 90, "phases": []},
        {"id": "bbb", "label": "Second", "temperature": 92, "phases": []},
    ]
    return device


async def test_the_status_names_the_profile_a_select_chose() -> None:
    """`publishState` reads `getSelectedProfile()`: `p` and `puid` follow the select, and the
    machine pushes the new state frame by itself, as it does whenever the frame changed."""
    device = _two_profiles()
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            before = await _status(socket)
            assert (before["puid"], before["p"]) == ("", "")
            await socket.send(json.dumps({"tp": "req:profiles:select", "rid": "q", "id": "bbb"}))
            pushed = await _status(socket)
            listed = (await _request(socket, "req:profiles:list"))["profiles"]
    finally:
        await device.stop()

    assert (pushed["puid"], pushed["p"]) == ("bbb", "Second")
    assert [(p["id"], p["selected"]) for p in listed] == [("aaa", False), ("bbb", True)]


async def test_a_client_that_connects_later_is_told_the_selection() -> None:
    device = _two_profiles()
    await device.start()
    try:
        await device.select_profile("aaa")
        async with connect(f"ws://{device.address}/ws") as socket:
            status = await _status(socket)
    finally:
        await device.stop()

    assert (status["puid"], status["p"]) == ("aaa", "First")


async def test_selecting_an_id_with_no_file_is_refused_and_the_selection_stays() -> None:
    """Firmware 1.10: `selectProfile` loads the file first and answers `Profile not found` for
    one it cannot read, instead of storing the id and showing an empty profile."""
    device = _two_profiles()
    await device.start()
    try:
        await device.select_profile("bbb")
        async with connect(f"ws://{device.address}/ws") as socket:
            assert (await _status(socket))["puid"] == "bbb"
            reply = await _request(socket, "req:profiles:select", id="nope")
            listed = (await _request(socket, "req:profiles:list"))["profiles"]
    finally:
        await device.stop()

    assert reply["error"] == "Profile not found"
    assert device.selected_profile_id == "bbb"
    assert [p["id"] for p in listed if p["selected"]] == ["bbb"]


async def test_renaming_the_selected_profile_reaches_the_status() -> None:
    """`saveProfile` reloads the selected profile from its file, so a rename shows."""
    device = _two_profiles()
    await device.start()
    try:
        await device.select_profile("aaa")
        async with connect(f"ws://{device.address}/ws") as socket:
            assert (await _status(socket))["p"] == "First"
            await socket.send(
                json.dumps(
                    {
                        "tp": "req:profiles:save",
                        "rid": "q",
                        "profile": {"id": "aaa", "label": "Renamed", "phases": []},
                    }
                )
            )
            status = await _status(socket)
    finally:
        await device.stop()

    assert (status["puid"], status["p"]) == ("aaa", "Renamed")


async def test_deleting_the_selected_profile_is_refused() -> None:
    """Firmware 1.10: `deleteProfile` refuses the selected profile (`Delete failed`); the file,
    the selection and the status stay as they were."""
    device = _two_profiles()
    await device.start()
    try:
        await device.select_profile("aaa")
        async with connect(f"ws://{device.address}/ws") as socket:
            await _status(socket)
            refused = await _request(socket, "req:profiles:delete", id="aaa")
            listed = (await _request(socket, "req:profiles:list"))["profiles"]
            deleted_other = await _request(socket, "req:profiles:delete", id="bbb")
    finally:
        await device.stop()

    assert refused["error"] == "Delete failed"
    assert [(p["id"], p["selected"]) for p in listed if p["id"] in ("aaa", "bbb")] == [
        ("aaa", True),
        ("bbb", False),
    ]
    assert "error" not in deleted_other


async def test_a_selection_set_on_the_fake_before_any_client_is_protected_all_the_same() -> None:
    """The display loads its selection at boot, so the refusal does not depend on anyone having
    asked for the status first."""
    device = _two_profiles()
    device.selected_profile_id = "aaa"
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            refused = await _request(socket, "req:profiles:delete", id="aaa")
        async with connect(f"ws://{device.address}/ws") as socket:
            status = await _status(socket)
    finally:
        await device.stop()

    assert refused["error"] == "Delete failed"
    assert (status["puid"], status["p"]) == ("aaa", "First")


# ── firmware 1.10 ────────────────────────────────────────────────────


async def test_a_new_machine_has_its_two_system_profiles_and_refuses_to_delete_them() -> None:
    device = FakeDevice()
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            listed = (await _request(socket, "req:profiles:list"))["profiles"]
            refused = [
                await _request(socket, "req:profiles:delete", id=pid)
                for pid in ("flush", "descale")
            ]
            again = (await _request(socket, "req:profiles:list"))["profiles"]
    finally:
        await device.stop()

    assert [(p["id"], p["utility"]) for p in listed] == [("flush", True), ("descale", True)]
    assert [p["label"] for p in listed] == ["[Utility] Backflush", "[Utility] Descale"]
    assert [r["error"] for r in refused] == ["Delete failed", "Delete failed"]
    assert [p["id"] for p in again] == ["flush", "descale"]


async def test_a_boot_seeds_a_missing_system_profile_and_keeps_an_edited_one() -> None:
    device = FakeDevice()
    edited = next(p for p in device.profiles if p["id"] == "flush")
    edited["label"] = "My backflush"
    device.profiles = [edited]
    device.ensure_system_profiles()
    assert [(p["id"], p["label"]) for p in device.profiles] == [
        ("flush", "My backflush"),
        ("descale", "[Utility] Descale"),
    ]


async def test_a_system_profile_stays_a_utility_profile_whatever_a_save_says() -> None:
    device = FakeDevice()
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            seeded = (await _request(socket, "req:profiles:load", id="flush"))["profile"]
            saved = await _request(
                socket,
                "req:profiles:save",
                profile={**seeded, "utility": False, "description": "edited"},
            )
    finally:
        await device.stop()

    assert saved["profile"]["utility"] is True
    assert saved["profile"]["description"] == "edited"


async def test_the_firmware_writes_a_dose_on_every_profile_and_keeps_a_ratio_stop() -> None:
    device = _two_profiles()
    device.profiles[1]["dose"] = 18
    device.profiles[1]["phases"] = [
        {"name": "p", "phase": "brew", "valve": 1, "duration": 30, "pump": 100}
        | {"targets": [{"type": "ratio", "operator": "gte", "value": 2.0}]}
    ]
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            first = (await _request(socket, "req:profiles:load", id="aaa"))["profile"]
            second = (await _request(socket, "req:profiles:load", id="bbb"))["profile"]
    finally:
        await device.stop()

    assert first["dose"] == 0
    assert second["dose"] == 18
    assert second["phases"][0]["targets"] == [{"type": "ratio", "operator": "gte", "value": 2.0}]


async def test_the_minimal_list_carries_utility() -> None:
    device = _two_profiles()
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            listed = (await _request(socket, "req:profiles:list", minimal=True))["profiles"]
    finally:
        await device.stop()

    assert listed == [
        {"id": "aaa", "label": "First", "utility": False},
        {"id": "bbb", "label": "Second", "utility": False},
    ]


async def test_an_eleventh_favourite_is_refused_and_starring_one_already_starred_is_not() -> None:
    device = FakeDevice()
    device.profiles = [
        {"id": f"p{i:02d}", "label": f"P{i}", "temperature": 90, "phases": []} for i in range(12)
    ]
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            replies = [
                await _request(socket, "req:profiles:favorite", id=f"p{i:02d}") for i in range(11)
            ]
            again = await _request(socket, "req:profiles:favorite", id="p03")
            await _request(socket, "req:profiles:unfavorite", id="p00")
            after_room = await _request(socket, "req:profiles:favorite", id="p10")
    finally:
        await device.stop()

    assert [("error" in r) for r in replies] == [False] * 10 + [True]
    assert replies[10]["error"] == "Favorite limit reached"
    assert "error" not in again
    assert "error" not in after_room
    assert len(device.favorite_profile_ids) == 10
    assert "p10" in device.favorite_profile_ids


async def test_a_new_profile_saved_at_the_favourite_limit_is_saved_and_not_starred() -> None:
    device = FakeDevice()
    device.favorite_profile_ids = {f"f{i}" for i in range(10)}
    await device.start()
    try:
        async with connect(f"ws://{device.address}/ws") as socket:
            saved = await _request(
                socket,
                "req:profiles:save",
                profile={
                    "label": "New",
                    "type": "standard",
                    "temperature": 90,
                    "phases": [
                        {"name": "p", "phase": "brew", "valve": 1, "duration": 20, "pump": 100}
                    ],
                },
            )
    finally:
        await device.stop()

    assert "error" not in saved
    assert saved["profile"]["favorite"] is False
    assert len(device.favorite_profile_ids) == 10
