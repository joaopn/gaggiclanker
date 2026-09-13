"""Loading exported shots and profiles into the archive.

The device keeps about 300 KB of history and deletes the oldest shots when free
space runs low. Whatever it dropped before this container existed survives only
in the files a maintainer saved out of the machine's own web UI — so this is the
back door the archive is otherwise missing, and the rules follow from that:

* **A batch never aborts.** One unreadable file in a directory of two hundred
  costs that file a row in the result list, not the other hundred and ninety
  nine their import.
* **A shot we cannot read is still kept**, quarantined, with the JSON bytes as
  its `raw_slog` — the same bargain the sync engine makes with bytes it cannot
  parse. The file is the only copy left; a parser fix
  next month can re-derive it, and a discarded file cannot.
* **Re-importing is free.** Shots de-duplicate on their device id and profiles
  on their content hash, so pointing the importer at the same folder
  twice reports skips rather than writing the archive twice over.
* **An imported shot is a shot.** It goes through the same
  :func:`~gaggiclanker.sync.derive.derive_shot` the sync engine uses, so its
  phases, diagnostics and execution score are computed once, by one code path.
  Two shots in one archive that were scored by two different code paths are not
  comparable, which would defeat the point of having them both.

Detection is by **content, not by file name**: an export directory is full of
files somebody renamed, and a zip entry's name is whatever the archiver chose.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import dumps
from gaggiclanker.db.repos.judgements import JudgementsRepository
from gaggiclanker.db.repos.notes import NotesRepository
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.sets import SetsRepository
from gaggiclanker.db.repos.shots import ShotInsert, ShotsRepository
from gaggiclanker.domain.exports import (
    ShotExport,
    export_device_id,
    looks_like_profile_export,
    looks_like_shot_export,
    profile_export_to_profiles,
    shot_export_to_slog,
    slog_to_raw,
)
from gaggiclanker.domain.slog import SlogError
from gaggiclanker.settings_service import SettingsService
from gaggiclanker.sync.derive import derive_shot

__all__ = [
    "IMPORT_SOURCE",
    "MAX_EXPANDED_BYTES",
    "MAX_ZIP_DEPTH",
    "ImportFile",
    "ImportResult",
    "ImportService",
    "ImportSummary",
]

log = structlog.get_logger(__name__)

#: `shots.source` for everything this module writes. The column has a CHECK
#: constraint listing exactly two values; this is the other one.
IMPORT_SOURCE = "import"

#: How deep to look inside nested zips. One zip of exports is the normal case
#: and a zip of zips happens; deeper than that is a zip bomb or a mistake, and
#: either way the honest answer is "this entry is not a file I can read".
MAX_ZIP_DEPTH = 2

#: How many bytes one batch may decompress, across every file in it and every
#: level of nesting. The same figure the API bounds a request body at, and for a
#: stronger reason: a few kilobytes of zeroed zip expands to gigabytes, and this
#: expansion happens on the event loop, so an unbounded one is not a big import
#: but a stopped appliance. Exports are tens of kilobytes each, so this is still
#: a folder of several hundred shots.
MAX_EXPANDED_BYTES = 50 * 1024 * 1024

#: Zip entry path segments that are archiver bookkeeping rather than somebody's
#: export: a dotfile at any depth (`exports/.DS_Store`) and macOS's resource-fork
#: shadow tree.
_ZIP_NOISE_SEGMENTS = ("__MACOSX",)

type ImportKind = Literal["shot", "profile", "unknown"]
type ImportStatus = Literal["created", "updated", "skipped", "failed"]


class ImportResult(BaseModel):
    """What became of one file — or of one profile inside a multi-profile file.

    Always a result and never an exception: a failed file is a row in the list
    with `status="failed"` and a message saying why, because the caller's next
    question is always "which ones did not land, and what was wrong with them".
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = ""
    kind: ImportKind = "unknown"
    status: ImportStatus
    #: Plain English, for the result list in the UI. Safe to show: it names the
    #: file and the problem, never the file's contents.
    message: str = ""
    shot_id: int | None = None
    #: The 6-digit padded device id, when the file was a shot.
    device_id: str | None = None
    profile_version_id: int | None = None
    #: The profile's label, when the file was a profile.
    label: str | None = None
    #: True when the shot was stored with bytes we could not read. It is in the
    #: archive and it is not a working shot; both halves matter.
    quarantined: bool = False


