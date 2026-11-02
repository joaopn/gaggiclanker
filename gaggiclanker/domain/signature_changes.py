"""A change to a signature that an agent asks for and a person answers.

Pure: the shapes a request stores (written through these models, read back through them) and
the one rule about them that is not a lookup, whether a change **loosens** a check.

A change names a row in force and says what should become of it:

* ``replace``: take the row out and put a new one in force in its place;
* ``retire``: take the row out;
* ``tier``: move the row to another tier;
* ``put_back``: put back a row the person rejected;
* ``put_back_override``: put back an override the person rejected (a Recipe version's limit).

Each change carries a snapshot of what it was about when it was asked (the row's sentence, tier
and phase, the person's rejection reason), so a card read after the answer still says what it
was a question about, even though the answer changed the row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.domain.metric_language import Compare, Expression, canonical_form
from gaggiclanker.domain.signature import ExpectationLike, sentence_with_unit

__all__ = [
    "CHANGE_REASON_MIN",
    "MAX_CHANGES",
    "PutBackChange",
    "PutBackOverrideChange",
    "ReplaceChange",
    "RetireChange",
    "RowKey",
    "StoredChange",
    "StoredChanges",
    "StoredReplacement",
    "TierChange",
    "change_loosens",
    "compare_loosens",
    "compare_sides",
    "replacement_words",
    "request_loosens",
    "revision_words",
    "rows_named",
]

#: A change's own reason, like the reason of any proposal, is a sentence.
CHANGE_REASON_MIN = 20
MAX_CHANGES = 10

type ChangeTier = Literal["critical", "important", "context"]
_TIER_RANK = {"critical": 0, "important": 1, "context": 2}

_STRICT = ConfigDict(extra="forbid")


class StoredReplacement(BaseModel):
    """The new row of a replacement, already validated against the profile's phases."""

    model_config = _STRICT

    tier: ChangeTier
    kind: Literal["measure", "reached", "expects_warning", "free_text"]
    phase: str | None = None
    expression: Expression | None = None
    warning_fault: str | None = None
    text: str = ""
    fault: str | None = None
    sentence: str


class _Change(BaseModel):
    model_config = _STRICT

    reason: str = Field(min_length=1, max_length=500)
    #: What it was about, when it was asked.
    phase: str | None = None
    old_sentence: str = ""
    old_tier: ChangeTier | None = None
    #: The limit of the row's measure as the Profiles page words it ("at least 93 °C").
    old_limit_text: str = ""


class ReplaceChange(_Change):
    kind: Literal["replace"] = "replace"
    expectation_id: int
    expectation: StoredReplacement


class RetireChange(_Change):
    kind: Literal["retire"] = "retire"
    expectation_id: int


class TierChange(_Change):
    kind: Literal["tier"] = "tier"
    expectation_id: int
    tier: ChangeTier


class PutBackChange(_Change):
    kind: Literal["put_back"] = "put_back"
    expectation_id: int
    #: The person's rejection, as it was when asked.
    rejected_reason: str = ""
    rejected_at: str | None = None


class PutBackOverrideChange(_Change):
    kind: Literal["put_back_override"] = "put_back_override"
    override_id: int
    #: The Recipe version the override belongs to: the conversation's own.
    recipe_version_id: int
    #: The expectation the override is a limit for, as it was when asked. Putting the override
    #: back needs that expectation in force, so the request names it too (see ``rows_named``).
    expectation_id: int | None = None
    #: The limit the override states, in words.
    new_sentence: str = ""
    rejected_reason: str = ""
    rejected_at: str | None = None


type StoredChange = Annotated[
    ReplaceChange | RetireChange | TierChange | PutBackChange | PutBackOverrideChange,
    Field(discriminator="kind"),
]


class StoredChanges(BaseModel):
    """What the ``changes_json`` column holds."""

    model_config = _STRICT

    changes: list[StoredChange] = Field(min_length=1, max_length=MAX_CHANGES)

    def expectation_ids(self) -> list[int]:
        """The expectations the request names, the one behind a put-back override included."""
        return [n for c in self.changes for kind, n in rows_named(c) if kind == "e"]

    def override_ids(self) -> list[int]:
        return [n for c in self.changes for kind, n in rows_named(c) if kind == "o"]


type RowKey = tuple[Literal["e", "o"], int]


