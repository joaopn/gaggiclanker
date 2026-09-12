"""Per-request context carried in a :mod:`contextvars` variable.

The request id has to reach code that never sees the ``Request`` object — the
logger, a repository, a device fetch started by a route — so it travels in a
context variable rather than as an argument. ``contextvars`` is task-local, so
each concurrent request keeps its own value and a spawned task inherits the
value it was spawned under.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("gaggiclanker_request_id", default=None)

__all__ = ["get_request_id", "request_context", "set_request_id"]


def get_request_id() -> str | None:
    """The current request id, or ``None`` outside a request."""
    return _request_id.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Set the request id; returns the token needed to reset it."""
    return _request_id.set(request_id)


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """Bind ``request_id`` for the duration of the block."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)
