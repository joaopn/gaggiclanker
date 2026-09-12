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
    "load_dotenv_values",
    "secret_hint",
]

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
        """Validate an inbound JSON value from a PATCH body.

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
                "configured": bool(self.value),
                "hint": secret_hint(self.value),
                "source": self.source,
                "description": self.description,
            }
        return {
            "key": self.key,
            "type": self.type,
            "secret": False,
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
        default="openai_compatible",
        env_key="GAGGICLANKER_LLM_PROVIDER",
        description=(
            "openai_compatible (OpenAI, OpenRouter, Ollama, LM Studio), anthropic, or "
            "claude_code."
        ),
    ),
    SettingDefinition(
        key="llmBaseUrl",
        type="string",
        default="",
        env_key="GAGGICLANKER_LLM_BASE_URL",
        description=(
            "Base URL for the openai_compatible provider, e.g. "
            "https://openrouter.ai/api/v1 or http://localhost:11434/v1. Empty uses the SDK "
            "default."
        ),
    ),
    SettingDefinition(
        key="llmModel",
        type="string",
        default="",
        env_key="GAGGICLANKER_LLM_MODEL",
        description="Model id for shot analysis. Empty lets the provider choose its default.",
    ),
    SettingDefinition(
        key="llmApiKey",
        type="string",
        default="",
        secret=True,
        env_key="GAGGICLANKER_LLM_API_KEY",
        description=(
            "API key for the configured LLM provider. Returned by the API as a four-character "
            "hint only."
        ),
    ),
)
