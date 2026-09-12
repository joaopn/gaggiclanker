"""Repositories for the archive: the only code in the project that writes SQL.

One module per table group, each exporting its pydantic row models and a
repository class. Services (the sync engine) and routes hold a repository; they
never hold a cursor.
"""

from gaggiclanker.db.repos.base import JsonText, dumps, from_iso, to_iso, utc_now
from gaggiclanker.db.repos.machines import MachineRow, MachinesRepository, MachineUpsert
from gaggiclanker.db.repos.notes import DeviceShotNotesRow, NotesRepository
from gaggiclanker.db.repos.profiles import (
    DeviceProfileRow,
    DeviceProfileSummary,
    ProfilesRepository,
    ProfileVersionRow,
)
from gaggiclanker.db.repos.shots import (
    ShotDetailRow,
    ShotInsert,
    ShotListRow,
    ShotSampleRow,
    ShotsRepository,
    ShotState,
)
from gaggiclanker.db.repos.sync import (
    SyncEventRow,
    SyncRepository,
    SyncRunRow,
    SyncRunUpdate,
)

__all__ = [
    "DeviceProfileRow",
    "DeviceProfileSummary",
    "DeviceShotNotesRow",
    "JsonText",
    "MachineRow",
    "MachineUpsert",
    "MachinesRepository",
    "NotesRepository",
    "ProfileVersionRow",
    "ProfilesRepository",
    "ShotDetailRow",
    "ShotInsert",
    "ShotListRow",
    "ShotSampleRow",
    "ShotState",
    "ShotsRepository",
    "SyncEventRow",
    "SyncRepository",
    "SyncRunRow",
    "SyncRunUpdate",
    "dumps",
    "from_iso",
    "to_iso",
    "utc_now",
]
