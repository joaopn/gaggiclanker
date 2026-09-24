"""Two small rules about a new Set's words and numbers, shared by every path that makes one.

The starting-point wizard and the design conversation both turn a suggestion
into a Set's first recipe, and both have to agree on what the Set is called and
when a grind setting is also a number. One copy each, here, so the two paths
cannot drift: a Set named one way when it came from the wizard and another when
it came from the chat, or a grind chart that plots a "two clicks finer" from
one of them, would be the same rule written twice and kept once.

Pure: no database, no FastAPI.
"""

from __future__ import annotations

__all__ = ["grind_value", "set_name"]

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
