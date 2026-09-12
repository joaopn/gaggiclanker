"""Data access for the chat: threads, messages, runs, the stream, the audit.

The one non-obvious table is ``chat_events``. Everything the SSE stream emits is
written here before it is published, and the stream route replays from it on
connect. That is what makes closing a laptop mid-answer harmless: the browser
comes back with the last ``seq`` it saw and gets the rest, rather than a blank
bubble it can never fill in because the in-memory bus is lossy by design.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from gaggiclanker.db.repository import Repository

__all__ = [
    "ChatEventRow",
    "ChatEventsRepository",
    "ChatMessageRow",
    "ChatMessageWrite",
    "ChatRepository",
    "ChatRunRow",
    "ChatThreadRow",
    "ChatThreadWrite",
    "ToolCallRow",
    "ToolCallsRepository",
]

type RunStatus = Literal["running", "ok", "failed", "cancelled", "interrupted"]


class ChatThreadWrite(BaseModel):
    """A new thread. Both fields optional: "just ask something" is a thread."""

    title: str = Field(default="", max_length=200)
    set_id: int | None = None


class ChatThreadRow(BaseModel):
    """One thread as listed."""

    id: int
    title: str = ""
    set_id: int | None = None
    set_name: str | None = None
    message_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class ChatMessageWrite(BaseModel):
    """One turn on its way in."""

    thread_id: int
    role: Literal["user", "assistant", "tool", "system"]
    content: str = ""
    run_id: int | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_results: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None


class ChatMessageRow(BaseModel):
    """One turn as stored."""

    id: int
    thread_id: int
    run_id: int | None = None
    role: str
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    created_at: str = ""


class ChatRunRow(BaseModel):
    """One press of Send."""

    id: int
    thread_id: int
    status: RunStatus = "running"
    provider: str = ""
    model: str = ""
    error: str | None = None
    usage: dict[str, Any] | None = None
    tool_rounds: int = 0
    tool_calls: int = 0
    started_at: str = ""
    finished_at: str | None = None


class ChatEventRow(BaseModel):
    """One streamed event, as replayed."""

    seq: int
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    at: str = ""


class ToolCallRow(BaseModel):
    """One dispatch, on its way into the audit."""

    run_id: int | None = None
    caller: str = "chat"
    tool: str
    permission: str = "read"
    input_hash: str = ""
    duration_ms: int = 0
    status: Literal["ok", "error", "refused", "timeout"]
    error: str | None = None


def _loads(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except ValueError:  # pragma: no cover - written by us, so only a corrupt file
        return fallback


class ChatRepository(Repository):
    """Threads, messages and runs. One class because they are never used apart."""

    # -- threads ----------------------------------------------------------

    async def create_thread(self, spec: ChatThreadWrite) -> ChatThreadRow:
        cursor = await self.db.execute(
            "INSERT INTO chat_threads (title, set_id) VALUES (?, ?)",
            (spec.title.strip(), spec.set_id),
        )
        row = await self.get_thread(int(cursor.lastrowid or 0))
        assert row is not None  # just inserted
        return row

    async def get_thread(self, thread_id: int) -> ChatThreadRow | None:
        row = await self.db.fetch_one(
            f"{_THREAD_SELECT} WHERE t.id = ?",
            (thread_id,),
        )
        return self.to_model(ChatThreadRow, row)

    async def list_threads(self, limit: int = 50) -> list[ChatThreadRow]:
        rows = await self.db.fetch_all(
            f"{_THREAD_SELECT} ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
            (limit,),
        )
        return self.to_models(ChatThreadRow, rows)

    async def rename_thread(self, thread_id: int, title: str) -> bool:
        cursor = await self.db.execute(
            "UPDATE chat_threads SET title = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE id = ?",
            (title.strip()[:200], thread_id),
        )
        return cursor.rowcount > 0

    async def delete_thread(self, thread_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
        return cursor.rowcount > 0

    async def touch_thread(self, thread_id: int) -> None:
        await self.db.execute(
            "UPDATE chat_threads SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE id = ?",
            (thread_id,),
        )

    # -- messages ---------------------------------------------------------

    async def add_message(self, spec: ChatMessageWrite) -> ChatMessageRow:
        cursor = await self.db.execute(
            """
            INSERT INTO chat_messages
                (thread_id, run_id, role, content, tool_calls_json, tool_results_json, usage_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.thread_id,
                spec.run_id,
                spec.role,
                spec.content,
                json.dumps(spec.tool_calls) if spec.tool_calls else None,
                json.dumps(spec.tool_results) if spec.tool_results else None,
                json.dumps(spec.usage) if spec.usage else None,
            ),
        )
        await self.touch_thread(spec.thread_id)
        row = await self.db.fetch_one(
            f"{_MESSAGE_SELECT} WHERE id = ?",
            (int(cursor.lastrowid or 0),),
        )
        assert row is not None  # just inserted
        return self._message(row)

    async def messages(self, thread_id: int, limit: int = 500) -> list[ChatMessageRow]:
        rows = await self.db.fetch_all(
            f"{_MESSAGE_SELECT} WHERE thread_id = ? ORDER BY id LIMIT ?",
            (thread_id, limit),
        )
        return [self._message(row) for row in rows]

    def _message(self, row: Any) -> ChatMessageRow:
        data = dict(zip(row.keys(), tuple(row), strict=True))
        data["tool_calls"] = _loads(data.pop("tool_calls_json", None), [])
        data["tool_results"] = _loads(data.pop("tool_results_json", None), [])
        data["usage"] = _loads(data.pop("usage_json", None), None)
        return ChatMessageRow.model_validate(data)

    # -- runs -------------------------------------------------------------

    async def start_run(self, thread_id: int, *, provider: str = "", model: str = "") -> ChatRunRow:
        cursor = await self.db.execute(
            "INSERT INTO chat_runs (thread_id, provider, model) VALUES (?, ?, ?)",
            (thread_id, provider, model),
        )
        run = await self.get_run(int(cursor.lastrowid or 0))
        assert run is not None  # just inserted
        return run

    async def get_run(self, run_id: int) -> ChatRunRow | None:
        row = await self.db.fetch_one(
            f"{_RUN_SELECT} WHERE id = ?",
            (run_id,),
        )
        if row is None:
            return None
        data = dict(zip(row.keys(), tuple(row), strict=True))
        data["usage"] = _loads(data.pop("usage_json", None), None)
        return ChatRunRow.model_validate(data)

    async def finish_run(
        self,
        run_id: int,
        *,
        status: RunStatus,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
        tool_rounds: int = 0,
        tool_calls: int = 0,
        provider: str = "",
        model: str = "",
    ) -> ChatRunRow | None:
        await self.db.execute(
            """
            UPDATE chat_runs
               SET status = ?, error = ?, usage_json = ?, tool_rounds = ?, tool_calls = ?,
                   provider = CASE WHEN ? = '' THEN provider ELSE ? END,
                   model    = CASE WHEN ? = '' THEN model    ELSE ? END,
                   finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (
                status,
                error,
                json.dumps(usage) if usage else None,
                tool_rounds,
                tool_calls,
                provider,
                provider,
                model,
                model,
                run_id,
            ),
        )
        return await self.get_run(run_id)

    async def reconcile_running(self) -> int:
        """Mark every ``running`` run interrupted. Called once, at boot.

        Same rule as an analysis: a run is only ``running`` while a process
        holds it, and no process survives a restart. Without this the chat page
        shows a spinner nobody can clear and a cancel button that cancels
        nothing.
        """
        cursor = await self.db.execute(
            """
            UPDATE chat_runs
               SET status = 'interrupted',
                   error = 'The process restarted while this answer was being written.',
                   finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE status = 'running'
            """
        )
        return cursor.rowcount


class ChatEventsRepository(Repository):
    """The persisted stream. Append-only, read by sequence."""

    async def append(self, run_id: int, seq: int, kind: str, data: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO chat_events (run_id, seq, kind, data) VALUES (?, ?, ?, ?)",
            (run_id, seq, kind, json.dumps(data, default=str)),
        )

    async def since(self, run_id: int, after_seq: int = 0) -> list[ChatEventRow]:
        rows = await self.db.fetch_all(
            "SELECT seq, kind, data, at FROM chat_events WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after_seq),
        )
        return [
            ChatEventRow(
                seq=int(row["seq"]),
                kind=str(row["kind"]),
                data=_loads(row["data"], {}),
                at=str(row["at"]),
            )
            for row in rows
        ]


class ToolCallsRepository(Repository):
    """The ``tool_calls`` audit: one row per dispatch, refusals included."""

    async def record(self, row: ToolCallRow) -> None:
        await self.db.execute(
            """
            INSERT INTO tool_calls
                (run_id, caller, tool, permission, input_hash, duration_ms, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.run_id,
                row.caller,
                row.tool,
                row.permission,
                row.input_hash,
                row.duration_ms,
                row.status,
                row.error,
            ),
        )

    async def for_run(self, run_id: int) -> list[ToolCallRow]:
        rows = await self.db.fetch_all(
            """
            SELECT run_id, caller, tool, permission, input_hash, duration_ms, status, error
              FROM tool_calls WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        )
        return self.to_models(ToolCallRow, rows)

    async def recent(self, limit: int = 50) -> list[ToolCallRow]:
        rows = await self.db.fetch_all(
            """
            SELECT run_id, caller, tool, permission, input_hash, duration_ms, status, error
              FROM tool_calls ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return self.to_models(ToolCallRow, rows)


_THREAD_SELECT = """
SELECT t.id, t.title, t.set_id, s.name AS set_name,
       (SELECT COUNT(*) FROM chat_messages m
         WHERE m.thread_id = t.id AND m.role IN ('user', 'assistant')) AS message_count,
       t.created_at, t.updated_at
  FROM chat_threads t
  LEFT JOIN sets s ON s.id = t.set_id
"""

_MESSAGE_SELECT = """
SELECT id, thread_id, run_id, role, content,
       tool_calls_json, tool_results_json, usage_json, created_at
  FROM chat_messages
"""

_RUN_SELECT = """
SELECT id, thread_id, status, provider, model, error, usage_json,
       tool_rounds, tool_calls, started_at, finished_at
  FROM chat_runs
"""
