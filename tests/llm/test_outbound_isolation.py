"""Every credential for an external service comes from the database, and only from there.

The rule is the maintainer's: all auth tokens of any kind that connect to any
external service live only inside the database. Taking the environment variables
out of the settings registry is half of it. The other half is what the libraries
underneath would pick up on their own — an SDK's key, bearer-token, extra-header,
organisation and base-URL variables, a profile file, a ``.netrc``, a proxy with a
password in it — and what the Claude Code CLI child would inherit.

So every candidate is set to a sentinel, with a ``.netrc`` and an Anthropic
profile in ``HOME``, and each provider is built the way the app builds it
(:func:`gaggiclanker.llm.config.build_provider`). The request it sends — through
the real outbound client, with only the network swapped for a recorder — and the
child environment the CLI would get must carry the stored credential and the
stored or preset base URL, and no sentinel anywhere: not in the URL, not in a
header, not in a proxy mount.

Each environment fallback is also shown to be live with a plain SDK client, so
this module fails if a variable stops mattering rather than passing vacuously.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from gaggiclanker.infra import outbound
from gaggiclanker.infra.outbound import environment_proxy_mounts, outbound_http_client
from gaggiclanker.llm.config import LlmConfig, build_provider
from gaggiclanker.llm.providers import anthropic as anthropic_module
from gaggiclanker.llm.providers import openai_compatible as openai_module
from gaggiclanker.llm.providers.anthropic import ANTHROPIC_API_BASE_URL, AnthropicProvider
from gaggiclanker.llm.providers.claude_code import build_child_env
from gaggiclanker.llm.providers.openai_compatible import PRESETS, OpenAiCompatibleProvider

SENTINEL = "sentinel-5c1f0e"
PROXY_WITH_PASSWORD = f"http://someone:{SENTINEL}@proxy.test:3128"

#: Every variable an SDK, the HTTP stack or the CLI would read a credential, a
#: header or a destination from.
CANDIDATES: dict[str, str] = {
    # The app's own retired names.
    "GAGGICLANKER_LLM_API_KEY": f"{SENTINEL}-app-key",
    "ANTHROPIC_API_KEY": f"{SENTINEL}-anthropic-key",
    "CLAUDE_CODE_OAUTH_TOKEN": f"{SENTINEL}-oauth",
    # The OpenAI SDK.
    "OPENAI_API_KEY": f"{SENTINEL}-openai-key",
    "OPENAI_ADMIN_KEY": f"{SENTINEL}-openai-admin",
    "OPENAI_ORG_ID": f"{SENTINEL}-org",
    "OPENAI_PROJECT_ID": f"{SENTINEL}-project",
    "OPENAI_BASE_URL": f"https://{SENTINEL}.example/v1",
    "OPENAI_WEBHOOK_SECRET": f"{SENTINEL}-webhook",
    "OPENAI_CUSTOM_HEADERS": f"Authorization: Bearer {SENTINEL}\nX-Sentinel: {SENTINEL}",
    "OPENROUTER_API_KEY": f"{SENTINEL}-openrouter",
    # The Anthropic SDK.
    "ANTHROPIC_AUTH_TOKEN": f"{SENTINEL}-bearer",
    "ANTHROPIC_BASE_URL": f"https://{SENTINEL}.example",
    "ANTHROPIC_CUSTOM_HEADERS": f"x-api-key: {SENTINEL}\nAuthorization: Bearer {SENTINEL}",
    "ANTHROPIC_PROFILE": "default",
    "ANTHROPIC_WEBHOOK_SIGNING_KEY": f"{SENTINEL}-signing",
    "ANTHROPIC_IDENTITY_TOKEN": f"{SENTINEL}-identity",
    "ANTHROPIC_FEDERATION_RULE_ID": f"{SENTINEL}-rule",
    "ANTHROPIC_ORGANIZATION_ID": f"{SENTINEL}-organization",
    # Proxies with a password, in both spellings.
    "HTTP_PROXY": PROXY_WITH_PASSWORD,
    "HTTPS_PROXY": PROXY_WITH_PASSWORD,
    "ALL_PROXY": PROXY_WITH_PASSWORD,
    "http_proxy": PROXY_WITH_PASSWORD,
    "https_proxy": PROXY_WITH_PASSWORD,
    "all_proxy": PROXY_WITH_PASSWORD,
}

STORED_KEY = "sk-stored-in-the-database"
STORED_GATEWAY = "http://gateway.test:8080/v1"


@pytest.fixture
def hostile_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every candidate variable set, and credential files where the libraries look for them."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".netrc").write_text(f"default login someone password {SENTINEL}\n", encoding="utf-8")
    (home / ".netrc").chmod(0o600)
    config_dir = home / ".config" / "anthropic"
    (config_dir / "configs").mkdir(parents=True)
    (config_dir / "credentials").mkdir()
    (config_dir / "configs" / "default.json").write_text(
        json.dumps(
            {"authentication": {"type": "user_oauth"}, "base_url": f"https://{SENTINEL}.profile"}
        ),
        encoding="utf-8",
    )
    credentials = config_dir / "credentials" / "default.json"
    credentials.write_text(json.dumps({"access_token": f"{SENTINEL}-profile"}), encoding="utf-8")
    credentials.chmod(0o600)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    for name, value in CANDIDATES.items():
        monkeypatch.setenv(name, value)
    yield home


class Recorder:
    """Stands in for the network behind the real outbound client."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.host == "api.anthropic.com" or "x-api-key" in request.headers:
            body: dict[str, Any] = {
                "data": [],
                "has_more": False,
                "first_id": None,
                "last_id": None,
            }
        else:
            body = {"object": "list", "data": []}
        return httpx2.Response(200, json=body)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Route the providers' own outbound client to a recorder, and nothing else about it."""
    recording = Recorder()

    def with_recorder() -> httpx2.AsyncClient:
        return outbound_http_client(transport=httpx2.MockTransport(recording))

    monkeypatch.setattr(openai_module, "outbound_http_client", with_recorder)
    monkeypatch.setattr(anthropic_module, "outbound_http_client", with_recorder)
    return recording


