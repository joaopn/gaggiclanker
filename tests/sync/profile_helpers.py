"""Shared by the suites about the profile a shot is derived with (real shot 204 and its profile)."""

from __future__ import annotations

import json
from typing import Any

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.profiles import ProfilesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.models import Profile
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.sync.derive import derive_shot
from tests.domain.helpers import SLOG_FIXTURES, constructed_profile

SLOG_204 = SLOG_FIXTURES / "shot_204_ramping_flow.slog"
PROFILE_ID_204 = parse_slog(SLOG_204.read_bytes()).header.profile_id


async def profile_version(db: Database, variant: str = "pressure-first") -> int:
    profile = Profile.model_validate(constructed_profile("shot_204", variant))
    version, _ = await ProfilesRepository(db).ensure_version(profile)
    return version.id


async def unlinked_shot(db: Database, device_id: str = "000204") -> int:
    """Shot 204 as a sync that ran before the profile was mirrored stores it."""
    raw = SLOG_204.read_bytes()
    derived = derive_shot(parse_slog(raw), raw, device_id=device_id)
    return await ShotsRepository(db).insert(derived.shot, derived.samples)


async def compliance(db: Database, shot_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT diagnostics_json FROM shots WHERE id = ?", (shot_id,))
    assert row is not None
    block: dict[str, Any] | None = json.loads(row["diagnostics_json"])["diagnostics"][
        "profile_compliance"
    ]
    return block


async def derivation_version(db: Database, shot_id: int) -> int:
    return int(
        await db.fetch_value("SELECT derivation_version FROM shots WHERE id = ?", (shot_id,))
    )
