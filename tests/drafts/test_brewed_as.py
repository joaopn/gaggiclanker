"""What a shot says it was brewed as: the name the machine recorded, when the profile is not called
that now. The shot header holds 47 bytes of name, so a profile that was never renamed but has a
longer name is not "brewed as" its own prefix."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
from fastapi import FastAPI

from gaggiclanker.db.repos.shots import brewed_as
from gaggiclanker.device.fake import FakeDevice, synthetic_slog_bytes
from gaggiclanker.domain.slog import encode_slog, parse_slog, shot_header_name
from tests.drafts.conftest import data
from tests.drafts.test_board import adopted, pull
from tests.drafts.test_rename import own_row, rename

__all__ = ["adopted"]

Adopted = tuple[FastAPI, httpx.AsyncClient, FakeDevice]

LONG = "Blooming milk for the weekend, 18 g in, 40 g out, slow"  # 54 bytes


def test_the_header_name_is_cut_at_47_bytes_on_a_character_boundary() -> None:
    assert shot_header_name("Blooming") == "Blooming"
    assert len(shot_header_name(LONG).encode()) == 47 and LONG.startswith(shot_header_name(LONG))
    # a two-byte character straddling the limit goes whole or not at all
    name = "a" * 46 + "é" + "tail"
    assert shot_header_name(name) == "a" * 46
    assert shot_header_name("é" * 40) == "é" * 23
    assert shot_header_name("") == ""


def test_a_character_cut_by_the_firmware_is_not_a_different_name() -> None:
    """The firmware cuts at 47 bytes even inside a character, and the archive decodes the stub as
    U+FFFD: a long name that was never renamed is still not "brewed as" its own cut."""
    label = "a" * 46 + "é" + "tail"  # byte 47 is the first byte of the two-byte é
    on_device = ("a" * 46 + "é").encode()[:47].decode("utf-8", errors="replace")
    assert on_device == "a" * 46 + "\ufffd"
    assert brewed_as(label, on_device) is None
    assert brewed_as(label, "a" * 46) is None
    assert brewed_as(label, "Another name") == "Another name"
    assert brewed_as(label, "a" * 46 + "\ufffd" + "x") == "a" * 46 + "\ufffd" + "x"
    assert brewed_as(None, on_device) is None


async def shot_for(adopted: Adopted, file: str, name: str, shot_id: int) -> dict[str, Any]:
    app, client, fake = adopted
    slog = parse_slog(synthetic_slog_bytes(shot_id=shot_id, profile_name=name))
    slog = dataclasses.replace(slog, header=slog.header.model_copy(update={"profile_id": file}))
    shot = fake.add_shot(shot_id, encode_slog(slog), profile_name=name)
    shot.entry = shot.entry.model_copy(update={"profile_id": file})
    await app.state.connection.engine.sync_shots(trigger="test")
    shots = data(await client.get("/api/shots", params={"limit": 50}))
    items = shots["items"] if isinstance(shots, dict) else shots
    return next(s for s in items if s["device_id"].lstrip("0") == str(shot_id))


async def test_a_long_name_that_was_never_renamed_is_not_brewed_as_its_own_prefix(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    assert (await rename(client, row["row"]["id"], LONG)).status_code == 200
    await pull(app)

    shot = await shot_for(adopted, file, LONG, 777)

    assert shot["profile_label"] == LONG
    assert shot["profile_name_on_device"] == shot_header_name(LONG), "the machine's cut name"
    assert shot["brewed_as"] is None


async def test_a_renamed_profile_says_what_the_shot_was_brewed_as(adopted: Adopted) -> None:
    _app, client, _fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    shot = await shot_for(adopted, file, row["row"]["label"], 778)
    assert shot["brewed_as"] is None, "the profile is still called what it was"

    await rename(client, row["row"]["id"], "Blooming milk")

    shots = data(await client.get("/api/shots", params={"limit": 50}))
    items = shots["items"] if isinstance(shots, dict) else shots
    renamed = next(s for s in items if s["id"] == shot["id"])
    assert renamed["profile_label"] == "Blooming milk"
    assert renamed["brewed_as"] == row["row"]["label"]
    detail = data(await client.get(f"/api/shots/{shot['id']}"))
    assert detail["shot"]["brewed_as"] == row["row"]["label"]


async def test_a_long_name_renamed_to_another_long_name_is_brewed_as_the_cut_name(
    adopted: Adopted,
) -> None:
    app, client, _fake = adopted
    row = await own_row(client)
    file = row["machine"]["device_id"]
    await rename(client, row["row"]["id"], LONG)
    await pull(app)
    shot = await shot_for(adopted, file, LONG, 779)
    await rename(client, row["row"]["id"], LONG.replace("Blooming", "Cortado"))
    shots = data(await client.get("/api/shots", params={"limit": 50}))
    items = shots["items"] if isinstance(shots, dict) else shots
    again = next(s for s in items if s["id"] == shot["id"])
    assert again["brewed_as"] == shot_header_name(LONG)
