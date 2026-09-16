"""Configuration: bootstrap environment plus a registry of runtime settings.

Two layers, on purpose:

**Bootstrap** (:class:`EnvSettings`) is what the process needs before there is a
database to read: where the data directory is, what to log at, what to bind.
Environment only, read once at startup.

**Runtime settings** (:data:`SETTINGS_REGISTRY`) are the keys the maintainer can
change from the UI without restarting the container. Each is declared once —
key, type, default, secret flag, prose description — and that one declaration
drives the ``settings`` table, ``GET/PATCH /api/settings``, validation, and the
hint shown for a secret. Adding a setting is one entry, the way cvclanker's
``settings-registry.ts`` works.

Precedence for a runtime setting is **database > default**, and that is the
whole rule. A runtime setting reads no environment variable at all: the Settings
page is where it is changed, the database is where it lives, and there is no
second, weaker surface that can disagree with either. The variables that used to
configure one are listed in :data:`FORMER_SETTING_ENV_KEYS` so a boot can say,
once, that they no longer do anything.

Secrets are write-only through the API: ``PATCH`` accepts one, ``GET`` returns
only a four-character hint, which is enough to tell "the right key is loaded"
from "the wrong key is loaded" without putting the key in a browser tab.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog
from dotenv import dotenv_values
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from gaggiclanker.domain.address import host_problem
from gaggiclanker.infra.outbound import PROXY_ENV_KEYS, url_carries_userinfo

__all__ = [
    "CLEANUP_MODES",
    "DEFAULT_ENV_FILE",
    "DEVICE_WRITES_ENV_KEY",
    "FORMER_SETTING_ENV_KEYS",
    "NOTES_WRITEBACK_FIELDS",
    "REMOVED_SETTINGS",
    "RETIRED_AUTH_ENV_KEYS",
    "SETTINGS_REGISTRY",
    "SETTING_PAIRS",
    "EnvSettings",
    "ResolvedSetting",
    "SettingDefinition",
    "SettingPair",
    "SettingType",
    "SettingValueError",
    "device_writes_env_key_set",
    "ignored_setting_env_keys",
    "is_argon2_hash",
    "load_dotenv_values",
    "retired_auth_env_keys",
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

    These seven are the only variables the application reads, because each one
    answers a question that has to be answered before there is a database to ask:
    where the file lives, what to bind, what to log. Everything else is a runtime
    setting in the database.

    Unprefixed names (``DATA_DIR``, ``LOG_LEVEL``, ``PORT``) are accepted because
    they read naturally in a compose file, with the ``GAGGICLANKER_`` prefixed
    form as an alias for boxes that run several services and want a namespace.
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
    #: An extra rule on top of the type, run on every write. Returns an error
    #: message — a fixed string, never quoting the value — or ``None``.
    validate: Callable[[Any], str | None] | None = None
    #: Not settable through ``PATCH /api/settings``. The value still resolves
    #: like any other, and the service can still write it through
    #: :meth:`SettingsService.store`; what this
    #: forbids is a browser form putting an arbitrary string in it. Used where
    #: a dedicated endpoint owns the write (``authPasswordHash``), because a
    #: masked text box invites somebody to type the password itself into it.
    readonly: bool = False

    @property
    def expected(self) -> str:
        """The message a rejected value gets. Never contains the value."""
        return _EXPECTED[self.type]

    def parse(self, raw: str) -> Any:
        """Coerce a stored string into the declared type.

        Raises ``ValueError(self.expected)`` on anything that does not convert,
        which the PATCH route turns into a 400 and a stored row written out of
        band turns into a warning plus the default. The message is fixed so
        neither path can echo the value back.
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
    source: Literal["database", "default"]
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
        "Set the password under Settings → Authentication, which posts it to "
        "POST /api/auth/password."
    )


