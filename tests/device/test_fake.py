"""The fake device's own guarantees.

A fake that drifts from the firmware is worse than no fake: every test above it
keeps passing while the real machine breaks. These are the properties the rest
of the suite silently relies on.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from gaggiclanker.device.fake import (
    DEFAULT_IDENTITY,
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
    assert slog.header.version == 7
    assert len(slog.samples) == 12
    assert slog.samples[-1].wp is not None


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
    assert device.profiles == []


def test_the_index_it_serves_round_trips_through_our_parser(
    fake_device: FakeDevice,
) -> None:
    from gaggiclanker.domain.index import encode_index

    parsed = parse_index(encode_index(fake_device.index()))
    assert {e.id for e in parsed.entries} == set(fake_device.shots)


async def test_the_fake_refuses_to_be_written_to(fake_device: FakeDevice) -> None:
    """A test that tries to write device settings must fail loudly.

    `POST /api/settings` on the real machine clears every checkbox-style
    boolean key the body omits. gaggiclanker never sends one; the fake makes
    sure a future version that does cannot pass its tests.
    """
    async with httpx.AsyncClient(base_url=f"http://{fake_device.address}") as http:
        response = await http.post("/api/settings", data={"mdnsName": "nope"})
    assert response.status_code == 405


async def test_an_unknown_path_gets_the_spa_like_the_real_firmware(
    fake_device: FakeDevice,
) -> None:
    """`serveWebAsset` answers any unrecognised path with index.html.

    A file *under* `/api/history/` that does not exist is still a real 404 —
    that route is a static mount — but a browser-shaped path is the SPA, which
    is how a mistyped URL turns into "expected binary, got HTML".
    """
    async with httpx.AsyncClient(base_url=f"http://{fake_device.address}") as http:
        response = await http.get("/shots/42")
    assert response.status_code == 200
    assert response.text.lower().startswith("<!doctype")


def test_the_client_limit_matches_the_firmware_build_flag() -> None:
    """`-DDEFAULT_MAX_WS_CLIENTS=3` — and the machine's own UI is one of them."""
    assert MAX_WS_CLIENTS == 3


def test_the_identity_names_a_pro_board() -> None:
    """`hardware` is the only place the board type is knowable (report §6)."""
    assert "Pro" in DEFAULT_IDENTITY["hardware"]


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
    assert parse_slog(saved.slog_bytes).header.version == 7
    # The header and the index entry have to name the same profile: the archive
    # reads the first, the device's own list reads the second, and a shot that
    # disagrees with itself looks like a sync bug.
    assert parse_slog(saved.slog_bytes).header.profile_name == saved.entry.profile_name
