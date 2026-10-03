"""The shape a pattern run must answer in, and the filter its answer passes before storage.

The schema is the only description of the output contract: the provider is sent its JSON
schema and the reply is validated against it, so the prompt explains the *fields* and never
their types. Everything is strict (``extra="forbid"``): a key nobody reads fails validation,
gets the LLM layer's one corrective turn, and is a `failed` run if the model insists.

What the schema cannot say is whether a proposal is **allowed**: that depends on the insights
this run was given. :func:`filter_proposals` decides it, and it is where the rules live that
make a shown proposal safe to approve:

* it rests on at least two distinct Sets (a lesson from one Set is that Set's own);
* it cites only insights it was given, and replaces only a general insight it was given;
* its scope never uses ``profile_style`` (a Set has no single style, so a style-scoped
  insight would reach no Set), and **every source Set matches it**, by the live
  :func:`~gaggiclanker.db.repos.knowledge_insights.scope_matches`, so that deleting a source
  on approval never loses its lesson for the Set it came from.

A proposal that fails any of these is dropped before storage and counted by reason on the run
(a model that invents sources often is a prompt problem worth seeing).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gaggiclanker.db.repos.knowledge_insights import InsightScope, scope_matches
from gaggiclanker.db.repos.patterns import PatternProposalWrite, PatternSource
from gaggiclanker.patterns.context import (
    PatternGeneral,
    PatternInsight,
    PatternSet,
    PatternsInput,
)

log = structlog.get_logger(__name__)

__all__ = [
    "DROP_REASONS",
    "TEXT_MAX",
    "FilterOutcome",
    "PatternProposalOut",
    "PatternsResult",
    "filter_proposals",
]

#: A general insight is a sentence or two, not a document.
TEXT_MAX = 500

#: Why a proposal is dropped, in the order the checks run.
DROP_REASONS = (
    "invented_source",
    "invented_replaces",
    "one_set",
    "profile_style",
    "scope_not_shared",
)


class PatternProposalOut(BaseModel):
    """One proposed general insight, as the model writes it."""

    model_config = ConfigDict(extra="forbid")

    #: The general insight, stated at the level the sources support and no further.
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    #: Which Sets it applies to, by attribute: only keys whose value every source Set shares.
    #: Empty reaches every Set.
    scope: InsightScope = Field(default_factory=InsightScope)
    #: The ids of the Set insights it was derived from (the `#id` in the input), from at
    #: least two different Sets.
    source_insight_ids: list[int] = Field(default_factory=list)
    #: The id of a general insight this one sharpens or contradicts; approving deletes it.
    replaces_insight_id: int | None = None

    @field_validator("text")
    @classmethod
    def _says_something(cls, value: str) -> str:
        """Refused when nothing is left after collapsing whitespace (non-breaking spaces too).

        Here rather than only where the proposal is stored: a refusal in the output model is a
        validation failure the LLM layer corrects once and otherwise records as `invalid_output`,
        where the same refusal after the call would be an exception no provider handling sees.
        """
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("a proposal needs some text")
        return collapsed


class PatternsResult(BaseModel):
    """The whole answer. One call, one document, no tool loop."""

    model_config = ConfigDict(extra="forbid")

    proposals: list[PatternProposalOut] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FilterOutcome:
    """What survived the filter, and what was dropped, by reason."""

    kept: list[PatternProposalWrite] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)


def filter_proposals(result: PatternsResult, given: PatternsInput) -> FilterOutcome:
    """Keep the proposals that are allowed, drop (and count) the rest."""
    insights = {insight.id: (item, insight) for item in given.sets for insight in item.insights}
    general = {item.id: item for item in given.general}
    kept: list[PatternProposalWrite] = []
    dropped: Counter[str] = Counter()

    for proposal in result.proposals:
        reason = _why_dropped(proposal, given, insights, general)
        if reason is not None:
            dropped[reason] += 1
            log.warning("pattern_proposal_dropped", reason=reason, text=proposal.text[:80])
            continue
        ids = list(dict.fromkeys(proposal.source_insight_ids))
        replaces = general.get(proposal.replaces_insight_id or 0)
        kept.append(
            PatternProposalWrite(
                text=proposal.text,
                scope=proposal.scope,
                sources=[
                    PatternSource(
                        insight_id=insight_id,
                        set_id=insights[insight_id][0].id,
                        set_name=insights[insight_id][0].name,
                        text=insights[insight_id][1].text,
                    )
                    for insight_id in ids
                ],
                replaces_id=None if replaces is None else replaces.id,
                replaces_text="" if replaces is None else replaces.text,
            )
        )
    return FilterOutcome(kept=kept, dropped=dict(dropped))


def _why_dropped(
    proposal: PatternProposalOut,
    given: PatternsInput,
    insights: dict[int, tuple[PatternSet, PatternInsight]],
    general: dict[int, PatternGeneral],
) -> str | None:
    ids = list(dict.fromkeys(proposal.source_insight_ids))
    if any(insight_id not in insights for insight_id in ids):
        return "invented_source"
    if proposal.replaces_insight_id is not None and proposal.replaces_insight_id not in general:
        return "invented_replaces"
    source_sets = {insights[insight_id][0].id for insight_id in ids}
    if len(source_sets) < 2:
        return "one_set"
    if proposal.scope.profile_style is not None:
        return "profile_style"
    stated = proposal.scope.stated()
    attributes = {item.id: item.attributes for item in given.sets}
    if not all(scope_matches(stated, attributes[set_id]) for set_id in source_sets):
        return "scope_not_shared"
    return None
