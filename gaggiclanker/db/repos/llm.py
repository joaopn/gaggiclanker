"""Data access for the two LLM tables: prompts, and the usage ledger."""

from __future__ import annotations

from pydantic import BaseModel, Field

from gaggiclanker.db.repository import Repository

__all__ = [
    "LlmCallRow",
    "LlmCallsRepository",
    "PromptRow",
    "PromptsRepository",
    "UsageTotals",
]


class PromptRow(BaseModel):
    """One prompt as stored: the live text, the shipped text, and when it moved."""

    name: str
    content: str
    default_content: str
    updated_at: str = ""

    @property
    def edited(self) -> bool:
        """A plain comparison, which is exactly what "reset" undoes."""
        return self.content != self.default_content


class LlmCallRow(BaseModel):
    """One finished call, on its way into the ledger."""

    call_id: str
    purpose: str
    label: str = ""
    subject: str | None = None
    provider: str
    model: str | None = None
    mode: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    status: str
    error: str | None = None
    #: The rendered messages and the raw reply, when `llmStoreCallText` is on.
    #: See migration 0004: this is what makes a stored analysis explainable
    #: after the prompt that produced it has been edited.
    input_text: str | None = None
    output_text: str | None = None
    created_at: str = ""


class UsageTotals(BaseModel):
    """What ``GET /api/llm/usage`` adds up."""

    since: str | None = None
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    by_model: list[dict[str, object]] = Field(default_factory=list)


class PromptsRepository(Repository):
    """The ``prompts`` table. Seeding rules live in :mod:`gaggiclanker.llm.prompts`."""

    async def get(self, name: str) -> PromptRow | None:
        row = await self.db.fetch_one(
            "SELECT name, content, default_content, updated_at FROM prompts WHERE name = ?",
            (name,),
        )
        return self.to_model(PromptRow, row)

    async def list_all(self) -> list[PromptRow]:
        rows = await self.db.fetch_all(
            "SELECT name, content, default_content, updated_at FROM prompts ORDER BY name"
        )
        return self.to_models(PromptRow, rows)

    async def insert(self, name: str, content: str) -> None:
        """A file we have never seen. Live text and default start identical."""
        await self.db.execute(
            "INSERT INTO prompts (name, content, default_content) VALUES (?, ?, ?)",
            (name, content, content),
        )

    async def refresh_both(self, name: str, content: str) -> None:
        """The file changed and nobody had edited the row: take the new text."""
        await self.db.execute(
            """
            UPDATE prompts
               SET content = ?, default_content = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             WHERE name = ?
            """,
            (content, content, name),
        )

    async def refresh_default(self, name: str, content: str) -> None:
        """The file changed but the row is edited: record the new default only.

        The user's text is never clobbered by an upgrade — but "reset to
        default" now converges on the *new* wording, which is what makes an
        image upgrade reachable at all for a prompt someone has touched.
        """
        await self.db.execute(
            "UPDATE prompts SET default_content = ? WHERE name = ?",
            (content, name),
        )

    async def set_content(self, name: str, content: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE prompts
               SET content = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             WHERE name = ?
            """,
            (content, name),
        )
        return cursor.rowcount > 0

    async def reset(self, name: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE prompts
               SET content = default_content,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             WHERE name = ?
            """,
            (name,),
        )
        return cursor.rowcount > 0


class LlmCallsRepository(Repository):
    """The ``llm_calls`` ledger: one row per finished call, and the totals."""

    async def record(self, row: LlmCallRow) -> None:
        await self.db.execute(
            """
            INSERT INTO llm_calls (
                call_id, purpose, label, subject, provider, model, mode,
                prompt_name, prompt_version, input_tokens, output_tokens,
                duration_ms, status, error, input_text, output_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(call_id) DO UPDATE SET
                status = excluded.status,
                mode = excluded.mode,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                duration_ms = excluded.duration_ms,
                error = excluded.error,
                input_text = excluded.input_text,
                output_text = excluded.output_text
            """,
            (
                row.call_id,
                row.purpose,
                row.label,
                row.subject,
                row.provider,
                row.model,
                row.mode,
                row.prompt_name,
                row.prompt_version,
                row.input_tokens,
                row.output_tokens,
                row.duration_ms,
                row.status,
                row.error,
                row.input_text,
                row.output_text,
            ),
        )

    async def recent(self, limit: int = 50) -> list[LlmCallRow]:
        rows = await self.db.fetch_all(
            """
            SELECT call_id, purpose, label, subject, provider, model, mode,
                   prompt_name, prompt_version, input_tokens, output_tokens,
                   duration_ms, status, error, input_text, output_text, created_at
              FROM llm_calls
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        )
        return self.to_models(LlmCallRow, rows)

    async def totals(self, since: str | None = None) -> UsageTotals:
        """Everything since ``since`` (an ISO timestamp), or everything.

        ``COALESCE`` on every sum because an unreported token count is NULL and
        ``SUM`` over an empty set is NULL too — without it "no calls yet" and
        "one call with no usage" both come back as a null the API would have to
        special-case twice.
        """
        where = "WHERE created_at >= ?" if since else ""
        params: tuple[str, ...] = (since,) if since else ()
        row = await self.db.fetch_one(
            f"""
            SELECT COUNT(*)                                          AS calls,
                   COALESCE(SUM(status = 'succeeded'), 0)            AS succeeded,
                   COALESCE(SUM(status = 'failed'), 0)               AS failed,
                   COALESCE(SUM(input_tokens), 0)                    AS input_tokens,
                   COALESCE(SUM(output_tokens), 0)                   AS output_tokens,
                   COALESCE(SUM(duration_ms), 0)                     AS duration_ms
              FROM llm_calls {where}
            """,  # noqa: S608 - `where` is a literal chosen here, never user input
            params,
        )
        by_model = await self.db.fetch_all(
            f"""
            SELECT COALESCE(model, '') AS model,
                   COALESCE(provider, '') AS provider,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens
              FROM llm_calls {where}
             GROUP BY model, provider
             ORDER BY calls DESC
            """,  # noqa: S608 - same literal
            params,
        )
        totals = UsageTotals(since=since)
        if row is not None:
            totals = UsageTotals(
                since=since,
                calls=int(row["calls"]),
                succeeded=int(row["succeeded"]),
                failed=int(row["failed"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                duration_ms=int(row["duration_ms"]),
            )
        totals.by_model = [
            {
                "model": entry["model"],
                "provider": entry["provider"],
                "calls": int(entry["calls"]),
                "input_tokens": int(entry["input_tokens"]),
                "output_tokens": int(entry["output_tokens"]),
            }
            for entry in by_model
        ]
        return totals
