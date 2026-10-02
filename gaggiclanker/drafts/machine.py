"""What the profile board's write phase does to the machine's own profile list.

The rule this module exists for: **the machine is read again before every write, and
nothing the archive remembers about it is trusted without that read.** The display is
edited by hand, reset by an update and rolled over by its own housekeeping; the mirror is
as old as the last sync and describes a machine that may no longer exist.

Three operations, all built on the client's gated writes so the switch, the audit and the
delete guard apply to every one of them:

* :func:`read_machine` lists the profiles and loads each one in full (reads);
* :func:`place` puts a document on the machine unless a profile with the same canonical
  content is already there, and verifies what it saved by reading it back;
* :func:`remove_profile` takes a profile off the machine, only when it still holds exactly
  what the archive recorded for it (a fresh load, twice: a file edited on the display since
  the last read is never deleted unseen), and only after the selection (and, for a
  replacement, the favourite star) has been moved to whatever takes over.

A failure at any step is a value describing why the profile was kept, never an exception
that leaves the caller guessing what state the machine is in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.domain.models import (
    Profile,
    canonical_profile_json,
    profile_content_hash,
)

__all__ = [
    "CHANGED_SINCE",
    "GONE",
    "NO_SUCCESSOR",
    "MachineState",
    "Placed",
    "Removal",
    "place",
    "read_machine",
    "remove_profile",
]

log = structlog.get_logger(__name__)

#: The reasons a profile is left on the machine, spelled once: the push result, the
#: rollback result and the tests all use these.
CHANGED_SINCE = "changed on the display since"
#: Why a profile that is selected on the machine stays: removing it would leave the display
#: naming a file that is gone, and no other profile is there to take the selection.
NO_SUCCESSOR = "selected on the machine and no other profile is on the machine to select instead"
GONE = "no longer on the machine"


@dataclass
class MachineState:
    """Every profile the machine holds right now, loaded in full, by id."""

    profiles: dict[str, Profile] = field(default_factory=dict)
    #: Ids the list named whose load then failed. Not on the machine as far as
    #: ``profiles`` goes, but not known to be gone either: a caller that acts on absence must
    #: leave these alone.
    unreadable: set[str] = field(default_factory=set)

    def id_with_content(self, content_hash: str) -> str | None:
        """The id of a profile holding exactly this canonical content, under any id."""
        for device_id, profile in self.profiles.items():
            if profile_content_hash(profile) == content_hash:
                return device_id
        return None


async def read_machine(client: GaggimateClient) -> MachineState:
    """List the profiles, then load each: two reads, no writes.

    The list names the ids and each profile is then loaded on its own, because the load
    is the read the rest of this module compares content against and the one the delete
    guard itself performs. (The list's own ``minimal`` form is not used: the client
    validates every listed item as a whole profile, and an id-and-label stub is not
    one.) A profile that disappears between the list and its load (someone deleting it
    on the display) is simply not in the state.
    """
    state = MachineState()
    for listed in await client.list_profiles():
        if not listed.id:
            continue
        try:
            state.profiles[listed.id] = await client.load_profile(listed.id)
        except DeviceError as exc:
            state.unreadable.add(listed.id)
            log.warning(
                "device_profile_unreadable", host=client.host, profile_id=listed.id, error=str(exc)
            )
    return state


@dataclass
class Placed:
    """The outcome of putting a document on the machine."""

    device_id: str
    #: What the machine serves for it now. ``None`` only when it could not be read back.
    served: Profile | None
    #: True when an identical profile was already there and nothing was saved.
    reused: bool = False
    #: Set when the save could not be verified: the profile is on the machine either way.
    problem: Literal["unreadable", "mismatch"] | None = None
    error: str = ""
    verification: dict[str, Any] | None = None


async def place(client: GaggimateClient, profile: Profile, machine: MachineState) -> Placed:
    """Save ``profile`` as a new profile unless the machine already holds its content.

    Two identical profiles on a display are clutter with no upside, and a second save
    also spends a write the safety layers would have to account for. The comparison is on
    canonical content (the label is part of it), so a renamed copy is a different profile.
    """
    existing = machine.id_with_content(profile_content_hash(profile))
    if existing is not None:
        return Placed(device_id=existing, served=machine.profiles[existing], reused=True)

    stored = await client.save_profile(profile)
    device_id = stored.id or ""
    try:
        served = await client.load_profile(device_id)
    except Exception as exc:
        # Acknowledged and unreadable: the same class of unknown as a mismatch. The
        # caller records a `failed` row naming the id so a rollback has something to remove.
        return Placed(
            device_id=device_id,
            served=None,
            problem="unreadable",
            error=f"saved, but could not be read back: {exc}",
            verification={"sent": profile.to_device(), "loaded": None},
        )
    sent_canonical = canonical_profile_json(profile)
    loaded_canonical = canonical_profile_json(served)
    if sent_canonical != loaded_canonical:
        log.warning("profile_push_mismatch", device_id=device_id, host=client.host)
        return Placed(
            device_id=device_id,
            served=served,
            problem="mismatch",
            error="the machine stored something other than what was sent",
            verification={
                "sent": profile.to_device(),
                "loaded": served.to_device(),
                "sent_canonical": json.loads(sent_canonical),
                "loaded_canonical": json.loads(loaded_canonical),
            },
        )
    machine.profiles[device_id] = served
    return Placed(device_id=device_id, served=served)


@dataclass
class Removal:
    """What happened when a profile was asked to be removed."""

    #: The profile is off the machine now (or was already, see ``gone``).
    removed: bool = False
    #: It was not on the machine to begin with.
    gone: bool = False
    #: It is a different profile from the one the caller meant (another label): not a
    #: refusal, there is simply nothing to replace, and the caller says nothing.
    unrelated: bool = False
    #: Why it was left alone, in one of the phrases above or the machine's own words.
    reason: str = ""
    #: The favourite star was moved to the successor.
    favorite_carried: bool = False
    #: The selection was moved to the successor.
    selected_carried: bool = False
    #: The machine's startup profile was this one and the firmware cleared it.
    startup_cleared: bool = False


async def _check(
    client: GaggimateClient,
    *,
    device_id: str,
    expected_hash: str,
    expected_label: str | None,
    blocked: str | None,
    successor: str | None,
) -> tuple[Removal | None, Profile | None]:
    """Everything that must hold before a profile may be removed, from a fresh read.

    ``(refusal, None)`` when it must stay (or is gone), ``(None, profile)`` when it may go.
    """
    try:
        fresh = await client.load_profile(device_id)
    except DeviceError as exc:
        # Unreadable is not the same as gone: ask the list, which is the machine's own
        # word on what it holds, rather than matching the text of an error.
        try:
            listed = {p.id for p in await client.list_profiles()}
        except DeviceError:
            return Removal(reason=f"could not be read: {exc}"), None
        if device_id not in listed:
            return Removal(gone=True, reason=GONE), None
        return Removal(reason=f"could not be read: {exc}"), None
    if expected_label is not None and fresh.label != expected_label:
        return Removal(unrelated=True), None
    if profile_content_hash(fresh) != expected_hash:
        return Removal(reason=CHANGED_SINCE), None
    if blocked is not None:
        return Removal(reason=blocked), None
    if fresh.selected and successor in (None, device_id):
        return Removal(reason=NO_SUCCESSOR), None
    return None, fresh


async def remove_profile(
    client: GaggimateClient,
    *,
    device_id: str,
    expected_hash: str,
    successor: str | None,
    expected_label: str | None = None,
    blocked: str | None = None,
    carry_favorite: bool = True,
) -> Removal:
    """Take ``device_id`` off the machine, if and only if it still holds what was recorded.

    Every profile the app has synced is the app's to manage (firmware defaults and ones made
    on the display included), so what stands between a profile and its removal is not who
    made it but whether the app has *seen* its content: the one rule is a fresh load that must
    hash to ``expected_hash``, what the archive recorded for the file. In order:

    1. a **fresh load** of the profile. Not on the machine: nothing to do;
    2. **the same profile**: with ``expected_label``, a profile carrying another label
       is somebody else's lineage and is not this push's to replace;
    3. **unchanged**: the fresh content hashes to ``expected_hash``. Anything else was edited
       on the display since the last read, and removing it would destroy that edit (the next
       read records it as a version of its profile, and the sync after decides);
    4. **not somebody else's**: ``blocked`` names another board profile still standing on
       it, and is the reason it stays; and the selected profile is never removed without a
       ``successor`` to take the selection;
    5. the selection (and, with ``carry_favorite``, the favourite star) moves to
       ``successor``, so the display looks the same to the person standing at it;
    6. a **second fresh load** and the same content check, immediately before the delete,
       so an edit made on the display while steps 1 to 5 ran is not destroyed;
    7. the delete, through the client's own guard, which checks the content once more.

    Any refusal or failure at 1 to 7, the write gate's included, leaves the profile where
    it is and says why.
    """
    refusal, fresh = await _check(
        client,
        device_id=device_id,
        expected_hash=expected_hash,
        expected_label=expected_label,
        blocked=blocked,
        successor=successor,
    )
    if refusal is not None or fresh is None:
        return refusal or Removal(reason=CHANGED_SINCE)  # `fresh` is None only with a refusal

    result = Removal()
    startup_was_this = await _startup_profile(client) == device_id
    try:
        if successor is not None and successor != device_id:
            if fresh.favorite and carry_favorite:
                await client.favorite_profile(successor)
                result.favorite_carried = True
            if fresh.selected:
                await client.select_profile(successor)
                result.selected_carried = True
        again = await client.load_profile(device_id)
        if profile_content_hash(again) != expected_hash:
            result.reason = CHANGED_SINCE
            return result
        await client.delete_profile(device_id, expected_hash=expected_hash)
    except DeviceError as exc:  # includes DeviceWriteRefused
        # Whatever was carried stays carried: both profiles are on the machine and the
        # successor is favourited/selected as well, which is harmless.
        result.reason = str(exc)
        return result
    result.removed = True
    # The firmware clears its startup-profile setting when that profile is deleted, and
    # this app never writes settings: it can only report it.
    result.startup_cleared = startup_was_this and await _startup_profile(client) != device_id
    return result


async def _startup_profile(client: GaggimateClient) -> str | None:
    """The machine's startup-profile setting, or ``None`` when it cannot be read."""
    try:
        settings = await client.get_settings()
    except Exception:
        return None
    value = settings.get("startupProfile")
    return value if isinstance(value, str) and value else None
