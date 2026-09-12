"""`starting_point_runs` — one row per "suggest a starting point".

Shaped after `shot_analyses` (`repos/analyses.py`) deliberately, because it has
the same life: a row is opened before the provider is contacted, closed when it
answers, and reconciled at boot if the process died in between. The one thing
it has that an analysis does not is an *accept*: three options are offered and
at most one of them becomes a Set, so the row carries what was chosen and what
it produced.

The accept is split in two: :meth:`claim` takes the run with
`UPDATE ... WHERE accepted_option IS NULL` **before** anything is created, and
:meth:`finish_accept` records what that produced. Two tabs pressing "use the
recommended one" is an ordinary race, and the loser has to be refused before it
has made a second Set on the same bag — which is why the guard cannot be the
last write of the sequence.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repository import Repository

__all__ = [
    "StartingPointRunRow",
    "StartingPointRunsRepository",
    "StartingPointStart",
]

#: The same four states an analysis has, for the same reasons. `interrupted` is
#: written by boot reconciliation and never by a request.
type StartingPointStatus = Literal["running", "ok", "failed", "interrupted"]


class StartingPointStart(BaseModel):
    """What is known before the provider is contacted."""

    model_config = ConfigDict(extra="forbid")

    bean_id: int
    machine_id: int
    grinder_id: int | None = None
    usual_grind: str = Field(default="", max_length=100)
    dose_hint_g: float | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class StartingPointRunRow(BaseModel):
    """One row of `starting_point_runs`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    bean_id: int
    bean_name: str | None = None
    machine_id: int
    grinder_id: int | None = None
    grinder_name: str | None = None
    usual_grind: str = ""
    dose_hint_g: float | None = None
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    #: The whole assembled context. Verbatim, so a suggestion stays explainable.
    input: dict[str, Any] = Field(default_factory=dict)
    #: The validated :class:`~gaggiclanker.starting.models.StartingPointResult`,
    #: as JSON. ``None`` while running and for ever on a failure.
    output: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    status: StartingPointStatus = "running"
    error: str | None = None
    llm_call_id: str | None = None
    accepted_option: str | None = None
    accepted_set_id: int | None = None
    accepted_set_version_id: int | None = None
    accepted_draft_id: int | None = None
    accepted_at: str | None = None
    created_at: str
    finished_at: str | None = None


#: The joins a reader wants beside the row: which bag and which grinder, by
#: name. A correlated read rather than a second request, the way `beans` joins
#: its Set count.
_SELECT = """
    SELECT r.*, b.name AS bean_name, g.name AS grinder_name
      FROM starting_point_runs r
      LEFT JOIN beans b    ON b.id = r.bean_id
      LEFT JOIN grinders g ON g.id = r.grinder_id
"""


def _decode(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):  # pragma: no cover - written by json.dumps
        return None


