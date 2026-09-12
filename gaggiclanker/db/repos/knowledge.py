"""`knowledge_rules` — the structured dial-in facts, as rows.

Tier 1 of the knowledge base: small,
machine-readable rules with the conditions they apply under, shared by the
analyzer, the Knowledge page and — later — the chat.

The seeding rules are `prompts`' rules, for the same reason: a shipped file is
the default, the row is the live copy, and an upgrade must reach every rule
nobody has touched without clobbering the ones they have. See
:func:`gaggiclanker.knowledge.rules.seed_rules`.

Selection happens in Python, not SQL. The table is a few hundred rows, the
conditions are a JSON document with per-category dimensions, and a WHERE clause
that could express them would be unreadable and — the part that matters — hard
to prove deterministic. The acceptance criterion for the analyzer is that two analyses of
the same shot select the *same* rules in the *same* order, and that is a sort
over a list.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.vocab import RULE_CATEGORIES, RuleConfidence

__all__ = ["RuleRow", "RuleWrite", "RulesRepository"]


class RuleWrite(BaseModel):
    """One rule as the seed file spells it, on its way into the table."""

    model_config = ConfigDict(extra="forbid")

    category: str
    key: str = Field(min_length=1, max_length=200)
    #: `{roast_level: [...], process: [...], style: [...], burr_type: [...],
    #: decaf: bool, signal: [...]}`. An absent dimension does not care.
    applies: dict[str, Any] = Field(default_factory=dict)
    #: The fact. `text` is the reserved one-sentence human form.
    value: dict[str, Any]
    unit: str = ""
    confidence: RuleConfidence = "expert"
    source: str = ""
    source_ref: str = ""


class RuleRow(BaseModel):
    """One row of `knowledge_rules`, as read back."""

    model_config = ConfigDict(extra="forbid")

    id: int
    category: str
    key: str
    applies: JsonObject = Field(default=None, validation_alias="applies_json")
    value: JsonObject = Field(default=None, validation_alias="value_json")
    unit: str = ""
    confidence: str = "expert"
    source: str = ""
    source_ref: str = ""
    enabled: bool = True
    updated_at: str = ""
    #: Whether the live value differs from what was shipped. Filled in by the
    #: repository from `default_json != value_json` rather than stored, because
    #: it is a comparison and a stored flag can disagree with the two values it
    #: summarises. `default_json` itself is not on this model: it is a second
    #: copy of the value that no reader wants and every serialiser would send.
    edited: bool = False

    @property
    def text(self) -> str:
        """The rule's one sentence, or a rendered fallback.

        A rule whose value has no `text` still has to read as something in a
        prompt, so the machine-readable keys are rendered instead. That path
        exists for a rule somebody wrote through the API; every shipped rule
        carries prose.
        """
        value = self.value or {}
        sentence = value.get("text")
        if isinstance(sentence, str) and sentence.strip():
            return " ".join(sentence.split())
        rest = {k: v for k, v in value.items() if k != "text"}
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(rest.items()))
        unit = f" {self.unit}" if self.unit and self.unit != "none" else ""
        return f"{self.key}: {rendered}{unit}" if rendered else self.key

    def render(self) -> str:
        """The line the prompt carries: the key, then the sentence."""
        return f"[{self.key}] {self.text}"


#: Where a category sorts. Selection order is (category index, key), which is
#: what makes "the same shot selects the same rules in the same order" a
#: property rather than an accident of dict iteration.
_CATEGORY_ORDER: dict[str, int] = {name: index for index, name in enumerate(RULE_CATEGORIES)}


def category_rank(category: str) -> int:
    """The sort rank of a category; unknown ones sort last, alphabetically after."""
    return _CATEGORY_ORDER.get(category, len(_CATEGORY_ORDER))


class RulesRepository(Repository):
    """Reads and writes the knowledge rules."""

    async def list_rules(
        self,
        *,
        category: str | None = None,
        enabled: bool | None = None,
        applies: list[str] | None = None,
    ) -> list[RuleRow]:
        """Every rule, in selection order.

        The same order as :func:`~gaggiclanker.knowledge.rules.select_rules`
        produces, so the Knowledge page lists rules the way a prompt lists them.

        ``applies`` narrows to the rules a given situation would select: each
        token is ``dimension:value`` (``roast_level:light``, ``style:bloom``,
        ``signal:taste:sour``) and a rule matches when every dimension it
        *states* is satisfied by one of them. That is the same question
        `select_rules` answers, which is why it is worth having here — "what
        would this bean actually be told" is the thing somebody editing a rule
        wants to check, and reconstructing it by eye from a page of `applies`
        documents is how a rule is edited on a wrong assumption.
        """
        where: list[str] = ["1 = 1"]
        params: list[Any] = []
        if category is not None:
            where.append("category = ?")
            params.append(category)
        if enabled is not None:
            where.append("enabled = ?")
            params.append(int(enabled))
        rows = await self.db.fetch_all(
            f"SELECT * FROM knowledge_rules WHERE {' AND '.join(where)}",  # noqa: S608 - clauses are literals, values are bound
            params,
        )
        decoded = self._decode(rows)
        if applies:
            decoded = [rule for rule in decoded if _would_select(rule, applies)]
        return _sorted(decoded)

    async def get(self, rule_id: int) -> RuleRow | None:
        row = await self.db.fetch_one("SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,))
        if row is None:
            return None
        return self._decode([row])[0]

    async def get_by_key(self, category: str, key: str) -> RuleRow | None:
        row = await self.db.fetch_one(
            "SELECT * FROM knowledge_rules WHERE category = ? AND key = ?", (category, key)
        )
        if row is None:
            return None
        return self._decode([row])[0]

    def _decode(self, rows: Any) -> list[RuleRow]:
        """Rows to models, with `edited` computed from the shipped copy."""
        out: list[RuleRow] = []
        for row in rows:
            payload = dict(zip(row.keys(), tuple(row), strict=True))
            default = payload.pop("default_json", "{}")
            payload["edited"] = str(default) != str(payload.get("value_json", ""))
            out.append(RuleRow.model_validate(payload))
        return out

    async def insert(self, rule: RuleWrite) -> int:
        """A rule the seed has never shipped before. Live and default identical."""
        payload = _values(rule)
        payload["default_json"] = payload["value_json"]
        payload["enabled"] = 1
        payload["updated_at"] = utc_now()
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        cursor = await self.db.execute(
            f"INSERT INTO knowledge_rules ({columns}) VALUES ({placeholders})",  # noqa: S608 - keys are the literal payload above
            payload,
        )
        return int(cursor.lastrowid or 0)

    async def refresh_both(self, rule: RuleWrite) -> None:
        """The file changed and nobody had edited the row: take the new fact."""
        payload = _values(rule)
        payload["default_json"] = payload["value_json"]
        payload["updated_at"] = utc_now()
        assignments = ", ".join(
            f"{name} = :{name}" for name in payload if name not in ("category", "key")
        )
        await self.db.execute(
            f"UPDATE knowledge_rules SET {assignments} WHERE category = :category AND key = :key",  # noqa: S608 - names are the literal payload above
            payload,
        )

    async def refresh_default(self, rule: RuleWrite) -> None:
        """The file changed but the row is edited: record the new default only.

        The user's value stands. Provenance — source, confidence, the reference —
        still moves, because those describe where the *rule* came from rather
        than what it now says, and leaving them stale would attribute an edited
        rule to a citation that no longer matches it.
        """
        payload = _values(rule)
        await self.db.execute(
            """
            UPDATE knowledge_rules
               SET default_json = :value_json,
                   confidence = :confidence,
                   source = :source,
                   source_ref = :source_ref
             WHERE category = :category AND key = :key
            """,
            payload,
        )

    async def set_enabled(self, rule_id: int, enabled: bool) -> bool:
        cursor = await self.db.execute(
            "UPDATE knowledge_rules SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), utc_now(), rule_id),
        )
        return cursor.rowcount > 0

    async def set_value(self, rule_id: int, value: dict[str, Any]) -> bool:
        cursor = await self.db.execute(
            "UPDATE knowledge_rules SET value_json = ?, updated_at = ? WHERE id = ?",
            (dumps(value), utc_now(), rule_id),
        )
        return cursor.rowcount > 0

    async def count(self) -> int:
        return int(await self.db.fetch_value("SELECT COUNT(*) FROM knowledge_rules") or 0)


def _values(rule: RuleWrite) -> dict[str, Any]:
    return {
        "category": rule.category,
        "key": rule.key,
        "applies_json": dumps(rule.applies),
        "value_json": dumps(rule.value),
        "unit": rule.unit,
        "confidence": rule.confidence,
        "source": rule.source,
        "source_ref": rule.source_ref,
    }


def _would_select(rule: RuleRow, tokens: list[str]) -> bool:
    """Whether ``tokens`` satisfy every dimension this rule states.

    A deliberately simple mirror of
    :func:`gaggiclanker.knowledge.rules._matches`: that one reads a typed
    context an analysis has assembled, this one reads strings off a query
    string. Keeping them apart means the query cannot make the analyzer's
    selection wrong; keeping them the same shape means the answers agree.
    """
    given: dict[str, set[str]] = {}
    for token in tokens:
        dimension, _, value = token.partition(":")
        given.setdefault(dimension, set()).add(value)

    for dimension, allowed in (rule.applies or {}).items():
        if isinstance(allowed, bool):
            if str(allowed).lower() not in {v.lower() for v in given.get(dimension, set())}:
                return False
            continue
        if not isinstance(allowed, list):
            continue
        if not set(allowed) & given.get(dimension, set()):
            return False
    return True


def _sorted(rules: list[RuleRow]) -> list[RuleRow]:
    """(category rank, category, key) — total, and stable across processes."""
    return sorted(rules, key=lambda rule: (category_rank(rule.category), rule.category, rule.key))
