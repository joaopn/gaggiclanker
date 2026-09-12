"""The shape the model must answer in.

This is the only description of the output contract. Its JSON schema is what the
provider is sent and its validator is what the reply is checked against
(:meth:`gaggiclanker.llm.service.LlmService.call_json`), so there is no second
copy of the contract in a prompt to drift away from this one — the prompt
explains the *fields*, never their types.

Everything is strict. `extra="forbid"` means a model that invents a key is
corrected rather than quietly storing a field nothing reads; the closed
vocabularies mean a suggestion whose variable is "grind size" fails validation
instead of becoming a row the accept path cannot apply. The LLM layer already
does one corrective turn on a validation failure, and a strict schema is what
makes that turn worth taking.

Two things are deliberately *not* enforced here and are post-processed instead
(:mod:`gaggiclanker.analyzer.service`): `rules_used` is filtered against the
rules the shot was actually given, and `profile_patch` is stored rather than
applied. The first is a check the schema cannot express — it depends on the
selection — and the second is policy: gaggiclanker writes nothing to the device.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.knowledge_insights import InsightScope
from gaggiclanker.domain.vocab import (
    Balance,
    ShotStyle,
    SuggestionDirection,
    SuggestionUnit,
    SuggestionVariable,
)

__all__ = [
    "MAX_PROPOSED_INSIGHTS",
    "AnalysisResult",
    "Execution",
    "ExecutionIssue",
    "ProfilePatch",
    "ProposedInsight",
    "Suggestion",
    "TastePrediction",
]

#: How many insights one analysis may keep. Two, because a model asked for "any
#: patterns you noticed" produces a list of eight, of which one is a pattern and
#: seven are restatements of this shot, and every one of them costs the user a
#: confirm-or-delete decision.
#:
#: **Not enforced by the schema.** It used to be a `max_length`, which turned a
#: third proposal into a validation failure, a corrective turn and a second
#: paid call — for an answer whose diagnosis and suggestions were fine. The
#: prompt asks for at most two and `_post_process` keeps the first two, the same
#: way an over-long citation list is trimmed rather than rejected.
MAX_PROPOSED_INSIGHTS = 2

#: How sure the model is, everywhere it is asked. Three words rather than a 0-1
#: number: a model asked for a probability produces a decimal with two digits of
#: false precision, and nothing downstream can do arithmetic with it anyway.
type Confidence = str


class ExecutionIssue(BaseModel):
    """One thing that went wrong mechanically, and the number that says so."""

    model_config = ConfigDict(extra="forbid")

    #: Which diagnostic this is about — a band name (`channeling_risk`), an
    #: indicator (`flow_vs_target`), or a plain phrase. Free text on purpose:
    #: the useful set is larger than any list we could close, and this field is
    #: read by a person rather than dispatched on.
    signal: str
    severity: str = Field(default="minor", description="minor | moderate | severe")
    #: The figure from the context that supports it. The prompt forbids inventing
    #: one; an issue with no evidence is an opinion.
    evidence: str = ""


class Execution(BaseModel):
    """How cleanly the machine executed the profile, in the model's words.

    Shown *beside* the deterministic execution score, never instead of it. The
    score is reproducible; this is an
    explanation of it.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str
    issues: list[ExecutionIssue] = Field(default_factory=list)


class TastePrediction(BaseModel):
    """What the model expects the cup tasted like, from the telemetry alone.

    Worth asking for even when a judgement exists: a prediction that disagrees
    with what the person actually tasted is the single most informative thing in
    the output, because one of the two is wrong and the shot page shows both.
    """

    model_config = ConfigDict(extra="forbid")

    balance: Balance
    body: str = Field(description="thin | medium | heavy")
    confidence: Confidence = "medium"


