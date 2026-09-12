"""Repositories for the archive: the only code in the project that writes SQL.

One module per table group, each exporting its pydantic row models and a
repository class. Services (the sync engine) and routes hold a repository; they
never hold a cursor.
"""

from gaggiclanker.db.repos.base import JsonText, dumps, from_iso, to_iso, utc_now
from gaggiclanker.db.repos.beans import BeanRow, BeansRepository, BeanWrite
from gaggiclanker.db.repos.grinders import GrinderRow, GrindersRepository, GrinderWrite
from gaggiclanker.db.repos.judgements import (
    JudgementsRepository,
    JudgementWrite,
    ShotJudgementRow,
)
from gaggiclanker.db.repos.machines import MachineRow, MachinesRepository, MachineUpsert
from gaggiclanker.db.repos.notes import DeviceShotNotesRow, NotesRepository
from gaggiclanker.db.repos.profiles import (
    DeviceProfileRow,
    DeviceProfileSummary,
    ProfilesRepository,
    ProfileVersionRow,
)
from gaggiclanker.db.repos.sets import (
    FieldChange,
    SetRow,
    SetsRepository,
    SetTrends,
    SetVersionPatch,
    SetVersionRow,
    SetVersionWrite,
    SetWrite,
    version_changes,
)
from gaggiclanker.db.repos.shots import (
    ShotDetailRow,
    ShotInsert,
    ShotListRow,
    ShotSampleRow,
    ShotSetBadge,
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
    "BeanRow",
    "BeanWrite",
    "BeansRepository",
    "DeviceProfileRow",
    "DeviceProfileSummary",
    "DeviceShotNotesRow",
    "FieldChange",
    "GrinderRow",
    "GrinderWrite",
    "GrindersRepository",
    "JsonText",
    "JudgementWrite",
    "JudgementsRepository",
    "MachineRow",
    "MachineUpsert",
    "MachinesRepository",
    "NotesRepository",
    "ProfileVersionRow",
    "ProfilesRepository",
    "SetRow",
    "SetTrends",
    "SetVersionPatch",
    "SetVersionRow",
    "SetVersionWrite",
    "SetWrite",
    "SetsRepository",
    "ShotDetailRow",
    "ShotInsert",
    "ShotJudgementRow",
    "ShotListRow",
    "ShotSampleRow",
    "ShotSetBadge",
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
    "version_changes",
]
