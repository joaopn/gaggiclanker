"""`shot_analyses` and `suggestions` — one LLM run, and what came of it.

An analysis row is opened before the provider is contacted and closed after it
answers, whichever way it answered. That ordering is what makes a crashed
process visible: :meth:`AnalysesRepository.reconcile_running` turns every row
still marked `running` at boot into `interrupted`, so "the container restarted
mid-analysis" is a state on the page rather than a spinner that never stops.

Suggestions are rows rather than a slice of the output document because a
suggestion has a life — open, then accepted or rejected, or superseded by a
later one for the same variable — and none of that fits inside an output that
must stay exactly as the model wrote it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import AnalysisStatus, SuggestionStatus, SuggestionVariable

__all__ = [
    "AnalysesRepository",
    "AnalysisRow",
    "AnalysisStart",
    "SuggestionRow",
    "SuggestionWrite",
    "SuggestionsRepository",
]


class AnalysisStart(BaseModel):
    """What is known before the provider is asked anything."""

    model_config = ConfigDict(extra="forbid")

    shot_id: int
    set_version_id: int | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: The rendered context, snapshotted. Not a reference to the prompt and the
    #: rules: both can change, and an old analysis explained by today's inputs
    #: is worse than one not explained at all.
    input: dict[str, Any] = Field(default_factory=dict)


class AnalysisRow(BaseModel):
    """One row of `shot_analyses`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    shot_id: int
    set_version_id: int | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    input: JsonObject = Field(default=None, validation_alias="input_json")
    output: JsonObject = Field(default=None, validation_alias="output_json")
    usage: JsonObject = Field(default=None, validation_alias="usage_json")
    cost_estimate: float | None = None
    status: AnalysisStatus = "running"
    error: str | None = None
    llm_call_id: str | None = None
    created_at: str
    finished_at: str | None = None
    #: Joined in by the read queries. Empty on a running or failed analysis.
    suggestions: list[SuggestionRow] = Field(default_factory=list)


class SuggestionWrite(BaseModel):
    """One suggestion, on its way out of a validated output document."""

    model_config = ConfigDict(extra="forbid")

    variable: SuggestionVariable
    direction: str = ""
    magnitude: float | None = None
    unit: str = "none"
    reason: str = ""
    confidence: str = ""
    priority: int = 1


