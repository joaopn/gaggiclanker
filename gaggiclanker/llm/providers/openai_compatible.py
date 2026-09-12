"""Everything that speaks ``/v1/chat/completions``, which is nearly everything.

OpenAI, OpenRouter, Ollama, LM Studio and every in-house gateway answer the
same request shape, so they are one provider with a table of presets rather
than five implementations. What actually differs between them is three facts —
the base URL, whether a key is required, and which response modes the endpoint
implements — and only the third cannot be known in advance, which is what the
mode fallback in the service exists to discover.

The SDK is used rather than raw HTTP because it already handles the awkward
parts (streaming-off defaults, retries we then switch off, error types that
carry the status). Its own retry is disabled on purpose: this layer has a retry
policy, a per-attempt deadline and a rate-limit budget, and an SDK quietly
retrying a 429 three times underneath all of that would spend the account's
budget without the budget noticing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2
import structlog
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.providers.base import ProviderCall, ProviderReply
from gaggiclanker.llm.schema import schema_name, strict_json_schema
from gaggiclanker.llm.types import CredentialCheck, ProviderId, ResponseMode, Usage

__all__ = ["PRESETS", "OpenAiCompatibleProvider", "Preset", "normalize_base_url"]

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Preset:
    """The three facts that differ between OpenAI-compatible endpoints."""

    base_url: str
    requires_api_key: bool
    modes: tuple[ResponseMode, ...]
    #: Extra request headers. OpenRouter uses these for its public leaderboard
    #: attribution; nothing depends on them working.
    headers: dict[str, str] | None = None
    #: Lets the user point the provider somewhere else. Off for the hosted
    #: services, because a base URL the caller controls plus a key we hold is
    #: how an API key gets exfiltrated to an attacker's host.
    allows_base_url: bool = False


#: The presets the settings page offers.
PRESETS: dict[str, Preset] = {
    "openrouter": Preset(
        base_url="https://openrouter.ai/api/v1",
        requires_api_key=True,
        modes=("json_schema", "json_object", "text"),
        headers={"HTTP-Referer": "https://github.com/gaggiclanker", "X-Title": "gaggiclanker"},
    ),
    "openai": Preset(
        base_url="https://api.openai.com/v1",
        requires_api_key=True,
        modes=("json_schema", "json_object", "text"),
    ),
    "ollama": Preset(
        base_url="http://localhost:11434/v1",
        requires_api_key=False,
        # Ollama implements json_schema (it maps to its own `format`) and plain
        # text, but not OpenAI's `json_object`, which it answers with a 400.
        modes=("json_schema", "text"),
        allows_base_url=True,
    ),
    "lmstudio": Preset(
        base_url="http://localhost:1234/v1",
        requires_api_key=False,
        modes=("json_schema", "json_object", "text"),
        allows_base_url=True,
    ),
    "openai_compatible": Preset(
        base_url="",
        requires_api_key=False,
        modes=("json_schema", "json_object", "text"),
        allows_base_url=True,
    ),
}

# The SDK refuses to construct without a key. A keyless endpoint (Ollama, LM
# Studio) gets this placeholder, which those servers ignore.
_PLACEHOLDER_KEY = "not-required"

# Path suffixes that mean "the user pasted the endpoint, not the base".
_ENDPOINT_SUFFIXES = ("/chat/completions", "/v1/chat/completions")

# Base paths that are already versioned, so `/v1` must not be appended again.
_VERSIONED = ("/v1", "/v1beta", "/api/v1", "/openai/v1")


def normalize_base_url(raw: str) -> str:
    """Make a pasted URL into something the SDK can join paths onto.

    People paste the full endpoint out of a provider's curl example about as
    often as they paste the base, and the resulting
    ``/v1/chat/completions/chat/completions`` 404 says nothing about what went
    wrong. Both forms, with or without a trailing slash, end up the same here.
    """
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    for suffix in _ENDPOINT_SUFFIXES:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    url = url.rstrip("/")
    if not url.endswith(_VERSIONED):
        url = f"{url}/v1"
    return url


class OpenAiCompatibleProvider:
    """One provider for the whole ``/v1/chat/completions`` family."""

    def __init__(
        self,
        *,
        preset: str = "openai_compatible",
        base_url: str = "",
        api_key: str = "",
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._preset_name = preset
        self.preset = PRESETS.get(preset, PRESETS["openai_compatible"])
        self.id: ProviderId = preset if preset in PRESETS else "openai_compatible"  # type: ignore[assignment]
        resolved = normalize_base_url(base_url) if self.preset.allows_base_url else ""
        self.base_url = resolved or self.preset.base_url
        self.api_key = api_key
        self.modes = self.preset.modes
        self.requires_api_key = self.preset.requires_api_key

        if self.preset.requires_api_key and not api_key:
            # Constructed anyway: the service turns "no credential" into an
            # `auth` result without a request, and `validate_credentials` has
            # to be able to say so too.
            log.debug("llm_provider_missing_key", provider=self.id)

        self.client = AsyncOpenAI(
            api_key=api_key or _PLACEHOLDER_KEY,
            base_url=self.base_url or None,
            default_headers=dict(self.preset.headers or {}),
            # Retries, deadlines and the rate-limit budget all live one layer
            # up. An SDK retrying underneath them would spend the account's
            # 429 budget invisibly and blow the per-attempt deadline.
            max_retries=0,
            http_client=http_client,
        )

    def missing_credential(self) -> str | None:
        if self.preset.requires_api_key and not self.api_key:
            return (
                f"No API key is configured for {self.id}. Set llmApiKey in Settings, or "
                "GAGGICLANKER_LLM_API_KEY in the environment."
            )
        return None

    # -- the call ---------------------------------------------------------

    async def complete(self, call: ProviderCall) -> ProviderReply:
        body: dict[str, Any] = {
            "model": call.model,
            "messages": [{"role": m.role, "content": m.content} for m in call.messages],
            "stream": False,
        }
        response_format = self._response_format(call)
        if response_format is not None:
            body["response_format"] = response_format

        try:
            completion = await self.client.chat.completions.create(**body, timeout=call.timeout_s)
        except APIStatusError as exc:
            raise LlmApiError(
                _status_message(exc),
                status=exc.status_code,
                body=_error_body(exc),
            ) from exc
        except APITimeoutError as exc:
            raise LlmApiError(f"the provider timed out after {call.timeout_s:g}s") from exc
        except APIConnectionError as exc:
            raise LlmApiError(f"connection to {self.base_url} failed: {exc}") from exc
        except OpenAIError as exc:
            raise LlmApiError(str(exc)) from exc

        text = _extract_text(completion)
        if not text:
            # A 200 with no content is a real failure mode of thin gateways
            # under load. "parse" is in the message deliberately: the retry
            # policy keys on it, and trying again is exactly right here.
            raise LlmApiError("the provider returned no content to parse")
        return ProviderReply(text=text, usage=_extract_usage(completion))

    def _response_format(self, call: ProviderCall) -> dict[str, Any] | None:
        if call.mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name(call.output_model),
                    "strict": True,
                    "schema": strict_json_schema(call.output_model),
                },
            }
        if call.mode == "json_object":
            return {"type": "json_object"}
        # text: send nothing at all rather than `{"type": "text"}`. A gateway
        # that does not implement response_format at all rejects the key
        # itself, and this is the mode we fall back *to* — it has to be the
        # plainest request the endpoint could possibly accept.
        return None

    # -- diagnostics ------------------------------------------------------

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
            detail=f"{len(models)} models available at {self.base_url}",
            models=models[:50],
        )

    async def list_models(self) -> list[str]:
        """``GET /v1/models``. The cheapest call that proves the key works."""
        try:
            page = await self.client.models.list()
        except APIStatusError as exc:
            raise LlmApiError(
                _status_message(exc), status=exc.status_code, body=_error_body(exc)
            ) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise LlmApiError(f"could not reach {self.base_url}: {exc}") from exc
        except OpenAIError as exc:
            raise LlmApiError(str(exc)) from exc
        return sorted(model.id for model in page.data)

    async def aclose(self) -> None:
        await self.client.close()


def _status_message(exc: APIStatusError) -> str:
    """The provider's own words, not the SDK's wrapper, where there are any."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
        if isinstance(error, str):
            return error
        if isinstance(body.get("message"), str):
            return str(body["message"])
    return exc.message or f"HTTP {exc.status_code}"


def _error_body(exc: APIStatusError) -> str:
    """The raw body, which is what capability detection pattern-matches on."""
    try:
        return exc.response.text
    except Exception:  # pragma: no cover - a body already consumed by the SDK
        return str(exc.body)


def _extract_text(completion: Any) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    content = getattr(getattr(choices[0], "message", None), "content", None)
    return content if isinstance(content, str) else ""


def _extract_usage(completion: Any) -> Usage:
    """Token counts under either of the two names in circulation.

    OpenAI says ``prompt_tokens``/``completion_tokens``; several gateways
    forward an upstream Anthropic-shaped ``input_tokens``/``output_tokens``
    instead. Reading both is two lines and saves a usage table full of nulls.
    """
    usage = getattr(completion, "usage", None)
    if usage is None:
        return Usage()
    prompt = _first_int(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _first_int(usage, "completion_tokens", "output_tokens")
    return Usage(prompt_tokens=prompt, completion_tokens=completion_tokens)


def _first_int(source: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(source, name, None)
        if value is None and isinstance(source, dict):
            value = source.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
