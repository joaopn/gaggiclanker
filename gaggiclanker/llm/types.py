"""The call contract: one request shape, one result union, for every provider.

The whole point of this layer is that a caller writes the same six lines
whether the answer comes from OpenRouter over HTTPS, from the Anthropic SDK, or
from a ``claude -p`` subprocess. Two rules make that true:

**Every call is a structured-JSON call.** There is no free-text `complete()`.
The caller hands over a pydantic model and gets an instance of it back, so the
boundary between "the model said something" and "the app has data" is one
`model_validate` that this layer owns rather than thirteen hand-written
`json.loads` at the call sites.

**A provider failure is a value, not an exception.** ``call_json`` returns
``Ok`` or ``Err`` and never raises for anything the provider did — a 429, a bad
key, a timeout, unparseable output. A caller that forgets to handle the failure
gets a type error from mypy rather than a 500 in production. Cancellation is
the one exception that still propagates, because a cancelled task is not a
provider failure and swallowing it would defeat the caller's own deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

__all__ = [
    "CredentialCheck",
    "Err",
    "ErrorCode",
    "LlmMessage",
    "LlmRequest",
    "LlmResult",
    "ModelPurpose",
    "Ok",
    "ProviderId",
    "ResponseMode",
    "Usage",
]

#: Which provider handles a call. ``openai_compatible`` is the family — the
#: presets (openrouter, openai, ollama, lmstudio) differ only in base URL and
#: whether a key is required, so they share one implementation.
type ProviderId = Literal[
    "openrouter",
    "openai",
    "ollama",
    "lmstudio",
    "openai_compatible",
    "anthropic",
    "claude_code",
]

#: How JSON is asked for, in descending order of strictness. A provider that
#: rejects the strict form falls back to the next one (see
#: :mod:`gaggiclanker.llm.modes`), which is why this is an ordered idea and not
#: a flag.
type ResponseMode = Literal["json_schema", "json_object", "text"]

#: What a call is *for*. Each purpose can name its own model, so the cheap
#: chat turn and the careful shot analysis do not have to share one.
type ModelPurpose = Literal["default", "analysis", "draft", "chat", "starting_point"]

#: The five things that can go wrong, as far as a caller is concerned.
#:
#: ``rate_limited`` and ``auth`` are the two a human has to act on (wait, or fix
#: a key); ``invalid_output`` means the model answered but not with the shape we
#: asked for; ``timeout`` means nobody answered in time; ``unknown`` is
#: everything else.
type ErrorCode = Literal["rate_limited", "auth", "invalid_output", "timeout", "unknown"]


class LlmMessage(BaseModel):
    """One turn of the conversation handed to the provider."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts, as far as the provider disclosed them.

    ``None`` means "not reported", which is different from zero: Ollama and the
    Codex-style CLIs report nothing, and an observer that rendered that as 0
    would make a local model look free *and* make a broken usage parser look
    like a local model.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def __add__(self, other: Usage) -> Usage:
        """Two turns of one call, summed.

        A retry and a corrective turn are both billed, so what the ledger wants
        is the total the call cost rather than what the last reply cost. Adding
        keeps ``None`` where *both* sides reported nothing, so "the provider
        told us nothing" never collapses into a zero somebody would add up.
        """

        def combine(left: int | None, right: int | None) -> int | None:
            if left is None and right is None:
                return None
            return (left or 0) + (right or 0)

        return Usage(
            prompt_tokens=combine(self.prompt_tokens, other.prompt_tokens),
            completion_tokens=combine(self.completion_tokens, other.completion_tokens),
        )


@dataclass(slots=True)
class LlmRequest[T: BaseModel]:
    """One structured call.

    ``output_model`` is both the JSON schema sent to the provider and the
    validator applied to what comes back, which is the reason there is no
    separate schema argument to keep in sync with it.
    """

    messages: list[LlmMessage]
    output_model: type[T]
    #: Empty means "whatever :func:`resolve_model` decides for ``purpose``".
    model: str = ""
    purpose: ModelPurpose = "default"
    #: Retries *inside* one call for a transient failure (a 5xx, a stall, an
    #: unparseable body). Separate from the process-wide rate-limit budget,
    #: which is about the account rather than about this request.
    max_retries: int = 2
    retry_delay_s: float = 0.5
    #: Per-attempt ceiling. ``None`` takes the ``llmTimeoutSeconds`` setting.
    timeout_s: float | None = None
    #: What the live-call list shows: what this call is ("analyse shot") and
    #: what it is about ("#129, Gaggiuino 9 bar"). Never a secret — it is
    #: rendered in a browser and written to the usage table.
    label: str = "llm call"
    subject: str = ""
    #: ``claude_code`` only: ``--effort``. Ignored by the HTTP providers.
    effort: str = ""
    #: Which prompt produced the messages, recorded on the usage row so a
    #: regression can be traced back to the prompt edit that caused it.
    prompt_name: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class Ok[T: BaseModel]:
    """A validated answer, plus everything the observer and the usage row need."""

    data: T
    usage: Usage = field(default_factory=Usage)
    raw: str = ""
    provider: str = ""
    model: str = ""
    mode: str = ""
    #: The observer record's id, which is also the ledger row's `call_id`. It is
    #: on the result rather than only in the observer so a caller that stores an
    #: outcome of its own — a shot analysis, say — can point at the row holding
    #: the rendered prompt and the raw reply without guessing which one it was.
    call_id: str = ""
    ok: Literal[True] = True


@dataclass(frozen=True, slots=True)
class Err:
    """A failure the caller is expected to branch on rather than catch.

    ``usage`` is not always empty. A reply that arrived and then failed
    validation was paid for exactly like one that passed, and so was the first
    turn of a corrective retry; dropping those tokens would make the usage
    ledger under-report precisely the calls that went wrong, which are the ones
    worth costing.
    """

    code: ErrorCode
    message: str
    status: int | None = None
    retryable: bool = False
    provider: str = ""
    model: str = ""
    mode: str = ""
    usage: Usage = field(default_factory=Usage)
    raw: str = ""
    #: As on :class:`Ok` — a failed call has a ledger row too, and it is the one
    #: carrying what was actually sent.
    call_id: str = ""
    ok: Literal[False] = False


#: The union ``call_json`` returns. ``result.ok`` narrows it for mypy, so a
#: caller writes ``if result.ok: use(result.data)`` and cannot reach ``.data``
#: on the failure branch.
type LlmResult[T: BaseModel] = Ok[T] | Err


@dataclass(frozen=True, slots=True)
class CredentialCheck:
    """What ``POST /api/llm/validate`` answers: does this provider work at all.

    ``detail`` carries the provider's own words (a CLI's auth status line, an
    SDK's error message) so a wrong key and an unreachable host read
    differently in the UI. It never carries the key itself.
    """

    provider: str
    ok: bool
    detail: str = ""
    models: list[str] = field(default_factory=list)
