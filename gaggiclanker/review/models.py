"""The shape a reading must answer in, built for the shot it is about.

This is the only description of the output contract. Its JSON schema is what
the provider is sent and its validator is what the reply is checked against
(:meth:`gaggiclanker.llm.service.LlmService.call_json`), so there is no second
copy of the contract in a prompt to drift away from this one: the prompt
explains the *fields*, never their types.

**The model is built per call** (:func:`build_output_model`), because what is a valid
answer depends on the shot:

* a window or an anchor may name only the phases *this shot* logged;
* the free-text results must carry exactly the ids of the confirmed signature's free-text
  expectations, one each, no more and no fewer;
* ``prediction`` exists exactly when the Set version was filed with one, so a reading that
  invents a stance for a prediction nobody made is refused as an unknown key, and one that
  leaves it out when there is one is refused as a missing key.

A violation fails validation, gets the LLM layer's one corrective turn, and if it still
answers wrongly the run is stored as `failed` with the error. Fault words are the fixed
list (:data:`gaggiclanker.domain.warnings.Fault`): a word outside it fails the same way.

Everything is strict. ``extra="forbid"`` means a model that adds a key (a taste prediction,
advice, a profile patch, a question for the person) fails validation: a reading says what
the shot did, it does not predict the cup, advise or propose, and a field nothing reads must
not be quietly dropped into a successful row.

Two things are deliberately *not* enforced here and are post-processed instead
(:mod:`gaggiclanker.review.service`): the citations are filtered against the rules and
excerpts the shot was actually given, and the numbers behind every claim are worked out by
the server from the expressions the model wrote (:mod:`gaggiclanker.review.evidence`).
"""

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, create_model, model_validator

from gaggiclanker.domain.metric_language import (
    Expression,
    NamedAnchor,
    NamedAnchorName,
    PhaseEndAnchor,
    PhaseStartAnchor,
    SecondsAnchor,
    Window,
)
from gaggiclanker.domain.vocab import PredictionStance
from gaggiclanker.domain.warnings import Fault

__all__ = [
    "CLAIMS_MAX",
    "CLAIM_TEXT_MAX",
    "EVIDENCE_MAX",
    "SUMMARY_MAX",
    "OutputModel",
    "build_output_model",
]

#: How long the summary may be. One sentence a table or a tooltip shows.
SUMMARY_MAX = 200

#: One claim is one sentence.
CLAIM_TEXT_MAX = 300

#: A reading makes 1 to 12 claims, and every claim (and every result) cites at most three
#: expressions: enough to show a number and the comparison that makes it matter.
CLAIMS_MAX = 12
EVIDENCE_MAX = 3

type OutputModel = type[BaseModel]

_STRICT = ConfigDict(extra="forbid")


def _names(phases: Sequence[str]) -> list[str]:
    """The shot's phase names, each once, in the order the shot ran them."""
    return list(dict.fromkeys(name.strip() for name in phases if name.strip()))


#: A sentence the model writes: stripped, and never empty once stripped. A whitespace-only text
#: is refused by the schema (so the corrective turn runs) rather than stored as nothing.
def _sentence(limit: int) -> Any:
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=limit)]


def _models(names: Sequence[str]) -> tuple[Any, Any]:
    """(the window model, the expression model) for a shot with these phase names.

    The language's own window and expression, with the phase-naming fields narrowed to the
    shot's phases: the evaluator, the rendering and the canonical form stay the language's,
    and only what may be *said* is the shot's.
    """
    # A shot with no phase table admits no phase name at all: a window is the whole shot or a
    # span between named moments or seconds.
    phase_type: Any = Literal[tuple(names)] if names else None
    phase_field: Any = (phase_type | None) if names else None

    class StartOfPhase(PhaseStartAnchor):
        phase_start: phase_type

    class EndOfPhase(PhaseEndAnchor):
        phase_end: phase_type

    anchors: list[Any] = [NamedAnchorName, NamedAnchor, SecondsAnchor]
    if names:
        anchors += [StartOfPhase, EndOfPhase]
    anchor_type: Any = anchors[0]
    for member in anchors[1:]:
        anchor_type = anchor_type | member

    class ReadingWindow(Window):
        phase: phase_field = None
        # A phase is named, never numbered: a number would bypass the names the shot logged.
        phase_number: None = None
        start: anchor_type | None = Field(default=None, alias="from")
        end: anchor_type | None = Field(default=None, alias="to")

    class ReadingExpression(Expression):
        window: ReadingWindow = Field(default_factory=ReadingWindow)

    return ReadingWindow, ReadingExpression


