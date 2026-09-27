"""What a chat is told about a shot: the catalogue, its tiers, and one renderer.

:mod:`~gaggiclanker.shotinfo.catalogue` lists every item a shot carries, with
its meaning and its tier; :mod:`~gaggiclanker.shotinfo.render` loads shots and
writes them at a tier. Everything that shows a model a shot goes through both.
"""

from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUPS,
    ITEMS,
    Item,
    ShotTier,
    Tier,
    default_tiers,
    effective_tiers,
    keys_in,
)
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.render import load_shots, needs_samples, render_shot, shot_lines

__all__ = [
    "CATALOGUE",
    "GROUPS",
    "ITEMS",
    "Item",
    "ShotFacts",
    "ShotTier",
    "Tier",
    "default_tiers",
    "effective_tiers",
    "keys_in",
    "load_shots",
    "needs_samples",
    "render_shot",
    "shot_lines",
]