#: The firmware's own hard limits, per policy key: what `profile.h` and
#: `schema/profile.json` will actually accept. The *policy* is meant to be
#: narrower than these; a bound set outside them is not a tuning choice, it is a
#: safety layer configured to allow everything the layer below it allows, which
#: is a layer that does nothing.
#:
#: Enforced on write rather than only on read, because the failure mode is
#: silent: `bounds_from` falls back per key on an unusable value, so a rejected
#: bound that was still *stored* would read as configured and behave as the
#: default.
_FIRMWARE_LIMITS: dict[str, tuple[float, float, str]] = {
    "profilePolicyTemperatureMinC": (0.0, 150.0, "0-150 °C (the firmware's own range)"),
    "profilePolicyTemperatureMaxC": (0.0, 150.0, "0-150 °C (the firmware's own range)"),
    "profilePolicyPressureMaxBar": (0.0, 12.0, "0-12 bar (the pump's own ceiling)"),
    "profilePolicyFlowMaxMlS": (0.0, 15.0, "0-15 ml/s (the firmware's own range)"),
    "profilePolicyPhaseDurationMinS": (0.5, 300.0, "0.5-300 s (BREW_SAFETY_DURATION_MS)"),
    "profilePolicyPhaseDurationMaxS": (0.5, 300.0, "0.5-300 s (BREW_SAFETY_DURATION_MS)"),
}


def _within_firmware_limits(key: str) -> Callable[[Any], str | None]:
    """A validator refusing anything the firmware itself would not accept."""
    low, high, described = _FIRMWARE_LIMITS[key]

    def validate(value: Any) -> str | None:
        try:
            number = float(value)
        except (TypeError, ValueError):  # pragma: no cover - coerce ran first
            return None
        if low <= number <= high:
            return None
        return (
            f"the safety policy is narrower than the firmware, not wider: this must be "
            f"within {described}"
        )

    return validate


#: The three cleanup policies. `off` is the default and the only one
#: that deletes nothing: `keep_newest` keeps a fixed number of shots on the
#: machine, `free_space` keeps a floor of free flash. Both are ceilings on how
#: much the *firmware's own* retention ever has to do — it deletes the oldest
#: shot when free space drops below 500 KB, whether or not the archive has it.
CLEANUP_MODES: tuple[str, ...] = ("off", "keep_newest", "free_space")

#: The judgement fields that can be mirrored to the machine's notes card,
#: spelled as the firmware's own JSON keys. `notes` is offered but
#: not on by default: the firmware caps it at 200 characters, and silently
#: publishing somebody's tasting note to a screen in the kitchen is a choice
#: they should make rather than inherit.
NOTES_WRITEBACK_FIELDS: tuple[str, ...] = (
    "rating",
    "balance",
    "doseIn",
    "doseOut",
    "grindSetting",
    "notes",
)


def _one_of(allowed: tuple[str, ...], noun: str) -> Callable[[Any], str | None]:
    """A validator for a small closed vocabulary stored as a string."""

    def validate(value: Any) -> str | None:
        if str(value) in allowed:
            return None
        return f"{noun} must be one of: {', '.join(allowed)}"

    return validate


def _at_least(minimum: int, message: str) -> Callable[[Any], str | None]:
    """A validator for an integer floor that exists for a reason worth naming."""

    def validate(value: Any) -> str | None:
        try:
            number = int(value)
        except (TypeError, ValueError):  # pragma: no cover - coerce ran first
            return None
        return None if number >= minimum else message

    return validate


def _known_writeback_fields(value: Any) -> str | None:
    """Refuse a field list with anything in it the firmware has no key for.

    A typo here would silently stop mirroring the field somebody meant, and the
    symptom — "my ratings do not show up on the machine" — points at the write
    path rather than at a comma-separated list in settings.
    """
    names = [part.strip() for part in str(value).split(",") if part.strip()]
    unknown = [name for name in names if name not in NOTES_WRITEBACK_FIELDS]
    if unknown:
        return f"unknown notes fields; allowed: {', '.join(NOTES_WRITEBACK_FIELDS)}"
    return None


