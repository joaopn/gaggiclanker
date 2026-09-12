#!/usr/bin/env python
"""Run the four validation layers over a profile file. Exit non-zero on any failure.

    uv run python scripts/profile_gate.py tests/fixtures/profiles/*.json
    uv run python scripts/profile_gate.py --host 127.0.0.1:8080 my-profile.json
    uv run python scripts/profile_gate.py --offline my-profile.json

What it is for: CI, and the five minutes before somebody puts a hand-written
profile on their machine. The test suite runs these layers over the profiles
*this repository* ships; this runs them over a file you have, which is the case
the suite cannot cover.

The layers, in the order they are applied and in increasing order of cost:

1. **schema** — the strict pydantic `Profile`. Catches the four things the
   firmware accepts and misreads: zero phases, a float `pump`, an `operator`
   spelling it reads as `lte`, a target type it silently drops.
2. **policy** — bounds narrower than the firmware's. Reports what it would clamp
   and refuses what it cannot fix.
3. **round trip** — save to a machine, read it back, compare canonical JSON.
   Needs `--host`.
4. **brew** — select the profile and run a shot to completion. Needs `--host`
   and `--brew`, and it is the only layer that can tell you a profile the
   machine stored faithfully will not actually run.

Layers 3 and 4 write to whatever `--host` names. **Point it at the simulator**
(`scripts/sim.sh serve`), not at your machine, unless you mean it: everything
this script creates it also deletes, but a profile saved to a real display is a
profile on a real display until the delete lands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.writes import DeviceWriteGate, PendingWrite
from gaggiclanker.domain.models import Profile, canonical_profile_json, with_app_suffix
from gaggiclanker.domain.profile_policy import (
    DEFAULT_BOUNDS,
    PolicyBounds,
    check,
    clamp,
)

#: `MODE_BREW` from `src/display/core/constants.h`.
MODE_BREW = 1

#: How long to let a brew run before calling it a failure. Long enough for a
#: 120 s profile — the policy's own ceiling — plus the save.
BREW_TIMEOUT_S = 180.0


class AllowEverything:
    """A write gate that authorises everything and records nothing.

    The app's gate reads a setting and an audit table, both of which live in a
    database this script does not have. Saying so out loud rather than reaching
    for the real one: **this is the only place in the codebase that bypasses the
    `deviceWritesEnabled` switch**, it is a developer tool run deliberately from
    a shell against a host named on the command line, and it deletes everything
    it creates.
    """

    async def authorize(self, write: PendingWrite) -> None:
        return None

    async def record(self, write: PendingWrite, *, result: str, error: str = "") -> None:
        return None


def report(status: str, message: str) -> None:
    sys.stdout.write(f"  {status:<5} {message}\n")


def load(path: Path) -> tuple[Profile | None, list[str]]:
    """Layer 1. Returns the profile, or the schema errors that stopped it."""
    try:
        document = json.loads(path.read_text())
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    try:
        return Profile.model_validate(document), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()
        ]


def apply_policy(profile: Profile, bounds: PolicyBounds) -> tuple[Profile | None, bool]:
    """Layer 2. Returns the clamped profile — or ``None`` if it was refused."""
    clamped, changes = clamp(profile, bounds)
    for change in changes:
        report("clamp", f"{change.path}: {change.before:g} -> {change.after:g} ({change.reason})")
    violations = check(clamped, bounds)
    for violation in violations:
        report("FAIL", f"{violation.path}: {violation.message}")
    if violations:
        return None, bool(changes)
    return clamped, bool(changes)


async def round_trip(client: GaggimateClient, profile: Profile) -> tuple[str | None, bool]:
    """Layer 3. Save, read back, compare. Returns the device id and whether it matched."""
    sent = profile.for_new_device_profile(label=with_app_suffix(profile.label))
    stored = await client.save_profile(sent)
    device_id = stored.id
    if device_id is None:
        report("FAIL", "the machine saved the profile and gave it no id")
        return None, False
    served = await client.load_profile(device_id)
    expected = canonical_profile_json(sent)
    actual = canonical_profile_json(served)
    if expected != actual:
        report("FAIL", "the machine stored something other than what was sent")
        report("", f"sent:   {expected}")
        report("", f"loaded: {actual}")
        return device_id, False
    report("ok", f"round trip verified as {device_id}")
    return device_id, True


async def brew(host: str, client: GaggimateClient, device_id: str) -> bool:
    """Layer 4. Select it and run a shot.

    The two frames that start a brew are ones `GaggimateClient` is forbidden to
    send — they are on the list in `tests/device/test_public_surface.py` — so
    they go out on a throwaway socket, exactly as the simulator test does it.
    The socket is closed as soon as the shot is saved: the firmware allows three
    clients in total and the client above is holding one.
    """
    import websockets

    await client.select_profile(device_id)
    async with websockets.connect(f"ws://{host}/ws", max_size=None) as socket:
        await asyncio.sleep(2)
        await socket.send(json.dumps({"tp": "req:change-mode", "mode": MODE_BREW}))
        await asyncio.sleep(1)
        await socket.send(json.dumps({"tp": "req:process:activate", "ignoreWarnings": True}))
        saved = False
        try:
            async with asyncio.timeout(BREW_TIMEOUT_S):
                async for raw in socket:
                    if json.loads(raw).get("tp") == "evt:history-shot-saved":
                        saved = True
                        break
        except TimeoutError:
            saved = False
        finally:
            await socket.send(json.dumps({"tp": "req:process:deactivate"}))
            await asyncio.sleep(0.5)
    report(
        "ok" if saved else "FAIL",
        "brewed to completion" if saved else "the machine would not brew it",
    )
    return saved


async def gate_one(
    path: Path, client: GaggimateClient | None, host: str, bounds: PolicyBounds, want_brew: bool
) -> bool:
    sys.stdout.write(f"{path}\n")
    profile, errors = load(path)
    if profile is None:
        for message in errors:
            report("FAIL", message)
        return False
    report("ok", f"schema: {profile.label!r}, {len(profile.phases)} phases")

    clamped, _ = apply_policy(profile, bounds)
    if clamped is None:
        return False
    report("ok", "policy")

    if client is None:
        report("skip", "round trip and brew (no --host)")
        return True

    device_id: str | None = None
    try:
        device_id, matched = await round_trip(client, clamped)
        if not matched or device_id is None:
            return False
        if want_brew and not await brew(host, client, device_id):
            return False
    finally:
        # Always, including on a failure: a gate that leaves its own profiles
        # behind changes the next run's profile list, and on a real machine it
        # leaves litter somebody has to clean up by hand.
        if device_id is not None:
            try:
                await client.delete_profile(device_id)
                report("ok", f"deleted {device_id}")
            except Exception as exc:  # reported, never swallowed
                report("WARN", f"could not delete {device_id}: {exc}")
    return True


async def run(args: argparse.Namespace) -> int:
    bounds = DEFAULT_BOUNDS
    client: GaggimateClient | None = None
    if args.host:
        client = GaggimateClient(args.host, timeout=20.0, write_gate=_gate())
        await client.start()
        if not await client.wait_connected(20.0):
            await client.stop()
            sys.stderr.write(f"no machine answering on ws://{args.host}/ws\n")
            return 2
    try:
        results = [
            await gate_one(Path(path), client, args.host, bounds, args.brew) for path in args.files
        ]
    finally:
        if client is not None:
            await client.stop()
    failed = results.count(False)
    sys.stdout.write(f"\n{len(results) - failed}/{len(results)} profiles passed the gate\n")
    return 1 if failed else 0


def _gate() -> DeviceWriteGate:
    gate: Any = AllowEverything()
    return gate  # type: ignore[no-any-return]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the four profile validation layers over one or more files."
    )
    parser.add_argument("files", nargs="+", help="profile JSON files")
    parser.add_argument(
        "--host",
        default="",
        help=(
            "host:port of a machine to round-trip against, e.g. 127.0.0.1:8080 for the "
            "simulator. Omit for layers 1 and 2 only."
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="alias for omitting --host: schema and policy only",
    )
    parser.add_argument(
        "--brew",
        action="store_true",
        help="also select each profile and brew a shot with it (layer 4). Needs --host.",
    )
    args = parser.parse_args(argv)
    if args.offline:
        args.host = ""
    if args.brew and not args.host:
        parser.error("--brew needs --host")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
