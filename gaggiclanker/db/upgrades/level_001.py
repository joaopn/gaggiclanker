"""Level 0 to level 1: a profile owns its versions, and drafts and machine files store names.

What changes in the database:

* ``profile_versions`` is rebuilt without the column-level ``UNIQUE`` on ``content_hash`` and
  gains ``board_id``, the profile the version belongs to. A version is unique by content per
  profile, and among the versions that belong to none.
* ``profile_drafts`` gains ``label`` (the draft's own name), ``new_profile`` (what the draft is:
  a new profile called ``label``, or a change) and ``destination_board_id`` (the profile a
  change goes to; NULL for a new profile, and for a change whose profile was not on the list).
* ``device_profiles`` gains ``label`` (the name the machine's file had).

What is carried forward: every row. Where two profiles listed one version (a Duplicate or a
copy made before versions had owners), the profile that is served today keeps the version and
each other profile gets a copy of it with the same brew and the same signature; rows that name
the version for any other reason (shots, Set versions, pins, drafts, other files) keep pointing
at the original, which is what they were served as.

This module is frozen: it imports nothing from the application, and what it needs from live
code (the placement rule, the name comparison) is copied here as it stood when the step was
written. It never begins or commits a transaction and never sets the level; the runner does.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

from gaggiclanker.db.upgrades.step import Step

__all__ = ["STEP"]

#: The fingerprint of ``schemas/level_000.sql``, the database this step upgrades.
EXPECTS = "e1d5cd1b426e"

#: A profile-list row's own columns that name a profile version.
_BOARD_VERSION_COLUMNS = (
    "current_version_id",
    "device_version_id",
    "failed_version_id",
    "previous_version_id",
    "back_from_version_id",
)

#: ``profile_versions`` as it is at level 1 (the schema file says what each column means).
_NEW_TABLE = """
CREATE TABLE profile_versions_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT    NOT NULL,
    board_id     INTEGER REFERENCES profile_board(id),
    label        TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    utility      INTEGER NOT NULL DEFAULT 0,
    json         TEXT    NOT NULL,
    device_json  TEXT,
    source       TEXT    NOT NULL DEFAULT 'device'
                 CHECK (source IN ('device', 'draft', 'import')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT
"""

_NEW_FILES_TABLE = """
CREATE TABLE device_profiles_new (
    device_id          TEXT    NOT NULL,
    current_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    favorite           INTEGER NOT NULL DEFAULT 0,
    selected           INTEGER NOT NULL DEFAULT 0,
    position           INTEGER,
    first_seen_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at         TEXT,
    label              TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (device_id)
) STRICT
"""

_INDEXES = (
    "CREATE UNIQUE INDEX idx_profile_versions_owned "
    "ON profile_versions(board_id, content_hash) WHERE board_id IS NOT NULL",
    "CREATE UNIQUE INDEX idx_profile_versions_unowned "
    "ON profile_versions(content_hash) WHERE board_id IS NULL",
    "CREATE INDEX idx_profile_versions_board ON profile_versions(board_id)",
)

#: Statuses of a draft still waiting for a person, whose destination is worked out. A closed
#: draft's destination is history.
_OPEN = ("draft", "approved")


def _name_key(label: str) -> str:
    """What "the same name" meant to the placement rule: no surrounding space, case ignored."""
    return label.strip().casefold()


@dataclass(slots=True)
class _Board:
    id: int
    label: str
    deleted: bool
    device_profile_id: str | None
    columns: dict[str, int | None]


class _Placement:
    """The placement rule of drafts as it stood when this step was written, on level 0 rows.

    A draft continued the live profile whose name was its label (case and surrounding space
    ignored), found through its base: the live profile listing the base version, else the live
    profile standing on a live file that holds it. A Set's profile came first when its name was
    the draft's. A name another live profile had, on anything that did not continue one, was
    taken, and a profile that is a pin's frozen copy was not continued. The answer is the profile
    id, or nothing for a new profile or a refused draft.
    """

    def __init__(self, conn: sqlite3.Connection, boards: dict[int, _Board]) -> None:
        self.conn = conn
        self.live = [b for b in boards.values() if not b.deleted]

    def _by_device(self, device_id: str) -> _Board | None:
        for board in self.live:
            if board.device_profile_id == device_id:
                return board
        return None

    def _base_profile(self, version_id: int) -> _Board | None:
        row = self.conn.execute(
            "SELECT b.id FROM profile_board b JOIN profile_board_versions v ON v.board_id = b.id "
            "WHERE v.version_id = ? AND b.deleted_at IS NULL ORDER BY b.id LIMIT 1",
            (version_id,),
        ).fetchone()
        if row is not None:
            return next(b for b in self.live if b.id == row["id"])
        device = self.conn.execute(
            "SELECT device_id FROM device_profiles "
            "WHERE current_version_id = ? AND deleted_at IS NULL LIMIT 1",
            (version_id,),
        ).fetchone()
        return None if device is None else self._by_device(str(device["device_id"]))

    def _by_set(self, set_id: int) -> _Board | None:
        current = self.conn.execute(
            "SELECT v.profile_version_id AS pv FROM set_versions v "
            "JOIN sets cs ON cs.id = v.set_id "
            "WHERE v.id = cs.current_version_id AND v.set_id = ?",
            (set_id,),
        ).fetchone()
        if current is None or current["pv"] is None:
            return None
        pushed = self.conn.execute(
            "SELECT pushed_device_profile_id AS d FROM set_versions "
            "WHERE set_id = ? AND profile_version_id = ? AND pushed_device_profile_id IS NOT NULL "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (set_id, current["pv"]),
        ).fetchone()
        found = None if pushed is None else self._by_device(str(pushed["d"]))
        if found is not None:
            return found
        row = self.conn.execute(
            "SELECT b.id FROM profile_board b JOIN profile_board_versions v ON v.board_id = b.id "
            "WHERE v.version_id = ? AND b.deleted_at IS NULL ORDER BY b.id LIMIT 1",
            (current["pv"],),
        ).fetchone()
        return None if row is None else next(b for b in self.live if b.id == row["id"])

    def _by_name_key(self, label: str) -> _Board | None:
        wanted = _name_key(label)
        return next((b for b in self.live if _name_key(b.label) == wanted), None)

    def _frozen(self, board_id: int) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM set_version_pins p "
                "JOIN set_versions v ON v.id = p.set_version_id "
                "JOIN sets s ON s.id = v.set_id "
                "JOIN profile_versions cpv ON cpv.id = p.profile_version_id "
                "WHERE p.board_id = ? AND p.unpinned_at IS NULL",
                (board_id,),
            ).fetchone()
            is not None
        )

    def destination(
        self, *, label: str, base_version_id: int | None, base_label: str | None, set_id: int | None
    ) -> tuple[int | None, str]:
        """The profile id (or ``None``) and what the rule said: ``profile`` (a change to it),
        ``orphan`` (a change that no live profile has the base of), ``frozen`` (a change to a
        pinned profile), ``new`` or ``taken`` (a new profile, free or not)."""
        key = _name_key(label)
        own = None if base_version_id is None else self._base_profile(base_version_id)
        if own is not None:
            continues = key == _name_key(own.label)
        else:
            continues = base_label is not None and key == _name_key(base_label)
        if not continues:
            taken = self._by_name_key(label)
            return None, "new" if taken is None else "taken"
        owner = own
        if set_id is not None:
            theirs = self._by_set(set_id)
            if theirs is not None and _name_key(theirs.label) == key:
                owner = theirs
        if owner is None:
            owner = self._by_name_key(label)
        if owner is None:
            return None, "orphan"
        if self._frozen(owner.id):
            return None, "frozen"
        return owner.id, "profile"


def _run(conn: sqlite3.Connection) -> dict[str, int]:
    counts: Counter[str] = Counter()

    boards = {
        int(r["id"]): _Board(
            id=int(r["id"]),
            label=str(r["label"]),
            deleted=r["deleted_at"] is not None,
            device_profile_id=r["device_profile_id"],
            columns={name: r[name] for name in _BOARD_VERSION_COLUMNS},
        )
        for r in conn.execute(
            f"SELECT id, label, deleted_at, device_profile_id, {', '.join(_BOARD_VERSION_COLUMNS)} "  # noqa: S608 - constant column names
            "FROM profile_board ORDER BY id"
        )
    }
    versions = {int(r["id"]): r for r in conn.execute("SELECT * FROM profile_versions ORDER BY id")}

    # ── 1. who owns each version ─────────────────────────────────────
    # Of the profiles that list it, the one for which it is the **active** version, else the one
    # whose machine file maps to it, else a live one before a deleted one and the lowest id (the
    # answer the profile list has always served for "which profile is this version"). Shots and
    # Set versions keep pointing at the version, so they stay with the profile that is using it.
    # A version nobody lists but a row names in one of its own columns is that row's (the lowest
    # id); anything else has no owner.
    file_holds = {
        str(r["device_id"]): int(r["current_version_id"])
        for r in conn.execute("SELECT device_id, current_version_id FROM device_profiles")
    }
    listers: dict[int, list[int]] = {}
    for r in conn.execute(
        "SELECT pbv.version_id AS v, pbv.board_id AS b FROM profile_board_versions pbv "
        "JOIN profile_board pb ON pb.id = pbv.board_id "
        "ORDER BY pbv.version_id, (pb.deleted_at IS NOT NULL), pbv.board_id"
    ):
        listers.setdefault(int(r["v"]), []).append(int(r["b"]))

    def claim(version_id: int, board_id: int) -> tuple[bool, bool, bool, int]:
        board = boards[board_id]
        active = board.columns["current_version_id"] == version_id
        maps = board.device_profile_id is not None and (
            file_holds.get(board.device_profile_id) == version_id
        )
        return (not active, not maps, board.deleted, board_id)

    namers: dict[int, list[int]] = {}
    for board in boards.values():
        for value in board.columns.values():
            if value is not None and board.id not in namers.setdefault(int(value), []):
                namers[int(value)].append(board.id)
    owner: dict[int, int] = {}
    for version_id in versions:
        if listers.get(version_id):
            owner[version_id] = min(listers[version_id], key=lambda b: claim(version_id, b))
        elif namers.get(version_id):
            owner[version_id] = namers[version_id][0]
    counts["versions"] = len(versions)
    counts["owned"] = len(owner)
    counts["listed_owners"] = sum(1 for v in owner if listers.get(v))
    counts["named_owners"] = len(owner) - counts["listed_owners"]

    # ── 3, 4, 5 read the level-0 rows, so they are worked out before anything moves ──
    labels = {version_id: str(row["label"]) for version_id, row in versions.items()}
    draft_labels: dict[int, str] = {}
    destinations: dict[int, int] = {}
    new_profiles: set[int] = set()
    placement = _Placement(conn, boards)
    for d in conn.execute(
        "SELECT id, status, base_version_id, draft_version_id, is_new, set_id "
        "FROM profile_drafts ORDER BY id"
    ).fetchall():
        shown = d["draft_version_id"] if d["draft_version_id"] is not None else d["base_version_id"]
        label = labels.get(int(shown), "")
        draft_labels[int(d["id"])] = label
        base_label = labels.get(int(d["base_version_id"]))
        if d["status"] in _OPEN:
            found, why = placement.destination(
                label=label,
                base_version_id=None if d["is_new"] else int(d["base_version_id"]),
                base_label=None if d["is_new"] else base_label,
                set_id=d["set_id"],
            )
            counts["open_drafts"] += 1
            counts[f"open_{why}"] += 1
            if found is not None:
                destinations[int(d["id"])] = found
            if why in ("new", "taken"):
                new_profiles.add(int(d["id"]))
        else:
            # History, read for display only: a new profile when it was written from scratch or
            # called something else than the profile it started from.
            if d["is_new"] or _name_key(label) != _name_key(base_label or ""):
                new_profiles.add(int(d["id"]))
            if d["draft_version_id"] is not None and listers.get(int(d["draft_version_id"])):
                destinations[int(d["id"])] = owner[int(d["draft_version_id"])]
                counts["closed_to_a_profile"] += 1
    counts["drafts"] = len(draft_labels)

    # ── 2. versions two or more profiles refer to: a copy for every profile but the owner ──
    # "Refer to" is listing the version, or naming it in a column of the profile's own row, so
    # that every profile ends up with versions it owns and none it only borrows.
    copies: dict[tuple[int, int], int] = {}
    for version_id in sorted(owner):
        referring = sorted(set(listers.get(version_id, [])) | set(namers.get(version_id, [])))
        for board_id in referring:
            if board_id != owner[version_id]:
                copies[(board_id, version_id)] = 0
                kind = "split_listed" if board_id in listers.get(version_id, []) else "split_named"
                counts[kind] += 1

    # ── the rebuild of profile_versions (the 12 steps; foreign keys are off) ──
    views = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'view' ORDER BY rowid"
    ).fetchall()
    for view in views:
        conn.execute(f'DROP VIEW "{view["name"]}"')
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'profile_versions'").fetchone()
    conn.execute(_NEW_TABLE)
    columns = "content_hash, label, type, utility, json, device_json, source, created_at"
    for version_id in versions:
        conn.execute(
            f"INSERT INTO profile_versions_new (id, board_id, {columns}) "  # noqa: S608 - constant column list
            f"SELECT id, ?, {columns} FROM profile_versions WHERE id = ?",
            (owner.get(version_id), version_id),
        )
    conn.execute("DROP TABLE profile_versions")
    conn.execute("ALTER TABLE profile_versions_new RENAME TO profile_versions")
    # The counter only ever goes up, whatever was deleted from the old table.
    floor = int(seq["seq"]) if seq is not None else 0
    if conn.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'profile_versions'").fetchone():
        conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'profile_versions'", (floor,)
        )
    elif floor:
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('profile_versions', ?)", (floor,)
        )
    for statement in _INDEXES:
        conn.execute(statement)
    for view in views:
        conn.execute(view["sql"])

    # ── 2 (continued). the copies, and what each profile that got one points at ──
    for (board_id, version_id), _ in sorted(copies.items()):
        cursor = conn.execute(
            f"INSERT INTO profile_versions (board_id, {columns}) "  # noqa: S608 - constant column list
            f"SELECT ?, {columns} FROM profile_versions WHERE id = ?",
            (board_id, version_id),
        )
        copy_id = int(cursor.lastrowid or 0)
        copies[(board_id, version_id)] = copy_id
        conn.execute(
            "UPDATE profile_board_versions SET version_id = ? "
            "WHERE board_id = ? AND version_id = ?",
            (copy_id, board_id, version_id),
        )
        for column in _BOARD_VERSION_COLUMNS:
            conn.execute(
                f"UPDATE profile_board SET {column} = ? WHERE id = ? AND {column} = ?",  # noqa: S608 - constant column names
                (copy_id, board_id, version_id),
            )
        stood_on = boards[board_id].device_profile_id
        owner_stands_on = boards[owner[version_id]].device_profile_id
        if stood_on is not None and stood_on != owner_stands_on:
            conn.execute(
                "UPDATE device_profiles SET current_version_id = ? "
                "WHERE device_id = ? AND current_version_id = ?",
                (copy_id, stood_on, version_id),
            )
        counts["signatures_copied"] += _copy_signature(conn, version_id, copy_id)

    # ── the new columns, and their fill ──────────────────────────────
    conn.execute("ALTER TABLE profile_drafts ADD COLUMN label TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "ALTER TABLE profile_drafts ADD COLUMN destination_board_id INTEGER "
        "REFERENCES profile_board(id)"
    )
    conn.execute(
        "ALTER TABLE profile_drafts ADD COLUMN new_profile INTEGER NOT NULL DEFAULT 0 "
        "CHECK (new_profile IN (0, 1))"
    )
    for draft_id, label in draft_labels.items():
        conn.execute("UPDATE profile_drafts SET label = ? WHERE id = ?", (label, draft_id))
    for draft_id in new_profiles:
        conn.execute("UPDATE profile_drafts SET new_profile = 1 WHERE id = ?", (draft_id,))
    for draft_id, destination in destinations.items():
        conn.execute(
            "UPDATE profile_drafts SET destination_board_id = ? WHERE id = ?",
            (destination, draft_id),
        )
    # `device_profiles` ends in a table constraint, which `ADD COLUMN` would write the new column
    # after (the schema file cannot be written that way), so it is rebuilt with the column in
    # place; each file is named by the version it maps to.
    conn.execute(_NEW_FILES_TABLE)
    cursor = conn.execute(
        "INSERT INTO device_profiles_new (device_id, current_version_id, favorite, selected, "
        " position, first_seen_at, last_seen_at, deleted_at, label) "
        "SELECT d.device_id, d.current_version_id, d.favorite, d.selected, d.position, "
        " d.first_seen_at, d.last_seen_at, d.deleted_at, "
        " (SELECT v.label FROM profile_versions v WHERE v.id = d.current_version_id) "
        "FROM device_profiles d ORDER BY d.rowid"
    )
    conn.execute("DROP TABLE device_profiles")
    conn.execute("ALTER TABLE device_profiles_new RENAME TO device_profiles")
    conn.execute("CREATE INDEX idx_device_profiles_version ON device_profiles(current_version_id)")
    counts["file_labels"] = cursor.rowcount

    _check_end_state(conn)
    return dict(counts)


def _copy_signature(conn: sqlite3.Connection, original: int, copy: int) -> int:
    """Give a copy the original's signature: every expectation as it stands, naming its source.

    The copy's profile is judged as it was a moment ago, and what a person rejected stays
    rejected. A Set version's overrides stay on the original's expectations, since Set versions
    name the original.
    """
    found = conn.execute(
        "SELECT id, created_at FROM profile_signatures WHERE profile_version_id = ?", (original,)
    ).fetchone()
    if found is None:
        return 0
    cursor = conn.execute(
        "INSERT INTO profile_signatures (profile_version_id, created_at) VALUES (?, ?)",
        (copy, found["created_at"]),
    )
    signature_id = int(cursor.lastrowid or 0)
    conn.execute(
        "INSERT INTO signature_expectations "
        "(signature_id, position, tier, phase, kind, expression_json, warning_fault, text, "
        " fault, sentence, reason, status, reject_reason, needs_phase, proposed_by_thread_id, "
        " proposed_by_draft_id, carried_from_id, proposed_at, answered_at) "
        "SELECT ?, position, tier, phase, kind, expression_json, warning_fault, text, fault, "
        " sentence, reason, status, reject_reason, needs_phase, proposed_by_thread_id, "
        " proposed_by_draft_id, id, proposed_at, answered_at "
        "FROM signature_expectations WHERE signature_id = ? ORDER BY position",
        (signature_id, found["id"]),
    )
    return 1


def _check_end_state(conn: sqlite3.Connection) -> None:
    """What every writer of the application relies on; a failure rolls the upgrade back."""
    unowned = conn.execute(
        "SELECT COUNT(*) FROM profile_board_versions pbv "
        "JOIN profile_versions pv ON pv.id = pbv.version_id "
        "WHERE pv.board_id IS NOT pbv.board_id"
    ).fetchone()[0]
    if unowned:
        raise RuntimeError(
            f"{unowned} listed version(s) are not owned by the profile that lists them"
        )
    for column in _BOARD_VERSION_COLUMNS:
        stray = conn.execute(
            f"SELECT COUNT(*) FROM profile_board pb "  # noqa: S608 - constant column names
            f"JOIN profile_versions pv ON pv.id = pb.{column} "
            "WHERE pv.board_id IS NOT pb.id"
        ).fetchone()[0]
        if stray:
            raise RuntimeError(f"{stray} row(s) name a version in {column} that they do not own")
    twice = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM profile_versions GROUP BY board_id, content_hash "
        "HAVING COUNT(*) > 1 AND board_id IS NOT NULL)"
    ).fetchone()[0]
    if twice:
        raise RuntimeError(f"{twice} profile(s) hold the same brew twice")


def run(conn: sqlite3.Connection) -> dict[str, int]:
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return _run(conn)
    finally:
        conn.row_factory = previous


STEP = Step(level=1, expects=EXPECTS, foreign_keys_off=True, run=run)
