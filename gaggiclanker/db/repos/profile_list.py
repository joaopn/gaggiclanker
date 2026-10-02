"""Fill the profile list from everything the archive already stores, once.

The board began as the profiles a sync keeps on the machine. The list it became has a row for
**every profile a person has had**, switched on or off, each with the versions it has been, so
old developing profiles that no board row names (a pushed draft that was replaced, a deleted
row, an imported file, a version the machine held before the board) come back as profiles of
their own, **off**, and a person switches on the ones they want.

This is a step the application runs at boot, after the migrations, and not SQL in the
migration: versions are grouped by the files and drafts that link them first and by label
second, and expressing that fixed point in one SQL statement would be less readable and less
testable than twenty lines of Python. It is idempotent by a marker row (``profile_list_build``,
migration 0036): it does nothing once it has run, so a person's later choices (a profile they
switched on) are never redone.

The grouping, in order:

1. **Deleted rows come back off.** A tombstoned board row is revived with ``on_machine`` off.
   When a live row has the same label, its versions are merged into that row instead and the
   tombstone stays a tombstone (its machine file is still waiting for the sync). Of several
   tombstones with one label only the newest is revived, the others are merged into it.
2. **An existing row's versions first**: the ones it names (current, previous, the version its
   file held, the one that did not verify, the one it was left from by going back), and the
   versions of the drafts that were put on the machine as the file it stands on or that were
   waiting on it. Drafts that replaced one of its versions, or were replaced by one, follow
   (to a fixed point), unless another profile already has the version.
3. **Then the label** with the app's `` [AI]`` suffix removed: a stored version no row has joins
   the live profile with that label (the exact label first), else a new profile is made for it.

A new profile is **off**, starred, with its newest version active. The synthetic base a
design is diffed against and utility profiles are never made into a profile or a version here
(a utility profile that already has a board row keeps it). A version that exists only because
an open proposal made it is not a version of anything yet: the proposal is shown as such.

Nothing is deleted and nothing is written to the machine.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import structlog

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.base import utc_now
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository, VersionSource
from gaggiclanker.db.repos.profiles import SYNTHETIC_BASE_LABEL
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.models import APP_PROFILE_SUFFIX

__all__ = ["ProfileListBuilder", "stripped_label", "version_source_for_draft"]

log = structlog.get_logger(__name__)

_SUFFIX = APP_PROFILE_SUFFIX.strip()


def stripped_label(label: str) -> str:
    """The label without the app's suffix, which marks agent-made profiles and nothing else."""
    text = label.rstrip()
    if text.endswith(_SUFFIX):
        text = text[: -len(_SUFFIX)]
    return text.rstrip()


def version_source_for_draft(draft: dict[str, Any]) -> VersionSource:
    """Whether a draft's version was the agent's or a person's own edit.

    A draft does not record who made it, so this reads what only an agent's draft carries: a
    Set, an analysis or suggestion it came from, a prediction, a refinement's parent or notes.
    A document typed into the editor carries none of them. A heuristic, said so on the page.
    """
    agent = any(
        draft.get(key) not in (None, "", 0)
        for key in (
            "set_id",
            "source_analysis_id",
            "source_suggestion_id",
            "parent_draft_id",
            "prediction",
            "notes",
        )
    ) or (draft.get("change_summary") or "") not in ("", "manual")
    return "agent" if agent else "edit"


