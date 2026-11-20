"""The profiles the machine keeps for itself, and how many stars it will show.

Firmware 1.10 seeds two profiles with fixed ids on every boot when they are missing, a
backflush (`flush`) and a descale (`descale`); they are utility profiles, editable, and
`req:profiles:delete` refuses them (`ProfileManager::isSystemProfile`, `system_profiles.h`).
It also refuses a favourite past ten (`MAX_FAVORITED_PROFILES`). Both are facts about the machine
that the list has to respect rather than discover as failed writes, so they are named once here.

Pure: no database, no device.
"""

from __future__ import annotations

__all__ = [
    "MAX_STARRED",
    "STAR_LIMIT_SENTENCE",
    "SYSTEM_PROFILE_IDS",
    "is_system_profile",
    "kept_by_machine_sentence",
]

#: The ids the firmware seeds (`BACKFLUSH_PROFILE_ID`, `DESCALING_PROFILE_ID`).
SYSTEM_PROFILE_IDS = frozenset({"flush", "descale"})

#: `MAX_FAVORITED_PROFILES`: the machine's home screen shows at most this many starred profiles.
MAX_STARRED = 10

#: What a person is told when a star would be the eleventh.
STAR_LIMIT_SENTENCE = (
    f"The machine shows at most {MAX_STARRED} starred profiles. Take a star off another first."
)


def is_system_profile(device_id: str | None) -> bool:
    """Whether a machine file is one the firmware seeds and will not delete."""
    return device_id in SYSTEM_PROFILE_IDS


def kept_by_machine_sentence(name: str) -> str:
    """What a person is told when something would switch a system profile off the machine."""
    return f"The machine keeps its own {name} profile; it cannot be switched off."
