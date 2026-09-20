"""``/api/chat`` — threads, turns, and the stream one turn is watched on.

Shaped like the analyzer's routes and for the same reason: an answer takes tens
of seconds, so ``POST .../messages`` answers 202 with a ``running`` row and the
work happens in a registered background task. The browser follows
``GET /api/chat/runs/{id}/stream``, which **replays from the database first** and
then joins the live bus — so a tab opened halfway through an answer, or one that
lost its connection, sees the whole thing rather than the tail.

The rate limit is on starting a run, not on reading one. A chat turn spends
provider tokens per press of Send, and the failure this guards against is the
same as the analyzer's: a retry loop in a tab nobody is watching.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from gaggiclanker.api.deps import ChatRunnerDep, DatabaseDep
from gaggiclanker.chat.runner import CHAT_EVENT
from gaggiclanker.db.repos.chat import (
    ChatMessageRow,
    ChatRepository,
    ChatRunRow,
    ChatThreadRow,
    ChatThreadWrite,
    ThreadRefusal,
    ThreadWriteResult,
)
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import NotFound, Unprocessable
from gaggiclanker.infra.ratelimit import rate_limit
from gaggiclanker.infra.sse import SseEvent, SseEventBus, sse_response
from gaggiclanker.tools.registry import CHAT_PERMISSIONS, registry

__all__ = ["CHAT_RATE_LIMIT", "router"]

router = APIRouter(prefix="/chat", tags=["chat"])

#: Twenty turns a minute. A turn is a provider call and several tool calls, so
#: anything faster is a loop; the number is higher than the analyzer's because a
#: conversation is a person typing and a backfill is not.
CHAT_RATE_LIMIT = 20


class ThreadDetail(BaseModel):
    """One thread and everything said in it."""

    model_config = ConfigDict(extra="forbid")

    thread: ChatThreadRow
    messages: list[ChatMessageRow] = Field(default_factory=list)
    runs: list[ChatRunRow] = Field(default_factory=list)


class OpenBody(BaseModel):
    """`POST /api/chat/threads/open`: the version to carry on talking about.

    ``set_version_id`` omitted is the Set's current version, which is what a
    Discuss button on the Set itself means.
    """

    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_version_id: int | None = None


#: Each refusal the thread repository can answer with, and what it is called and
#: blamed on. All 422: every one of them is a body naming something that does
#: not resolve, and none of them is a state the caller could wait out.
_THREAD_REFUSALS: dict[ThreadRefusal, tuple[str, str]] = {
    "no_set": ("set_id", "that Set does not exist"),
    "no_version": ("set_version_id", "that is not a version of this Set"),
    "version_without_set": ("set_id", "name the Set the version belongs to"),
}


def _thread(result: ThreadWriteResult, *, status_code: int = 200) -> JSONResponse:
    """The created thread, or the one error its refusal means."""
    if result.thread is not None:
        return envelope_response(result.thread.model_dump(mode="json"), status_code=status_code)
    field, message = _THREAD_REFUSALS[result.refused or "no_set"]
    raise Unprocessable(
        "That conversation cannot be filed there",
        details={"field": field, "message": message},
    )


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8000)


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class SendResult(BaseModel):
    """What a press of Send produced: the run to follow, and the stored question."""

    model_config = ConfigDict(extra="forbid")

    run: ChatRunRow
    message: ChatMessageRow


class ToolInfo(BaseModel):
    """One tool, as the UI labels a trace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    permission: str
    description: str


class ToolList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[ToolInfo] = Field(default_factory=list)


@router.get(
    "/tools",
    response_model=ApiResponse[ToolList],
    summary="The tools the chat may call, with their permission class",
)
async def list_tools() -> JSONResponse:
    """Read-only, and the same list the runner sends the provider.

    The UI needs it to render a trace: a tool call arrives as a name, and a
    panel that said "propose_set_version" without saying that is a *proposal*
    would be hiding the one thing a reader has to know.
    """
    return envelope_response(
        ToolList(
            tools=[
                ToolInfo(name=spec.name, permission=spec.permission, description=spec.description)
                for spec in registry.specs(CHAT_PERMISSIONS)
            ]
        ).model_dump(mode="json")
    )


@router.get(
    "/threads",
    response_model=ApiResponse[list[ChatThreadRow]],
    summary="Conversations, most recently used first",
)
async def list_threads(
    db: DatabaseDep, limit: int = Query(default=50, ge=1, le=200)
) -> JSONResponse:
    rows = await ChatRepository(db).list_threads(limit=limit)
    return envelope_response([row.model_dump(mode="json") for row in rows])


@router.post(
    "/threads",
    response_model=ApiResponse[ChatThreadRow],
    status_code=201,
    summary="Start a conversation: general, or about one version of a Set",
)
async def create_thread(body: ChatThreadWrite, db: DatabaseDep) -> JSONResponse:
    """Always creates. New inside a folder is a fresh session on that Set.

    With a Set and no version it is filed under whatever is current, and it
    stays there: the conversation is the room one change was argued in, not a
    view of the Set that follows it around.
    """
    return _thread(await ChatRepository(db).create_thread(body), status_code=201)


@router.post(
    "/threads/open",
    response_model=ApiResponse[ChatThreadRow],
    summary="The conversation about a version, started if there is none",
)
async def open_thread(body: OpenBody, db: DatabaseDep) -> JSONResponse:
    """What Discuss and Review press: continue this version's chat.

    200 whether it existed or not, because the caller asked for the room rather
    than for a new one — which is also what makes a second press of Discuss
    harmless instead of a second empty conversation.
    """
    result = await ChatRepository(db).open_thread(body.set_id, body.set_version_id)
    return _thread(result)


