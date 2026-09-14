"""Resolving runtime settings: database over environment over default.

The service is the only place that knows the precedence rule. Everything else —
routes, the device client, the LLM layer — asks for a key and gets the
effective value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import structlog

from gaggiclanker.db.settings_repo import SettingsRepository
from gaggiclanker.infra.errors import BadRequest
from gaggiclanker.settings import (
    REMOVED_SETTINGS,
    SETTING_PAIRS,
    SETTINGS_REGISTRY,
    ResolvedSetting,
    SettingDefinition,
    SettingValueError,
)

__all__ = ["READ_ONLY_MESSAGE", "SettingsService"]

#: What a PATCH gets for a key a dedicated endpoint owns. Only one key is
#: read-only today, so the message can name its endpoint; if a second ever
#: appears, this becomes a per-definition string.
READ_ONLY_MESSAGE = (
    "read-only through this endpoint. Set the sign-in password under "
    "Settings → Authentication, which posts it to POST /api/auth/password."
)

log = structlog.get_logger(__name__)


class SettingsService:
    """Reads and updates runtime settings against the registry."""

    def __init__(
        self,
        repo: SettingsRepository,
        registry: dict[str, SettingDefinition] | None = None,
        dotenv: Mapping[str, str | None] | None = None,
    ) -> None:
        self.repo = repo
        self.registry = registry if registry is not None else SETTINGS_REGISTRY
        # The ``.env`` file, already parsed. EnvSettings reads it for the
        # bootstrap keys through pydantic-settings; without passing the same
        # values here, a registry key set in ``.env`` would be invisible while
        # DATA_DIR from the same file worked — one file, two behaviours, and no
        # way for the user to tell which keys are which.
        self.dotenv: Mapping[str, str | None] = dotenv or {}

    def definition(self, key: str) -> SettingDefinition:
        """The definition for ``key``, or a 400 naming the valid keys."""
        definition = self.registry.get(key)
        if definition is None:
            raise BadRequest(
                f"Unknown setting {key!r}",
                details={"key": key, "known_keys": sorted(self.registry)},
            )
        return definition

    def _raw_env(self, env_key: str) -> str | None:
        """The environment value for ``env_key``: the process env over ``.env``.

        The same precedence pydantic-settings applies to the bootstrap keys, so
        the two layers cannot disagree about what "the environment" means.
        """
        raw = os.environ.get(env_key)
        if raw is None:
            raw = self.dotenv.get(env_key)
        return raw

    def _from_env(self, definition: SettingDefinition) -> Any | None:
        """The environment value for a definition, or ``None``.

        An unparseable environment value is a misconfiguration the operator
        cannot see in the UI, so it is logged loudly and then ignored rather
        than crashing the container on boot.
        """
        if definition.env_key is None:
            return None
        raw = self._raw_env(definition.env_key)
        if raw is None or raw.strip() == "":
            return None
        try:
            return definition.parse(raw)
        except ValueError:
            log.warning(
                "setting_env_value_invalid",
                setting=definition.key,
                env_key=definition.env_key,
                expected=definition.type,
            )
            return None

    def _resolve(self, definition: SettingDefinition, stored: str | None) -> ResolvedSetting:
        override: Any = None
        if stored is not None:
            try:
                override = definition.parse(stored)
            except ValueError:
                # A row written out of band (hand-edited file, restored backup
                # from an older schema). Ignore it rather than fail the whole
                # settings read.
                log.warning("setting_stored_value_invalid", setting=definition.key)
                override = None

        env_value = self._from_env(definition)

        if override is not None:
            value, source = override, "database"
        elif env_value is not None:
            value, source = env_value, "environment"
        else:
            value, source = definition.default, "default"

        return ResolvedSetting(
            key=definition.key,
            type=definition.type,
            value=value,
            default=definition.default,
            override=override,
            source=source,  # type: ignore[arg-type]
            secret=definition.secret,
            description=definition.description,
            readonly=definition.readonly,
        )

    def removed_env_keys(self) -> list[str]:
        """Environment variables still set for settings that no longer exist.

        Only the names: a value is never logged, and for these the name is the
        whole message — "this variable does nothing now". Empty values count as
        unset, the same rule `_from_env` applies.
        """
        return [
            env_key
            for env_key in REMOVED_SETTINGS.values()
            if (raw := self._raw_env(env_key)) is not None and raw.strip() != ""
        ]

    async def resolve_all(self) -> dict[str, ResolvedSetting]:
        """Every registry key, resolved. Registry order is preserved."""
        stored = await self.repo.get_all()
        return {
            key: self._resolve(definition, stored.get(key))
            for key, definition in self.registry.items()
        }

    async def resolve(self, key: str) -> ResolvedSetting:
        """One key, resolved."""
        definition = self.definition(key)
        return self._resolve(definition, await self.repo.get(key))

    async def get(self, key: str) -> Any:
        """The effective value of ``key``."""
        return (await self.resolve(key)).value

    async def apply(self, updates: dict[str, Any]) -> dict[str, ResolvedSetting]:
        """Validate and store a PATCH body, then return the new state.

        Every key is validated before anything is written, so a body with one
        bad value changes nothing. ``None`` clears the override, which is how
        the UI's "reset to default" works.
        """
        return await self.write(await self.validate(updates))

    async def validate(self, updates: dict[str, Any]) -> dict[str, str | None]:
        """Check a PATCH body without writing it: key -> serialized value, or ``None`` to clear.

        Raises the same 400 :meth:`apply` does. Separate so a caller can decide
        whether a change may be made at all — the machine connection refuses
        one while something is using the machine — before anything is stored.
        """
        if not updates:
            raise BadRequest("No settings to update")

        validated: dict[str, str | None] = {}
        failures: list[dict[str, str]] = []

        for key, value in updates.items():
            definition = self.registry.get(key)
            if definition is None:
                failures.append({"field": key, "message": "unknown setting"})
                continue
            if definition.readonly:
                # A dedicated endpoint owns this key. Refused here rather than
                # validated, because the message that helps is "use that one",
                # not "that is the wrong shape".
                failures.append({"field": key, "message": READ_ONLY_MESSAGE})
                continue
            if value is None:
                validated[key] = None
                continue
            try:
                validated[key] = definition.serialize(definition.coerce(value))
            except SettingValueError as exc:
                # A validator's message is a fixed string by contract, so it is
                # safe to send on; it is also the only thing that says what the
                # field actually wants.
                failures.append({"field": key, "message": str(exc)})
            except ValueError:
                # definition.expected, not str(exc): the message goes to the
                # client and the rejected value may be the secret being set.
                failures.append({"field": key, "message": definition.expected})

        failures.extend(await self._pair_failures(validated))

        if failures:
            raise BadRequest("Invalid settings update", details=failures)
        return validated

    async def write(self, validated: dict[str, str | None]) -> dict[str, ResolvedSetting]:
        """Store what :meth:`validate` returned, then return the new state."""
        for key, serialized in validated.items():
            if serialized is None:
                await self.repo.delete(key)
            else:
                await self.repo.set(key, serialized)

        log.info("settings_updated", keys=sorted(validated))
        return await self.resolve_all()

    async def effective_after(
        self, validated: dict[str, str | None], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """What ``keys`` will resolve to once ``validated`` is stored. Writes nothing.

        A key set in the body is its parsed value; a key cleared with ``None``
        is what resolves with no stored row — the environment's value or the
        default; a key the body leaves alone is what it resolves to now.
        """
        values: dict[str, Any] = {}
        for key in keys:
            definition = self.definition(key)
            if key not in validated:
                values[key] = await self.get(key)
                continue
            serialized = validated[key]
            values[key] = self._resolve(definition, serialized).value
        return values

    async def _pair_failures(self, validated: dict[str, str | None]) -> list[dict[str, str]]:
        """Check the bounds that only make sense in pairs.

        Run after every key has passed its own validator, because a pair rule
        compares two numbers and both have to *be* numbers first. The comparison
        is against the **effective** value of each half — the one in this PATCH,
        or the one already resolved when the body moves only one of them — so
        raising the minimum above a maximum nobody touched is refused too.

        A key sent as ``None`` clears its override, and what it reverts to is
        not known until the row is gone. It is compared against its current
        value instead, which errs towards refusing: a combination that would
        have become valid is rejected and the person sends both halves. The
        alternative errs towards storing an inverted pair, and an inverted pair
        makes `clamp` stop clamping.
        """
        touched = [
            pair for pair in SETTING_PAIRS if pair.lower in validated or pair.upper in validated
        ]
        if not touched:
            return []
        resolved = await self.resolve_all()
        failures: list[dict[str, str]] = []
        for pair in touched:
            low = self._effective(pair.lower, validated, resolved)
            high = self._effective(pair.upper, validated, resolved)
            if low is not None and high is not None and low > high:
                failures.append({"field": pair.lower, "message": pair.message})
        return failures

    def _effective(
        self,
        key: str,
        validated: dict[str, str | None],
        resolved: dict[str, ResolvedSetting],
    ) -> float | None:
        """What ``key`` will be worth once this PATCH lands, as a number."""
        raw = validated.get(key) if validated.get(key) is not None else None
        value = self.definition(key).parse(raw) if raw is not None else resolved[key].value
        try:
            return float(value)
        except (TypeError, ValueError):  # pragma: no cover - both sides validated above
            return None

    async def store(self, key: str, value: Any) -> None:
        """Write one setting, read-only flag and all.

        The service's own way in, for the code that legitimately owns a key a
        browser form must not touch: ``POST /api/auth/password`` writes
        ``authPasswordHash`` through here. The value still goes through the
        definition's validation — being allowed to write a key is not the same
        as being allowed to write rubbish into it — but the read-only rule,
        which is about *where* a write may come from, does not apply.
        """
        definition = self.definition(key)
        await self.repo.set(key, definition.serialize(definition.coerce(value)))
        log.info("setting_stored", setting=key)
