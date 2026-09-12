"""Profile versioning: the same profile twice is one row, an edit is a new one.

The trap this file exists for: the firmware's `writeProfile` does not round-trip
its input textually. It always emits a `transition` even where the author wrote
none, always adds `transition.target`, spells out a phase `temperature` of 0,
and stamps its own `id`, `favorite` and `selected`. Hash the raw document and
every profile appears to change the first time the machine touches it, and the
small_archive fills with phantom versions nobody made.
"""

from __future__ import annotations

import json
from typing import Any

from gaggiclanker.domain.models import Profile, profile_content_hash
from gaggiclanker.sync.engine import PROFILE_UPDATED_EVENT
from tests.sync.conftest import Archive


def _as_the_firmware_writes_it(raw: dict[str, Any]) -> None:
    """Rewrite a profile document the way `writeProfile` (profile.h:335-410) would.

    Every field the firmware fills in for free, applied in place: the implied
    `transition` with its undocumented `target`, the explicit phase
    `temperature` of 0 that means "inherit", and the `selected`/`favorite` flags
    it stamps on from NVS as it serialises. None of it changes what the profile
    brews, so none of it may reach the content hash.
    """
    for phase in raw["phases"]:
        phase.setdefault("transition", {"type": "instant", "duration": 0, "adaptive": False})
        phase["transition"].setdefault("target", "time")
        phase.setdefault("temperature", 0)
    raw["selected"] = not raw.get("selected", False)
    raw["favorite"] = not raw.get("favorite", False)


async def _versions(small_archive: Archive) -> int:
    return int(await small_archive.db.fetch_value("SELECT COUNT(*) FROM profile_versions") or 0)


async def test_the_mirror_lands(small_archive: Archive) -> None:
    run = await small_archive.engine.sync_profiles(trigger="test")

    assert run.status == "ok"
    assert run.profiles_changed == len(small_archive.device.profiles)
    mirrored = await small_archive.engine.profiles.list_device_profiles()
    assert {p.device_id for p in mirrored} == {p["id"] for p in small_archive.device.profiles}
    assert all(p.label for p in mirrored)


async def test_the_same_json_twice_is_one_version(small_archive: Archive) -> None:
    await small_archive.engine.sync_profiles(trigger="test")
    before = await _versions(small_archive)

    second = await small_archive.engine.sync_profiles(trigger="test")

    assert await _versions(small_archive) == before
    assert second.profiles_changed == 0, "an unchanged machine is not news every 15 minutes"


async def test_the_firmwares_own_round_trip_is_not_a_change(small_archive: Archive) -> None:
    """Re-serve a profile the way `writeProfile` would, and nothing has changed.

    This is the real-world case: the machine re-emits every profile on every
    `req:profiles:list`, with its own defaults filled in.
    """
    await small_archive.engine.sync_profiles(trigger="test")
    before = await _versions(small_archive)

    for raw in small_archive.device.profiles:
        _as_the_firmware_writes_it(raw)

    await small_archive.engine.sync_profiles(trigger="test")

    assert await _versions(small_archive) == before


async def test_an_edited_label_is_a_new_version(small_archive: Archive) -> None:
    """A rename *is* a change: the name is what the `.slog` header records.

    Two shots that say "Adaptive v2" should mean the same profile, so renaming
    one has to produce a version the small_archive can tell apart.
    """
    await small_archive.engine.sync_profiles(trigger="test")
    before = await _versions(small_archive)
    target = small_archive.device.profiles[0]
    original_version = await small_archive.engine.profiles.get_device_profile(1, target["id"])
    assert original_version is not None
    target["label"] = "Renamed by the user"

    run = await small_archive.engine.sync_profiles(trigger="test")

    assert await _versions(small_archive) == before + 1
    assert run.profiles_changed == 1
    current = await small_archive.engine.profiles.get_device_profile(1, target["id"])
    assert current is not None
    assert current.current_version_id != original_version.current_version_id

    # The old version is kept, not overwritten: shots from before the rename
    # still point at what they were actually brewed with.
    old = await small_archive.engine.profiles.get_version(original_version.current_version_id)
    assert old is not None
    assert old.label != "Renamed by the user"


async def test_an_edited_phase_is_a_new_version(small_archive: Archive) -> None:
    await small_archive.engine.sync_profiles(trigger="test")
    before = await _versions(small_archive)
    target = small_archive.device.profiles[0]
    target["phases"][0]["duration"] = float(target["phases"][0]["duration"]) + 3

    await small_archive.engine.sync_profiles(trigger="test")

    assert await _versions(small_archive) == before + 1