def _at_least_one_phase(value: Any) -> str | None:
    """A profile with zero phases crashes brew start; a policy of zero forbids every profile."""
    try:
        count = int(value)
    except (TypeError, ValueError):  # pragma: no cover - coerce ran first
        return None
    if count >= 1:
        return None
    return "a profile has at least one phase, so this must be 1 or more"


@dataclass(frozen=True, slots=True)
class SettingPair:
    """Two keys whose values only make sense together.

    A per-key validator cannot see its sibling, and the sibling is exactly what
    makes 100 a valid minimum or an absurd one. :meth:`SettingsService.apply`
    checks these after every key has passed its own validator, against the
    *effective* value of each — the one being written, or the one already
    resolved when the PATCH only moves one half of the pair.
    """

    lower: str
    upper: str
    message: str


#: Bounds that come in pairs. Inverted ones are refused rather than tolerated:
#: `clamp` cannot clamp into an empty range, so it leaves the value alone, and a
#: policy that silently stops clamping is worse than no policy at all.
SETTING_PAIRS: tuple[SettingPair, ...] = (
    SettingPair(
        lower="profilePolicyTemperatureMinC",
        upper="profilePolicyTemperatureMaxC",
        message="the minimum temperature must not be above the maximum",
    ),
    SettingPair(
        lower="profilePolicyPhaseDurationMinS",
        upper="profilePolicyPhaseDurationMaxS",
        message="the shortest phase must not be longer than the longest",
    ),
)


#: Registry keys that were removed, with the environment variable each one read
#: while both existed. The key half is what a ``PATCH`` naming one is refused
#: with; the variable half joins :data:`FORMER_SETTING_ENV_KEYS` in the boot
#: report. Their stored rows are deleted by a migration; nothing here resolves
#: them.
REMOVED_SETTINGS: dict[str, str] = {
    # MCP device-write tools: MCP and the chat now read and propose, nothing more.
    "mcpDeviceWrites": "GAGGICLANKER_MCP_DEVICE_WRITES",
    # Automatic cleanup: a cleanup runs only from a confirmed plan on the Sync page.
    "deviceCleanupAuto": "GAGGICLANKER_DEVICE_CLEANUP_AUTO",
    # Automatic notes write-back: notes go only when a person sends them.
    "notesWritebackEnabled": "GAGGICLANKER_NOTES_WRITEBACK_ENABLED",
    # The MCP endpoint at /mcp: the chat's MCP server speaks stdio only, to the
    # child process the claude_code provider spawns, and has nothing to switch.
    "mcpEnabled": "GAGGICLANKER_MCP_ENABLED",
}


#: The switch that used to let this box write to the machine from the
#: environment. Set non-empty, it refuses the boot rather than joining the
#: report below: every other former variable was a preference, and ignoring a
#: preference costs somebody a trip to the Settings page. This one allowed
#: writes to an espresso machine, and its owner reading a silent boot log would
#: believe the writes were still gated by a file they control. So it is named
#: and the container stops, the way a credential variable does.
DEVICE_WRITES_ENV_KEY = "GAGGICLANKER_DEVICE_WRITES_ENABLED"


