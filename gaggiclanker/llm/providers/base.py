"""What every provider has to be able to do, and nothing more.

A provider's whole job is: given messages and a response mode, come back with
text (or, when the SDK already parsed it, an object) plus whatever token counts
it disclosed. It does not retry, it does not classify, it does not fall back to
another mode and it does not know what a pydantic model is beyond deriving a
schema from one. All of that is the service's, so it is written once instead of
three times — which is the reason a subprocess and an HTTPS call can sit behind
the same interface.

The one thing a provider *must* get right is raising
:class:`~gaggiclanker.llm.errors.LlmApiError` with the status and body it was
given, because those two fields are what the service's classification and mode
fallback are built on. A provider that swallows a 429 into a bare ``Exception``
turns a rate-limit latch into three pointless retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from gaggiclanker.llm.chat_types import ChatRequest, ChatTurn, OnChatEvent
from gaggiclanker.llm.types import (
    CredentialCheck,
    LlmMessage,
    ProviderId,
    ResponseMode,
    Usage,
)

__all__ = ["Provider", "ProviderCall", "ProviderReply"]


@dataclass(slots=True)
class ProviderCall:
    """One attempt, in one mode."""

    mode: ResponseMode
    model: str
    messages: list[LlmMessage]
    output_model: type[BaseModel]
    timeout_s: float
    #: ``claude_code`` only. Carried here rather than in a provider-specific
    #: argument so the service does not need a branch to pass it on.
    effort: str = ""


@dataclass(slots=True)
class ProviderReply:
    """What came back, before any validation."""

    text: str
    usage: Usage = field(default_factory=Usage)
    #: Set when the SDK parsed the reply itself (the Anthropic SDK does). The
    #: service prefers it over re-parsing ``text``, but still validates it
    #: against the output model — an SDK's parse is not our contract.
    data: Any = None


@runtime_checkable
class Provider(Protocol):
    """The interface the service talks to."""

    #: Which provider this is, for logs, usage rows and the mode memory key.
    id: ProviderId
    #: The endpoint, for the mode memory key. Empty for a CLI provider, which
    #: has one capability profile regardless of where it connects.
    base_url: str
    #: Modes to try, strongest first.
    modes: tuple[ResponseMode, ...]

    async def complete(self, call: ProviderCall) -> ProviderReply:
        """Make one attempt. Raises :class:`LlmApiError` for a provider refusal."""
        ...

    async def chat(self, request: ChatRequest, on_event: OnChatEvent) -> ChatTurn:
        """One conversational turn with native tool use, streamed.

        The second half of the interface, added for the chat. It is on the same
        protocol as :meth:`complete` rather than on a separate one because the
        alternative — an ``isinstance`` check at the one call site — moves a
        compile-time guarantee into a runtime branch that is only exercised by
        the provider somebody forgot to implement.

        ``on_event`` is called as chunks arrive and must not be awaited; the
        completed turn is the return value. Raises :class:`LlmApiError` for a
        provider refusal, exactly as ``complete`` does, so the chat runner
        classifies failures with the same code.
        """
        ...

    def missing_credential(self) -> str | None:
        """Why this provider cannot be called at all, or ``None`` if it can.

        Asked before every call, so a provider with no key fails as ``auth``
        without a request. That is not only tidier: an OpenAI-compatible client
        must be constructed with *some* key, so without this check OpenRouter
        would send a literal ``Bearer not-required`` and report back whatever
        that earns - a 401, a 402, or on an unlucky gateway a 200 of nonsense.
        """
        ...

    async def validate_credentials(self) -> CredentialCheck:
        """Cheapest possible "does this work at all" check."""
        ...

    async def list_models(self) -> list[str]:
        """Model ids to offer in the settings page. May be a static list."""
        ...

    async def aclose(self) -> None:
        """Release any transport the provider owns."""
        ...