async def test_a_profile_removed_from_the_machine_is_tombstoned(small_archive: Archive) -> None:
    """Never deleted: a year of shots resolves through this row."""
    await small_archive.engine.sync_profiles(trigger="test")
    removed = small_archive.device.profiles.pop()

    await small_archive.engine.sync_profiles(trigger="test")

    gone = await small_archive.engine.profiles.get_device_profile(1, removed["id"])
    assert gone is not None
    assert gone.deleted_at is not None

    visible = await small_archive.engine.profiles.list_device_profiles()
    assert removed["id"] not in {p.device_id for p in visible}
    with_deleted = await small_archive.engine.profiles.list_device_profiles(include_deleted=True)
    assert removed["id"] in {p.device_id for p in with_deleted}


async def test_a_restored_profile_loses_its_tombstone(small_archive: Archive) -> None:
    await small_archive.engine.sync_profiles(trigger="test")
    removed = small_archive.device.profiles.pop()
    await small_archive.engine.sync_profiles(trigger="test")

    small_archive.device.profiles.append(removed)
    await small_archive.engine.sync_profiles(trigger="test")

    back = await small_archive.engine.profiles.get_device_profile(1, removed["id"])
    assert back is not None
    assert back.deleted_at is None


async def test_an_empty_list_does_not_tombstone_the_mirror(small_archive: Archive) -> None:
    """An empty `req:profiles:list` is a failed read, not a wiped machine.

    The firmware creates a Default profile on an empty filesystem, so it never
    genuinely has none. Acting on an empty answer would tombstone every profile
    the small_archive holds the moment a list arrives short.
    """
    await small_archive.engine.sync_profiles(trigger="test")
    small_archive.device.profiles.clear()

    await small_archive.engine.sync_profiles(trigger="test")

    assert await small_archive.engine.profiles.list_device_profiles()


async def test_shots_are_linked_to_the_profile_they_were_brewed_with(
    small_archive: Archive,
) -> None:
    """Shots usually arrive before the mirror; whichever runs second links them."""
    header_profile_id = "pPdHFJ0HBq"  # tests/fixtures/slog/shot_196_baseline_high.slog
    small_archive.device.profiles.append(
        {
            "id": header_profile_id,
            "label": "Amigo Alturas Classic [AI]",
            "type": "standard",
            "description": "",
            "temperature": 93,
            "phases": [{"name": "Pump", "phase": "brew", "valve": 1, "duration": 28, "pump": 100}],
        }
    )
    await small_archive.engine.sync_shots(trigger="test")

    unlinked = await small_archive.engine.shots.list_shots(limit=200)
    assert all(row.profile_version_id is None for row in unlinked.items)

    await small_archive.engine.sync_profiles(trigger="test")

    linked = await small_archive.engine.shots.list_shots(limit=200)
    matched = [row for row in linked.items if row.profile_id_on_device == header_profile_id]
    assert matched
    assert all(row.profile_version_id is not None for row in matched)
    assert all(row.profile_label == "Amigo Alturas Classic [AI]" for row in matched)


async def test_the_stored_version_is_the_canonical_form(small_archive: Archive) -> None:
    await small_archive.engine.sync_profiles(trigger="test")

    version = await small_archive.engine.profiles.get_version(1)
    assert version is not None
    assert version.profile is not None
    # Device-owned fields are not part of what a profile brews, so they are not
    # in the canonical document and cannot reach the hash.
    assert "id" not in version.profile
    assert "favorite" not in version.profile
    assert "selected" not in version.profile
    assert version.content_hash == profile_content_hash(
        Profile.model_validate({**version.profile, "id": "x"})
    )
    # The raw document the device served is kept beside it, for reference.
    assert version.device_profile is not None
    assert "id" in version.device_profile


async def test_sse_announces_a_changed_profile(small_archive: Archive) -> None:
    await small_archive.engine.sync_profiles(trigger="test")
    small_archive.drain()
    small_archive.device.profiles[0]["label"] = "Something else"

    await small_archive.engine.sync_profiles(trigger="test")

    assert [e for e in small_archive.drain() if e.event == PROFILE_UPDATED_EVENT]


async def test_a_profile_that_does_not_validate_is_skipped_not_fatal(
    small_archive: Archive,
) -> None:
    """One odd profile must not cost the other twenty.

    The client drops profiles that fail validation with a warning; what this
    pins is that the mirror still lands for everything else.
    """
    small_archive.device.profiles.append(json.loads('{"id": "broken", "label": "No phases"}'))

    run = await small_archive.engine.sync_profiles(trigger="test")

    assert run.status == "ok"
    mirrored = await small_archive.engine.profiles.list_device_profiles()
    assert "broken" not in {p.device_id for p in mirrored}
    assert len(mirrored) >= 1
