"""A conversation as a JSON file a person can keep.

Built **only** from what is stored (``chat_messages`` and ``chat_runs``). The
system prompt and everything rendered into it each turn (the Set chat's opening
context with its shot lines, the design context, the base shot-field glossary) is
never stored, so it cannot be in the transcript; nothing here renders a prompt or calls
``opening_context``, and a test builds the real context and looks for it.

Shots do reach the transcript when the agent asks for them, as tool results (the
extended glossary that heads an extended read is inside such a
result and goes with it).
The successful result of a tool that renders shots (``SHOT_RENDERING_TOOLS``),
or of a name that is no registered tool (the ``claude_code`` provider stores an
empty one when it cannot pair a result with its call, so it may be anything), is
therefore replaced by one line naming the tool and the body's size; a test walks
the registry so that a new shot tool cannot be forgotten. Every other registered
tool's result is kept whole (a rule search, SQL rows, a draft's clamp notes), and
so is a failed or refused one: it is a short sentence and the agent's next move
answers it. Tool calls are kept whole, since a proposal's change, prediction
and reason live in its arguments.

Pure functions over rows, so the same rows give the same bytes; the export time
is a parameter for that reason.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.chat import ChatMessageRow, ChatRunRow, ChatThreadRow
from gaggiclanker.tools import registry

__all__ = [
    "SHOT_RENDERING_TOOLS",
    "ChatTranscript",
    "TranscriptCall",
    "TranscriptMessage",
    "TranscriptResult",
    "TranscriptRun",
    "build_transcript",
    "render_transcript",
    "transcript_filename",
]

_MAX_SLUG = 60

#: Tools whose implementation calls ``render_shot``: their successful results
#: are shot renderings, which the transcript leaves out. ``tests/chat/test_transcript.py``
#: finds these in the registry's code and fails when this set is missing one.
SHOT_RENDERING_TOOLS = frozenset(
    {"get_shot", "get_shot_extended", "get_shot_full", "compare_shots", "list_set_shots"}
)


class TranscriptCall(BaseModel):
    """A tool call as the agent made it: name and arguments, whole."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class TranscriptResult(BaseModel):
    """A tool result. ``content`` is None for a shot rendering: only its size is kept."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str
    ok: bool
    characters: int
    content: str | None = None


class TranscriptMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: int | None = None
    role: str
    at: str = ""
    content: str = ""
    tool_calls: list[TranscriptCall] = Field(default_factory=list)
    tool_results: list[TranscriptResult] = Field(default_factory=list)


class TranscriptRun(BaseModel):
    """One press of Send. Every ``*_tokens`` field is a count of tokens, never characters.

    ``tokens_in`` is the input billed over all the run's requests (each tool
    round re-sends the conversation, so it is not the conversation's size);
    ``context_tokens`` is the last request's input, which is. The cache figures
    are the cached parts of ``tokens_in``. Null means the provider did not say.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    status: str
    provider: str = ""
    model: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    context_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    requests: int | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None


class ChatTranscript(BaseModel):
    """The same fields and the same exclusions in both formats."""

    model_config = ConfigDict(extra="forbid")

    thread_id: int
    title: str
    about: str
    created_at: str = ""
    exported_at: str
    messages: list[TranscriptMessage] = Field(default_factory=list)
    runs: list[TranscriptRun] = Field(default_factory=list)


def build_transcript(
    thread: ChatThreadRow,
    messages: Sequence[ChatMessageRow],
    runs: Sequence[ChatRunRow],
    *,
    exported_at: str,
) -> ChatTranscript:
    if thread.set_name:
        about = f"{thread.set_name} {thread.set_version_label or ''}".strip()
    else:
        about = "General"
    return ChatTranscript(
        thread_id=thread.id,
        title=thread.title or "New conversation",
        about=about,
        created_at=thread.created_at,
        exported_at=exported_at,
        messages=[_message(row) for row in messages],
        runs=[_run(row) for row in sorted(runs, key=lambda run: run.id)],
    )


def _message(row: ChatMessageRow) -> TranscriptMessage:
    return TranscriptMessage(
        id=row.id,
        run_id=row.run_id,
        role=row.role,
        at=row.created_at,
        content=row.content,
        tool_calls=[
            TranscriptCall(
                id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments=call["arguments"] if isinstance(call.get("arguments"), dict) else {},
            )
            for call in row.tool_calls
        ],
        tool_results=[_result(result) for result in row.tool_results],
    )


def _hidden(name: str) -> bool:
    """A successful result of this name is left out: a shot tool, or not a tool we know."""
    return name in SHOT_RENDERING_TOOLS or name not in registry


def _result(raw: dict[str, Any]) -> TranscriptResult:
    content = str(raw.get("content", ""))
    name = str(raw.get("name", ""))
    # No `ok` key reads as ok, as the runner does when it replays a stored row.
    ok = bool(raw.get("ok", True))
    return TranscriptResult(
        id=str(raw.get("id", "")),
        name=name,
        ok=ok,
        characters=len(content),
        content=None if ok and _hidden(name) else content,
    )


def _run(row: ChatRunRow) -> TranscriptRun:
    usage = row.usage or {}
    return TranscriptRun(
        id=row.id,
        status=row.status,
        provider=row.provider,
        model=row.model,
        tokens_in=_int(usage.get("input_tokens", usage.get("prompt_tokens"))),
        tokens_out=_int(usage.get("output_tokens", usage.get("completion_tokens"))),
        context_tokens=_int(usage.get("context_tokens")),
        cache_read_tokens=_int(usage.get("cache_read")),
        cache_write_tokens=_int(usage.get("cache_write")),
        requests=_int(usage.get("requests")),
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def render_transcript(transcript: ChatTranscript) -> str:
    return json.dumps(transcript.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def transcript_filename(thread_id: int, title: str) -> str:
    """``chat-<id>-<slug>.json``; the slug is ASCII ``[a-z0-9-]``, at most 60 characters."""
    # NFKD first so "Café" is "cafe" rather than "caf".
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower())
    slug = slug.strip("-")[:_MAX_SLUG].strip("-")
    stem = f"chat-{thread_id}-{slug}" if slug else f"chat-{thread_id}"
    return f"{stem}.json"
