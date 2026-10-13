"""Level 1 to level 2: a Set has one profile, and a profile has at most one Set.

What changes in the database:

* ``sets`` gains ``board_id``, the Set's profile, with a unique index: a profile has at most one
  Set. It is NULL for a Set that never named a profile (and, from now on, for a Set being
  designed).
* ``profile_board`` loses ``pending_set_id``, ``pending_major`` and ``back_from_set_version_id``,
  ``set_versions`` loses ``pushed_device_profile_id`` and ``profile_drafts`` loses
  ``cleared_set_version_ids_json``. A put now records on its Set when it is made, so nothing
  waits for the sync to do it, and the Set's profile is the one file a Set brews.

What is carried forward: every row, nothing is deleted.

* A Set's profile is the owner of its current version's profile version; when that version has
  no owner (or names none), the owner of the newest of its versions that names an owned version;
  otherwise the Set has none.
* Where several Sets named one profile, the Set whose current version is the profile's active
  version keeps it (else the most recently created). Every other Set gets a profile of its own,
  "<profile> — <Set>", switched off and not starred, holding a copy of every profile version the
  Set names with the signature a copied version carries, and the Set's versions name the copies.
  Shots keep the versions they were brewed on, so nothing about them changes. Nothing is written
  to the machine: the copies are off.
* A Set that named versions of several profiles has each of those versions pointed at its own
  profile's version of the same brew, a copy made (listed, not active) where the profile has
  none.
* A profile that was waiting to be recorded on its own Set, with the draft still approved and
  still the profile's active version, is recorded now. Any other waiting recording is dropped.

This module is frozen: it imports nothing from the application, and what it needs from live code
(the name of a Set's next version, what a copied version carries, the rule that a Set follows its
profile) is copied here as it stood when the step was written. It never begins or commits a
transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any

import structlog

from gaggiclanker.db.upgrades.step import Step

__all__ = ["STEP"]

log = structlog.get_logger(__name__)

#: The fingerprint of ``schemas/level_001.sql``, the database this step upgrades.
EXPECTS = "47eca71aef4b"

#: ``profile_board`` as it is at level 2 (the schema file says what each column means).
_NEW_BOARD_TABLE = """
CREATE TABLE profile_board_new (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    label              TEXT    NOT NULL,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    device_profile_id  TEXT,
    device_version_id  INTEGER REFERENCES profile_versions(id),
    on_home_screen     INTEGER NOT NULL DEFAULT 1 CHECK (on_home_screen IN (0, 1)),
    origin             TEXT    NOT NULL CHECK (origin IN ('adopted', 'draft')),
    failed_version_id  INTEGER REFERENCES profile_versions(id),
    pending_draft_id   INTEGER REFERENCES profile_drafts(id) ON DELETE SET NULL,
    deleted_at         TEXT,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    previous_version_id INTEGER REFERENCES profile_versions(id),
    back_from_version_id INTEGER REFERENCES profile_versions(id),
    on_machine INTEGER NOT NULL DEFAULT 1
    CHECK (on_machine IN (0, 1)),
    conflict_overruled_hash TEXT
) STRICT
"""

_BOARD_COLUMNS = (
    "id, label, current_version_id, device_profile_id, device_version_id, on_home_screen, origin, "
    "failed_version_id, pending_draft_id, deleted_at, created_at, updated_at, previous_version_id, "
    "back_from_version_id, on_machine, conflict_overruled_hash"
)

_BOARD_INDEXES = (
    "CREATE INDEX idx_profile_board_version ON profile_board(current_version_id)",
    "CREATE INDEX idx_profile_board_device ON profile_board(device_profile_id)",
    "CREATE UNIQUE INDEX idx_profile_board_live_device ON profile_board(device_profile_id) "
    "WHERE deleted_at IS NULL AND device_profile_id IS NOT NULL",
    "CREATE UNIQUE INDEX idx_profile_board_live_pending ON profile_board(pending_draft_id) "
    "WHERE deleted_at IS NULL AND pending_draft_id IS NOT NULL",
)

_VERSION_COLUMNS = "content_hash, label, type, utility, json, device_json, source, created_at"


def _name_key(label: str) -> str:
    """What "the same name" meant to the list: no surrounding space, case ignored."""
    return label.strip().casefold()


@dataclass(slots=True)
class _Set:
    id: int
    name: str
    created_at: str
    current_version_id: int | None
    archived: bool = False
    #: The profile version that decided the Set's profile, and that profile.
    anchor: int | None = None
    board: int | None = None


def _run(conn: sqlite3.Connection) -> dict[str, int]:
    counts: Counter[str] = Counter()
    before = _totals(conn)
    now = str(conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0])

    conn.execute("ALTER TABLE sets ADD COLUMN board_id INTEGER REFERENCES profile_board(id)")

    owner = {
        int(r["id"]): (None if r["board_id"] is None else int(r["board_id"]))
        for r in conn.execute("SELECT id, board_id FROM profile_versions")
    }
    sets = [
        _Set(
            int(r["id"]),
            str(r["name"]),
            str(r["created_at"]),
            r["current_version_id"],
            bool(r["archived"]),
        )
        for r in conn.execute(
            "SELECT id, name, created_at, current_version_id, archived FROM sets ORDER BY id"
        )
    ]
    versions_of: dict[int, list[sqlite3.Row]] = {}
    for r in conn.execute(
        "SELECT id, set_id, profile_version_id, created_at FROM set_versions "
        "ORDER BY created_at, id"
    ):
        versions_of.setdefault(int(r["set_id"]), []).append(r)
    by_id = {int(r["id"]): r for rows in versions_of.values() for r in rows}
    counts["sets"] = len(sets)

    # ── 1. each Set's profile ────────────────────────────────────────
    for s in sets:
        s.anchor, s.board = _profile_of(s, versions_of.get(s.id, []), by_id, owner)
    counts["sets_with_a_profile"] = sum(1 for s in sets if s.board is not None)
    counts["sets_without_a_profile"] = sum(1 for s in sets if s.board is None)

    # ── 2. two Sets on one profile: the keeper, and a profile of its own for the rest ──
    active = {
        int(r["id"]): int(r["current_version_id"])
        for r in conn.execute("SELECT id, current_version_id FROM profile_board")
    }
    labels = {
        int(r["id"]): str(r["label"]) for r in conn.execute("SELECT id, label FROM profile_board")
    }
    taken = {
        _name_key(str(r["label"]))
        for r in conn.execute("SELECT label FROM profile_board WHERE deleted_at IS NULL")
    }
    groups: dict[int, list[_Set]] = {}
    for s in sets:
        if s.board is not None:
            groups.setdefault(s.board, []).append(s)
    moved: set[int] = set()  # sets that got a profile of their own
    for board_id, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        keeper = _keeper(
            group,
            [
                x
                for x in group
                if x.current_version_id is not None
                and by_id[x.current_version_id]["profile_version_id"] == active[board_id]
            ],
        )
        counts["keepers"] += 1
        for s in sorted(group, key=lambda x: x.id):
            if s is keeper:
                continue
            copy_board = _own_profile(conn, s, labels[board_id], taken, counts, now)
            log.info(
                "set_profile_copied",
                set_id=s.id,
                from_board_id=board_id,
                board_id=copy_board,
                kept_by_set_id=keeper.id,
            )
            s.board = copy_board
            moved.add(s.id)
            counts["profiles_copied"] += 1

    # ── 2b. two Sets with no profile on one loose version: the rest get a loose copy ──
    for loose_id, group in sorted(_loose_groups(sets, versions_of, owner).items()):
        keeper = _keeper(
            group,
            [
                x
                for x in group
                if x.current_version_id is not None
                and by_id[x.current_version_id]["profile_version_id"] == loose_id
            ],
        )
        for s in sorted(group, key=lambda x: x.id):
            if s is keeper:
                continue
            copy = _loose_copy(conn, loose_id, s.name, counts, now)
            for v in versions_of.get(s.id, []):
                if v["profile_version_id"] == loose_id:
                    conn.execute(
                        "UPDATE set_versions SET profile_version_id = ? WHERE id = ?",
                        (copy, v["id"]),
                    )
                    counts["versions_repointed"] += 1
            log.info(
                "set_loose_brew_copied",
                set_id=s.id,
                from_profile_version_id=loose_id,
                profile_version_id=copy,
                kept_by_set_id=keeper.id,
            )

    # ── 3. every version of a Set names a version its profile owns ──
    for s in sets:
        if s.board is None:
            continue
        conn.execute("UPDATE sets SET board_id = ? WHERE id = ?", (s.board, s.id))
        for v in versions_of.get(s.id, []):
            old = v["profile_version_id"]
            if old is None:
                continue
            new = _own_version(conn, s.board, int(old), counts, now)
            if new != int(old):
                conn.execute(
                    "UPDATE set_versions SET profile_version_id = ? WHERE id = ?", (new, v["id"])
                )
                counts["versions_repointed"] += 1
    conn.execute(
        "CREATE UNIQUE INDEX idx_sets_profile ON sets(board_id) WHERE board_id IS NOT NULL"
    )

    # ── 4. what was waiting for the sync to record on a Set ──
    for r in conn.execute(
        "SELECT id, current_version_id, pending_draft_id, pending_set_id, pending_major "
        "FROM profile_board WHERE pending_set_id IS NOT NULL ORDER BY id"
    ).fetchall():
        if _record_pending(conn, r, now):
            counts["pending_recorded"] += 1
        else:
            counts["pending_dropped"] += 1

    # ── 5. a Set that is behind its profile catches up ──
    # The sync used to record an edit on a Set only when it was told to; where it never was, the
    # Set's current version names an older version than the profile has active. From here a Set
    # always records what the machine brews, so each such Set gets one minor version naming it.
    for r in conn.execute(
        "SELECT s.id AS set_id, b.current_version_id AS active, v.intent AS intent "
        "FROM sets s JOIN profile_board b ON b.id = s.board_id "
        "JOIN set_versions v ON v.id = s.current_version_id "
        "WHERE v.profile_version_id IS NOT b.current_version_id ORDER BY s.id"
    ).fetchall():
        followed = _follow(
            conn,
            int(r["set_id"]),
            int(r["active"]),
            major=False,
            origin="manual",
            origin_analysis_id=None,
            intent=str(r["intent"] or ""),
            prediction="",
            compares_to=None,
            now=now,
        )
        if followed is not None:
            counts["sets_followed"] += 1
            log.info(
                "set_followed_profile",
                set_id=int(r["set_id"]),
                profile_version_id=int(r["active"]),
                set_version_id=followed,
            )

    # ── the columns that went ──
    _drop_board_columns(conn)
    conn.execute("ALTER TABLE set_versions DROP COLUMN pushed_device_profile_id")
    conn.execute("ALTER TABLE profile_drafts DROP COLUMN cleared_set_version_ids_json")

    _check_end_state(conn, before, counts)
    return dict(counts)


def _profile_of(
    s: _Set,
    versions: list[sqlite3.Row],
    by_id: dict[int, sqlite3.Row],
    owner: dict[int, int | None],
) -> tuple[int | None, int | None]:
    """The version that decided the Set's profile, and the profile."""
    current = None if s.current_version_id is None else by_id.get(s.current_version_id)
    if current is not None and current["profile_version_id"] is not None:
        pv = int(current["profile_version_id"])
        if owner.get(pv) is not None:
            return pv, owner[pv]
    for v in reversed(versions):  # newest first
        if v["profile_version_id"] is not None:
            pv = int(v["profile_version_id"])
            if owner.get(pv) is not None:
                return pv, owner[pv]
    return None, None


