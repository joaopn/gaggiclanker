"""Small rules about a Set's words and numbers, shared by every path that needs them.

The starting-point wizard and the design conversation both turn a suggestion
into a Set's first recipe, and both have to agree on what the Set is called and
when a grind setting is also a number. One copy each, here, so the two paths
cannot drift: a Set named one way when it came from the wizard and another when
it came from the chat, or a grind chart that plots a "two clicks finer" from
one of them, would be the same rule written twice and kept once.

The same goes for a version's **name**. A Set version is v<major>.<minor>: a
dial-in change (grind, dose, yield, a profile draft that tunes a parameter)
keeps the major and takes the next minor, and a functional change to what the
profile does starts the next major. Every path that appends a version asks
:func:`change_is_major` what its default is, and every place that shows one
asks :func:`version_label` how it reads.

Pure: no database, no FastAPI.
"""

from __future__ import annotations

import re
from typing import Literal

__all__ = [
    "VersionPath",
    "change_is_major",
    "grind_value",
    "next_version_name",
    "parse_version_label",
    "set_name",
    "version_label",
]

#: Which kind of write is appending a version, for :func:`change_is_major`.
#:
#: * ``change`` — a recipe change a person or the agent spelled out: the Add a
#:   version form, an accepted proposal, an accepted analysis suggestion.
#: * ``draft`` — a profile draft pushed to the machine and recorded on the Set:
#:   a tuned copy of a profile, which is dialling in.
#: * ``rollback`` — going back to an earlier version's recipe.
type VersionPath = Literal["change", "draft", "rollback"]


def change_is_major(path: VersionPath, *, profile_changed: bool, major: bool | None) -> bool:
    """Whether a new version is a major one: the person's answer, else the default.

    The maintainer's rule: a major version is a functional change to what the
    profile does, and the person has the last word on every card and form.
    ``major`` is that word, and when it is given it wins in both directions.
    ``None`` is "nobody said", and then the default for the path applies:

    * a **change** is major exactly when it switches the Set to a different
      profile. Grind, dose and yield are dialling in.
    * a pushed **draft** is minor. It is a tuned copy of a profile (a degree of
      temperature, a longer pre-infusion), which is dialling in too, unless the
      person marks it major.
    * a **roll back** follows the change rule on what it changes relative to
      the current version: going back over a grind nudge is minor, going back
      to another profile is major.

    The agent's suggestion is not an input here. It is shown on the card, which
    preselects the box, and what the person sends is ``major``.
    """
    if major is not None:
        return major
    if path == "draft":
        return False
    return profile_changed


def version_label(major: int, minor: int) -> str:
    """ "v1", "v1.1", "v2" — how a version is named wherever it is read.

    Minor 0 is left off: every version that existed before minor versions did
    is N.0 and keeps reading "vN", which is what every chat and prediction
    written until then calls it. The views repeat this rule in SQL
    (migration 0027), pinned to this function by a test.
    """
    return f"v{major}" if minor == 0 else f"v{major}.{minor}"


def next_version_name(
    *, current_major: int, current_major_minor_max: int, highest_major: int, major: bool
) -> tuple[int, int]:
    """The (major, minor) the next version of a Set takes.

    A **minor** keeps the current version's major and takes the highest minor
    within that major + 1 (v1.2 → v1.3). A **major** takes the Set's highest
    major + 1 at minor 0 (v1.2 → v2). The first version of a Set is a major
    from nothing (all zeros), which is v1.
    """
    if major:
        return highest_major + 1, 0
    return current_major, current_major_minor_max + 1


_LABEL = re.compile(r"^\s*[vV]?\s*(\d{1,6})(?:\.(\d{1,6}))?\s*$")


def parse_version_label(text: str) -> tuple[int, int] | None:
    """A version's name as somebody would type it, as (major, minor); ``None`` if it is not one.

    "v1.1", "1.1", "V2" and "2" are all accepted, and "2" is v2 (2.0), never
    the second version: the name is what a reader sees, and the ordinal is a
    number nobody is shown. A major of 0 names nothing.
    """
    found = _LABEL.match(text)
    if found is None:
        return None
    major = int(found.group(1))
    minor = int(found.group(2) or 0)
    if major < 1:
        return None
    return major, minor


#: The range `set_versions.grind_value` accepts. A number outside it is not a
#: dial reading anybody has, and is left as text only.
_GRIND_VALUE_MAX = 10000


def set_name(bean_name: str | None, grinder_name: str | None) -> str:
    """ "Ethiopia Guji on the Niche Zero" — the bag and the grinder.

    The bag alone would collide the day somebody runs the same bean on a second
    grinder, which is exactly the comparison both paths invite. Capped at the
    200 characters a Set's name may have.
    """
    bean = (bean_name or "New bean").strip()
    grinder = (grinder_name or "").strip()
    name = f"{bean} on the {grinder}" if grinder else bean
    return name[:200]


def grind_value(setting: str, *, absolute: bool) -> float | None:
    """The numeric half of a grind, when the setting really is a number.

    Only for an absolute setting: parsing "two clicks finer than usual" into 2
    and plotting it on the Set's grind chart would draw a line that means
    nothing. ``absolute`` is the model's own claim and the text still has to
    parse, so both have to hold.

    **The first number wins**, which is a deliberate choice rather than an
    oversight. `set_versions` keeps the reading as text *and* as a number for
    exactly this reason (migration 0005): a Mazzer's "between 3 and 4" is what
    the user reads back, and 3 is what a chart plots. Taking the first number
    puts the point at the bottom of the stated range every time, which is at
    least consistent; averaging to 3.5 would invent a precision the dial does
    not have, and refusing to parse it at all would leave the Set's grind chart
    with a hole wherever somebody owns that grinder.
    """
    if not absolute:
        return None
    for token in setting.strip().split():
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        return value if 0 <= value <= _GRIND_VALUE_MAX else None
    return None
