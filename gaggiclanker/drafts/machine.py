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
* :func:`remove_if_ours` takes a profile off the machine, only when it is one this app
  saved, only when it still holds exactly what the archive recorded for it, and only after
  the favourite star and the selection have been moved to whatever replaces it.

A failure at any step is a value describing why the profile was kept, never an exception
that leaves the caller guessing what state the machine is in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from gaggiclanker.db.repos.device_writes import DeviceWritesRepository
from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.errors import DeviceError
from gaggiclanker.domain.models import (
    APP_PROFILE_SUFFIX,
    Profile,
    canonical_profile_json,
    profile_content_hash,
)

__all__ = [
    "CHANGED_SINCE",
    "GONE",
    "NOT_OURS",
    "MachineState",
    "Placed",
    "Removal",
    "can_remove",
    "place",
    "read_machine",
    "remove_if_ours",
]

log = structlog.get_logger(__name__)

#: The reasons a profile is left on the machine, spelled once: the push result, the
#: rollback result and the tests all use these.
NOT_OURS = "not created by this app"
CHANGED_SINCE = "changed on the display since"
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
    writes: DeviceWritesRepository,
    *,
    device_id: str,
    expected_hash: str | None,
    expected_label: str | None,
    blocked: str | None,
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
    labelled = fresh.label.rstrip().endswith(APP_PROFILE_SUFFIX.strip())
    if not labelled or not await writes.created_by_us(device_id, host=client.host):
        return Removal(reason=NOT_OURS), None
    if expected_hash is not None and profile_content_hash(fresh) != expected_hash:
        return Removal(reason=CHANGED_SINCE), None
    if blocked is not None:
        return Removal(reason=blocked), None
    return None, fresh


async def can_remove(
    client: GaggimateClient,
    writes: DeviceWritesRepository,
    *,
    device_id: str,
    expected_hash: str | None,
    blocked: str | None = None,
) -> Removal | None:
    """``None`` when :func:`remove_if_ours` would go ahead; otherwise why it would not.

    Reads only: for a caller that has to know a removal would be refused before it does
    something else first, so it never puts a copy back beside a profile it is then unable
    to remove.
    """
    refusal, _ = await _check(
        client,
        writes,
        device_id=device_id,
        expected_hash=expected_hash,
        expected_label=None,
        blocked=blocked,
    )
    return refusal


async def remove_if_ours(
    client: GaggimateClient,
    writes: DeviceWritesRepository,
    *,
    device_id: str,
    expected_hash: str | None,
    successor: str | None,
    expected_label: str | None = None,
    blocked: str | None = None,
) -> Removal:
    """Take ``device_id`` off the machine, if and only if it is safe to.

    In order, and the order is the safety:

    1. a **fresh load** of the profile. Not on the machine: nothing to do;
    2. **the same profile**: with ``expected_label``, a profile carrying another label
       is somebody else's lineage and is not this push's to replace;
    3. **ours**: the audit shows this box saved that id on this host, and the label on
       the machine right now carries the app suffix. A person's own profile is never
       removed, whatever the archive thinks it is;
    4. **unchanged**: the fresh content hashes to ``expected_hash``, what the archive
       recorded for it. Anything else was edited on the display, and removing it would
       destroy that edit. ``None`` skips this check, for the one profile whose content
       is known to differ from the record (a push whose read-back failed);
    5. **not somebody else's**: ``blocked`` names another draft or Set still standing
       on it, and is the reason it stays;
    6. the favourite star and the selection move to ``successor``, so the display looks
       the same to the person standing at it;
    7. a **second fresh load** and the same content check, immediately before the delete,
       so an edit made on the display while steps 1 to 6 ran is not destroyed;
    8. the delete, through the client's own guard.

    Any refusal or failure at 1 to 8, the write gate's included, leaves the profile where
    it is and says why.
    """
    refusal, fresh = await _check(
        client,
        writes,
        device_id=device_id,
        expected_hash=expected_hash,
        expected_label=expected_label,
        blocked=blocked,
    )
    if refusal is not None or fresh is None:
        return refusal or Removal(reason=NOT_OURS)  # `fresh` is None only with a refusal

    result = Removal()
    startup_was_this = await _startup_profile(client) == device_id
    try:
        if successor is not None and successor != device_id:
            if fresh.favorite:
                await client.favorite_profile(successor)
                result.favorite_carried = True
            if fresh.selected:
                await client.select_profile(successor)
                result.selected_carried = True
        again = await client.load_profile(device_id)
        if expected_hash is not None and profile_content_hash(again) != expected_hash:
            result.reason = CHANGED_SINCE
            return result
        await client.delete_profile(device_id)
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
