"""Device storage management: deciding which shots the machine may lose.

The machine is a buffer with a few megabytes of flash that deletes its own
oldest shots once free space drops below 500 KB. gaggiclanker is the archive, so
the only thing this package adds is *timing*: shots leave the machine when the
archive has them rather than when the machine runs out of room.

Nothing here decides that a shot is safe to delete on its own — the rule is in
:mod:`gaggiclanker.cleanup.eligibility` and it is applied twice, once by the
plan step so a person can see what would go, and once by the write gate before a
frame reaches the wire. The second one is the one that counts.
"""

from __future__ import annotations

__all__: list[str] = []
