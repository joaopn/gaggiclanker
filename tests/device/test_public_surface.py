"""The prototype writes nothing to the machine, and this is what enforces it.

The prototype writes nothing to the device until profile drafts and push are
built. A convention decays; a test does not. The stakes are
concrete: `POST /api/settings` clears every checkbox-style boolean key the body
omits (so a partial write turns off HomeKit, boiler fill and the momentary
buttons), `req:profiles:save` with a float `pump` leaves a profile that never
runs the pump, and `req:history:delete` is unrecoverable — the machine is the
only copy until we have synced it.

If you are here because this test failed, the question is not "how do I update
the list" but "has the four-layer write path in investigation.md §4.6 been
built yet".
"""

from __future__ import annotations

import inspect

from gaggiclanker.device.client import READ_ONLY_METHODS, GaggimateClient

#: Methods that are part of running the client rather than talking to the
#: machine. They send nothing the device can act on.
LIFECYCLE_METHODS = frozenset({"start", "stop", "wait_connected", "subscribe"})


def public_methods() -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(GaggimateClient)
        if not name.startswith("_")
        and (inspect.isfunction(value) or inspect.iscoroutinefunction(value))
    }


def test_the_public_surface_is_exactly_the_allowed_reads() -> None:
    assert public_methods() == set(READ_ONLY_METHODS) | LIFECYCLE_METHODS


def test_every_allowed_method_actually_exists() -> None:
    """A typo in the allow-list would make the test above vacuous."""
    for name in READ_ONLY_METHODS:
        assert callable(getattr(GaggimateClient, name)), name


def test_no_method_name_suggests_a_write() -> None:
    """A second net, cast wider than the exact list, for the obvious spellings."""
    forbidden = ("save", "delete", "write", "post", "set_", "update", "start_ota", "select")
    offenders = [name for name in public_methods() if any(name.startswith(p) for p in forbidden)]
    assert not offenders, offenders


def test_the_raw_sender_is_private() -> None:
    """`_send` is the only thing that can put an arbitrary frame on the wire."""
    assert hasattr(GaggimateClient, "_send")
    assert "_send" not in public_methods()


def test_no_write_request_type_appears_anywhere_in_the_client() -> None:
    """Not even in a helper, a constant or a docstring's example call.

    A source grep rather than an API check, because the way a write sneaks back
    in is somebody adding `req:profiles:save` to a private helper that a public
    read then calls.
    """
    from gaggiclanker.device import client as module

    source = inspect.getsource(module)
    writes = (
        # Profiles: everything that mutates the machine's profile store or its
        # selection, including the two that only change a flag.
        '"req:profiles:save"',
        '"req:profiles:delete"',
        '"req:profiles:select"',
        '"req:profiles:reorder"',
        '"req:profiles:favorite"',
        '"req:profiles:unfavorite"',
        # History: deletion is unrecoverable and a note write overwrites the
        # index's rating and volume as a side effect.
        '"req:history:delete"',
        '"req:history:notes:save"',
        '"req:history:rebuild"',
        # Anything that moves the hardware or the firmware.
        '"req:ota-start"',
        '"req:autotune-start"',
        '"req:process:activate"',
        '"req:process:deactivate"',
        '"req:grind:activate"',
        '"req:flush:start"',
        '"req:flush:stop"',
        '"req:change-mode"',
        '"req:change-brew-target"',
        '"req:raise-temp"',
        '"req:lower-temp"',
    )
    found = [w for w in writes if w in source]
    assert not found, found
