"""The GaggiMate web UI's JSON exports, read back into the archive's shapes.

The machine's own web UI can save a shot as `shot-<id>.json` and a profile as
`profile-<id>.json`. Those files are the only way back for a shot the device has
already deleted — it keeps 300 KB of history and drops the oldest under storage
pressure — so reading them is not a convenience, it is the recovery path.

Two rules shape the module:

1. **Lenient about keys, strict about values.** The shot export carries UI state
   (`loaded`, `data`) and bookkeeping (`samplesExpected`, `trailingBytes`) that
   mean nothing here, and a later firmware will add more; rejecting the file
   over one of them would cost a shot nobody else still has. A `duration` that
   is not a number, on the other hand, is a file we cannot read honestly, and
   that is an error.

2. **An imported shot must be indistinguishable from a fetched one.** The export
   is the firmware's own JS parser's reading of a `.slog`, in real units rounded
   to two decimals. :func:`shot_export_to_slog` re-quantises every value through
   the encoder's own arithmetic (`domain/slog.quantise_sample_value`), so the
   in-memory structure equals what :func:`~gaggiclanker.domain.slog.parse_slog`
   would have produced — and therefore so do the phases, the diagnostics and the
   execution score. :func:`slog_to_raw` then re-encodes it, so an imported shot
   has `raw_slog` bytes like every other shot and stays re-derivable.

The profile export is the firmware profile document with `id`, `selected` and
`favorite` stripped by the UI (firmware report §3.1); the firmware's own
`writeProfile` output, which keeps all three and adds `transition.target`, must
import unchanged. Both already validate against
:class:`~gaggiclanker.domain.models.Profile`, so `ProfileExport` is only about
the container: a single object, or a JSON array, which is how the UI exports
more than one.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import (
    PhaseTransition,
    Profile,
    Sample,
    ShotNotes,
    SlogHeader,
)
from gaggiclanker.domain.slog import (
    FIELD_DEFS,
    FIELDS_MASK_ALL,
    FIELDS_MASK_V5,
    Slog,
    apply_phases,
    encode_slog,
    header_size_for,
    quantise_sample_value,
    quantise_time,
    sample_size_for,
)

__all__ = [
    "ProfileExport",
    "ShotExport",
    "ShotExportNotes",
    "ShotExportSample",
    "ShotExportTransition",
    "export_device_id",
    "looks_like_profile_export",
    "looks_like_shot_export",
    "profile_export_to_profiles",
    "shot_export_to_slog",
    "slog_to_raw",
]

#: The version the UI writes when a file predates the `version` key. v5 is the
#: oldest format with the 512-byte header and the transition table, which is
#: what every export in the wild carries.
DEFAULT_SHOT_EXPORT_VERSION = 5

#: Open rather than closed. The export is produced by a web UI that grows keys
#: between releases (`wp` and `finalExitReason` arrived with v7), and the
#: `fieldsMask` — not the key set — is what decides the binary layout. Unknown
#: keys are kept on the model rather than dropped, so they show up in a log.
_LENIENT = ConfigDict(extra="allow", populate_by_name=True)


class ShotExportTransition(BaseModel):
    """One entry of the export's `phaseTransitions` array."""

    model_config = _LENIENT

    sample_index: int = Field(ge=0, alias="sampleIndex")
    phase_number: int = Field(ge=0, alias="phaseNumber")
    phase_name: str = Field(default="", alias="phaseName")
    #: v7 only: why the *previous* phase ended. Reserved padding before then,
    #: so an older export reads 0 ("Unknown") rather than a wrong answer.
    transition_reason: int = Field(default=0, ge=0, le=255, alias="transitionReason")

    def to_domain(self) -> PhaseTransition:
        return PhaseTransition(
            sample_index=self.sample_index,
            phase_number=self.phase_number,
            transition_reason=self.transition_reason,
            phase_name=self.phase_name,
        )


class ShotExportSample(BaseModel):
    """One row of the export's `samples` array, in real units.

    `systemInfo` is the awkward one: the UI writes the decoded object
    (`{raw, shotStartedVolumetric, …}`), older exports and hand-written files
    write the raw integer, and both mean the same bitfield. It is normalised to
    the integer here, which is what the binary format actually stores.
    """

    model_config = _LENIENT

    t: int | None = None
    tt: float | None = None
    ct: float | None = None
    tp: float | None = None
    cp: float | None = None
    fl: float | None = None
    tf: float | None = None
    pf: float | None = None
    vf: float | None = None
    v: float | None = None
    ev: float | None = None
    pr: float | None = None
    #: v7 only: cumulative water pumped, ml.
    wp: float | None = None
    si: int | None = Field(default=None, alias="systemInfo")
    phase_number: int | None = Field(default=None, ge=0, alias="phaseNumber")
    phase_display_number: int | None = Field(default=None, alias="phaseDisplayNumber")

    @field_validator("si", mode="before")
    @classmethod
    def _system_info_raw(cls, value: Any) -> Any:
        """Accept the decoded object or the bare bitfield."""
        if isinstance(value, dict):
            return value.get("raw")
        return value

    def value(self, name: str) -> float | None:
        """The named `.slog` field, or ``None`` when the export omits it."""
        found: float | None = getattr(self, name, None)
        return found