class ImportSummary(BaseModel):
    """One batch: every result, plus the counts a UI puts in a headline."""

    model_config = ConfigDict(extra="forbid")

    items: list[ImportResult]
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    @classmethod
    def of(cls, items: list[ImportResult]) -> ImportSummary:
        counts = dict.fromkeys(("created", "updated", "skipped", "failed"), 0)
        for item in items:
            counts[item.status] += 1
        return cls(items=items, **counts)


@dataclass(frozen=True, slots=True)
class ImportFile:
    """One uploaded or on-disk file: its name, for reporting, and its bytes."""

    filename: str
    data: bytes


@dataclass(slots=True)
class _Budget:
    """How many more bytes this batch may decompress.

    One object for the whole batch — every file, every level of nesting — because
    a bomb is just as effective spread over a hundred entries as concentrated in
    one, and a per-entry cap cannot see that.
    """

    remaining: int

    def take(self, count: int) -> bool:
        """Claim ``count`` bytes, or refuse and leave the budget untouched."""
        if count > self.remaining:
            return False
        self.remaining -= count
        return True


class ImportService:
    """Reads export files into `shots`, `shot_samples` and `profile_versions`."""

    def __init__(self, db: Database, settings: SettingsService | None = None) -> None:
        self.db = db
        self.settings = settings
        self.shots = ShotsRepository(db)
        self.profiles = ProfilesRepository(db)
        self.notes = NotesRepository(db)
        self.sets = SetsRepository(db)
        self.judgements = JudgementsRepository(db)

    # ── the batch ────────────────────────────────────────────────────

    async def import_files(
        self, files: Sequence[ImportFile], *, replace: bool = False
    ) -> ImportSummary:
        """Import every file, reporting each one separately.

        A file may be a shot export, a profile export, a JSON array of profiles,
        or a zip of any of those. Nothing here raises: a file that cannot be read
        is a failed row in the result list.

        There is no machine to choose. The archive holds one, it exists before
        any pull, and an import done before the machine was ever connected lands
        in the same place the sync engine will later write to — which is what
        makes importing first and connecting afterwards an ordinary order to do
        things in rather than a permanent fork in the archive.
        """
        budget = _Budget(MAX_EXPANDED_BYTES)
        items: list[ImportResult] = []
        for file in files:
            items.extend(await self._import_file(file, replace=replace, budget=budget))
        await self._link_versions_by_label(items)
        summary = ImportSummary.of(items)
        log.info(
            "import_batch_finished",
            files=len(files),
            created=summary.created,
            updated=summary.updated,
            skipped=summary.skipped,
            failed=summary.failed,
        )
        return summary

    async def _import_file(
        self,
        file: ImportFile,
        *,
        replace: bool,
        budget: _Budget,
        depth: int = 0,
    ) -> list[ImportResult]:
        """One file: a zip to expand, or a JSON document to read."""
        if zipfile.is_zipfile(io.BytesIO(file.data)):
            return await self._import_zip(file, replace=replace, budget=budget, depth=depth)

        try:
            document = json.loads(file.data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [
                ImportResult(
                    filename=file.filename,
                    status="failed",
                    message=f"not JSON: {exc}",
                )
            ]

        if looks_like_shot_export(document):
            return [
                await self.import_shot(
                    document,
                    replace=replace,
                    filename=file.filename,
                    raw=file.data,
                )
            ]
        if looks_like_profile_export(document):
            return await self.import_profile(document, filename=file.filename)
        return [
            ImportResult(
                filename=file.filename,
                status="failed",
                message=(
                    "not a GaggiMate export: a shot export has a `samples` array, "
                    "a profile export has `label` and `phases`"
                ),
            )
        ]

    async def _import_zip(
        self, file: ImportFile, *, replace: bool, budget: _Budget, depth: int
    ) -> list[ImportResult]:
        """Expand one zip, within the batch's decompression budget."""
        if depth >= MAX_ZIP_DEPTH:
            return [
                ImportResult(
                    filename=file.filename,
                    status="failed",
                    message=f"zips nested more than {MAX_ZIP_DEPTH} deep are not expanded",
                )
            ]

        items: list[ImportResult] = []
        try:
            with zipfile.ZipFile(io.BytesIO(file.data)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or _is_zip_noise(info.filename):
                        continue
                    label = f"{file.filename}:{info.filename}"
                    data = _read_within(archive, info, budget)
                    if data is None:
                        items.append(
                            ImportResult(
                                filename=label,
                                status="failed",
                                message=(
                                    "this entry would take the batch past the "
                                    f"{MAX_EXPANDED_BYTES // (1024 * 1024)} MB decompression "
                                    "limit; import it on its own"
                                ),
                            )
                        )
                        continue
                    items.extend(
                        await self._import_file(
                            ImportFile(filename=label, data=data),
                            replace=replace,
                            budget=budget,
                            depth=depth + 1,
                        )
                    )
        except (zipfile.BadZipFile, OSError) as exc:
            items.append(
                ImportResult(
                    filename=file.filename, status="failed", message=f"unreadable zip: {exc}"
                )
            )
        if not items:
            items.append(
                ImportResult(
                    filename=file.filename, status="skipped", message="the zip holds no files"
                )
            )
        return items

    # ── shots ────────────────────────────────────────────────────────

    async def import_shot(
        self,
        data: bytes | dict[str, Any],
        *,
        replace: bool = False,
        filename: str = "",
        raw: bytes | None = None,
    ) -> ImportResult:
        """Import one shot export.

        ``raw`` is the file's own bytes when the caller has already decoded the
        JSON. They are what a quarantined row stores: re-serialising the parsed
        document would lose whatever made it unreadable, which is exactly the
        evidence the next fix needs.
        """
        document, raw_bytes, failure = _as_document(data, raw, filename)
        if failure is not None:
            return failure

        try:
            export = ShotExport.model_validate(document)
            slog = shot_export_to_slog(export)
            slog_bytes = slog_to_raw(slog)
        except (ValidationError, SlogError, ValueError) as exc:
            return await self._quarantine(
                document,
                raw_bytes,
                replace=replace,
                filename=filename,
                reason=_reason(exc),
            )

        device_id = export_device_id(export)
        if not device_id:
            return ImportResult(
                filename=filename,
                kind="shot",
                status="failed",
                message="the export has no shot id, so it cannot be told apart from another",
            )

        existing = await self.shots.get_by_device_id(device_id)
        if existing is not None and not replace:
            return ImportResult(
                filename=filename,
                kind="shot",
                status="skipped",
                shot_id=existing.id,
                device_id=device_id,
                message="already in the archive; pass replace to overwrite it",
            )

        derived = derive_shot(
            slog,
            slog_bytes,
            device_id=device_id,
            source=IMPORT_SOURCE,
            # Not `False`, and not the machine's stored flag: nobody told us
            # what board this shot came off, so the diagnostics decide from the
            # trace (a Standard board writes a hard zero for pressure).
            has_pressure=None,
            incomplete=slog.incomplete,
        )
        shot = derived.shot
        shot.profile_version_id = await self._version_for(export.profile_id)

        status: ImportStatus = "updated"
        if existing is not None:
            if existing.source != shot.source:
                # A shot the sync engine pulled, re-stated from a file. The bytes
                # are now the export's, so `source` follows them — worth a line in
                # the log, because it is the one field a replace changes that
                # nobody asked it to.
                log.info(
                    "shot_source_changed",
                    shot_id=existing.id,
                    device_id=device_id,
                    was=existing.source,
                    now=shot.source,
                )
            await self.shots.replace_derived(existing.id, shot, derived.samples)
            shot_id = existing.id
        else:
            shot_id = await self.shots.insert(shot, derived.samples)
            status = "created"
            # Only a newly created shot is offered to auto-assignment. A replace
            # is a better copy of a shot we already hold, and `replace_derived`
            # deliberately keeps its `set_version_id` — re-guessing at that
            # point could move a shot the user had already filed by hand.
            await self.sets.auto_assign(
                shot_id,
                profile_version_id=shot.profile_version_id,
                device_profile_id=shot.profile_id_on_device,
            )

        if export.notes is not None:
            # The export's `notes` is the machine's own notes document, so it
            # belongs in the same mirror the sync engine fills. A replace
            # refreshes it for the same reason it refreshes the samples: this
            # file is a newer reading of what the *device* held. The
            # maintainer's own judgement lives in its own table, and the only
            # thing done to it here is the same one the sync engine does: a shot
            # with notes and *no* verdict gets one seeded from them. An insert
            # that does nothing on conflict, so re-importing a file can never
            # overwrite a verdict somebody typed.
            document = export.notes.for_shot(device_id)
            await self.notes.upsert(shot_id, document)
            await self.judgements.seed_from_device_notes(shot_id, document)

        message = f"{len(derived.samples)} samples"
        if derived.diagnostics_error is not None:
            message = f"{message}; diagnostics failed ({derived.diagnostics_error})"
        log.info(
            "shot_imported",
            shot_id=shot_id,
            device_id=device_id,
            samples=len(derived.samples),
            status=status,
        )
        return ImportResult(
            filename=filename,
            kind="shot",
            status=status,
            shot_id=shot_id,
            device_id=device_id,
            message=message,
        )

    async def _quarantine(
        self,
        document: Any,
        raw_bytes: bytes,
        *,
        replace: bool,
        filename: str,
        reason: str,
    ) -> ImportResult:
        """Store a shot we could not read, if we can tell which shot it is.

        Without an id there is nothing to key the row on and nothing to stop the
        same broken file being stored a hundred times, so that one is a plain
        failure. With an id the bytes are kept: the machine's copy is long gone
        and a parser fix is a re-derive away.
        """
        device_id = _document_device_id(document)
        if not device_id:
            return ImportResult(filename=filename, kind="shot", status="failed", message=reason)

        existing = await self.shots.get_by_device_id(device_id)
        if existing is not None and not replace:
            return ImportResult(
                filename=filename,
                kind="shot",
                status="skipped",
                shot_id=existing.id,
                device_id=device_id,
                message=f"already in the archive, and this copy does not parse either: {reason}",
            )

        shot = ShotInsert(
            device_id=device_id,
            source=IMPORT_SOURCE,
            raw_slog=raw_bytes,
            quarantined=True,
            quarantine_reason=reason,
        )
        status: ImportStatus = "updated"
        if existing is not None:
            await self.shots.replace_derived(existing.id, shot)
            shot_id = existing.id
        else:
            shot_id = await self.shots.insert(shot)
            status = "created"
        log.warning("shot_import_quarantined", shot_id=shot_id, device_id=device_id, reason=reason)
        return ImportResult(
            filename=filename,
            kind="shot",
            status=status,
            shot_id=shot_id,
            device_id=device_id,
            quarantined=True,
            message=f"stored unreadable, with its bytes: {reason}",
        )

    async def _link_versions_by_label(self, items: list[ImportResult]) -> None:
        """Last-resort link from an imported shot to a profile version, by label.

        Runs once at the end of a batch rather than inside `import_shot`, so
        that a folder whose shots happen to be read before its profiles still
        links: the shot pass has no way to know a matching version is three
        files away.

        The heuristic, and its limits:

        * only shots whose `profile_version_id` is still NULL — an id match
          (`_version_for`) is evidence and this is a guess, so it never
          overrides one;
        * match on the `.slog` header's `profileName` against
          `profile_versions.label`, exactly and case-sensitively, because the
          machine treats "9 Bar" and "9 bar" as two profiles;
        * newest matching version wins (`find_version_by_label`), since a
          relabelled profile creates a new version and the most recent one is
          what that name means now.

        It can be wrong: two profiles that once shared a name are
        indistinguishable here. That is the honest cost of having any link at
        all for a shot whose `profileId` the machine deleted years ago, and it
        is why the link is only ever made where there is none.
        """
        for item in items:
            if item.kind != "shot" or item.shot_id is None or item.quarantined:
                continue
            shot = await self.shots.get(item.shot_id)
            if shot is None or shot.profile_version_id is not None:
                continue
            version = await self.profiles.find_version_by_label(shot.profile_name_on_device)
            if version is None:
                continue
            await self.shots.link_profile_version(shot.id, version.id)
            # Echoed on the result so the import report can link to the version
            # it guessed, and so a wrong guess is visible rather than silent.
            item.profile_version_id = version.id
            log.info(
                "shot_linked_by_profile_label",
                shot_id=shot.id,
                version_id=version.id,
                label=shot.profile_name_on_device,
            )

    async def _version_for(self, profile_id: str) -> int | None:
        """The mirrored profile version this shot's `profileId` points at, if any.

        Usually ``None`` for an import: the profile the shot was brewed with was
        very likely edited or deleted long before anyone exported the shot. The
        profiles sync re-links shots whose link is still NULL, so an import done
        before the machine is attached is fixed up rather than wrong for ever.
        """
        if not profile_id:
            return None
        version = await self.profiles.find_version_for_device_profile(profile_id)
        return None if version is None else version.id

    # ── profiles ─────────────────────────────────────────────────────

    async def import_profile(
        self, data: bytes | dict[str, Any] | list[Any], *, filename: str = ""
    ) -> list[ImportResult]:
        """Import a profile export — one profile, or a JSON array of them.

        A list of results rather than one, because a JSON array *is* a
        multi-profile export (firmware report §3.5) and "the third profile in
        that file has a phase we refuse" is the only useful thing to say about
        such a file.

        De-duplication is by content hash, so the UI's export (which strips
        `id`, `selected` and `favorite`) and the firmware's own `writeProfile`
        output (which keeps them and adds `transition.target`) are recognised as
        the same profile and stored once.
        """
        document, _raw, failure = _as_document(data, None, filename)
        if failure is not None:
            return [failure]

        try:
            profiles = profile_export_to_profiles(document)
        except ValidationError as exc:
            return [
                ImportResult(
                    filename=filename, kind="profile", status="failed", message=_reason(exc)
                )
            ]

        results: list[ImportResult] = []
        for profile in profiles:
            version, created = await self.profiles.ensure_version(
                profile, source=IMPORT_SOURCE, device_json=dumps(profile.to_device())
            )
            results.append(
                ImportResult(
                    filename=filename,
                    kind="profile",
                    status="created" if created else "skipped",
                    profile_version_id=version.id,
                    label=version.label,
                    message=(
                        f"profile version {version.id}"
                        if created
                        else "this exact profile is already stored"
                    ),
                )
            )
            log.info(
                "profile_imported", version_id=version.id, label=version.label, created=created
            )
        return results


def _is_zip_noise(name: str) -> bool:
    """Whether a zip entry is archiver bookkeeping rather than an export.

    Every segment, not just the first: `exports/.DS_Store` and a nested
    `stuff/__MACOSX/._shot.json` are as much noise as the ones at the root, and
    reporting them as failed files would bury the real results.
    """
    segments = [part for part in name.replace("\\", "/").split("/") if part]
    return any(part.startswith(".") or part in _ZIP_NOISE_SEGMENTS for part in segments)


def _read_within(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _Budget) -> bytes | None:
    """Read one entry if the batch can still afford it, else ``None``.

    Both halves matter. The declared size is checked first so a bomb is refused
    without being decompressed at all — but a zip's own header is not evidence,
    so the read is bounded too and a lie about the size is caught by the byte
    that does not fit.
    """
    if not budget.take(info.file_size):
        return None
    with archive.open(info) as entry:
        data = entry.read(info.file_size + 1)
    if len(data) > info.file_size:
        return None
    return data


def _as_document(
    data: bytes | str | dict[str, Any] | list[Any],
    raw: bytes | None,
    filename: str,
) -> tuple[Any, bytes, ImportResult | None]:
    """Normalise "bytes or an already-parsed document" into both forms."""
    if isinstance(data, bytes | str):
        payload = data.encode() if isinstance(data, str) else data
        try:
            return json.loads(payload), payload, None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return (
                None,
                payload,
                ImportResult(filename=filename, status="failed", message=f"not JSON: {exc}"),
            )
    # `default=str` never fires on a document that came out of json.loads; it is
    # there so a hand-built dict cannot turn a quarantine into a TypeError.
    return data, raw if raw is not None else json.dumps(data, default=str).encode(), None


def _document_device_id(document: Any) -> str:
    """The padded shot id of a document we could not otherwise read."""
    if not isinstance(document, dict):
        return ""
    try:
        return export_device_id(ShotExport(id=document.get("id", "")))
    except (ValidationError, ValueError):
        return ""


def _reason(exc: Exception) -> str:
    """A one-line failure message safe to show the caller.

    pydantic's own rendering repeats the offending *input* for each error, which
    for an export is somebody's shot data and for a settings-shaped file could
    be worse. Only the field path and the problem are kept.
    """
    if isinstance(exc, ValidationError):
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:5]
        ]
        extra = "" if exc.error_count() <= 5 else f" (+{exc.error_count() - 5} more)"
        return f"{exc.error_count()} validation error(s): " + "; ".join(problems) + extra
    return f"{type(exc).__name__}: {exc}"
