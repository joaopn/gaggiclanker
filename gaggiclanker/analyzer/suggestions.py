"""Accepting a suggestion: turning advice into a new Set version.

This is the only place in the codebase that converts a direction and a magnitude
into a number, and that concentration is the point. "Two steps finer" has to
become `grind_value - 2` exactly once, with the grinder's convention written
down beside it, or two call sites will eventually disagree about which way finer
is.

Three rules govern what can be accepted at all:

* **Only four variables are actionable.** grind, dose, yield and temperature are
  columns of `set_versions`; pressure, flow, pre-infusion, puck prep and profile
  are edits to a profile, and gaggiclanker writes nothing to the device. Those
  are refused with a message saying so, not silently
  dropped — the suggestion is still worth reading.

* **The Set must not have moved on.** A suggestion made about version 3 is a
  delta from version 3's numbers; applying it to version 5 is exactly the
  mistake the version chain exists to prevent. If the Set has a newer version
  than the one the shot was pulled with, the accept is refused and the user is
  told to record the change by hand. It also makes the chunk's second acceptance
  criterion a property rather than a coincidence: the new version's parent *is*
  the shot's version, because they are the same row.

* **There must be a number to change.** A Set version with no dose cannot have
  5 g added to it. The refusal names the field, because the fix is to fill it in.
"""

from __future__ import annotations

import re

import structlog
from pydantic import ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.analyses import AnalysesRepository, SuggestionRow, SuggestionsRepository
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionPatch, SetVersionRow
from gaggiclanker.domain.vocab import ACTIONABLE_VARIABLES
from gaggiclanker.infra.errors import Conflict, NotFound, Unprocessable

__all__ = ["accept_suggestion", "reject_suggestion"]

log = structlog.get_logger(__name__)

#: Which `set_versions` column each actionable variable writes.
_COLUMN: dict[str, str] = {
    "grind": "grind_value",
    "dose": "dose_g",
    "yield": "target_yield_g",
    "temperature": "target_temperature_c",
}

#: How a direction becomes a sign.
#:
#: `finer` is negative because every stepped grinder this archive has seen —
#: Niche, DF64, Mazzer, EK43 — numbers *up* for coarser, with zero at the burrs
#: touching. A grinder that numbers the other way would need its own column on
#: `grinders`; until one turns up, this is the convention and it is written down
#: here rather than assumed in four places.
_SIGN: dict[str, float] = {
    "finer": -1.0,
    "coarser": 1.0,
    "increase": 1.0,
    "decrease": -1.0,
}

#: Which directions make sense for which variable. `finer` on a dose is not a
#: unit mismatch, it is a category error, and saying so is more useful than
#: guessing that it meant `decrease`.
_ALLOWED_DIRECTIONS: dict[str, tuple[str, ...]] = {
    "grind": ("finer", "coarser"),
    "dose": ("increase", "decrease"),
    "yield": ("increase", "decrease"),
    "temperature": ("increase", "decrease"),
}

#: What the version's field is called when we have to name it in a refusal.
_FIELD_LABEL: dict[str, str] = {
    "grind": "grind value",
    "dose": "dose",
    "yield": "target yield",
    "temperature": "target temperature",
}


async def accept_suggestion(
    db: Database, suggestion_id: int
) -> tuple[SuggestionRow, SetVersionRow]:
    """Apply one suggestion, creating the Set version it asks for.

    Returns the updated suggestion and the version it produced. Raises an
    :class:`~gaggiclanker.infra.errors.AppError` subclass for every refusal, each
    naming what would have to change for the accept to work.
    """
    suggestions = SuggestionsRepository(db)
    suggestion = await suggestions.get(suggestion_id)
    if suggestion is None:
        raise NotFound(f"No suggestion {suggestion_id}")
    if suggestion.status != "open":
        raise Conflict(
            f"That suggestion is already {suggestion.status}",
            details={"field": "status", "message": "only an open suggestion can be accepted"},
        )
    if suggestion.variable not in ACTIONABLE_VARIABLES:
        raise Conflict(
            f"A {suggestion.variable} suggestion cannot be applied automatically",
            details={
                "field": "variable",
                "message": (
                    "Only grind, dose, yield and temperature are recorded on a Set version. "
                    "Pressure, flow, pre-infusion, puck prep and profile changes are edits to "
                    "a brew profile, and this prototype writes nothing to the machine — make "
                    "the change there and record a new Set version by hand."
                ),
            },
        )

    analysis = await AnalysesRepository(db).get(suggestion.analysis_id, with_suggestions=False)
    if analysis is None or analysis.set_version_id is None:  # pragma: no cover - FK guarantees it
        raise Conflict(
            "That suggestion is about a shot with no Set",
            details={
                "field": "set_version_id",
                "message": "attach the shot to a Set before accepting advice about it",
            },
        )

    sets = SetsRepository(db)
    version = await sets.get_version(analysis.set_version_id)
    if version is None:
        raise Conflict(
            "The Set version this advice was about no longer exists",
            details={"field": "set_version_id", "message": "nothing to apply the change to"},
        )
    current = await sets.current_version(version.set_id)
    if current is None or current.id != version.id:
        raise Conflict(
            f"This advice was about version {version.version_no}, and the Set is now on "
            f"version {current.version_no if current else '?'}",
            details={
                "field": "set_version_id",
                "message": (
                    "A suggestion is a change to the numbers it was given. Applying it to a "
                    "later version would compound two changes into one. Record the change by "
                    "hand if you still want it."
                ),
            },
        )

    patch = _patch_for(suggestion, version)
    new_version = await sets.add_version(version.set_id, patch)
    if new_version is None:  # pragma: no cover - the version was read above
        raise Conflict("The Set changed while the suggestion was being applied")

    if not await suggestions.accept(suggestion_id, new_version.id):  # pragma: no cover - re-checked
        raise Conflict("That suggestion was resolved by someone else")

    log.info(
        "suggestion_accepted",
        suggestion_id=suggestion_id,
        variable=suggestion.variable,
        set_version_id=new_version.id,
        parent_version_id=version.id,
    )
    stored = await suggestions.get(suggestion_id)
    if stored is None:  # pragma: no cover - read back after a successful update
        raise Conflict("The suggestion vanished while it was being accepted")
    return stored, new_version


