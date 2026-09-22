"""Turning the settings registry into a provider, a credential and a model.

Three rules are encoded here rather than left to the call sites:

**A credential belongs to one provider.** ``llmApiKey`` is the key for whatever
``llmProvider`` names; it is never lent to a different provider because a caller
asked for one by name. Otherwise a "validate Anthropic" button on the settings
page would send the OpenRouter key to Anthropic.

**A base URL is only honoured where it is safe.** Redirecting a hosted
provider's endpoint while still sending its key is how a key ends up on
someone else's server, so ``llmBaseUrl`` applies to the self-hosted presets and
the generic one, and is ignored for OpenAI and OpenRouter.

**A model is chosen per purpose, falling back.** The careful analysis and a
quick chat turn should not have to share one model, but nobody wants to fill in
four boxes to get started — so each purpose falls back to ``modelDefault`` and
then to the provider's own default (an empty string, which every provider reads
as "you choose").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from gaggiclanker.llm.providers.anthropic import AnthropicProvider
from gaggiclanker.llm.providers.base import Provider
from gaggiclanker.llm.providers.claude_code import ClaudeCodeProvider
from gaggiclanker.llm.providers.openai_compatible import PRESETS, OpenAiCompatibleProvider
from gaggiclanker.llm.types import ModelPurpose, ProviderId
from gaggiclanker.settings_service import SettingsService

__all__ = ["DRAFT_KEYS", "LlmConfig", "build_provider", "load_llm_config"]

#: Every provider the settings page offers, in the order it offers them.
PROVIDER_IDS: tuple[ProviderId, ...] = (
    "openrouter",
    "openai",
    "ollama",
    "lmstudio",
    "openai_compatible",
    "anthropic",
    "claude_code",
)

#: The settings a "Validate credentials" may try before they are saved: exactly
#: what decides who answers and with which credential. Nothing else — a draft
#: that could move the timeout or the models would be testing a different call
#: from the one the button describes.
DRAFT_KEYS: tuple[str, ...] = (
    "llmProvider",
    "llmBaseUrl",
    "llmApiKey",
    "anthropicApiKey",
    "claudeCodeOauthToken",
    "claudeCodeBin",
    "claudeCodeEffort",
)

#: Which settings key holds the per-purpose model. ``default`` is the fallback
#: for all of them, so it is not in this map.
_PURPOSE_KEYS: dict[ModelPurpose, str] = {
    "analysis": "modelAnalysis",
    "draft": "modelDraft",
    "chat": "modelChat",
    "starting_point": "modelStartingPoint",
}


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """Everything a call needs, resolved once."""

    provider: ProviderId = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    claude_code_bin: str = "claude"
    claude_code_effort: str = ""
    timeout_s: float = 300.0
    rate_limit_retries: int = 2
    store_call_text: bool = True
    models: dict[str, str] = field(default_factory=dict)
    #: Where the archive lives. Not a setting — it comes from ``EnvSettings`` —
    #: but it belongs on the snapshot because exactly one provider needs it:
    #: ``claude_code`` hands it to the MCP server it spawns for the chat, so the
    #: CLI's tool loop reads the same database this process opened.
    data_dir: str = ""

    def resolve_model(self, purpose: ModelPurpose = "default") -> str:
        """The model for ``purpose``: its own, then the default, then empty."""
        specific = self.models.get(purpose, "").strip()
        if specific:
            return specific
        return self.models.get("default", "").strip()

    def credential_for(self, provider: ProviderId) -> str:
        """The key this provider is allowed to use, which may be none.

        ``llmApiKey`` is the *configured* provider's key, so a named provider
        that is not the configured one gets nothing unless it has a key of its
        own (Anthropic and Claude Code do).
        """
        if provider == "anthropic":
            return self.anthropic_api_key
        if provider == "claude_code":
            return self.claude_code_oauth_token
        if provider == self.provider:
            return self.api_key
        return ""


async def load_llm_config(
    settings: SettingsService, *, data_dir: str = "", draft: Mapping[str, Any] | None = None
) -> LlmConfig:
    """Read the registry once and hand back a frozen snapshot.

    A snapshot rather than a live reader because a single call must not see the
    provider change halfway through its retries — the mode it remembered and
    the key it authenticated with would then belong to different endpoints.

    ``draft`` is effective values (already validated) laid over the stored
    ones, for a configuration that is being typed but has not been saved.
    """
    overlay = draft or {}

    async def text(key: str) -> str:
        value = overlay[key] if key in overlay else await settings.get(key)
        return str(value or "").strip()

    provider = await text("llmProvider")
    if provider not in PROVIDER_IDS:
        provider = "openai_compatible"

    return LlmConfig(
        provider=provider,  # type: ignore[arg-type]
        base_url=await text("llmBaseUrl"),
        api_key=await text("llmApiKey"),
        anthropic_api_key=await text("anthropicApiKey"),
        claude_code_oauth_token=await text("claudeCodeOauthToken"),
        claude_code_bin=await text("claudeCodeBin") or "claude",
        claude_code_effort=await text("claudeCodeEffort"),
        timeout_s=float(await settings.get("llmTimeoutSeconds")),
        rate_limit_retries=int(await settings.get("llmRateLimitRetries")),
        store_call_text=bool(await settings.get("llmStoreCallText")),
        models={
            "default": await text("modelDefault"),
            **{purpose: await text(key) for purpose, key in _PURPOSE_KEYS.items()},
        },
        data_dir=data_dir,
    )


def build_provider(config: LlmConfig, provider: ProviderId | None = None) -> Provider:
    """Construct the provider named by ``provider``, defaulting to the configured one."""
    target: ProviderId = provider or config.provider
    if target == "claude_code":
        return ClaudeCodeProvider(
            binary=config.claude_code_bin,
            oauth_token=config.claude_code_oauth_token,
            effort=config.claude_code_effort,
            data_dir=config.data_dir,
        )
    if target == "anthropic":
        return AnthropicProvider(api_key=config.anthropic_api_key)
    preset = PRESETS.get(target, PRESETS["openai_compatible"])
    return OpenAiCompatibleProvider(
        preset=target,
        # Only the presets that declare it take a user-supplied base URL; see
        # the module docstring.
        base_url=config.base_url if preset.allows_base_url else "",
        api_key=config.credential_for(target),
    )
