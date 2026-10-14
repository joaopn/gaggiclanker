"""Level 2 to level 3: a version is what its profile brews, whatever the profile is called.

What changes in the database:

* Every ``profile_versions.content_hash`` is recomputed without the name (the brew hash).
  Versions of one profile that now share one are one version: the lowest id survives and
  everything that named the others names it. (Versions that belong to none are unique by brew
  and stored label instead; see below.)
* ``profile_board`` gains ``device_label``, the name of the document recorded for its file
  (``NULL`` exactly when ``device_version_id`` is), so that a file read later is compared with
  what the app recorded: its brew and its name. It is filled from the stored label of the
  version the row recorded, which is the name that document had.
* ``v_profiles``, ``v_set_versions`` and ``v_shots`` show the profile's name
  (``profile_board.label``) and fall back to the version's stored label only for a version that
  belongs to no profile.

What is carried forward: every row except the version rows of one profile that differ from
another only in name. Every reference to such a row (the thirteen foreign keys, and the version
ids kept in columns and JSON without one) is moved to the survivor. A profile list entry of a
merged pair becomes one (the earliest ``added_at`` and its source). Signatures: a survivor with
none takes the other's; when both have one the other's expectations are added after the
survivor's last one, except those that are a carried copy of an expectation the survivor already
has, which are dropped (a Set version's override, a review claim's expectation and any
carried-from link on a dropped expectation move to the expectation it was copied from; when
either of the two was rejected, the one kept is rejected, with that rejection's reason and time).

A version on no profile (a draft for a new profile, an imported file, a machine file no profile
stands on) has no profile name, so it stays the document it came as: unique by brew **and** its
stored label (the unique index changes to say so), and only such versions equal in both would
merge, which the previous level's unique index already made one row. So no loose version merges
and no Set moves: the copies the previous step made for Sets that shared a loose version keep
their own names and stay apart.

This module is frozen: it imports nothing from the application, and the brew hash is copied
here as it stood when the step was written. It never begins or commits a transaction and never
sets the level; the runner does.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

import structlog

from gaggiclanker.db.upgrades.step import Step

__all__ = ["STEP", "brew_hash_of_document"]

log = structlog.get_logger(__name__)

#: The fingerprint of ``schemas/level_002.sql``, the database this step upgrades.
EXPECTS = "b7ad217c3a9d"

_BOARD_VERSION_COLUMNS = (
    "current_version_id",
    "device_version_id",
    "failed_version_id",
    "previous_version_id",
    "back_from_version_id",
)

#: ``(table, column)`` for every plain column that names a profile version and is not one of the
#: tables the fold handles itself (the list entries, the pins and the signatures).
_PLAIN_REFERENCES = (
    ("profile_drafts", "base_version_id"),
    ("profile_drafts", "draft_version_id"),
    ("profile_drafts", "replaced_version_id"),
    ("device_profiles", "current_version_id"),
    ("shots", "profile_version_id"),
    ("set_versions", "profile_version_id"),
    *(("profile_board", column) for column in _BOARD_VERSION_COLUMNS),
)

#: ``(table, json column, keys, where)`` for JSON that names a profile version by one of
#: ``keys``; ``where`` limits the rows where the same key means something else elsewhere.
_JSON_REFERENCES = (
    ("sync_events", "data_json", ("version_id",), "kind IN ('profile_changed', 'board_recorded')"),
    ("set_version_proposals", "patch_json", ("profile_version_id",), "1"),
    ("starting_point_runs", "output_json", ("profile_version_id",), "1"),
    ("starting_point_runs", "input_json", ("profile_version_id",), "1"),
)

# The firmware default of a phase transition, spelled once.
_FIRMWARE_DEFAULT_TRANSITION: dict[str, Any] = {"type": "instant", "duration": 0, "adaptive": False}

_V_PROFILES = """
CREATE VIEW v_profiles AS
SELECT pv.id AS profile_version_id,
       COALESCE(pb.label, pv.label) AS label,
       pv.type,
       pv.utility,
       pv.source,
       pv.content_hash,
       pv.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.profile_version_id = pv.id) AS shot_count
  FROM profile_versions pv
  LEFT JOIN profile_board pb ON pb.id = pv.board_id
 WHERE NOT (pv.label = 'Empty baseline'
            AND json_extract(pv.json, '$.description') =
                'An empty baseline, created because the archive held no profile to diff a new draft against.');
