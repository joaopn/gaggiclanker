"""Turning a parsed `.slog` into the rows that go in the database.

A free function rather than a method, because two callers need exactly this and
neither should own it: the sync engine, reading a machine, and the JSON importer,
reading a JSON export of a shot the machine has already deleted. If the
derivation lived on the engine, the importer would either reach into it or grow
a second copy — and a second copy of "what the score of a shot is" is how two
shots in one archive stop being comparable.

Everything here is derived from ``raw`` and is rebuildable from it. That is the
archive's central bet: the bytes are the product, and a
parser or a diagnostic we improve next month costs a re-derive rather than a
lost shot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from gaggiclanker.db.repos.base import dumps, to_iso
from gaggiclanker.db.repos.shots import ShotInsert, ShotSampleRow
from gaggiclanker.domain.diagnostics import transform_shot
from gaggiclanker.domain.models import IndexEntry
from gaggiclanker.domain.scoring import execution_score
from gaggiclanker.domain.slog import Slog

__all__ = [
    "NO_TIMESTAMP_EPOCH",
    "SI_SCALE_CONNECTED",
    "DerivedShot",
    "derive_shot",
    "epoch_to_iso",
    "index_fields",
    "sample_rows",
]

log = structlog.get_logger(__name__)

#: `startEpoch` below this is the firmware saying "NTP never synced", not a shot
#: pulled in January 1970. The machine's own UI draws no timestamp for these
#: (firmware report §2.2), and neither do we: `started_at` stays NULL and the
#: shot sorts last.
NO_TIMESTAMP_EPOCH = 10_000

#: `si` bit 0x0004: a BLE scale was connected and healthy for this sample. A
#: weight diagnostic on a shot without one is a diagnostic on an integration of
#: modelled pump flow, which means much less.
SI_SCALE_CONNECTED = 0x0004


@dataclass(frozen=True, slots=True)
class DerivedShot:
    """One shot ready to store: the row, its samples, and whether anything failed."""

    shot: ShotInsert
    samples: list[ShotSampleRow]
    #: Set when the samples parsed but the diagnostics did not. The shot is
    #: still storable and still *un*-quarantined — see :func:`derive_shot`.
    diagnostics_error: str | None = None


def derive_shot(
    slog: Slog,
    raw: bytes,
    *,
    machine_id: int,
    device_id: str,
    source: str = "device",
    has_pressure: bool | None = None,
    entry: IndexEntry | None = None,
    incomplete: bool = False,
) -> DerivedShot:
    """Everything storable about one parsed `.slog`.

    ``device_id`` is passed in rather than read out of the file: the `.slog`
    header carries no id at all, and the filename (or, for an import, the
    export's own id) is the only authority on which shot these bytes are.

    ``entry`` is the device's index row when there is one. The header wins
    wherever both have an opinion — the index entry's `volume` is overwritten by
    whatever the user typed into the machine's notes card, and its `duration` is
    rounded, while the header is what the recorder actually wrote — so only the
    aggregates and flags the header does not carry are taken from it.

    ``has_pressure`` gates every pressure-derived diagnostic. ``None`` means
    "nobody has told us", which makes the diagnostics decide from the trace;
    ``False`` is a Standard board, where pressure is a hard zero and a confident
    pressure diagnostic is confident nonsense.
    """
    header = slog.header
    samples = sample_rows(slog)
    scale_connected = any(
        sample.si is not None and sample.si & SI_SCALE_CONNECTED for sample in slog.samples
    )

    shot = ShotInsert(
        device_id=device_id,
        machine_id=machine_id,
        source=source,
        raw_slog=raw,
        started_at=epoch_to_iso(slog.timestamp),
        start_epoch=slog.timestamp,
        duration_ms=slog.duration_ms,
        profile_id_on_device=header.profile_id,
        profile_name_on_device=header.profile_name,
        final_weight_g=slog.volume_g,
        final_exit_reason=header.final_exit_reason,
        brew_delay_ms=header.brew_delay_ms,
        slog_version=header.version,
        sample_interval_ms=header.sample_interval,
        fields_mask=header.fields_mask,
        sample_count=len(samples),
        scale_connected=scale_connected,
        incomplete=incomplete or slog.incomplete,
    )

    from_index = index_fields(entry)
    for key in (
        "deleted_on_device",
        "index_rating",
        "index_volume_g",
        "index_avg_temp_c",
        "index_max_pressure_bar",
        "index_avg_flow_ml_s",
        "index_flags",
    ):
        if key in from_index:
            setattr(shot, key, from_index[key])

    error = _attach_diagnostics(shot, slog, has_pressure=has_pressure)
    return DerivedShot(shot=shot, samples=samples, diagnostics_error=error)


def _attach_diagnostics(shot: ShotInsert, slog: Slog, *, has_pressure: bool | None) -> str | None:
    """Phases, diagnostics and the execution score — best effort, and on purpose.

    This is the one failure in the ingest path that does **not** quarantine. The
    bytes parsed, the samples are real and the curve will draw; a bug in a
    channeling heuristic must not hide a perfectly good shot from the archive.
    The caller counts the failure, and re-deriving is a pass over `raw_slog`
    away.
    """
    try:
        transformed = transform_shot(slog, "per_phase", has_pressure=has_pressure)
        score = execution_score(transformed)
    except Exception as exc:  # pragma: no cover - a diagnostics bug, not a data shape
        log.warning("shot_diagnostics_failed", device_id=shot.device_id, exc_info=True)
        return f"{type(exc).__name__}: {exc}"

    shot.phases_json = dumps(transformed["phases"])
    shot.diagnostics_json = dumps(
        {
            "summary": transformed["summary"],
            "diagnostics": transformed["diagnostics"],
            "detail_level": transformed["detail_level"],
            "has_pressure": transformed["has_pressure"],
        }
    )
    shot.execution_score = score.score
    shot.execution_reason = score.reason
    return None


def index_fields(entry: IndexEntry | None) -> dict[str, Any]:
    """The device index's own view of a shot.

    Used twice: to fill in the aggregates the `.slog` header does not carry, and
    — for a quarantined shot, where there is no header to read — as the only
    thing known about it beyond its bytes.
    """
    if entry is None:
        return {}
    return {
        "started_at": epoch_to_iso(entry.timestamp),
        "start_epoch": entry.timestamp,
        "duration_ms": entry.duration_ms,
        "profile_id_on_device": entry.profile_id,
        "profile_name_on_device": entry.profile_name,
        "deleted_on_device": entry.deleted,
        "index_rating": entry.rating,
        "index_volume_g": entry.volume_g,
        "index_avg_temp_c": entry.avg_temp_c,
        "index_max_pressure_bar": entry.max_pressure_bar,
        "index_avg_flow_ml_s": entry.avg_flow_ml_s,
        "index_flags": entry.flags,
    }


def epoch_to_iso(epoch: int) -> str | None:
    """A `.slog`'s `startEpoch` as a stored timestamp, or ``None`` for "never synced"."""
    if epoch < NO_TIMESTAMP_EPOCH:
        return None
    return to_iso(datetime.fromtimestamp(epoch, tz=UTC))


def sample_rows(slog: Slog) -> list[ShotSampleRow]:
    """Sample models into row models, keyed by `t_ms`.

    Two defences the format makes necessary. A sample with no `t` cannot be
    stored at all (`t_ms` is half the primary key) and a file with two samples
    at the same millisecond — a torn write, or two `record()` calls inside one
    tick — would otherwise fail the whole insert on a uniqueness violation and
    cost the shot. The last sample for a timestamp wins, which matches how the
    firmware's own parser walks the file.
    """
    rows: dict[int, ShotSampleRow] = {}
    for sample in slog.samples:
        if sample.t is None:
            continue
        rows[sample.t] = ShotSampleRow(
            t_ms=sample.t,
            tt=sample.tt,
            ct=sample.ct,
            tp=sample.tp,
            cp=sample.cp,
            fl=sample.fl,
            tf=sample.tf,
            pf=sample.pf,
            vf=sample.vf,
            v=sample.v,
            ev=sample.ev,
            pr=sample.pr,
            si=sample.si,
            wp=sample.wp,
            phase_number=sample.phase,
        )
    return [rows[key] for key in sorted(rows)]
