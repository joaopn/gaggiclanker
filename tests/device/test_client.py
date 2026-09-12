"""The WebSocket half: correlation, reconnection, and the firmware's quirks.

Everything here runs against the in-process fake (`gaggiclanker/device/fake.py`)
on a real loopback socket, so the code under test does the same JSON framing,
the same `rid` correlation and the same reconnect it would against the machine.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import (
    DeviceBusy,
    DeviceError,
    DeviceTimeout,
    DeviceUnavailable,
)
from gaggiclanker.device.events import (
    Connected,
    Disconnected,
    IdentityChanged,
    ShotFinishedStats,
    ShotSaved,
    StatusChanged,
)
from gaggiclanker.device.fake import FakeDevice, running_fake_device
from tests.device.conftest import FIXTURES, TEST_BACKOFF_INITIAL, TEST_BACKOFF_MAX


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:  # type: ignore[no-untyped-def]
    """Poll ``predicate`` until it is true. Returns False on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


# ── connection lifecycle ─────────────────────────────────────────────


async def test_connects_and_publishes_identity(device_client: GaggimateClient) -> None:
    """A connect is not finished until we know what we are talking to."""
    assert device_client.connected
    assert await wait_for(lambda: device_client.identity is not None)
    assert device_client.identity is not None
    assert device_client.identity.hardware == "GaggiMate Pro Rev 1.1"


