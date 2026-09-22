"""``call_json``: one structured call, however many attempts that takes.

Everything awkward about talking to a language model lives here so that no
caller has to know about any of it. Read from the outside in:

1. **The rate-limit latch.** If the process has already been stopped, the call
   returns immediately without touching the provider (:mod:`.budget`).
2. **Mode fallback.** ``json_schema``, then ``json_object``, then ``text``,
   skipping straight to the one that worked last time for this endpoint
   (:mod:`.modes`). Only a *capability* error advances to the next mode; any
   other failure is returned as-is, because a 401 is not fixed by asking less
   politely.
3. **Per-attempt deadline, retries, backoff.** Each attempt gets its own
   ``asyncio.timeout``; a transient failure is retried with linear backoff. The
   caller's cancellation is never swallowed — it is not a provider failure, and
   a caller that gave up should not wait out three more attempts.
4. **One corrective retry.** If the reply is JSON but not the shape the output
   model describes, the model is shown its own answer and the validation error
   and asked for a corrected object. Once. A model that cannot fix it in one
   turn will not fix it in three, and each turn costs the whole prompt again.
5. **Classification.** Exactly one :class:`Err` code comes out, and the caller
   branches on it rather than on a string.

The result is always a value. The only exception that escapes is
``CancelledError``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from gaggiclanker.db.repos.llm import LlmCallRow, LlmCallsRepository
from gaggiclanker.infra.errors import BadRequest
from gaggiclanker.llm.budget import RateLimitBudget, get_rate_limit_budget
from gaggiclanker.llm.config import DRAFT_KEYS, LlmConfig, build_provider, load_llm_config
from gaggiclanker.llm.errors import (
    LlmApiError,
    classify_llm_error,
    is_capability_error,
    should_retry,
)
from gaggiclanker.llm.json_utils import parse_json_content
from gaggiclanker.llm.modes import ModeMemory, get_mode_memory
from gaggiclanker.llm.observer import CallHandle, LlmCallObserver
from gaggiclanker.llm.providers.base import Provider, ProviderCall
from gaggiclanker.llm.types import (
    CredentialCheck,
    Err,
    LlmMessage,
    LlmRequest,
    LlmResult,
    Ok,
    ProviderId,
    ResponseMode,
    Usage,
)
from gaggiclanker.settings_service import SettingsService

__all__ = ["CORRECTIVE_INSTRUCTION", "LlmService", "ProviderFactory"]

log = structlog.get_logger(__name__)

#: What the model is told when its JSON did not validate. Short and literal:
#: the useful content is the pydantic error, and padding it with apology
#: reliably makes smaller models apologise back instead of answering.
CORRECTIVE_INSTRUCTION = (
    "That reply did not match the required schema:\n\n{errors}\n\n"
    "Return the corrected JSON object only — no prose, no code fence, no "
    "explanation. Keep every value you got right and fix only what is listed above."
)

#: Shown by every call once the latch is set.
STOP_MESSAGE = (
    "The LLM is stopped: the provider's rate limit was hit and the retry budget "
    "is spent. Clear it from Settings once the limit has reset."
)

#: How a provider is constructed. A parameter so a test can hand the service a
#: fake without patching module state, and so the settings page can build a
#: second provider to validate without disturbing the cached one.
type ProviderFactory = Callable[[LlmConfig, ProviderId | None], Provider]


@dataclass(slots=True)
class _Capability:
    """Internal: this mode is not implemented, try the next one."""

    message: str


class LlmService:
    """The single entry point. One per app, held on ``app.state``."""

    def __init__(
        self,
        settings: SettingsService,
        *,
        observer: LlmCallObserver | None = None,
        calls_repo: LlmCallsRepository | None = None,
        budget: RateLimitBudget | None = None,
        mode_memory: ModeMemory | None = None,
        provider_factory: ProviderFactory = build_provider,
        data_dir: str = "",
    ) -> None:
        self.settings = settings
        #: Passed through to the config snapshot for the one provider that needs
        #: it; see :class:`~gaggiclanker.llm.config.LlmConfig`.
        self.data_dir = data_dir
        self.observer = observer or LlmCallObserver()
        self.calls_repo = calls_repo
        self.budget = budget or get_rate_limit_budget()
        self.mode_memory = mode_memory or get_mode_memory()
        self._build_provider = provider_factory
        self._provider: Provider | None = None
        self._provider_key: tuple[str, ...] | None = None

    # -- configuration ----------------------------------------------------

    async def config(self) -> LlmConfig:
        return await load_llm_config(self.settings, data_dir=self.data_dir)

    async def resolve_model(self, purpose: str = "default") -> str:
        config = await self.config()
        return config.resolve_model(purpose)  # type: ignore[arg-type]

    async def provider_for(self, config: LlmConfig, name: ProviderId | None = None) -> Provider:
        """The provider, cached until the configuration that produced it changes.

        Building one means building an HTTP client, and settings change rarely;
        caching on the tuple of things that matter means a settings edit takes
        effect on the very next call without anyone having to remember to
        invalidate anything.
        """
        target = name or config.provider
        key = (
            target,
            config.base_url,
            config.credential_for(target)[:8],
            config.claude_code_bin,
            config.claude_code_effort,
        )
        if self._provider is not None and self._provider_key == key:
            return self._provider
        if self._provider is not None:
            await self._provider.aclose()
        provider = self._build_provider(config, target)
        self._provider = provider
        self._provider_key = key
        return provider

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()
            self._provider = None
            self._provider_key = None

    # -- the call ---------------------------------------------------------

    async def call_json[T: BaseModel](self, request: LlmRequest[T]) -> LlmResult[T]:
        """Make one structured call. Never raises for a provider failure.

        The whole body is wrapped, including the global rate-limit loop. An
        observer entry that was registered and never finalised spins in the
        header for the life of the process and leaves no row in the ledger, so
        a cancellation - a closed browser tab, a shutdown - has to be recorded
        and then re-raised rather than allowed to escape past the bookkeeping.
        """
        config = await self.config()
        self.budget.seed(config.rate_limit_retries)

        model = request.model.strip() or config.resolve_model(request.purpose)
        handle = self.observer.register(
            label=request.label,
            subject=request.subject,
            provider=config.provider,
            model=model or "(provider default)",
            purpose=request.purpose,
        )
        started = time.monotonic()

        try:
            outcome = await self._run(request, config, model)
        except BaseException as exc:
            # BaseException, not Exception: CancelledError is the case this
            # exists for, and it is not an Exception. The entry is closed and
            # the row written before the exception carries on, because a caller
            # that gave up still spent whatever had already been spent.
            message = _cancellation_message(exc)
            handle.fail(message)
            await self._persist(
                request,
                Err(code="unknown", message=message, provider=config.provider, model=model),
                handle,
                config,
                model,
            )
            log.info(
                "llm_call_abandoned",
                provider=config.provider,
                model=model,
                label=request.label,
                reason=type(exc).__name__,
            )
            raise

        duration_ms = int((time.monotonic() - started) * 1000)
        # Stamp the ledger id on the way out, once, rather than threading it
        # through every construction site below. A caller that stores an outcome
        # of its own needs to be able to find the row with the prompt on it.
        outcome = replace(outcome, call_id=handle.record.id)
        if isinstance(outcome, Ok):
            handle.succeed(outcome.usage, mode=outcome.mode)
        else:
            handle.fail(outcome.message, mode=outcome.mode, usage=outcome.usage)

        _log_call(request, outcome, duration_ms=duration_ms, model=model)
        await self._persist(request, outcome, handle, config, model)
        return outcome

    async def _run[T: BaseModel](
        self, request: LlmRequest[T], config: LlmConfig, model: str
    ) -> LlmResult[T]:
        """Everything between registering the call and finalising it."""
        if self.budget.stopped:
            # No provider contact at all: that is the whole point of the latch.
            return Err(code="rate_limited", message=STOP_MESSAGE, provider=config.provider)

        try:
            provider = await self.provider_for(config)
        except Exception as exc:  # a misconfigured provider must not 500 the caller
            return Err(
                code="unknown",
                message=f"could not build the {config.provider} provider: {exc}",
                provider=config.provider,
                model=model,
            )

        # Before anything leaves the box. A provider with no credential cannot
        # succeed, and sending a placeholder key to find that out costs a round
        # trip and tells the user "401" instead of "you have not set a key".
        missing = provider.missing_credential()
        if missing is not None:
            return Err(code="auth", message=missing, provider=provider.id, model=model)

        outcome: LlmResult[T] = await self._call_inner(request, config, provider, model)
        while isinstance(outcome, Err) and outcome.code == "rate_limited":
            # The budget is global: one shared allowance for the whole process,
            # then the latch. See gaggiclanker/llm/budget.py.
            if not self.budget.consume():
                return replace(outcome, message=f"{outcome.message} ({STOP_MESSAGE})")
            outcome = await self._call_inner(request, config, provider, model)
        return outcome

    async def _call_inner[T: BaseModel](
        self, request: LlmRequest[T], config: LlmConfig, provider: Provider, model: str
    ) -> LlmResult[T]:
        modes = self.mode_memory.order(provider.id, provider.base_url, provider.modes)
        last: Err | None = None
        for mode in modes:
            outcome = await self._try_mode(request, config, provider, model, mode)
            if isinstance(outcome, _Capability):
                log.debug(
                    "llm_mode_unsupported",
                    provider=provider.id,
                    mode=mode,
                    detail=outcome.message,
                )
                last = Err(
                    code="unknown",
                    message=outcome.message,
                    provider=provider.id,
                    model=model,
                    mode=mode,
                )
                continue
            if isinstance(outcome, Ok):
                self.mode_memory.remember(provider.id, provider.base_url, mode)
            return outcome
        return last or Err(
            code="unknown",
            message="the provider rejected every response mode",
            provider=provider.id,
            model=model,
        )

    async def _try_mode[T: BaseModel](
        self,
        request: LlmRequest[T],
        config: LlmConfig,
        provider: Provider,
        model: str,
        mode: ResponseMode,
    ) -> LlmResult[T] | _Capability:
        timeout_s = request.timeout_s or config.timeout_s
        messages = list(request.messages)
        attempt = 0
        corrective_used = False
        # What this mode has already cost. A retry and a corrective turn are
        # both billed, so the figure the ledger wants is the running total, not
        # what the last reply happened to report.
        spent = Usage()
        last_raw = ""

        while True:
            if attempt > 0:
                # Linear, not exponential: the failures worth retrying here are
                # a stalled gateway or a transient 5xx, which clear in seconds.
                # Exponential backoff on a 300 s deadline mostly buys the user
                # a longer wait for the same failure.
                await asyncio.sleep(request.retry_delay_s * attempt)

            call = ProviderCall(
                mode=mode,
                model=model,
                messages=messages,
                output_model=request.output_model,
                timeout_s=timeout_s,
                effort=request.effort or config.claude_code_effort,
            )

            try:
                async with asyncio.timeout(timeout_s):
                    reply = await provider.complete(call)
            except TimeoutError:
                message = (
                    f"the provider did not answer within {timeout_s:g}s. Raise "
                    "llmTimeoutSeconds in Settings if this model legitimately needs longer."
                )
                if attempt < request.max_retries:
                    attempt += 1
                    continue
                return Err(
                    code="timeout",
                    message=message,
                    retryable=True,
                    provider=provider.id,
                    model=model,
                    mode=mode,
                    usage=spent,
                    raw=last_raw,
                )
            except asyncio.CancelledError:
                # The caller gave up. Not a provider failure, and swallowing it
                # would keep their task alive past the deadline they set.
                raise
            except LlmApiError as exc:
                if is_capability_error(status=exc.status, body=exc.body or exc.message, mode=mode):
                    return _Capability(str(exc))
                message = str(exc)
                if attempt < request.max_retries and should_retry(
                    status=exc.status, message=message
                ):
                    log.debug(
                        "llm_attempt_failed",
                        provider=provider.id,
                        mode=mode,
                        attempt=attempt,
                        status=exc.status,
                    )
                    attempt += 1
                    continue
                return Err(
                    code=classify_llm_error(status=exc.status, message=message),
                    message=message,
                    status=exc.status,
                    retryable=should_retry(status=exc.status, message=message),
                    provider=provider.id,
                    model=model,
                    mode=mode,
                    usage=spent,
                    raw=last_raw,
                )
            except Exception as exc:
                # A provider is a third-party SDK and a subprocess; between them
                # they can raise anything, and an SDK constructed without a key
                # raises a TypeError rather than one of its own errors. Letting
                # that escape would break the contract this whole module is
                # built on - that a provider failure is a value - and would
                # reach the caller as a 500 instead of an `unknown`.
                log.warning(
                    "llm_provider_raised",
                    provider=provider.id,
                    mode=mode,
                    error=type(exc).__name__,
                    exc_info=exc,
                )
                return Err(
                    code="unknown",
                    message=f"the {provider.id} provider raised {type(exc).__name__}: {exc}",
                    retryable=False,
                    provider=provider.id,
                    model=model,
                    mode=mode,
                    usage=spent,
                    raw=last_raw,
                )

            spent = spent + reply.usage
            last_raw = reply.text

            try:
                payload = reply.data if reply.data is not None else parse_json_content(reply.text)
            except ValueError as exc:
                if attempt < request.max_retries:
                    attempt += 1
                    continue
                return Err(
                    code="invalid_output",
                    message=str(exc),
                    provider=provider.id,
                    model=model,
                    mode=mode,
                    usage=spent,
                    raw=last_raw,
                )

            try:
                data = request.output_model.model_validate(payload)
            except ValidationError as exc:
                if not corrective_used:
                    # One correction, and it is a new question rather than a
                    # retry: no backoff, and it does not spend the retry
                    # budget, which exists for transport failures.
                    corrective_used = True
                    messages = [
                        *messages,
                        LlmMessage(role="assistant", content=reply.text),
                        LlmMessage(
                            role="user",
                            content=CORRECTIVE_INSTRUCTION.format(errors=_render_errors(exc)),
                        ),
                    ]
                    continue
                return Err(
                    code="invalid_output",
                    message=f"the reply did not match {request.output_model.__name__}: {exc}",
                    provider=provider.id,
                    model=model,
                    mode=mode,
                    usage=spent,
                    raw=last_raw,
                )

            return Ok(
                data=data,
                usage=spent,
                raw=reply.text,
                provider=provider.id,
                model=model,
                mode=mode,
            )

    # -- diagnostics ------------------------------------------------------

    async def validate_credentials(
        self, provider: ProviderId | None = None, draft: dict[str, Any] | None = None
    ) -> CredentialCheck:
        """Does this provider work at all? The cheapest call each one offers.

        ``draft`` is what the settings form holds and has not saved, keyed like
        a ``PATCH /api/settings`` body: the question people ask with the button
        is "does what I just typed work", and answering it about the stored
        values made a pasted token look missing until it was saved. The draft is
        validated exactly as a PATCH would be and written nowhere.
        """
        config = await self.draft_config(draft) if draft else await self.config()
        target = provider or config.provider
        built = self._build_provider(config, target)
        try:
            return await built.validate_credentials()
        finally:
            if built is not self._provider:
                await built.aclose()

    async def draft_config(self, draft: dict[str, Any]) -> LlmConfig:
        """The configuration as it would be once ``draft`` is saved. Writes nothing.

        One rule is stricter than saving: the stored ``llmApiKey`` belongs to
        the stored provider at the stored address, so a draft that moves either
        without typing a key of its own validates with no key at all. Otherwise
        moving the picker from OpenRouter to OpenAI and pressing Validate would
        send the OpenRouter key to OpenAI before anyone chose to save that.
        """
        if any(key not in DRAFT_KEYS for key in draft):
            raise BadRequest(
                "Only the provider settings can be validated unsaved",
                details={"allowed": list(DRAFT_KEYS)},
            )
        validated = await self.settings.validate(draft)
        effective = await self.settings.effective_after(validated, tuple(validated))
        stored = await self.config()
        config = await load_llm_config(self.settings, data_dir=self.data_dir, draft=effective)
        moved = config.provider != stored.provider or config.base_url != stored.base_url
        if moved and "llmApiKey" not in validated:
            config = replace(config, api_key="")
        return config

    async def list_models(self, provider: ProviderId | None = None) -> list[str]:
        config = await self.config()
        target = provider or config.provider
        built = self._build_provider(config, target)
        try:
            return await built.list_models()
        except LlmApiError as exc:
            log.info("llm_list_models_failed", provider=target, error=str(exc))
            return []
        finally:
            if built is not self._provider:
                await built.aclose()

    # -- persistence ------------------------------------------------------

    async def _persist[T: BaseModel](
        self,
        request: LlmRequest[T],
        result: LlmResult[T],
        handle: CallHandle,
        config: LlmConfig,
        model: str,
    ) -> None:
        """Write the usage row. A failure here never fails the call.

        The row is the only durable record of what the LLM cost; the observer's
        ring is gone at the next restart. But a call that succeeded and then
        could not be logged still succeeded, and turning that into an error
        would lose the analysis as well as the accounting.
        """
        if self.calls_repo is None:
            return
        # Usage on a failure is not always empty: a reply that arrived and then
        # failed validation was billed exactly like one that passed.
        usage = result.usage
        try:
            await self.calls_repo.record(
                LlmCallRow(
                    call_id=handle.record.id,
                    purpose=request.purpose,
                    label=request.label,
                    subject=request.subject or None,
                    provider=result.provider or config.provider,
                    model=model or None,
                    mode=result.mode or None,
                    prompt_name=request.prompt_name,
                    prompt_version=request.prompt_version,
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    duration_ms=handle.record.duration_ms or handle.duration_ms,
                    status="succeeded" if isinstance(result, Ok) else "failed",
                    error=None if isinstance(result, Ok) else result.message[:500],
                    input_text=_capped(_render_messages(request.messages))
                    if config.store_call_text
                    else None,
                    output_text=_capped(result.raw) if config.store_call_text else None,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("llm_call_not_recorded", error=str(exc))


#: How much of the prompt and the reply a ledger row keeps. A shot's samples
#: rendered into a prompt are tens of kilobytes; an accidental loop is
#: megabytes, and the archive is the thing that must not grow without bound.
MAX_STORED_TEXT = 200_000


def _capped(text: str) -> str | None:
    """``text`` for the ledger, truncated with a note saying so."""
    if not text:
        return None
    if len(text) <= MAX_STORED_TEXT:
        return text
    return text[:MAX_STORED_TEXT] + f"\n...[truncated at {MAX_STORED_TEXT} characters]"


def _render_messages(messages: list[LlmMessage]) -> str:
    """The conversation as one document, for the ledger's ``input_text``."""
    return "\n\n".join(f"[{message.role}]\n{message.content}" for message in messages)


