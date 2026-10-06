"""The numbers behind a reading, worked out by the server and never typed by the model.

A claim cites one to three expressions of the metric language. This module evaluates each
one on the shot with the language's own evaluator and stores what came back: the sentence
the language renders, the value, the unit, the kind of channel it came from, why it is
absent when it is, and whether the expression's own comparison held. The model chose the
expression and the limit it wanted to hold the shot to; it never supplies a result. A
number a person reads on a claim is therefore one the same expression gives on
``POST /api/shots/{id}/evaluate``.

**A claim whose numbers do not bear it out is kept, marked ``supported = False``**: when an
expression's comparison failed, or when none of its evidence could be measured (a phase the
shot never reached, a sensor it lacks). The page says "the numbers don't bear this out"
and a person decides. A claim with no evidence at all has nothing against it and is
supported (only a free-text result or the prediction may have none).

The window of every claim is resolved here too, with the evaluator's own window code
(:func:`gaggiclanker.domain.metric_language.window_span`), so the span the curve highlights
is the span the numbers were taken over.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from gaggiclanker.db.repos.reviews import ClaimWrite, EvidenceStored
from gaggiclanker.domain.metric_language import (
    Expression,
    ShotData,
    Window,
    canonical_form,
    evaluate,
    render,
    window_span,
    window_words,
)
from gaggiclanker.domain.signature import limit_text

__all__ = ["claims_from_answer", "evaluated", "supported"]


def evaluated(expressions: Sequence[Expression], data: ShotData) -> list[EvidenceStored]:
    """Each expression's result on the shot, in the order the model gave them."""
    stored: list[EvidenceStored] = []
    for expression in expressions:
        result = evaluate(expression, data)
        limit = limit_text(expression, result.unit)
        sentence = result.sentence
        if expression.compare is not None and expression.relative_to is not None:
            # The language words a share's limit as a fraction ("at most 0.15") beside a value
            # a page shows as a percentage: say the limit the way a check does instead.
            bare = render(expression.model_copy(update={"compare": None}), data)
            sentence = f"{bare}, {limit}"
        stored.append(
            EvidenceStored(
                expression=json.loads(canonical_form(expression)),
                sentence=sentence,
                limit_text=limit if expression.compare is not None else "",
                value=result.value,
                unit=result.unit,
                kind=result.kind,
                absent=result.absent,
                why=result.why,
                held=result.held,
            )
        )
    return stored


def supported(evidence: Sequence[EvidenceStored]) -> bool:
    """Whether the numbers bear a claim out: no comparison failed and something was measured."""
    if not evidence:
        return True
    if any(item.held is False for item in evidence):
        return False
    return any(item.value is not None for item in evidence)


def _phase_of(window: Window, data: ShotData) -> str | None:
    """The phase a window is, by the name the shot gave it, when it is one."""
    if window.phase is not None:
        return window.phase.strip()
    if window.phase_number is not None:
        for span in data.phases:
            if span.number == window.phase_number:
                return span.name or None
    return None


def _placed(window: Window, data: ShotData) -> dict[str, Any]:
    span = window_span(data, window)
    return {
        "window": json.loads(
            window.model_dump_json(by_alias=True, exclude_none=True, exclude_defaults=True)
        ),
        "window_text": window_words(window, data),
        "phase": _phase_of(window, data),
        "start_s": None if span is None else span[0],
        "end_s": None if span is None else span[1],
    }


class _Asked(Protocol):
    """What of an expectation the claims need: which one it was, and the word it fails with."""

    @property
    def id(self) -> int: ...
    @property
    def fault(self) -> str: ...


def claims_from_answer(
    answer: Any, data: ShotData, expectations: Sequence[_Asked]
) -> list[ClaimWrite]:
    """Every statement of a validated reading as a claim to store, numbers evaluated.

    ``answer`` is an instance of the per-call output model
    (:func:`gaggiclanker.review.models.build_output_model`): claims first, then one result
    per free-text expectation in the order they were asked in (written order), then the
    prediction when there is one. A free-text result carries its expectation's own fault
    word (the model does not pick it), and only when it did not hold.
    """
    claims: list[ClaimWrite] = []

    for item in answer.claims:
        evidence = evaluated(item.evidence, data)
        claims.append(
            ClaimWrite(
                kind="claim",
                fault=item.fault,
                text=item.text.strip(),
                evidence=evidence,
                supported=supported(evidence),
                **_placed(item.window, data),
            )
        )

    results = {item.expectation_id: item for item in answer.free_text_results}
    for expectation in expectations:
        result = results.get(expectation.id)
        if result is None:
            continue
        evidence = evaluated(result.evidence, data)
        claims.append(
            ClaimWrite(
                kind="free_text",
                expectation_id=expectation.id,
                held=result.held,
                fault=None if result.held else expectation.fault,
                text=result.text.strip(),
                evidence=evidence,
                supported=supported(evidence),
                **_placed(result.window, data),
            )
        )

    prediction = getattr(answer, "prediction", None)
    if prediction is not None:
        evidence = evaluated(prediction.evidence, data)
        claims.append(
            ClaimWrite(
                kind="prediction",
                stance=prediction.stance,
                text=prediction.text.strip(),
                evidence=evidence,
                supported=supported(evidence),
            )
        )
    return claims