"""  # noqa: E501 - the view as schema.sql has it, word for word

_V_SET_VERSIONS = """
CREATE VIEW v_set_versions AS
SELECT v.id AS set_version_id,
       v.set_id,
       st.name AS set_name,
       v.version_major,
       v.version_minor,
       'v' || v.version_major
           || CASE WHEN v.version_minor > 0 THEN '.' || v.version_minor ELSE '' END
           AS version_label,
       (st.current_version_id = v.id) AS is_current,
       v.parent_version_id,
       'v' || par.version_major
           || CASE WHEN par.version_minor > 0 THEN '.' || par.version_minor ELSE '' END
           AS parent_version_label,
       v.profile_version_id,
       COALESCE(pb.label, pv.label) AS profile_label,
       v.grind_setting,
       v.grind_value,
       v.dose_g,
       v.target_yield_g,
       CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
             AND json_extract(pv.json, '$.temperature') > 0
            THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
       v.intent,
       v.origin,
       v.prediction,
       'v' || cmp.version_major
           || CASE WHEN cmp.version_minor > 0 THEN '.' || cmp.version_minor ELSE '' END
           AS compares_to_version_label,
       'v' || res.version_major
           || CASE WHEN res.version_minor > 0 THEN '.' || res.version_minor ELSE '' END
           AS restores_version_label,
       v.outcome,
       v.outcome_note,
       v.created_at,
       (SELECT COUNT(*) FROM shots s WHERE s.set_version_id = v.id) AS shot_count
  FROM set_versions v
  JOIN sets st ON st.id = v.set_id
  LEFT JOIN set_versions par ON par.id = v.parent_version_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
  LEFT JOIN profile_board pb ON pb.id = pv.board_id
  LEFT JOIN set_versions cmp ON cmp.id = v.compares_to_version_id
  LEFT JOIN set_versions res ON res.id = v.restores_version_id;
