"""Shared pieces every archive repository uses: timestamps and JSON columns.

The house rule is that no dict reaches SQL, so each
table has a pydantic row model and the repository converts. The two things every
one of those models needs are here: the timestamp format the schema's defaults
emit, and a JSON-text type that is validated on the way in *and* on the way out.

Validating on read as well as on write is not belt-and-braces. The file is a
backup somebody restores, a `sqlite3` prompt somebody pokes, and a column six
migrations from now; "it was valid when we wrote it" stops being true, and a
diagnostics blob that has become the string ``None`` should fail loudly at the
repository rather than three layers up in a chart component.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator

__all__ = [
    "JsonList",
    "JsonObject",
    "JsonText",
    "RowModel",
    "dumps",
    "from_iso",
    "to_iso",
    "utc_now",
]

#: The format the schema's column defaults use
#: (``strftime('%Y-%m-%dT%H:%M:%fZ','now')``). Milliseconds, a literal Z, and —
#: the property that matters — lexicographic order equal to chronological order,
#: so ``ORDER BY started_at DESC`` is correct without a date function.
_ISO_MS = "%Y-%m-%dT%H:%M:%S.%f"


def utc_now() -> str:
    """Now, in the exact shape the schema's DEFAULT clauses produce."""
    return to_iso(datetime.now(UTC))


def to_iso(moment: datetime) -> str:
    """Render a datetime the way every timestamp column in this schema is stored."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime(_ISO_MS)[:-3] + "Z"


def from_iso(text: str) -> datetime:
    """Parse a stored timestamp back, spelling out the ``Z`` ``fromisoformat`` will not take."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _is_json(value: str) -> str:
    try:
        json.loads(value)
    except ValueError as exc:
        raise ValueError(f"not valid JSON: {exc}") from None
    return value


#: A TEXT column holding a JSON document. Parsed on every validation, which is
#: both directions: model -> row on write, row -> model on read.
type JsonText = Annotated[str, AfterValidator(_is_json)]


def _loads(value: Any) -> Any:
    """Parse a JSON column on the way *out* of the database.

    Read models carry the decoded document, write models carry the text. That
    split is deliberate: a route that hands the browser a JSON string inside a
    JSON body makes every consumer parse it again, and a repository that let an
    arbitrary object into a TEXT column would have nothing to validate.
    """
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ValueError(f"stored column is not valid JSON: {exc}") from None


#: A JSON object column, decoded on read.
type JsonObject = Annotated[dict[str, Any] | None, BeforeValidator(_loads)]
#: A JSON array column, decoded on read.
type JsonList = Annotated[list[Any] | None, BeforeValidator(_loads)]


def dumps(value: Any) -> str:
    """Compact JSON for a :data:`JsonText` column.

    ``default=str`` because the diagnostics blobs carry the occasional value
    pydantic already rendered (a Decimal, a datetime) and losing a whole shot's
    diagnostics to a serialiser is not a trade worth making.
    """
    return json.dumps(value, separators=(",", ":"), default=str)


RowModel = TypeVar("RowModel", bound=BaseModel)
