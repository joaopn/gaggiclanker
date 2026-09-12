"""The client's surface is two closed lists, and this is what keeps them closed.

The list used to be one: ten reads, and a failing test if an eleventh appeared.
That was the right shape for a prototype that wrote nothing. Profile drafts and
push add five profile writes, so the question this file answers changes from "is
anything writable" to **"is exactly the agreed set writable, and is every one of
them gated"**.

Storage cleanup and notes write-back move two request types across the line, and the shape of
that move is the thing to copy if a third is ever proposed. `req:history:delete`
and `req:history:notes:save` were in the forbidden-grep list below, which is
where a request type lives while this client may not send it at all. They are
now in the "appears exactly once" list, which is where it lives once a gated
method owns it — one method, one frame, no second place that sends it. Moving an
entry between those two lists is the edit that admits the surface has grown; a
rule that let a write appear *without* that edit would be a rule that does not
hold.

The stakes have not changed. `POST /api/settings` clears every checkbox-style
boolean key the body omits (so a partial write turns off HomeKit, boiler fill
and the momentary buttons), `req:profiles:save` with a float `pump` leaves a
profile that never runs the pump, `req:history:delete` is unrecoverable — the
machine is the only copy until we have synced it, which is exactly why the gate
refuses it for any shot the archive does not already hold intact — and a profile
with zero phases crashes brew start on the display.

So: two allow-lists, a forbidden-request-type grep that still covers everything
outside them, and a check that no write method can reach `_send` except through
the gate. If you are here because this test failed, the question is not "how do
I update the list" but "does this write belong in the seven, and has it got a
rule in front of it".
"""

from __future__ import annotations

import inspect

from gaggiclanker.device.client import GATED_WRITE_METHODS, READ_ONLY_METHODS, GaggimateClient
from gaggiclanker.device.writes import DenyAllWrites, DeviceWriteGate

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


def test_the_public_surface_is_exactly_the_two_allowed_lists() -> None:
    assert public_methods() == set(READ_ONLY_METHODS) | set(GATED_WRITE_METHODS) | LIFECYCLE_METHODS


def test_the_two_lists_do_not_overlap() -> None:
    """A method in both lists would be a read nobody gates and a write nobody reads."""
    assert not READ_ONLY_METHODS & GATED_WRITE_METHODS


def test_every_allowed_method_actually_exists() -> None:
    """A typo in either allow-list would make the test above vacuous."""
    for name in READ_ONLY_METHODS | GATED_WRITE_METHODS:
        assert callable(getattr(GaggimateClient, name)), name


def test_no_read_method_name_suggests_a_write() -> None:
    """A second net, cast wider than the exact list, for the obvious spellings.

    The write verbs are still forbidden — on the *reads*. Five methods are
    allowed to be called `save_profile` and friends, and they are named
    explicitly rather than matched by prefix, which is what stops
    `save_settings` from ever looking like it belongs.
    """
    forbidden = ("save", "delete", "write", "post", "set_", "update", "start_ota", "select")
    offenders = [
        name
        for name in public_methods() - set(GATED_WRITE_METHODS)
        if any(name.startswith(prefix) for prefix in forbidden)
    ]
    assert not offenders, offenders


def test_the_raw_sender_is_private() -> None:
    """`_send` is the only thing that can put an arbitrary frame on the wire."""
    assert hasattr(GaggimateClient, "_send")
    assert "_send" not in public_methods()


def test_no_forbidden_request_type_appears_anywhere_in_the_client() -> None:
    """Not even in a helper, a constant or a docstring's example call.

    A source grep rather than an API check, because the way a write sneaks back
    in is somebody adding `req:history:rebuild` to a private helper that a
    public read then calls. The seven writes this client is allowed to make are
    absent from this list and checked separately below; everything else the
    firmware will act on is here.
    """
    from gaggiclanker.device import client as module

    source = inspect.getsource(module)
    writes = (
        # Profiles: the one mutation that is still refused. Reordering rewrites
        # the display's whole `profileOrder` for a cosmetic gain, and a partial
        # order silently drops the ids it omits.
        '"req:profiles:reorder"',
        # History: a rebuild regenerates `index.bin` from every `.slog` on the
        # machine at once, which is minutes of filesystem work and a progress
        # stream nothing here consumes. The delete and the notes save moved out
        # of this list when they were added and are pinned below instead.
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
    found = [write for write in writes if write in source]
    assert not found, found


def test_each_allowed_write_type_appears_exactly_once() -> None:
    """One frame per method, and no second place that sends it.

    A duplicate would mean a code path that reaches `_send` without going
    through the write method the audit and the gate are attached to.
    """
    from gaggiclanker.device import client as module

    source = inspect.getsource(module)
    for request_type in (
        '"req:profiles:save"',
        '"req:profiles:delete"',
        '"req:profiles:select"',
        '"req:profiles:favorite"',
        '"req:profiles:unfavorite"',
        # The two history writes. Each is sent by exactly one gated method, and
        # each has an eligibility rule in `SettingsWriteGate.authorize` that
        # runs before the frame exists.
        '"req:history:delete"',
        '"req:history:notes:save"',
    ):
        assert source.count(request_type) == 1, request_type


def test_every_write_method_goes_through_the_gate() -> None:
    """No write method calls `_send` directly; they all call `_write`.

    `_write` is where authorisation and the audit row live, so a write that
    reaches `_send` on its own is a write nobody recorded and nobody allowed.
    """
    for name in GATED_WRITE_METHODS:
        source = inspect.getsource(getattr(GaggimateClient, name))
        assert "self._send(" not in source, f"{name} reaches _send without the gate"
        assert "self._write(" in source or "refused" in source, name


def test_a_client_with_no_gate_refuses_everything() -> None:
    """The default is deny-all, so read-only is what forgetting gives you."""
    client = GaggimateClient("machine.test")
    assert isinstance(client._gate, DenyAllWrites)
    assert isinstance(client._gate, DeviceWriteGate)
