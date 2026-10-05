"""What every shot field means, written for the model that reads them.

Most of a shot's lines are not self-explanatory — "Resistance level: 1.36, from
the machine", "cup share of target 117.2 %" — and a model left to guess reads them
from its training, which knows nothing of this engine's definitions. So every
chat that reads shots is told this glossary: one entry per item that is **not
excluded**, grouped as a rendering is, each marked with the tier it is in, so
the model never meets a line it was not told about. It is rendered in two
halves. The base half is in the system prompt; the extended half, about two
thirds of the text, is in what the model is sent exactly once whenever an
extended result is: on the newest one replayed in the history (placed from
:func:`extended_meanings`), or on this answer's first extended read when the history
has none (attached by :func:`gaggiclanker.tools.builtin._extended_meanings`). An
answer with no extended result in view carries none.

It is generated from the catalogue's own meanings, so an item and its
explanation are one text with two readers (this prompt, and the settings page
that shows it beside the item).

The meanings are written from the vendored diagnostics code and from
gaggimate-mcp's shot diagnostics reference, shipped verbatim under
`knowledge/seed/docs/diagnostics/` (MIT; the licence travels with it in that
directory's ATTRIBUTION.md).

It changes only when a tier does, which suits prompt caching: the same tiers
render the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    GROUPS,
    Tier,
)

__all__ = ["EXTENDED_TOOLS", "Part", "extended_meanings", "render_glossary"]

#: The two halves of the glossary. Base rides in the system prompt of every
#: request; extended is in what the model is sent exactly once whenever an extended
#: result is: on the newest one replayed in the history, or on this answer's first
#: extended read when the history has none.
type Part = Literal["base", "extended"]

#: The tools whose results carry extended lines, so the ones that bring the
#: extended meanings with them.
EXTENDED_TOOLS = ("get_shot_extended", "get_shot_full", "compare_shots")

_PREAMBLE = (
    "SHOT FIELDS",
    "Base is what get_shot returns and what a Set conversation's opening context and its shot "
    "search show; extended is what get_shot_extended adds; get_shot_full returns both. A shot "
    "is written as `shot <id>`, then `[Group]` headings with `label: value` lines; the brew "
    "phases are every phase but pre-infusion.",
    "A line that is missing means the machine did not record that value (no scale, no pressure "
    "sensor, too few samples to judge) — never that it was zero.",
    "Below are the base fields. The meanings of the extended fields are not in this prompt: "
    "they come once in the conversation, at the head of the newest get_shot_extended, "
    "get_shot_full or compare_shots result, or at the head of this answer's first such "
    "read when the conversation has none yet, before the shot lines. They apply to every "
    "extended line in the conversation.",
)

_EXTENDED_HEADING = (
    "SHOT FIELDS, EXTENDED",
    "The meanings of the extended fields, sent once in the conversation, with a shot read "
    "in detail. The preamble and group notes of the SHOT FIELDS section of the "
    "system prompt apply to them too.",
)


def extended_meanings(tiers: Mapping[str, Tier]) -> str | None:
    """The extended half as it is attached to a result, or None with nothing extended to explain.

    The one rendering both attachments use: a tool attaching it to the first
    extended read of a run, and the history placing it on the newest extended
    result it replays. So the bytes are the same wherever it lands.
    """
    if not any(tier == "extended" for tier in tiers.values()):
        return None
    return render_glossary(tiers, "extended")


def render_glossary(tiers: Mapping[str, Tier], part: Part = "base") -> str:
    """One half of the glossary: every item a chat can be shown in that tier.

    The halves are disjoint and together cover every shown item once. So is what
    a group says once for its rows: its note is written under its heading in the
    first half that has the group (base comes first, and it is always in the
    prompt). Each item gives the unit its value is in. Deterministic: the same
    tiers give the same bytes.
    """
    shown = [item for item in CATALOGUE if tiers.get(item.key, "excluded") != "excluded"]
    mine = [item for item in shown if tiers[item.key] == part]
    earlier = [item for item in shown if part == "extended" and tiers[item.key] == "base"]
    lines = list(_PREAMBLE if part == "base" else _EXTENDED_HEADING)
    for group in GROUPS:
        entries = [
            f"- {item.label} [{tiers[item.key]}]: {item.meaning}"
            for item in mine
            if item.group == group
        ]
        if not entries:
            continue
        lines += ["", f"[{group}]"]
        if group in GROUP_NOTES and not any(item.group == group for item in earlier):
            lines.append(GROUP_NOTES[group])
        lines += entries
    return "\n".join(lines)
