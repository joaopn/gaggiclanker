"""How a profile's phase name meets the one the machine logged.

The firmware writes each phase's name into the shot log in a field of 25 bytes
(`char phaseName[25]`, filled with `strncpy`, so at most 24 bytes of the name and a NUL), but the
profile holds the whole name. A phase called "Pre-infusion with a long soak" is logged as
"Pre-infusion with a long" and nothing downstream may compare the two as strings.

**One rule, used wherever a profile phase name meets a logged one**: a logged name matches a
profile phase when it equals that profile name cut to 24 bytes, on a character boundary (never
through the middle of a UTF-8 character), ignoring case and runs of spaces. Both sides are cut
before they are compared, so a name that is already cut matches itself. The cut is made on
the name as written (the firmware cuts raw bytes); case and runs of spaces are folded after.
A profile whose different names are the same within their first 24 bytes cannot be told
apart in a log, which is why :func:`clashing_names` exists: a signature refuses such a profile.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "LOGGED_NAME_BYTES",
    "clashing_names",
    "cut_logged_name",
    "phase_key",
    "raw_phase_names",
    "same_phase",
]

#: What the firmware keeps of a phase name: `char phaseName[25]`, the last byte the NUL.
LOGGED_NAME_BYTES = 24


def cut_logged_name(name: str) -> str:
    """The name as the firmware logs it: at most 24 bytes of UTF-8, never half a character."""
    raw = name.encode("utf-8")
    if len(raw) <= LOGGED_NAME_BYTES:
        return name
    return raw[:LOGGED_NAME_BYTES].decode("utf-8", errors="ignore")


def phase_key(name: str) -> str:
    """The name as phases are compared: cut as the firmware cuts it, then folded.

    The firmware cuts the **raw** bytes of the name, so the cut comes first, and only then are
    case and runs of spaces ignored (on both sides, so ``"Pre  infusion"`` and
    ``"pre infusion"`` are one name). A logged name split in the middle of a character decodes
    with a trailing replacement character, which is dropped.
    """
    cut = cut_logged_name(name).rstrip("\ufffd")
    return " ".join(cut.split()).casefold()


def raw_phase_names(profile: Mapping[str, Any] | None) -> list[str] | None:
    """A profile document's phase names exactly as the profile spells them, or ``None``.

    Not stripped: the log holds the first 24 bytes of the name as written, leading spaces
    included, so a name must be cut before it is cleaned (:func:`phase_key`). Used wherever a
    profile's names are matched against a log's; the stripped list the derivation stores
    (``profile_phase_names``) is for showing.
    """
    if profile is None:
        return None
    phases = profile.get("phases")
    if not isinstance(phases, list) or not phases:
        return None
    return [str(phase.get("name") or "") if isinstance(phase, Mapping) else "" for phase in phases]


def same_phase(left: str, right: str) -> bool:
    """Whether two names (a profile's and a logged one, in either order) are one phase."""
    return phase_key(left) == phase_key(right)


def clashing_names(names: Sequence[str]) -> list[tuple[str, str]]:
    """Pairs of *different* phase names a log could not tell apart (the same in 24 bytes).

    A profile may repeat a name on purpose (two phases called "Pressurize"; the language reads
    "the first Pressurize"), which is not a clash: the log shows the same name for both and so
    does the profile. A clash is two names that differ only beyond the 24th byte.
    """
    seen: dict[str, str] = {}
    clashes: list[tuple[str, str]] = []
    for name in names:
        key = phase_key(name)
        full = " ".join(name.split()).casefold()
        if key in seen and " ".join(seen[key].split()).casefold() != full:
            clashes.append((seen[key], name))
        seen.setdefault(key, name)
    return clashes
