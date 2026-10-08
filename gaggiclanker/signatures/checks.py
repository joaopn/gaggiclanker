"""A shot's ordered checks, worked out whenever it is read.

Nothing about a signature's results is stored on a shot: the checks are a function of the
shot's stored facts, where it is filed (its target yield, its dose, its Set version's
override) and the expectations in force (``confirmed``) of its profile version. Proposing,
rejecting or restoring an expectation, or changing an override, therefore changes every
shot's checks at once, with no re-derivation, and ``DERIVATION_VERSION`` does not move.

This module is the one place that gathers what the pure evaluation
(:func:`gaggiclanker.domain.signature.build_checks`) needs, in a fixed number of queries for
any number of shots: the expectations in force of every profile version involved, the
overrides of every Set version involved, and, only for a shot a confirmed measure or phase
check applies to **and** whose result is not already remembered, its stored samples.

**A read never parses a log.** The language reads the samples the derivation stored
(`shot_samples`, with the phase number of each), the stored phases (their names) and the shot
row's flags, which is the same data the parsed log gave it: a test pins every expression to the
same answer on both. Parsing a log builds a pydantic model per sample, which is most of what a
page of the shots list would otherwise cost.

**A per-process memo** keeps a shot's checks for as long as nothing they depend on has changed.
The key is everything the result is a function of: the shot, a fingerprint of its stored
derivation, its flags and weight, the confirmed expectations (their content, so a confirm, a
reject or a tier change is a different key), the override, and the filing (Set version,
target, dose). Nothing has to be invalidated by hand, since a change to any of those is a
different key; the memo is bounded and a restart empties it.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.signatures import ExpectationRow, SignatureRepository
from gaggiclanker.domain.metric_language import Compare, ShotData
from gaggiclanker.domain.models import PhaseTransition
from gaggiclanker.domain.phase_names import raw_phase_names
from gaggiclanker.domain.signature import ShotChecks, SignatureState, build_checks
from gaggiclanker.domain.warnings import ShotWarning

__all__ = [
    "MEMO_LIMIT",
    "CheckSubject",
    "checks_for_shots",
    "clear_checks_memo",
    "fingerprint",
    "stored_shot_data",
]

#: The kinds that read the shot's samples or phase table.
_NEEDS_THE_LOG = frozenset({"measure", "reached"})

#: How many shots' checks are remembered at least (about a kilobyte or two each); a request
#: that works out more shots than half of this raises the bound to twice its own size.
MEMO_LIMIT = 8192

type _Key = tuple[Any, ...]
_MEMO: OrderedDict[_Key, ShotChecks] = OrderedDict()


def clear_checks_memo() -> None:
    """Forget every remembered result (for a test; a restart does the same)."""
    _MEMO.clear()


def fingerprint(*parts: object) -> str:
    """A short stable hash of its parts' text: what a stored derivation is, for the memo key."""
    text = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.blake2b(text.encode(), digest_size=12).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckSubject:
    """What one shot's checks are worked out from, besides its profile's signature."""

    shot_id: int
    #: The profile version the shot brewed: whose signature applies.
    profile_version_id: int | None
    #: The Set version it is filed under: whose override applies.
    set_version_id: int | None
    warnings: Sequence[ShotWarning]
    phases: Sequence[Mapping[str, Any]]
    duration_s: float
    scale_connected: bool
    final_weight_g: float | None
    target_yield_g: float | None
    dose_g: float | None
    has_pressure: bool = True
    #: Whether the log had a phase table (a log of version 4 or earlier has none).
    per_phase: bool = True
    #: A shot whose bytes did not parse has no samples and no checks.
    quarantined: bool = False
    #: The stored shot-wide facts (``diagnostics["metrics"]``): with the phases, what the
    #: derivation wrote, which a re-derivation may change without touching anything else.
    metrics: Mapping[str, Any] | None = None
    #: The shot row's ``updated_at``: every write of the samples (an insert, the importer's
    #: replace) sets it, so a shot whose samples were rewritten is a different key.
    revision: str = ""


def stored_shot_data(
    samples: Sequence[Mapping[str, float]],
    phases: Sequence[Mapping[str, Any]],
    subject: CheckSubject,
    profile_phases: Sequence[str] | None,
) -> ShotData:
    """What the metric language reads of a shot, built from what the derivation stored.

    The transition table is rebuilt from the samples' own phase numbers (a phase begins at its
    first sample) and the stored phases' names; the rest is the shot row's flags and weight.
    """
    names: dict[int, str] = {}
    for row in phases:
        number = row.get("phase_number")
        if isinstance(number, int) and not isinstance(number, bool):
            names.setdefault(number, str(row.get("name") or ""))
    transitions: list[PhaseTransition] = []
    previous: int | None = None
    if subject.per_phase:
        for index, sample in enumerate(samples):
            if "phase" not in sample:
                continue
            number = int(sample["phase"])
            if number != previous:
                transitions.append(
                    PhaseTransition(
                        sample_index=index,
                        phase_number=number,
                        transition_reason=0,
                        phase_name=names.get(number, ""),
                    )
                )
                previous = number
    final = subject.final_weight_g if subject.scale_connected else None
    return ShotData.build(
        samples,
        transitions,
        profile_phases=profile_phases,
        has_pressure=subject.has_pressure,
        scale_connected=subject.scale_connected,
        final_weight_g=final if final is not None and final > 0 else None,
        target_yield_g=subject.target_yield_g,
        dose_g=subject.dose_g,
    )


