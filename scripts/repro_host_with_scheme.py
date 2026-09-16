#!/usr/bin/env python
"""Reproduce: a machine address typed with its scheme never reaches the machine.

    uv run python scripts/repro_host_with_scheme.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

``gaggimateHost`` is documented as a bare host, but the address a browser shows
for the machine is ``http://192.168.1.50/``, and that is what gets pasted. The
client put the setting straight into ``http://{host}`` and ``ws://{host}/ws``,
so the URL became ``http://http://192.168.1.50//api/settings``: aiohttp read the
host as ``http``, name resolution failed, and the Device page reported
``Cannot connect to host http:80 ssl:default [Temporary failure in name
resolution]`` — with nothing in the message pointing at the setting.

This script starts the fake machine, points a connection built from settings
values at it as ``http://127.0.0.1:<port>/``, as ``WS://127.0.0.1:<port>`` and
bare, waits for the socket and asks for the machine's settings over HTTP. It
also checks that saving a value the client cannot use (an ``https://`` scheme,
a path, a user name) is refused with a message instead of stored.
"""

from __future__ import annotations

import asyncio
import sys

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.connection import device_config
from gaggiclanker.device.fake import build_fake_device
from gaggiclanker.settings import SETTINGS_REGISTRY, SettingValueError


async def reaches(typed: str) -> bool:
    config = device_config(
        {
            "gaggimateHost": typed,
            "gaggimateProtocol": "ws",
            "gaggimateTimeoutSeconds": 5.0,
            "deviceSyncEnabled": True,
        }
    )
    client = GaggimateClient(config.host, protocol=config.protocol, timeout=config.timeout)
    await client.start()
    try:
        if not await client.wait_connected(5.0):
            print(f"FAIL {typed!r}: no socket to {client.ws_url}")
            return False
        try:
            await client.get_settings()
        except Exception as exc:
            print(f"FAIL {typed!r}: {exc}")
            return False
        print(f"ok   {typed!r}: {client.http_base}")
        return True
    finally:
        await client.stop()


async def main() -> int:
    fake = build_fake_device()
    address = await fake.start()
    try:
        results = [
            await reaches(typed)
            for typed in (f"http://{address}/", f"WS://{address}", f" {address} ")
        ]
    finally:
        await fake.stop()

    definition = SETTINGS_REGISTRY["gaggimateHost"]
    for unusable in ("https://192.168.1.50", "192.168.1.50/gaggimate", "user@192.168.1.50"):
        try:
            definition.coerce(unusable)
        except SettingValueError:
            print(f"ok   {unusable!r}: refused")
        else:
            print(f"FAIL {unusable!r}: accepted")
            results.append(False)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
