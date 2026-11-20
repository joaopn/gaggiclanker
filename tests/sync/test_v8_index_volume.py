"""The machine's index volume, for a firmware 1.10 shot whose weight was an estimate.

The machine copies a shot's header weight into its index entry, and a dose out typed on the
notes card replaces it there. For a version 8 shot with no scale (or one whose scale was lost
before the end) the header weight is the flow estimate, so the index's volume is too, and every
reader that falls back to it (the Yield column, the ratio, the spread) would show a guess. The
archive keeps the index volume only when it is not the header's own weight, and the index pass
that follows a change on the machine applies the same rule without churning the row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain import scale_weights
from gaggiclanker.domain.ids import pad6
from gaggiclanker.domain.models import SI_ACTIVE_SCALE_CONNECTED
from tests.domain.helpers import SCALE_LOST_AT, V8_SHOT, edited_v8
from tests.sync.conftest import archive_for

HEADER_G = 31.9

NO_SCALE = edited_v8(lambda _i, sample: {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED})
SCALE_LOST = edited_v8(
    lambda index, sample: (
        {"si": (sample.si or 0) & ~SI_ACTIVE_SCALE_CONNECTED} if index >= SCALE_LOST_AT else {}
    )
)


@pytest.mark.parametrize(
    ("raw", "kept"),
    [(NO_SCALE, None), (SCALE_LOST, None), (V8_SHOT.read_bytes(), HEADER_G)],
    ids=["no scale", "scale lost", "scale throughout"],
)
async def test_the_index_pass_keeps_the_machines_volume_only_where_it_is_not_the_estimate(
    empty_device: FakeDevice, tmp_path: Path, raw: bytes, kept: float | None
) -> None:
    shot = empty_device.add_shot(225, raw)
    shot.entry.volume_g = HEADER_G
    async with archive_for(empty_device, tmp_path) as archive:
        first = await archive.engine.sync_shots(trigger="test")
        assert first.shots_inserted == 1
        stored = await archive.engine.shots.get_by_device_id(pad6(225))
        assert stored is not None and stored.index_volume_g == kept
        assert stored.volume_g == (HEADER_G if kept is not None else None)

        # Nothing changed on the machine: nothing is written, and the shot is not "updated" on
        # every pass because the index and the archive hold different figures.
        assert (await archive.engine.sync_shots(trigger="test")).shots_updated == 0

        # A rating typed on the machine changes the entry; the volume is judged again.
        shot.entry.rating = 4
        run = await archive.engine.sync_shots(trigger="test")
        assert run.shots_updated == 1
        stored = await archive.engine.shots.get_by_device_id(pad6(225))
        assert stored is not None and stored.index_rating == 4
        assert stored.index_volume_g == kept
        assert (await archive.engine.sync_shots(trigger="test")).shots_updated == 0

        # A dose out typed on the machine's notes card is the person's figure, and is kept.
        shot.entry.volume_g = 36.5
        assert (await archive.engine.sync_shots(trigger="test")).shots_updated == 1
        stored = await archive.engine.shots.get_by_device_id(pad6(225))
        assert stored is not None and stored.index_volume_g == pytest.approx(36.5)
        # The scale's own weight still wins over the machine's figure where there is one.
        assert stored.volume_g == pytest.approx(HEADER_G if kept is not None else 36.5)


async def test_without_the_rule_the_index_pass_stores_the_estimate(
    empty_device: FakeDevice, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first half: the figure the rule keeps out is there to be stored."""
    monkeypatch.setattr(scale_weights, "ACTIVE_SCALE_VERSION", 99)
    shot = empty_device.add_shot(225, NO_SCALE)
    shot.entry.volume_g = HEADER_G
    async with archive_for(empty_device, tmp_path) as archive:
        await archive.engine.sync_shots(trigger="test")
        shot.entry.rating = 4
        await archive.engine.sync_shots(trigger="test")
        stored = await archive.engine.shots.get_by_device_id(pad6(225))
        assert stored is not None
        assert stored.index_volume_g == pytest.approx(HEADER_G)
        assert stored.volume_g == pytest.approx(HEADER_G)
