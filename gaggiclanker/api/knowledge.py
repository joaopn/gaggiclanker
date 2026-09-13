"""`/api/knowledge` — all three tiers of the knowledge base.

Three groups of routes, one per tier, and they have deliberately different
shapes because the tiers are owned by different people:

* **rules** (`/rules`) are shipped in a file. There is **no DELETE** — deleting
  the row only means the next boot re-seeds it, and `enabled = false` is what
  people mean when they say delete — and a **reload** endpoint re-runs the
  seeding so a maintainer editing `seed/rules.yaml` does not have to restart the
  container to see it;
* **documents** (`/docs`, `/search`) are also shipped in files, so they have the
  same edit-and-reset contract and no delete either. What they add is a `PUT`
  that re-chunks, because a chunk is derived from the markdown and there is
  nothing to store between the two;
* **insights** (`/insights`) belong to this box alone. They are the one part of
  the knowledge base with a real DELETE, and the one part where confirming is a
  distinct verb from editing: nothing unconfirmed ever reaches a prompt.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, RootModel

from gaggiclanker.api.deps import (
    BeansRepoDep,
    InsightsRepoDep,
    KnowledgeDocsRepoDep,
    KnowledgeServiceDep,
    RulesRepoDep,
    SetsRepoDep,
)
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.knowledge import RuleRow, RulesRepository
from gaggiclanker.db.repos.knowledge_docs import ChunkHit, ChunkRow, DocRow
from gaggiclanker.db.repos.knowledge_insights import (
    SCOPE_KEYS,
    InsightRow,
    InsightScope,
    InsightWrite,
    set_attributes,
)
from gaggiclanker.db.repos.sets import SetsRepository
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


# ── tier 2: the documents, their chunks and the search over them ──────


class DocListData(BaseModel):
    """Every seeded document, slug order, without its markdown.

    Without, deliberately: the list is a directory and the bodies are 200 KB.
    The doc view fetches the one it is showing.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[DocRow]


class DocDetailData(BaseModel):
    """One document: its markdown, and the chunks the retriever actually sees."""

    model_config = ConfigDict(extra="forbid")

    doc: DocRow
    chunks: list[ChunkRow]


class DocEdit(BaseModel):
    """`PUT /api/knowledge/docs/{slug}`: replace the markdown, then re-chunk.

    The whole document, not a patch. Chunk boundaries are derived from the
    headings, so an edit that moves a heading moves every citation below it —
    there is no smaller unit of change here that means anything.
    """

    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(max_length=2_000_000)


class SearchData(BaseModel):
    """What a search found, best first."""

    model_config = ConfigDict(extra="forbid")

    query: str
    items: list[ChunkHit]


@router.get(
    "/docs",
    response_model=ApiResponse[DocListData],
    summary="The knowledge documents",
)
async def list_docs(docs: KnowledgeDocsRepoDep) -> JSONResponse:
    """Every document with its chunk count and what it would cost a prompt."""
    items = await docs.list_docs()
    return envelope_response(DocListData(items=items).model_dump(mode="json"))


@router.get(
    "/docs/{slug}",
    response_model=ApiResponse[DocDetailData],
    summary="One document and its chunks",
)
async def get_doc(slug: str, docs: KnowledgeDocsRepoDep) -> JSONResponse:
    """The markdown plus the chunks, so the page can anchor a citation.

    Both, because they answer different questions: the markdown is what an
    editor loads, and the chunks are what retrieval actually sees. A page that
    showed only the first would leave "why did my edit split that section in
    two" unanswerable.
    """
    doc = await docs.get_doc(slug)
    if doc is None:
        raise NotFound(f"No knowledge document {slug!r}")
    chunks = await docs.chunks_for_doc(doc.id)
    return envelope_response(DocDetailData(doc=doc, chunks=chunks).model_dump(mode="json"))


