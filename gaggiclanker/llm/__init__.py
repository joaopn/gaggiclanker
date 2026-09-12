"""The provider-agnostic structured-call layer.

One entry point — :meth:`LlmService.call_json` — takes messages and a pydantic
model and returns an instance of that model or a typed failure, whether the
answer comes from an HTTPS endpoint or a subprocess. See
:mod:`gaggiclanker.llm.types` for the contract and
:mod:`gaggiclanker.llm.service` for the pipeline.
"""

from __future__ import annotations

from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import (
    Err,
    LlmMessage,
    LlmRequest,
    LlmResult,
    Ok,
    Usage,
)

__all__ = [
    "Err",
    "LlmMessage",
    "LlmRequest",
    "LlmResult",
    "LlmService",
    "Ok",
    "Usage",
]