#: Every environment variable that used to configure a runtime setting, and now
#: configures nothing. Runtime settings resolve from the database or their
#: default; an operator baseline in a compose file or a shell would be a second
#: surface that can disagree with the Settings page, which is exactly the
#: confusion this list exists to end.
#:
#: The names live here rather than on the definitions because they are history,
#: not configuration: nothing reads them, and a setting added tomorrow has no
#: business acquiring one. A boot that finds any of them set names them once
#: (never a value) so the person who wrote them into a file hears that the file
#: is inert, and goes to Settings.
#:
#: :data:`DEVICE_WRITES_ENV_KEY` is deliberately absent — it refuses the boot
#: instead — and so are the credential variables of
#: :data:`RETIRED_AUTH_ENV_KEYS`, for the same reason.
FORMER_SETTING_ENV_KEYS: tuple[str, ...] = (
    "GAGGIMATE_HOST",
    "GAGGIMATE_PROTOCOL",
    "GAGGIMATE_TIMEOUT_S",
    "GAGGICLANKER_DEVICE_SYNC_ENABLED",
    "GAGGICLANKER_DEVICE_CLEANUP_MODE",
    "GAGGICLANKER_DEVICE_CLEANUP_KEEP_NEWEST",
    "GAGGICLANKER_DEVICE_CLEANUP_MIN_FREE_KB",
    "GAGGICLANKER_NOTES_WRITEBACK_FIELDS",
    "GAGGICLANKER_PROFILE_POLICY_TEMP_MIN_C",
    "GAGGICLANKER_PROFILE_POLICY_TEMP_MAX_C",
    "GAGGICLANKER_PROFILE_POLICY_PRESSURE_MAX_BAR",
    "GAGGICLANKER_PROFILE_POLICY_FLOW_MAX_ML_S",
    "GAGGICLANKER_PROFILE_POLICY_PHASE_MIN_S",
    "GAGGICLANKER_PROFILE_POLICY_PHASE_MAX_S",
    "GAGGICLANKER_PROFILE_POLICY_MAX_PHASES",
    "GAGGICLANKER_LLM_PROVIDER",
    "GAGGICLANKER_LLM_BASE_URL",
    "CLAUDE_CODE_BIN",
    "CLAUDE_CODE_EFFORT",
    "GAGGICLANKER_LLM_TIMEOUT_S",
    "GAGGICLANKER_LLM_RATE_LIMIT_RETRIES",
    "GAGGICLANKER_LLM_STORE_CALL_TEXT",
    "GAGGICLANKER_ANALYSIS_CHUNK_TOKEN_BUDGET",
    "GAGGICLANKER_MODEL",
    "GAGGICLANKER_MODEL_ANALYSIS",
    "GAGGICLANKER_MODEL_DRAFT",
    "GAGGICLANKER_MODEL_CHAT",
    "GAGGICLANKER_MODEL_STARTING_POINT",
    "GAGGICLANKER_CHAT_MAX_TOOL_ROUNDS",
    "GAGGICLANKER_CHAT_MAX_TOOL_CALLS",
    "GAGGICLANKER_CHAT_HISTORY_TOKEN_BUDGET",
    # The variables of settings that were removed outright, rather than moved
    # into the database: same message, same list, one fewer thing to remember.
    *REMOVED_SETTINGS.values(),
)


#: Environment variables that carry a credential for an external service, or
#: used to, and must not be set: the sign-in settings, the LLM providers' keys and
#: token, and the credential variables the SDKs underneath would otherwise honour
#: on their own (a key, a bearer token, or extra headers that can replace the
#: stored key's). Every credential lives in the database alone (Settings →
#: Authentication, Settings → LLM), and no registry key reads the environment at
#: all. These are not ignored with a warning the way
#: :data:`FORMER_SETTING_ENV_KEYS` are: an install that configured its sign-in here has
#: its user and hash nowhere else, so ignoring the variables would switch
#: authentication off and open the app — and a key left in a compose file is a
#: key still sitting in a file nobody meant to keep it in. Startup refuses
#: instead, naming the variables, until they are removed.
#:
#: An explicit list rather than a ``*_TOKEN`` pattern: containers legitimately
#: carry unrelated tokens. The outbound clients never read any of these anyway
#: (``gaggiclanker/infra/outbound.py``); the refusal is the upgrade guard.
RETIRED_AUTH_ENV_KEYS: tuple[str, ...] = (
    "AUTH_USER",
    "AUTH_PASSWORD",
    "AUTH_PASSWORD_HASH",
    "AUTH_TOKEN_TTL_S",
    "AUTH_JWT_SECRET",
    "GAGGICLANKER_AUTH_PASSWORD",
    "GAGGICLANKER_AUTH_JWT_SECRET",
    "GAGGICLANKER_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OPENAI_API_KEY",
    "OPENAI_ADMIN_KEY",
    "OPENAI_CUSTOM_HEADERS",
    "OPENROUTER_API_KEY",
)


