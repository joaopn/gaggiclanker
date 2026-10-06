"""Ask the metric language about one stored shot.

The language is pure; this is the one place that gathers what it reads for a
shot in the archive: the samples and the phase table from the stored bytes (the
very ones the derivation read, so a number asked for here is the number
stored), the pressure gate the derivation used, and the filing, which is read
now and never stored: refiling a shot changes what ``relative_to`` divides by
and moves nothing that was stored.
"""

from __future__ import annotations

from collections.abc import Sequence

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.db.repos.signatures import SignatureRepository
from gaggiclanker.domain.diagnostics import as_sample_dicts
from gaggiclanker.domain.metric_language import Expression, Result, ShotData, evaluate
from gaggiclanker.domain.phase_names import raw_phase_names
from gaggiclanker.domain.slog import SlogError, parse_slog
from gaggiclanker.shotinfo.render import load_shots
from gaggiclanker.signatures.checks import stored_shot_data

__all__ = ["UnreadableShot", "evaluate_for_shot", "shot_data", "stored_data"]


class UnreadableShot(Exception):
    """The shot is in the archive but its bytes do not parse (it is quarantined)."""


async def shot_data(db: Database, shot_id: int) -> ShotData | None:
    """What the language reads of one stored shot, or ``None`` when there is no such shot.

    Raises :class:`UnreadableShot` for a shot whose bytes do not parse.
    """
    loaded = await load_shots(db, [shot_id])
    source = await ShotsRepository(db).derivation_source(shot_id)
    if not loaded or source is None:
        return None
    facts = loaded[0]
    try:
        slog = parse_slog(source.raw_slog, source.device_id)
    except SlogError as exc:
        raise UnreadableShot(str(exc)) from exc
    final = facts.shot.final_weight_g if facts.shot.scale_connected else None
    version = facts.version
    return ShotData.build(
        as_sample_dicts(slog),
        slog.transitions,
        profile_phases=raw_phase_names(source.profile),
        has_pressure=facts.has_pressure,
        scale_connected=facts.shot.scale_connected,
        final_weight_g=final if final is not None and final > 0 else None,
        target_yield_g=facts.target_yield_g,
        dose_g=version.dose_g if version is not None else None,
    )


async def stored_data(db: Database, shot_id: int) -> ShotData | None:
    """What the language reads of one stored shot, built from the rows the derivation stored.

    The same data the shot's checks are worked out from (:mod:`gaggiclanker.signatures.checks`),
    so a number a reading's evidence shows and the number its Checks line shows can never come
    from two readings of the shot, and the shot's log is not parsed again. ``None`` when there is
    no such shot; raises :class:`UnreadableShot` for a quarantined one (no samples were stored).
    """
    loaded = await load_shots(db, [shot_id])
    if not loaded:
        return None
    facts = loaded[0]
    if facts.shot.quarantined:
        raise UnreadableShot("its bytes never parsed")
    repo = SignatureRepository(db)
    samples = (await repo.stored_samples([shot_id]))[shot_id]
    version_id = facts.shot.profile_version_id
    documents = await repo.profile_documents([version_id]) if version_id else {}
    return stored_shot_data(
        samples,
        facts.phases,
        facts.check_subject,
        raw_phase_names(documents.get(version_id or 0)),
    )


async def evaluate_for_shot(
    db: Database, shot_id: int, expressions: Sequence[Expression]
) -> list[Result] | None:
    """One result per expression, in order, or ``None`` when there is no such shot."""
    data = await shot_data(db, shot_id)
    if data is None:
        return None
    return [evaluate(expression, data) for expression in expressions]