@router.put(
    "/docs/{slug}",
    response_model=ApiResponse[DocDetailData],
    summary="Replace a document's markdown and re-chunk it",
)
async def put_doc(slug: str, body: DocEdit, knowledge: KnowledgeServiceDep) -> JSONResponse:
    """Store the edit, re-chunk, and answer with what retrieval will now see.

    An edited document keeps the user's text through every later re-seed; only
    its shipped default moves. That is what makes editing safe to do — the same
    contract a rule and a prompt have.
    """
    doc = await knowledge.set_doc_markdown(slug, body.markdown)
    if doc is None:
        raise NotFound(f"No knowledge document {slug!r}")
    chunks = await knowledge.docs.chunks_for_doc(doc.id)
    return envelope_response(DocDetailData(doc=doc, chunks=chunks).model_dump(mode="json"))


@router.post(
    "/docs/{slug}/reset",
    response_model=ApiResponse[DocDetailData],
    summary="Put the shipped text of a document back",
)
async def reset_doc(slug: str, knowledge: KnowledgeServiceDep) -> JSONResponse:
    """Restore the default the package shipped, and re-chunk.

    The default converges on the *current* wording rather than the version the
    user forked from: seeding records the new default on an edited row even
    while leaving its text alone, so a reset after an upgrade gives the upgrade's
    text.
    """
    doc = await knowledge.reset_doc(slug)
    if doc is None:
        raise NotFound(f"No knowledge document {slug!r}")
    chunks = await knowledge.docs.chunks_for_doc(doc.id)
    return envelope_response(DocDetailData(doc=doc, chunks=chunks).model_dump(mode="json"))


@router.get(
    "/search",
    response_model=ApiResponse[SearchData],
    summary="Search the knowledge documents",
)
async def search(
    knowledge: KnowledgeServiceDep,
    q: Annotated[str, Query(min_length=1, max_length=500, description="Words to look for")],
    k: Annotated[int, Query(ge=1, le=50, description="How many chunks to return")] = 8,
) -> JSONResponse:
    """BM25 over the chunk index, heading matches weighted above body matches.

    The query is words, not FTS5 syntax: everything that is not a word or a
    digit is dropped, so a search can never be a syntax error
    (`db/repos/knowledge_docs.py::match_expression`).
    """
    items = await knowledge.search_chunks(q, k=k)
    return envelope_response(SearchData(query=q, items=items).model_dump(mode="json"))


# ── tier 3: the learned insights ──────────────────────────────────────


