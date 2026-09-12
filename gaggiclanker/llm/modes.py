"""Remembering which response mode a given endpoint actually accepts.

Mode fallback costs a round trip: ask for ``json_schema``, get a 400 saying the
gateway has never heard of it, ask again for ``json_object``. Paying that on
every call against a local Ollama would double the latency of every analysis
for the lifetime of the container.

So the first success is remembered, keyed by ``provider:base_url`` rather than
by provider alone — "openai_compatible" is a family, and the same code path
serves OpenRouter (full json_schema), a local LM Studio build (json_object
only) and an in-house gateway (text only). The key is the endpoint, because
that is what the capability belongs to.

The memory is deliberately a hint and not a cache of results: a remembered mode
is only *tried first*, and a later failure falls through the remaining modes as
normal. That is what makes it safe to keep for the whole process life — an
upgraded gateway costs one extra round trip, once.
"""

from __future__ import annotations

import threading

from gaggiclanker.llm.types import ResponseMode

__all__ = ["ModeMemory", "get_mode_memory"]


class ModeMemory:
    """The last mode known to have worked, per ``provider:base_url``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._modes: dict[str, ResponseMode] = {}

    @staticmethod
    def key(provider: str, base_url: str) -> str:
        return f"{provider}:{base_url}"

    def remember(self, provider: str, base_url: str, mode: ResponseMode) -> None:
        with self._lock:
            self._modes[self.key(provider, base_url)] = mode

    def recall(self, provider: str, base_url: str) -> ResponseMode | None:
        with self._lock:
            return self._modes.get(self.key(provider, base_url))

    def forget(self, provider: str, base_url: str) -> None:
        """Drop one entry — used when a base URL or key changes under us."""
        with self._lock:
            self._modes.pop(self.key(provider, base_url), None)

    def clear(self) -> None:
        with self._lock:
            self._modes.clear()

    def order(
        self, provider: str, base_url: str, supported: tuple[ResponseMode, ...]
    ) -> list[ResponseMode]:
        """``supported`` with the remembered mode moved to the front."""
        remembered = self.recall(provider, base_url)
        if remembered is None or remembered not in supported:
            return list(supported)
        return [remembered, *(mode for mode in supported if mode != remembered)]


_MEMORY = ModeMemory()


def get_mode_memory() -> ModeMemory:
    """The process-wide mode memory."""
    return _MEMORY
