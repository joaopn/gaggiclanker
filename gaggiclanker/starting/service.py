"""Proposing a starting point, and turning the one somebody picked into a Set.

Two halves, and they are deliberately far apart in time: `propose` spends a
provider call and stores three options; `accept` is a button press that happens
minutes or days later and creates exactly one Set.

**`propose` is shaped exactly like an analysis** (`analyzer/service.py`), for
the same three reasons and with the same consequences:

* the row is opened before the call, so a process that dies mid-call leaves a
  `running` row the next boot marks `interrupted` rather than a spinner nobody
  can clear;
* a provider failure is a stored `failed` row, never an exception — the row is
  the handle the page is already rendering, and an error with no id is an error
  nobody can look up;
* the call does not run inside the HTTP request. `start` hands the work to the
  app's `TaskRegistry` and the route answers 202; the task name
  (`starting_point:<bean>:<machine>:<grinder>`) is claimed synchronously inside
  `spawn`, so a second tab pressing the button gets the running row rather than
  a second paid call for the same bag on the same kit.

**`accept` is where the money is.** It creates the Set and its first version
with `origin='starting_point'` — the only origin that can appear on a version 1
— and, when the chosen option carried a whole profile document, a draft through
`DraftsService.create_manual`, which is what puts it through the schema, the
safety policy and the clamp. A document the policy refuses is a 422 naming
every violation, and the Set is **not** created: half-accepting an option would
leave a Set pointing at a profile that does not exist.

The accept is idempotent by refusal rather than by repetition, and the *order*
is what makes that true: the run is claimed with a guarded UPDATE before
anything is created, so of two concurrent accepts exactly one proceeds. One run
yields one Set; the loser gets a 409 carrying the Set the winner made, which is
the thing the person actually wants to be taken to. A claim whose work then
fails — a profile document the policy refuses — is released again, so the other
two options stay reachable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profile_drafts import ProfileDraftRow
from gaggiclanker.db.repos.sets import (
    SetRow,
    SetsRepository,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.db.repos.starting import (
    StartingPointRunRow,
    StartingPointRunsRepository,
    StartingPointStart,
)
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable
from gaggiclanker.infra.sse import SseEvent, SseEventBus
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.prompts import PromptService
from gaggiclanker.llm.service import LlmService
from gaggiclanker.llm.types import LlmMessage, LlmRequest, Ok
from gaggiclanker.starting.context import StartingPointContext, build_context
from gaggiclanker.starting.models import OPTION_KEYS, StartingPointOption, StartingPointResult

__all__ = [
    "STARTING_POINT_EVENTS",
    "STARTING_POINT_PROMPT",
    "STARTING_POINT_USER_PROMPT",
    "AcceptedStartingPoint",
    "StartingPointService",
    "starting_point_task_name",
]

log = structlog.get_logger(__name__)

#: The two prompts one run renders. Split for the reason the analysis pair is:
#: the persona and the layout of the facts are edited by different people for
#: different reasons.
STARTING_POINT_PROMPT = "starting_point"
STARTING_POINT_USER_PROMPT = "starting_point-user"

#: Published on the LLM bus, which the header's activity indicator is already
#: watching — so a suggestion started in the Sets wizard shows up in a tab
#: looking at something else.
STARTING_POINT_EVENTS = (
    "starting_point.started",
    "starting_point.finished",
    "starting_point.failed",
)

#: Linear backoff between this call's own retries. A real wait in production;
#: the suite sets it to zero rather than sleeping out every failure path.
RETRY_DELAY_S = 0.5

#: The label of the synthetic base a draft is built on when the archive has no
#: profile at all. See :meth:`StartingPointService._base_version_for`.
SYNTHETIC_BASE_LABEL = "Empty baseline"


def starting_point_task_name(bean_id: int, machine_id: int, grinder_id: int | None) -> str:
    """The registry name that makes one run per bag-and-kit an invariant.

    Keyed on the whole triple rather than on the bean: asking about the same bag
    on a second grinder is a different question with a different answer, and
    refusing it because the first is still running would be wrong.
    """
    return f"starting_point:{bean_id}:{machine_id}:{grinder_id or 0}"


@dataclass(frozen=True, slots=True)
class AcceptedStartingPoint:
    """What an accept produced: the Set, its first version, and any draft."""

    run: StartingPointRunRow
    set_row: SetRow
    version: SetVersionRow
    draft: ProfileDraftRow | None = None


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything assembled before the provider is contacted."""

    run: StartingPointRunRow
    context: StartingPointContext
    system: str
    user: str
    version: str
    model: str