class ProfileListBuilder(Repository):
    """Builds the list once. Reads and writes the board and its versions."""

    def __init__(self, db: Database) -> None:
        super().__init__(db)
        self.board = ProfileBoardRepository(db)

    async def built(self) -> bool:
        return await self.db.fetch_one("SELECT 1 FROM profile_list_build WHERE id = 1") is not None

    async def build(self) -> dict[str, int] | None:
        """Fill the list. ``None`` when it was already built, else counts of what was made."""
        async with self.db.transaction():
            if await self.built():
                return None
            counts = await self._build()
            await self.db.execute(
                "INSERT INTO profile_list_build (id, built_at) VALUES (1, ?)", (utc_now(),)
            )
        log.info("profile_list_built", **counts)
        return counts

    async def _build(self) -> dict[str, int]:
        rows = await self.board.list_rows(include_deleted=True)
        versions = {
            int(r["id"]): dict(r)
            for r in await self.db.fetch_all(
                "SELECT id, label, utility, source, created_at FROM profile_versions"
            )
        }
        drafts = [
            dict(r)
            for r in await self.db.fetch_all(
                "SELECT d.id, d.base_version_id, d.draft_version_id, d.status, d.set_id, "
                "d.source_analysis_id, d.source_suggestion_id, d.parent_draft_id, d.prediction, "
                "d.notes, d.change_summary, d.pushed_device_profile_id, d.replaced_version_id, "
                "pending.board_id AS pending_row "
                "FROM profile_drafts d LEFT JOIN "
                "(SELECT pending_draft_id AS pid, id AS board_id FROM profile_board "
                " WHERE pending_draft_id IS NOT NULL) pending ON pending.pid = d.id "
                "ORDER BY d.id"
            )
        ]
        source_of: dict[int, VersionSource] = {}
        closed_versions: set[int] = set()
        for draft in drafts:
            v = draft["draft_version_id"]
            if v is None:
                continue
            if draft["status"] not in ("draft", "approved"):
                closed_versions.add(int(v))
            source_of.setdefault(int(v), version_source_for_draft(draft))
        shot_versions = {
            int(r["v"])
            for r in await self.db.fetch_all(
                "SELECT DISTINCT profile_version_id AS v FROM shots "
                "WHERE profile_version_id IS NOT NULL"
            )
        }

        def source(version_id: int) -> VersionSource:
            found = versions[version_id]
            if found["source"] == "import":
                return "import"
            if found["source"] == "device":
                return "machine"
            return source_of.get(version_id, "edit")

        def eligible(version_id: int) -> bool:
            found = versions[version_id]
            if found["utility"] or found["label"] == SYNTHETIC_BASE_LABEL:
                return False
            if found["source"] in ("device", "import"):
                return True
            return version_id in closed_versions or version_id in shot_versions

        # 1. The rows that will be profiles, and which row each tombstone merges into.
        live = [r for r in rows if r.deleted_at is None]
        dead = [r for r in rows if r.deleted_at is not None]
        target: dict[int, int] = {r.id: r.id for r in live}
        revived: list[BoardRow] = []
        live_by_label = {r.label: r.id for r in reversed(live)}
        dead_by_label: dict[str, list[BoardRow]] = defaultdict(list)
        for row in dead:
            dead_by_label[row.label].append(row)
        for label, group in dead_by_label.items():
            if label in live_by_label:
                for row in group:
                    target[row.id] = live_by_label[label]
                continue
            keep = max(group, key=lambda r: r.id)
            revived.append(keep)
            for row in group:
                target[row.id] = keep.id
        claimed = {r.device_profile_id for r in live if r.device_profile_id}
        for row in revived:
            file = row.device_profile_id
            clear_file = file is not None and file in claimed
            await self.db.execute(
                "UPDATE profile_board SET deleted_at = NULL, on_machine = 0, "
                "pending_draft_id = NULL, pending_set_id = NULL, pending_major = NULL, "
                "failed_version_id = NULL, "
                "device_profile_id = CASE WHEN ? THEN NULL ELSE device_profile_id END, "
                "device_version_id = CASE WHEN ? THEN NULL ELSE device_version_id END, "
                "updated_at = ? WHERE id = ?",
                (int(clear_file), int(clear_file), utc_now(), row.id),
            )
            if file is not None and not clear_file:
                claimed.add(file)
        by_id = {r.id: r for r in rows}
        profile_rows = [by_id[i] for i in sorted({*(r.id for r in live), *(r.id for r in revived)})]

        # 2. Each profile's versions: what the rows name, then what their drafts made.
        groups: dict[int, set[int]] = {r.id: set() for r in profile_rows}
        owner: dict[int, int] = {}

        def give(row_id: int, version_id: int, *, force: bool = False) -> bool:
            if version_id not in versions:
                return False
            if not force and version_id in owner and owner[version_id] != row_id:
                return False
            groups[row_id].add(version_id)
            owner.setdefault(version_id, row_id)
            return True

        for row in rows:
            row_id = target[row.id]
            for named in (
                row.current_version_id,
                row.previous_version_id,
                row.device_version_id,
                row.failed_version_id,
                row.back_from_version_id,
            ):
                if named is not None:
                    # A row's own versions are always its own, even when another row has the
                    # same document (a machine can hold two identical files).
                    give(row_id, named, force=True)
        files_of: dict[str, int] = {}
        for row in rows:
            if row.device_profile_id:
                files_of.setdefault(row.device_profile_id, target[row.id])
        for draft in drafts:
            version = draft["draft_version_id"]
            if version is None:
                continue
            owner_row = files_of.get(draft["pushed_device_profile_id"] or "")
            if owner_row is None and draft["pending_row"] is not None:
                owner_row = target.get(int(draft["pending_row"]))
            if owner_row is not None:
                give(owner_row, int(version))
        changed = True
        while changed:
            changed = False
            for draft in drafts:
                new = draft["draft_version_id"]
                old = draft["replaced_version_id"]
                if new is None or old is None:
                    continue
                for row_id, held in groups.items():
                    if old in held and new not in held:
                        changed = give(row_id, int(new)) or changed
                    elif new in held and old not in held:
                        changed = give(row_id, int(old)) or changed

        # 3. What is left, by label.
        new_groups: dict[str, list[int]] = {}
        for version_id in sorted(versions):
            if version_id in owner or not eligible(version_id):
                continue
            label = versions[version_id]["label"]
            joins = _live_row_for(label, profile_rows)
            if joins is not None:
                give(joins, version_id)
            else:
                new_groups.setdefault(stripped_label(label), []).append(version_id)

        for row_id in sorted(groups):
            for version_id in sorted(groups[row_id]):
                await self.board.add_version(
                    row_id,
                    version_id,
                    source(version_id),
                    added_at=versions[version_id]["created_at"],
                )
        made = 0
        for key in sorted(new_groups):
            members = new_groups[key]
            newest = max(members)
            created = await self.db.execute(
                "INSERT INTO profile_board (label, current_version_id, on_machine, "
                "on_home_screen, origin, created_at, updated_at) VALUES (?, ?, 0, 1, ?, ?, ?)",
                (
                    versions[newest]["label"],
                    newest,
                    "draft" if versions[newest]["source"] == "draft" else "adopted",
                    utc_now(),
                    utc_now(),
                ),
            )
            new_id = int(created.lastrowid or 0)
            for version_id in members:
                await self.board.add_version(
                    new_id,
                    version_id,
                    source(version_id),
                    added_at=versions[version_id]["created_at"],
                )
            made += 1
        return {
            "profiles_made": made,
            "tombstones_revived": len(revived),
            "tombstones_merged": len([r for r in dead if target[r.id] != r.id]),
            "versions_listed": int(
                await self.db.fetch_value("SELECT COUNT(*) FROM profile_board_versions") or 0
            ),
        }


def _live_row_for(label: str, rows: list[BoardRow]) -> int | None:
    """The profile a stored version with this label joins: exact label first, then by name."""
    for row in rows:
        if row.label == label:
            return row.id
    base = stripped_label(label)
    for row in rows:
        if stripped_label(row.label) == base:
            return row.id
    return None