def config(**overrides: Any) -> LlmConfig:
    values: dict[str, Any] = {
        "provider": "openai",
        "base_url": "",
        "api_key": STORED_KEY,
        "anthropic_api_key": STORED_KEY,
        "claude_code_oauth_token": "stored-oauth-token",
    }
    values.update(overrides)
    return LlmConfig(**values)


def assert_no_sentinel(request: httpx2.Request) -> None:
    assert SENTINEL not in str(request.url)
    for name, value in request.headers.items():
        assert SENTINEL not in name, name
        assert SENTINEL not in value, (name, value)
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers


def assert_client_holds_no_sentinel(http_client: httpx2.AsyncClient) -> None:
    assert http_client.trust_env is False
    assert http_client.follow_redirects is False
    # Every proxy in the environment carries a password, so none is mounted.
    assert http_client._mounts == {}
    assert SENTINEL not in repr(http_client._mounts)


# ── the fallbacks are real ───────────────────────────────────────────


async def test_the_sentinels_would_reach_a_plain_sdk_client(hostile_environment: Path) -> None:
    """Without the guarantees, each of these would change what is sent."""
    plain_openai = AsyncOpenAI()
    assert plain_openai.api_key == CANDIDATES["OPENAI_API_KEY"]
    assert SENTINEL in str(plain_openai.base_url)
    assert plain_openai.organization == CANDIDATES["OPENAI_ORG_ID"]
    assert SENTINEL in json.dumps(dict(plain_openai._custom_headers))

    plain_anthropic = AsyncAnthropic()
    assert plain_anthropic.api_key == CANDIDATES["ANTHROPIC_API_KEY"]
    assert plain_anthropic.auth_token == CANDIDATES["ANTHROPIC_AUTH_TOKEN"]
    assert SENTINEL in str(plain_anthropic.base_url)
    assert SENTINEL in json.dumps(dict(plain_anthropic._custom_headers))

    # The HTTP stack's own environment proxies, before the credentials are removed.
    from httpx2._utils import get_environment_proxies

    assert any(SENTINEL in (url or "") for url in get_environment_proxies().values())
    await plain_openai.close()
    await plain_anthropic.close()


# ── the OpenAI-compatible presets ────────────────────────────────────


@pytest.mark.parametrize(
    ("preset", "stored_key", "stored_base", "expected_base", "expected_bearer"),
    [
        ("openrouter", STORED_KEY, "", PRESETS["openrouter"].base_url, STORED_KEY),
        ("openai", STORED_KEY, "", PRESETS["openai"].base_url, STORED_KEY),
        ("ollama", "", "", PRESETS["ollama"].base_url, "not-required"),
        ("lmstudio", "", "", PRESETS["lmstudio"].base_url, "not-required"),
        ("openai_compatible", STORED_KEY, STORED_GATEWAY, STORED_GATEWAY, STORED_KEY),
        # A hosted preset with a stored base URL ignores it, and still no sentinel.
        ("openai", STORED_KEY, STORED_GATEWAY, PRESETS["openai"].base_url, STORED_KEY),
    ],
)
async def test_an_openai_compatible_request_carries_only_what_was_stored(
    hostile_environment: Path,
    recorder: Recorder,
    preset: str,
    stored_key: str,
    stored_base: str,
    expected_base: str,
    expected_bearer: str,
) -> None:
    provider = build_provider(config(provider=preset, api_key=stored_key, base_url=stored_base))
    assert isinstance(provider, OpenAiCompatibleProvider)
    assert provider.missing_credential() is None
    assert_client_holds_no_sentinel(provider.client._client)

    await provider.client.models.list()

    (request,) = recorder.requests
    assert str(request.url) == f"{expected_base}/models"
    assert request.headers["authorization"] == f"Bearer {expected_bearer}"
    assert_no_sentinel(request)
    await provider.client.close()