def _cancellation_message(exc: BaseException) -> str:
    """What the ledger says about a call that never finished.

    A cancellation is named as one because it is the common case and reads very
    differently from a crash; anything else keeps its type, which is the only
    handle on a bug that got past the provider's own catch-all.
    """
    if isinstance(exc, asyncio.CancelledError):
        return "the call was cancelled before it finished"
    return f"the call was abandoned: {type(exc).__name__}: {exc}"


def _render_errors(exc: ValidationError) -> str:
    """The validation failures as a short list a model can act on.

    pydantic's own ``str(exc)`` carries a docs URL and the input value on every
    line; the input value is the model's own words coming straight back, which
    at best wastes tokens and at worst re-primes it on the wrong answer.
    """
    lines = []
    for error in exc.errors()[:10]:
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)


def _log_call[T: BaseModel](
    request: LlmRequest[T], result: LlmResult[T], *, duration_ms: int, model: str
) -> None:
    """The one structured line per call. Never the prompt, never the reply."""
    usage = result.usage
    log.info(
        "llm_call_completed",
        provider=result.provider,
        model=model,
        mode=result.mode,
        purpose=request.purpose,
        label=request.label,
        duration_ms=duration_ms,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        status="ok" if isinstance(result, Ok) else "error",
        error_code=None if isinstance(result, Ok) else result.code,
        error_status=None if isinstance(result, Ok) else result.status,
    )
