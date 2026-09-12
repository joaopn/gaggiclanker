"""The pipeline: modes, retries, classification, the latch, the correction."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.llm import LlmCallsRepository
from gaggiclanker.llm.budget import RateLimitBudget
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.modes import ModeMemory
from gaggiclanker.llm.providers.base import ProviderReply
from gaggiclanker.llm.service import MAX_STORED_TEXT, LlmService
from gaggiclanker.llm.types import Err, LlmMessage, LlmRequest, Ok, Usage
from gaggiclanker.settings_service import SettingsService
from tests.llm.conftest import Answer, FakeProvider, api_error


async def _until(predicate: Callable[[], bool]) -> None:
    """Yield to the loop until ``predicate`` holds — the provider has been entered."""
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("the provider was never called")


def request(**overrides: object) -> LlmRequest[Answer]:
    fields: dict[str, object] = {
        "messages": [LlmMessage(role="user", content="how was that shot?")],
        "output_model": Answer,
        "model": "test-model",
        "max_retries": 2,
        "retry_delay_s": 0.0,
        "label": "judge shot",
        "subject": "#129",
    }
    fields.update(overrides)
    return LlmRequest(**fields)  # type: ignore[arg-type]


async def test_happy_path_returns_a_validated_model(
    service: LlmService, provider: FakeProvider
) -> None:
    result = await service.call_json(request())

    assert isinstance(result, Ok)
    assert result.data == Answer(verdict="ok", score=1)
    assert result.mode == "json_schema"
    assert result.usage == Usage(prompt_tokens=11, completion_tokens=7)
    assert provider.calls[0].output_model is Answer


async def test_fenced_json_is_unwrapped(service: LlmService, provider: FakeProvider) -> None:
    provider.script = ['Sure!\n```json\n{"verdict": "sour", "score": 3}\n```\nHope that helps.']

    result = await service.call_json(request())

    assert isinstance(result, Ok)
    assert result.data.verdict == "sour"


# -- mode fallback --------------------------------------------------------


async def test_a_capability_error_falls_back_to_the_next_mode(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [
        api_error(400, "unsupported", body="response_format is not supported by this model"),
        '{"verdict": "fine", "score": 2}',
    ]

    result = await service.call_json(request())

    assert isinstance(result, Ok)
    assert provider.modes_used == ["json_schema", "json_object"]
    assert result.mode == "json_object"


async def test_a_real_failure_does_not_try_the_next_mode(
    service: LlmService, provider: FakeProvider
) -> None:
    """A 401 is about the key. Asking less politely does not fix it."""
    provider.script = [api_error(401, "invalid api key")]

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert result.code == "auth"
    assert provider.modes_used == ["json_schema"]


async def test_a_model_name_400_is_not_read_as_a_capability_gap(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [api_error(400, "unknown model: not-a-model", body="unknown model")]

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert provider.modes_used == ["json_schema"]


async def test_every_mode_rejected_reports_the_last_reason(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [api_error(400, "no", body="json_schema is not supported")]

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert provider.modes_used == ["json_schema", "json_object", "text"]


async def test_the_working_mode_is_remembered_for_the_next_call(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    memory = ModeMemory()
    provider = FakeProvider(
        script=[
            api_error(400, "nope", body="response_format unsupported"),
            '{"verdict": "ok", "score": 1}',
        ]
    )
    service = LlmService(
        settings, budget=budget, mode_memory=memory, provider_factory=lambda *_: provider
    )

    await service.call_json(request())
    provider.script = ['{"verdict": "ok", "score": 1}']
    provider.calls.clear()
    await service.call_json(request())

    # Second call starts where the first one ended up: no wasted round trip.
    assert provider.modes_used == ["json_object"]
    assert memory.recall(provider.id, provider.base_url) == "json_object"


# -- retries --------------------------------------------------------------


async def test_a_5xx_is_retried_up_to_max_retries(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [api_error(503, "upstream is having a moment")]

    result = await service.call_json(request(max_retries=2))

    assert isinstance(result, Err)
    # Three attempts in the first mode, then the other two modes once each:
    # a 503 is not a capability error, so it returns without falling back.
    assert len(provider.calls) == 3


async def test_a_400_is_not_retried(service: LlmService, provider: FakeProvider) -> None:
    provider.script = [api_error(400, "malformed request", body="bad json in messages")]

    await service.call_json(request(max_retries=3))

    assert len(provider.calls) == 1


async def test_unparseable_output_is_retried_then_reported(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = ["I would rather not answer in JSON, thanks."]

    result = await service.call_json(request(max_retries=1))

    assert isinstance(result, Err)
    assert result.code == "invalid_output"
    assert len(provider.calls) == 2


async def test_backoff_is_linear_in_the_attempt_number(
    service: LlmService, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    provider.script = [api_error(500, "boom")]

    await service.call_json(request(max_retries=3, retry_delay_s=0.25))

    assert slept == [0.25, 0.5, 0.75]


# -- classification -------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (api_error(429, "slow down"), "rate_limited"),
        (api_error(403, "forbidden"), "auth"),
        (api_error(None, "the subscription usage limit was reached"), "rate_limited"),
        (api_error(None, "please run /login first"), "auth"),
        (api_error(500, "internal"), "unknown"),
    ],
)
async def test_error_codes(
    service: LlmService, provider: FakeProvider, error: LlmApiError, code: str
) -> None:
    provider.script = [error]

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Err)
    assert result.code == code


async def test_prose_about_rate_limits_cannot_latch_a_call_with_a_status(
    service: LlmService, provider: FakeProvider
) -> None:
    """The archive feeds the model text people typed. It must not steer the app."""
    provider.script = [api_error(500, "the roaster's notes mention rate limiting and quota")]

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Err)
    assert result.code == "unknown"


# -- the rate-limit latch -------------------------------------------------


async def test_the_budget_is_spent_then_the_process_latches(
    settings: SettingsService, provider: FakeProvider
) -> None:
    # The allowance comes from the registry, not from the object: the budget is
    # seeded on the first call so a setting change reaches it without a restart.
    await settings.apply({"llmRateLimitRetries": 1})
    budget = RateLimitBudget()
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )
    provider.script = [api_error(429, "too many requests")]

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Err)
    assert result.code == "rate_limited"
    assert budget.stopped
    # Two whole inner calls: the original, and the one the global retry bought.
    assert len(provider.calls) == 2


async def test_a_latched_process_does_not_touch_the_provider(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget
) -> None:
    budget.latch()
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert result.code == "rate_limited"
    assert provider.calls == []


async def test_resetting_the_latch_lets_calls_through_again(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget
) -> None:
    budget.latch()
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )
    assert isinstance(await service.call_json(request()), Err)

    budget.reset(2)
    result = await service.call_json(request())

    assert isinstance(result, Ok)


# -- the corrective retry -------------------------------------------------


async def test_a_schema_violation_gets_one_corrective_turn(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [
        '{"verdict": "ok"}',  # `score` missing
        '{"verdict": "ok", "score": 4}',
    ]

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Ok)
    assert result.data.score == 4
    corrected = provider.calls[1].messages
    assert corrected[-2].role == "assistant"
    assert corrected[-1].role == "user"
    assert "score" in corrected[-1].content
    # The model's own reply, not pydantic's echo of it, is what it is shown.
    assert "did not match the required schema" in corrected[-1].content


async def test_only_one_correction_is_attempted(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = ['{"verdict": "ok"}']

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Err)
    assert result.code == "invalid_output"
    assert len(provider.calls) == 2


# -- deadline and cancellation --------------------------------------------


async def test_a_stalled_provider_hits_the_per_attempt_deadline(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    provider = FakeProvider(delay=10)
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )

    result = await service.call_json(request(max_retries=0, timeout_s=0.05))

    assert isinstance(result, Err)
    assert result.code == "timeout"
    assert result.retryable


async def test_caller_cancellation_is_not_swallowed(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    """A caller that gave up must not wait out the remaining attempts."""
    provider = FakeProvider(delay=10)
    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=lambda *_: provider
    )

    task = asyncio.create_task(service.call_json(request(timeout_s=10)))
    await _until(lambda: bool(provider.calls))
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# -- the observer and the model resolution --------------------------------


async def test_the_observer_records_the_call(service: LlmService) -> None:
    await service.call_json(request())

    record = service.observer.snapshot()[0]
    assert record.status == "succeeded"
    assert record.label == "judge shot"
    assert record.subject == "#129"
    assert record.total_tokens == 18
    assert record.duration_ms is not None


async def test_a_failed_call_is_recorded_with_its_message(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = [api_error(401, "invalid api key")]

    await service.call_json(request(max_retries=0))

    record = service.observer.snapshot()[0]
    assert record.status == "failed"
    assert "invalid api key" in (record.error or "")


async def test_the_model_comes_from_the_purpose(
    service: LlmService, provider: FakeProvider
) -> None:
    await service.settings.apply({"modelDefault": "base-model", "modelAnalysis": "careful-model"})

    await service.call_json(request(model="", purpose="analysis"))
    await service.call_json(request(model="", purpose="chat"))

    assert provider.calls[0].model == "careful-model"
    assert provider.calls[1].model == "base-model"


async def test_an_sdk_parsed_reply_is_still_validated(
    service: LlmService, provider: FakeProvider
) -> None:
    """A provider that parsed for us does not get to skip our contract."""
    provider.script = [ProviderReply(text="{}", data={"verdict": "ok", "score": 9})]

    result = await service.call_json(request())

    assert isinstance(result, Ok)
    assert result.data.score == 9


async def test_a_provider_that_cannot_be_built_is_an_error_not_a_crash(
    settings: SettingsService, budget: RateLimitBudget
) -> None:
    def explode(*_args):
        raise LlmApiError("no such binary")

    service = LlmService(
        settings, budget=budget, mode_memory=ModeMemory(), provider_factory=explode
    )

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert result.code == "unknown"


# -- bookkeeping survives whatever the call does --------------------------


async def test_a_cancelled_call_is_closed_and_recorded(
    settings: SettingsService, budget: RateLimitBudget, db: Database
) -> None:
    """An entry that is registered and never finalised spins in the header for ever."""
    provider = FakeProvider(delay=10)
    repo = LlmCallsRepository(db)
    service = LlmService(
        settings,
        budget=budget,
        mode_memory=ModeMemory(),
        calls_repo=repo,
        provider_factory=lambda *_: provider,
    )

    task = asyncio.create_task(service.call_json(request(timeout_s=10)))
    await _until(lambda: bool(provider.calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.observer.running == 0
    record = service.observer.snapshot()[0]
    assert record.status == "failed"
    assert "cancelled" in (record.error or "")
    rows = await repo.recent()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "cancelled" in (rows[0].error or "")


async def test_an_unexpected_provider_exception_becomes_an_error_not_a_crash(
    service: LlmService, provider: FakeProvider
) -> None:
    """A provider is a third-party SDK and a subprocess; it can raise anything."""
    provider.script = [RuntimeError("the SDK exploded")]

    result = await service.call_json(request(max_retries=1))

    assert isinstance(result, Err)
    assert result.code == "unknown"
    assert result.retryable is False
    assert "RuntimeError" in result.message
    # Not retried: nothing about an unknown crash suggests the next one differs.
    assert len(provider.calls) == 1
    assert service.observer.snapshot()[0].status == "failed"


async def test_a_missing_credential_never_reaches_the_provider(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.missing = "No API key is configured for openrouter."

    result = await service.call_json(request())

    assert isinstance(result, Err)
    assert result.code == "auth"
    assert provider.calls == []


# -- usage on a failure ---------------------------------------------------


async def test_a_reply_that_failed_validation_still_reports_its_tokens(
    service: LlmService, provider: FakeProvider
) -> None:
    """It was billed exactly like one that passed."""
    provider.script = ['{"verdict": "ok"}']

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Err)
    assert result.code == "invalid_output"
    # Two turns: the original and the corrective one, both paid for.
    assert result.usage == Usage(prompt_tokens=22, completion_tokens=14)
    assert service.observer.snapshot()[0].total_tokens == 36


async def test_a_corrective_turn_does_not_lose_the_first_turns_tokens(
    service: LlmService, provider: FakeProvider
) -> None:
    provider.script = ['{"verdict": "ok"}', '{"verdict": "ok", "score": 4}']

    result = await service.call_json(request(max_retries=0))

    assert isinstance(result, Ok)
    assert result.usage == Usage(prompt_tokens=22, completion_tokens=14)


# -- the call text --------------------------------------------------------


async def test_the_ledger_keeps_the_prompt_and_the_reply(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget, db: Database
) -> None:
    """A prompt is editable, so the text is the only durable explanation."""
    repo = LlmCallsRepository(db)
    service = LlmService(
        settings,
        budget=budget,
        mode_memory=ModeMemory(),
        calls_repo=repo,
        provider_factory=lambda *_: provider,
    )

    await service.call_json(request())

    row = (await repo.recent())[0]
    assert row.input_text is not None
    assert "how was that shot?" in row.input_text
    assert row.output_text == '{"verdict": "ok", "score": 1}'


async def test_the_call_text_can_be_switched_off(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget, db: Database
) -> None:
    await settings.apply({"llmStoreCallText": False})
    repo = LlmCallsRepository(db)
    service = LlmService(
        settings,
        budget=budget,
        mode_memory=ModeMemory(),
        calls_repo=repo,
        provider_factory=lambda *_: provider,
    )

    await service.call_json(request())

    row = (await repo.recent())[0]
    assert row.input_text is None
    assert row.output_text is None
    # The counts are kept either way.
    assert row.input_tokens == 11


async def test_a_runaway_prompt_is_truncated_rather_than_stored_whole(
    settings: SettingsService, provider: FakeProvider, budget: RateLimitBudget, db: Database
) -> None:
    repo = LlmCallsRepository(db)
    service = LlmService(
        settings,
        budget=budget,
        mode_memory=ModeMemory(),
        calls_repo=repo,
        provider_factory=lambda *_: provider,
    )

    await service.call_json(
        request(messages=[LlmMessage(role="user", content="x" * (MAX_STORED_TEXT + 5000))])
    )

    row = (await repo.recent())[0]
    assert row.input_text is not None
    assert len(row.input_text) < MAX_STORED_TEXT + 200
    assert "truncated" in row.input_text