async def reject_suggestion(db: Database, suggestion_id: int) -> SuggestionRow:
    """Mark a suggestion rejected. Nothing else changes.

    Rejecting does not supersede its siblings: disagreeing with the primary
    advice is a reason to look at the backups, not to discard them.
    """
    suggestions = SuggestionsRepository(db)
    suggestion = await suggestions.get(suggestion_id)
    if suggestion is None:
        raise NotFound(f"No suggestion {suggestion_id}")
    if suggestion.status != "open":
        raise Conflict(
            f"That suggestion is already {suggestion.status}",
            details={"field": "status", "message": "only an open suggestion can be rejected"},
        )
    await suggestions.reject(suggestion_id)
    log.info("suggestion_rejected", suggestion_id=suggestion_id, variable=suggestion.variable)
    stored = await suggestions.get(suggestion_id)
    if stored is None:  # pragma: no cover - read back after a successful update
        raise Conflict("The suggestion vanished while it was being rejected")
    return stored


def _patch_for(suggestion: SuggestionRow, version: SetVersionRow) -> SetVersionPatch:
    """The one-field patch this suggestion asks for.

    Exactly one field is sent — that is what makes the new version's diff say
    "grind: 22 -> 20" and nothing else — plus the intent, which is not inherited
    and which carries the model's own reason so the timeline reads as a
    conversation rather than a list of numbers.
    """
    variable = suggestion.variable
    allowed = _ALLOWED_DIRECTIONS[variable]
    if suggestion.direction == "hold":
        raise Conflict(
            "That suggestion says to hold, so there is nothing to apply",
            details={"field": "direction", "message": "a hold is advice to change something else"},
        )
    if suggestion.direction not in allowed:
        raise Unprocessable(
            f"Direction {suggestion.direction!r} does not apply to {variable}",
            details={
                "field": "direction",
                "message": f"a {variable} suggestion moves {' or '.join(allowed)}",
            },
        )
    if not suggestion.magnitude:
        raise Unprocessable(
            f"That {variable} suggestion carries no magnitude",
            details={"field": "magnitude", "message": "there is no number to apply"},
        )

    column = _COLUMN[variable]
    base = getattr(version, column)
    if base is None:
        raise Conflict(
            f"This Set version records no {_FIELD_LABEL[variable]}",
            details={
                "field": column,
                "message": (
                    f"Set the {_FIELD_LABEL[variable]} on the Set first; a change is a delta "
                    "and there is nothing to apply it to."
                ),
            },
        )

    delta = _SIGN[suggestion.direction] * abs(float(suggestion.magnitude))
    target = round(float(base) + delta, 3)
    intent = _intent(suggestion, float(base), target)

    try:
        if variable == "grind":
            return SetVersionPatch(
                grind_value=target,
                grind_setting=_grind_text(version.grind_setting, float(base), target),
                intent=intent,
                origin="analysis",
                origin_analysis_id=suggestion.analysis_id,
            )
        return SetVersionPatch.model_validate(
            {
                column: target,
                "intent": intent,
                "origin": "analysis",
                "origin_analysis_id": suggestion.analysis_id,
            }
        )
    except ValidationError as exc:
        # The bounds on `SetVersionPatch` are the archive's own sanity limits
        # (25-150 °C, a dose of at most 100 g). A suggestion that would push a
        # value outside them is refused rather than clamped: a clamped value is
        # a number nobody chose, and the next shot would be dialled against it.
        raise Unprocessable(
            f"Applying that would put the {_FIELD_LABEL[variable]} outside its allowed range",
            details={
                "field": column,
                "message": f"{target:g} is not an acceptable {_FIELD_LABEL[variable]}",
            },
        ) from exc


def _intent(suggestion: SuggestionRow, base: float, target: float) -> str:
    """The one sentence the version timeline shows.

    The model's own reason, prefixed with what actually moved. The prefix is
    there because a reason on its own ("the shot ran four seconds fast") does
    not say which way anybody went.
    """
    unit = "" if suggestion.unit in ("none", "") else f" {suggestion.unit}"
    head = (
        f"{suggestion.variable} {suggestion.direction} "
        f"{abs(float(suggestion.magnitude or 0)):g}{unit} ({base:g} -> {target:g})"
    )
    reason = suggestion.reason.strip()
    return f"{head}: {reason}"[:500] if reason else head[:500]


def _grind_text(existing: str | None, base: float, target: float) -> str:
    """The grind setting as text, with the number moved and the words kept.

    "22 clicks" becomes "20 clicks" rather than "20": the words are how the user
    reads their own grinder back, and throwing them away to write a bare number
    would make an accepted suggestion look like a different kind of record from
    a hand-typed one.
    """
    rendered = f"{target:g}"
    if not existing:
        return rendered
    # Only an exact appearance of the old number is replaced. A partial match
    # ("2" inside "22") would corrupt the label, which is why this is a word
    # boundary rather than a `str.replace`.
    pattern = re.compile(rf"(?<![\d.]){re.escape(f'{base:g}')}(?![\d.])")
    replaced, count = pattern.subn(rendered, existing, count=1)
    return replaced if count else rendered