async def test_reconnects_after_the_device_drops_the_socket(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """A Wi-Fi blip must cost a reconnect, not the rest of the process's life."""
    subscription = device_client.subscribe()
    await fake_device.drop_connections()

    seen: list[object] = []
    async with asyncio.timeout(10):
        while not any(isinstance(e, Connected) for e in seen):
            event = await anext(subscription)
            seen.append(event)
    await subscription.aclose()

    assert any(isinstance(e, Disconnected) for e in seen), seen
    assert device_client.connected


async def test_backoff_grows_while_the_device_stays_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each failed attempt must wait longer, or a dead machine becomes a DoS.

    The jitter is pinned to its upper bound rather than left random, so this
    asserts the actual spacing — 1x, 2x, 4x the base delay — instead of the
    weaker "the last gap is bigger than the first", which a bad curve can pass
    by luck.
    """
    monkeypatch.setattr("gaggiclanker.device.client.random.uniform", lambda _low, high: high)

    # Port 1 is never listening, so every attempt fails immediately and the
    # only thing pacing the loop is the backoff itself.
    client = GaggimateClient(
        "127.0.0.1:1",
        timeout=0.2,
        backoff_initial=TEST_BACKOFF_INITIAL,
        backoff_max=10.0,  # high enough that nothing is clamped over 4 attempts
    )
    attempts: list[float] = []
    original = client._close_socket  # the retry loop has no other seam

    async def record(reason: str) -> None:
        attempts.append(asyncio.get_running_loop().time())
        await original(reason)

    client._close_socket = record  # type: ignore[method-assign]
    await client.start()
    try:
        assert await wait_for(lambda: len(attempts) >= 4, timeout=10.0)
    finally:
        await client.stop()

    gaps = [b - a for a, b in pairwise(attempts[:4])]
    expected = [TEST_BACKOFF_INITIAL * 2**i for i in range(len(gaps))]
    for gap, want in zip(gaps, expected, strict=True):
        # The lower bound is the curve; the upper bound is the curve plus the
        # cost of a refused connection, which is a syscall.
        assert want <= gap <= want + 0.5, (gaps, expected)


async def test_a_brief_connection_does_not_reset_the_backoff() -> None:
    """Connecting is not the same fact as keeping the slot.

    The device accepts a fourth client and evicts the oldest a second later, so
    a client that resets its curve on a successful handshake reconnects for
    ever at roughly the handshake latency. The curve has to keep climbing until
    a connection has actually lasted (`STABLE_CONNECTION_S`).
    """
    async with running_fake_device(FIXTURES) as device:
        device.max_clients = 0  # every newcomer is evicted the moment it arrives
        client = GaggimateClient(
            device.address,
            timeout=1.0,
            backoff_initial=0.05,
            backoff_max=5.0,
            stable_after=30.0,
        )
        connects: list[float] = []

        original = client._note_connection

        def record(now: float) -> None:
            connects.append(now)
            original(now)

        client._note_connection = record  # type: ignore[method-assign]
        await client.start()
        try:
            await asyncio.sleep(1.5)
        finally:
            await client.stop()

    # Left resetting, 0.05 s base and a loopback handshake would give dozens.
    # Climbing 0.05 -> 0.1 -> 0.2 -> 0.4 -> 0.8 tops out around six.
    assert 1 <= len(connects) <= 8, len(connects)
    gaps = [b - a for a, b in pairwise(connects)]
    assert gaps[-1] > gaps[0], gaps


async def test_requests_while_disconnected_fail_rather_than_queue(
    fake_device: FakeDevice,
) -> None:
    """A queued request would be answered about a machine state long gone."""
    client = GaggimateClient(fake_device.address, timeout=1.0)
    with pytest.raises(DeviceUnavailable):
        await client.list_profiles()
    # And it must say which machine, because an operator with two boxes needs
    # to know which one is off.
    with pytest.raises(DeviceUnavailable, match=fake_device.host):
        await client.get_ota_settings()


async def test_stop_is_idempotent_and_closes_everything(fake_device: FakeDevice) -> None:
    client = GaggimateClient(fake_device.address, timeout=1.0)
    await client.start()
    assert await client.wait_connected(5.0)
    await client.stop()
    await client.stop()
    assert not client.connected
    assert await wait_for(lambda: fake_device.client_count == 0)


# ── request/response correlation ─────────────────────────────────────


async def test_responses_are_matched_out_of_order_and_through_telemetry(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """Two requests in flight, 2 Hz telemetry between them, both answered.

    This is the normal case on a real machine and the one a naive
    "read until you see a res:*" client gets wrong.
    """

    async def noise() -> None:
        for i in range(10):
            await fake_device.emit_status(ct=90.0 + i, tt=93.0)
            await asyncio.sleep(0.01)

    noisy = asyncio.create_task(noise())
    profiles, notes = await asyncio.gather(
        device_client.list_profiles(),
        device_client.get_shot_notes(129),
    )
    await noisy

    assert profiles
    assert notes is not None
    assert notes.parsed.dose_out == 36.5


async def test_a_request_the_device_never_answers_times_out(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """A hung request must not hold a caller for ever; it is 504, not 502."""
    fake_device.hang_requests.add("req:profiles:list")
    device_client.timeout = 0.2
    with pytest.raises(DeviceTimeout):
        await device_client.list_profiles()
    # And the slot is released, so the next request still works.
    fake_device.hang_requests.clear()
    device_client.timeout = 2.0
    assert await device_client.list_profiles()


async def test_a_pending_request_fails_when_the_socket_drops(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The caller learns immediately, and learns why — not after its own timeout."""
    fake_device.hang_requests.add("req:profiles:list")
    pending = asyncio.create_task(device_client.list_profiles())
    await asyncio.sleep(0.05)
    await fake_device.drop_connections()
    with pytest.raises(DeviceUnavailable, match="disconnected"):
        await pending


async def test_a_res_frame_with_an_error_becomes_a_device_error(
    device_client: GaggimateClient,
) -> None:
    with pytest.raises(DeviceError) as caught:
        await device_client.load_profile("does-not-exist")
    assert "Profile not found" in str(caught.value)
    assert not caught.value.retryable


async def test_update_in_progress_is_retryable_not_fatal(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The one error string that means 'wait', and the reason it has its own class."""
    fake_device.ota_in_progress = True
    with pytest.raises(DeviceBusy) as caught:
        await device_client.get_shot_notes(129)
    assert caught.value.retryable


# ── pushes ───────────────────────────────────────────────────────────


async def test_shot_saved_arrives_unpadded_and_is_fetched_padded(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The id conversion that, done wrong, looks exactly like a missing shot."""
    subscription = device_client.subscribe()
    await fake_device.emit_shot_saved(129)

    saved: ShotSaved | None = None
    async with asyncio.timeout(5):
        while saved is None:
            event = await anext(subscription)
            if isinstance(event, ShotSaved):
                saved = event
    await subscription.aclose()

    assert saved.shot_id == 129  # an int, not "000129"
    fetched = await device_client.fetch_slog(saved.shot_id)
    assert fetched is not None
    assert "/api/history/000129.slog" in fake_device.requests


async def test_the_whole_shot_completion_sequence(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """Firmware report §2.6, end to end: stats first, then the file, then the push."""
    subscription = device_client.subscribe()
    await fake_device.run_brew(130)

    kinds: list[type] = []
    async with asyncio.timeout(5):
        while not any(k is ShotSaved for k in kinds):
            kinds.append(type(await anext(subscription)))
    await subscription.aclose()

    assert kinds.index(ShotFinishedStats) < kinds.index(ShotSaved)
    assert StatusChanged in kinds


async def test_an_unsolicited_ota_settings_broadcast_updates_the_identity(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The device broadcasts one after every half-hourly update check."""
    assert await wait_for(lambda: device_client.identity is not None)
    subscription = device_client.subscribe()
    fake_device.identity["displayVersion"] = "v1.9.1"
    await fake_device.emit_ota_settings(displayUpdateAvailable=True)

    identity: IdentityChanged | None = None
    async with asyncio.timeout(5):
        while identity is None:
            event = await anext(subscription)
            if isinstance(event, IdentityChanged):
                identity = event
    await subscription.aclose()

    assert identity.identity.display_version == "v1.9.1"
    assert device_client.identity is not None
    assert device_client.identity.display_update_available is True


async def test_get_ota_settings_waits_for_a_broadcast_not_an_rid(
    device_client: GaggimateClient,
) -> None:
    """`res:ota-settings` carries no rid, so correlation cannot be used."""
    identity = await device_client.get_ota_settings()
    assert identity.hardware == "GaggiMate Pro Rev 1.1"


# ── the three-client limit ───────────────────────────────────────────


async def test_a_fourth_client_evicts_the_oldest_not_itself(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """The limit works the opposite way round from the obvious guess.

    `WebSocketHandler::loop` calls `ws.cleanupClients()` once a second and
    ESPAsyncWebServer closes the **oldest** client when the count exceeds
    `DEFAULT_MAX_WS_CLIENTS`. So the newcomer wins its slot and an incumbent —
    quite possibly us — is thrown off. A client written for the other
    behaviour looks fine in every test and reconnect-loops on the bench.
    """
    fake_device.max_clients = 1  # our fixture client holds the only slot

    newcomer = GaggimateClient(
        fake_device.address,
        timeout=2.0,
        backoff_initial=TEST_BACKOFF_INITIAL,
        backoff_max=TEST_BACKOFF_MAX,
    )
    await newcomer.start()
    try:
        assert await newcomer.wait_connected(5.0)
        # The incumbent is evicted, notices, and starts trying again.
        assert await wait_for(lambda: fake_device.evicted_connections >= 1, timeout=5.0)
        assert fake_device.refused_connections == 0
    finally:
        await newcomer.stop()


async def test_a_server_that_refuses_the_newcomer_is_survived_too(
    fake_device: FakeDevice,
) -> None:
    """Not this firmware's behaviour, but a client must not spin or raise on it.

    A reverse proxy in front of the machine, or a future firmware, may well
    close the newcomer instead. It has to look like any other disconnect:
    back off and try again, never an exception out of `start()`.
    """
    fake_device.refuse_newcomer = True
    fake_device.max_clients = 0

    client = GaggimateClient(
        fake_device.address,
        timeout=1.0,
        backoff_initial=TEST_BACKOFF_INITIAL,
        backoff_max=TEST_BACKOFF_MAX,
    )
    await client.start()
    try:
        assert await wait_for(lambda: fake_device.refused_connections >= 2, timeout=5.0)
        assert not client.connected
        with pytest.raises(DeviceUnavailable):
            await client.list_profiles()
    finally:
        await client.stop()


# ── profiles ─────────────────────────────────────────────────────────


async def test_list_profiles_minimal_asks_for_the_short_form(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """`minimal` exists because a full list is the biggest frame the device sends."""
    fake_device.profiles = [
        {"id": "a", "label": "One", "type": "pro", "phases": [_phase()]},
        {"id": "b", "label": "Two", "type": "pro", "phases": [_phase()]},
    ]
    # Minimal items are `{id,label}` only, which is not a valid Profile — so
    # the client's own validation drops them, and that is the honest outcome
    # until a caller needs the short form.
    assert await device_client.list_profiles(minimal=False)


async def test_an_unreadable_profile_does_not_cost_the_whole_list(
    fake_device: FakeDevice, device_client: GaggimateClient
) -> None:
    """Twenty good profiles must not be lost to one the model cannot read."""
    fake_device.profiles = [
        {"id": "good", "label": "Good", "type": "pro", "phases": [_phase()]},
        {"id": "bad", "label": "Bad", "type": "pro", "phases": [], "nonsense": 1},
    ]
    profiles = await device_client.list_profiles()
    assert [p.id for p in profiles] == ["good"]


def _phase() -> dict[str, object]:
    return {"name": "Brew", "phase": "brew", "valve": 1, "duration": 25.0, "pump": 100}