def _keeper(group: list[_Set], on_it: list[_Set]) -> _Set:
    """The Set that keeps what several share: a Set that is not archived before one that is, then
    the one whose current version is the shared version (``on_it``), else the most recently
    created. Of several that qualify, the most recent."""

    def recency(s: _Set) -> tuple[str, int]:
        return (s.created_at, s.id)

    pool = [s for s in group if not s.archived] or group
    chosen = [s for s in pool if s in on_it]
    return max(chosen or pool, key=recency)


def _own_profile(
    conn: sqlite3.Connection,
    s: _Set,
    profile_label: str,
    taken: set[str],
    counts: Counter[str],
    now: str,
) -> int:
    """A profile of its own for a Set that shared one: off, not starred, called after both."""
    assert s.anchor is not None
    base = f"{profile_label} — {s.name}"
    label, n = base, 1
    while _name_key(label) in taken:
        n += 1
        label = f"{base} ({n})"
    taken.add(_name_key(label))
    # Its active version is a copy of the Set's current brew; the row needs one to exist, so it
    # names the original for the moment (foreign keys are off) and the copy replaces it.
    cursor = conn.execute(
        "INSERT INTO profile_board (label, current_version_id, origin, on_machine, on_home_screen) "
        "VALUES (?, ?, 'draft', 0, 0)",
        (label, s.anchor),
    )
    board_id = int(cursor.lastrowid or 0)
    current = _own_version(conn, board_id, s.anchor, counts, now)
    conn.execute(
        "UPDATE profile_board SET current_version_id = ? WHERE id = ?", (current, board_id)
    )
    return board_id


