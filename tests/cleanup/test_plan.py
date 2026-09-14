"""What a cleanup *would* do, under each policy. Nothing here touches the machine."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from gaggiclanker.cleanup.service import CleanupService
from gaggiclanker.domain.ids import pad6
from tests.cleanup.conftest import (
    CORRUPT_ID,
    DELETED_ID,
    FAKE_SPIFFS_FREE,
    FIRST_ID,
    SMALL_COUNT,
    service,
)


async def test_the_default_policy_plans_nothing(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """`off` is the shipped default, and it is a real mode rather than an absence."""
    app, _ = live
    plan = await service(app).plan()
    assert plan.policy.mode == "off"
    assert plan.planned == []


async def test_the_plan_counts_what_the_archive_thinks_is_on_the_machine(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """A shot the index already flagged deleted is not on the machine any more."""
    app, _ = live
    plan = await service(app).plan()
    assert plan.on_device_count == SMALL_COUNT - 1  # the pre-deleted one is gone
    assert pad6(DELETED_ID) not in {item.device_id for item in plan.planned}


async def test_keep_newest_deletes_the_oldest_surplus_in_order(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """Oldest first, which is the order the firmware's own retention uses."""
    app, _ = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": 10}
    )
    plan = await service(app).plan()
    assert plan.on_device_count == SMALL_COUNT - 1
    assert len(plan.planned) == plan.on_device_count - 10
    ids = [item.device_id for item in plan.planned]
    assert ids == sorted(ids)
    assert ids[0] == pad6(FIRST_ID)


async def test_keep_newest_plans_nothing_when_the_machine_is_under_the_target(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": 100}
    )
    assert (await service(app).plan()).planned == []


async def test_an_ineligible_shot_is_listed_with_its_reason_rather_than_hidden(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """ "Why is that shot still on my machine" has to be answerable from the page."""
    app, _ = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": 5}
    )
    plan = await service(app).plan()
    skipped = {item.device_id: item.reason for item in plan.skipped}
    assert pad6(CORRUPT_ID) in skipped
    assert "quarantined" in skipped[pad6(CORRUPT_ID)]
    assert pad6(CORRUPT_ID) not in {item.device_id for item in plan.planned}


async def test_a_refused_shot_does_not_stop_the_policy_reaching_its_target(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """ "Keep the newest N" is a ceiling on the count, not a fixed cut point.

    One of the six oldest shots is quarantined and cannot go, so the plan takes
    the next eligible one instead and still names six. The alternative — stop at
    the quarantined shot — would mean one unparseable file quietly disables the
    policy for ever, which is the failure mode nobody would notice.
    """
    app, _ = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "keep_newest", "deviceCleanupKeepNewest": SMALL_COUNT - 7}
    )
    plan = await service(app).plan()
    chosen = {item.device_id for item in plan.planned}
    assert len(plan.planned) == 6
    assert pad6(CORRUPT_ID) not in chosen
    assert pad6(CORRUPT_ID + 1) in chosen


async def test_free_space_plans_only_as_much_as_the_deficit_needs(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    # A floor a little above what the fake reports free, so the deficit is one
    # or two shots rather than the whole archive.
    floor_kb = (FAKE_SPIFFS_FREE // 1024) + 8
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "free_space", "deviceCleanupMinFreeKb": floor_kb}
    )
    plan = await service(app).plan()
    assert plan.free_bytes == FAKE_SPIFFS_FREE
    assert plan.free_source == "spiffs"
    assert plan.planned
    assert plan.bytes_freed >= floor_kb * 1024 - FAKE_SPIFFS_FREE
    # And not one shot more than it needed.
    assert plan.bytes_freed - plan.planned[-1].raw_bytes < floor_kb * 1024 - FAKE_SPIFFS_FREE


async def test_free_space_plans_nothing_when_there_is_already_room(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    app, _ = live
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "free_space", "deviceCleanupMinFreeKb": 1024}
    )
    assert (await service(app).plan()).planned == []


async def test_free_space_refuses_to_act_on_a_machine_that_reported_no_figures(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """No identity frame means no free-space number, and a guess would delete shots."""
    app, _ = live
    blind = CleanupService(
        app.state.db, app.state.settings_service, connection=None, pace_seconds=0.0
    )
    await app.state.settings_service.apply(
        {"deviceCleanupMode": "free_space", "deviceCleanupMinFreeKb": 4096}
    )
    plan = await blind.plan()
    assert plan.planned == []
    assert plan.blocked is not None
    assert "free space" in plan.blocked


async def test_the_sd_card_wins_when_the_machine_has_one(
    live: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    """The firmware moves `/h/` onto SD when a card is mounted, and only then reports `sd*`."""
    app, _ = live
    app.state.connection.client.identity = app.state.connection.client.identity.model_copy(
        update={"sd_free": 12_345, "sd_total": 8 * 1024 * 1024}
    )
    plan = await service(app).plan()
    assert plan.free_bytes == 12_345
    assert plan.free_source == "sd"
