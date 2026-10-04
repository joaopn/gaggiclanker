"""What every shot field means, written for the model that reads them.

Most of a shot's lines are not self-explanatory — "Saturation: 0 % EARLY",
"Flow jitter: 0.02 ml/s VERY_STABLE" — and a model left to guess reads them
from its training, which knows nothing of this engine's thresholds. So every
chat that reads shots is told this glossary: one entry per item that is **not
excluded**, grouped as a rendering is, each marked with the tier it is in, so
the model never meets a line it was not told about. It is rendered in two
halves. The base half is in the system prompt; the extended half, about two
thirds of the text, is attached to the first successful extended read of a
run (see :func:`gaggiclanker.tools.builtin._extended_meanings`), unless the history
the run starts from already has extended reads: the history places one copy, from
:func:`extended_meanings`, on the newest of them. It is in the context exactly once
whenever extended lines are, and an answer whose history has none and that opens no
shot in detail carries none.

It is generated from the catalogue's own meanings, so an item and its
explanation are one text with two readers (this prompt, and the settings page
that shows it beside the item). The band thresholds inside those meanings are
read from the vendored tables (:func:`gaggiclanker.shotinfo.catalogue.band_text`).

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
    SHARED_BANDS,
    Item,
    Tier,
    band_text,
)

__all__ = ["EXTENDED_TOOLS", "Part", "extended_meanings", "render_glossary", "shared_bands_of"]

#: The two halves of the glossary. Base rides in the system prompt of every
#: request; extended is sent once per answer, with the first extended read.
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
    "they arrive at the head of the first result from get_shot_extended, get_shot_full or "
    "compare_shots, before the shot lines they explain, and stay in the conversation: if "
    "an earlier result in it already has them, they are not sent again.",
)

_EXTENDED_HEADING = (
    "SHOT FIELDS, EXTENDED",
    "The meanings of the extended fields, sent once with the first extended read of this "
    "answer. The preamble, group notes and band tables of the SHOT FIELDS section of the "
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


def shared_bands_of(item: Item) -> list[str]:
    """The shared tables an item's meaning names, by name, in the order it carries them."""
    return [
        name
        for table in item.bands
        for name, (shared, _) in SHARED_BANDS.items()
        if shared is table
    ]


def render_glossary(tiers: Mapping[str, Tier], part: Part = "base") -> str:
    """One half of the glossary: every item a chat can be shown in that tier.

    The halves are disjoint and together cover every shown item once. So are
    the things shared between items: a band table more than one item reads is
    written once, under ``[Shared bands]``, in the first half that needs it, and
    a group's note under its heading in the first half that has the group (base
    comes first, and it is always in the prompt). Each item gives the unit its
    value is in. Deterministic: the same tiers give the same bytes.
    """
    shown = [item for item in CATALOGUE if tiers.get(item.key, "excluded") != "excluded"]
    mine = [item for item in shown if tiers[item.key] == part]
    earlier = [item for item in shown if part == "extended" and tiers[item.key] == "base"]
    named = {name for item in mine for name in shared_bands_of(item)}
    named -= {name for item in earlier for name in shared_bands_of(item)}
    lines = list(_PREAMBLE if part == "base" else _EXTENDED_HEADING)
    if named:
        lines += [
            "",
            "[Shared bands]",
            "The band tables several fields are read against, named in their entries; each "
            "entry gives the unit.",
        ]
        lines += [
            f"- {name} bands: {band_text(table, descending=descending)}"
            for name, (table, descending) in SHARED_BANDS.items()
            if name in named
        ]
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
