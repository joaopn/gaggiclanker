"""What a pattern run is told: the confirmed Set insights, the general ones, the declined.

Assembled by :func:`build_patterns_input` and nothing else, and **deterministic**: the same
rows give the same input, byte for byte, because Sets are ordered by id and so are the
insights under them, the general insights and the declined proposals. That is what makes a
run traceable (the input is stored verbatim on its row) and what the golden test pins.

What the model sees per Set insight is its id, text, the Set it belongs to with the
attributes an insight's scope may name, the version it was learned at, and what it rests on
(each version's outcome then and now, from the insight's own provenance). Not shots, not
telemetry: this call generalises lessons, it does not re-derive them.

Only **confirmed** insights are read. A waiting or dismissed one is a proposal nobody
accepted, and generalising from it would manufacture the run's own evidence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.knowledge_insights import SCOPE_KEYS, InsightScope, InsightsRepository
from gaggiclanker.db.repos.patterns import PatternProposalsRepository, PatternRunsRepository

__all__ = [
    "PatternDeclined",
    "PatternGeneral",
    "PatternInsight",
    "PatternSet",
    "PatternsInput",
    "build_patterns_input",
]


class PatternInsight(BaseModel):
    """One confirmed insight of a Set."""

    model_config = ConfigDict(extra="forbid")

    id: int
    text: str
    #: "v2", or ``None`` for an insight learned before versions were recorded.
    learned_at: str | None = None
    #: Each version it rests on, as "v3 held → now failed" (the insight's own rendering).
    rests_on: list[str] = Field(default_factory=list)


class PatternSet(BaseModel):
    """A Set with confirmed insights, and the attributes a scope may name for it."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    archived: bool = False
    bean: str | None = None
    roaster: str | None = None
    grinder: str | None = None
    #: In the shape the matching rule reads (:func:`set_attributes`), unstated ones left out.
    #: The post-filter checks a proposal's scope against exactly this.
    attributes: dict[str, Any] = Field(default_factory=dict)
    insights: list[PatternInsight] = Field(default_factory=list)


class PatternGeneral(BaseModel):
    """An existing confirmed general insight."""

    model_config = ConfigDict(extra="forbid")

    id: int
    scope: dict[str, Any] = Field(default_factory=dict)
    text: str


class PatternDeclined(BaseModel):
    """A proposal a person turned down: what was proposed, from what, with which scope."""

    model_config = ConfigDict(extra="forbid")

    id: int
    text: str
    scope: dict[str, Any] = Field(default_factory=dict)
    #: "Guji daily #5 \"text\"", one per source as it was given then.
    sources: list[str] = Field(default_factory=list)


class PatternsInput(BaseModel):
    """Everything one run is told."""

    model_config = ConfigDict(extra="forbid")

    sets: list[PatternSet] = Field(default_factory=list)
    general: list[PatternGeneral] = Field(default_factory=list)
    declined: list[PatternDeclined] = Field(default_factory=list)

    @property
    def insight_count(self) -> int:
        return sum(len(item.insights) for item in self.sets)

    def render(self) -> dict[str, str]:
        """The prompt's variables: one rendered block per section."""
        return {
            "set_insights": _render_sets(self.sets),
            "general_insights": _render_general(self.general),
            "declined": _render_declined(self.declined),
        }


async def build_patterns_input(db: Database) -> PatternsInput:
    """Assemble what one run is told. A pure function of the database it is handed."""
    insights = InsightsRepository(db)
    sets: list[PatternSet] = []
    for facts in await PatternRunsRepository(db).sets_with_confirmed_insights():
        attributes = await insights.attributes_of(facts.id) or {}
        confirmed = [item for item in await insights.own(facts.id) if item.confirmed]
        sets.append(
            PatternSet(
                id=facts.id,
                name=facts.name,
                archived=facts.archived,
                bean=facts.bean_name,
                roaster=facts.roaster or None,
                grinder=facts.grinder_name,
                attributes={
                    key: attributes[key]
                    for key in SCOPE_KEYS
                    if key != "profile_style" and attributes.get(key) is not None
                },
                insights=[
                    PatternInsight(
                        id=item.id,
                        text=item.text,
                        learned_at=item.set_version_label,
                        rests_on=[rest.render() for rest in item.rests_on],
                    )
                    for item in sorted(confirmed, key=lambda row: row.id)
                ],
            )
        )
    general = [
        PatternGeneral(id=item.id, scope=item.scope.stated(), text=item.text)
        for item in sorted(await insights.list_insights(confirmed=True), key=lambda row: row.id)
    ]
    declined = [
        PatternDeclined(
            id=item.id,
            text=item.text,
            scope=item.scope.stated(),
            sources=[
                f'{source.set_name} #{source.insight_id} "{source.text}"' for source in item.sources
            ],
        )
        for item in await PatternProposalsRepository(db).declined()
    ]
    return PatternsInput(sets=sets, general=general, declined=declined)


def _scope_words(scope: dict[str, Any]) -> str:
    return InsightScope.model_validate(scope).label() if scope else "any Set"


def _render_sets(sets: list[PatternSet]) -> str:
    blocks: list[str] = []
    for item in sets:
        names = [f'bean "{item.bean}"' if item.bean else "no bean"]
        if item.roaster:
            names[0] += f" by {item.roaster}"
        names.append(f'grinder "{item.grinder}"' if item.grinder else "no grinder")
        attributes = ", ".join(f"{key}={value}" for key, value in item.attributes.items())
        lines = [
            f'SET {item.id} "{item.name}"{" (archived)" if item.archived else ""}: '
            f"{'; '.join(names)}",
            f"  scope attributes: {attributes or 'none stated'}",
        ]
        for insight in item.insights:
            facts = [
                f"learned at {insight.learned_at}" if insight.learned_at else "learned earlier"
            ]
            if insight.rests_on:
                facts.append("rests on " + ", ".join(insight.rests_on))
            lines.append(f"  #{insight.id} [{'; '.join(facts)}] {insight.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(none)"


def _render_general(general: list[PatternGeneral]) -> str:
    if not general:
        return "(none)"
    return "\n".join(f"#{item.id} [{_scope_words(item.scope)}] {item.text}" for item in general)


def _render_declined(declined: list[PatternDeclined]) -> str:
    if not declined:
        return "(none)"
    lines: list[str] = []
    for item in declined:
        lines.append(f'- "{item.text}" [{_scope_words(item.scope)}]')
        lines.extend(f"    from {source}" for source in item.sources)
    return "\n".join(lines)