class Suggestion(BaseModel):
    """One change to make next time.

    `direction` and `magnitude` are separate because "two steps finer" is how a
    person says it; the sign is applied in exactly one place, the accept path,
    where the grinder's convention is written down.
    """

    model_config = ConfigDict(extra="forbid")

    variable: SuggestionVariable
    direction: SuggestionDirection
    #: How far. ``None`` only for `puck_prep` and `profile`, where there is
    #: nothing to count.
    magnitude: float | None = None
    unit: SuggestionUnit = "none"
    reason: str
    confidence: Confidence = "medium"
    #: 1 first. The prompt asks for one primary suggestion and at most two
    #: backups, because a list of six is not advice.
    priority: int = Field(default=1, ge=1, le=10)


class ProfilePatch(BaseModel):
    """A proposed edit to one field of one phase. Recorded, never applied.

    The prototype writes nothing to the device, so these are shown as "what it
    would change" and nothing more. `from_` is the value the model read in the
    context; storing it is what lets a later reader see whether the patch was
    even addressed to the profile that is in the Set now.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    phase_index: int = Field(ge=0)
    field: str
    #: `from` is a Python keyword, so the field is `from_` with the wire name
    #: `from`. `populate_by_name` above lets both spellings validate.
    from_: str = Field(default="", alias="from")
    to: str = ""
    reason: str = ""


class ProposedInsight(BaseModel):
    """Something the analyzer thinks is true of this setup rather than this shot.

    Stored **unconfirmed** and shown for a one-click confirm. Nothing
    unconfirmed reaches a later
    prompt, which is the whole safety property: a model that generalises from one
    shot and is then believed by the next analysis has manufactured its own
    evidence.
    """

    model_config = ConfigDict(extra="forbid")

    #: What the insight is about. Every key optional; the ones it names must all
    #: match a Set for the insight to apply to it. An empty scope is a claim
    #: about the whole kitchen and should be rare.
    scope: InsightScope = Field(default_factory=InsightScope)
    #: One sentence. What was learned, in the user's own units.
    text: str = Field(min_length=1, max_length=2000)
    #: The shots it was learned from — ids from the trajectory in the context.
    #: Filtered on the way in against the shots this analysis was actually
    #: shown, because an insight's evidence is what makes it checkable.
    evidence_shot_ids: list[int] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """The whole answer. One call, one document, no tool loop."""

    model_config = ConfigDict(extra="forbid")

    #: What kind of shot this was. The server has already detected it from the
    #: profile and says so in the context; asking again is a cheap check on
    #: whether the model and the deterministic detector agree, and a disagreement
    #: is shown rather than resolved.
    shot_style: ShotStyle
    execution: Execution
    taste_prediction: TastePrediction
    #: The paragraph a person reads first: what happened and why.
    diagnosis: str
    suggestions: list[Suggestion] = Field(default_factory=list)
    profile_patch: list[ProfilePatch] = Field(default_factory=list)
    #: What the model needs in order to do better next time — a missing dose, a
    #: taste note nobody recorded. gaggimate-mcp calls this "surfacing knowledge
    #: gaps" and it is the cheapest way to improve the next analysis.
    questions_for_user: list[str] = Field(default_factory=list)
    #: The keys of the knowledge rules the model relied on. Validated against the
    #: rules this shot was actually given; an invented key is dropped with a
    #: warning rather than shown as a citation (`investigation.md` §8).
    rules_used: list[str] = Field(default_factory=list)
    #: The `heading_path` of every reference excerpt the model leaned on.
    #: Validated the same way and for the same reason — a citation into a
    #: document that was never in front of it is worse than no citation, because
    #: the reader can follow it and find prose that says something else.
    excerpts_used: list[str] = Field(default_factory=list)
    #: Trimmed to :data:`MAX_PROPOSED_INSIGHTS` by `_post_process`, in the order
    #: the model gave them. Stored as unconfirmed rows linked to this analysis;
    #: the user confirms them from the panel.
    proposed_insights: list[ProposedInsight] = Field(default_factory=list)
