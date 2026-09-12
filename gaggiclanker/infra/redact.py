"""A structlog processor that keeps secrets out of the log.

The log is the one place a secret leaks by accident rather than by design: the
code that handles a password knows it is handling a password, but the code that
logs ``body=...`` or ``headers=...`` three layers away does not. So the rule is
applied at the last possible moment, to every event, from every module.

Two rules, in this order:

1. **By key.** Any field whose name contains ``token``, ``password``,
   ``api_key``/``apikey``, ``authorization``, ``secret`` or ``credential`` is
   replaced with ``"[redacted]"``. Applied recursively into dicts and lists,
   because the dangerous case is a nested body rather than a top-level kwarg.
2. **By value.** A string that begins ``Bearer `` or looks like a PHC hash
   (``$argon2...``) is redacted whatever it is called, because those are the two
   shapes that travel under an innocent name (``header``, ``value``, ``stored``).

What it deliberately does **not** do is redact a field whose name merely *lists*
secret-ish names: ``settings_updated keys=["llmApiKey"]`` is the record of which
settings changed, it carries no value, and turning it into ``[redacted]`` would
destroy the audit line while protecting nothing. That is why the key test is on
the field name and the value test is on the value's shape, and why neither looks
at list *elements* as names.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

__all__ = ["REDACTED", "SENSITIVE_KEY_PATTERN", "redact_processor", "redact_value"]

REDACTED = "[redacted]"

#: Substring match, case-insensitive, so ``apiKey``, ``api_key``,
#: ``anthropic_api_key`` and ``X-Api-Key`` are all caught by one rule.
SENSITIVE_KEY_PATTERN = re.compile(
    r"token|password|passwd|api[_-]?key|apikey|authorization|secret|credential",
    re.IGNORECASE,
)

#: Shapes that are a secret regardless of the field they arrived in.
_SENSITIVE_VALUE_PREFIXES: tuple[str, ...] = ("bearer ", "$argon2")

#: How deep to walk. A log event nested more deeply than this is not a log
#: event; the cap is what stops a self-referential structure from hanging the
#: logger.
_MAX_DEPTH = 6


def _looks_sensitive(value: str) -> bool:
    head = value[:16].lower()
    return head.startswith(_SENSITIVE_VALUE_PREFIXES)


def redact_value(value: Any, *, sensitive_key: bool = False, depth: int = 0) -> Any:
    """Return ``value`` with anything secret replaced by :data:`REDACTED`."""
    if sensitive_key:
        # The whole subtree, not just a scalar: `headers={"authorization": ...}`
        # under a key called `credentials` is still a credential.
        return REDACTED
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, str):
        return REDACTED if _looks_sensitive(value) else value
    if isinstance(value, dict):
        return {
            key: redact_value(
                item,
                sensitive_key=isinstance(key, str) and bool(SENSITIVE_KEY_PATTERN.search(key)),
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        rendered = [redact_value(item, depth=depth + 1) for item in value]
        return type(value)(rendered) if isinstance(value, tuple | set) else rendered
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor form of :func:`redact_value`."""
    return {
        key: redact_value(
            value,
            sensitive_key=isinstance(key, str) and bool(SENSITIVE_KEY_PATTERN.search(key)),
        )
        for key, value in event_dict.items()
    }
