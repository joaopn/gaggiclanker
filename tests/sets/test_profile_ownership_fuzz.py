# ruff: noqa: S311, E501
"""A randomised driver over the Writes-off path and what follows it, through the real app
(routes, services, sync engine) against a fake machine. Each seed is one run of random steps (new
Sets, new versions, adoption, puts, Make active, a file leaving and returning, a profile listing a
Set's file without making it active, a file renamed on the display, a profile renamed in the app
(through the rename route) and the person keeping a side of the
conflicts that come of it); after every step the ownership and Set invariants are asserted, a
profile never moves off a file the machine still holds, no two live profiles share a name, a
rename makes no version, and a new shot on each Set's current version (linked, and by the machine
file that holds it) must file under that Set. A small fixed set of seeds keeps it to seconds."""

from __future__ import annotations

import copy
import itertools
import random
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.repos.lineage import name_key
from gaggiclanker.device.fake import build_fake_device
from gaggiclanker.settings import EnvSettings
from tests.conftest import running_app, seed_settings
from tests.drafts.conftest import FIXTURES, data
from tests.drafts.helpers import assert_sets_follow_their_profiles, assert_versions_owned
from tests.drafts.test_board import WRITE_FRAMES
from tests.sets.conftest import make_shot

SEEDS = range(12)
STEPS = 30
_shots = itertools.count(1)


class Failed(AssertionError):
    pass


async def check(
    app: Any, client: Any, fake: Any, log: list[str], seen_files: dict[int, str]
) -> None:
    db = app.state.db
    try:
        await assert_versions_owned(db)
        await assert_sets_follow_their_profiles(db)
    except AssertionError as exc:
        raise Failed(f"invariant: {exc}") from None
    live = await db.fetch_all(
        "SELECT id, label, device_profile_id FROM profile_board WHERE deleted_at IS NULL"
    )
    names = [name_key(r["label"]) for r in live]
    if len(names) != len(set(names)):
        raise Failed(f"two live profiles share a name: {sorted(r['label'] for r in live)}")
    on_machine = {str(p["id"]) for p in fake.profiles}
    for r in live:
        now, before = r["device_profile_id"], seen_files.get(r["id"])
        if before is not None and now != before and before in on_machine:
            raise Failed(
                f"{r['label']} moved off {before}, which the machine still holds, to {now}"
            )
        if now is not None:
            seen_files[r["id"]] = now
    shared = await db.fetch_all(
        "SELECT v.profile_version_id pv, COUNT(DISTINCT s.id) n FROM sets s "
        "JOIN set_versions v ON v.id = s.current_version_id "
        "WHERE v.profile_version_id IS NOT NULL AND s.archived = 0 AND s.automatch = 1 "
        "GROUP BY v.profile_version_id HAVING n > 1"
    )
    if shared:
        raise Failed(f"two Sets' current versions name one version: {[dict(r) for r in shared]}")
    sets = await db.fetch_all(
        "SELECT s.id, s.name, s.current_version_id cur, v.profile_version_id pv FROM sets s "
        "JOIN set_versions v ON v.id = s.current_version_id WHERE v.profile_version_id IS NOT NULL"
    )
    for s in sets:
        linked = await make_shot(db, f"fz{next(_shots):07d}", profile_version_id=s["pv"])
        files = await db.fetch_all(
            "SELECT device_id FROM device_profiles WHERE current_version_id = ? AND deleted_at IS NULL",
            (s["pv"],),
        )
        by_file = [
            await make_shot(db, f"fz{next(_shots):07d}", profile_id_on_device=f["device_id"])
            for f in files
        ]
        await client.post("/api/shots/profile-match", json={"shot_ids": [linked, *by_file]})
        for shot in (linked, *by_file):
            got = await db.fetch_value("SELECT set_version_id FROM shots WHERE id = ?", (shot,))
            if got != s["cur"]:
                raise Failed(
                    f"a shot on Set {s['name']}'s current brew (pv {s['pv']}) filed under {got}, "
                    f"not {s['cur']} ({'linked' if shot == linked else 'by file'})"
                )


