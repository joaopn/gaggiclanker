"""`knowledge_insights` — tier 3, what this archive has learned about this kitchen.

Rows rather than a file (gaggimate-mcp keeps the same thing as
`brewing-insights.md`) because an insight has to be *selected*: "this grinder
needs two clicks finer for anything anaerobic" belongs in front of a
conversation about an anaerobic Set on that grinder and nowhere else. The selection rule is one
sentence and it lives in :func:`scope_matches`:

    an insight applies when **every** key its scope states matches the Set.

So ``{}`` is a fact about the whole kitchen, ``{"grinder_id": 2}`` is about one
grinder, and ``{"grinder_id": 2, "process": "anaerobic"}`` is about the
combination and stays quiet for a washed bean on the same grinder. There is no
partial credit and no scoring: a rule that half-applies is a rule nobody can
predict, and the point of showing these to a model is that the user knows what
it was told.

**An insight is general or belongs to one Set.** A general insight
(``set_id`` NULL: hand-written, or an agent-written one nobody could place) keeps
the attribute matching above and reaches every conversation whose Set it
matches. An insight written in a Set's conversation carries that Set and the
version the conversation was about, has no attribute scope, and is given to that
Set's conversations only — whatever bean and grinder another Set shares with it.
One function, :meth:`InsightsRepository.for_set`, answers "what applies to this
Set" for the opening context, ``get_insights`` and the Set page, so the three
cannot disagree.

**Unconfirmed insights never reach a prompt.** They are proposals — by the chat
today, and by the per-shot analysis before it was retired — and a model that
generalises from one shot and is then believed by the next conversation has
manufactured its own evidence. :meth:`InsightsRepository.select` reads
``confirmed = 1``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from gaggiclanker.db.repos.base import JsonList, dumps, utc_now
from gaggiclanker.db.repos.beans import BeansRepository
from gaggiclanker.db.repos.version_names import label_sql
from gaggiclanker.db.repository import Repository

__all__ = [
    "SCOPE_KEYS",
    "InsightRow",
    "InsightScope",
    "InsightSource",
    "InsightWrite",
    "InsightsRepository",
    "scope_matches",
    "set_attributes",
]

#: The dimensions an insight may be scoped by. Closed, because the chat is
#: asked to propose scopes and a model inventing `bean_variety` would produce a
#: row that silently never matches anything.
SCOPE_KEYS = (
    "bean_id",
    "roast_level",
    "process",
    "origin",
    "grinder_id",
    "profile_style",
)

type InsightSource = Literal["analysis", "chat", "user"]


class InsightScope(BaseModel):
    """What an insight is about. Every field optional; an absent one means "any".

    ``extra="forbid"`` is load-bearing here rather than tidiness: this model
    validates a scope the *model* proposed, and a misspelled dimension has to be
    a validation failure the LLM layer can correct rather than a row that never
    fires.
    """

    model_config = ConfigDict(extra="forbid")

    bean_id: int | None = None
    roast_level: str | None = None
    process: str | None = None
    origin: str | None = None
    grinder_id: int | None = None
    profile_style: str | None = None

    def stated(self) -> dict[str, Any]:
        """Only the keys this scope actually names, in :data:`SCOPE_KEYS` order."""
        values = self.model_dump()
        return {key: values[key] for key in SCOPE_KEYS if values[key] is not None}

    def label(self) -> str:
        """A one-line badge: ``process=anaerobic · grinder=2``, or "any shot"."""
        stated = self.stated()
        if not stated:
            return "any shot"
        return " · ".join(f"{key}={value}" for key, value in stated.items())


def scope_matches(scope: dict[str, Any] | None, attributes: dict[str, Any]) -> bool:
    """Whether an insight's scope holds for a Set's attributes.

    All-keys-must-match, and an attribute the Set does not state never matches a
    scope that names it: an insight about natural processing must not be handed
    to a conversation about a bag whose roaster printed no process. Saying nothing is
    better than reasoning from a guess — the same rule
    :class:`~gaggiclanker.knowledge.rules.SetContext` documents for tier 1.

    Strings compare case-insensitively and trimmed, because `origin` is typed by
    hand into a bean form and "Ethiopia " is the same country as "ethiopia".
    """
    for key, wanted in (scope or {}).items():
        if key not in SCOPE_KEYS or wanted is None:
            # An unknown key cannot be satisfied, so an insight carrying one
            # never fires. That is the safe direction: the alternative is
            # ignoring it, which silently widens the scope the user confirmed.
            return False
        have = attributes.get(key)
        if have is None:
            return False
        if isinstance(wanted, str) or isinstance(have, str):
            if str(have).strip().lower() != str(wanted).strip().lower():
                return False
        elif have != wanted:
            return False
    return True


def set_attributes(
    *,
    bean_id: int | None = None,
    roast_level: str | None = None,
    process: str | None = None,
    origin: str | None = None,
    grinder_id: int | None = None,
    profile_style: str | None = None,
) -> dict[str, Any]:
    """A Set's attributes in the shape :func:`scope_matches` reads them.

    One place, because there are two callers that assemble it from different
    rows — the chat from the Set it is about, the API from a Set the page
    asked about — and a dimension added to :data:`SCOPE_KEYS` has to reach both
    or an insight silently stops matching on one of them.

    The normalisation is the part worth sharing: an empty string and the
    detected style ``"unknown"`` both mean *not stated*, and a scope that names
    an unstated dimension must not match (the same rule tier 1's `SetContext`
    documents). A blank `origin` reaching the comparison as `""` would match an
    insight scoped to `origin: ""`, which nothing should ever be.
    """
    values = {
        "bean_id": bean_id,
        "roast_level": roast_level,
        "process": process,
        "origin": origin,
        "grinder_id": grinder_id,
        "profile_style": None if profile_style == "unknown" else profile_style,
    }
    return {
        key: (None if isinstance(value, str) and not value.strip() else value)
        for key, value in values.items()
    }


class InsightWrite(BaseModel):
    """One insight on its way into the table."""

    model_config = ConfigDict(extra="forbid")

    scope: InsightScope = Field(default_factory=InsightScope)
    text: str = Field(min_length=1, max_length=2000)
    evidence_shot_ids: list[int] = Field(default_factory=list)
    source: InsightSource = "user"
    confirmed: bool = False
    #: The Set this insight belongs to, when it was learned in one. A Set's
    #: insight states no attribute scope: it is about this Set whatever its bean
    #: and grinder are shared with.
    set_id: int | None = None
    #: The version the conversation that wrote it was about.
    set_version_id: int | None = None

    @model_validator(mode="after")
    def _a_set_insight_has_no_scope(self) -> InsightWrite:
        if self.set_version_id is not None and self.set_id is None:
            raise ValueError("an insight learned at a version belongs to that version's Set")
        if self.set_id is not None and self.scope.stated():
            raise ValueError("an insight that belongs to a Set states no attribute scope")
        return self

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        """Collapsed whitespace. An insight is a sentence, not a document."""
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("an insight needs some text")
        return collapsed


class InsightRow(BaseModel):
    """One row of `knowledge_insights`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    scope: InsightScope = Field(default_factory=InsightScope)
    text: str
    evidence_shot_ids: JsonList = Field(default=None, validation_alias="evidence_shot_ids_json")
    source: str = "user"
    #: The retired per-shot analysis that proposed it, for an insight with
    #: `source = 'analysis'`; nothing writes it now. Its id is the carried
    #: review of the same shot when that analysis finished.
    analysis_id: int | None = None
    confirmed: bool = False
    created_at: str = ""
    updated_at: str = ""
    confirmed_at: str | None = None
    #: The Set it belongs to; NULL is a general insight.
    set_id: int | None = None
    #: The version it was learned at, and its name ("v3"). NULL for "learned
    #: before versions were recorded", and for a general insight.
    set_version_id: int | None = None
    set_version_label: str | None = None
    #: The person turned the card down. Kept for the conversation that proposed
    #: it, shown nowhere else, reaching no prompt.
    dismissed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def general(self) -> bool:
        """Whether it is general knowledge rather than one Set's."""
        return self.set_id is None

    @property
    def scope_label(self) -> str:
        if self.set_id is not None:
            return (
                f"this Set, learned at {self.set_version_label}"
                if self.set_version_label
                else "this Set"
            )
        return self.scope.label()

    def render(self) -> str:
        """The line a prompt carries: the scope, then the sentence."""
        return f"[{self.scope_label}] {self.text}"