class StartingPointService:
    """The one entry point. Held on ``app.state.starting``."""

    def __init__(
        self,
        db: Database,
        llm: LlmService,
        prompts: PromptService,
        *,
        drafts: Any = None,
        bus: SseEventBus | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.prompts = prompts
        #: :class:`~gaggiclanker.drafts.service.ProfileDraftService`. Untyped to
        #: keep this module out of the draft service's import graph, which
        #: reaches the device client.
        self.drafts = drafts
        self.bus = bus
        self.retry_delay_s = RETRY_DELAY_S
        self.runs = StartingPointRunsRepository(db)
        self.sets = SetsRepository(db)
        # Runs whose row is being opened right now, so a second request waits
        # for the first one's row instead of reading a database it has not
        # written to yet. Exactly the analyzer's `_opening` map, and it is held
        # only across the opening.
        self._opening: dict[str, asyncio.Future[StartingPointRunRow]] = {}

    # ── proposing ────────────────────────────────────────────────────

    async def start(
        self,
        *,
        bean_id: int,
        machine_id: int,
        grinder_id: int | None = None,
        usual_grind: str = "",
        dose_hint_g: float | None = None,
        tasks: TaskRegistry,
        model: str | None = None,
    ) -> tuple[StartingPointRunRow, bool]:
        """Queue a run. Returns the row and whether this call started it.

        The row comes back before the provider is contacted, because the row is
        the handle: the wizard renders it as `running`, the LLM stream says when
        it moves, and the caller never holds a two-minute request open.

        Raises ``LookupError`` for a bean, machine or grinder that does not
        exist — the caller's mistake rather than the provider's, and it wants to
        be a 404 rather than a stored `failed` row.
        """
        name = starting_point_task_name(bean_id, machine_id, grinder_id)
        # No await before the spawn, so this check and the registry's own name
        # guard together leave no window.
        pending = self._opening.get(name)
        if pending is not None:
            return await asyncio.shield(pending), False

        opened: asyncio.Future[StartingPointRunRow] = asyncio.get_running_loop().create_future()
        self._opening[name] = opened
        spec: dict[str, Any] = {
            "bean_id": bean_id,
            "machine_id": machine_id,
            "grinder_id": grinder_id,
            "usual_grind": usual_grind,
            "dose_hint_g": dose_hint_g,
            "model": model,
        }
        try:
            tasks.spawn(name, self._background(name, spec, opened))
        except RuntimeError:
            # The name is held by a task that has already opened its row — it
            # removed itself from `_opening` when it did.
            del self._opening[name]
            opened.cancel()
            running = await self._latest_running(bean_id, machine_id, grinder_id)
            if running is not None:
                return running, False
            raise
        row = await opened
        return row, row.status == "running"

    async def _background(
        self,
        name: str,
        spec: dict[str, Any],
        opened: asyncio.Future[StartingPointRunRow],
    ) -> None:
        """The registered task: open the row, hand it back, then do the work."""
        try:
            prepared = await self._prepare(**spec)
        except BaseException as exc:
            if not opened.done():
                opened.set_exception(exc)
            raise
        finally:
            self._opening.pop(name, None)
        opened.set_result(prepared.run)
        await self._complete(prepared)

    async def propose(
        self,
        *,
        bean_id: int,
        machine_id: int,
        grinder_id: int | None = None,
        usual_grind: str = "",
        dose_hint_g: float | None = None,
        model: str | None = None,
        as_of: str = "",
    ) -> StartingPointRunRow:
        """Ask for a starting point and wait for it. Returns the row, whatever happened.

        The direct form, for tests and for anything that genuinely wants to
        block. A route should call :meth:`start` instead — see the module
        docstring for why a provider call has no business inside a request.
        """
        prepared = await self._prepare(
            bean_id=bean_id,
            machine_id=machine_id,
            grinder_id=grinder_id,
            usual_grind=usual_grind,
            dose_hint_g=dose_hint_g,
            model=model,
            as_of=as_of,
        )
        return await self._complete(prepared)

    async def _prepare(
        self,
        *,
        bean_id: int,
        machine_id: int,
        grinder_id: int | None = None,
        usual_grind: str = "",
        dose_hint_g: float | None = None,
        model: str | None = None,
        as_of: str = "",
    ) -> _Prepared:
        config = await self.llm.config()
        resolved = (model or "").strip() or config.resolve_model("starting_point")

        # Read here rather than inside `build_context`, so assembling a context
        # stays a pure function of the database it was handed — which is what
        # lets the golden test build one without a settings service.
        budget = int(await self.llm.settings.get("analysisChunkTokenBudget"))
        context = await build_context(
            self.db,
            bean_id=bean_id,
            machine_id=machine_id,
            grinder_id=grinder_id,
            usual_grind=usual_grind,
            dose_hint_g=dose_hint_g,
            as_of=as_of,
            chunk_token_budget=budget,
        )
        system, user, version = await self._render(context)

        run_id = await self.runs.start(
            StartingPointStart(
                bean_id=bean_id,
                machine_id=machine_id,
                grinder_id=grinder_id,
                usual_grind=usual_grind.strip(),
                dose_hint_g=dose_hint_g,
                provider=config.provider,
                model=resolved,
                prompt_name=STARTING_POINT_PROMPT,
                prompt_version=version,
                input=context.model_dump(mode="json"),
            )
        )
        self._publish("starting_point.started", run_id, bean_id, status="running")
        return _Prepared(
            run=_require(await self.runs.get(run_id), run_id),
            context=context,
            system=system,
            user=user,
            version=version,
            model=resolved,
        )

    async def _complete(self, prepared: _Prepared) -> StartingPointRunRow:
        """The provider call and the bookkeeping. Never raises for a failure."""
        run_id = prepared.run.id
        bean_id = prepared.run.bean_id
        context = prepared.context

        try:
            result = await self.llm.call_json(
                LlmRequest(
                    messages=[
                        LlmMessage(role="system", content=prepared.system),
                        LlmMessage(role="user", content=prepared.user),
                    ],
                    output_model=StartingPointResult,
                    model=prepared.model,
                    purpose="starting_point",
                    label="starting point",
                    subject=f"{context.bean.name} on {context.hardware.grinder_name}".strip(),
                    prompt_name=STARTING_POINT_PROMPT,
                    prompt_version=prepared.version,
                    retry_delay_s=self.retry_delay_s,
                )
            )
        except asyncio.CancelledError:
            # Not a provider failure, so not a `failed` row: it stays `running`
            # and boot reconciliation marks it `interrupted`, which is what
            # actually happened to it.
            self._publish("starting_point.failed", run_id, bean_id, status="running")
            raise
        except Exception as exc:
            await self.runs.finish(
                run_id, status="failed", error=f"unknown: {type(exc).__name__}: {exc}"
            )
            log.exception("starting_point_crashed", run_id=run_id, bean_id=bean_id)
            self._publish("starting_point.failed", run_id, bean_id, status="failed")
            raise

        if not isinstance(result, Ok):
            row = await self.runs.finish(
                run_id,
                status="failed",
                error=f"{result.code}: {result.message}",
                provider=result.provider,
                model=result.model,
                llm_call_id=result.call_id or None,
                usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
            )
            log.info("starting_point_failed", run_id=run_id, bean_id=bean_id, code=result.code)
            self._publish("starting_point.failed", run_id, bean_id, status="failed")
            return _require(row, run_id)

        output = _post_process(result.data, context)
        row = await self.runs.finish(
            run_id,
            status="ok",
            output=output.model_dump(mode="json"),
            usage=_usage(result.usage.prompt_tokens, result.usage.completion_tokens),
            provider=result.provider,
            model=result.model,
            llm_call_id=result.call_id or None,
        )
        log.info("starting_point_finished", run_id=run_id, bean_id=bean_id, model=result.model)
        self._publish("starting_point.finished", run_id, bean_id, status="ok")
        return _require(row, run_id)

    async def _render(self, context: StartingPointContext) -> tuple[str, str, str]:
        """The two prompts, rendered, plus the version string for the ledger."""
        variables = context.render()
        system = await self.prompts.load(STARTING_POINT_PROMPT)
        user = await self.prompts.load(STARTING_POINT_USER_PROMPT, variables)
        return system.system, user.user, f"{system.version}+{user.version}"

    # ── accepting ────────────────────────────────────────────────────

    async def accept(self, run_id: int, option_key: str) -> AcceptedStartingPoint:
        """Turn one option into a Set, its first version and — maybe — a draft.

        The order matters and is the reason this is not four independent
        writes. The draft is created **first**, because it is the step that can
        be refused: a profile document the safety policy will not allow is a 422
        naming every violation, and it has to come back before a Set exists. A
        Set pointing at a profile that was rejected is worse than no Set.
        """
        run = await self.runs.get(run_id)
        if run is None:
            raise NotFound(f"No starting point {run_id}")
        if run.status != "ok" or not run.output:
            raise Conflict(
                f"Starting point {run_id} is {run.status}; there is nothing to accept yet"
            )
        if run.accepted_option is not None:
            raise self._conflict(run)
        if option_key not in OPTION_KEYS:
            raise Unprocessable(
                "That is not one of the options",
                details={"field": "option", "message": f"expected one of {', '.join(OPTION_KEYS)}"},
            )

        result = StartingPointResult.model_validate(run.output)
        option = result.option(option_key)
        if option is None:  # pragma: no cover - the schema guarantees all three
            raise Unprocessable(
                "That option is not in this run",
                details={"field": "option", "message": f"{option_key} was not offered"},
            )

        # **Claim first, create second.** The read above is not a guard: two
        # requests can both see an unaccepted run and both get past it. The
        # guarded UPDATE is atomic, so exactly one of them proceeds to create
        # anything — and the loser is refused before it has made a second Set
        # for one bag, which is what happens when the guard is the last write
        # rather than the first.
        if not await self.runs.claim(run_id, option_key):
            return await self._already_accepted(run_id)

        try:
            draft = await self._draft_for(option)
            set_row, version = await self._create_set(run, option, draft)
        except BaseException:
            # The claim was a promise to create a Set, and this is how one that
            # could not be kept is withdrawn: a refused profile document must
            # leave the run acceptable again, or the other two options — which
            # may be perfectly fine — become unreachable for ever.
            await self.runs.release(run_id)
            raise

        await self.runs.finish_accept(
            run_id,
            set_id=set_row.id,
            set_version_id=version.id,
            draft_id=draft.id if draft else None,
        )

        log.info(
            "starting_point_accepted",
            run_id=run_id,
            option=option_key,
            set_id=set_row.id,
            draft_id=draft.id if draft else None,
        )
        return AcceptedStartingPoint(
            run=_require(await self.runs.get(run_id), run_id),
            set_row=set_row,
            version=version,
            draft=draft,
        )

    async def _already_accepted(self, run_id: int) -> AcceptedStartingPoint:
        """Raise the 409 for a run somebody else has taken.

        Never returns — the annotation is a lie the type checker accepts because
        the caller reads better as `return await self._already_accepted(...)`
        than as two lines. It re-reads the row rather than using the stale one,
        because the winner may still have been mid-flight when we read it and
        the ids are the useful half of this refusal.
        """
        run = await self.runs.get(run_id)
        if run is None:  # pragma: no cover - it existed a moment ago
            raise NotFound(f"No starting point {run_id}")
        raise self._conflict(run)

    @staticmethod
    def _conflict(run: StartingPointRunRow) -> Conflict:
        """ "Somebody already took this", carrying what they made.

        The ids matter more than the message: the UI uses them to take the
        person to the Set that exists rather than showing them an error about
        one they cannot see.
        """
        return Conflict(
            f"Starting point {run.id} was already accepted "
            f"({run.accepted_option}); it created Set {run.accepted_set_id}",
            details={
                "option": run.accepted_option,
                "set_id": run.accepted_set_id,
                "set_version_id": run.accepted_set_version_id,
                "draft_id": run.accepted_draft_id,
            },
        )

    async def _draft_for(self, option: StartingPointOption) -> ProfileDraftRow | None:
        """The draft this option's profile document becomes, if it carried one.

        Goes through ``create_manual`` rather than a private path, because that
        is the method that runs the schema check, the clamp and the policy —
        the same four layers a hand-typed profile gets, and that is not
        negotiable for a document a language model wrote.
        """
        if option.profile is None:
            return None
        if self.drafts is None:
            raise Unprocessable(
                "This option carries a new profile, and drafting is not available",
                details={
                    "field": "option",
                    "message": (
                        "the draft service is not wired up on this connection; pick an option "
                        "that names an existing profile instead"
                    ),
                },
            )
        base_version_id = await self._base_version_for(option)
        draft: ProfileDraftRow = await self.drafts.create_manual(
            base_version_id=base_version_id,
            # `to_device()`, not `model_dump()`: it re-expands the `^_`
            # annotation keys and drops the `annotations` field itself, which
            # `Profile`'s own validator refuses as an input key.
            document=option.profile.to_device(),
            change_summary=option.profile_note or option.headline,
            notes=f"Proposed by the starting-point wizard ({option.option}).",
        )
        return draft

    async def _base_version_for(self, option: StartingPointOption) -> int:
        """What the new profile is diffed against.

        A draft is always *derived from* a version, because the diff view is
        how somebody reads it before approving. There is nothing here it is
        genuinely derived from — this profile was authored, not edited — so the
        base is the closest thing available: the most-used profile in the
        library, which is what "what you brew now" means.

        When the library is empty the base is a synthetic minimal profile,
        stored as a version like any other. That is a real row rather than a
        special case in the diff view, and it costs one profile nobody selects.
        """
        from gaggiclanker.db.repos.profiles import ProfilesRepository
        from gaggiclanker.domain.models import Profile

        profiles = ProfilesRepository(self.db)
        best = await self.db.fetch_value(
            """
            SELECT pv.id FROM profile_versions pv
             WHERE pv.utility = 0
             ORDER BY (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) DESC,
                      pv.id DESC
             LIMIT 1
            """
        )
        if best is not None:
            return int(best)
        version, _ = await profiles.ensure_version(
            Profile.model_validate(
                {
                    "label": SYNTHETIC_BASE_LABEL,
                    "type": "pro",
                    "description": (
                        "An empty baseline, created because the archive held no profile to "
                        "diff a starting-point draft against."
                    ),
                    "temperature": 93.0,
                    "phases": [
                        {
                            "name": "Extraction",
                            "phase": "brew",
                            "valve": 1,
                            "duration": 30,
                            "pump": {"target": "pressure", "pressure": 9, "flow": 0},
                            "targets": [{"type": "volumetric", "operator": "gte", "value": 36}],
                        }
                    ],
                }
            ),
            source="draft",
        )
        return version.id

    async def _create_set(
        self,
        run: StartingPointRunRow,
        option: StartingPointOption,
        draft: ProfileDraftRow | None,
    ) -> tuple[SetRow, SetVersionRow]:
        """The Set and its version 1.

        The version points at the option's chosen profile when it named one, and
        at the **draft's** version when it authored one — the draft's document
        is what the Set means from now on, even though nobody has pushed it to
        the machine yet. `intent` carries the option's rationale, trimmed: it is
        the sentence the timeline shows against version 1 for ever, and "the
        wizard suggested it" would be a worse answer than the reasoning itself.
        """
        profile_version_id = option.profile_version_id
        if draft is not None:
            profile_version_id = draft.draft_version_id

        intent = _intent(run, option, draft)
        name = _set_name(run)
        stored = await self.sets.create(
            SetWrite(
                name=name,
                bean_id=run.bean_id,
                machine_id=run.machine_id,
                grinder_id=run.grinder_id,
            ),
            SetVersionWrite(
                profile_version_id=profile_version_id,
                grind_setting=option.grind_setting[:100],
                grind_value=_grind_value(option),
                dose_g=option.dose_g,
                target_yield_g=option.yield_g,
                target_temperature_c=option.temperature_c,
                intent=intent,
                origin="starting_point",
            ),
        )
        version_id = stored.current_version_id
        version = None if version_id is None else await self.sets.get_version(version_id)
        if version is None:  # pragma: no cover - create() writes version 1
            raise RuntimeError(f"Set {stored.id} was created without a version")
        return stored, version

    # ── reading ──────────────────────────────────────────────────────

    async def get(self, run_id: int) -> StartingPointRunRow:
        run = await self.runs.get(run_id)
        if run is None:
            raise NotFound(f"No starting point {run_id}")
        return run

    async def _latest_running(
        self, bean_id: int, machine_id: int, grinder_id: int | None
    ) -> StartingPointRunRow | None:
        for row in await self.runs.for_bean(bean_id, limit=5):
            if (
                row.status == "running"
                and row.machine_id == machine_id
                and row.grinder_id == grinder_id
            ):
                return row
        return None

    # ── events ───────────────────────────────────────────────────────

    def _publish(self, event: str, run_id: int, bean_id: int, *, status: str) -> None:
        """Best-effort fan-out. Observability must never fail what it observes."""
        if self.bus is None:
            return
        self.bus.publish(
            SseEvent(event=event, data={"run_id": run_id, "bean_id": bean_id, "status": status})
        )


def _require(row: StartingPointRunRow | None, run_id: int) -> StartingPointRunRow:
    if row is None:  # pragma: no cover - the insert above guarantees it
        raise RuntimeError(f"starting point {run_id} vanished between write and read")
    return row


def _usage(prompt_tokens: int | None, completion_tokens: int | None) -> dict[str, Any] | None:
    """``None`` when the provider reported nothing. See `llm/types.py::Usage`."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
    }


def _set_name(run: StartingPointRunRow) -> str:
    """ "Ethiopia Guji on the Niche Zero" — the bag and the grinder.

    The bag alone would collide the day somebody runs the same bean on a second
    grinder, which is exactly the comparison this feature invites.
    """
    bean = (run.bean_name or "New bean").strip()
    grinder = (run.grinder_name or "").strip()
    name = f"{bean} on the {grinder}" if grinder else bean
    return name[:200]


def _intent(
    run: StartingPointRunRow, option: StartingPointOption, draft: ProfileDraftRow | None
) -> str:
    """Version 1's stated intent: the option's own reasoning, plus its links.

    The draft id is in here as text as well as in
    `starting_point_runs.accepted_draft_id`, because the Set page renders the
    intent and the person reading it needs to know a profile is waiting for
    approval. The column is what code reads; this sentence is what a human does.
    """
    parts = [f"Starting point ({option.option}): {option.rationale}".strip()]
    if draft is not None:
        parts.append(f"Profile draft #{draft.id} is waiting for approval.")
    parts.append(f"From starting-point run #{run.id}.")
    return " ".join(parts)[:500]


def _grind_value(option: StartingPointOption) -> float | None:
    """The numeric half of the grind, when the setting really is a number.

    Only for an absolute setting: parsing "two clicks finer than usual" into 2
    and plotting it on the Set's grind chart would draw a line that means
    nothing. `grind_is_absolute` is the model's own claim and the text still has
    to parse, so both have to hold.

    **The first number wins**, which is a deliberate choice rather than an
    oversight. `set_versions` keeps the reading as text *and* as a number for
    exactly this reason (migration 0005): a Mazzer's "between 3 and 4" is what
    the user reads back, and 3 is what a chart plots. Taking the first number
    puts the point at the bottom of the stated range every time, which is at
    least consistent; averaging to 3.5 would invent a precision the dial does
    not have, and refusing to parse it at all would leave the Set's grind chart
    with a hole wherever somebody owns that grinder.
    """
    if not option.grind_is_absolute:
        return None
    text = option.grind_setting.strip().split()
    for token in text:
        try:
            value = float(token.replace(",", "."))
        except ValueError:
            continue
        return value if 0 <= value <= 10000 else None
    return None


def _post_process(
    result: StartingPointResult, context: StartingPointContext
) -> StartingPointResult:
    """Everything the schema cannot check, in one place.

    Four filters, all the same shape and all the analyzer's reasoning
    (`analyzer/service.py::_post_process`): a citation the run was not given is
    dropped and logged rather than failing the whole answer, because a
    fabricated citation is a small flaw in an otherwise useful suggestion and
    throwing three options away over it costs the user a call.

    The fourth is the one this feature adds: a `profile_version_id` naming a
    profile the model was not shown is **cleared**, not dropped-and-ignored. A
    Set version pointing at an arbitrary profile id would silently change which
    shots auto-assignment attaches to it, and the option is still perfectly
    usable without a profile — it just means "keep what is selected".
    """
    options: list[StartingPointOption] = []
    for option in result.options:
        profile_version_id = option.profile_version_id
        if profile_version_id is not None and profile_version_id not in context.profile_version_ids:
            log.warning(
                "starting_point_cited_unknown",
                kind="profile",
                bean_id=context.bean.bean_id,
                dropped=profile_version_id,
            )
            profile_version_id = None
        options.append(
            option.model_copy(
                update={
                    "profile_version_id": profile_version_id,
                    "rules_used": _cited(
                        option.rules_used, context.rule_keys, context.bean.bean_id, "rules"
                    ),
                    "excerpts_used": _cited(
                        option.excerpts_used,
                        context.excerpt_paths,
                        context.bean.bean_id,
                        "excerpts",
                    ),
                    "similar_set_version_ids": _cited(
                        option.similar_set_version_ids,
                        context.similar_version_ids,
                        context.bean.bean_id,
                        "similar_sets",
                    ),
                }
            )
        )
    return result.model_copy(update={"options": options})


def _cited[T](claimed: list[T], allowed: frozenset[T], bean_id: int, kind: str) -> list[T]:
    """The citations that were really on offer, deduplicated, order preserved."""
    dropped = [item for item in claimed if item not in allowed]
    if dropped:
        log.warning(
            "starting_point_cited_unknown",
            kind=kind,
            bean_id=bean_id,
            dropped=sorted(str(item) for item in dropped),
        )
    return list(dict.fromkeys(item for item in claimed if item in allowed))
