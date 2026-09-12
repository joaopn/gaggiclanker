"""A small in-memory rate limiter for the routes that spend money.

Only two routes need one, and they are the two that queue LLM work:
``POST /api/shots/{id}/analyses`` and ``POST /api/sets/{id}/analyse``. Everything
else here reads SQLite. The failure this guards against is not an attacker — it
is a browser tab with a retry loop, or a script, turning into a provider bill
while nobody is watching.

In memory, and per process, for the same reason the login throttle is: this is
one uvicorn process on an appliance, and a limiter that writes to the database
on every request is a heavier tax than the thing it is protecting. A restart
clears it, which is fine: a restart also cancels every queued analysis.

Note what already exists and what this adds. The :class:`~gaggiclanker.infra.tasks.TaskRegistry`
makes a *second* request for the same shot idempotent — the name is claimed
synchronously, so two tabs pressing the button get the same running row. That
says nothing about fifty requests for fifty different shots, which is what this
counts.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from fastapi import Request

from gaggiclanker.infra.errors import TooManyRequests

__all__ = [
    "ANALYSIS_RATE_LIMIT",
    "ANALYSIS_WINDOW_SECONDS",
    "SWEEP_EVERY",
    "RateLimiter",
    "rate_limit",
]

log = structlog.get_logger(__name__)

#: Ten analyses a minute. A single analysis takes a provider tens of seconds, so
#: anything faster than this is a loop rather than a person, and the batch route
#: (`POST /api/sets/{id}/analyse`) is the supported way to ask for fifty.
ANALYSIS_RATE_LIMIT = 10
ANALYSIS_WINDOW_SECONDS = 60.0

#: How many checks between sweeps of the counter map. Small enough that an idle
#: key is forgotten quickly, large enough that the sweep is noise next to the
#: work the limited routes actually do.
SWEEP_EVERY = 64


@dataclass
class RateLimiter:
    """Sliding-window counters, keyed by bucket and caller.

    A sliding window rather than a fixed one: a fixed window lets a caller spend
    the whole allowance at 11:59:59 and the whole next allowance at 12:00:00,
    which is twice the limit at the moment it matters most.
    """

    _hits: dict[tuple[str, str], deque[float]] = field(
        default_factory=lambda: defaultdict(deque), repr=False
    )
    _until_sweep: int = SWEEP_EVERY

    def check(self, bucket: str, caller: str, *, limit: int, window: float) -> None:
        """Record a hit, or raise :class:`TooManyRequests` if the window is full."""
        now = time.monotonic()
        self._sweep(now, window)
        hits = self._hits[(bucket, caller)]
        cutoff = now - window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(hits[0] + window - now + 0.999))
            log.warning("rate_limited", bucket=bucket, limit=limit, retry_after=retry_after)
            raise TooManyRequests(
                f"Rate limit reached: at most {limit} of these per "
                f"{int(window)} seconds. Try again in {retry_after} s.",
                retry_after=retry_after,
                details={
                    "limit": limit,
                    "window_seconds": int(window),
                    "retry_after_seconds": retry_after,
                },
            )
        hits.append(now)

    def _sweep(self, now: float, window: float) -> None:
        """Drop callers whose hits have all aged out.

        Pruning happens on access, so a caller that stops calling leaves an
        empty deque behind for ever — a slow leak keyed on client address,
        which for an appliance behind a proxy is one key and for one exposed to
        the internet is as many keys as there are scanners. Every
        :data:`SWEEP_EVERY` checks, walk the map and forget the idle ones. The
        work is proportional to the number of distinct callers, which is why it
        is amortised rather than done on every request.
        """
        self._until_sweep -= 1
        if self._until_sweep > 0:
            return
        self._until_sweep = SWEEP_EVERY
        cutoff = now - window
        for key, hits in list(self._hits.items()):
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    @property
    def tracked_callers(self) -> int:
        """How many callers are being counted. For tests and for a health probe."""
        return len(self._hits)


def caller_key(request: Request) -> str:
    """Who this request counts against.

    The authenticated subject when auth is on (set on the scope by the guard),
    the client address otherwise. Not the other way round: with auth on, one
    user behind one address is still one user, and keying on the address would
    make a reverse proxy collapse every caller into one bucket.
    """
    subject = request.scope.get("auth_subject")
    if isinstance(subject, str) and subject:
        return f"user:{subject}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def rate_limit(
    bucket: str,
    limit: int,
    window: float = ANALYSIS_WINDOW_SECONDS,
) -> Callable[[Request], None]:
    """A FastAPI dependency that applies one bucket's limit to a route.

    Used as ``dependencies=[Depends(rate_limit("analysis", ANALYSIS_RATE_LIMIT))]``
    so the limit is visible in the route declaration rather than buried in the
    handler, and so it runs before the body is validated.
    """

    def dependency(request: Request) -> None:
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limits", None)
        if limiter is None:  # pragma: no cover - the lifespan always builds one
            return
        limiter.check(bucket, caller_key(request), limit=limit, window=window)

    return dependency