@pytest.mark.parametrize("preset", ["openrouter", "openai"])
async def test_a_hosted_preset_with_no_stored_key_is_refused_and_holds_no_sentinel(
    hostile_environment: Path, recorder: Recorder, preset: str
) -> None:
    provider = build_provider(config(provider=preset, api_key=""))
    assert isinstance(provider, OpenAiCompatibleProvider)
    assert "llmApiKey" in (provider.missing_credential() or "")
    assert provider.client.api_key == "not-required"
    assert SENTINEL not in str(provider.client.base_url)
    assert_client_holds_no_sentinel(provider.client._client)

    # Even a call that skipped the refusal would carry no environment key.
    await provider.client.models.list()
    assert_no_sentinel(recorder.requests[0])
    await provider.client.close()


async def test_a_gateway_with_no_stored_base_url_is_a_configuration_error(
    hostile_environment: Path, recorder: Recorder
) -> None:
    """Otherwise the SDK would take OPENAI_BASE_URL and send the stored key there."""
    provider = build_provider(config(provider="openai_compatible", base_url=""))
    assert isinstance(provider, OpenAiCompatibleProvider)
    assert "llmBaseUrl" in (provider.missing_credential() or "")
    assert SENTINEL not in str(provider.client.base_url)
    assert str(provider.client.base_url).startswith("https://llm-base-url-not-configured.invalid")
    check = await provider.validate_credentials()
    assert check.ok is False
    assert recorder.requests == []
    await provider.client.close()


# ── Anthropic ────────────────────────────────────────────────────────


async def test_an_anthropic_request_carries_only_the_stored_key(
    hostile_environment: Path, recorder: Recorder
) -> None:
    provider = build_provider(config(provider="anthropic"))
    assert isinstance(provider, AnthropicProvider)
    assert provider.missing_credential() is None
    assert provider.client.auth_token is None
    assert provider.client.credentials is None
    assert_client_holds_no_sentinel(provider.client._client)

    await provider.client.models.list()

    (request,) = recorder.requests
    assert str(request.url).startswith(f"{ANTHROPIC_API_BASE_URL}/v1/models")
    assert request.headers["x-api-key"] == STORED_KEY
    # No bearer from the environment, the profile file or the .netrc.
    assert "authorization" not in request.headers
    assert_no_sentinel(request)
    await provider.client.close()


async def test_anthropic_with_no_stored_key_is_refused_and_holds_no_sentinel(
    hostile_environment: Path, recorder: Recorder
) -> None:
    provider = build_provider(config(provider="anthropic", anthropic_api_key=""))
    assert isinstance(provider, AnthropicProvider)
    assert "anthropicApiKey" in (provider.missing_credential() or "")
    assert provider.client.api_key == ""
    assert provider.client.auth_token is None
    assert provider.client.credentials is None
    assert str(provider.client.base_url).rstrip("/") == ANTHROPIC_API_BASE_URL

    # A call that skipped the refusal cannot even be built: the SDK finds no
    # credential, because it was given an empty one and looked nowhere else.
    with pytest.raises(TypeError, match="Could not resolve authentication method"):
        await provider.client.models.list()
    assert recorder.requests == []
    await provider.client.close()


# ── the Claude Code CLI child ────────────────────────────────────────


def test_the_cli_child_gets_the_stored_token_and_no_sentinel(hostile_environment: Path) -> None:
    provider = build_provider(config(provider="claude_code"))
    env = build_child_env(scratch_home="/tmp/scratch", oauth_token=provider.oauth_token)  # type: ignore[attr-defined]

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "stored-oauth-token"
    assert env["HOME"] == "/tmp/scratch"
    for name, value in env.items():
        assert SENTINEL not in name
        assert SENTINEL not in value, name
    assert not any(name.upper().endswith("_PROXY") for name in env)


def test_the_cli_child_with_no_stored_token_gets_none_at_all(hostile_environment: Path) -> None:
    env = build_child_env(scratch_home="/tmp/scratch", oauth_token="")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert all(SENTINEL not in value for value in env.values())


# ── proxies ──────────────────────────────────────────────────────────


def test_a_proxy_without_a_password_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy is a route; only one carrying credentials is dropped."""
    for name in ("HTTP_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")

    assert environment_proxy_mounts() == {"https://": "http://proxy.test:3128"}
    client = outbound_http_client()
    assert len(client._mounts) == 1
    env = build_child_env(scratch_home="/tmp/scratch")
    assert env["HTTPS_PROXY"] == "http://proxy.test:3128"


def test_a_proxy_with_a_password_is_never_mounted(hostile_environment: Path) -> None:
    assert environment_proxy_mounts() == {}
    assert outbound_http_client()._mounts == {}


@pytest.mark.parametrize(
    ("value", "carries"),
    [
        ("http://proxy.test:3128", False),
        ("proxy.test:3128", False),
        ("http://user:pass@proxy.test:3128", True),
        ("user@proxy.test:3128", True),
        ("https://token@proxy.test", True),
        ("", False),
    ],
)
def test_userinfo_is_recognised_with_or_without_a_scheme(value: str, carries: bool) -> None:
    assert outbound.url_carries_userinfo(value) is carries
