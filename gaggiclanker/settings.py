"""Configuration: bootstrap environment plus a registry of runtime settings.

Two layers, on purpose:

**Bootstrap** (:class:`EnvSettings`) is what the process needs before there is a
database to read: where the data directory is, what to log at, what to bind.
Environment only, read once at startup.

**Runtime settings** (:data:`SETTINGS_REGISTRY`) are the keys the maintainer can
change from the UI without restarting the container. Each is declared once —
key, type, default, secret flag, environment variable, prose description — and
that one declaration drives the ``settings`` table, ``GET/PATCH /api/settings``,
validation, and the hint shown for a secret. Adding a setting is one entry, the
way cvclanker's ``settings-registry.ts`` works.

Precedence for a runtime setting is **database > environment > default**. The
environment is the operator's baseline (compose file, ``.env``); the database is
what the maintainer changed in the UI, so it has to win — otherwise a value set
in the UI would be silently ignored on a box that also sets the variable.

Secrets are write-only through the API: ``PATCH`` accepts one, ``GET`` returns
only a four-character hint, which is enough to tell "the right key is loaded"
from "the wrong key is loaded" without putting the key in a browser tab.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog
from dotenv import dotenv_values
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "DEFAULT_ENV_FILE",
    "SETTINGS_REGISTRY",
    "EnvSettings",
    "ResolvedSetting",
    "SettingDefinition",
    "SettingType",
    "SettingValueError",
    "is_argon2_hash",
    "load_dotenv_values",
    "secret_hint",
]


class SettingValueError(ValueError):
    """A value a setting's own validator rejected, with a message safe to echo.

    ``SettingsService.apply`` puts this message in ``error.details``, which goes
    to the client — so every message a validator raises must be a **fixed
    string** that says what the field wants, and must never quote the value it
    was given. The value can be a password.
    """


# Where both layers look for a dotenv file: the working directory, which is the
# repository root in a checkout and /app in the container.
DEFAULT_ENV_FILE = Path(".env")


def load_dotenv_values(path: Path | None = None) -> dict[str, str | None]:
    """Parse a dotenv file into a mapping. A missing file is an empty mapping.

    ``EnvSettings`` gets the same file through pydantic-settings; this is for
    the runtime registry, which resolves its keys itself and would otherwise
    ignore a file the bootstrap layer honours.
    """
    target = DEFAULT_ENV_FILE if path is None else path
    if not target.is_file():
        return {}
    return dict(dotenv_values(target))


log = structlog.get_logger(__name__)

SettingType = Literal["string", "int", "float", "bool"]

# How many leading characters of a secret the API discloses. Four is enough to
# recognise a key you pasted ("sk-p..." vs "sk-o...") and useless to anyone who
# does not already have it.
SECRET_HINT_LENGTH = 4

# What a rejected value is told, per declared type. Fixed strings, never
# f-strings carrying the input: these messages travel to the client in
# `error.details`, and the value being rejected may be the API key the user
# just pasted. `int("sk-live-...")` raises with the string in the message, so
# the coercion below never lets a ValueError's own text through.
_EXPECTED: dict[str, str] = {
    "string": "expected a string",
    "int": "expected an integer",
    "float": "expected a number",
    "bool": "expected a boolean",
}


class EnvSettings(BaseSettings):
    """Bootstrap configuration, read from the environment once at startup.

    Unprefixed names (``DATA_DIR``, ``LOG_LEVEL``, ``GAGGIMATE_HOST``) are
    accepted because they read naturally in a compose file, with the
    ``GAGGICLANKER_`` prefixed form as an alias for boxes that run several
    services and want a namespace.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    data_dir: Path = Field(
        default=Path("data"),
        validation_alias=AliasChoices("DATA_DIR", "GAGGICLANKER_DATA_DIR"),
        description="Directory holding the SQLite file and the backups/ subdirectory.",
    )
    log_level: str = Field(
        default="info",
        validation_alias=AliasChoices("LOG_LEVEL", "GAGGICLANKER_LOG_LEVEL"),
        description="debug, info, warning or error.",
    )
    log_json: bool = Field(
        default=True,
        validation_alias=AliasChoices("LOG_JSON", "GAGGICLANKER_LOG_JSON"),
        description="JSON log lines (containers) or human-readable console output (dev).",
    )
    host: str = Field(
        default="0.0.0.0",  # noqa: S104 - a container listens on all interfaces by design
        validation_alias=AliasChoices("HOST", "GAGGICLANKER_HOST"),
        description="Bind address for uvicorn.",
    )
    port: int = Field(
        default=8000,
        validation_alias=AliasChoices("PORT", "GAGGICLANKER_PORT"),
        description="Bind port for uvicorn.",
    )
    web_dist: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("WEB_DIST", "GAGGICLANKER_WEB_DIST"),
        description=(
            "Directory holding the built SPA. Unset means <repo>/web/dist, which is right "
            "for a source checkout; the container installs the package into a venv and so "
            "sets this explicitly."
        ),
    )
    auth_password: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_PASSWORD", "GAGGICLANKER_AUTH_PASSWORD"),
        description=(
            "The sign-in password in plain text, hashed once at boot into the settings table "
            "and never stored as given. Bootstrap-only and deliberately NOT a registry key, so "
            "it can never come back out of GET /api/settings - not even as a hint. Use "
            "AUTH_PASSWORD_HASH instead if you would rather the plain password never reached "
            "the environment at all."
        ),
    )
    auth_jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices("AUTH_JWT_SECRET", "GAGGICLANKER_AUTH_JWT_SECRET"),
        description=(
            "HS256 signing key for session tokens. Empty (the normal case) generates one into "
            "the runtime_secrets table on first boot and keeps it, so sessions survive a "
            "restart and a restored backup. Set it only to share sessions between processes; "
            "at least 32 characters, or the app refuses to sign anything with it."
        ),
    )
    cors_origins: str = Field(
        default="",
        validation_alias=AliasChoices("CORS_ORIGINS", "GAGGICLANKER_CORS_ORIGINS"),
        description=(
            "Comma-separated origins allowed to call the API. Empty in production, where the "
            "SPA is served from the same origin; the Vite dev server needs its own entry."
        ),
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "gaggiclanker.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    """One runtime setting. The single declaration the whole stack reads."""

    key: str
    type: SettingType
    default: Any
    description: str
    secret: bool = False
    env_key: str | None = None
    #: An extra rule on top of the type, run on every write. Returns an error
    #: message — a fixed string, never quoting the value — or ``None``.
    validate: Callable[[Any], str | None] | None = None
    #: Not settable through ``PATCH /api/settings``. The value still resolves
    #: from the environment and the database like any other, and the service
    #: can still write it through :meth:`SettingsService.store`; what this
    #: forbids is a browser form putting an arbitrary string in it. Used where
    #: a dedicated endpoint owns the write (``authPasswordHash``), because a
    #: masked text box invites somebody to type the password itself into it.
    readonly: bool = False

    @property
    def expected(self) -> str:
        """The message a rejected value gets. Never contains the value."""
        return _EXPECTED[self.type]

    def parse(self, raw: str) -> Any:
        """Coerce a stored/environment string into the declared type.

        Raises ``ValueError(self.expected)`` on anything that does not convert,
        which the PATCH route turns into a 400 and the environment reader turns
        into a warning plus the default. The message is fixed so neither path
        can echo the value back.
        """
        text = raw.strip()
        match self.type:
            case "string":
                return text
            case "int":
                try:
                    return int(text)
                except ValueError:
                    raise ValueError(self.expected) from None
            case "float":
                try:
                    return float(text)
                except ValueError:
                    raise ValueError(self.expected) from None
            case "bool":
                lowered = text.lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
                raise ValueError(self.expected)
        raise AssertionError(f"unhandled setting type {self.type}")  # pragma: no cover

    def serialize(self, value: Any) -> str:
        """Render a value for the TEXT column. Booleans are 'true'/'false'."""
        if self.type == "bool":
            return "true" if value else "false"
        return str(value)

    def coerce(self, value: Any) -> Any:
        """Validate an inbound JSON value, then apply the setting's own rule.

        Two steps because they answer different questions: the type check says
        "this could be stored", :attr:`validate` says "this is worth storing".
        """
        return self._checked(self._coerce_type(value))

    def _checked(self, value: Any) -> Any:
        """Run :attr:`validate`, if there is one, and raise on its message."""
        if self.validate is None:
            return value
        problem = self.validate(value)
        if problem is not None:
            raise SettingValueError(problem)
        return value

    def _coerce_type(self, value: Any) -> Any:
        """The type half of :meth:`coerce`.

        Booleans are rejected where an int is expected: JSON's ``true`` is a
        Python ``bool`` and ``int(True) == 1``, so without this a typo silently
        stores 1.

        Every rejection raises ``ValueError(self.expected)`` — a fixed string.
        The caller puts it in ``error.details``, which is sent to the client,
        and the value being rejected can be a secret.
        """
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return self.parse(value)
            raise ValueError(self.expected)
        if self.type == "int":
            if isinstance(value, bool):
                raise ValueError(self.expected)
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return self.parse(value)
            raise ValueError(self.expected)
        if self.type == "float":
            if isinstance(value, bool):
                raise ValueError(self.expected)
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                return self.parse(value)
            raise ValueError(self.expected)
        if isinstance(value, str):
            return value.strip()
        raise ValueError(self.expected)


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    """A setting's effective value and where it came from."""

    key: str
    type: SettingType
    value: Any
    default: Any
    override: Any
    source: Literal["database", "environment", "default"]
    secret: bool
    description: str
    #: Mirrors :attr:`SettingDefinition.readonly`. The UI reads it to render
    #: the key's state without an editable control, and the form skips it when
    #: building a PATCH.
    readonly: bool = False

    def to_api(self) -> dict[str, Any]:
        """The JSON shape ``GET /api/settings`` returns.

        A secret never renders its value, its default or its override — only
        whether one is configured, where it came from, and the hint.
        """
        if self.secret:
            return {
                "key": self.key,
                "type": self.type,
                "secret": True,
                "readonly": self.readonly,
                "configured": bool(self.value),
                "hint": secret_hint(self.value),
                "source": self.source,
                "description": self.description,
            }
        return {
            "key": self.key,
            "type": self.type,
            "secret": False,
            "readonly": self.readonly,
            "value": self.value,
            "default": self.default,
            "override": self.override,
            "source": self.source,
            "description": self.description,
        }


def secret_hint(value: Any) -> str | None:
    """The first few characters of a secret, or ``None`` when it is unset."""
    if not value:
        return None
    text = str(value)
    if len(text) <= SECRET_HINT_LENGTH:
        # Too short to hint without disclosing it outright; say it is set and
        # nothing more.
        return "*" * len(text)
    return text[:SECRET_HINT_LENGTH]


#: The PHC prefixes argon2 writes. A pure string check, kept here rather than in
#: ``auth/passwords.py`` so the registry can validate a value without importing
#: the auth package — settings is the bottom of the stack and stays there.
_ARGON2_PREFIXES: tuple[str, ...] = ("$argon2id$", "$argon2i$", "$argon2d$")


def is_argon2_hash(value: str) -> bool:
    """Whether ``value`` looks like an argon2 PHC string.

    Shape only; it says nothing about whether the hash verifies. What it
    catches is somebody typing the *password* into a field that wants its hash,
    which without this check is a value that can never authenticate anybody.
    """
    return value.startswith(_ARGON2_PREFIXES)


def _must_be_an_argon2_hash(value: Any) -> str | None:
    # Empty is allowed and means "no password configured", which is how auth is
    # switched off. Anything else has to be a hash.
    if not value or is_argon2_hash(str(value)):
        return None
    return (
        "expected an argon2id hash (a '$argon2id$...' string), not a password. "
        "Set the password with POST /api/auth/password, or put the plain "
        "password in AUTH_PASSWORD and restart."
    )


def _registry(*definitions: SettingDefinition) -> dict[str, SettingDefinition]:
    return {definition.key: definition for definition in definitions}


# The registry. Keys are camelCase because the front end consumes them
# directly. Later work appends its own entries here (device tuning, LLM
# providers and per-purpose models, auth); nothing else needs to change
# for a new setting to appear in the API and the UI.
SETTINGS_REGISTRY: dict[str, SettingDefinition] = _registry(
    SettingDefinition(
        key="gaggimateHost",
        type="string",
        default="",
        env_key="GAGGIMATE_HOST",
        description=(
            "Hostname or IP of the GaggiMate display board, without a scheme; an explicit "
            "host:port is accepted for a simulator or the fake device. Empty disables device "
            "sync. mDNS (gaggimate.local) is unreliable from inside a container and is off "
            "entirely when HomeKit is enabled, so prefer a fixed IP or a DHCP reservation."
        ),
    ),
    SettingDefinition(
        key="gaggimateProtocol",
        type="string",
        default="ws",
        env_key="GAGGIMATE_PROTOCOL",
        description=(
            "WebSocket scheme for the device connection: ws or wss. The firmware never "
            "terminates TLS (WebSocketHandler.cpp serves plain HTTP on port 80), so wss is only "
            "useful behind a reverse proxy that adds it."
        ),
    ),
    SettingDefinition(
        key="gaggimateTimeoutSeconds",
        type="float",
        default=15.0,
        env_key="GAGGIMATE_TIMEOUT_S",
        description=(
            "How long to wait for one device request — a WebSocket res:* frame or an HTTP "
            "body — before giving up. The machine's own web UI uses 30 s; shorter is better "
            "here because a stuck request holds one of only two HTTP slots."
        ),
    ),
    SettingDefinition(
        key="deviceSyncEnabled",
        type="bool",
        default=True,
        env_key="GAGGICLANKER_DEVICE_SYNC_ENABLED",
        description=(
            "Hold the WebSocket and mirror shots, profiles and notes. Turn off to work on an "
            "archive without touching the machine."
        ),
    ),
    SettingDefinition(
        key="devicePollIntervalSeconds",
        type="int",
        default=60,
        env_key="GAGGICLANKER_DEVICE_POLL_INTERVAL_SECONDS",
        description=(
            "How often to re-diff the shot index as a safety net behind "
            "evt:history-shot-saved, which is missed while the socket is down."
        ),
    ),
    SettingDefinition(
        key="llmProvider",
        type="string",
        default="claude_code",
        env_key="GAGGICLANKER_LLM_PROVIDER",
        description=(
            "Which provider answers a call: openrouter, openai, ollama, lmstudio, "
            "openai_compatible, anthropic or claude_code. claude_code is the default because "
            "it spends a Claude subscription the maintainer already pays for, rather than "
            "needing an API key nobody has yet."
        ),
    ),
    SettingDefinition(
        key="llmBaseUrl",
        type="string",
        default="",
        env_key="GAGGICLANKER_LLM_BASE_URL",
        description=(
            "Endpoint for the self-hosted and generic presets (ollama, lmstudio, "
            "openai_compatible), e.g. http://localhost:11434/v1. Ignored for the hosted "
            "providers: redirecting one of those while still sending its key is how an API "
            "key ends up on somebody else's server. Empty uses the preset's own URL."
        ),
    ),
    SettingDefinition(
        key="llmApiKey",
        type="string",
        default="",
        secret=True,
        env_key="GAGGICLANKER_LLM_API_KEY",
        description=(
            "API key for the configured openai-compatible provider. It belongs to that "
            "provider alone and is never sent to another one. Returned by the API as a "
            "four-character hint only."
        ),
    ),
    SettingDefinition(
        key="anthropicApiKey",
        type="string",
        default="",
        secret=True,
        env_key="ANTHROPIC_API_KEY",
        description=(
            "API key for the anthropic provider. Deliberately separate from llmApiKey so "
            "switching provider does not send one service's key to another. Never passed to "
            "the claude_code CLI, which uses the subscription token instead."
        ),
    ),
    SettingDefinition(
        key="claudeCodeOauthToken",
        type="string",
        default="",
        secret=True,
        env_key="CLAUDE_CODE_OAUTH_TOKEN",
        description=(
            "Subscription token for the claude_code provider. Mint it with "
            "`claude setup-token` and paste it here, or set CLAUDE_CODE_OAUTH_TOKEN in the "
            "environment. An interactive `claude login` is NOT enough: that writes "
            "~/.claude, and every call runs with a scratch HOME so no ambient CLAUDE.md or "
            "session state reaches the model - which hides those credentials too. A box that "
            "is logged in but has no token here answers `Not logged in - please run /login`."
        ),
    ),
    SettingDefinition(
        key="claudeCodeBin",
        type="string",
        default="claude",
        env_key="CLAUDE_CODE_BIN",
        description=(
            "The Claude Code binary to run. A bare name is looked up on PATH; give an "
            "absolute path when the CLI is installed somewhere uvicorn's PATH does not reach."
        ),
    ),
    SettingDefinition(
        key="claudeCodeEffort",
        type="string",
        default="",
        env_key="CLAUDE_CODE_EFFORT",
        description=(
            "How hard claude_code thinks: low, medium, high, xhigh or max. Empty lets the "
            "CLI decide. An unrecognised value is dropped rather than passed through."
        ),
    ),
    SettingDefinition(
        key="llmTimeoutSeconds",
        type="float",
        default=300.0,
        env_key="GAGGICLANKER_LLM_TIMEOUT_S",
        description=(
            "How long one attempt may take before it is abandoned. Five minutes: a reasoning "
            "model working through a shot's diagnostics genuinely takes minutes, and a "
            "deadline shorter than the work turns every analysis into a timeout."
        ),
    ),
    SettingDefinition(
        key="llmRateLimitRetries",
        type="int",
        default=2,
        env_key="GAGGICLANKER_LLM_RATE_LIMIT_RETRIES",
        description=(
            "How many times the whole process retries a rate limit before it latches and "
            "stops calling the provider at all. Shared by every call, not per call: when the "
            "account is throttled the next shot will be refused too, and failing sixty of "
            "them slowly is worse than stopping once. Clear the latch from this page. 0 "
            "stops at the first 429."
        ),
    ),
    SettingDefinition(
        key="llmStoreCallText",
        type="bool",
        default=True,
        env_key="GAGGICLANKER_LLM_STORE_CALL_TEXT",
        description=(
            "Keep the rendered prompt and the raw reply on each row of the call ledger, "
            "capped at 200 KB each. On by default because a prompt is editable, so without "
            "the text an analysis stored today cannot be explained after the prompt that "
            "produced it has been changed. Turn it off to keep only the token counts."
        ),
    ),
    SettingDefinition(
        key="modelDefault",
        type="string",
        default="",
        env_key="GAGGICLANKER_MODEL",
        description=(
            "Model id used when a purpose has none of its own. Empty lets the provider "
            "choose — which for claude_code is the CLI's own default."
        ),
    ),
    SettingDefinition(
        key="modelAnalysis",
        type="string",
        default="",
        env_key="GAGGICLANKER_MODEL_ANALYSIS",
        description=(
            "Model for per-shot analysis, the slow careful one. Empty falls back to modelDefault."
        ),
    ),
    SettingDefinition(
        key="modelDraft",
        type="string",
        default="",
        env_key="GAGGICLANKER_MODEL_DRAFT",
        description=(
            "Model for drafts and summaries, where speed beats depth. Empty falls back to "
            "modelDefault."
        ),
    ),
    SettingDefinition(
        key="authUser",
        type="string",
        default="",
        env_key="AUTH_USER",
        description=(
            "Sign-in username. Auth is enabled exactly when this and a password hash are both "
            "set, and the switch is re-read on every request - so turning it on from this page "
            "takes effect without a restart. Empty means the whole app is open, which is the "
            "right default for a machine only your LAN can reach."
        ),
    ),
    SettingDefinition(
        key="authPasswordHash",
        type="string",
        default="",
        secret=True,
        env_key="AUTH_PASSWORD_HASH",
        readonly=True,
        validate=_must_be_an_argon2_hash,
        description=(
            "argon2id hash of the sign-in password (a `$argon2id$...` PHC string). Read-only "
            "through the settings API: change the password with POST /api/auth/password, which "
            "hashes it on the server and revokes every open session, or seed the first one with "
            "AUTH_PASSWORD in the environment. A stored value that is not an argon2 hash cannot "
            "authenticate anybody, so auth stays ON and refuses every sign-in rather than "
            "quietly letting the whole API open."
        ),
    ),
    SettingDefinition(
        key="authTokenTtlSeconds",
        type="int",
        default=2592000,
        env_key="AUTH_TOKEN_TTL_S",
        description=(
            "How long a session token stays valid, in seconds. Thirty days by default: this is "
            "a home appliance whose tab stays open for weeks, and signing out is a button that "
            "revokes the session server-side rather than something the expiry has to do."
        ),
    ),
    SettingDefinition(
        key="modelChat",
        type="string",
        default="",
        env_key="GAGGICLANKER_MODEL_CHAT",
        description="Model for conversational turns. Empty falls back to modelDefault.",
    ),
)
