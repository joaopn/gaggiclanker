"""Shot id conversions.

The device speaks two dialects of the same number.  `evt:history-shot-saved`
carries an *unpadded* int (`{"id": 129}`), while URLs (`/api/history/000129.slog`)
and the notes file name use the 6-digit zero-padded form.  Getting this wrong
fetches nothing and looks like a missing shot, so both directions live here and
nowhere else.
"""

from __future__ import annotations

import re

_PADDED = re.compile(r"^\d{1,9}$")

#: Width of the device's zero-padded shot id (`padId`, ShotHistoryPlugin.cpp).
ID_WIDTH = 6


def pad6(shot_id: int | str) -> str:
    """Return the 6-digit zero-padded form of *shot_id* (``129 -> "000129"``).

    Accepts either dialect so callers never have to know which one they hold.
    Ids wider than six digits are returned unpadded rather than truncated —
    the device would do the same and the file name stays correct.
    """
    value = unpad(shot_id)
    return f"{value:0{ID_WIDTH}d}"


def unpad(shot_id: int | str) -> int:
    """Return the integer form of *shot_id* (``"000129" -> 129``).

    Raises:
        ValueError: if *shot_id* is not a non-negative decimal integer.
    """
    if isinstance(shot_id, bool):  # bool is an int subclass; never a shot id
        raise ValueError(f"invalid shot id: {shot_id!r}")
    if isinstance(shot_id, int):
        if shot_id < 0:
            raise ValueError(f"invalid shot id: {shot_id!r}")
        return shot_id
    if not isinstance(shot_id, str):
        raise ValueError(f"invalid shot id: {shot_id!r}")
    text = shot_id.strip()
    if not _PADDED.match(text):
        raise ValueError(f"invalid shot id: {shot_id!r}")
    return int(text, 10)
