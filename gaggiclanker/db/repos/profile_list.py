"""Fill the profile list from everything the archive already stores, once.

The board began as the profiles a sync keeps on the machine. The list it became has a row for
**every profile a person has had**, switched on or off, each with the versions it has been, so
old developing profiles that no board row names (a pushed draft that was replaced, a deleted
row, an imported file, a version the machine held before the board) come back as profiles of
their own, **off**, and a person switches on the ones they want.

This is a step the application runs at boot, after the schema is ready, and not SQL:
versions are grouped by the files and drafts that link them first and by label
second, and expressing that fixed point in one SQL statement would be less readable and less
testable than twenty lines of Python. It is idempotent by a marker row
(``profile_list_build``): it does nothing once it has run, so a person's later choices (a
profile they switched on) are never redone.

The grouping, in order:

1. **Deleted rows come back off.** A tombstoned board row is revived with ``on_machine`` off.
   When a live row has the same label, its versions are merged into that row instead and the
   tombstone stays a tombstone (its machine file is still waiting for the sync). Of several
   tombstones with one label only the newest is revived, the others are merged into it.
2. **An existing row's versions first**: the ones it names (current, previous, the version its
   file held, the one that did not verify, the one it was left from by going back), and the
   versions of the drafts that were put on the machine as the file it stands on or that were
   waiting on it.
3. **The stored drafts, replayed oldest first, through the rule history was written under**
   (``lineage_owner``, exact names): a version lands in the profile a put of it would then have
   landed in (the Set's chain, or the profile whose version list has its base when the name is
   the profile's own). A draft stored before a change could keep a profile's name
   carries the "[AI]" marker the app then added, on a base that has none, so it fails the name
   test and is grouped as it always was: a firmware profile does not absorb the agent's old
   work. A renamed version of a Set's profile stays with it. What a draft replaced on the
   machine is the same profile. A version that would start a new
   profile joins the one that already has exactly its label, so no two profiles share a name.
4. **What no draft made** (imports, versions the machine held before the board) joins the
   profile with exactly that label, else becomes a profile of its own. The suffix is never
   stripped.

A new profile is **off**, starred, with its newest version active, except one whose version a
live file on the machine holds that no live row stands on (made on the display after the board):
that one is **on**, starred as the file is, and so is a tombstoned (deleted) row whose file, or
a file holding its version, is still live on the machine: the upgrade never turns what the
machine has now into something the next sync removes. A tombstone whose file is gone comes back
off, and so do profiles made only from stored history.
The synthetic base a
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
from gaggiclanker.db.repos.lineage import lineage_owner
from gaggiclanker.db.repos.profile_board import BoardRow, ProfileBoardRepository, VersionSource
from gaggiclanker.db.repos.profiles import SYNTHETIC_BASE_LABEL
from gaggiclanker.db.repository import Repository

__all__ = ["ProfileListBuilder", "stripped_label", "version_source_for_draft"]

log = structlog.get_logger(__name__)

#: The marker earlier versions of the app added to the names of profiles the agent wrote. The app
#: no longer adds it, but stored drafts and versions carry it, and this one-time fill replays that
#: history, grouping "X" and "X [AI]" as the app did then. Nothing outside the fill reads it.
_SUFFIX = "[AI]"


def stripped_label(label: str) -> str:
    """The label without the marker earlier versions of the app added (stored history only)."""
    text = label.rstrip()
    if text.endswith(_SUFFIX):
        text = text[: -len(_SUFFIX)]
    return text.rstrip()


def version_source_for_draft(draft: dict[str, Any]) -> VersionSource:
    """Whether a draft's version was the agent's or a person's own edit.

    A draft records who made it (``made_by``) and that is believed. Only a draft made before the
    column existed has none, and for those this reads what only an agent's draft carries: a Set,
    an analysis or suggestion it came from, a prediction, a refinement's parent or notes. A
    document typed into the editor carries none of them. A heuristic, for history only.
    """
    recorded = draft.get("made_by")
    if recorded in ("agent", "edit"):
        return recorded  # type: ignore[no-any-return]
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
                "d.base_device_profile_id, d.is_new, "
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
        # Only files the mirror holds now (``deleted_at IS NULL``) count as on the machine: a file
        # a wipe took is gone, and its profile stays off.
        live_files = {
            str(r["device_id"]): bool(r["favorite"])
            for r in await self.db.fetch_all(
                "SELECT device_id, favorite FROM device_profiles WHERE deleted_at IS NULL"
            )
        }
        for row in revived:
            file = row.device_profile_id
            clear_file = file is not None and file in claimed
            held_now = file is not None and not clear_file and file in live_files
            await self.db.execute(
                "UPDATE profile_board SET deleted_at = NULL, on_machine = ?, "
                "on_home_screen = CASE WHEN ? THEN ? ELSE on_home_screen END, "
                "pending_draft_id = NULL, pending_set_id = NULL, pending_major = NULL, "
                "failed_version_id = NULL, "
                "device_profile_id = CASE WHEN ? THEN NULL ELSE device_profile_id END, "
                "device_version_id = CASE WHEN ? THEN NULL ELSE device_version_id END, "
                "updated_at = ? WHERE id = ?",
                (
                    int(held_now),
                    int(held_now),
                    int(live_files.get(file or "", True)),
                    int(clear_file),
                    int(clear_file),
                    utc_now(),
                    row.id,
                ),
            )
            if file is not None and not clear_file:
                claimed.add(file)
        by_id = {r.id: r for r in rows}
        profile_rows = [by_id[i] for i in sorted({*(r.id for r in live), *(r.id for r in revived)})]

        # 2. Group the versions: what the rows name, then the stored drafts replayed, oldest
        # first, through the rule history was written under (`lineage_owner`), then what is left
        # by exact label.
        pushed_versions = {
            int(d["draft_version_id"])
            for d in drafts
            if d["status"] == "pushed" and d["draft_version_id"] is not None
        }
        groups = _Groups(versions, {r.id: r for r in profile_rows}, pushed_versions)
        for row in rows:
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
                    groups.seed(target[row.id], named)
        files_of: dict[str, int] = {}
        for row in rows:
            if row.device_profile_id:
                files_of.setdefault(row.device_profile_id, target[row.id])
        replayable = [
            d
            for d in drafts
            if d["draft_version_id"] is not None
            and int(d["draft_version_id"]) in versions
            and (
                d["status"] not in ("draft", "approved")
                or groups.owner_of(int(d["draft_version_id"]))
            )
        ]
        # Hard facts first: a draft pushed as the file a profile stands on, or waiting on one.
        for draft in replayable:
            home = files_of.get(draft["pushed_device_profile_id"] or "")
            if home is None and draft["pending_row"] is not None:
                home = target.get(int(draft["pending_row"]))
            if home is not None:
                groups.add(home, int(draft["draft_version_id"]))
        lookup = _ReplayLookup(groups, files_of, profile_rows)
        for draft in replayable:
            version = int(draft["draft_version_id"])
            if not eligible(version) and groups.owner_of(version) is None:
                continue
            # A version a hard fact already placed stays where it is; otherwise the put's own
            # rule: the profile with exactly its name (a Set's profile first), else a new one.
            landed = groups.owner_of(version)
            if landed is None:
                landed = await lineage_owner(
                    lookup, set_id=draft["set_id"], version_label=versions[version]["label"]
                )
            if landed is None:
                landed = groups.new()
            groups.add(landed, version)
            # What the draft replaced on the machine is the same profile.
            replaced = draft["replaced_version_id"]
            if replaced is not None and groups.owner_of(int(replaced)) is not None:
                groups.add(landed, int(replaced))
            if draft["set_id"] is not None:
                lookup.last_for_set[int(draft["set_id"])] = version

        # 3. What no draft made (imports, versions the machine held before the board): the
        # profile with exactly that label, else a profile of its own. The suffix is never
        # stripped, so a firmware profile never absorbs the agent's work.
        for version_id in sorted(versions):
            if groups.owner_of(version_id) is not None or not eligible(version_id):
                continue
            label = versions[version_id]["label"]
            home = groups.with_label(label)
            groups.add(home if home is not None else groups.new(), version_id)

        # Whatever path placed a version (a Set chain that was renamed onto a name another
        # profile holds, for one), no two profiles end with one name: a made group whose name is
        # held by another joins that profile (an existing one first, else the older made group).
        groups.unify_names()

        # What the machine holds now is never turned into something the next sync removes. A file
        # a live row does not stand on (made on the display after the board, or kept from the
        # old page's "on the machine, not on the board") whose version belongs to a profile the
        # fill makes: that profile is **on**, starred as the file is. A profile that exists keeps
        # its switch; a tombstone the person deleted comes back off, as before.
        # Such a profile stands on that file and its active version is the one the file holds,
        # so the first sync finds nothing to do (a display edit made before the upgrade is not
        # undone). A live row's own file is not touched here: an edit there is the normal conflict.
        stood = set(claimed)  # files some row, live or revived, stands on
        held_by: dict[int, tuple[str, int, bool]] = {}
        revived_ids = {r.id for r in revived}
        for held in await self.db.fetch_all(
            "SELECT device_id, current_version_id, favorite FROM device_profiles "
            "WHERE deleted_at IS NULL ORDER BY position, device_id"
        ):
            home = groups.owner_of(int(held["current_version_id"]))
            if home is None or held["device_id"] in stood:
                continue
            if home < 0 or home in revived_ids:
                held_by.setdefault(
                    home,
                    (
                        str(held["device_id"]),
                        int(held["current_version_id"]),
                        bool(held["favorite"]),
                    ),
                )
        # A revived row's own file, when the file holds a version of that very profile (edited on
        # the display before the upgrade): the row's active version is that one.
        for row in revived:
            file = row.device_profile_id
            if file is None or file not in live_files or file not in claimed:
                continue
            held_version = next(
                (
                    int(v["current_version_id"])
                    for v in await self.db.fetch_all(
                        "SELECT current_version_id FROM device_profiles WHERE device_id = ? "
                        "AND deleted_at IS NULL",
                        (file,),
                    )
                ),
                None,
            )
            if held_version is not None and groups.owner_of(held_version) == row.id:
                held_by.setdefault(row.id, (file, held_version, live_files[file]))
        for row_id in sorted(revived_ids & held_by.keys()):
            file, version_id, favorite = held_by[row_id]
            await self.db.execute(
                "UPDATE profile_board SET on_machine = 1, on_home_screen = ?, "
                "current_version_id = ?, device_profile_id = ?, device_version_id = ? "
                "WHERE id = ?",
                (int(favorite), version_id, file, version_id, row_id),
            )

        for row_id in sorted(k for k in groups.members if k > 0):
            for version_id in sorted(groups.members[row_id]):
                await self.board.add_version(
                    row_id,
                    version_id,
                    source(version_id),
                    added_at=versions[version_id]["created_at"],
                )
        made = 0
        for key in sorted(k for k in groups.members if k < 0 and groups.members[k]):
            members = groups.members[key]
            hold = held_by.get(key)
            active = groups.active(key) if hold is None else hold[1]
            created = await self.db.execute(
                "INSERT INTO profile_board (label, current_version_id, device_profile_id, "
                "device_version_id, on_machine, on_home_screen, origin, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    versions[active]["label"],
                    active,
                    None if hold is None else hold[0],
                    None if hold is None else hold[1],
                    int(hold is not None),
                    1 if hold is None else int(hold[2]),
                    "draft" if versions[active]["source"] == "draft" else "adopted",
                    utc_now(),
                    utc_now(),
                ),
            )
            new_id = int(created.lastrowid or 0)
            for version_id in sorted(members):
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


class _Groups:
    """Versions grouped into profiles. Existing board rows are groups with a positive id (an
    anchor); groups the replay makes have negative ids and become new profiles. Two groups that
    each hold a board row are never merged: they stay two profiles."""

    def __init__(
        self,
        versions: dict[int, dict[str, Any]],
        rows: dict[int, BoardRow],
        pushed: set[int],
    ) -> None:
        self.versions = versions
        self.rows = rows
        #: Versions a person's put took to the machine: what a made profile is active on.
        self.pushed = pushed
        self.members: dict[int, set[int]] = {r: set() for r in rows}
        self._owner: dict[int, int] = {}
        self._next = -1

    def new(self) -> int:
        gid = self._next
        self._next -= 1
        self.members[gid] = set()
        return gid

    def owner_of(self, version_id: int) -> int | None:
        return self._owner.get(version_id)

    def seed(self, gid: int, version_id: int) -> None:
        if version_id in self.versions:
            self.members[gid].add(version_id)
            self._owner.setdefault(version_id, gid)

    def add(self, gid: int, version_id: int) -> None:
        """Put a version in a group, merging with the group that already has it when allowed."""
        if version_id not in self.versions:
            return
        held = self._owner.get(version_id)
        if held is None:
            self.members[gid].add(version_id)
            self._owner[version_id] = gid
        elif held != gid:
            self._merge(gid, held)

    def _merge(self, a: int, b: int) -> None:
        if a > 0 and b > 0:
            return  # two profiles that exist stay two
        keep, drop = (b, a) if b > 0 else (a, b)
        for version_id in self.members[drop]:
            self._owner[version_id] = keep
        self.members[keep] |= self.members[drop]
        self.members[drop] = set()

    def active(self, gid: int) -> int:
        """A made profile's active version: its newest pushed one, else its newest."""
        members = self.members[gid]
        pushed = members & self.pushed
        return max(pushed or members)

    def name_of(self, gid: int) -> str:
        """A profile's current name, as a put reads it: the row's label, or its active version's."""
        row = self.rows.get(gid)
        return row.label if row is not None else str(self.versions[self.active(gid)]["label"])

    def _ordered(self) -> list[int]:
        """Profiles that exist first (by id), then made ones, oldest first."""
        return sorted((g for g in self.members if self.members[g]), key=lambda g: (g < 0, abs(g)))

    def with_label(self, label: str) -> int | None:
        """The profile whose current name is exactly this label (never a former name)."""
        return next((g for g in self._ordered() if self.name_of(g) == label), None)

    def unify_names(self) -> None:
        """Merge made groups into the profile that already holds their name, to a fixed point."""
        changed = True
        while changed:
            changed = False
            ordered = self._ordered()
            for gid in ordered:
                if gid > 0:
                    continue
                for other in ordered:
                    if (
                        other != gid
                        and self.name_of(other) == self.name_of(gid)
                        and (other > 0 or abs(other) < abs(gid))
                    ):
                        self._merge(other, gid)
                        changed = True
                        break
                if changed:
                    break


class _ReplayLookup:
    """`lineage_owner`'s questions, answered from the groups built so far."""

    def __init__(self, groups: _Groups, files_of: dict[str, int], rows: list[BoardRow]) -> None:
        self.groups = groups
        self.files_of = files_of
        #: The last draft version replayed for each Set: the Set's chain, as it stood.
        self.last_for_set: dict[int, int] = {}

    def label_of(self, profile: int) -> str:
        return self.groups.name_of(profile)

    async def by_set(self, set_id: int) -> int | None:
        version = self.last_for_set.get(set_id)
        return None if version is None else self.groups.owner_of(version)

    async def by_label(self, label: str) -> int | None:
        return self.groups.with_label(label)
