"""The small decisions: classification, retries, capability, JSON, schema, modes."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.errors import (
    classify_llm_error,
    is_capability_error,
    should_retry,
)
from gaggiclanker.llm.json_utils import parse_json_content
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.observer import LlmCallObserver
from gaggiclanker.llm.schema import strict_json_schema
from gaggiclanker.llm.types import ResponseMode, Usage

# -- classification -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (429, "anything at all", "rate_limited"),
        (401, "nope", "auth"),
        (403, "nope", "auth"),
        (408, "slow", "timeout"),
        (500, "internal", "unknown"),
        # No status: the CLI providers. Now the words are all there is.
        (None, "You have hit your usage limit", "rate_limited"),
        (None, "Invalid API key provided", "auth"),
        (None, "the request timed out", "timeout"),
        (None, "something went sideways", "unknown"),
    ],
)
def test_classification(status: int | None, message: str, expected: str) -> None:
    assert classify_llm_error(status=status, message=message) == expected


def test_a_status_beats_the_words_in_the_body() -> None:
    """The body can contain text the archive fed the model. It gets no vote."""
    assert classify_llm_error(status=500, message="rate limit quota usage limit") == "unknown"


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (429, "", True),
        (503, "", True),
        (500, "", True),
        (400, "", False),
        (404, "", False),
        (None, "could not parse the reply", True),
        (None, "connection refused", True),
        (None, "the model disagreed with itself", False),
    ],
)
def test_retry_decisions(status: int | None, message: str, expected: bool) -> None:
    assert should_retry(status=status, message=message) is expected


@pytest.mark.parametrize(
    ("status", "body", "mode", "expected"),
    [
        (400, "response_format is not supported", "json_schema", True),
        (422, "unknown parameter: response_format", "json_schema", True),
        (400, "Unrecognized request argument: json_schema", "json_object", True),
        # Not a capability gap: the model name is wrong.
        (400, "unknown model: gtp-4o", "json_schema", False),
        # Not a capability gap: the account is the problem.
        (429, "response_format rate limited", "json_schema", False),
        (401, "response_format", "json_schema", False),
        # A 500 mentioning it is a broken gateway, not a capability report.
        (500, "response_format exploded", "json_schema", False),
        # There is nowhere below text to fall back to.
        (400, "response_format", "text", False),
    ],
)
def test_capability_detection(status: int | None, body: str, mode: str, expected: bool) -> None:
    assert is_capability_error(status=status, body=body, mode=mode) is expected


# -- JSON extraction ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Here you go:\n{"a": 1}\nHope that helps.', {"a": 1}),
        ('  \n{"a": 1}\n  ', {"a": 1}),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_parse_json_content(text: str, expected: object) -> None:
    assert parse_json_content(text) == expected


def test_unparseable_content_says_parse() -> None:
    """The retry policy keys on the word, so the message has to carry it."""
    with pytest.raises(ValueError, match="parse"):
        parse_json_content("I would rather not.")


# -- schema ---------------------------------------------------------------


class Nested(BaseModel):
    note: str


class Shape(BaseModel):
    verdict: str
    score: int | None = None
    detail: Nested | None = None
    tags: list[str] = Field(default_factory=list)


def test_strict_schema_closes_objects_and_requires_everything() -> None:
    schema = strict_json_schema(Shape)

    assert schema["additionalProperties"] is False
    # Optional in pydantic, required-but-nullable in the schema: strict mode
    # refuses a partial `required`, and the model is still the real validator.
    assert set(schema["required"]) == {"verdict", "score", "detail", "tags"}
    assert "$defs" not in schema
    assert "default" not in schema["properties"]["tags"]


def test_nested_models_are_inlined_and_closed() -> None:
    schema = strict_json_schema(Shape)
    variants = schema["properties"]["detail"]["anyOf"]
    nested = next(v for v in variants if v.get("type") == "object")

    assert nested["additionalProperties"] is False
    assert nested["required"] == ["note"]


def test_refs_are_kept_when_asked() -> None:
    schema = strict_json_schema(Shape, inline_refs=False)

    assert "$defs" in schema
    assert schema["$defs"]["Nested"]["additionalProperties"] is False


# -- the budget -----------------------------------------------------------


def test_the_budget_spends_then_latches() -> None:
    budget = RateLimitBudget()
    budget.seed(2)

    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False
    assert budget.stopped
    # Latched stays latched: further calls do not get a fresh allowance.
    assert budget.consume() is False


def test_seeding_twice_does_not_refill_a_running_backfill() -> None:
    budget = RateLimitBudget()
    budget.seed(2)
    budget.consume()
    budget.seed(2)

    assert budget.snapshot()["remaining"] == 1


def test_zero_retries_stops_at_the_first_rate_limit() -> None:
    budget = RateLimitBudget()
    budget.seed(0)

    assert budget.consume() is False
    assert budget.stopped


def test_reset_clears_the_latch() -> None:
    budget = RateLimitBudget()
    budget.seed(0)
    budget.consume()

    budget.reset(3)

    assert budget.snapshot() == {"stopped": False, "retries": 3, "remaining": 3}


# -- mode memory ----------------------------------------------------------


def test_a_remembered_mode_is_tried_first_but_the_rest_still_follow() -> None:
    memory = ModeMemory()
    modes: tuple[ResponseMode, ...] = ("json_schema", "json_object", "text")

    assert memory.order("p", "u", modes) == list(modes)

    memory.remember("p", "u", "json_object")

    assert memory.order("p", "u", modes) == ["json_object", "json_schema", "text"]


def test_the_memory_is_keyed_on_the_endpoint_not_the_provider() -> None:
    """One implementation serves OpenRouter and a local gateway; they differ."""
    memory = ModeMemory()
    memory.remember("openai_compatible", "http://localhost:1234/v1", "text")

    assert memory.recall("openai_compatible", "https://openrouter.ai/api/v1") is None


# -- the observer ---------------------------------------------------------


def test_the_ring_drops_finished_calls_but_never_running_ones() -> None:
    observer = LlmCallObserver(size=3)
    running = [observer.register(label=f"live {i}") for i in range(3)]
    for index in range(3):
        observer.register(label=f"done {index}").succeed(Usage(1, 1))

    labels = {record.label for record in observer.snapshot()}

    assert observer.running == 3
    assert all(handle.record.label in labels for handle in running)


def test_a_finished_call_carries_its_totals() -> None:
    observer = LlmCallObserver()
    handle = observer.register(label="analyse", subject="#7", provider="anthropic", model="opus")
    handle.succeed(Usage(prompt_tokens=100, completion_tokens=20), mode="json_schema")

    record = observer.snapshot()[0]

    assert record.status == "succeeded"
    assert record.total_tokens == 120
    assert record.mode == "json_schema"
    assert record.completed_at is not None


def test_unreported_usage_stays_unreported() -> None:
    """None is not zero. A local model that says nothing must not look free."""
    assert Usage().total_tokens is None
    assert Usage(prompt_tokens=0, completion_tokens=0).total_tokens == 0