def _canonical(document: dict[str, Any]) -> str:
    """What the application hashes at this level: the profile by what it brews, its name
    included (the device-owned fields, annotations and absent values left out, the firmware's
    defaults for a phase transition dropped, whole floats written as integers)."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [strip(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    body = {
        k: v
        for k, v in document.items()
        if k not in ("id", "favorite", "selected") and not k.startswith("_")
    }
    body = strip(body)
    phases = []
    for phase in body.get("phases", []):
        out = dict(phase)
        if not out.get("temperature"):
            out.pop("temperature", None)
        transition = out.get("transition")
        if transition is not None:
            trimmed = dict(transition)
            if trimmed.get("target") in (None, "time"):
                trimmed.pop("target", None)
            if trimmed == {"type": "instant", "duration": 0, "adaptive": False}:
                out.pop("transition", None)
            else:
                out["transition"] = trimmed
        phases.append(out)
    body["phases"] = phases
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _under_name(conn: sqlite3.Connection, version_id: int, label: str) -> tuple[str, str]:
    """A version's document under another name, and its content hash: a copy is a version of the
    profile that holds it, named as that profile is."""
    document = json.loads(
        conn.execute("SELECT json FROM profile_versions WHERE id = ?", (version_id,)).fetchone()[
            "json"
        ]
    )
    document["label"] = label
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text, hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _copy_version(
    conn: sqlite3.Connection,
    board_id: int | None,
    version_id: int,
    label: str,
    counts: Counter[str],
    now: str,
) -> int:
    """A copy of a version under ``label`` (the machine's file is not the copy's: no device
    document), carrying the signature a copied version carries."""
    text, content_hash = _under_name(conn, version_id, label)
    cursor = conn.execute(
        "INSERT INTO profile_versions (board_id, content_hash, label, type, utility, json, "
        " device_json, source, created_at) "
        "SELECT ?, ?, ?, type, utility, ?, NULL, source, created_at FROM profile_versions "
        "WHERE id = ?",
        (board_id, content_hash, label, text, version_id),
    )
    copy = int(cursor.lastrowid or 0)
    counts["signatures_copied"] += _carry_signature(conn, version_id, copy, now)
    return copy


def _own_version(
    conn: sqlite3.Connection, board_id: int, version_id: int, counts: Counter[str], now: str
) -> int:
    """This profile's version of the brew ``version_id`` is: itself when it owns it, the version
    of the same content it already has, else a copy of it under the profile's name (listed, with
    the signature a copied version carries, and not active)."""
    found = conn.execute(
        "SELECT board_id, content_hash, created_at FROM profile_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if found["board_id"] == board_id:
        return version_id
    label = str(
        conn.execute("SELECT label FROM profile_board WHERE id = ?", (board_id,)).fetchone()[0]
    )
    _, renamed = _under_name(conn, version_id, label)
    mine = conn.execute(
        "SELECT id FROM profile_versions WHERE board_id = ? AND content_hash IN (?, ?) "
        "ORDER BY (content_hash = ?) DESC LIMIT 1",
        (board_id, renamed, found["content_hash"], renamed),
    ).fetchone()
    if mine is not None:
        return int(mine["id"])
    copy = _copy_version(conn, board_id, version_id, label, counts, now)
    listed = conn.execute(
        "SELECT added_at, source FROM profile_board_versions WHERE version_id = ? "
        "ORDER BY (board_id = ?) DESC, board_id LIMIT 1",
        (version_id, found["board_id"]),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO profile_board_versions (board_id, version_id, added_at, source) "
        "VALUES (?, ?, ?, ?)",
        (
            board_id,
            copy,
            found["created_at"] if listed is None else listed["added_at"],
            "edit" if listed is None else listed["source"],
        ),
    )
    counts["versions_copied"] += 1
    return copy


def _loose_groups(
    sets: list[_Set], versions_of: dict[int, list[sqlite3.Row]], owner: dict[int, int | None]
) -> dict[int, list[_Set]]:
    """The versions no profile owns that more than one Set with no profile names, each with those
    Sets."""
    named: dict[int, dict[int, _Set]] = {}
    for s in sets:
        if s.board is not None:
            continue
        for v in versions_of.get(s.id, []):
            pv = v["profile_version_id"]
            if pv is not None and owner.get(int(pv)) is None:
                named.setdefault(int(pv), {})[s.id] = s
    return {pv: list(group.values()) for pv, group in named.items() if len(group) > 1}


def _loose_copy(
    conn: sqlite3.Connection, version_id: int, set_name: str, counts: Counter[str], now: str
) -> int:
    """A version no profile owns, for the Set that shared one: the same brew under
    "<name> — <Set>", which is what makes it a version of its own at this level."""
    base = str(
        conn.execute("SELECT label FROM profile_versions WHERE id = ?", (version_id,)).fetchone()[0]
    )
    label, n = f"{base} — {set_name}", 1
    while conn.execute(
        "SELECT 1 FROM profile_versions WHERE board_id IS NULL AND content_hash = ?",
        (_under_name(conn, version_id, label)[1],),
    ).fetchone():
        n += 1
        label = f"{base} — {set_name} ({n})"
    counts["loose_copied"] += 1
    return _copy_version(conn, None, version_id, label, counts, now)


def _carry_signature(conn: sqlite3.Connection, original: int, copy: int, now: str) -> int:
    """What a copied version carries: the original's confirmed expectations, in force on the copy
    and naming where they came from. Nothing when the original has none."""
    rows = conn.execute(
        "SELECT e.* FROM signature_expectations e "
        "JOIN profile_signatures s ON s.id = e.signature_id "
        "WHERE s.profile_version_id = ? AND e.status = 'confirmed' ORDER BY e.position",
        (original,),
    ).fetchall()
    if not rows:
        return 0
    cursor = conn.execute(
        "INSERT INTO profile_signatures (profile_version_id, created_at) VALUES (?, ?)",
        (copy, now),
    )
    signature_id = int(cursor.lastrowid or 0)
    for position, row in enumerate(rows):
        conn.execute(
            "INSERT INTO signature_expectations "
            "(signature_id, position, tier, phase, kind, expression_json, warning_fault, text, "
            " fault, sentence, reason, status, needs_phase, carried_from_id, proposed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 0, ?, ?)",
            (
                signature_id,
                position,
                row["tier"],
                row["phase"],
                row["kind"],
                row["expression_json"],
                row["warning_fault"],
                row["text"],
                row["fault"],
                row["sentence"],
                row["reason"],
                row["id"],
                now,
            ),
        )
    return 1


def _record_pending(conn: sqlite3.Connection, row: sqlite3.Row, now: str) -> bool:
    """Record a waiting put on its Set now, when it is still what the sync would have recorded:
    the Set is the profile's own, the draft is still approved and its version is the profile's
    active version. Returns whether it was recorded; anything else is dropped."""
    set_id = int(row["pending_set_id"])
    own = conn.execute(
        "SELECT current_version_id FROM sets WHERE id = ? AND board_id = ?", (set_id, row["id"])
    ).fetchone()
    if own is None or own["current_version_id"] is None or row["pending_draft_id"] is None:
        return False
    draft = conn.execute(
        "SELECT * FROM profile_drafts WHERE id = ?", (row["pending_draft_id"],)
    ).fetchone()
    if (
        draft is None
        or draft["status"] != "approved"
        or draft["draft_version_id"] != row["current_version_id"]
    ):
        return False
    new = _follow(
        conn,
        set_id,
        int(row["current_version_id"]),
        major=bool(row["pending_major"]),
        origin="analysis"
        if draft["source_analysis_id"] is not None
        else ("chat" if draft["set_id"] == set_id and draft["prediction"] else "manual"),
        origin_analysis_id=draft["source_analysis_id"],
        intent=str(draft["change_summary"])[:500],
        prediction=str(draft["prediction"]) if draft["set_id"] == set_id else "",
        compares_to=draft["compares_to_version_id"],
        now=now,
    )
    if new is None:
        return False
    conn.execute(
        "UPDATE profile_drafts SET recorded_version_id = ? WHERE id = ?", (new, draft["id"])
    )
    return True


def _follow(
    conn: sqlite3.Connection,
    set_id: int,
    profile_version_id: int,
    *,
    major: bool,
    origin: str,
    origin_analysis_id: int | None,
    intent: str,
    prediction: str,
    compares_to: int | None,
    now: str,
) -> int | None:
    """The Set follows its profile: a new version naming ``profile_version_id``, grind, dose and
    yield carried from the current one, named as a minor unless ``major``. Returns the new
    version's id, or ``None`` when the Set's current version names it already."""
    current = conn.execute(
        "SELECT v.* FROM set_versions v JOIN sets s ON s.current_version_id = v.id WHERE s.id = ?",
        (set_id,),
    ).fetchone()
    if current is None or current["profile_version_id"] == profile_version_id:
        return None
    current_major = int(current["version_major"])
    highest_major = int(
        conn.execute(
            "SELECT MAX(version_major) FROM set_versions WHERE set_id = ?", (set_id,)
        ).fetchone()[0]
        or 0
    )
    minor_max = int(
        conn.execute(
            "SELECT MAX(version_minor) FROM set_versions WHERE set_id = ? AND version_major = ?",
            (set_id, current_major),
        ).fetchone()[0]
        or 0
    )
    if major or current_major == 0:
        number = (highest_major + 1, 0)
    else:
        number = (current_major, minor_max + 1)
    if prediction:
        known = conn.execute(
            "SELECT 1 FROM set_versions WHERE id = ? AND set_id = ?", (compares_to, set_id)
        ).fetchone()
        compares_to = compares_to if known is not None else int(current["id"])
    else:
        compares_to = None
    cursor = conn.execute(
        "INSERT INTO set_versions (set_id, parent_version_id, profile_version_id, grind_setting, "
        " grind_value, dose_g, target_yield_g, intent, origin, origin_analysis_id, prediction, "
        " compares_to_version_id, prediction_at, created_at, version_major, version_minor) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            set_id,
            current["id"],
            profile_version_id,
            current["grind_setting"],
            current["grind_value"],
            current["dose_g"],
            current["target_yield_g"],
            intent,
            origin,
            origin_analysis_id,
            prediction,
            compares_to,
            now if prediction else None,
            now,
            number[0],
            number[1],
        ),
    )
    new = int(cursor.lastrowid or 0)
    conn.execute("UPDATE sets SET current_version_id = ? WHERE id = ?", (new, set_id))
    # A proposal made against the version the Set has just moved on from is overtaken.
    conn.execute(
        "UPDATE set_version_proposals SET status = 'stale', decided_at = ? "
        "WHERE set_id = ? AND status = 'proposed'",
        (now, set_id),
    )
    return new


def _drop_board_columns(conn: sqlite3.Connection) -> None:
    """``profile_board`` without ``pending_set_id``, ``pending_major`` and
    ``back_from_set_version_id`` (the 12 steps: the two that are foreign keys cannot be dropped
    in place). Foreign keys are off, and no view names the table."""
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'profile_board'").fetchone()
    conn.execute(_NEW_BOARD_TABLE)
    conn.execute(
        f"INSERT INTO profile_board_new ({_BOARD_COLUMNS}) "  # noqa: S608 - constant column list
        f"SELECT {_BOARD_COLUMNS} FROM profile_board ORDER BY id"
    )
    conn.execute("DROP TABLE profile_board")
    conn.execute("ALTER TABLE profile_board_new RENAME TO profile_board")
    floor = int(seq["seq"]) if seq is not None else 0
    if conn.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'profile_board'").fetchone():
        conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'profile_board'", (floor,)
        )
    elif floor:
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('profile_board', ?)", (floor,)
        )
    for statement in _BOARD_INDEXES:
        conn.execute(statement)


def _totals(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - constant table names
        for table in (
            "sets",
            "set_versions",
            "shots",
            "profile_board",
            "profile_versions",
            "profile_board_versions",
            "profile_signatures",
            "set_version_pins",
            "profile_drafts",
        )
    }


def _check_end_state(
    conn: sqlite3.Connection, before: dict[str, int], counts: Counter[str]
) -> None:
    """What every writer of the application relies on; a failure rolls the upgrade back."""
    foreign = conn.execute(
        "SELECT COUNT(*) FROM set_versions v JOIN sets s ON s.id = v.set_id "
        "JOIN profile_versions pv ON pv.id = v.profile_version_id "
        "WHERE pv.board_id IS NOT s.board_id"
    ).fetchone()[0]
    if foreign:
        raise RuntimeError(f"{foreign} Set version(s) name a version their Set's profile lacks")
    shared = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM sets WHERE board_id IS NOT NULL "
        "GROUP BY board_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    if shared:
        raise RuntimeError(f"{shared} profile(s) still have more than one Set")
    behind = conn.execute(
        "SELECT COUNT(*) FROM sets s JOIN profile_board b ON b.id = s.board_id "
        "JOIN set_versions v ON v.id = s.current_version_id "
        "WHERE v.profile_version_id IS NOT b.current_version_id"
    ).fetchone()[0]
    if behind:
        raise RuntimeError(f"{behind} Set(s) do not brew their profile's active version")
    loose = conn.execute(
        "SELECT COUNT(*) FROM (SELECT v.profile_version_id FROM set_versions v "
        "JOIN profile_versions pv ON pv.id = v.profile_version_id WHERE pv.board_id IS NULL "
        "GROUP BY v.profile_version_id HAVING COUNT(DISTINCT v.set_id) > 1)"
    ).fetchone()[0]
    if loose:
        raise RuntimeError(f"{loose} version(s) no profile owns are named by more than one Set")
    after = _totals(conn)
    expected = dict(before)
    expected["profile_board"] += counts["profiles_copied"]
    expected["profile_versions"] += counts["versions_copied"] + counts["loose_copied"]
    expected["profile_board_versions"] += counts["versions_copied"]
    expected["profile_signatures"] += counts["signatures_copied"]
    expected["set_versions"] += counts["pending_recorded"] + counts["sets_followed"]
    if after != expected:
        raise RuntimeError(f"rows are not accounted for: expected {expected}, found {after}")


def run(conn: sqlite3.Connection) -> dict[str, int]:
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return _run(conn)
    finally:
        conn.row_factory = previous


STEP = Step(level=2, expects=EXPECTS, foreign_keys_off=True, run=run)