async def run_one(seed: int, tmp_path: Path) -> list[str]:
    rnd = random.Random(seed)
    log: list[str] = []
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake = build_fake_device(FIXTURES)
    fake.shots.clear()
    # Five brewing files, some with near-twins so content can coincide after edits.
    keep = [p for p in fake.profiles if not p.get("utility") and "Backflush" not in p["label"]]
    fake.profiles = [copy.deepcopy(p) for p in keep[:5]]
    await fake.start(port=0)
    try:
        env = EnvSettings(DATA_DIR=str(data_dir), LOG_LEVEL="warning", LOG_JSON=True)  # type: ignore[call-arg]
        await seed_settings(env, gaggimateHost=fake.address, gaggimateTimeoutSeconds=5)
        hidden: list[dict[str, Any]] = []
        seen_files: dict[int, str] = {}
        async with running_app(env) as (app, client):
            assert await app.state.connection.client.wait_connected(5.0)
            eng = app.state.connection.engine
            db = app.state.db
            await eng.sync_profiles(trigger="test")
            writes = False
            bean = data(await client.post("/api/beans", json={"name": "fz", "roaster": "x"}))
            n_sets = 0
            for step in range(STEPS):
                ops = ["new_set", "new_set", "add", "add", "add"]
                ops += ["sync"] if writes else ["writes_on"] * (1 if step > 4 else 0)
                if writes:
                    ops += [
                        "make_active",
                        "put",
                        "return_file",
                        "sync",
                        "list_inactive",
                        "list_inactive",
                        "display_rename",
                        "display_rename",
                        "app_rename",
                        "app_rename",
                        "keep_machine",
                        "keep_machine",
                        "keep_machine",
                    ]
                if not writes:
                    ops += ["hide_file"]
                op = rnd.choice(ops)
                if op == "new_set":
                    choices = data(await client.get("/api/sets/profile-choices"))["items"]
                    if not choices:
                        continue
                    pick = rnd.choice(choices)
                    n_sets += 1
                    r = await client.post(
                        "/api/sets",
                        json={
                            "name": f"S{n_sets}",
                            "bean_id": bean["id"],
                            "version": {
                                "profile_version_id": pick["profile_version_id"],
                                "dose_g": 18,
                            },
                        },
                    )
                    log.append(f"{step}: new_set on {pick['label']} -> {r.status_code}")
                    if r.status_code >= 500:
                        raise Failed(f"500 on new set: {r.text}")
                elif op == "add":
                    sets = await db.fetch_all(
                        "SELECT id, name, board_id FROM sets WHERE designing = 0"
                    )
                    if not sets:
                        continue
                    s = rnd.choice(sets)
                    body: dict[str, Any] = {"dose_g": round(rnd.uniform(16, 20), 1)}
                    if s["board_id"] is None and rnd.random() < 0.8:
                        choices = data(await client.get("/api/sets/profile-choices"))["items"]
                        if choices:
                            pick = rnd.choice(choices)
                            body["profile_version_id"] = pick["profile_version_id"]
                    r = await client.post(f"/api/sets/{s['id']}/versions", json=body)
                    log.append(
                        f"{step}: add to {s['name']} {body} -> {r.status_code} {r.json().get('error', {}).get('code') if r.status_code >= 300 else ''}"
                    )
                    if r.status_code >= 500:
                        raise Failed(f"500 on add: {r.text}")
                elif op == "list_inactive":
                    # A profile lists the version of a file a Set with no profile is on, without
                    # making it active: the Set is taken over through it and follows the profile.
                    loose = await db.fetch_all(
                        "SELECT s.id, s.name, v.profile_version_id pv FROM sets s "
                        "JOIN set_versions v ON v.id = s.current_version_id "
                        "JOIN profile_versions p ON p.id = v.profile_version_id "
                        "WHERE s.board_id IS NULL AND p.board_id IS NULL"
                    )
                    free = await db.fetch_all(
                        "SELECT b.id FROM profile_board b WHERE b.deleted_at IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM sets s WHERE s.board_id = b.id)"
                    )
                    if not loose or not free:
                        continue
                    target, row = rnd.choice(loose), rnd.choice(free)
                    async with db.transaction():
                        await app.state.board.board.add_version(row["id"], target["pv"], "machine")
                    log.append(f"{step}: row {row['id']} lists {target['name']}'s file version")
                elif op == "display_rename":
                    # The display renames a file (the brew is the same, the name is not): the
                    # profile standing on it is in conflict at the next sync, and the version it
                    # holds is still the profile's own.
                    standing = await db.fetch_all(
                        "SELECT b.device_profile_id d FROM profile_board b WHERE b.deleted_at IS NULL "
                        "AND b.device_profile_id IS NOT NULL"
                    )
                    names = {str(r["d"]) for r in standing}
                    live = [p for p in fake.profiles if p["id"] in names]
                    if not live:
                        continue
                    f = rnd.choice(live)
                    f["label"] = f"{f['label'].split(' ~')[0]} ~{rnd.randrange(3)}"
                    run = await eng.sync_profiles(trigger="test")
                    log.append(
                        f"{step}: display renames {f['id']} to {f['label']} + sync -> {run.status}"
                    )
                elif op == "app_rename":
                    # The profile is renamed in the app through the rename route (nothing is sent
                    # until a sync with the switch on): a rename is no version, the names stay
                    # unique (a name another profile has is refused), and the machine hears
                    # nothing now.
                    rows = await db.fetch_all(
                        "SELECT id, label FROM profile_board WHERE deleted_at IS NULL"
                    )
                    if not rows:
                        continue
                    row = rnd.choice(rows)
                    name = f"{row['label'].split(' ^')[0]} ^{rnd.randrange(4)}"
                    if rnd.random() < 0.2:
                        # Somebody else's name, in any case: refused, and nothing changes.
                        name = str(rnd.choice(rows)["label"]).swapcase()
                    holder = await app.state.board.board.find_live_by_name_key(
                        name, excluding=row["id"]
                    )
                    count = await db.fetch_value("SELECT COUNT(*) FROM profile_versions")
                    fake.ws_requests.clear()
                    r = await client.put(
                        f"/api/profile-board/{row['id']}/name", json={"label": name}
                    )
                    log.append(f"{step}: app renames {row['label']} to {name} -> {r.status_code}")
                    if r.status_code != (409 if holder else 200):
                        raise Failed(f"the rename answered {r.status_code}: {r.text}")
                    if await db.fetch_value("SELECT COUNT(*) FROM profile_versions") != count:
                        raise Failed("a rename wrote a version")
                    if [t for t in fake.ws_requests if t in WRITE_FRAMES]:
                        raise Failed("a rename wrote to the machine")
                elif op == "keep_machine":
                    board = data(await client.get("/api/profile-board"))
                    held = [r for r in board["rows"] if r["in_conflict"]]
                    if not held:
                        continue
                    row = rnd.choice(held)
                    seen = data(await client.get(f"/api/profile-board/{row['row']['id']}/conflict"))
                    side = rnd.choice(["machine", "machine", "app"])
                    fake.ws_requests.clear()
                    r = await client.post(
                        f"/api/profile-board/{row['row']['id']}/conflict",
                        json={"keep": side, "content_hash": seen["machine"]["content_hash"]},
                    )
                    log.append(f"{step}: keep {side} on {row['row']['label']} -> {r.status_code}")
                    if r.status_code >= 500:
                        raise Failed(f"500 on conflict: {r.text}")
                    if [t for t in fake.ws_requests if t in WRITE_FRAMES]:
                        raise Failed("resolving a conflict wrote to the machine")
                    if side == "machine" and r.status_code < 300:
                        got = data(r)
                        file = next(
                            (p for p in fake.profiles if p["id"] == got["device_profile_id"]), None
                        )
                        if file is not None and file["label"] != got["label"]:
                            raise Failed(
                                f"the machine's side kept: the profile is {got['label']!r}, the file {file['label']!r}"
                            )
                elif op == "hide_file":
                    if len(fake.profiles) > 2:
                        f = fake.profiles.pop(rnd.randrange(len(fake.profiles)))
                        hidden.append(f)
                        log.append(f"{step}: hide file {f['label']}")
                    continue
                elif op == "writes_on":
                    await app.state.settings_service.apply({"deviceWritesEnabled": True})
                    writes = True
                    rnd.shuffle(fake.profiles)
                    run = await eng.sync_profiles(trigger="test")
                    log.append(
                        f"{step}: writes on + sync ({[p['label'] for p in fake.profiles]}) -> {run.status}"
                    )
                elif op == "sync":
                    run = await eng.sync_profiles(trigger="test")
                    log.append(f"{step}: sync -> {run.status}")
                elif op == "return_file":
                    if not hidden:
                        continue
                    f = hidden.pop(rnd.randrange(len(hidden)))
                    fake.profiles.insert(rnd.randrange(len(fake.profiles) + 1), f)
                    run = await eng.sync_profiles(trigger="test")
                    log.append(f"{step}: file {f['label']} back + sync -> {run.status}")
                elif op == "make_active":
                    rows = await db.fetch_all(
                        "SELECT b.id, b.label, b.current_version_id FROM profile_board b "
                        "WHERE b.deleted_at IS NULL AND (SELECT COUNT(*) FROM profile_board_versions l "
                        "WHERE l.board_id = b.id) > 1"
                    )
                    if not rows:
                        continue
                    row = rnd.choice(rows)
                    others = await db.fetch_all(
                        "SELECT version_id FROM profile_board_versions WHERE board_id = ? AND version_id != ?",
                        (row["id"], row["current_version_id"]),
                    )
                    v = rnd.choice(others)["version_id"]
                    r = await client.put(
                        f"/api/profile-board/{row['id']}/active-version", json={"version_id": v}
                    )
                    log.append(
                        f"{step}: make active {row['label']} v{v} -> {r.status_code} {r.json().get('error', {}).get('code') if r.status_code >= 300 else ''}"
                    )
                    if r.status_code >= 500:
                        raise Failed(f"500 on make active: {r.text}")
                elif op == "put":
                    rows = await db.fetch_all(
                        "SELECT id, label, current_version_id FROM profile_board WHERE deleted_at IS NULL"
                    )
                    if not rows:
                        continue
                    row = rnd.choice(rows)
                    stored = data(
                        await client.get(f"/api/profile-versions/{row['current_version_id']}")
                    )
                    document = dict(stored["profile"])
                    document["temperature"] = float(rnd.choice([90, 91, 92, 93, 94]))
                    try:
                        draft = await app.state.draft_proposals.create_manual(
                            base_version_id=row["current_version_id"],
                            document=document,
                            change_summary="fz",
                        )
                    except Exception as exc:
                        log.append(f"{step}: draft refused {type(exc).__name__}")
                        continue
                    r = await client.post("/api/profile-board", json={"draft_id": draft.id})
                    log.append(
                        f"{step}: put on {row['label']} {document['temperature']} -> {r.status_code}"
                    )
                    if r.status_code >= 500:
                        raise Failed(f"500 on put: {r.text}")
                await check(app, client, fake, log, seen_files)
    except Failed as exc:
        log.append(f"FAILED: {exc}")
        return log
    finally:
        await fake.stop()
    log.append("ok")
    return log


@pytest.mark.parametrize("seed", SEEDS)
async def test_fuzz(seed: int, tmp_path: Path) -> None:
    log = await run_one(seed, tmp_path)
    assert log[-1] == "ok", "\n".join(log[-12:])
