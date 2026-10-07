"""Everything the archive holds about one shot, loaded once and read many ways.

A :class:`ShotFacts` is the rows a shot is made of — the shot itself, the
person's judgement, the version it was filed under, the note typed on the
machine, its reading and, when asked for, its samples — held
side by side and never re-queried. Every catalogue item is a function of one of
these, so the opening context, the search and the three shot tools can only
ever render the same shot the same way.

The diagnostics blob comes in **two shapes**, and both are real archive data.
Ingest stores the full block (`per_phase`: nested `resistance`, `extraction`,
`weight` and `profile_compliance`); an older or hand-written row may hold the
summary block (flat `resistance_avg`, `pressure_rmse_bar`, …).
:meth:`ShotFacts.section` and :meth:`ShotFacts.flat` are the two ways in, and an
item reads whichever the shot has — a reader that understood one shape would
silently drop half the archive.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.repos.judgements import ShotJudgementRow
from gaggiclanker.db.repos.notes import DeviceShotNotesRow
from gaggiclanker.db.repos.reviews import ReadingRecord
from gaggiclanker.db.repos.sets import SetVersionRow
from gaggiclanker.db.repos.shots import ShotDetailRow, ShotSampleRow
from gaggiclanker.domain.signature import ShotChecks
from gaggiclanker.domain.slog import FIELD_DEFS
from gaggiclanker.domain.warnings import ShotWarning, percent_of_target, shot_warnings
from gaggiclanker.review.reading import merge_reading
from gaggiclanker.signatures.checks import CheckSubject

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
    #: What the shot's readings are: the newest review of any status, the newest finished one and
    #: its claims. A model's review of the shot's data, made without the person's judgement,
    #: and only ever *served* to a chat as the claims the person did not reject. ``None`` for facts
    #: built by hand, which read as a shot never read.
    reading: ReadingRecord | None = None
    #: Every stored sample in time order, or ``None`` when they were not
    #: loaded — which is a different thing from a shot with no samples (``()``).
    samples: tuple[ShotSampleRow, ...] | None = None
    #: The shot's ordered checks, worked out with its profile version's **confirmed** signature
    #: when it was loaded (:func:`gaggiclanker.shotinfo.render.load_shots`), before any reading
    #: is merged in. ``None`` for facts built by hand, which read as a shot with no signature:
    #: its universal warnings, as they are.
    checks: ShotChecks | None = None

    @property
    def shot_id(self) -> int:
        return self.shot.id

    @property
    def set_id(self) -> int | None:
        badge = self.shot.set_badge
        return badge.set_id if badge is not None else None

    @property
    def version_label(self) -> str | None:
        """The name of the Set version the shot is filed under, "v1.1"."""
        badge = self.shot.set_badge
        return badge.version_label if badge is not None else None

    @property
    def puck_flow_recorded(self) -> bool:
        """Whether the firmware recorded puck flow for this shot, on a machine that can.

        A board with no pressure sensor writes the field, as zeros, on every sample: the
        mask says the column exists, not that anything was measured, so such a shot has no
        puck flow (nor any number built on it) however the mask reads.

        Read from the header's field mask, the one record of which channels
        a file carries. Without it the engine's flow averages are ``0.0`` over
        nothing; with it a ``0.0`` is a real reading — a puck that choked. A
        row with no mask (written by hand, or before it was stored) is taken
        at its word.
        """
        mask = self.shot.fields_mask
        return self.has_pressure and (mask is None or bool(mask & (1 << _PUCK_FLOW_BIT)))

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
    def firmware(self) -> Mapping[str, Any]:
        """The firmware analyzer's values (machine puck resistance, water pumped).

        Empty for a shot derived before they existed; the boot re-derive fills
        it in. Its own block, apart from the engine's own numbers.
        """
        value = self.blob.get("firmware")
        return value if isinstance(value, dict) else {}

    @property
    def metrics(self) -> Mapping[str, Any]:
        """The shot-wide facts derived with it: the profile's phases it never began, fast flow.

        Empty for a shot derived before they existed; the boot re-derive fills it in.
        """
        value = self.blob.get("metrics")
        return value if isinstance(value, dict) else {}

    @property
    def check_subject(self) -> CheckSubject:
        """What this shot's checks are worked out from, besides its profile's signature."""
        shot = self.shot
        version = self.version
        return CheckSubject(
            shot_id=shot.id,
            profile_version_id=shot.profile_version_id,
            set_version_id=shot.set_version_id,
            warnings=self.warnings,
            phases=self.phases,
            duration_s=shot.duration_ms / 1000,
            scale_connected=shot.scale_connected,
            final_weight_g=shot.final_weight_g,
            target_yield_g=self.target_yield_g,
            dose_g=version.dose_g if version is not None else None,
            has_pressure=self.has_pressure,
            per_phase=self.metrics.get("per_phase") is not False,
            quarantined=shot.quarantined,
            metrics=self.metrics,
            revision=shot.updated_at,
        )

    @property
    def target_yield_g(self) -> float | None:
        """The target yield of the version the shot is filed under, when it has one.

        Read when the shot is read, never stored with it: filing, moving or
        discarding a shot changes it, and must not need a re-derivation.
        """
        target = self.version.target_yield_g if self.version is not None else None
        return target if target is not None and target > 0 else None

    @property
    def warnings(self) -> list[ShotWarning]:
        """What is plainly wrong with the shot, from its stored numbers and where it is filed."""
        return shot_warnings(
            final_weight_g=self.shot.final_weight_g,
            scale_connected=self.shot.scale_connected,
            final_exit_reason=self.shot.final_exit_reason or 0,
            duration_s=self.shot.duration_ms / 1000,
            target_yield_g=self.target_yield_g,
            phases=self.phases,
            metrics=self.metrics,
        )

    @property
    def signature_checks(self) -> ShotChecks:
        """The checks before any reading: the confirmed signature's results and the warnings."""
        return self.checks if self.checks is not None else ShotChecks.from_warnings(self.warnings)

    @property
    def shot_checks(self) -> ShotChecks:
        """The checks **a chat is given**: the reading's free-text results, unless rejected.

        A result a person rejected leaves its expectation unchecked, as it does in the verdict and
        the badge the person reads (:func:`gaggiclanker.review.reading.serve_reading`).
        """
        return merge_reading(self.signature_checks, self.reading)

    def share_of_target(self, weight_g: float | None) -> float | None:
        """A weight as a percentage of the filed version's target yield, to a tenth."""
        return percent_of_target(weight_g, self.target_yield_g)

    @property
    def full(self) -> bool:
        """True when the diagnostics are the full block rather than the summary."""
        return isinstance(self.diagnostics.get("weight"), dict)

    def section(self, name: str) -> Mapping[str, Any] | None:
        """One sub-block of the full diagnostics (``resistance``, ``extraction``…).

        ``None`` on a summary-level blob and wherever the engine wrote ``None``
        — the pressure-derived blocks on a machine with no pressure sensor.
        """
        value = self.diagnostics.get(name)
        return value if isinstance(value, dict) else None

    def section_value(self, name: str, key: str) -> float | None:
        block = self.section(name)
        return None if block is None else number(block.get(key))

    def flat(self, key: str) -> float | None:
        """A number from the summary-level blob; ``None`` on the full one."""
        return None if self.full else number(self.diagnostics.get(key))

    def summary_value(self, block: str, key: str) -> float | None:
        """A number from the summary statistics (``summary.flow.avg_flow_ml_s``)."""
        value = self.summary.get(block)
        return number(value.get(key)) if isinstance(value, dict) else None

    @property
    def phases(self) -> Sequence[Mapping[str, Any]]:
        return [phase for phase in self.shot.phases or [] if isinstance(phase, dict)]
