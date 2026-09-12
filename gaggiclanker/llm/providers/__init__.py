"""One module per way of reaching a model. See :mod:`.base` for the interface."""

from __future__ import annotations

from gaggiclanker.llm.providers.anthropic import AnthropicProvider
from gaggiclanker.llm.providers.base import Provider, ProviderCall, ProviderReply
from gaggiclanker.llm.providers.claude_code import ClaudeCodeProvider
from gaggiclanker.llm.providers.openai_compatible import OpenAiCompatibleProvider

__all__ = [
    "AnthropicProvider",
    "ClaudeCodeProvider",
    "OpenAiCompatibleProvider",
    "Provider",
    "ProviderCall",
    "ProviderReply",
]
