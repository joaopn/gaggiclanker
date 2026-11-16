"""What a provider says when it has no key or no address tells the person to *set* a setting.

A mechanical rename of the noun "Set" once also rewrote the verb, and the message
read "Recipe llmApiKey under Settings". The text is read through the method that
produces it, which is also what the service hands back as the refusal.
"""

from __future__ import annotations

from gaggiclanker.llm.providers.anthropic import AnthropicProvider
from gaggiclanker.llm.providers.openai_compatible import OpenAiCompatibleProvider


def test_anthropic_without_a_key_says_to_set_it() -> None:
    assert AnthropicProvider(api_key="").missing_credential() == (
        "No Anthropic API key is configured. Set anthropicApiKey under Settings → LLM."
    )


def test_a_hosted_preset_without_a_key_says_to_set_it() -> None:
    provider = OpenAiCompatibleProvider(preset="openrouter", api_key="")

    assert provider.missing_credential() == (
        f"No API key is configured for {provider.id}. Set llmApiKey under Settings → LLM."
    )


def test_a_gateway_without_an_address_says_to_set_it() -> None:
    provider = OpenAiCompatibleProvider(preset="openai_compatible", api_key="k", base_url="")

    assert provider.missing_credential() == (
        f"No base URL is configured for {provider.id}. Set llmBaseUrl under Settings → LLM."
    )
