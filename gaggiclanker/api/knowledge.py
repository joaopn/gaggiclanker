"""`/api/knowledge` — the rule tier, listed, toggled and edited.

A small CRUD surface over one table, with the same two rules the prompts router
has and for the same reasons: there is **no DELETE** (a rule is created by
shipping a file, so deleting the row only means the next boot re-seeds it, and
`enabled = false` is what people mean when they say delete), and a **reload**
endpoint re-runs the seeding so a maintainer editing `seed/rules.yaml` does not
have to restart the container to see it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, RootModel

from gaggiclanker.api.deps import RulesRepoDep
from gaggiclanker.db.repos.knowledge import RuleRow, RulesRepository
from gaggiclanker.domain.vocab import RULE_CATEGORIES
from gaggiclanker.infra.envelope import ApiResponse, envelope_response
from gaggiclanker.infra.errors import BadRequest, NotFound
from gaggiclanker.knowledge.rules import seed_rules

__all__ = ["router"]

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class RuleListData(BaseModel):
    """Every rule that matched the filter, in selection order."""

    model_config = ConfigDict(extra="forbid")

    items: list[RuleRow]
    #: The categories that exist, in the order the analyzer lists them, so the
    #: page can render its groups without inventing an order of its own.
    categories: list[str]


class RulePatch(BaseModel):
    """`PATCH /api/knowledge/rules/{id}`: turn one off, or change what it says.

    Both fields optional and both meaningful: a PATCH with only `enabled` does
    not touch the value, and one with only `value` does not re-enable a rule
    somebody switched off.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    #: The whole value document, including its `text`. Replaced, not merged: a
    #: merge cannot express "remove this key", and an editor that has the whole
    #: object in a text box is sending the whole object anyway.
    value: dict[str, Any] | None = None


class ReloadData(RootModel[dict[str, int]]):
    """``{"changed": 4}`` — how many rows the re-seed touched."""


@router.get(
    "/rules",
    response_model=ApiResponse[RuleListData],
    summary="The knowledge rules, by category",
)
async def list_rules(
    rules: RulesRepoDep,
    category: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    applies: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated `dimension:value` tokens — "
                "`roast_level:light,process:natural,style:bloom,signal:taste:sour`. "
                "Narrows to the rules a shot in that situation would be told."
            )
        ),
    ] = None,
) -> JSONResponse:
    """Every rule, or the ones a stated situation would actually select.

    `?applies=` answers "what would this bean be told", which is the question
    somebody editing a rule needs answered — reading it off a page of `applies`
    documents by eye is how a rule gets edited on a wrong assumption about when
    it fires.
    """
    if category is not None and category not in RULE_CATEGORIES:
        raise BadRequest(
            f"Unknown category {category!r}",
            details={"field": "category", "message": f"try one of {', '.join(RULE_CATEGORIES)}"},
        )
    tokens = [token.strip() for token in (applies or "").split(",") if token.strip()]
    if tokens and any(":" not in token for token in tokens):
        raise BadRequest(
            "Every `applies` token is `dimension:value`",
            details={
                "field": "applies",
                "message": "for example roast_level:light,style:bloom,signal:taste:sour",
            },
        )
    items = await rules.list_rules(category=category, enabled=enabled, applies=tokens or None)
    return envelope_response(
        RuleListData(items=items, categories=list(RULE_CATEGORIES)).model_dump(mode="json")
    )


@router.patch(
    "/rules/{rule_id}",
    response_model=ApiResponse[RuleRow],
    summary="Enable, disable or edit one rule",
)
async def patch_rule(rule_id: int, body: RulePatch, rules: RulesRepoDep) -> JSONResponse:
    """Apply whichever of the two fields was sent.

    An edited rule keeps its own text through every later re-seed; only its
    shipped default moves (`gaggiclanker/knowledge/rules.py`). That is what
    makes editing a rule safe to do — an upgrade cannot silently take it back.
    """
    if await rules.get(rule_id) is None:
        raise NotFound(f"No rule {rule_id}")
    if body.value is not None:
        await rules.set_value(rule_id, body.value)
    if body.enabled is not None:
        await rules.set_enabled(rule_id, body.enabled)
    stored = await rules.get(rule_id)
    if stored is None:  # pragma: no cover - checked above, same request
        raise NotFound(f"No rule {rule_id}")
    return envelope_response(stored.model_dump(mode="json"))


@router.post(
    "/rules/reload",
    response_model=ApiResponse[ReloadData],
    summary="Re-run the rule seeding from the shipped file",
)
async def reload_rules(request: Request) -> JSONResponse:
    """Re-seed without a restart. Edited rules keep their text.

    Built on the request's own database handle rather than the repository
    dependency because seeding is an app-level operation that happens to be
    reachable over HTTP — the same shape `POST /api/prompts/reload` has.
    """
    changed = await seed_rules(RulesRepository(request.app.state.db))
    return envelope_response(ReloadData({"changed": changed}).model_dump(mode="json"))