_SELECT = f"""
    SELECT i.*, {label_sql("v")} AS set_version_label
      FROM knowledge_insights i
      LEFT JOIN set_versions v ON v.id = i.set_version_id
"""  # noqa: S608 - the only interpolation is the version label expression, a constant


class InsightsRepository(Repository):
    """Reads and writes the learned tier."""

    async def list_insights(
        self,
        *,
        confirmed: bool | None = None,
    ) -> list[InsightRow]:
        """The **general** insights, oldest first.

        One order for every reader, including the prompt. Oldest first because
        that is the order they were learned in, and a later insight that
        qualifies an earlier one only reads correctly after it. General only:
        a Set's own insights are :meth:`own`, and nothing reads both through
        one door by accident.
        """
        where = ["i.set_id IS NULL", "i.dismissed = 0"]
        params: list[Any] = []
        if confirmed is not None:
            where.append("i.confirmed = ?")
            params.append(int(confirmed))
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY i.created_at, i.id",
            params,
        )
        return [self._decode(row) for row in rows]

    async def own(self, set_id: int, *, include_dismissed: bool = False) -> list[InsightRow]:
        """What was learned **in** this Set, in any state, oldest first.

        Dismissed ones only when asked: the conversation that proposed one is
        told what happened to it, and nothing else reads them.
        """
        rows = await self.db.fetch_all(
            f"{_SELECT} WHERE i.set_id = ? "
            + ("" if include_dismissed else "AND i.dismissed = 0 ")
            + "ORDER BY i.created_at, i.id",
            (set_id,),
        )
        return [self._decode(row) for row in rows]

    async def select(self, attributes: dict[str, Any]) -> list[InsightRow]:
        """The confirmed **general** insights that apply to a Set's attributes.

        Filtered in Python, like tier 1's rule selection and for the same
        reason: the rule is "every stated key matches", the table is small, and
        a WHERE clause that could express it over a JSON column would be both
        unreadable and hard to prove deterministic. A Set's own insights are
        not here: they apply to their Set by ownership, not by attributes.
        """
        return [
            insight
            for insight in await self.list_insights(confirmed=True)
            if scope_matches(insight.scope.stated(), attributes)
        ]

    async def for_set(self, set_id: int) -> list[InsightRow]:
        """What applies to a Set: its own confirmed insights, then the general ones that match.

        **The one selection** for a Set's conversations (opening context and
        ``get_insights``) and for the Set page. Another Set's insights are never
        selected, whatever bean and grinder they share with this one; a
        dismissed insight is never selected; an unconfirmed one never reaches a
        prompt. Oldest first across both, so a later insight that qualifies an
        earlier one reads after it.
        """
        attributes = await self.attributes_of(set_id)
        if attributes is None:
            return []
        mine = [item for item in await self.own(set_id) if item.confirmed]
        general = await self.select(attributes)
        return sorted([*mine, *general], key=lambda item: (item.created_at, item.id))

    async def attributes_of(self, set_id: int) -> dict[str, Any] | None:
        """A Set's attributes in the shape :func:`scope_matches` reads, or ``None`` for no such Set.

        ``profile_style`` is left unstated on purpose: it is detected per
        *shot* from the profile the machine ran, so a Set has no single one, and
        an insight scoped by style belongs on the shot page.
        """
        row = await self.db.fetch_one(
            "SELECT bean_id, grinder_id FROM sets WHERE id = ?", (set_id,)
        )
        if row is None:
            return None
        bean = await BeansRepository(self.db).get(row["bean_id"]) if row["bean_id"] else None
        return set_attributes(
            bean_id=row["bean_id"],
            grinder_id=row["grinder_id"],
            roast_level=getattr(bean, "roast_level", None),
            process=getattr(bean, "process", None),
            origin=getattr(bean, "origin", None),
        )

    async def get(self, insight_id: int) -> InsightRow | None:
        row = await self.db.fetch_one(f"{_SELECT} WHERE i.id = ?", (insight_id,))
        return None if row is None else self._decode(row)

    async def insert(self, insight: InsightWrite) -> int:
        now = utc_now()
        cursor = await self.db.execute(
            """
            INSERT INTO knowledge_insights
                (scope_json, text, evidence_shot_ids_json, source,
                 confirmed, created_at, updated_at, confirmed_at, set_id, set_version_id)
            VALUES (:scope, :text, :evidence, :source,
                    :confirmed, :now, :now, :confirmed_at, :set_id, :set_version_id)
            """,
            {
                "set_id": insight.set_id,
                "set_version_id": insight.set_version_id,
                "scope": dumps(insight.scope.stated()),
                "text": insight.text,
                "evidence": dumps(sorted(set(insight.evidence_shot_ids))),
                "source": insight.source,
                "confirmed": int(insight.confirmed),
                "now": now,
                "confirmed_at": now if insight.confirmed else None,
            },
        )
        return int(cursor.lastrowid or 0)

    async def update(
        self,
        insight_id: int,
        *,
        text: str | None = None,
        scope: InsightScope | None = None,
        evidence_shot_ids: list[int] | None = None,
    ) -> bool:
        """Edit what an insight says or what it is about. Confirmation is separate."""
        assignments: list[str] = []
        params: dict[str, Any] = {"id": insight_id, "now": utc_now()}
        if text is not None:
            assignments.append("text = :text")
            params["text"] = " ".join(text.split())
        if scope is not None:
            assignments.append("scope_json = :scope")
            params["scope"] = dumps(scope.stated())
        if evidence_shot_ids is not None:
            assignments.append("evidence_shot_ids_json = :evidence")
            params["evidence"] = dumps(sorted(set(evidence_shot_ids)))
        if not assignments:
            return await self.get(insight_id) is not None
        cursor = await self.db.execute(
            f"UPDATE knowledge_insights SET {', '.join(assignments)}, updated_at = :now "  # noqa: S608 - assignments are literals, values are bound
            "WHERE id = :id",
            params,
        )
        return cursor.rowcount > 0

    async def set_confirmed(self, insight_id: int, confirmed: bool) -> bool:
        """Confirm or un-confirm. ``confirmed_at`` is cleared on the way back.

        Cleared rather than kept as "when it was last confirmed": the timestamp
        is shown beside the flag, and a date under an unconfirmed row reads as a
        contradiction.
        """
        now = utc_now()
        cursor = await self.db.execute(
            """
            UPDATE knowledge_insights
               SET confirmed = :confirmed,
                   confirmed_at = :confirmed_at,
                   dismissed = CASE WHEN :confirmed = 1 THEN 0 ELSE dismissed END,
                   updated_at = :now
             WHERE id = :id
            """,
            {
                "id": insight_id,
                "confirmed": int(confirmed),
                "confirmed_at": now if confirmed else None,
                "now": now,
            },
        )
        return cursor.rowcount > 0

    async def dismiss(self, insight_id: int) -> bool:
        """Turn a Set's insight down. Kept for the conversation that proposed it.

        Never confirmed at the same time: a dismissed insight reaches no prompt
        and is shown nowhere but in the chat that proposed it. Adding it later
        (:meth:`set_confirmed`) undoes the dismissal.
        """
        now = utc_now()
        cursor = await self.db.execute(
            """
            UPDATE knowledge_insights
               SET dismissed = 1, confirmed = 0, confirmed_at = NULL, updated_at = :now
             WHERE id = :id
            """,
            {"id": insight_id, "now": now},
        )
        return cursor.rowcount > 0

    async def delete(self, insight_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM knowledge_insights WHERE id = ?", (insight_id,))
        return cursor.rowcount > 0

    async def count(self, *, confirmed: bool | None = None) -> int:
        """How many general insights there are: the Knowledge page's own count."""
        if confirmed is None:
            return int(
                await self.db.fetch_value(
                    "SELECT COUNT(*) FROM knowledge_insights WHERE set_id IS NULL"
                )
                or 0
            )
        return int(
            await self.db.fetch_value(
                "SELECT COUNT(*) FROM knowledge_insights WHERE set_id IS NULL AND confirmed = ?",
                (int(confirmed),),
            )
            or 0
        )

    def _decode(self, row: Any) -> InsightRow:
        """One row to a model, with the scope parsed out of its JSON column.

        Parsed here rather than by a validator on the field because a stored
        scope that has grown a key this build does not know about must not take
        the whole row down: the unknown key is dropped on read (and
        :func:`scope_matches` would have refused it anyway), so a downgrade
        shows a narrower insight rather than a 500.
        """
        payload = dict(zip(row.keys(), tuple(row), strict=True))
        raw = payload.pop("scope_json", "{}")
        try:
            document = json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            document = {}
        payload["scope"] = InsightScope.model_validate(
            {key: value for key, value in document.items() if key in SCOPE_KEYS}
        )
        return InsightRow.model_validate(payload)
