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
    SETTINGS_REGISTRY,
    ResolvedSetting,
    SettingDefinition,
)

__all__ = ["SettingsService"]

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
        )

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
        if not updates:
            raise BadRequest("No settings to update")

        validated: dict[str, str | None] = {}
        failures: list[dict[str, str]] = []

        for key, value in updates.items():
            definition = self.registry.get(key)
            if definition is None:
                failures.append({"field": key, "message": "unknown setting"})
                continue
            if value is None:
                validated[key] = None
                continue
            try:
                validated[key] = definition.serialize(definition.coerce(value))
            except ValueError:
                # definition.expected, not str(exc): the message goes to the
                # client and the rejected value may be the secret being set.
                failures.append({"field": key, "message": definition.expected})

        if failures:
            raise BadRequest("Invalid settings update", details=failures)

        for key, serialized in validated.items():
            if serialized is None:
                await self.repo.delete(key)
            else:
                await self.repo.set(key, serialized)

        log.info("settings_updated", keys=sorted(validated))
        return await self.resolve_all()