class SuggestionRow(BaseModel):
    """One row of `suggestions`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    analysis_id: int
    variable: SuggestionVariable
    direction: str = ""
    magnitude: float | None = None
    unit: str = "none"
    reason: str = ""
    confidence: str = ""
    priority: int = 1
    status: SuggestionStatus = "open"
    resulting_set_version_id: int | None = None
    created_at: str
    #: Joined in where the reader needs to know which shot this is advice about
    #: — the Set page lists suggestions across every shot in the Set.
    shot_id: int | None = None
    set_version_id: int | None = None


class AnalysesRepository(Repository):
    """Opens, closes and reads analysis runs."""

    async def start(self, spec: AnalysisStart) -> int:
        """Open a `running` row. Returns its id."""
        payload = {
            "shot_id": spec.shot_id,
            "set_version_id": spec.set_version_id,
            "provider": spec.provider,
            "model": spec.model,
            "prompt_name": spec.prompt_name,
            "prompt_version": spec.prompt_version,
            "input_json": dumps(spec.input),
            "status": "running",
            "created_at": utc_now(),
        }
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        cursor = await self.db.execute(
            f"INSERT INTO shot_analyses ({columns}) VALUES ({placeholders})",  # noqa: S608 - keys are the literal payload above
            payload,
        )
        return int(cursor.lastrowid or 0)

    async def finish(
        self,
        analysis_id: int,
        *,
        status: AnalysisStatus,
        output: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        llm_call_id: str | None = None,
        provider: str = "",
        model: str = "",
    ) -> AnalysisRow | None:
        """Close a run, however it went.

        ``provider`` and ``model`` are written again because the request may have
        left them to the configured defaults, and what the row should record is
        what actually answered.
        """
        await self.db.execute(
            """
            UPDATE shot_analyses
               SET status = :status,
                   output_json = :output_json,
                   usage_json = :usage_json,
                   error = :error,
                   llm_call_id = COALESCE(:llm_call_id, llm_call_id),
                   provider = CASE WHEN :provider = '' THEN provider ELSE :provider END,
                   model = CASE WHEN :model = '' THEN model ELSE :model END,
                   finished_at = :finished_at
             WHERE id = :id
            """,
            {
                "id": analysis_id,
                "status": status,
                "output_json": None if output is None else dumps(output),
                "usage_json": None if usage is None else dumps(usage),
                "error": None if error is None else error[:1000],
                "llm_call_id": llm_call_id,
                "provider": provider,
                "model": model,
                "finished_at": utc_now(),
            },
        )
        return await self.get(analysis_id)

    async def get(self, analysis_id: int, *, with_suggestions: bool = True) -> AnalysisRow | None:
        row = await self.db.fetch_one("SELECT * FROM shot_analyses WHERE id = ?", (analysis_id,))
        model = self.to_model(AnalysisRow, row)
        if model is not None and with_suggestions:
            model.suggestions = await SuggestionsRepository(self.db).for_analysis(analysis_id)
        return model

    async def for_shot(self, shot_id: int, *, with_suggestions: bool = True) -> list[AnalysisRow]:
        """Every analysis of one shot, newest first — the order the panel reads."""
        rows = await self.db.fetch_all(
            "SELECT * FROM shot_analyses WHERE shot_id = ? ORDER BY id DESC",
            (shot_id,),
        )
        models = self.to_models(AnalysisRow, rows)
        if with_suggestions and models:
            by_analysis = await SuggestionsRepository(self.db).for_analyses(
                [model.id for model in models]
            )
            for model in models:
                model.suggestions = by_analysis.get(model.id, [])
        return models

    async def latest_for_shot(self, shot_id: int) -> AnalysisRow | None:
        rows = await self.for_shot(shot_id)
        return rows[0] if rows else None

    async def running_shot_ids(self) -> set[int]:
        """Every shot with an analysis in flight right now.

        The batch's own guard against doubling work: the registry name stops
        two *requests* colliding on one shot, but a batch runs its shots inside
        its own task where the registry cannot see them, so the row is what the
        two paths agree on.
        """
        rows = await self.db.fetch_all(
            "SELECT DISTINCT shot_id FROM shot_analyses WHERE status = 'running'"
        )
        return {int(row["shot_id"]) for row in rows}

    async def unanalysed_in_set(self, set_id: int) -> list[int]:
        """Shots in this Set with no successful analysis, oldest first.

        A failed or interrupted run does not count as analysed: the whole point
        of "analyse the un-analysed" is to pick up what the rate limit or the
        restart dropped.

        A run that is still *going* is **not** filtered here. The batch does
        that itself (`AnalyzerService._batch_shots`), because it has to report
        how many it left alone and a row that vanished from this query cannot
        be counted.
        """
        rows = await self.db.fetch_all(
            """
            SELECT sh.id
              FROM shots sh
              JOIN set_versions v ON v.id = sh.set_version_id
             WHERE v.set_id = ?
               AND sh.quarantined = 0
               AND NOT EXISTS (
                     SELECT 1 FROM shot_analyses a
                      WHERE a.shot_id = sh.id AND a.status = 'ok')
             ORDER BY COALESCE(sh.started_at, ''), sh.id
            """,
            (set_id,),
        )
        return [int(row["id"]) for row in rows]

    async def shots_in_set(self, set_id: int) -> list[int]:
        rows = await self.db.fetch_all(
            """
            SELECT sh.id
              FROM shots sh
              JOIN set_versions v ON v.id = sh.set_version_id
             WHERE v.set_id = ? AND sh.quarantined = 0
             ORDER BY COALESCE(sh.started_at, ''), sh.id
            """,
            (set_id,),
        )
        return [int(row["id"]) for row in rows]

    async def reconcile_running(self) -> int:
        """Mark every `running` row `interrupted`. Runs once, at boot.

        A row is only `running` while a process is holding it, and no process
        survives a boot. Anything still in that state was cut off mid-call —
        a container restart, an OOM, a power cut — and saying so is the whole
        point: the alternative is a shot whose panel spins for ever with no row
        anybody can act on.
        """
        cursor = await self.db.execute(
            """
            UPDATE shot_analyses
               SET status = 'interrupted',
                   error = COALESCE(error, 'the process stopped before this analysis finished'),
                   finished_at = ?
             WHERE status = 'running'
            """,
            (utc_now(),),
        )
        return cursor.rowcount


class SuggestionsRepository(Repository):
    """Reads and writes the actionable part of an analysis."""

    async def insert_many(self, analysis_id: int, items: list[SuggestionWrite]) -> list[int]:
        ids: list[int] = []
        now = utc_now()
        for item in items:
            cursor = await self.db.execute(
                """
                INSERT INTO suggestions
                    (analysis_id, variable, direction, magnitude, unit, reason,
                     confidence, priority, status, created_at)
                VALUES (:analysis_id, :variable, :direction, :magnitude, :unit, :reason,
                        :confidence, :priority, 'open', :created_at)
                """,
                {
                    "analysis_id": analysis_id,
                    "variable": item.variable,
                    "direction": item.direction,
                    "magnitude": item.magnitude,
                    "unit": item.unit,
                    "reason": item.reason,
                    "confidence": item.confidence,
                    "priority": item.priority,
                    "created_at": now,
                },
            )
            ids.append(int(cursor.lastrowid or 0))
        return ids

    async def get(self, suggestion_id: int) -> SuggestionRow | None:
        row = await self.db.fetch_one(
            """
            SELECT s.*, a.shot_id, a.set_version_id
              FROM suggestions s
              JOIN shot_analyses a ON a.id = s.analysis_id
             WHERE s.id = ?
            """,
            (suggestion_id,),
        )
        return self.to_model(SuggestionRow, row)

    async def for_analysis(self, analysis_id: int) -> list[SuggestionRow]:
        rows = await self.db.fetch_all(
            """
            SELECT s.*, a.shot_id, a.set_version_id
              FROM suggestions s
              JOIN shot_analyses a ON a.id = s.analysis_id
             WHERE s.analysis_id = ?
             ORDER BY s.priority, s.id
            """,
            (analysis_id,),
        )
        return self.to_models(SuggestionRow, rows)

    async def for_analyses(self, analysis_ids: list[int]) -> dict[int, list[SuggestionRow]]:
        if not analysis_ids:
            return {}
        placeholders = ", ".join("?" for _ in analysis_ids)
        rows = await self.db.fetch_all(
            f"""
            SELECT s.*, a.shot_id, a.set_version_id
              FROM suggestions s
              JOIN shot_analyses a ON a.id = s.analysis_id
             WHERE s.analysis_id IN ({placeholders})
             ORDER BY s.priority, s.id
            """,  # noqa: S608 - placeholders are generated, ids are bound
            analysis_ids,
        )
        out: dict[int, list[SuggestionRow]] = {}
        for model in self.to_models(SuggestionRow, rows):
            out.setdefault(model.analysis_id, []).append(model)
        return out

    async def for_set(self, set_id: int) -> list[SuggestionRow]:
        """Every suggestion made about any shot in this Set, newest first.

        The Set page's own list. Ordered by the analysis rather than by the
        suggestion, because advice is read as a run: three suggestions from one
        shot belong together whatever their priorities.
        """
        rows = await self.db.fetch_all(
            """
            SELECT s.*, a.shot_id, a.set_version_id
              FROM suggestions s
              JOIN shot_analyses a ON a.id = s.analysis_id
              JOIN set_versions v ON v.id = a.set_version_id
             WHERE v.set_id = ?
             ORDER BY a.created_at DESC, a.id DESC, s.priority, s.id
            """,
            (set_id,),
        )
        return self.to_models(SuggestionRow, rows)

    async def for_shots(self, shot_ids: list[int]) -> dict[int, list[SuggestionRow]]:
        """Suggestions per shot, for the trajectory. One query, not N."""
        if not shot_ids:
            return {}
        placeholders = ", ".join("?" for _ in shot_ids)
        rows = await self.db.fetch_all(
            f"""
            SELECT s.*, a.shot_id, a.set_version_id
              FROM suggestions s
              JOIN shot_analyses a ON a.id = s.analysis_id
             WHERE a.shot_id IN ({placeholders})
             ORDER BY a.created_at, s.priority, s.id
            """,  # noqa: S608 - placeholders are generated, ids are bound
            shot_ids,
        )
        out: dict[int, list[SuggestionRow]] = {}
        for model in self.to_models(SuggestionRow, rows):
            if model.shot_id is not None:
                out.setdefault(model.shot_id, []).append(model)
        return out

    async def accept(self, suggestion_id: int, version_id: int | None) -> bool:
        """Mark one accepted and supersede its open siblings, atomically.

        "Siblings" is every other *open* suggestion for the same variable in the
        same analysis. They were not rejected — nobody disagreed with them — they
        were overtaken, and the distinction is what makes "how often is the
        model's first choice the one you take" answerable later.
        """
        async with self.db.transaction():
            cursor = await self.db.execute(
                """
                UPDATE suggestions
                   SET status = 'accepted', resulting_set_version_id = ?
                 WHERE id = ? AND status = 'open'
                """,
                (version_id, suggestion_id),
            )
            if cursor.rowcount == 0:
                return False
            await self.db.execute(
                """
                UPDATE suggestions
                   SET status = 'superseded'
                 WHERE status = 'open'
                   AND id != ?
                   AND analysis_id = (SELECT analysis_id FROM suggestions WHERE id = ?)
                   AND variable = (SELECT variable FROM suggestions WHERE id = ?)
                """,
                (suggestion_id, suggestion_id, suggestion_id),
            )
        return True

    async def reject(self, suggestion_id: int) -> bool:
        cursor = await self.db.execute(
            "UPDATE suggestions SET status = 'rejected' WHERE id = ? AND status = 'open'",
            (suggestion_id,),
        )
        return cursor.rowcount > 0