"""

_V_SHOTS = """
CREATE VIEW v_shots AS
SELECT s.id                                        AS shot_id,
       s.device_id,
       s.started_at,
       s.duration_ms,
       s.duration_ms / 1000.0                      AS duration_s,
       s.profile_version_id,
       COALESCE(pb.label, pv.label)                AS profile_label,
       s.profile_name_on_device,
       COALESCE(s.final_weight_g, s.index_volume_g) AS volume_g,
       s.index_avg_temp_c                          AS avg_temp_c,
       s.index_max_pressure_bar                    AS max_pressure_bar,
       s.index_avg_flow_ml_s                       AS avg_flow_ml_s,
       s.sample_count,
       s.scale_connected,
       s.incomplete,
       s.quarantined,
       s.set_version_id,
       sv.set_id,
       sv.version_major                            AS set_version_major,
       sv.version_minor                            AS set_version_minor,
       'v' || sv.version_major
           || CASE WHEN sv.version_minor > 0 THEN '.' || sv.version_minor ELSE '' END
                                                   AS set_version_label,
       st.name                                     AS set_name,
       sv.grind_setting,
       sv.dose_g                                   AS set_dose_g,
       sv.target_yield_g,
       CASE WHEN json_type(pv.json, '$.temperature') IN ('integer', 'real')
             AND json_extract(pv.json, '$.temperature') > 0
            THEN json_extract(pv.json, '$.temperature') END AS profile_temperature_c,
       b.id                                        AS bean_id,
       b.name                                      AS bean_name,
       b.roast_level,
       b.process,
       b.origin,
       g.id                                        AS grinder_id,
       g.name                                      AS grinder_name,
       j.rating,
       j.balance,
       j.decision,
       j.dose_in_g,
       j.dose_out_g,
       -- Yield over dose: the judgement's dose in when one was entered and the
       -- version's otherwise; the judgement's dose out when entered and the scale's
       -- (else the machine index's) weight otherwise. No dose or no yield, no ratio.
       CASE WHEN COALESCE(NULLIF(j.dose_in_g, 0), sv.dose_g) > 0
             AND COALESCE(j.dose_out_g, s.final_weight_g, s.index_volume_g) IS NOT NULL
            THEN COALESCE(j.dose_out_g, s.final_weight_g, s.index_volume_g)
                 / COALESCE(NULLIF(j.dose_in_g, 0), sv.dose_g) END AS ratio,
       j.notes                                     AS judgement_notes,
       s.diagnostics_json,
       s.synced_at
  FROM shots s
  LEFT JOIN profile_versions pv ON pv.id = s.profile_version_id
  LEFT JOIN profile_board pb    ON pb.id = pv.board_id
  LEFT JOIN set_versions sv     ON sv.id = s.set_version_id
  LEFT JOIN sets st             ON st.id = sv.set_id
  LEFT JOIN beans b             ON b.id = st.bean_id
  LEFT JOIN grinders g          ON g.id = st.grinder_id
  LEFT JOIN shot_judgements j   ON j.shot_id = s.id;
"""


def _normalise_numbers(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _normalise_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise_numbers(v) for v in value]
    return value


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def _canonical_phase(phase: dict[str, Any]) -> dict[str, Any]:
    out = dict(phase)
    if not out.get("temperature"):
        out.pop("temperature", None)
    transition = out.get("transition")
    if transition is not None:
        trimmed = dict(transition)
        if trimmed.get("target") in (None, "time"):
            trimmed.pop("target", None)
        if _normalise_numbers(trimmed) == _FIRMWARE_DEFAULT_TRANSITION:
            out.pop("transition", None)
        else:
            out["transition"] = trimmed
    return out


def brew_hash_of_document(document: dict[str, Any]) -> str:
    """The brew hash of a stored version document: its canonical form without the name.

    The stored ``json`` is already canonical (it is what the application wrote); this drops the
    label and canonicalises again, which gives the application's own ``brew_hash`` for the same
    profile (a test holds the two together over every seeded database).
    """
    body = _drop_none(
        {
            k: v
            for k, v in document.items()
            if k not in ("id", "favorite", "selected", "annotations", "label")
        }
    )
    body["phases"] = [_canonical_phase(phase) for phase in body["phases"]]
    text = json.dumps(
        _normalise_numbers(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_ids(value: Any, keys: tuple[str, ...], mapping: dict[int, int]) -> tuple[Any, bool]:
    """``value`` with every integer under one of ``keys`` that is a merged id made its survivor."""
    changed = False
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in keys and isinstance(item, int) and not isinstance(item, bool):
                new = mapping.get(item, item)
                changed = changed or new != item
                out[key] = new
            else:
                new_item, did = _replace_ids(item, keys, mapping)
                changed = changed or did
                out[key] = new_item
        return out, changed
    if isinstance(value, list):
        items = []
        for item in value:
            new_item, did = _replace_ids(item, keys, mapping)
            changed = changed or did
            items.append(new_item)
        return items, changed
    return value, False


def _run(conn: sqlite3.Connection) -> dict[str, int]:
    counts: Counter[str] = Counter()
    versions = {int(r["id"]): r for r in conn.execute("SELECT * FROM profile_versions ORDER BY id")}
    counts["versions"] = len(versions)

    # ── the recorded name of each profile's file, before any label stops being the name ──
    conn.execute("ALTER TABLE profile_board ADD COLUMN device_label TEXT")
    cursor = conn.execute(
        "UPDATE profile_board SET device_label = "
        "(SELECT v.label FROM profile_versions v WHERE v.id = profile_board.device_version_id) "
        "WHERE device_version_id IS NOT NULL"
    )
    counts["device_labels"] = cursor.rowcount

    # ── the new identity, and which versions it makes one ─────────────────────────────
    new_hash = {i: brew_hash_of_document(json.loads(r["json"])) for i, r in versions.items()}
    # A profile's versions are one by brew; a version on no profile is its brew and its label.
    groups: dict[tuple[int | None, str, str], list[int]] = defaultdict(list)
    for version_id, row in versions.items():
        owner = row["board_id"]
        groups[(owner, new_hash[version_id], "" if owner is not None else row["label"])].append(
            version_id
        )
    merges = {key: ids for key, ids in groups.items() if len(ids) > 1}
    counts["rehashed"] = len(versions)

    survivor: dict[int, int] = {}
    for ids in merges.values():
        for other in ids[1:]:
            survivor[other] = ids[0]

    # ── fold each merged-away version into its survivor ──────────────────────────────
    for (owner, _, _), ids in sorted(merges.items(), key=lambda item: item[1]):
        moved_before = counts["expectations_moved"] + counts["signatures_taken"]
        for other in ids[1:]:
            _fold(conn, ids[0], other, counts)
        counts["merged_owned" if owner is not None else "merged_ownerless"] += len(ids) - 1
        log.info(
            "profile_versions_merged",
            survivor=ids[0],
            merged=ids[1:],
            owner=owner,
            signature_rows_moved=counts["expectations_moved"]
            + counts["signatures_taken"]
            - moved_before,
        )
    _repoint_json(conn, survivor, counts)
    for other in survivor:
        conn.execute("DELETE FROM profile_versions WHERE id = ?", (other,))
    # Loose versions are unique by brew and label from here on (the old index said brew alone).
    conn.execute("DROP INDEX idx_profile_versions_unowned")
    for version_id, digest in new_hash.items():
        if version_id not in survivor:
            conn.execute(
                "UPDATE profile_versions SET content_hash = ? WHERE id = ?", (digest, version_id)
            )
    conn.execute(
        "CREATE UNIQUE INDEX idx_profile_versions_unowned "
        "ON profile_versions(content_hash, label) WHERE board_id IS NULL"
    )

    # ── the views show the profile's name ────────────────────────────────────────────
    for name, sql in (
        ("v_profiles", _V_PROFILES),
        ("v_set_versions", _V_SET_VERSIONS),
        ("v_shots", _V_SHOTS),
    ):
        conn.execute(f"DROP VIEW {name}")
        conn.execute(sql)

    _check_end_state(conn)
    return dict(counts)


def _fold(conn: sqlite3.Connection, keep: int, gone: int, counts: Counter[str]) -> None:
    """Everything that named ``gone`` names ``keep``."""
    if (
        conn.execute(
            "SELECT 1 FROM set_version_pins WHERE profile_version_id = ?", (gone,)
        ).fetchone()
        and conn.execute(
            "SELECT 1 FROM set_version_pins WHERE profile_version_id = ?", (keep,)
        ).fetchone()
    ):
        raise RuntimeError(
            f"profile versions {keep} and {gone} are the copies of two different pins and would "
            "become one"
        )
    cursor = conn.execute(
        "UPDATE set_version_pins SET profile_version_id = ? WHERE profile_version_id = ?",
        (keep, gone),
    )
    counts["repointed_set_version_pins"] += cursor.rowcount
    for table, column in _PLAIN_REFERENCES:
        cursor = conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608 - constant names
            (keep, gone),
        )
        counts[f"repointed_{table}"] += cursor.rowcount
    for entry in conn.execute(
        "SELECT board_id, added_at, source FROM profile_board_versions WHERE version_id = ?",
        (gone,),
    ).fetchall():
        kept = conn.execute(
            "SELECT added_at FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
            (entry["board_id"], keep),
        ).fetchone()
        if kept is None:
            conn.execute(
                "UPDATE profile_board_versions SET version_id = ? "
                "WHERE board_id = ? AND version_id = ?",
                (keep, entry["board_id"], gone),
            )
            continue
        if entry["added_at"] < kept["added_at"]:
            conn.execute(
                "UPDATE profile_board_versions SET added_at = ?, source = ? "
                "WHERE board_id = ? AND version_id = ?",
                (entry["added_at"], entry["source"], entry["board_id"], keep),
            )
        conn.execute(
            "DELETE FROM profile_board_versions WHERE board_id = ? AND version_id = ?",
            (entry["board_id"], gone),
        )
        counts["list_entries_merged"] += 1
    _fold_signature(conn, keep, gone, counts)


def _fold_signature(conn: sqlite3.Connection, keep: int, gone: int, counts: Counter[str]) -> None:
    theirs = conn.execute(
        "SELECT id FROM profile_signatures WHERE profile_version_id = ?", (gone,)
    ).fetchone()
    if theirs is None:
        return
    ours = conn.execute(
        "SELECT id FROM profile_signatures WHERE profile_version_id = ?", (keep,)
    ).fetchone()
    if ours is None:
        conn.execute(
            "UPDATE profile_signatures SET profile_version_id = ? WHERE id = ?",
            (keep, theirs["id"]),
        )
        counts["signatures_taken"] += 1
        return
    counts["signatures_unioned"] += 1
    ours_ids = {
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM signature_expectations WHERE signature_id = ?", (ours["id"],)
        )
    }
    last = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM signature_expectations WHERE signature_id = ?",
        (ours["id"],),
    ).fetchone()[0]
    for exp in conn.execute(
        "SELECT id, carried_from_id, status, reject_reason, answered_at "
        "FROM signature_expectations WHERE signature_id = ? ORDER BY position",
        (theirs["id"],),
    ).fetchall():
        if exp["carried_from_id"] in ours_ids:
            # A carried copy of an expectation the survivor has: it stays only as that one.
            source = int(exp["carried_from_id"])
            if exp["status"] == "rejected":
                # A rejection is never lost: the one kept is rejected, for that reason, then.
                conn.execute(
                    "UPDATE signature_expectations SET status = 'rejected', reject_reason = ?, "
                    "answered_at = ? WHERE id = ? AND status != 'rejected'",
                    (exp["reject_reason"], exp["answered_at"], source),
                )
            conn.execute(
                "UPDATE review_claims SET expectation_id = ? WHERE expectation_id = ?",
                (source, exp["id"]),
            )
            conn.execute(
                "UPDATE set_version_signature_overrides SET expectation_id = ? "
                "WHERE expectation_id = ?",
                (source, exp["id"]),
            )
            conn.execute(
                "UPDATE signature_expectations SET carried_from_id = ? WHERE carried_from_id = ?",
                (source, exp["id"]),
            )
            counts["expectations_dropped_as_carried_copies"] += 1
            continue
        last += 1
        conn.execute(
            "UPDATE signature_expectations SET signature_id = ?, position = ? WHERE id = ?",
            (ours["id"], last, exp["id"]),
        )
        counts["expectations_moved"] += 1
    conn.execute("DELETE FROM profile_signatures WHERE id = ?", (theirs["id"],))


def _repoint_json(conn: sqlite3.Connection, survivor: dict[int, int], counts: Counter[str]) -> None:
    if not survivor:
        return
    for table, column, keys, where in _JSON_REFERENCES:
        for row in conn.execute(
            f"SELECT rowid AS rid, {column} AS doc FROM {table} "  # noqa: S608 - constant names
            f"WHERE {column} IS NOT NULL AND {where}"
        ).fetchall():
            try:
                document = json.loads(row["doc"])
            except ValueError:
                continue
            fixed, changed = _replace_ids(document, keys, survivor)
            if changed:
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",  # noqa: S608 - constant names
                    (json.dumps(fixed, separators=(",", ":"), ensure_ascii=False), row["rid"]),
                )
                counts[f"json_repointed_{table}"] += 1


def _check_end_state(conn: sqlite3.Connection) -> None:
    """What every writer of the application relies on; a failure rolls the upgrade back."""
    if conn.execute(
        "SELECT COUNT(*) FROM profile_board_versions pbv "
        "JOIN profile_versions pv ON pv.id = pbv.version_id WHERE pv.board_id IS NOT pbv.board_id"
    ).fetchone()[0]:
        raise RuntimeError("a profile lists a version it does not own")
    for column in _BOARD_VERSION_COLUMNS:
        if conn.execute(
            f"SELECT COUNT(*) FROM profile_board pb "  # noqa: S608 - constant column names
            f"JOIN profile_versions pv ON pv.id = pb.{column} WHERE pv.board_id IS NOT pb.id"
        ).fetchone()[0]:
            raise RuntimeError(f"a profile names a version in {column} that it does not own")
    if conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM profile_versions GROUP BY board_id, content_hash, "
        "CASE WHEN board_id IS NULL THEN label END HAVING COUNT(*) > 1)"
    ).fetchone()[0]:
        raise RuntimeError(
            "two versions of one profile, or two loose ones of one name, share a brew"
        )
    for row in conn.execute("SELECT id, json, content_hash FROM profile_versions"):
        if brew_hash_of_document(json.loads(row["json"])) != row["content_hash"]:
            raise RuntimeError(f"profile version {row['id']} does not have its brew hash")
    if conn.execute(
        "SELECT COUNT(*) FROM profile_board WHERE (device_version_id IS NULL) != "
        "(device_label IS NULL)"
    ).fetchone()[0]:
        raise RuntimeError("a profile records a file's version without its name, or the reverse")
    if conn.execute(
        "SELECT COUNT(*) FROM set_versions v JOIN sets s ON s.id = v.set_id "
        "JOIN profile_versions pv ON pv.id = v.profile_version_id "
        "WHERE s.board_id IS NOT NULL AND pv.board_id IS NOT s.board_id"
    ).fetchone()[0]:
        raise RuntimeError("a Set names a version its profile does not own")


def run(conn: sqlite3.Connection) -> dict[str, int]:
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return _run(conn)
    finally:
        conn.row_factory = previous


STEP = Step(level=3, expects=EXPECTS, foreign_keys_off=False, run=run)
