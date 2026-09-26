"""Data access for the chat: threads, messages, runs, the stream, the audit.

A thread is one of two things and the repository is where that stays true: a
**general** conversation about the archive, or a conversation about **one
version of one Set** — the room where that one change was argued. Creating one
with a Set and no version files it under whatever is current at that moment and
it stays there for good, so a folder's conversations read as a history rather
than as a pile that all claim to be about the latest recipe.

The one non-obvious table is ``chat_events``. Everything the SSE stream emits is
written here before it is published, and the stream route replays from it on
connect. That is what makes closing a laptop mid-answer harmless: the browser
comes back with the last ``seq`` it saw and gets the rest, rather than a blank
bubble it can never fill in because the in-memory bus is lossy by design.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from gaggiclanker.db.repos.sets import SetsRepository
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
    "ThreadRefusal",
    "ThreadWriteResult",
    "ToolCallRow",
    "ToolCallsRepository",
]

type RunStatus = Literal["running", "ok", "failed", "cancelled", "interrupted"]

#: Why a thread could not be created where it was asked for. A slug, as in
#: :mod:`gaggiclanker.db.repos.sets`: the repository has no opinion about
#: statuses and the route that does is the one place the mapping is written.
type ThreadRefusal = Literal["no_set", "no_version", "version_without_set"]


class ChatThreadWrite(BaseModel):
    """A new thread.

    Both fields optional, and what they mean together is the thread's kind.
    Neither is a **general** conversation. ``set_id`` alone is a conversation
    about that Set's **current** version — pressing New in a folder — and
    ``set_version_id`` beside it names the version outright, which is what
    continuing an older experiment looks like. A version without its Set is
    refused rather than guessed at: the two travel together everywhere else.
    """

    title: str = Field(default="", max_length=200)
    set_id: int | None = None
    set_version_id: int | None = None


class ChatThreadRow(BaseModel):
    """One thread as listed, with what the folder needs to label it."""

    id: int
    title: str = ""
    set_id: int | None = None
    set_name: str | None = None
    #: The version this conversation is about. NULL exactly when ``set_id`` is.
    set_version_id: int | None = None
    #: That version's number, joined in: a folder row reads "v6 · title", and a
    #: reader thinks in "v6" rather than in a row id.
    set_version_no: int | None = None
    #: A later roll back stepped over this version. Derived from the Set's line,
    #: never stored — it is a fact about what came after — and carried here so
    #: the folder can mute the row without a request per conversation.
    dead_end: bool = False
    message_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class ThreadWriteResult:
    """A created thread, or the reason there is none. Exactly one is set."""

    thread: ChatThreadRow | None = None
    refused: ThreadRefusal | None = None


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

    async def create_thread(self, spec: ChatThreadWrite) -> ThreadWriteResult:
        """Start a conversation, on a version it is allowed to be about.

        The version is resolved and checked inside the transaction that writes
        the row, because "the current version" is a moving answer: a version
        added while this request was in flight would otherwise file the thread
        under a recipe nobody had brewed with yet.
        """
        async with self.db.transaction():
            resolved = await self._resolve_version(spec.set_id, spec.set_version_id)
            if isinstance(resolved, str):
                return ThreadWriteResult(refused=resolved)
            cursor = await self.db.execute(
                "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES (?, ?, ?)",
                (spec.title.strip(), spec.set_id, resolved),
            )
            thread_id = int(cursor.lastrowid or 0)
        row = await self.get_thread(thread_id)
        assert row is not None  # just inserted
        return ThreadWriteResult(thread=row)

    async def open_thread(
        self, set_id: int, set_version_id: int | None = None
    ) -> ThreadWriteResult:
        """The conversation about this version, or a new one when there is none.

        What Review and Discuss do: a second shot pulled under the same version
        is argued in the same room as the first. "The conversation" is the most
        recently updated one, so a version somebody has started several
        conversations about continues in the one they were last in.

        Except the conversation a Set's first recipe was designed in, once the
        design is over. It belongs to version 1 as well, but one conversation is
        one version's worth of work, and designing the recipe was that work: the
        agent tells the person to start a new conversation to analyse the shots,
        so Discuss on version 1 must not bring them back into the design. While
        the Set is still being designed it is the room, and Continue designing
        lands in it. "The design conversation" is one a first-recipe card was
        proposed in — the only trace a design leaves on a thread.
        """
        async with self.db.transaction():
            resolved = await self._resolve_version(set_id, set_version_id)
            if isinstance(resolved, str):
                return ThreadWriteResult(refused=resolved)
            existing = await self.db.fetch_value(
                "SELECT t.id FROM chat_threads t JOIN sets s ON s.id = t.set_id "
                "WHERE t.set_version_id = ? AND (s.designing = 1 OR NOT EXISTS ("
                "  SELECT 1 FROM set_version_proposals p "
                "  WHERE p.thread_id = t.id AND p.kind = 'design')) "
                "ORDER BY t.updated_at DESC, t.id DESC LIMIT 1",
                (resolved,),
            )
            if existing is None:
                cursor = await self.db.execute(
                    "INSERT INTO chat_threads (title, set_id, set_version_id) VALUES (?, ?, ?)",
                    ("", set_id, resolved),
                )
                existing = int(cursor.lastrowid or 0)
        row = await self.get_thread(int(existing))
        assert row is not None  # either found or just inserted
        return ThreadWriteResult(thread=row)

    async def _resolve_version(
        self, set_id: int | None, set_version_id: int | None
    ) -> int | ThreadRefusal | None:
        """Which version a thread belongs to: the id, ``None``, or a refusal.

        Through :class:`~gaggiclanker.db.repos.sets.SetsRepository` rather than
        with SQL of its own — "the current version of a Set" and "a version of
        this Set" are rules that already exist there, and a second copy here is
        how a thread would come to be filed under a version the Set page does
        not think is current.
        """
        if set_id is None:
            return "version_without_set" if set_version_id is not None else None
        sets = SetsRepository(self.db)
        if set_version_id is None:
            current = await sets.current_version(set_id)
            if current is None:
                return "no_set"
            return current.id
        if await sets.version_of_set(set_id, set_version_id) is None:
            return "no_version"
        return set_version_id

    async def get_thread(self, thread_id: int) -> ChatThreadRow | None:
        row = await self.db.fetch_one(
            f"{_THREAD_SELECT} WHERE t.id = ?",
            (thread_id,),
        )
        thread = self.to_model(ChatThreadRow, row)
        if thread is None:
            return None
        return (await self.mark_dead_ends([thread]))[0]

    async def list_threads(self, limit: int = 50) -> list[ChatThreadRow]:
        rows = await self.db.fetch_all(
            f"{_THREAD_SELECT} ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
            (limit,),
        )
        return await self.mark_dead_ends(self.to_models(ChatThreadRow, rows))

    async def mark_dead_ends(self, threads: Sequence[ChatThreadRow]) -> list[ChatThreadRow]:
        """Fill in ``dead_end`` for a page of threads, in one further query.

        A dead end is a fact about a Set's whole line — walk back from its
        current version and see what you step over — so it cannot be a column
        on the thread and it cannot be joined in. It is one query for every Set
        the page mentions, which is what keeps the folder list at two queries
        rather than one per conversation.
        """
        rows = list(threads)
        set_ids = {row.set_id for row in rows if row.set_id is not None}
        if not set_ids:
            return rows
        dead = await SetsRepository(self.db).dead_end_versions(set_ids)
        return [
            row.model_copy(update={"dead_end": row.set_version_id in dead})
            if row.set_version_id is not None
            else row
            for row in rows
        ]

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
       t.set_version_id, v.version_no AS set_version_no,
       (SELECT COUNT(*) FROM chat_messages m
         WHERE m.thread_id = t.id AND m.role IN ('user', 'assistant')) AS message_count,
       t.created_at, t.updated_at
  FROM chat_threads t
  LEFT JOIN sets s ON s.id = t.set_id
  LEFT JOIN set_versions v ON v.id = t.set_version_id
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
