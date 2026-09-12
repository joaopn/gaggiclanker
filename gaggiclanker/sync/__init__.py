"""The sync engine: everything on the machine ends up in SQLite, and stays there.

The machine is a buffer with roughly 300 KB of heap that deletes its oldest
shots once free space drops below 500 KB. gaggiclanker is the archive. That one
fact shapes this package: sync promptly, keep the raw bytes whatever happens to
the parser, and treat every push from the device as "go and look" rather than as
the only record that something happened.
"""

from gaggiclanker.sync.engine import (
    PROFILE_UPDATED_EVENT,
    SHOT_INGESTED_EVENT,
    SHOT_QUARANTINED_EVENT,
    SHOT_UPDATED_EVENT,
    SYNC_PROGRESS_EVENT,
    SyncEngine,
)

__all__ = [
    "PROFILE_UPDATED_EVENT",
    "SHOT_INGESTED_EVENT",
    "SHOT_QUARANTINED_EVENT",
    "SHOT_UPDATED_EVENT",
    "SYNC_PROGRESS_EVENT",
    "SyncEngine",
]