@router.get(
    "/threads/{thread_id}",
    response_model=ApiResponse[ThreadDetail],
    summary="One conversation, with its transcript",
)
async def get_thread(thread_id: int, db: DatabaseDep) -> JSONResponse:
    repo = ChatRepository(db)
    thread = await repo.get_thread(thread_id)
    if thread is None:
        raise NotFound(f"No chat thread {thread_id}")
    messages = await repo.messages(thread_id)
    runs = [
        run
        for run in [await repo.get_run(message.run_id) for message in messages if message.run_id]
        if run is not None
    ]
    seen: dict[int, ChatRunRow] = {run.id: run for run in runs}
    return envelope_response(
        ThreadDetail(
            thread=thread,
            messages=messages,
            runs=sorted(seen.values(), key=lambda run: run.id),
        ).model_dump(mode="json")
    )


@router.patch(
    "/threads/{thread_id}",
    response_model=ApiResponse[ChatThreadRow],
    summary="Rename a conversation",
)
async def rename_thread(thread_id: int, body: RenameBody, db: DatabaseDep) -> JSONResponse:
    repo = ChatRepository(db)
    if not await repo.rename_thread(thread_id, body.title):
        raise NotFound(f"No chat thread {thread_id}")
    row = await repo.get_thread(thread_id)
    assert row is not None  # renamed above
    return envelope_response(row.model_dump(mode="json"))


@router.delete(
    "/threads/{thread_id}",
    response_model=ApiResponse[dict[str, bool]],
    summary="Delete a conversation and its transcript",
)
async def delete_thread(thread_id: int, db: DatabaseDep) -> JSONResponse:
    if not await ChatRepository(db).delete_thread(thread_id):
        raise NotFound(f"No chat thread {thread_id}")
    return envelope_response({"deleted": True})


@router.post(
    "/threads/{thread_id}/messages",
    response_model=ApiResponse[SendResult],
    status_code=202,
    summary="Ask something; the answer streams on the run",
    dependencies=[Depends(rate_limit("chat", CHAT_RATE_LIMIT))],
)
async def send(
    thread_id: int, body: SendBody, runner: ChatRunnerDep, request: Request
) -> JSONResponse:
    """202, not 200: the work is queued and the row is the handle.

    The same shape as `POST /api/shots/{id}/analyses`, and for the same reason —
    a request holding a two-minute provider call open is a request `docker stop`
    kills mid-flight, with the browser still waiting.
    """
    run, message = await runner.send(thread_id, body.message, tasks=request.app.state.tasks)
    return envelope_response(
        SendResult(run=run, message=message).model_dump(mode="json"), status_code=202
    )


@router.get(
    "/runs/{run_id}",
    response_model=ApiResponse[ChatRunRow],
    summary="One run: its status, usage and tool counts",
)
async def get_run(run_id: int, db: DatabaseDep) -> JSONResponse:
    run = await ChatRepository(db).get_run(run_id)
    if run is None:
        raise NotFound(f"No chat run {run_id}")
    return envelope_response(run.model_dump(mode="json"))


@router.post(
    "/runs/{run_id}/cancel",
    response_model=ApiResponse[ChatRunRow],
    summary="Stop a run that is still going",
)
async def cancel_run(run_id: int, runner: ChatRunnerDep) -> JSONResponse:
    return envelope_response((await runner.cancel(run_id)).model_dump(mode="json"))


@router.get(
    "/runs/{run_id}/stream",
    summary="Server-sent events for one run, replayed then live",
    response_class=EventSourceResponse,
)
async def stream_run(
    run_id: int,
    request: Request,
    runner: ChatRunnerDep,
    after: int = Query(default=0, ge=0, description="Resume after this sequence number."),
) -> EventSourceResponse:
    return sse_response(_run_stream(runner, request.app.state.events, run_id, after))


async def _run_stream(
    runner: Any, bus: SseEventBus, run_id: int, after: int
) -> AsyncIterator[SseEvent]:
    """Replay from the table, then follow the bus, skipping what was replayed.

    Subscribing *before* the replay would be the other obvious order and it is
    the wrong one: an event that arrives between the two is then delivered once
    from the bus and once from the replay. Replaying first and filtering on the
    sequence number gives exactly-once for a client that reconnects, which is
    the property the whole persisted-stream design exists for.

    The subscription is taken with ``bus.subscribe()`` rather than by iterating
    ``bus.stream()``, because this generator **returns early** — on the run's
    terminal event — and returning out of an ``async for`` leaves the inner
    generator suspended for the garbage collector to finalise on a loop that may
    no longer be running. The symptom is a leaked subscriber queue and a process
    that hangs in ``gc.collect()`` at exit. The ``with`` block releases the queue
    on every exit path, early return included.
    """
    highest = after
    with bus.subscribe() as queue:
        for event in await runner.replay(run_id, after):
            highest = max(highest, int(event.data.get("seq", 0)))
            yield event
        if not runner.is_running(run_id):
            # It finished while we were replaying. The terminal event is in the
            # table by now — the runner persists before it publishes — so read
            # the tail rather than returning without it, which would leave the
            # browser waiting for a `completed` that had already happened.
            for event in await runner.replay(run_id, highest):
                highest = max(highest, int(event.data.get("seq", 0)))
                yield event
            return
        while True:
            event = await queue.get()
            if event.event != CHAT_EVENT:
                continue
            data = event.data if isinstance(event.data, dict) else {}
            if int(data.get("run_id", 0)) != run_id:
                continue
            seq = int(data.get("seq", 0))
            if seq <= highest:
                continue
            highest = seq
            yield SseEvent(event=CHAT_EVENT, data=data, id=str(seq))
            if data.get("kind") in {"completed", "error", "cancelled"}:
                return
