"""``/api/llm`` — configuring the provider, and watching what it is doing.

Nothing here makes an analysis call; that is the analyzer's. These endpoints are the
plumbing around one: prove the credentials work, list the models on offer, show
the calls in flight, add up what they cost, and clear the rate-limit latch when
the provider has recovered.

The live list is split the same way the device stream is (see
``gaggiclanker/api/device.py``): a snapshot for a tab that has just opened, and
a stream for everything after. A tab that joined mid-analysis would otherwise
see nothing until the call finished, which is exactly the window the indicator
exists to cover.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from gaggiclanker.analyzer.service import ANALYSIS_EVENTS
from gaggiclanker.api.deps import LlmServiceDep, SettingsServiceDep
from gaggiclanker.db.repos.llm import LlmCallsRepository, UsageTotals
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import BadRequest
from gaggiclanker.infra.sse import SseEvent, sse_response
from gaggiclanker.llm.config import PROVIDER_IDS
from gaggiclanker.llm.observer import LLM_CALL_EVENT, LlmCallObserver
from gaggiclanker.llm.providers.claude_code import (
    CLAUDE_CODE_EFFORT_LEVELS,
    ClaudeCodeProvider,
)
from gaggiclanker.llm.types import ProviderId

__all__ = ["router"]

router = APIRouter(prefix="/llm", tags=["llm"])


class ProviderBody(BaseModel):
    """``{"provider": "openrouter"}``, or an empty body for the configured one."""

    provider: str | None = None


class CredentialCheckData(BaseModel):
    provider: str
    ok: bool
    detail: str = ""
    models: list[str] = []


class ModelsData(BaseModel):
    provider: str
    models: list[str]


class RateLimitData(BaseModel):
    """The process-wide budget. ``stopped`` is the latch the UI clears."""

    stopped: bool
    retries: int
    remaining: int


class LlmCallsData(BaseModel):
    calls: list[dict[str, Any]]
    running: int


class LlmStatusData(BaseModel):
    """Everything the settings page's LLM section needs in one read."""

    provider: str
    providers: list[str]
    base_url: str
    models: dict[str, str]
    effort_levels: list[str]
    timeout_s: float
    rate_limit: RateLimitData
    claude_code: dict[str, Any]


def _provider_id(raw: str | None) -> ProviderId | None:
    """Validate a provider name from the client, or 400 naming the valid ones."""
    if raw is None or not raw.strip():
        return None
    if raw not in PROVIDER_IDS:
        raise BadRequest(
            f"Unknown LLM provider {raw!r}",
            details={"provider": raw, "known_providers": list(PROVIDER_IDS)},
        )
    return raw


def _observer(request: Request) -> LlmCallObserver:
    observer: LlmCallObserver = request.app.state.llm.observer
    return observer


@router.post(
    "/validate",
    response_model=ApiResponse[CredentialCheckData],
    summary="Check that a provider's credentials work",
)
async def validate_provider(
    body: ProviderBody, service: LlmServiceDep, request: Request
) -> JSONResponse:
    """The cheapest call each provider offers — a model list, or `claude auth status`.

    Never an actual completion: a validate button that costs tokens is one
    people stop pressing.
    """
    check = await service.validate_credentials(_provider_id(body.provider))
    return envelope_response(
        CredentialCheckData(
            provider=check.provider, ok=check.ok, detail=check.detail, models=check.models
        ).model_dump(mode="json")
    )


@router.get(
    "/models",
    response_model=ApiResponse[ModelsData],
    summary="Model ids a provider offers",
)
async def list_models(
    service: LlmServiceDep,
    provider: str | None = Query(default=None, description="Defaults to the configured provider."),
) -> JSONResponse:
    target = _provider_id(provider)
    config = await service.config()
    models = await service.list_models(target)
    return envelope_response(
        ModelsData(provider=target or config.provider, models=models).model_dump(mode="json")
    )


