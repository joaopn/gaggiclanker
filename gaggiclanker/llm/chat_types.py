"""The conversation contract: one message shape for three very different APIs.

``call_json`` (see :mod:`gaggiclanker.llm.types`) is a single structured
question. A chat is the other thing: many turns, native tool use, and tokens
arriving one at a time. The three providers express that in three shapes —
chat-completions ``tool_calls`` with a ``role: "tool"`` reply per call,
Anthropic ``tool_use``/``tool_result`` content blocks, and Claude Code's
``stream-json``, where the tool loop happens inside the CLI and we only watch —
so this module defines the shape the rest of the app uses and each provider
translates at its own edge.

Two decisions worth stating.

**A tool result is a message, not an attachment.** Folding results into the
assistant turn would make the transcript unreplayable: the next turn has to be
shown exactly what the model was shown, and that includes which call produced
which JSON.

**Events are pushed, the turn is returned.** A provider streams
:class:`ChatEvent` values through a callback as they arrive and returns the
completed :class:`ChatTurn` at the end. The runner needs both: the events are
what the browser sees within a frame, the turn is what the loop branches on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from gaggiclanker.llm.types import Usage

__all__ = [
    "CHAT_EVENT_KINDS",
    "ChatEvent",
    "ChatEventKind",
    "ChatMessage",
    "ChatRequest",
    "ChatToolCall",
    "ChatToolResult",
    "ChatTurn",
    "OnChatEvent",
]

#: What the SSE stream carries, and what a provider may emit. ``completed``,
#: ``error`` and ``cancelled`` are terminal and the runner emits them, not the
#: provider — a provider that finished one turn has not finished the run.
type ChatEventKind = Literal[
    "delta", "tool_call", "tool_result", "message", "completed", "error", "cancelled"
]

CHAT_EVENT_KINDS: tuple[str, ...] = (
    "delta",
    "tool_call",
    "tool_result",
    "message",
    "completed",
    "error",
    "cancelled",
)


@dataclass(frozen=True, slots=True)
class ChatToolCall:
    """One tool the model asked for.

    ``id`` is the provider's own correlation id where there is one and a
    generated one where there is not; the result has to carry it back, and
    inventing one on our side for a provider that supplies one would break the
    pairing on the wire.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatToolResult:
    """What one tool call produced, as the model will be shown it."""

    id: str
    name: str
    content: str
    ok: bool = True


@dataclass(slots=True)
class ChatMessage:
    """One turn of the conversation, in the shape every provider can render.

    ``role="tool"`` carries results and no text; ``role="assistant"`` may carry
    text, tool calls, or both.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ChatToolCall] = field(default_factory=list)
    tool_results: list[ChatToolResult] = field(default_factory=list)


@dataclass(slots=True)
class ChatEvent:
    """One thing that happened, on its way to the browser."""

    kind: ChatEventKind
    data: dict[str, Any] = field(default_factory=dict)


#: Pushed as events arrive. Synchronous on purpose: a provider that had to
#: ``await`` the consumer would let a slow browser back-pressure the model.
type OnChatEvent = Callable[[ChatEvent], None]


@dataclass(slots=True)
class ChatRequest:
    """One provider turn: the transcript, the tools, and the deadline."""

    messages: list[ChatMessage]
    #: Function-calling schemas in the provider's own shape, produced by
    #: :meth:`~gaggiclanker.tools.registry.ToolRegistry.openai_schemas` and its
    #: Anthropic twin. Empty means "answer without tools".
    tools: list[dict[str, Any]] = field(default_factory=list)
    system: str = ""
    model: str = ""
    timeout_s: float = 300.0
    max_tokens: int = 4096
    #: The Set this conversation is scoped to, when it is scoped to one. Only
    #: ``claude_code`` reads it, and it has to: the CLI runs the tool loop
    #: itself against our MCP server, so a scope the runner keeps to itself
    #: would make `get_set` with no argument answer "not scoped" on the default
    #: provider and succeed on every other one — and, worse, would leave the
    #: CLI's child offering the whole archive's tools inside a conversation
    #: about one Set.
    set_id: int | None = None
    #: The version being argued, beside it. Carried for the same reason and
    #: forwarded to the same place; the two travel together everywhere.
    set_version_id: int | None = None
    #: Set by the caller to stop the turn. Checked between streamed chunks and
    #: passed to the subprocess providers as the signal to kill the child; it is
    #: an ``Event`` rather than task cancellation so a cancelled run can still
    #: write its own row on the way out.
    cancel: asyncio.Event | None = None


@dataclass(slots=True)
class ChatTurn:
    """What one provider turn produced."""

    text: str = ""
    tool_calls: list[ChatToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    #: ``end_turn`` | ``tool_use`` | ``max_tokens`` | ``cancelled``, as the
    #: provider reported it. The runner only branches on whether there are tool
    #: calls, but the reason is worth recording: a turn truncated at
    #: ``max_tokens`` looks like a model that stopped mid-sentence.
    stop_reason: str = ""
    model: str = ""
    #: Set by ``claude_code``, where the tool loop ran inside the CLI: these
    #: calls and results already happened, and the runner records them for the
    #: transcript rather than executing them.
    executed_tool_calls: list[ChatToolCall] = field(default_factory=list)
    executed_tool_results: list[ChatToolResult] = field(default_factory=list)
