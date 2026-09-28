"""Settings → Shot information: every item, the tier it is in, and what that costs.

The page is where a person decides what a chat is told about each shot, so it
shows three things per item beside the choice: what the item means (the same
text the glossary gives the model), its default, and its value on a real shot
of this archive, written by the renderer itself. And it shows what the choice
costs, as approximate tokens, measured on that same shot's renderings at the
tiers as they stand: a person moving the curve into base should see the
autoload jump before the next conversation pays for it.

The document is answered whole by every route — read, move an item, reset —
so the page takes one response and its estimates are never a step behind its
tiers.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shot_info import ShotInfoTiersRepository, ShotInfoTierWrite
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.infra.errors import NotFound, Unprocessable
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.shotinfo.catalogue import (
    CATALOGUE,
    GROUP_NOTES,
    GROUPS,
    ITEMS,
    ShotTier,
    Tier,
    effective_tiers,
)
from gaggiclanker.shotinfo.facts import ShotFacts
from gaggiclanker.shotinfo.glossary import render_glossary
from gaggiclanker.shotinfo.render import item_example, load_shots, render_shot

__all__ = [
    "CHARS_PER_TOKEN",
    "ExampleShot",
    "ShotInfoGroup",
    "ShotInfoItem",
    "ShotInformation",
    "ShotInformationService",
    "TokenEstimates",
    "approximate_tokens",
]

#: A rough, provider-neutral rate for English-like text with numbers in it.
#: The estimates are there to compare one choice with another, not to bill:
#: every provider tokenises differently, and none of them is asked.
CHARS_PER_TOKEN = 3.5


def approximate_tokens(text: str) -> int:
    """Characters over :data:`CHARS_PER_TOKEN`, rounded."""
    return round(len(text) / CHARS_PER_TOKEN)


class ShotInfoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    #: What the item's line starts with in a rendering.
    label: str
    meaning: str
    default_tier: Tier
    tier: Tier
    locked: bool
    #: The item's value on the example shot, as the renderer writes it, or
    #: ``None`` when that shot does not have it (or there is no shot).
    example: str | None


class ShotInfoGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    #: What the group says once for all its rows, as the glossary says it.
    note: str | None
    items: list[ShotInfoItem]


class ExampleShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: int
    started_at: str | None
    judged: bool


class TokenEstimates(BaseModel):
    """Approximate tokens, measured on the example shot at the current tiers.

    The per-shot figures and the autoload are ``None`` with no shot to measure;
    the glossary is text of its own and is always given.
    """

    model_config = ConfigDict(extra="forbid")

    base_per_shot: int | None
    extended_per_shot: int | None
    full_per_shot: int | None
    glossary: int
    #: Base per shot times `recent_shots`: what a Set conversation's opening
    #: context spends on its shots, on every turn.
    autoload: int | None
    #: The `chatRecentShots` setting.
    recent_shots: int
    #: The `chatCurvePoints` setting: about how many rows the curve in the
    #: extended and full figures is cut to.
    curve_points: int


class ShotInformation(BaseModel):
    """`GET /api/shot-information`: the whole page."""

    model_config = ConfigDict(extra="forbid")

    groups: list[ShotInfoGroup]
    example_shot: ExampleShot | None
    estimates: TokenEstimates


class ShotInformationService:
    """Reads and moves the tiers, and measures them on the example shot."""

    def __init__(self, db: Database, settings: SettingsService) -> None:
        self.db = db
        self.settings = settings
        self.tiers = ShotInfoTiersRepository(db)

    async def document(self) -> ShotInformation:
        tiers = await effective_tiers(self.db)
        example = await ShotsRepository(self.db).example_shot()
        # With the samples: the curve's examples and the extended estimate
        # both need them, and it is one shot.
        loaded = await load_shots(self.db, [example.id], samples=True) if example else []
        facts = loaded[0] if loaded else None
        recent = int(await self.settings.get("chatRecentShots"))
        curve_points = int(await self.settings.get("chatCurvePoints"))
        shown = (
            ExampleShot(shot_id=example.id, started_at=example.started_at, judged=example.judged)
            if example is not None and facts is not None
            else None
        )
        return ShotInformation(
            groups=_groups(tiers, facts, curve_points),
            example_shot=shown,
            estimates=_estimates(tiers, facts, recent, curve_points),
        )

    async def set_tier(self, key: str, tier: Tier) -> ShotInformation:
        item = ITEMS.get(key)
        if item is None:
            raise NotFound("No item of shot information by that key")
        if item.locked:
            raise Unprocessable(
                f"{item.name} is locked in {item.default_tier}: the agent cannot search or "
                "cite shots without it",
                code="LOCKED_ITEM",
                details={"field": "key", "message": "a locked item stays in its tier"},
            )
        await self.tiers.set_tier(ShotInfoTierWrite(item_key=key, tier=tier))
        return await self.document()

    async def reset(self) -> ShotInformation:
        await self.tiers.reset()
        return await self.document()


def _groups(
    tiers: Mapping[str, Tier], facts: ShotFacts | None, curve_points: int
) -> list[ShotInfoGroup]:
    return [
        ShotInfoGroup(
            name=group,
            note=GROUP_NOTES.get(group),
            items=[
                ShotInfoItem(
                    key=item.key,
                    name=item.name,
                    label=item.label,
                    meaning=item.meaning,
                    default_tier=item.default_tier,
                    tier=tiers[item.key],
                    locked=item.locked,
                    example=(
                        item_example(facts, item.key, curve_points=curve_points)
                        if facts is not None
                        else None
                    ),
                )
                for item in CATALOGUE
                if item.group == group
            ],
        )
        for group in GROUPS
    ]


def _estimates(
    tiers: Mapping[str, Tier], facts: ShotFacts | None, recent_shots: int, curve_points: int
) -> TokenEstimates:
    glossary = approximate_tokens(render_glossary(tiers))
    if facts is None:
        return TokenEstimates(
            base_per_shot=None,
            extended_per_shot=None,
            full_per_shot=None,
            glossary=glossary,
            autoload=None,
            recent_shots=recent_shots,
            curve_points=curve_points,
        )

    def cost(tier: ShotTier) -> int:
        return approximate_tokens(render_shot(facts, tier, tiers, curve_points=curve_points))

    base = cost("base")
    return TokenEstimates(
        base_per_shot=base,
        extended_per_shot=cost("extended"),
        full_per_shot=cost("full"),
        glossary=glossary,
        autoload=base * recent_shots,
        recent_shots=recent_shots,
        curve_points=curve_points,
    )
