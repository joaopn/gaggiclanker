"""``/api/prompts`` — reading, editing and resetting the prompt text.

The prompts are data (see :mod:`gaggiclanker.llm.prompts`), so this is a small
CRUD surface over one table with two rules worth stating:

* **A PUT is validated before it is stored.** YAML, the schema, and every
  fragment it references. A prompt that does not parse would otherwise fail at
  the next analysis, minutes later and nowhere near the editor.
* **There is no DELETE.** A prompt is created by shipping a file; deleting the
  row would only mean the next boot re-seeds it, and "reset" is what people
  actually want when they say delete.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, RootModel

from gaggiclanker.api.deps import PromptServiceDep
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import BadRequest, NotFound
from gaggiclanker.llm.prompts import PromptError, PromptService, seed_prompts

__all__ = ["router"]

router = APIRouter(prefix="/prompts", tags=["prompts"])


class PromptSummary(BaseModel):
    name: str
    description: str
    updated_at: str
    edited: bool
    fragment: bool
    valid: bool
    variables: list[dict[str, Any]]


class PromptListData(BaseModel):
    prompts: list[PromptSummary]


class PromptData(BaseModel):
    name: str
    content: str
    default_content: str
    edited: bool
    updated_at: str
    fragment: bool


class PromptPutBody(BaseModel):
    """The whole YAML document, as typed in the editor."""

    content: str


class ReloadData(RootModel[dict[str, int]]):
    """``{"changed": 2}`` — how many rows the re-seed touched."""


async def _guarded(service: PromptService, name: str, action: str) -> Any:
    """Run a prompt operation, mapping its one exception onto the envelope.

    ``PromptError`` covers both "no such prompt" and "that content is not
    valid", which are a 404 and a 400; the message is the only thing that tells
    them apart, so the split is made here rather than by giving the prompt layer
    two exception types that mean the same thing to it.
    """
    try:
        match action:
            case "get":
                return await service.get(name)
            case "reset":
                return await service.reset(name)
            case _:  # pragma: no cover - guarded by the callers below
                raise AssertionError(action)
    except PromptError as exc:
        raise NotFound(str(exc)) from None


@router.get("", response_model=ApiResponse[PromptListData], summary="Every prompt and fragment")
async def list_prompts(service: PromptServiceDep) -> JSONResponse:
    return envelope_response({"prompts": await service.list_prompts()})


@router.post(
    "/reload",
    response_model=ApiResponse[ReloadData],
    summary="Re-seed the table from the files on disk",
)
async def reload_prompts(request: Request) -> JSONResponse:
    """Applies the seeding rules again — new files, and defaults for edited rows.

    Registered before the ``{name}`` routes below, or ``reload`` would be read
    as the name of a prompt.
    """
    changed = await seed_prompts(PromptsRepository(request.app.state.db))
    return envelope_response({"changed": changed})


@router.get(
    "/{name:path}",
    response_model=ApiResponse[PromptData],
    summary="One prompt: its live text and the shipped default",
)
async def get_prompt(name: str, service: PromptServiceDep) -> JSONResponse:
    return envelope_response(await _guarded(service, name, "get"))


@router.put(
    "/{name:path}",
    response_model=ApiResponse[PromptData],
    summary="Replace a prompt's text",
)
async def put_prompt(name: str, body: PromptPutBody, service: PromptServiceDep) -> JSONResponse:
    try:
        return envelope_response(await service.save(name, body.content))
    except PromptError as exc:
        message = str(exc)
        if message.startswith("no prompt named"):
            raise NotFound(message) from None
        # The message names the line or the fragment at fault, which is what
        # the editor shows under the text area.
        raise BadRequest(message, details={"prompt": name}) from None


@router.post(
    "/{name:path}/reset",
    response_model=ApiResponse[PromptData],
    summary="Restore a prompt to the text that shipped",
)
async def reset_prompt(name: str, service: PromptServiceDep) -> JSONResponse:
    return envelope_response(await _guarded(service, name, "reset"))
