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

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from gaggiclanker.db.repos.base import dumps, to_iso
from gaggiclanker.db.repos.shots import (
    ShotDerivationUpdate,
    ShotInsert,
    ShotSampleRow,
    ShotsRepository,
)
from gaggiclanker.domain.diagnostics import as_sample_dicts, transform_shot
from gaggiclanker.domain.firmware_values import compute_firmware_values
from gaggiclanker.domain.models import IndexEntry
from gaggiclanker.domain.phase_control import PhaseControl, phase_controls
from gaggiclanker.domain.scoring import execution_score
from gaggiclanker.domain.slog import Slog, SlogError, parse_slog

__all__ = [
    "DERIVATION_VERSION",
    "NO_TIMESTAMP_EPOCH",
    "SI_SCALE_CONNECTED",
    "DerivedShot",
    "derive_shot",
    "epoch_to_iso",
    "index_fields",
    "rederive_shots",
    "refill_final_weights",
    "sample_rows",
]

log = structlog.get_logger(__name__)

#: The version of what :func:`derive_shot` produces: a shot's phases, diagnostics
#: and execution score. Written on every new derive, and the boot step
#: (:func:`rederive_shots`) re-derives every stored shot below it from its bytes.
#: **A change to what derive produces bumps this**, and the next start brings the
#: archive along, so a search, a sort or a Set never compares two definitions.
#: 1: puck resistance from the machine's own measurement when the shot has it.
#: 2: the firmware analyzer's machine puck resistance, liquid resistance and water
#: pumped (``diagnostics_json["firmware"]``).
#: 3: profile compliance reads the shot's profile: each phase is graded only on
#: the target it steers by (pressure or pump flow), never on a soft limit, a
#: simple phase or the post-brew tail, and a shot with no usable profile has no
#: adherence. **The profile a shot is linked to is now an input**: linking a
#: shot to a profile version leaves it to be derived again.
#: 4: a sample the phase's *limit* held (a pressure phase at its flow limit, a
#: flow phase at its pressure limit) is not graded either.
#: 5: the largest undershoot is 0 when the shot never fell below its target
#: (it showed the smallest overshoot).
#: 6: the channeling block's flow-versus-target residual is read only over
#: flow-steered samples, absent otherwise. A version of its own, so a database
#: that booted at 5 is derived again for this change too.
DERIVATION_VERSION = 6

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
    device_id: str,
    source: str = "device",
    has_pressure: bool | None = None,
    entry: IndexEntry | None = None,
    incomplete: bool = False,
    profile: Mapping[str, Any] | None = None,
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

    ``profile`` is the stored document of the profile version the shot is linked
    to. It is the only thing that says which of a phase's two logged targets is
    the one the machine steered by, so without it (a shot never mirrored, or an
    unreadable document) no adherence is worked out: absent, not perfect.
    """
    header = slog.header
    samples = sample_rows(slog)
    scale_connected = any(
        sample.si is not None and sample.si & SI_SCALE_CONNECTED for sample in slog.samples
    )

    shot = ShotInsert(
        device_id=device_id,
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

    error = _attach_diagnostics(
        shot, slog, has_pressure=has_pressure, controls=phase_controls(profile)
    )
    return DerivedShot(shot=shot, samples=samples, diagnostics_error=error)


def _attach_diagnostics(
    shot: ShotInsert,
    slog: Slog,
    *,
    has_pressure: bool | None,
    controls: tuple[PhaseControl, ...] | None,
) -> str | None:
    """Phases, diagnostics and the execution score — best effort, and on purpose.

    This is the one failure in the ingest path that does **not** quarantine. The
    bytes parsed, the samples are real and the curve will draw; a bug in a
    channeling heuristic must not hide a perfectly good shot from the archive.
    The caller counts the failure, and re-deriving is a pass over `raw_slog`
    away.
    """
    try:
        transformed = transform_shot(
            slog, "per_phase", has_pressure=has_pressure, phase_controls=controls
        )
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
            # What the machine's own shot analyzer shows, in its units and taken
            # its way: informational, kept apart from the banded diagnostics.
            "firmware": compute_firmware_values(
                as_sample_dicts(slog), slog.volume_g, has_pressure=transformed["has_pressure"]
            ),
            # The score's own working, not just its result. `shots.execution_score`
            # and `execution_reason` are columns because the list sorts and filters
            # on them; the per-component penalties belong with the diagnostics they
            # were computed from, so the shot page can say *which* fault cost what
            # without re-deriving the score in the browser.
            "score": score.as_dict(),
        }
    )
    shot.execution_score = score.score
    shot.execution_reason = score.reason
    shot.derivation_version = DERIVATION_VERSION
    return None


async def refill_final_weights(shots: ShotsRepository) -> int:
    """Re-read the final weight of every stored shot that has none; return the count filled.

    The final weight is derived from the bytes, so a better rule for reading it
    (a shot whose scale dropped to zero in its last samples, see
    :meth:`Slog.volume_g`) reaches the shots already archived only by reading
    their bytes again. Run at boot: the candidates are the few shots a scale was
    connected to that still have no weight, and a shot the rule finds nothing in
    stays as it is, so running it again changes nothing.
    """
    filled = 0
    for shot in await shots.missing_final_weight():
        try:
            weight = parse_slog(shot.raw_slog, shot.device_id).volume_g
        except SlogError:
            continue  # stored before it was quarantined for this; nothing to read
        if weight is None:
            continue
        await shots.set_final_weight(shot.id, weight)
        log.info("shot_final_weight_refilled", shot_id=shot.id, final_weight_g=weight)
        filled += 1
    return filled


async def rederive_shots(shots: ShotsRepository) -> tuple[int, int]:
    """Bring every shot derived by an older version up to :data:`DERIVATION_VERSION`.

    Returns ``(rederived, failed)``. Run at boot, before any request: the four
    derived columns are rewritten from ``raw_slog`` with the same code a new shot
    goes through, and nothing else of the shot is touched (notes, judgements,
    Set membership, samples and the device's own index fields are not derived).

    The profile is the one the shot is linked to now (its versions are content-
    hashed and immutable, so it is the same document every time), and none when
    it is not linked.

    The pressure gate is the one the shot was first derived with, read back from
    its stored diagnostics, and ``None`` ("decide from the trace") when it has
    none. Not the machine's row of today: which board a shot came off does not
    change when the machine's settings do, and an imported shot never had a
    machine to ask. Reusing the stored answer keeps the result a function of the
    bytes.

    A shot whose bytes no longer parse, or whose diagnostics fail, keeps what it
    has, is logged, and is marked failed at this version so the next boot does
    not try it again; a later version retries it. Each shot is one statement, so
    a crash leaves every shot old or new, and a second run finds nothing to do.
    """
    started = time.monotonic()
    rederived = failed = 0
    for shot_id in await shots.pending_derivations(DERIVATION_VERSION):
        source = await shots.derivation_source(shot_id)
        if source is None:
            continue
        try:
            slog = parse_slog(source.raw_slog, source.device_id)
            derived = derive_shot(
                slog,
                source.raw_slog,
                device_id=source.device_id,
                has_pressure=source.has_pressure,
                profile=source.profile,
            )
            error = derived.diagnostics_error
        except Exception as exc:  # a shot must never stop the archive booting
            error = f"{type(exc).__name__}: {exc}"
        if error is not None:
            log.warning("shot_rederive_failed", shot_id=shot_id, error=error)
            await shots.mark_derivation_failed(shot_id, DERIVATION_VERSION)
            failed += 1
            continue
        shot = derived.shot
        landed = await shots.rewrite_derived(
            shot_id,
            ShotDerivationUpdate(
                phases_json=shot.phases_json,
                diagnostics_json=shot.diagnostics_json,
                execution_score=shot.execution_score,
                execution_reason=shot.execution_reason,
                derivation_version=DERIVATION_VERSION,
            ),
            profile_version_id=source.profile_version_id,
        )
        if not landed:
            # Linked to another profile since it was read: that link put the shot
            # back to version 0, so the next pass (the mirror or import that linked it
            # runs one, else the next start) derives it with the new profile.
            log.info("shot_rederive_superseded", shot_id=shot_id)
            continue
        rederived += 1
    log.info(
        "shots_rederived",
        count=rederived,
        failed=failed,
        derivation_version=DERIVATION_VERSION,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return rederived, failed


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
