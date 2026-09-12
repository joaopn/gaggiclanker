"""Claude over the Messages API, for a box that has an API key rather than a CLI.

Three things differ from the chat-completions family and each one is a place
this file exists to absorb:

* **The system prompt is a top-level field**, not a message with
  ``role: "system"``. A system message left in the list is a 400.
* **Structured output is the SDK's**, not a ``response_format`` object. The
  installed SDK (1.5.0) has ``messages.parse(output_format=Model)``, which
  sends the schema through ``output_config`` and hands back a parsed instance,
  so the schema is not hand-assembled here at all — the pydantic model is
  passed straight in. The reply is still validated against that model by the
  service: an SDK's parse is the SDK's contract, not ours.
* **``max_tokens`` is required.** There is no "as many as you need", so a
  number has to be chosen, and a too-small one truncates mid-JSON and looks
  like a parse bug.

Prompt caching is switched on for the system block. The analysis prompt is the
same few thousand tokens on every shot in a backfill, and a cache read is a
tenth of the price of a fresh read; the marker costs nothing when the prompt is
below the cache minimum, because the API just ignores it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import structlog
from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
)

from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.base import ProviderCall, ProviderReply
from gaggiclanker.llm.schema import strict_json_schema
from gaggiclanker.llm.types import CredentialCheck, ProviderId, ResponseMode, Usage

__all__ = ["AnthropicProvider"]

log = structlog.get_logger(__name__)

#: Required by the API and not inferable. Generous: a truncated reply is an
#: unparseable one, and the cost is per token emitted, not per token allowed.
DEFAULT_MAX_TOKENS = 8192

#: What the settings page offers. The Messages API has a ``/v1/models``
#: endpoint, so this is only the fallback when listing fails.
SUGGESTED_MODELS: tuple[str, ...] = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)

# Asking for JSON in the weak mode. Anthropic has no `json_object` equivalent,
# so `text` is the only fallback and the instruction has to carry the whole
# requirement.
_TEXT_MODE_INSTRUCTION = (
    "Reply with a single JSON object conforming to this JSON Schema, and with "
    "nothing else — no prose, no code fence:\n{schema}"
)


class AnthropicProvider:
    """The Messages API, with the system block hoisted and cached."""

    id: ProviderId = "anthropic"
    #: No `json_object`: the API has exactly two levels of insistence, a
    #: schema or an instruction.
    modes: tuple[ResponseMode, ...] = ("json_schema", "text")

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.client = AsyncAnthropic(
            api_key=api_key or None,
            base_url=base_url or None,
            # As with the OpenAI client: retries, deadlines and the rate-limit
            # budget belong to the service, and an SDK retrying underneath them
            # spends the account's 429 allowance without the budget seeing it.
            max_retries=0,
            http_client=http_client,
        )

    def missing_credential(self) -> str | None:
        if not self.api_key:
            return (
                "No Anthropic API key is configured. Set anthropicApiKey in Settings, or "
                "ANTHROPIC_API_KEY in the environment."
            )
        return None

    async def complete(self, call: ProviderCall) -> ProviderReply:
        system, messages = _split_system(call)
        kwargs: dict[str, Any] = {
            "model": call.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "timeout": call.timeout_s,
        }
        if system:
            kwargs["system"] = system

        try:
            if call.mode == "json_schema":
                parsed = await self.client.messages.parse(output_format=call.output_model, **kwargs)
                return ProviderReply(
                    text=_join_text(parsed),
                    usage=_extract_usage(parsed),
                    data=_first_parsed(parsed),
                )
            message = await self.client.messages.create(**kwargs)
        except APIStatusError as exc:
            raise LlmApiError(
                _status_message(exc), status=exc.status_code, body=_error_body(exc)
            ) from exc
        except APITimeoutError as exc:
            raise LlmApiError(f"the provider timed out after {call.timeout_s:g}s") from exc
        except APIConnectionError as exc:
            raise LlmApiError(f"connection to the Anthropic API failed: {exc}") from exc
        except AnthropicError as exc:
            raise LlmApiError(str(exc)) from exc

        text = _join_text(message)
        if not text:
            raise LlmApiError("the provider returned no content to parse")
        return ProviderReply(text=text, usage=_extract_usage(message))

    async def validate_credentials(self) -> CredentialCheck:
        missing = self.missing_credential()
        if missing is not None:
            return CredentialCheck(provider=self.id, ok=False, detail=missing)
        try:
            models = await self.list_models()
        except LlmApiError as exc:
            return CredentialCheck(provider=self.id, ok=False, detail=str(exc))
        return CredentialCheck(
            provider=self.id,
            ok=True,
            detail=f"{len(models)} models available",
            models=models[:50],
        )

    async def list_models(self) -> list[str]:
        try:
            page = await self.client.models.list(limit=100)
        except APIStatusError as exc:
            raise LlmApiError(
                _status_message(exc), status=exc.status_code, body=_error_body(exc)
            ) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise LlmApiError(f"could not reach the Anthropic API: {exc}") from exc
        except AnthropicError as exc:
            raise LlmApiError(str(exc)) from exc
        return sorted(model.id for model in page.data)

    async def aclose(self) -> None:
        await self.client.close()


def _split_system(call: ProviderCall) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Hoist every system message into the top-level block, and mark it cached.

    One block rather than several: ``cache_control`` marks a cache *breakpoint*
    and the API allows only a handful of them, so joining first and marking
    once is both cheaper and impossible to get wrong as prompts grow.
    """
    system_parts = [m.content for m in call.messages if m.role == "system"]
    messages = [{"role": m.role, "content": m.content} for m in call.messages if m.role != "system"]
    if call.mode == "text":
        system_parts.append(
            _TEXT_MODE_INSTRUCTION.format(
                schema=json.dumps(strict_json_schema(call.output_model), indent=2)
            )
        )
    if not system_parts:
        return [], messages
    return [
        {
            "type": "text",
            "text": "\n\n".join(system_parts),
            "cache_control": {"type": "ephemeral"},
        }
    ], messages


def _join_text(message: Any) -> str:
    parts = [
        block.text
        for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ]
    return "\n".join(parts).strip()


def _first_parsed(message: Any) -> Any:
    for block in getattr(message, "content", []):
        parsed = getattr(block, "parsed_output", None)
        if parsed is not None:
            return parsed
    return None


def _extract_usage(message: Any) -> Usage:
    """Input tokens are the sum of fresh, cache-write and cache-read.

    All three are billed as input and all three are real work; reporting only
    ``input_tokens`` on a cached call makes a 30k-token prompt look like 20
    tokens, which is exactly the number someone would use to conclude the
    analysis is free.
    """
    usage = getattr(message, "usage", None)
    if usage is None:
        return Usage()
    parts = [
        getattr(usage, name, None)
        for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ]
    numbers = [value for value in parts if isinstance(value, int) and not isinstance(value, bool)]
    prompt = sum(numbers) if numbers else None
    output = getattr(usage, "output_tokens", None)
    completion = output if isinstance(output, int) and not isinstance(output, bool) else None
    return Usage(prompt_tokens=prompt, completion_tokens=completion)


def _status_message(exc: APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
    return exc.message or f"HTTP {exc.status_code}"


def _error_body(exc: APIStatusError) -> str:
    try:
        return exc.response.text
    except Exception:  # pragma: no cover - a body already consumed by the SDK
        return str(exc.body)
