"""A profile's name on the machine: its own name, and the version of its Recipe after it.

The name a person gave a profile never carries a version. What the machine shows for a profile
that a Recipe brews is that name, a space and the Recipe's current version label ("Alma Lever
v1.3"), so the person at the machine sees which version it is running. One pure function derives
it, and the functions beside it read a machine's name back: the app's own name for the file (with
any version after it) is not a rename made at the machine, and a rename made there is cut back to
the person's name only by the suffix the app itself expected or recorded, never by a pattern.
"""

from __future__ import annotations

import re

__all__ = [
    "app_suffix",
    "is_app_name",
    "is_app_name_in_shot_header",
    "machine_name",
    "name_taken_from",
]

#: What a version label looks like ("v1", "v1.3"): the Recipe's own rule, in one place.
_VERSION_LABEL = re.compile(r"v\d+(?:\.\d+)?")

#: The start of a version label, as far as a cut name can show it ("", "v", "v1", "v1.").
_VERSION_LABEL_START = re.compile(r"(?:v\d*(?:\.\d*)?)?")

#: A name cut to the shot header's 47 bytes is at least this long: the cut falls on a character
#: boundary, so at most three bytes are lost to a character straddling it.
_CUT_AT_LEAST = 47 - 3


def machine_name(label: str, version_label: str | None) -> str:
    """The name the machine shows for a profile: the label, then the Recipe's current version
    label when the profile has a Recipe with a version, else the label alone."""
    return label if version_label is None else f"{label} {version_label}"


def app_suffix(name: str, label: str) -> str:
    """The version suffix (" v1.3") the app put after ``label`` in ``name``, else ``""``.

    Only the profile's own name followed by a space and a well-formed version label counts: a
    profile literally called "Foo v1.3" has no suffix as itself, since ``name`` is then its label.
    """
    if name.startswith(f"{label} ") and _VERSION_LABEL.fullmatch(name[len(label) + 1 :]):
        return name[len(label) :]
    return ""


def is_app_name(name: str, label: str) -> bool:
    """Whether ``name`` is the profile's own name, with or without a well-formed version after it:
    what the app wrote (a stale or edited suffix is put right by the next save, never taken as a
    rename)."""
    return name == label or bool(app_suffix(name, label))


def name_taken_from(seen: str, label: str, expected: str, recorded: str | None) -> str:
    """The profile's name when the machine renamed its file to ``seen``: ``seen`` with the suffix
    the app expected or last recorded cut off when ``seen`` ends with exactly that suffix, else
    ``seen`` as it is. Never a generic pattern."""
    for suffix in (app_suffix(expected, label), app_suffix(recorded or "", label)):
        if suffix and seen.endswith(suffix) and seen[: -len(suffix)].strip():
            return seen[: -len(suffix)]
    return seen


def is_app_name_in_shot_header(name_on_device: str, label: str) -> bool:
    """Whether a shot's recorded profile name is the profile's own name with a version after it,
    as the machine wrote it into the header: whole, or cut at the header's 47 bytes (the cut may
    leave the label's space or part of the version label, and a trailing U+FFFD is the half of a
    character the cut straddled)."""
    name = name_on_device.rstrip("\ufffd")
    if is_app_name(name, label):
        return True
    if len(name.encode()) < _CUT_AT_LEAST or not name.startswith(label):
        return False
    rest = name[len(label) :]
    return rest.startswith(" ") and bool(_VERSION_LABEL_START.fullmatch(rest[1:]))
