"""Take secrets out of a document the machine sent before anything keeps it.

The machine's `GET /api/settings` carries its credentials next to the settings
people do want to see: the Wi-Fi password (outside access-point mode), the
access-point password and the Home Assistant password (`WebUIPlugin.cpp:486-495`).
The archive has no use for them, and a copy in the database is served by the API
and carried into every backup, so they are removed on the way in and never
stored, logged or shown. Removed rather than masked: a placeholder would read as
a value somebody had set.

The closed list is what firmware v1.9.0 sends; the name test catches what a
later build adds under a similar name. A key that merely *contains* one of the
words goes too (``passwordHint``): a false positive loses a display value, a
false negative keeps a password.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SECRET_KEYS", "SECRET_KEY_WORDS", "is_secret_key", "without_secrets"]

#: The keys firmware v1.9.0 puts in its settings document.
SECRET_KEYS: tuple[str, ...] = ("wifiPassword", "apPassword", "haPassword")

#: Any key whose name contains one of these (case-insensitive) is a secret.
SECRET_KEY_WORDS: tuple[str, ...] = ("password", "token", "secret")


def is_secret_key(name: str) -> bool:
    lowered = name.lower()
    return name in SECRET_KEYS or any(word in lowered for word in SECRET_KEY_WORDS)


def without_secrets(value: Any) -> Any:
    """A copy of a JSON value with every secret key removed at any depth.

    Objects inside lists are walked too. Scalars and non-secret keys come back
    untouched; the argument is never modified.
    """
    if isinstance(value, dict):
        return {
            key: without_secrets(item)
            for key, item in value.items()
            if not (isinstance(key, str) and is_secret_key(key))
        }
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value
