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
from gaggiclanker.infra.errors import BadRequest, Conflict
from gaggiclanker.infra.sse import SseEvent, sse_response
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.claude_cli import CHANNELS, ClaudeCliManager, valid_target
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
    """``{"provider": "openrouter"}``, or an empty body for the configured one.

    ``settings`` is the settings form's unsaved provider values, shaped like a
    ``PATCH /api/settings`` body (secrets only when typed): validate then tests
    what is on the screen rather than what was last saved.
    """

    provider: str | None = None
    settings: dict[str, Any] | None = None


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


class ClaudeCliBinary(BaseModel):
    path: str | None
    version: str | None


class ClaudeCliJob(BaseModel):
    """The last install this process ran; ``idle`` when there has been none."""

    state: str
    target: str
    version: str
    message: str
    started_at: str | None
    finished_at: str | None


class ClaudeCliStatusData(BaseModel):
    """The Claude Code updater: which binary runs, what npm offers, the last install."""

    platform_package: str | None
    bundled: ClaudeCliBinary
    managed: ClaudeCliBinary
    overridden: bool
    active_binary: str
    channels: dict[str, str]
    job: ClaudeCliJob


class ClaudeCliInstallBody(BaseModel):
    """``{"version": "stable"}``: a channel (stable, latest) or an exact version."""

    version: str = "stable"


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
    """The cheapest call each provider offers — a model list, or a one-word `claude -p`.

    Never an actual completion: a validate button that costs tokens is one
    people stop pressing. Unsaved ``settings`` are tried, never stored.
    """
    check = await service.validate_credentials(_provider_id(body.provider), body.settings)
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
        # `auth status` alone: this runs on every settings page load, and the
        # paid probe belongs to the Validate button.
        check = await provider.auth_status()
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


# -- the Claude Code CLI updater --------------------------------------------

#: One install at a time, process-wide; the name is the idempotency rule.
CLAUDE_CLI_TASK = "claude-cli-install"


def _claude_cli(request: Request) -> ClaudeCliManager:
    manager: ClaudeCliManager = request.app.state.claude_cli
    return manager


async def _claude_cli_status(request: Request, settings: SettingsServiceDep) -> dict[str, Any]:
    manager = _claude_cli(request)
    data = await manager.status(configured_bin=str(await settings.get("claudeCodeBin") or ""))
    return ClaudeCliStatusData.model_validate(data).model_dump(mode="json")


@router.get(
    "/claude-cli",
    response_model=ApiResponse[ClaudeCliStatusData],
    summary="The Claude Code CLI in use, the image's own, and the releases npm offers",
)
async def get_claude_cli(request: Request, settings: SettingsServiceDep) -> JSONResponse:
    """Asks npm for its dist-tags (cached for ten minutes); never installs anything."""
    return envelope_response(await _claude_cli_status(request, settings))


@router.post(
    "/claude-cli/install",
    response_model=ApiResponse[ClaudeCliStatusData],
    status_code=202,
    summary="Install a Claude Code release into the data directory",
)
async def install_claude_cli(
    body: ClaudeCliInstallBody, request: Request, settings: SettingsServiceDep
) -> JSONResponse:
    """Starts the download and answers at once; the page polls ``GET`` for the outcome.

    The release is verified against npm's sha512 and run once before it is
    switched to, so a failed install leaves the binary in use untouched.
    """
    target = body.version.strip()
    if not valid_target(target):
        raise BadRequest(
            "version must be a channel or an exact version like 2.1.267",
            details={"field": "version", "channels": list(CHANNELS)},
        )
    manager = _claude_cli(request)
    tasks: TaskRegistry = request.app.state.tasks
    if manager.running or tasks.get(CLAUDE_CLI_TASK) is not None:
        raise Conflict("A Claude Code install is already running")
    manager.begin(target)
    tasks.spawn(CLAUDE_CLI_TASK, manager.install(target))
    return envelope_response(await _claude_cli_status(request, settings), status_code=202)


@router.delete(
    "/claude-cli",
    response_model=ApiResponse[ClaudeCliStatusData],
    summary="Remove the installed Claude Code release and go back to the image's",
)
async def remove_claude_cli(request: Request, settings: SettingsServiceDep) -> JSONResponse:
    manager = _claude_cli(request)
    if manager.running:
        raise Conflict("A Claude Code install is running; wait for it to finish")
    manager.remove()
    return envelope_response(await _claude_cli_status(request, settings))