class StartingPointRunsRepository(Repository):
    """Reads and writes `starting_point_runs`."""

    async def start(self, spec: StartingPointStart) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO starting_point_runs
                (bean_id, machine_id, grinder_id, usual_grind, dose_hint_g, provider, model,
                 prompt_name, prompt_version, input_json, status, created_at)
            VALUES
                (:bean_id, :machine_id, :grinder_id, :usual_grind, :dose_hint_g, :provider,
                 :model, :prompt_name, :prompt_version, :input_json, 'running', :created_at)
            """,
            {
                "bean_id": spec.bean_id,
                "machine_id": spec.machine_id,
                "grinder_id": spec.grinder_id,
                "usual_grind": spec.usual_grind,
                "dose_hint_g": spec.dose_hint_g,
                "provider": spec.provider,
                "model": spec.model,
                "prompt_name": spec.prompt_name,
                "prompt_version": spec.prompt_version,
                "input_json": json.dumps(spec.input, separators=(",", ":"), sort_keys=True),
                "created_at": utc_now(),
            },
        )
        return int(cursor.lastrowid or 0)

    async def finish(
        self,
        run_id: int,
        *,
        status: StartingPointStatus,
        output: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        provider: str = "",
        model: str = "",
        llm_call_id: str | None = None,
    ) -> StartingPointRunRow | None:
        """Close a run. ``provider``/``model`` overwrite only when non-empty.

        Non-empty, because what actually answered is more interesting than what
        was configured when the row was opened — but a failure before any
        provider was reached reports neither, and blanking the row then would
        lose the configuration that produced the failure.
        """
        await self.db.execute(
            """
            UPDATE starting_point_runs
               SET status = :status,
                   output_json = :output_json,
                   usage_json = :usage_json,
                   error = :error,
                   provider = CASE WHEN :provider = '' THEN provider ELSE :provider END,
                   model = CASE WHEN :model = '' THEN model ELSE :model END,
                   llm_call_id = COALESCE(:llm_call_id, llm_call_id),
                   finished_at = :finished_at
             WHERE id = :id
            """,
            {
                "id": run_id,
                "status": status,
                "output_json": (
                    None
                    if output is None
                    else json.dumps(output, separators=(",", ":"), sort_keys=True)
                ),
                "usage_json": (None if usage is None else json.dumps(usage, separators=(",", ":"))),
                "error": error,
                "provider": provider,
                "model": model,
                "llm_call_id": llm_call_id,
                "finished_at": utc_now(),
            },
        )
        return await self.get(run_id)

    async def claim(self, run_id: int, option: str) -> bool:
        """Take the run for this option, atomically. ``False`` when somebody beat us.

        This is the whole of the accept's concurrency control, and it runs
        **before** anything is created. A guarded UPDATE is atomic in SQLite, so
        of two requests that both read an unaccepted row exactly one writes it;
        the loser gets ``False`` and the caller turns that into a 409.

        It used to run last, after the Set existed — which meant two accepts
        created two Sets for one bag and only the second one was told off. The
        order is the fix, not the guard.

        The ids of what the option produced are filled in afterwards by
        :meth:`finish_accept`, because they do not exist yet: a claim is a
        promise to create them, and :meth:`release` is how a promise that could
        not be kept is withdrawn.
        """
        cursor = await self.db.execute(
            """
            UPDATE starting_point_runs
               SET accepted_option = :option, accepted_at = :accepted_at
             WHERE id = :id AND accepted_option IS NULL
            """,
            {"id": run_id, "option": option, "accepted_at": utc_now()},
        )
        return cursor.rowcount > 0

    async def release(self, run_id: int) -> None:
        """Undo a claim whose work then failed. Only ever an unfinished one.

        The `accepted_set_id IS NULL` guard is what makes this safe to call from
        an exception handler: a claim that did produce a Set is never withdrawn,
        whatever went wrong afterwards, because the Set is real and the row has
        to keep pointing at it.
        """
        await self.db.execute(
            """
            UPDATE starting_point_runs
               SET accepted_option = NULL, accepted_at = NULL
             WHERE id = ? AND accepted_set_id IS NULL
            """,
            (run_id,),
        )

    async def finish_accept(
        self,
        run_id: int,
        *,
        set_id: int,
        set_version_id: int,
        draft_id: int | None = None,
    ) -> None:
        """Record what the claimed option produced. The other half of :meth:`claim`."""
        await self.db.execute(
            """
            UPDATE starting_point_runs
               SET accepted_set_id = :set_id,
                   accepted_set_version_id = :set_version_id,
                   accepted_draft_id = :draft_id
             WHERE id = :id
            """,
            {
                "id": run_id,
                "set_id": set_id,
                "set_version_id": set_version_id,
                "draft_id": draft_id,
            },
        )

    async def get(self, run_id: int) -> StartingPointRunRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE r.id = ?", (run_id,))
        return None if row is None else self._to_row(row)

    async def for_bean(self, bean_id: int, *, limit: int = 20) -> list[StartingPointRunRow]:
        """This bag's runs, newest first."""
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE r.bean_id = ? ORDER BY r.id DESC LIMIT ?", (bean_id, limit)
        )
        return [self._to_row(row) for row in rows]

    async def reconcile_running(self) -> int:
        """Mark every `running` row `interrupted`. Called once at boot.

        A `running` row is only true while a process holds it, and no process
        survives a restart — leaving them would be a spinner nobody can clear,
        which is the same reasoning as `AnalysesRepository.reconcile_running`.
        """
        cursor = await self.db.execute(
            """
            UPDATE starting_point_runs
               SET status = 'interrupted',
                   error = COALESCE(error, 'interrupted: the process restarted mid-call'),
                   finished_at = ?
             WHERE status = 'running'
            """,
            (utc_now(),),
        )
        return cursor.rowcount

    def _to_row(self, row: Any) -> StartingPointRunRow:
        data = dict(row)
        data["input"] = _decode(data.pop("input_json", None)) or {}
        data["output"] = _decode(data.pop("output_json", None))
        data["usage"] = _decode(data.pop("usage_json", None))
        return StartingPointRunRow.model_validate(data)