def rows_named(change: StoredChange) -> list[RowKey]:
    """The rows a change is about: ``("e", id)`` for an expectation, ``("o", id)`` for an override.

    **The one definition of "the rows a request names"**: the guard's "two changes name one row",
    a newer request staling an older one, and every hook that stales a waiting request when a
    person or a tool changes a row all read it. An override is about itself *and* about its
    expectation, because the override can only come back while that expectation is in force.
    """
    if isinstance(change, PutBackOverrideChange):
        keys: list[RowKey] = [("o", change.override_id)]
        if change.expectation_id is not None:
            keys.append(("e", change.expectation_id))
        return keys
    return [("e", change.expectation_id)]


# ── loosening ────────────────────────────────────────────────────────


def compare_loosens(old: Compare, new: Compare) -> bool:
    """Whether holding a value to ``new`` lets in something ``old`` refused.

    ``>=`` and ``>`` loosen by a lower bound, ``<=`` and ``<`` by a higher one, ``between`` by
    widening on either side. On the same number the inclusive form is the looser one (``>`` to
    ``>=``). A limit that changes direction or shape (a floor into a ceiling, a bound into a
    range) cannot be called tighter, so it counts as loosening.
    """
    if old.op in (">", ">=") and new.op in (">", ">="):
        assert old.value is not None and new.value is not None
        if new.value != old.value:
            return new.value < old.value
        return old.op == ">" and new.op == ">="
    if old.op in ("<", "<=") and new.op in ("<", "<="):
        assert old.value is not None and new.value is not None
        if new.value != old.value:
            return new.value > old.value
        return old.op == "<" and new.op == "<="
    if old.op == "between" and new.op == "between":
        assert old.low is not None and old.high is not None
        assert new.low is not None and new.high is not None
        return new.low < old.low or new.high > old.high
    return True


def compare_sides(compare: Compare | None) -> frozenset[str]:
    """Which side(s) of the value a limit bounds: ``lower`` for ``>=`` and ``>`` (and a
    ``between``'s low end), ``upper`` for ``<=`` and ``<`` (and its high end)."""
    if compare is None:
        return frozenset()
    if compare.op in (">", ">="):
        return frozenset({"lower"})
    if compare.op in ("<", "<="):
        return frozenset({"upper"})
    return frozenset({"lower", "upper"})


def _without_compare(expression: Expression) -> str:
    return canonical_form(expression.model_copy(update={"compare": None}))


def change_loosens(change: StoredChange, old: ExpectationLike | None) -> bool:
    """Whether one change loosens a check: a retirement, a lower tier, a replacement with the
    same expression and a looser compare, and any replacement whose expression differs other
    than in its compare. Putting back, and tightening, do not."""
    if isinstance(change, RetireChange):
        return True
    if isinstance(change, TierChange):
        return old is not None and _TIER_RANK[change.tier] > _TIER_RANK[old.tier]
    if isinstance(change, ReplaceChange):
        if old is None:
            return True
        new = change.expectation
        if _TIER_RANK[new.tier] > _TIER_RANK[old.tier]:
            return True
        if (new.kind, new.phase) != (old.kind, old.phase):
            return True
        if new.expression is None or old.expression is None:
            # Not measures: the same kind and phase with other words or another warning is
            # another thing to hold the shot to.
            return (new.warning_fault, new.text) != (old.warning_fault, old.text)
        if _without_compare(new.expression) != _without_compare(old.expression):
            return True
        if new.expression.compare is None or old.expression.compare is None:
            return True
        return compare_loosens(old.expression.compare, new.expression.compare)
    return False


def request_loosens(changes: StoredChanges, rows: Mapping[int, ExpectationLike]) -> bool:
    """Whether any change of the request loosens a check."""
    return any(
        change_loosens(
            c, rows.get(c.expectation_id) if not isinstance(c, PutBackOverrideChange) else None
        )
        for c in changes.changes
    )


def replacement_words(change: ReplaceChange) -> str:
    """The replacement's sentence as a card says it, its limit in its unit."""
    return sentence_with_unit(change.expectation.sentence, change.expectation.expression)


def revision_words(change: StoredChange) -> str:
    """What a change does, in the words a card puts after an arrow."""
    if isinstance(change, ReplaceChange):
        return replacement_words(change)
    if isinstance(change, RetireChange):
        return "taken out"
    if isinstance(change, TierChange):
        return f"moved to {change.tier}"
    if isinstance(change, PutBackChange):
        return "put back"
    return change.new_sentence or "put back"