class ShotExportNotes(ShotNotes):
    """The `notes` object of a shot export.

    The device's own notes document, which is why it is
    :class:`~gaggiclanker.domain.models.ShotNotes` with one relaxation: `id` may
    be missing, because a hand-assembled export sometimes has no id inside the
    notes even though the shot itself has one. :meth:`for_shot` puts it back, so
    what reaches `device_shot_notes` is a complete document.
    """

    id: str = ""

    def for_shot(self, device_id: str) -> ShotNotes:
        """This document with the shot's id filled in."""
        return ShotNotes.model_validate({**self.to_device(), "id": self.id or device_id})


class ShotExport(BaseModel):
    """`shot-<id>.json` as the machine's web UI writes it.

    Everything that describes the shot is typed; `loaded`, `data` and anything
    else the UI carries is kept as an extra and ignored.
    """

    model_config = _LENIENT

    #: The device's shot id, as a string — `"129"` in the UI's own export.
    id: str = ""
    profile: str = ""
    profile_id: str = Field(default="", alias="profileId")
    #: Unix epoch **seconds**, as the `.slog` header stores it. Below 10 000
    #: means the machine's clock never synced (see `sync/derive.py`).
    timestamp: int = Field(default=0, ge=0)
    #: Milliseconds, unlike `timestamp`. The UI writes it as an integer.
    duration: int = Field(default=0, ge=0)
    #: Final beverage weight in grams, which is the header's `finalWeight`.
    volume: float | None = None
    incomplete: bool = False
    version: int = Field(default=DEFAULT_SHOT_EXPORT_VERSION, ge=1, le=255)
    sample_interval: int = Field(default=250, ge=0, alias="sampleInterval")
    fields_mask: int | None = Field(default=None, ge=0, alias="fieldsMask")
    #: The header's `sampleCount`. Larger than `len(samples)` means the file was
    #: torn, and re-encoding reproduces exactly that.
    samples_expected: int | None = Field(default=None, ge=0, alias="samplesExpected")
    trailing_bytes: int = Field(default=0, ge=0, alias="trailingBytes")
    #: v7 only.
    final_exit_reason: int = Field(default=0, ge=0, le=255, alias="finalExitReason")
    brew_delay: int = Field(default=0, ge=0, alias="brewDelay")
    phase_transitions: list[ShotExportTransition] = Field(
        default_factory=list, alias="phaseTransitions"
    )
    samples: list[ShotExportSample] = Field(default_factory=list)
    notes: ShotExportNotes | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _id_as_text(cls, value: Any) -> Any:
        """`{"id": 129}` and `{"id": "129"}` are the same shot."""
        if isinstance(value, bool):
            raise ValueError("shot id must be a number or a string")
        if isinstance(value, int):
            return str(value)
        return value

    @field_validator("duration", "timestamp", "sample_interval", mode="before")
    @classmethod
    def _whole_number(cls, value: Any) -> Any:
        """Accept `54617.0` where the UI writes `54617`, reject `54617.5`.

        The exports round to two decimals throughout, so a fractional
        millisecond here is a file we are misreading rather than one we should
        silently truncate.
        """
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    @property
    def resolved_fields_mask(self) -> int:
        """Which sample fields this file claims to carry.

        The export always states it; the fallback is for a hand-written file,
        and it is the mask the firmware of that version wrote — all 14 fields
        from v7, everything but `wp` before it.
        """
        if self.fields_mask:
            return self.fields_mask
        return FIELDS_MASK_ALL if self.version >= 7 else FIELDS_MASK_V5


def export_device_id(export: ShotExport) -> str:
    """The archive's id for an exported shot: the 6-digit zero-padded form.

    The same spelling the sync engine stores, so an export of a shot that was
    also pulled from the machine lands on the same `device_id` and is recognised
    as the duplicate it is. An id that is not a decimal number
    cannot be padded — no firmware writes one, but a hand-edited file might — and
    is kept verbatim rather than rejected.
    """
    try:
        return pad6(export.id)
    except ValueError:
        return export.id.strip()


def looks_like_shot_export(document: Any) -> bool:
    """Whether this JSON document is a shot export, judged by content.

    Content and not a file name: a maintainer's export directory is full of
    files somebody renamed, and a zip entry's name is whatever the archiver
    chose. `samples` is the discriminator — no profile document has one.

    Deliberately looser than "this parses as a :class:`ShotExport`". A file whose
    `samples` array is malformed is still recognisably somebody's shot, and the
    importer's answer to that is a quarantined row holding its bytes, not a
    shrug. Recognising it is what makes that possible.
    """
    if not isinstance(document, dict):
        return False
    return "samples" in document or {"timestamp", "duration"} <= set(document)