def retired_auth_env_keys(
    environ: Mapping[str, str], dotenv: Mapping[str, str | None]
) -> list[str]:
    """The refused variables set non-empty in the process env or ``.env``, by name.

    Names only, as they are spelled where they were found (the process
    environment first, then ``.env``); a value never leaves this function.
    Compared without regard to case, because the libraries that read them are
    not all case-sensitive and pydantic-settings was not: a lower-case
    ``auth_jwt_secret`` is refused, not silently dropped.

    Two kinds of variable are refused:

    * any name in :data:`RETIRED_AUTH_ENV_KEYS`;
    * a proxy variable (:data:`~gaggiclanker.infra.outbound.PROXY_ENV_KEYS`, any
      case) whose value carries userinfo — a proxy may be named, but a password
      for one is a credential. A proxy without userinfo is allowed.

    Empty counts as unset — the rule every environment read here follows — so an
    old compose file that still passes ``${AUTH_USER:-}`` through starts normally.
    """
    retired = {name.upper() for name in RETIRED_AUTH_ENV_KEYS}
    proxies = {name.upper() for name in PROXY_ENV_KEYS}
    found: list[str] = []
    for source in (environ, dotenv):
        for name, raw in source.items():
            if raw is None or raw.strip() == "" or name in found:
                continue
            upper = name.upper()
            if upper in retired or (upper in proxies and url_carries_userinfo(raw)):
                found.append(name)
    return found


def device_writes_env_key_set(environ: Mapping[str, str]) -> str | None:
    """The name :data:`DEVICE_WRITES_ENV_KEY` is spelled as, if it is set non-empty.

    Any case, for the reason :func:`retired_auth_env_keys` compares that way.
    ``None`` when it is unset or empty — ``GAGGICLANKER_DEVICE_WRITES_ENABLED=``
    left behind by an old compose file means "unset" and starts normally.
    """
    wanted = DEVICE_WRITES_ENV_KEY.upper()
    for name, raw in environ.items():
        if name.upper() == wanted and raw is not None and raw.strip() != "":
            return name
    return None