def build_output_model(
    *,
    phases: Sequence[str],
    free_text_ids: Sequence[int],
    has_prediction: bool,
) -> OutputModel:
    """The output model for one reading: its enums are this shot's, its keys this reading's."""
    names = _names(phases)
    ids = sorted(set(free_text_ids))
    window, expression = _models(names)

    evidence_one_to_three = list[expression]  # type: ignore[valid-type]

    claim = create_model(
        "ReadingClaim",
        __config__=_STRICT,
        window=(window, Field(description="the part of the shot the claim is about")),
        fault=(Fault | None, Field(description="a word from the fixed list, or null")),
        text=(_sentence(CLAIM_TEXT_MAX), ...),
        evidence=(
            evidence_one_to_three,
            Field(min_length=1, max_length=EVIDENCE_MAX),
        ),
    )

    fields: dict[str, Any] = {
        "summary": (_sentence(SUMMARY_MAX), ...),
        "claims": (list[claim], Field(min_length=1, max_length=CLAIMS_MAX)),  # type: ignore[valid-type]
    }

    if ids:
        free_result = create_model(
            "ReadingFreeTextResult",
            __config__=_STRICT,
            expectation_id=(Literal[tuple(ids)], Field(description="the expectation answered")),
            held=(bool, Field(description="whether the expectation held on this shot")),
            window=(window, Field(description="the part of the shot the answer rests on")),
            text=(_sentence(CLAIM_TEXT_MAX), ...),
            evidence=(
                list[expression],  # type: ignore[valid-type]
                Field(default_factory=list, max_length=EVIDENCE_MAX),
            ),
        )
        fields["free_text_results"] = (
            list[free_result],  # type: ignore[valid-type]
            Field(min_length=len(ids), max_length=len(ids)),
        )
    else:
        # No free-text expectation to answer: the key may only be an empty list.
        fields["free_text_results"] = (list[Any], Field(default_factory=list, max_length=0))

    if has_prediction:
        stance = create_model(
            "ReadingPrediction",
            __config__=_STRICT,
            stance=(PredictionStance, Field(description="how the shot moved against it")),
            text=(_sentence(CLAIM_TEXT_MAX), ...),
            evidence=(
                list[expression],  # type: ignore[valid-type]
                Field(default_factory=list, max_length=EVIDENCE_MAX),
            ),
        )
        fields["prediction"] = (stance, Field(description="against the version's prediction"))

    fields["rules_used"] = (list[str], Field(default_factory=list))
    fields["excerpts_used"] = (list[str], Field(default_factory=list))

    ids_wanted = tuple(ids)

    def _each_expectation_once(self: BaseModel) -> BaseModel:
        found = sorted(item.expectation_id for item in self.free_text_results)  # type: ignore[attr-defined]
        if tuple(found) != ids_wanted:
            raise ValueError(
                f"free_text_results must answer each of the expectations {list(ids_wanted)} "
                f"exactly once, and answered {found}"
            )
        return self

    validators: dict[str, Any] = {
        "each_expectation_once": model_validator(mode="after")(_each_expectation_once)
    }
    return create_model("ShotReading", __config__=_STRICT, __validators__=validators, **fields)