@router.get(
    "/rate-limit",
    response_model=ApiResponse[RateLimitData],
    summary="The process-wide rate-limit budget",
)
async def get_rate_limit(service: LlmServiceDep) -> JSONResponse:
    return envelope_response(service.budget.snapshot())


@router.post(
    "/rate-limit/reset",
    response_model=ApiResponse[RateLimitData],
    summary="Clear the rate-limit latch and restore the budget",
)
async def reset_rate_limit(service: LlmServiceDep) -> JSONResponse:
    """Explicit, because an automatic timer would just re-enter the same wall."""
    config = await service.config()
    service.budget.reset(config.rate_limit_retries)
    return envelope_response(service.budget.snapshot())


@router.get(
    "/calls",
    response_model=ApiResponse[LlmCallsData],
    summary="The last 50 calls, newest first",
)
async def get_calls(request: Request) -> JSONResponse:
    observer = _observer(request)
    return envelope_response(
        LlmCallsData(
            calls=[record.to_api() for record in observer.snapshot()],
            running=observer.running,
        ).model_dump(mode="json")
    )


@router.get(
    "/calls/stream",
    summary="Server-sent stream of call state changes",
    response_class=EventSourceResponse,
)
async def stream_calls(request: Request) -> EventSourceResponse:
    observer = _observer(request)
    bus = request.app.state.events
    return sse_response(_call_stream(observer, bus))


async def _call_stream(observer: LlmCallObserver, bus: Any) -> AsyncIterator[SseEvent]:
    # The snapshot first, then the live changes. The other order would race: a
    # call that finished between subscribing and snapshotting would arrive as
    # an update and then be overwritten by the older snapshot.
    yield SseEvent(
        event="llm.snapshot",
        data={
            "calls": [record.to_api() for record in observer.snapshot()],
            "running": observer.running,
        },
    )
    async for event in bus.stream():
        # The call ring, plus the analyzer's own lifecycle. An analysis is
        # an LLM call with a row behind it, and a client watching this stream to
        # know what the LLM is doing should not have to open the sync stream as
        # well to learn that one started.
        if event.event == LLM_CALL_EVENT or event.event in ANALYSIS_EVENTS:
            yield event


@router.get(
    "/usage",
    response_model=ApiResponse[UsageTotals],
    summary="Token and duration totals from the call ledger",
)
async def get_usage(
    request: Request,
    since: str | None = Query(
        default=None,
        description="ISO-8601 timestamp. Omit for everything ever recorded.",
    ),
) -> JSONResponse:
    repo = LlmCallsRepository(request.app.state.db)
    totals = await repo.totals(since)
    return envelope_response(totals.model_dump(mode="json"))


@router.get(
    "/status",
    response_model=ApiResponse[LlmStatusData],
    summary="Provider, per-purpose models, the latch, and the Claude Code panel",
)
async def get_status(service: LlmServiceDep, settings: SettingsServiceDep) -> JSONResponse:
    config = await service.config()
    claude_code: dict[str, Any] = {"binary": config.claude_code_bin}
    if config.provider == "claude_code":
        # Probing the CLI means spawning it twice, so it is done only when the
        # CLI is the thing in use. A box configured for OpenRouter should not
        # pay for a subprocess on every settings page load.
        provider = ClaudeCodeProvider(
            binary=config.claude_code_bin,
            oauth_token=config.claude_code_oauth_token,
            effort=config.claude_code_effort,
        )
        check = await provider.validate_credentials()
        claude_code |= {
            "version": await provider.version(),
            "authenticated": check.ok,
            "detail": check.detail,
            "effort": config.claude_code_effort,
        }
    return envelope_response(
        LlmStatusData(
            provider=config.provider,
            providers=list(PROVIDER_IDS),
            base_url=config.base_url,
            models={purpose: config.resolve_model(purpose) for purpose in config.models},  # type: ignore[arg-type]
            effort_levels=list(CLAUDE_CODE_EFFORT_LEVELS),
            timeout_s=config.timeout_s,
            rate_limit=RateLimitData.model_validate(service.budget.snapshot()),
            claude_code=claude_code,
        ).model_dump(mode="json")
    )
