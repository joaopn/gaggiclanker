"""The HTTP half: the four ways the firmware answers something other than the file.

Each of these is a real failure mode of the GaggiMate firmware
and each has to be a *different* outcome, because the sync loop's decision
depends on which one it is: wait (503), retry (HTML, header-only), skip (404),
store anyway (unparseable).
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceBusy, DeviceProtocolError
from gaggiclanker.device.fake import FakeDevice, header_only_bytes, synthetic_slog_bytes


async def test_fetch_index_parses_what_the_device_encodes(
    device_client: GaggimateClient,
) -> None:
    index = await device_client.fetch_index()
    assert index is not None
    assert index.header.entry_size == 128
    assert {e.id for e in index.entries} >= {129, 196, 204, 222}


async def test_a_missing_index_is_none_not_an_error(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """A machine with no history yet is not a machine in trouble."""
    fake_device.index_missing = True
    assert await device_client.fetch_index() is None


async def test_fetch_recent_is_newest_first_and_clamped(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    recent = await device_client.fetch_recent(2)
    assert recent is not None
    assert [e.id for e in recent.entries] == [222, 204]
    # The firmware zeroes nextId on recent.bin: it is a window, not the index.
    assert recent.header.next_id == 0

    await device_client.fetch_recent(999)
    assert "/api/history/recent.bin?limit=50" in fake_device.requests


async def test_fetch_slog_returns_the_bytes_and_the_parse(
    device_client: GaggimateClient,
) -> None:
    """The bytes are the product; the parse is a convenience on top of them."""
    fetched = await device_client.fetch_slog(196)
    assert fetched is not None
    assert fetched.raw[:4] == b"SHOT"
    assert fetched.slog is not None
    assert fetched.slog.samples
    assert fetched.parse_error is None
    assert fetched.incomplete is False


async def test_a_header_only_file_is_retried_and_then_succeeds(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The device serves a header while it is still writing the shot.

    Two header-only answers then the real file — which is what a fetch racing
    the index poller actually sees.
    """
    fake_device.shots[196].header_only_requests = 2
    before = len(fake_device.requests)

    fetched = await device_client.fetch_slog(196)

    assert fetched is not None
    assert fetched.slog is not None
    assert fetched.slog.samples
    assert fetched.incomplete is False
    assert len(fake_device.requests) - before == 3


async def test_a_file_that_never_finishes_comes_back_flagged_incomplete(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """After the budget, return what we have rather than nothing.

    The samples in a half-written file are real, and the machine deletes old
    shots under storage pressure — a partial shot beats no shot.
    """
    fake_device.shots[196].header_only_requests = 10_000
    fetched = await device_client.fetch_slog(196)

    assert fetched is not None
    assert fetched.incomplete is True
    assert fetched.raw == header_only_bytes(fake_device.shots[196].slog_bytes)


async def test_a_missing_slog_is_none(device_client: GaggimateClient) -> None:
    assert await device_client.fetch_slog(999_999) is None


async def test_html_is_classified_as_a_retryable_protocol_error(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """A 200 carrying the device's own SPA is the firmware's loudest quirk.

    `ESPAsyncWebServer` answers unknown paths with the embedded UI, and
    `/api/history` has been seen doing it under memory pressure — so this is a
    transient device failure, not a missing file, and the message has to say so
    or the next person spends an evening on a corrupt-file hunt.
    """
    fake_device.html_paths.add("/api/history/index.bin")
    with pytest.raises(DeviceProtocolError) as caught:
        await device_client.fetch_index()
    assert caught.value.retryable
    assert "web UI" in str(caught.value)


async def test_503_during_an_ota_is_busy_not_broken(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """Every /api/history/* route answers 503 for the whole update."""
    fake_device.ota_in_progress = True
    with pytest.raises(DeviceBusy) as caught:
        await device_client.fetch_index()
    assert caught.value.retryable
    assert caught.value.status == 503

    fake_device.ota_in_progress = False
    assert await device_client.fetch_index() is not None


async def test_unparseable_bytes_are_returned_rather_than_lost(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """Losing a shot to a parser bug is worse than storing one we cannot read.

    The machine will have deleted its copy by the time the parser is fixed, so
    the bytes come back with `parse_error` set and the sync engine quarantines them.
    """
    fake_device.add_shot(500, b"SHOT" + bytes([99]) + b"\x00" * 600)
    fetched = await device_client.fetch_slog(500)
    assert fetched is not None
    assert fetched.slog is None
    assert fetched.parse_error is not None
    assert fetched.raw.startswith(b"SHOT")


async def test_notes_over_http_and_over_the_socket_agree(
    device_client: GaggimateClient,
) -> None:
    """Two routes to the same document; the sync engine uses whichever is available."""
    over_http = await device_client.fetch_notes_json(129)
    over_ws = await device_client.get_shot_notes(129)
    assert over_http is not None
    assert over_ws is not None
    assert over_http.to_device() == over_ws.to_device()


async def test_a_shot_with_no_notes_is_none_over_both_routes(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    assert fake_device.shots[196].notes is None
    assert await device_client.fetch_notes_json(196) is None
    assert await device_client.get_shot_notes(196) is None


async def test_get_status_and_get_settings_are_plain_reads(
    device_client: GaggimateClient,
) -> None:
    status = await device_client.get_status()
    assert set(status) == {"mode", "tt", "ct"}
    settings = await device_client.get_settings()
    assert "mdnsName" in settings


async def test_an_unknown_path_answering_html_is_not_mistaken_for_json(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The SPA fallback catches /api/status too if the firmware is confused."""
    fake_device.html_paths.add("/api/status")
    with pytest.raises(DeviceProtocolError):
        await device_client.get_status()


async def test_http_concurrency_is_capped_at_two(fake_device: FakeDevice) -> None:
    """The display serves everything from ~300 KB of heap.

    Its own UI aborts in-flight fetches for this reason; three parallel `.slog`
    reads is how an external client gets HTML back instead of binary.
    """
    in_flight = 0
    peak = 0
    original = fake_device._history_file_handler  # the fake has no other seam

    async def counting(request: web.Request) -> web.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.05)
            return await original(request)
        finally:
            in_flight -= 1

    # Replaced before the restart: `start()` is what registers the handlers on
    # the router, so a swap after it would never be reached.
    fake_device._history_file_handler = counting  # type: ignore[method-assign]
    await fake_device.stop()
    await fake_device.start()

    for shot_id in (300, 301, 302, 303, 304, 305):
        fake_device.add_shot(shot_id, synthetic_slog_bytes(8, shot_id=shot_id))

    client = GaggimateClient(fake_device.address, timeout=5.0)
    await client.start()
    try:
        await asyncio.gather(*(client.fetch_slog(i) for i in range(300, 306)))
    finally:
        await client.stop()

    assert peak <= 2, f"{peak} concurrent requests reached the device"
