"""Everything the archive holds about one shot, loaded once and read many ways.

A :class:`ShotFacts` is the rows a shot is made of — the shot itself, the
person's judgement, the version it was filed under, the note typed on the
machine and, when asked for, its samples — held side by side and never
re-queried. Every catalogue item is a function of one of these, so the opening
context, the search and the three shot tools can only ever render the same shot
the same way.

The diagnostics blob comes in **two shapes**, and both are real archive data.
Ingest stores the full block (`per_phase`: nested `resistance`, `channeling`,
`temperature`, … each with its own `annotations`); an older or hand-written row
may hold the summary block (flat `resistance_avg`, `channeling_risk`, … with one
`annotations` dict at the top). :meth:`ShotFacts.section` and
:meth:`ShotFacts.flat` are the two ways in, and an item reads whichever the shot
has — a reader that understood one shape would silently drop half the archive.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.repos.judgements import ShotJudgementRow
from gaggiclanker.db.repos.notes import DeviceShotNotesRow
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.db.repos.shots import ShotDetailRow, ShotSampleRow
from gaggiclanker.domain.slog import FIELD_DEFS

__all__ = ["ShotFacts", "number"]

#: The `fieldsMask` bit puck flow (`pf`) is recorded under.
_PUCK_FLOW_BIT = next(field.bit for field in FIELD_DEFS if field.name == "pf")


def number(value: Any) -> float | None:
    """A JSON value as a float, or ``None`` when it is not a number.

    ``bool`` is an ``int`` in Python, and a flag read as 1.0 is a number a
    model would be entitled to reason about; it is refused here. So is a
    string, whatever it looks like: the blob is written by the engine, and a
    string where a number belongs is a shape nobody should be guessing at.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


@dataclass(frozen=True, slots=True)
class ShotFacts:
    """One shot's rows, as :func:`gaggiclanker.shotinfo.render.load_shots` read them."""

    shot: ShotDetailRow
    judgement: ShotJudgementRow | None = None
    #: The Set version the shot is filed under, with its recipe. ``None`` for a
    #: shot in the inbox.
    version: SetVersionRow | None = None
    #: What was typed on the machine's own notes card, when it was pulled.
    note: DeviceShotNotesRow | None = None
    #: Every stored sample in time order, or ``None`` when they were not
    #: loaded — which is a different thing from a shot with no samples (``()``).
    samples: tuple[ShotSampleRow, ...] | None = None

    @property
    def shot_id(self) -> int:
        return self.shot.id

    @property
    def set_id(self) -> int | None:
        badge = self.shot.set_badge
        return badge.set_id if badge is not None else None

    @property
    def version_no(self) -> int | None:
        badge = self.shot.set_badge
        return badge.version_no if badge is not None else None

    @property
    def puck_flow_recorded(self) -> bool:
        """Whether the firmware recorded puck flow for this shot.

        Read from the header's field mask, the one record of which channels
        a file carries. Without it the engine's flow averages are ``0.0`` over
        nothing; with it a ``0.0`` is a real reading — a puck that choked. A
        row with no mask (written by hand, or before it was stored) is taken
        at its word.
        """
        mask = self.shot.fields_mask
        return mask is None or bool(mask & (1 << _PUCK_FLOW_BIT))

    @property
    def blob(self) -> Mapping[str, Any]:
        """The stored diagnostics document, or an empty one."""
        return self.shot.diagnostics or {}

    @property
    def has_pressure(self) -> bool:
        """Whether the machine had a pressure sensor for this shot.

        Read from the blob, defaulting to yes: a shot
        derived before the flag existed had a sensor, since the flag was added
        for the boards that do not.
        """
        return bool(self.blob.get("has_pressure", True))

    @property
    def summary(self) -> Mapping[str, Any]:
        value = self.blob.get("summary")
        return value if isinstance(value, dict) else {}

    @property
    def diagnostics(self) -> Mapping[str, Any]:
        value = self.blob.get("diagnostics")
        return value if isinstance(value, dict) else {}

    @property
    def score(self) -> Mapping[str, Any]:
        value = self.blob.get("score")
        return value if isinstance(value, dict) else {}

    @property
    def full(self) -> bool:
        """True when the diagnostics are the full block rather than the summary."""
        return isinstance(self.diagnostics.get("temperature"), dict)

    def section(self, name: str) -> Mapping[str, Any] | None:
        """One sub-block of the full diagnostics (``resistance``, ``channeling``…).

        ``None`` on a summary-level blob and wherever the engine wrote ``None``
        — the pressure-derived blocks on a machine with no pressure sensor.
        """
        value = self.diagnostics.get(name)
        return value if isinstance(value, dict) else None

    def section_value(self, name: str, key: str) -> float | None:
        block = self.section(name)
        return None if block is None else number(block.get(key))

    def section_band(self, name: str, key: str) -> str | None:
        block = self.section(name)
        if block is None:
            return None
        return _label((block.get("annotations") or {}).get(key))

    def flat(self, key: str) -> float | None:
        """A number from the summary-level blob; ``None`` on the full one."""
        return None if self.full else number(self.diagnostics.get(key))

    def flat_band(self, key: str) -> str | None:
        """An annotation from the summary-level blob; ``None`` on the full one."""
        if self.full:
            return None
        return _label((self.diagnostics.get("annotations") or {}).get(key))

    def summary_value(self, block: str, key: str) -> float | None:
        """A number from the summary statistics (``summary.flow.avg_flow_ml_s``)."""
        value = self.summary.get(block)
        return number(value.get(key)) if isinstance(value, dict) else None

    @property
    def phases(self) -> Sequence[Mapping[str, Any]]:
        return [phase for phase in self.shot.phases or [] if isinstance(phase, dict)]


def _label(value: Any) -> str | None:
    """A band label, or ``None`` for anything that is not one.

    ``N/A`` is the engine's word for "not assessed" — no target flow was
    commanded, or the window was too short — and it is treated as absent: the
    number beside it is a placeholder zero, never a measurement.
    """
    if not isinstance(value, str) or not value or value == "N/A":
        return None
    return value