def looks_like_profile_export(document: Any) -> bool:
    """Whether this JSON document is a profile export (or an array of them)."""
    if isinstance(document, list):
        return bool(document) and all(looks_like_profile_export(item) for item in document)
    return (
        isinstance(document, dict)
        and isinstance(document.get("phases"), list)
        and "label" in document
    )


class ProfileExport(RootModel[Profile | list[Profile]]):
    """`profile-<id>.json`: one profile, or a JSON array of them.

    The UI strips `id`, `selected` and `favorite` on export and the firmware's
    `writeProfile` keeps them and adds `transition.target`;
    :class:`~gaggiclanker.domain.models.Profile` already models both, and
    :func:`~gaggiclanker.domain.models.canonical_profile_json` drops exactly the
    fields that differ — so the two forms of one profile hash the same and
    import as one version.
    """

    root: Profile | list[Profile]

    @property
    def profiles(self) -> list[Profile]:
        """Every profile in the document, in file order."""
        return list(self.root) if isinstance(self.root, list) else [self.root]

    @classmethod
    def from_document(cls, document: Any) -> Self:
        return cls.model_validate(document)


def profile_export_to_profiles(document: Any) -> list[Profile]:
    """Parse a profile export document into profiles.

    Raises:
        pydantic.ValidationError: the document is not a profile, or carries a
            phase the safety model refuses (an unknown target type, a float
            `pump`). Both are data loss we would rather see than swallow.
    """
    return ProfileExport.from_document(document).profiles


def shot_export_to_slog(export: ShotExport) -> Slog:
    """Rebuild the :class:`Slog` the binary parser would have produced.

    Every sample value goes through the encoder's own rounding, so the numbers
    here are the numbers a device-fetched `.slog` carries and the diagnostics
    cannot disagree with themselves about which copy of a shot they are reading.

    A field whose `fieldsMask` bit is set but whose value the export omits
    becomes that field's zero rather than ``None``: the encoder writes 0 for a
    missing value and the parser reads 0 back, and matching the parser is the
    whole job. Fields whose bit is *clear* stay ``None`` — "never recorded" is a
    different fact from "recorded zero", and the diagnostics depend on it.
    """
    version = export.version
    fields_mask = export.resolved_fields_mask
    interval = export.sample_interval
    layout = [fdef for fdef in FIELD_DEFS if fields_mask & (1 << fdef.bit)]

    samples: list[Sample] = []
    for row in export.samples:
        values: dict[str, float | int | None] = {}
        for fdef in layout:
            if fdef.name == "t":
                values["t"] = quantise_time(row.t, version=version, sample_interval=interval)
            else:
                values[fdef.name] = quantise_sample_value(fdef.name, row.value(fdef.name))
        samples.append(Sample.model_validate(values))

    transitions = [t.to_domain() for t in export.phase_transitions]
    # The transition table, not the export's per-sample `phaseNumber`: the table
    # is what the binary carries and what the parser reads, and the two agree on
    # every real export. Trusting the table keeps one derivation path.
    apply_phases(samples, transitions, version)

    sample_count = export.samples_expected if export.samples_expected is not None else len(samples)
    header = SlogHeader(
        version=version,
        sample_size=sample_size_for(version, fields_mask),
        header_size=header_size_for(version),
        sample_interval=interval,
        fields_mask=fields_mask,
        sample_count=sample_count,
        duration_ms=export.duration,
        start_epoch=export.timestamp,
        profile_id=export.profile_id,
        profile_name=export.profile,
        final_weight_g=export.volume or None,
        transitions=transitions,
        final_exit_reason=export.final_exit_reason,
        brew_delay_ms=export.brew_delay,
    )
    return Slog(
        header=header,
        samples=samples,
        # The parser derives this from the file's own shape; here the export
        # states it *and* the shape implies it, and either is enough. A claimed
        # sample count larger than the samples present re-encodes to a file the
        # parser will call incomplete for the same reason.
        incomplete=export.incomplete or sample_count > len(samples) or export.trailing_bytes != 0,
        trailing_bytes=export.trailing_bytes,
        shot_id=export_device_id(export) or None,
    )


def slog_to_raw(slog: Slog) -> bytes:
    """The `.slog` bytes for a rebuilt shot.

    Thin on purpose — it is :func:`~gaggiclanker.domain.slog.encode_slog` — but
    named where the importer reads, because the rule it serves lives here:
    **every archived shot has raw bytes**, whichever door it came in through.
    `raw_slog` is what every derived column is rebuilt from when a parser or a
    diagnostic improves, and a shot imported without it would be the one shot
    that could never be re-derived.

    Raises:
        SlogError: the export declares a `fieldsMask` bit this build cannot
            write, or no fields at all. The caller quarantines the file.
    """
    return encode_slog(slog)
