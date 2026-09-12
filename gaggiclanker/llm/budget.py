"""The process-wide rate-limit budget and its STOP latch.

A rate limit is an account fact, not a request fact. When Anthropic starts
answering 429, the eleventh shot in a backfill will be refused exactly like the
first, so retrying each one individually burns the retry budget of every call in
the queue and delays the moment anyone notices. The budget is therefore
**shared by the whole process**: a fixed number of global retries, and then a
latch that makes every subsequent call fail immediately with ``rate_limited``
until a human clears it.

The latch is the feature, not a side effect. It turns "sixty shots each failed
after three retries over eleven minutes" into "one shot failed, and the LLM is
stopped until you look at it" — which is what the maintainer actually wants to
see in the UI.

Clearing it is explicit (``POST /api/llm/rate-limit/reset``, or the settings
page button) because an automatic timer would just re-enter the same wall.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import structlog

__all__ = ["RateLimitBudget", "get_rate_limit_budget"]

log = structlog.get_logger(__name__)


@dataclass
class RateLimitBudget:
    """A retry allowance shared by every call, plus the latch it ends in.

    ``seed`` is idempotent per configured value: the first call that finds an
    unseeded budget sets it, and later calls leave it alone so a running
    backfill keeps one allowance rather than resetting it per shot.
    """

    retries: int = 2
    remaining: int | None = None
    stopped: bool = False
    #: A plain lock, not asyncio: the budget is touched from request handlers
    #: on one loop today, but a background worker on another thread would
    #: otherwise interleave a read and a decrement.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def seed(self, retries: int) -> None:
        """Set the allowance if nothing has claimed one yet."""
        with self._lock:
            if self.remaining is None:
                self.retries = max(0, retries)
                self.remaining = self.retries

    def consume(self) -> bool:
        """Spend one global retry, or latch STOPPED and refuse.

        Returns ``True`` when the caller may try again. ``False`` means the
        budget is spent *and* the latch is now set, so the next call does not
        even reach the provider.
        """
        with self._lock:
            if self.stopped:
                return False
            if self.remaining is None:
                self.remaining = self.retries
            if self.remaining > 0:
                self.remaining -= 1
                return True
            self.stopped = True
            log.warning("llm_rate_limit_stopped", retries=self.retries)
            return False

    def latch(self) -> None:
        """Stop the process without spending a retry (an explicit STOP)."""
        with self._lock:
            self.stopped = True

    def reset(self, retries: int | None = None) -> None:
        """Clear the latch and restore the allowance. The UI's "resume" button."""
        with self._lock:
            if retries is not None:
                self.retries = max(0, retries)
            self.remaining = self.retries
            self.stopped = False
        log.info("llm_rate_limit_reset", retries=self.retries)

    def snapshot(self) -> dict[str, object]:
        """What ``GET /api/llm/rate-limit`` and the settings page render."""
        with self._lock:
            return {
                "stopped": self.stopped,
                "retries": self.retries,
                "remaining": self.retries if self.remaining is None else self.remaining,
            }


# One per process, by design — see the module docstring. Tests and the app both
# reach it through this accessor rather than importing the object, so a test can
# reset it without reaching into module state it does not own.
_BUDGET = RateLimitBudget()


def get_rate_limit_budget() -> RateLimitBudget:
    """The process-wide budget."""
    return _BUDGET