class InsightListData(BaseModel):
    """The insights that matched the filter, oldest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[InsightRow]
    #: The dimensions a scope may name, so the form renders from data rather
    #: than from a list typed into a component.
    scope_keys: list[str]


class InsightCreate(BaseModel):
    """`POST /api/knowledge/insights`: write one by hand.

    Source is fixed to `user` rather than taken from the body: the column says
    *who* learned this, and a client asserting "an analysis said so" would make
    the one thing this row is for — knowing whether a model or a person is
    behind it — unreliable.
    """

    model_config = ConfigDict(extra="forbid")

    scope: InsightScope = Field(default_factory=InsightScope)
    text: str = Field(min_length=1, max_length=2000)
    evidence_shot_ids: list[int] = Field(default_factory=list)
    #: A hand-written insight is confirmed by default: the person writing it is
    #: the person who would confirm it, and a form that makes you save and then
    #: press confirm is a form with a bug in it.
    confirmed: bool = True


class InsightPatch(BaseModel):
    """`PATCH /api/knowledge/insights/{id}`: edit it, or change its confirmation.

    Every field optional and each independent: a PATCH with only `confirmed`
    does not touch the text, and one with only `text` does not confirm a
    proposal somebody has not read yet.
    """

    model_config = ConfigDict(extra="forbid")

    scope: InsightScope | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    evidence_shot_ids: list[int] | None = None
    confirmed: bool | None = None


@router.get(
    "/insights",
    response_model=ApiResponse[InsightListData],
    summary="What this archive has learned",
)
async def list_insights(
    insights: InsightsRepoDep,
    sets: SetsRepoDep,
    beans: BeansRepoDep,
    confirmed: Annotated[bool | None, Query()] = None,
    analysis_id: Annotated[int | None, Query()] = None,
    set_id: Annotated[
        int | None,
        Query(
            description=(
                "Only the **confirmed** insights that apply to this Set — the same "
                "selection an analysis of one of its shots is given."
            )
        ),
    ] = None,
) -> JSONResponse:
    """Every insight, or the ones a filter narrows to.

    `?set_id=` answers the Set page's question — "what has this archive learned
    that applies here" — through the same `select_insights` an analysis uses, so
    the page cannot show a different answer from the prompt. It matches on the
    Set's own attributes only: `profile_style` is detected *per shot* from the
    profile the machine ran, so a Set has no single one and an insight scoped by
    style is left to the shot page.
    """
    if set_id is not None:
        items = await insights.select(await _set_attributes(sets, beans, set_id))
    else:
        items = await insights.list_insights(confirmed=confirmed, analysis_id=analysis_id)
    return envelope_response(
        InsightListData(items=items, scope_keys=list(SCOPE_KEYS)).model_dump(mode="json")
    )


async def _set_attributes(
    sets: SetsRepository, beans: BeansRepository, set_id: int
) -> dict[str, Any]:
    """One Set, in the flat shape :func:`scope_matches` reads.

    ``None`` for anything the Set does not state, which is what stops an insight
    about naturals reaching a bag whose roaster printed no process.
    """
    row = await sets.get(set_id)
    if row is None:
        raise NotFound(f"No Set {set_id}")
    bean = await beans.get(row.bean_id)
    # `profile_style` is left unstated on purpose: it is detected per *shot*
    # from the profile the machine ran, so a Set has no single one, and an
    # insight scoped by style belongs on the shot page.
    return set_attributes(
        bean_id=row.bean_id,
        roast_level=bean.roast_level if bean else None,
        process=bean.process if bean else None,
        origin=bean.origin if bean else None,
        grinder_id=row.grinder_id,
    )


@router.post(
    "/insights",
    response_model=ApiResponse[InsightRow],
    status_code=201,
    summary="Write an insight by hand",
)
async def create_insight(body: InsightCreate, insights: InsightsRepoDep) -> JSONResponse:
    insight_id = await insights.insert(
        InsightWrite(
            scope=body.scope,
            text=body.text,
            evidence_shot_ids=body.evidence_shot_ids,
            source="user",
            confirmed=body.confirmed,
        )
    )
    stored = await insights.get(insight_id)
    if stored is None:  # pragma: no cover - inserted in this request
        raise NotFound(f"No insight {insight_id}")
    return envelope_response(stored.model_dump(mode="json"), status_code=201)


@router.patch(
    "/insights/{insight_id}",
    response_model=ApiResponse[InsightRow],
    summary="Edit an insight, or confirm it",
)
async def patch_insight(
    insight_id: int, body: InsightPatch, insights: InsightsRepoDep
) -> JSONResponse:
    """Apply whichever fields were sent.

    Confirming is what puts an insight in front of the next analysis of a
    matching Set; un-confirming takes it out again with nothing else to
    remember, the way disabling a rule does.
    """
    if await insights.get(insight_id) is None:
        raise NotFound(f"No insight {insight_id}")
    await insights.update(
        insight_id,
        text=body.text,
        scope=body.scope,
        evidence_shot_ids=body.evidence_shot_ids,
    )
    if body.confirmed is not None:
        await insights.set_confirmed(insight_id, body.confirmed)
    stored = await insights.get(insight_id)
    if stored is None:  # pragma: no cover - checked above, same request
        raise NotFound(f"No insight {insight_id}")
    return envelope_response(stored.model_dump(mode="json"))


@router.delete(
    "/insights/{insight_id}",
    response_model=ApiResponse[dict[str, bool]],
    summary="Throw an insight away",
)
async def delete_insight(insight_id: int, insights: InsightsRepoDep) -> JSONResponse:
    """A real delete, unlike a rule's.

    A rule is shipped in a file, so deleting the row only means the next boot
    re-seeds it and `enabled = false` is what "delete" has to mean there. An
    insight is *this box's*: nothing re-creates it, and a proposal the user
    rejects should leave no trace to read past.
    """
    if not await insights.delete(insight_id):
        raise NotFound(f"No insight {insight_id}")
    return envelope_response({"deleted": True})
