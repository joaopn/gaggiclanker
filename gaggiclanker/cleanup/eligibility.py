"""When may a shot be deleted from the machine? One function, two callers.

`req:history:delete` is unrecoverable: the `.slog`, the notes file and the index
entry all go, and the machine is the only copy of a shot until the archive has
it. So the rule is stated once, here, and applied in both places that matter:

* :class:`~gaggiclanker.drafts.gate.SettingsWriteGate` runs it before the client
  puts a frame on the wire, which is the check that actually protects anything —
  a refusal is audited with its reason;
* :class:`~gaggiclanker.cleanup.service.CleanupService` runs it to build the
  plan, so the preview a person approves is the same set the gate will allow.

A route does **not** run it. A rule enforced at the edge is a rule that a second
caller — a background task, a future API, a script — gets to skip.

The four disqualifications, and why each one:

``no shot``
    The archive has never seen this id on this machine. There is nothing to
    protect the shot with, so nothing may delete it.

``wrong machine``
    Shot ids are a per-device counter, so `000129` exists on every machine that
    has pulled a hundred and twenty-nine shots. A candidate row is fetched by
    ``(machine_id, device_id)`` and the pair is re-asserted here, because "this
    id is in the archive" and "this shot is in the archive" are different claims.

``quarantined``
    The bytes are stored but they did not parse. They may yet parse after a
    parser fix — that is the entire reason quarantine exists — and the device's
    copy is the only other one. Quarantined shots stay on the machine.

``truncated``
    The blob is not the length the header says it should be
    (``header_size + sample_count * sample_size``). That means the fetch caught
    the file mid-write, or something went wrong on the way in, and the machine
    may well hold bytes we do not. The exception is a shot the archive already
    recorded as ``incomplete``: the parser saw the shortfall, said so, and the
    short file *is* what the machine served — there is nothing more to lose.
"""

from __future__ import annotations

from gaggiclanker.db.repos.cleanup import CleanupCandidate
from gaggiclanker.domain.slog import header_size_for, sample_size_for

__all__ = ["expected_slog_bytes", "ineligible_reason"]


def expected_slog_bytes(candidate: CleanupCandidate) -> int | None:
    """How long this shot's file should be, or ``None`` when we cannot say.

    ``None`` for a row whose header fields were never filled in — a quarantined
    shot, or one imported from an export that carried no version. Such a shot is
    refused on other grounds; this returns ``None`` rather than guessing a
    length that would look like agreement.
    """
    if candidate.slog_version is None or candidate.fields_mask is None:
        return None
    header = header_size_for(candidate.slog_version)
    sample = sample_size_for(candidate.slog_version, candidate.fields_mask)
    return header + candidate.sample_count * sample


def ineligible_reason(
    candidate: CleanupCandidate | None, *, machine_id: int, device_id: str
) -> str | None:
    """Why this shot may **not** be deleted from the machine, or ``None`` if it may.

    A sentence rather than a code, because where it ends up is an audit row and
    a toast: "shot 000129 is quarantined — its bytes never parsed, so the
    machine's copy is the only one that might" is the whole explanation, and a
    code would need a lookup table to become one.
    """
    if candidate is None:
        return (
            f"Shot {device_id} is not in the archive for this machine, so there is no copy "
            "of it here to protect. Sync before cleaning up."
        )
    if candidate.machine_id != machine_id:
        return (
            f"Shot {device_id} in the archive belongs to a different machine. Shot ids are a "
            "per-device counter and deleting on the strength of a matching number would "
            "delete somebody else's shot."
        )
    if candidate.quarantined:
        return (
            f"Shot {device_id} is quarantined: its bytes are stored but did not parse. A "
            "parser fix can still re-derive it, and the machine's copy is the only other "
            "one there is."
        )
    if candidate.raw_bytes <= 0:
        return f"Shot {device_id} has no raw bytes stored in the archive."
    expected = expected_slog_bytes(candidate)
    if expected is None:
        return (
            f"Shot {device_id} carries no .slog header fields in the archive, so the stored "
            "bytes cannot be checked against the length the header implies."
        )
    if candidate.raw_bytes != expected:
        if candidate.incomplete and candidate.raw_bytes >= header_size_for(
            candidate.slog_version or 7
        ):
            # The archive already knows this file was short and said so at
            # ingest. The machine served these bytes and has no others; keeping
            # it on the device buys nothing.
            return None
        return (
            f"Shot {device_id} is {candidate.raw_bytes} bytes in the archive but its header "
            f"implies {expected}. The machine may hold bytes this box does not."
        )
    return None
