"""Classifying a provider failure, and deciding whether it is worth retrying.

Three judgements live here, and each one has a rule that is easy to get wrong:

**Which error code the caller sees.** An HTTP status is authoritative when
there is one. Text patterns are consulted *only* when there is not — i.e. for
the CLI providers, which have no status line. That asymmetry is deliberate:
this app feeds the model roast notes and shot comments, and a tasting note
containing the words "rate limit" must not be able to latch the whole process
into a rate-limited stop.

**Whether to retry.** Transient means transient: a 429, a 5xx, a stall, a
socket that never opened, a body that did not parse. A 400 is the request
being wrong and retrying it verbatim is just a slower 400.

**Whether the provider is refusing the *mode* rather than the request.** Some
gateways answer a perfectly good request with a 400 because they do not
implement ``response_format: json_schema``. That is not a failure, it is a
capability report, and the answer is to ask again in a simpler mode.
"""

from __future__ import annotations

from gaggiclanker.llm.types import ErrorCode

__all__ = [
    "LlmApiError",
    "classify_llm_error",
    "is_capability_error",
    "should_retry",
]

# Phrases that mean "the account is out of budget or being throttled", used
# only when no HTTP status is available. Kept narrow and lower-cased.
_RATE_LIMIT_PATTERNS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "too many requests",
    "quota exceeded",
    "usage limit",
    "overloaded",
    "capacity constraints",
)

# Phrases that mean "there is no usable credential". `claude auth status`, the
# Claude Code CLI's own 401 envelope and the SDKs all phrase it differently.
_AUTH_PATTERNS: tuple[str, ...] = (
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "authentication_error",
    "not logged in",
    "no credentials",
    "please run /login",
    "oauth token",
    "credit balance",
)

# Phrases that mean "we never got an answer", again only meaningful without a
# status.
_TIMEOUT_PATTERNS: tuple[str, ...] = ("timeout", "timed out", "deadline exceeded")

# What a provider says when it does not implement the structured-output shape
# we asked for. Matched against a 4xx body; see `is_capability_error`.
_CAPABILITY_PATTERNS: tuple[str, ...] = (
    "response_format",
    "responseformat",
    "json_schema",
    "jsonschema",
    "responseschema",
    "structured output",
    "structured_output",
    "output_config",
    "does not support",
    "not supported",
    "unsupported parameter",
    "unrecognized request argument",
    "unknown parameter",
    "extra inputs are not permitted",
)

# Why a retry is worth making. Matched against the failure message when the
# status does not already settle it.
_RETRY_PATTERNS: tuple[str, ...] = (
    "parse",
    "timeout",
    "timed out",
    "connection",
    "connect",
    "fetch failed",
    "temporarily unavailable",
    "overloaded",
    "broken pipe",
    "reset by peer",
)


class LlmApiError(Exception):
    """A provider said no. Carries the status and a truncated body for triage.

    Providers raise this; the service turns it into an :class:`Err`. The body
    is truncated because it lands in a log line and an OpenRouter error can
    carry the whole upstream response.
    """

    #: Bodies longer than this are cut. Long enough for a JSON error object,
    #: short enough that a log line stays one line.
    BODY_LIMIT = 600

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body[: self.BODY_LIMIT]

    def __str__(self) -> str:
        if self.status is None:
            return self.message
        return f"{self.message} (HTTP {self.status})"


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def classify_llm_error(*, status: int | None, message: str) -> ErrorCode:
    """Map a failure to the code the caller branches on.

    The status wins where there is one. Text patterns are consulted only for a
    statusless failure — a CLI provider, or a transport error — so content the
    model was *given* cannot decide how the app behaves.
    """
    if status is not None:
        if status == 429:
            return "rate_limited"
        if status in (401, 403):
            return "auth"
        if status == 408 or status == 504:
            return "timeout"
        return "unknown"

    if _matches(message, _RATE_LIMIT_PATTERNS):
        return "rate_limited"
    if _matches(message, _AUTH_PATTERNS):
        return "auth"
    if _matches(message, _TIMEOUT_PATTERNS):
        return "timeout"
    return "unknown"


def should_retry(*, status: int | None, message: str) -> bool:
    """Is this failure worth trying again as-is?

    A 429 is: the budget above this decides whether we are *allowed* to, but the
    failure itself is transient. A 4xx that is not 429 is not — the request is
    wrong and will be wrong again.
    """
    if status is not None:
        if status == 429 or status >= 500:
            return True
        if status == 408:
            return True
        return False
    return _matches(message, _RETRY_PATTERNS)


# A 400 that is about the *model id* rather than the response mode. Without
# these guards "unknown model: gtp-4o" — which contains "not supported" often
# enough — would be read as a capability gap and retried in all three modes
# before the user is told they typed the model name wrong.
_MODEL_ERROR_PATTERNS: tuple[str, ...] = (
    "unknown model",
    "model not found",
    "invalid model",
    "no such model",
    "model_not_found",
)


def is_capability_error(*, status: int | None, body: str, mode: str) -> bool:
    """Is this the provider refusing the *mode* rather than the request?

    Narrow on purpose. 400 is the status every gateway uses for "I have never
    heard of ``response_format``"; 422 is added because a couple of strict
    pydantic-fronted proxies answer that instead. A 500 mentioning
    ``response_format`` is a broken gateway and dropping to a weaker mode would
    paper over it; 401/403/429 are about the account and would otherwise cost
    three attempts in three modes before the user sees "bad key".

    ``text`` is the weakest mode there is, so nothing it returns can be a
    capability gap — there is nowhere left to fall back to.
    """
    if mode == "text":
        return False
    if status not in (400, 422):
        return False
    if _matches(body, _MODEL_ERROR_PATTERNS):
        return False
    return _matches(body, _CAPABILITY_PATTERNS)