def _expectation_print(rows: Sequence[ExpectationRow]) -> str:
    return fingerprint(
        [
            (
                r.id,
                r.tier,
                r.phase,
                r.kind,
                r.expression.model_dump_json(by_alias=True, exclude_none=True)
                if r.expression is not None
                else None,
                r.warning_fault,
                r.text,
                r.fault,
                r.sentence,
            )
            for r in rows
        ]
    )


def _key(
    subject: CheckSubject,
    expectations: Sequence[ExpectationRow],
    override: tuple[int, Compare] | None,
) -> _Key:
    return (
        subject.shot_id,
        subject.revision,
        fingerprint(
            json.dumps(subject.phases, sort_keys=True), json.dumps(subject.metrics, sort_keys=True)
        ),
        subject.profile_version_id,
        subject.set_version_id,
        subject.scale_connected,
        subject.has_pressure,
        subject.final_weight_g,
        subject.target_yield_g,
        subject.dose_g,
        subject.per_phase,
        subject.duration_s,
        tuple(subject.warnings),
        _expectation_print(expectations),
        None if override is None else (override[0], override[1].model_dump_json(exclude_none=True)),
    )


def _remember(key: _Key, checks: ShotChecks, working_set: int) -> None:
    _MEMO[key] = checks
    _MEMO.move_to_end(key)
    # Big enough for the one request that needs the most: a Review sort works out every shot of
    # the archive, and a bound under that would thrash and recompute them all on every page.
    limit = max(MEMO_LIMIT, 2 * working_set)
    while len(_MEMO) > limit:
        _MEMO.popitem(last=False)


async def checks_for_shots(db: Database, subjects: Sequence[CheckSubject]) -> dict[int, ShotChecks]:
    """Each shot's ordered checks, by shot id."""
    if not subjects:
        return {}
    repo = SignatureRepository(db)
    confirmed = await repo.confirmed_for_versions(
        {s.profile_version_id for s in subjects if s.profile_version_id is not None}
    )
    live = [s for s in subjects if not s.quarantined]
    applying = [
        s for s in live if s.profile_version_id is not None and confirmed.get(s.profile_version_id)
    ]
    overrides = await repo.confirmed_overrides(
        {s.set_version_id for s in applying if s.set_version_id is not None}
    )

    found: dict[int, ShotChecks] = {}
    pending: list[tuple[CheckSubject, Sequence[ExpectationRow], tuple[int, Compare] | None, _Key]]
    pending = []
    for subject in subjects:
        if subject.quarantined:
            # Bytes that did not parse have no samples and nothing to check; nothing is logged.
            found[subject.shot_id] = ShotChecks(state=SignatureState(subject.profile_version_id))
            continue
        expectations: Sequence[ExpectationRow] = (
            confirmed.get(subject.profile_version_id, []) if subject.profile_version_id else []
        )
        override = None
        if expectations and subject.set_version_id is not None:
            stored = overrides.get(subject.set_version_id)
            if stored is not None and stored.compare is not None:
                # Applied to the expectation it names and to nothing else; an expectation of
                # another profile version's signature is never in this list to be hit.
                override = (stored.expectation_id, stored.compare)
        key = _key(subject, expectations, override) if expectations else ()
        hit = _MEMO.get(key) if expectations else None
        if hit is not None:
            _MEMO.move_to_end(key)
            found[subject.shot_id] = hit
        else:
            pending.append((subject, expectations, override, key))

    reading = [
        s.shot_id for s, exps, _, _ in pending if any(e.kind in _NEEDS_THE_LOG for e in exps)
    ]
    samples = await repo.stored_samples(reading)
    documents = await repo.profile_documents(
        {
            s.profile_version_id
            for s, _, _, _ in pending
            if s.shot_id in samples and s.profile_version_id
        }
    )
    for subject, expectations, override, key in pending:
        data = None
        if subject.shot_id in samples:
            document = documents.get(subject.profile_version_id or 0)
            data = stored_shot_data(
                samples[subject.shot_id], subject.phases, subject, raw_phase_names(document)
            )
        checks = build_checks(
            warnings=subject.warnings,
            expectations=expectations,
            override=override,
            data=data,
            phases=subject.phases,
            duration_s=subject.duration_s,
            profile_version_id=subject.profile_version_id,
        )
        if expectations:
            # A shot with no signature costs nothing to work out again, and would only push the
            # ones that do out of the memo.
            _remember(key, checks, len(subjects))
        found[subject.shot_id] = checks
    return found
