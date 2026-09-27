"""What every shot field means, written for the model that reads them.

Most of a shot's lines are not self-explanatory — "Saturation: 0 % EARLY",
"Flow jitter: 0.02 ml/s VERY_STABLE" — and a model left to guess reads them
from its training, which knows nothing of this engine's thresholds. So every
chat that reads shots carries this glossary in its system prompt: one entry per
item that is **not excluded**, grouped as a rendering is, each marked with the
tier it is in, so the model knows what `get_shot_extended` would add and never
meets a line it was not told about.

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

from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    GROUPS,
    SHARED_BANDS,
    Item,
    Tier,
    band_text,
)

__all__ = ["render_glossary", "shared_bands_of"]

_PREAMBLE = (
    "SHOT FIELDS",
    "Base is what get_shot returns and what a Set conversation's opening context and its shot "
    "search show; extended is what get_shot_extended adds; get_shot_full returns both. A shot "
    "is written as `shot <id>`, then `[Group]` headings with `label: value` lines; the brew "
    "phases are every phase but pre-infusion.",
    "A line that is missing means the machine did not record that value (no scale, no pressure "
    "sensor, too few samples to judge) — never that it was zero.",
)


def shared_bands_of(item: Item) -> list[str]:
    """The shared tables an item's meaning names, by name, in the order it carries them."""
    return [
        name
        for table in item.bands
        for name, (shared, _) in SHARED_BANDS.items()
        if shared is table
    ]


def render_glossary(tiers: Mapping[str, Tier]) -> str:
    """Every item a chat can be shown, with its tier and what it means.

    A band table more than one item reads is written once, under
    ``[Shared bands]``, and only when an item that is shown names it; each of
    those items gives the unit its value is in.
    """
    shown = [item for item in CATALOGUE if tiers.get(item.key, "excluded") != "excluded"]
    named = {name for item in shown for name in shared_bands_of(item)}
    lines = list(_PREAMBLE)
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
            for item in shown
            if item.group == group
        ]
        if not entries:
            continue
        lines += ["", f"[{group}]"]
        if group in GROUP_NOTES:
            lines.append(GROUP_NOTES[group])
        lines += entries
    return "\n".join(lines)
