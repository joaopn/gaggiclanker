"""The shape a review must answer in.

This is the only description of the output contract. Its JSON schema is what
the provider is sent and its validator is what the reply is checked against
(:meth:`gaggiclanker.llm.service.LlmService.call_json`), so there is no second
copy of the contract in a prompt to drift away from this one: the prompt
explains the *fields*, never their types.

Everything is strict. ``extra="forbid"`` means a model that adds a key (a
list of suggestions, a profile patch, a question for the person) fails
validation, gets the LLM layer's one corrective turn, and if it still answers
with it the run is stored as `failed` with the error: a review writes three
things about a shot, and a field nothing reads must not be quietly dropped
into a successful row.

One thing is deliberately *not* enforced here and is post-processed instead
(:mod:`gaggiclanker.review.service`): the citations are filtered against the
rules and excerpts the shot was actually given, a check that depends on the
selection and so cannot be a schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.domain.vocab import Balance, ReviewConfidence, TasteBody

__all__ = ["SUMMARY_MAX", "ReviewResult", "TastePrediction"]

#: How long the summary may be. One sentence a table or a search result could
#: show one day ("Channelled at 12 s; fast, likely sour").
SUMMARY_MAX = 200


class TastePrediction(BaseModel):
    """What the model expects the cup to taste like, from the telemetry alone.

    Blind: a review is never shown the person's judgement, so this is a
    prediction and the shot page sets it beside what they actually tasted.
    """

    model_config = ConfigDict(extra="forbid")

    balance: Balance
    body: TasteBody
    confidence: ReviewConfidence


class ReviewResult(BaseModel):
    """The whole answer. One call, one document, no tool loop."""

    model_config = ConfigDict(extra="forbid")

    taste_prediction: TastePrediction
    #: One paragraph: what the telemetry shows and why, with the figures from
    #: the input that say so, execution faults included.
    description: str = Field(min_length=1)
    #: One sentence.
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX)
    #: The keys of the knowledge rules the model relied on. Validated against
    #: the rules this shot was given; an invented key is dropped with a warning
    #: rather than shown as a citation.
    rules_used: list[str] = Field(default_factory=list)
    #: The `heading_path` of every reference excerpt the model leaned on,
    #: validated the same way.
    excerpts_used: list[str] = Field(default_factory=list)