def ignored_setting_env_keys(environ: Mapping[str, str]) -> list[str]:
    """The former setting variables set non-empty, by name, for the boot report.

    Names only, as they are spelled where they were found; the value is never
    read and never logged — it could be anything, including something its owner
    considers private. Empty counts as unset, the rule every environment read
    here follows.
    """
    former = {name.upper() for name in FORMER_SETTING_ENV_KEYS}
    return [
        name
        for name, raw in environ.items()
        if name.upper() in former and raw is not None and raw.strip() != ""
    ]


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
        description=(
            "Hostname or IP of the GaggiMate display board; an explicit host:port is accepted "
            "for a simulator or the fake device, and an address copied from the browser "
            "(http://192.168.1.50/) is reduced to its host. Empty disables device "
            "sync. mDNS (gaggimate.local) is unreliable from inside a container and is off "
            "entirely when HomeKit is enabled, so prefer a fixed IP or a DHCP reservation. A "
            "change applies immediately: the connection is rebuilt without a restart, and "
            "refused while a push, a cleanup, a notes send or a pull is using the machine."
        ),
        validate=lambda value: host_problem(str(value)),
    ),
    SettingDefinition(
        key="gaggimateProtocol",
        type="string",
        default="ws",
        description=(
            "WebSocket scheme for the device connection: ws or wss. The firmware never "
            "terminates TLS (WebSocketHandler.cpp serves plain HTTP on port 80), so wss is only "
            "useful behind a reverse proxy that adds it. A change applies immediately."
        ),
    ),
    SettingDefinition(
        key="gaggimateTimeoutSeconds",
        type="float",
        default=15.0,
        description=(
            "How long to wait for one device request — a WebSocket res:* frame or an HTTP "
            "body — before giving up. The machine's own web UI uses 30 s; shorter is better "
            "here because a stuck request holds one of only two HTTP slots. A change applies "
            "immediately."
        ),
    ),
    SettingDefinition(
        key="deviceSyncEnabled",
        type="bool",
        default=True,
        description=(
            "Hold the WebSocket, so the header shows whether the machine is online and pulls "
            "and pushes can reach it. Turn off to work on an archive without touching the "
            "machine. Nothing is mirrored on its own either way: shots, profiles and notes "
            "move when a pull is asked for. A change applies immediately: off closes the "
            "connection, on opens it, with no restart."
        ),
    ),
    SettingDefinition(
        key="deviceWritesEnabled",
        type="bool",
        default=False,
        description=(
            "Allow this box to write to the machine at all. Profiles: save a new one, delete "
            "one it created, select it, star it. From the Sync page only, when a person "
            "confirms it: send judgements to shots' notes cards, and delete shots the archive "
            "already holds intact. Off by default, and it is the only thing standing between "
            "a bug and a display that will not brew — a profile with zero phases crashes brew "
            "start, and recovering one means a reflash plus a filesystem erase. Device "
            "settings are never written (POST /api/settings clears every boolean key it omits)."
        ),
    ),
    SettingDefinition(
        key="deviceCleanupMode",
        type="string",
        default="off",
        validate=_one_of(CLEANUP_MODES, "the cleanup mode"),
        description=(
            "The cleanup the Sync page proposes, which runs only when a person confirms it "
            "there: off (propose nothing), "
            "keep_newest (keep deviceCleanupKeepNewest shots on it), or free_space (delete "
            "oldest-first until deviceCleanupMinFreeKb of flash is free). A shot is only ever "
            "deleted when this box already holds its raw bytes intact and unquarantined. The "
            "firmware deletes its own oldest shots below 500 KB free whatever this says; all "
            "this changes is whether they go while the archive still has them."
        ),
    ),
    SettingDefinition(
        key="deviceCleanupKeepNewest",
        type="int",
        default=50,
        validate=_at_least(
            5,
            "keep at least 5 shots on the machine: its own history screen is how most "
            "people look at a shot they have just pulled",
        ),
        description=(
            "With deviceCleanupMode=keep_newest, how many shots to leave on the machine. "
            "The rest are deleted oldest first."
        ),
    ),
    SettingDefinition(
        key="deviceCleanupMinFreeKb",
        type="int",
        default=2048,
        validate=_at_least(
            1024,
            "keep at least 1024 KB free: the firmware starts deleting shots of its own "
            "below 500 KB, and a floor at that level would be a policy that never acts "
            "before the machine does",
        ),
        description=(
            "With deviceCleanupMode=free_space, the free flash to keep available, in KB. "
            "Shots are deleted oldest first until spiffsFree (or sdFree, with a card) is "
            "above this. The firmware's own threshold is 500 KB."
        ),
    ),
    SettingDefinition(
        key="notesWritebackFields",
        type="string",
        default="rating,balance,doseIn,doseOut,grindSetting",
        validate=_known_writeback_fields,
        description=(
            "Which judgement fields a notes send from the Sync page writes to the machine's "
            "notes card, comma-separated. Saving a judgement never sends anything. "
            "Allowed: rating, balance, doseIn, doseOut, grindSetting, notes. Anything left "
            "out keeps whatever the machine already has in that field."
        ),
    ),
    SettingDefinition(
        key="profilePolicyTemperatureMinC",
        type="float",
        validate=_within_firmware_limits("profilePolicyTemperatureMinC"),
        default=60.0,
        description=(
            "Safety policy: the coldest a profile or a phase override may ask for. The "
            "firmware accepts anything up to 150 °C; this is the bound a draft is clamped to "
            "before it is allowed near the machine."
        ),
    ),
    SettingDefinition(
        key="profilePolicyTemperatureMaxC",
        type="float",
        validate=_within_firmware_limits("profilePolicyTemperatureMaxC"),
        default=100.0,
        description="Safety policy: the hottest a profile or a phase override may ask for, in °C.",
    ),
    SettingDefinition(
        key="profilePolicyPressureMaxBar",
        type="float",
        validate=_within_firmware_limits("profilePolicyPressureMaxBar"),
        default=12.0,
        description=(
            "Safety policy: the highest pump pressure or pressure stop condition a profile may "
            "carry, in bar. Twelve is the pump's own ceiling."
        ),
    ),
    SettingDefinition(
        key="profilePolicyFlowMaxMlS",
        type="float",
        validate=_within_firmware_limits("profilePolicyFlowMaxMlS"),
        default=10.0,
        description=(
            "Safety policy: the highest pump flow or flow stop condition a profile may carry, "
            "in ml/s. The firmware takes 15; ten is already more than a 58 mm basket passes "
            "without channelling."
        ),
    ),
    SettingDefinition(
        key="profilePolicyPhaseDurationMinS",
        type="float",
        validate=_within_firmware_limits("profilePolicyPhaseDurationMinS"),
        default=0.5,
        description="Safety policy: the shortest phase a profile may contain, in seconds.",
    ),
    SettingDefinition(
        key="profilePolicyPhaseDurationMaxS",
        type="float",
        validate=_within_firmware_limits("profilePolicyPhaseDurationMaxS"),
        default=120.0,
        description=(
            "Safety policy: the longest phase a profile may contain, in seconds. The firmware's "
            "own cap is 300 s; a phase that long with no stop condition is the failure this "
            "bound exists for."
        ),
    ),
    SettingDefinition(
        key="profilePolicyMaxPhases",
        type="int",
        validate=_at_least_one_phase,
        default=10,
        description=(
            "Safety policy: the most phases a profile may have. Exceeding it is refused rather "
            "than trimmed — truncating a profile would change what it brews while claiming to "
            "have made it safe."
        ),
    ),
    SettingDefinition(
        key="llmProvider",
        type="string",
        default="claude_code",
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
        description=(
            "API key for the configured openai-compatible provider. It belongs to that "
            "provider alone and is never sent to another one. Returned by the API as a "
            "four-character hint only. Stored in the database only; no environment variable "
            "reaches it."
        ),
    ),
    SettingDefinition(
        key="anthropicApiKey",
        type="string",
        default="",
        secret=True,
        description=(
            "API key for the anthropic provider. Deliberately separate from llmApiKey so "
            "switching provider does not send one service's key to another. Never passed to "
            "the claude_code CLI, which uses the subscription token instead. Stored in the "
            "database only; no environment variable reaches it."
        ),
    ),
    SettingDefinition(
        key="claudeCodeOauthToken",
        type="string",
        default="",
        secret=True,
        description=(
            "Subscription token for the claude_code provider. Mint it with "
            "`claude setup-token` and paste it here; it is stored in the database only, and no "
            "environment variable reaches it. An interactive `claude login` is NOT enough: that "
            "writes "
            "~/.claude, and every call runs with a scratch HOME so no ambient CLAUDE.md or "
            "session state reaches the model - which hides those credentials too. A box that "
            "is logged in but has no token here answers `Not logged in - please run /login`."
        ),
    ),
    SettingDefinition(
        key="claudeCodeBin",
        type="string",
        default="claude",
        description=(
            "The Claude Code binary to run. A bare name is looked up on PATH; give an "
            "absolute path when the CLI is installed somewhere uvicorn's PATH does not reach."
        ),
    ),
    SettingDefinition(
        key="claudeCodeEffort",
        type="string",
        default="",
        description=(
            "How hard claude_code thinks: low, medium, high, xhigh or max. Empty lets the "
            "CLI decide. An unrecognised value is dropped rather than passed through."
        ),
    ),
    SettingDefinition(
        key="llmTimeoutSeconds",
        type="float",
        default=300.0,
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
        description=(
            "Keep the rendered prompt and the raw reply on each row of the call ledger, "
            "capped at 200 KB each. On by default because a prompt is editable, so without "
            "the text an analysis stored today cannot be explained after the prompt that "
            "produced it has been changed. Turn it off to keep only the token counts."
        ),
    ),
    SettingDefinition(
        key="analysisChunkTokenBudget",
        type="int",
        default=1500,
        description=(
            "How many estimated tokens of knowledge-base prose one analysis may be given. "
            "The retrieved excerpts are supporting context — the rule tier is what is "
            "authoritative — so the default of 1500 buys two or three passages and leaves "
            "the shot, its trajectory and the rules dominating the prompt. 0 turns "
            "retrieval off entirely."
        ),
    ),
    SettingDefinition(
        key="modelDefault",
        type="string",
        default="",
        description=(
            "Model id used when a purpose has none of its own. Empty lets the provider "
            "choose — which for claude_code is the CLI's own default."
        ),
    ),
    SettingDefinition(
        key="modelAnalysis",
        type="string",
        default="",
        description=(
            "Model for per-shot analysis, the slow careful one. Empty falls back to modelDefault."
        ),
    ),
    SettingDefinition(
        key="modelDraft",
        type="string",
        default="",
        description=(
            "Model for drafts and summaries, where speed beats depth. Empty falls back to "
            "modelDefault."
        ),
    ),
    SettingDefinition(
        key="authUser",
        type="string",
        default="",
        description=(
            "Sign-in username. Auth is enabled exactly when this and a password hash are both "
            "set, and the switch is re-read on every request - so turning it on from this page "
            "takes effect without a restart. Empty means the whole app is open, which is the "
            "right default for a machine only your LAN can reach. Stored in the database only; "
            "no environment variable reaches it."
        ),
    ),
    SettingDefinition(
        key="authPasswordHash",
        type="string",
        default="",
        secret=True,
        readonly=True,
        validate=_must_be_an_argon2_hash,
        description=(
            "argon2id hash of the sign-in password (a `$argon2id$...` PHC string). Read-only "
            "through the settings API: change the password with POST /api/auth/password, which "
            "hashes it on the server and revokes every open session (Settings → Authentication "
            "does exactly that). Stored in the database only; no environment variable reaches "
            "it. A stored value that is not an argon2 hash cannot authenticate anybody, so auth "
            "stays ON and refuses every sign-in rather than quietly letting the whole API open."
        ),
    ),
    SettingDefinition(
        key="authTokenTtlSeconds",
        type="int",
        default=2592000,
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
        description="Model for conversational turns. Empty falls back to modelDefault.",
    ),
    SettingDefinition(
        key="modelStartingPoint",
        type="string",
        default="",
        description=(
            "Model for the starting-point wizard, which authors a whole profile in one "
            "call and wants the careful one. Empty falls back to modelDefault."
        ),
    ),
    SettingDefinition(
        key="chatMaxToolRounds",
        type="int",
        default=8,
        validate=_at_least(1, "a chat turn needs at least one round to answer in"),
        description=(
            "How many provider round-trips one chat answer may take. Each round is one "
            "model call plus whatever tools it asked for; the budget is what stops a "
            "model that keeps re-querying from spending an afternoon's tokens on one "
            "question."
        ),
    ),
    SettingDefinition(
        key="chatMaxToolCalls",
        type="int",
        default=20,
        validate=_at_least(1, "a chat turn needs at least one tool call to be useful"),
        description=(
            "How many tool calls one chat answer may make in total, across all rounds. "
            "Hit either this or chatMaxToolRounds and the model is told to answer with "
            "what it already has."
        ),
    ),
    SettingDefinition(
        key="chatHistoryTokenBudget",
        type="int",
        default=12000,
        validate=_at_least(1000, "a history budget below 1000 tokens drops the question itself"),
        description=(
            "Roughly how many tokens of conversation history are sent with each turn. "
            "Oldest messages are dropped first; the newest user message is always kept, "
            "because a turn without the question is not a turn."
        ),
    ),
)
