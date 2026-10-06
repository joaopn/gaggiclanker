#!/usr/bin/env python
"""Rebuild the front end's shot-fields and signature fixtures from real shots.

`web/src/test/fixtures/shot-fields.json` is what the shot page's tests render:
`GET /api/shots/{id}/fields` documents, in exactly the shape the route answers.
`web/src/test/fixtures/signature.json` is what the Profiles page's Signature card and
the Set page's override rows render: the answers of `GET /api/profile-versions/{id}/signature`
and of the Set version's override list. Both are generated rather than hand-written, so a
test of a page is a test against what the server really serves.

Shot-fields documents:

* ``lever``: the constructed lever shot (built from a real fixture, see
  ``tests/lever_shot.py``) filed under a version with a 36 g target and linked to its profile
  version, read without a signature: fast flow, a skipped decline phase and a cup over its
  target, with a phase the shot never reached;
* ``leverNoScale`` and ``leverNoPressure``: the same shot as a machine with no
  scale and as a Standard board record it. The channels are zeroed and the flag
  cleared, as the firmware writes them, never left out: the fields come out
  absent;
* ``leverSigned``: the lever shot read against a confirmed signature (the decline phase
  must be reached, the cup at the end of the ramp at most 15 % of the target, the soak's cup
  at most 5 %, the preinfusion's cup at most 5 %, fast flow expected in the ramp, and a
  free-text expectation), so it fails red and amber, shows a grey expected warning, holds one
  and has a line for the reading;
* ``leverSignedNoScale``: the same signature on the shot of a machine with no scale, whose
  cup checks are not measured;
* ``turbo``: a turbo-like profile whose confirmed signature expects ``fast flow``: the
  warning is grey, nothing is amber;
* ``real``: the exported shot, in no Set, with no warning and no target.

Signature documents: ``none`` (a version nobody proposed anything for), ``proposed``,
``confirmed``, ``mixed`` (one confirmed, one rejected with a reason, the rest proposed by a
conversation), ``carried`` (a next version whose soak phase was renamed: the carried ones wait,
one needs a new phase) and ``overrides`` (a Set version's waiting and confirmed overrides).

Re-run it whenever the catalogue, the metrics, the signature routes or the fields route change:

    uv run python scripts/build_web_shot_fields_fixture.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gaggiclanker.api.signatures import SignatureData, _override_out, _signature  # noqa: E402
from gaggiclanker.db.connection import Database  # noqa: E402
from gaggiclanker.db.migrations import run_migrations  # noqa: E402
from gaggiclanker.db.repos import base as repos_base  # noqa: E402
from gaggiclanker.db.repos.beans import BeansRepository, BeanWrite  # noqa: E402
from gaggiclanker.db.repos.chat import ChatRepository, ChatThreadWrite  # noqa: E402
from gaggiclanker.db.repos.grinders import GrindersRepository, GrinderWrite  # noqa: E402
from gaggiclanker.db.repos.profiles import ProfilesRepository  # noqa: E402
from gaggiclanker.db.repos.sets import SetsRepository, SetVersionWrite, SetWrite  # noqa: E402
from gaggiclanker.db.repos.shots import ShotsRepository  # noqa: E402
from gaggiclanker.db.repos.signatures import SignatureRepository  # noqa: E402
from gaggiclanker.domain.exports import ShotExport, shot_export_to_slog, slog_to_raw  # noqa: E402
from gaggiclanker.domain.models import Profile  # noqa: E402
from gaggiclanker.domain.signature import ExpectationInput  # noqa: E402
from gaggiclanker.domain.slog import Slog  # noqa: E402
from gaggiclanker.shotinfo.fields import shot_fields  # noqa: E402
from gaggiclanker.signatures.service import SignatureService  # noqa: E402
from gaggiclanker.sync.derive import derive_shot  # noqa: E402
from tests.lever_shot import (  # noqa: E402
    LEVER_PROFILE,
    TARGET_YIELD_G,
    lever_shot,
    without_pressure,
    without_scale,
)
from tests.signatures.helpers import turbo_profile, turbo_shot  # noqa: E402

EXPORT = REPO / "tests" / "fixtures" / "exports" / "shot-129.json"
FIELDS_TARGET = REPO / "web" / "src" / "test" / "fixtures" / "shot-fields.json"
SIGNATURE_TARGET = REPO / "web" / "src" / "test" / "fixtures" / "signature.json"

RAMP_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "ramp"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.15},
}
SOAK_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "soak"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.05},
}


PREINFUSION_CUP = {
    "channel": "cup_weight",
    "op": "at_end",
    "window": {"phase": "preinfusion"},
    "relative_to": "target_yield",
    "compare": {"op": "<=", "value": 0.05},
}


def lever_signature() -> list[ExpectationInput]:
    """What the constructed lever profile is for, as an agent would propose it."""
    return [
        ExpectationInput(tier="critical", kind="reached", phase="decline"),
        ExpectationInput(tier="critical", kind="measure", expression=RAMP_CUP),
        ExpectationInput(tier="important", kind="measure", expression=SOAK_CUP),
        ExpectationInput(tier="important", kind="measure", expression=PREINFUSION_CUP),
        ExpectationInput(
            tier="important", kind="expects_warning", warning="fast flow", phase="ramp"
        ),
        ExpectationInput(
            tier="context",
            kind="free_text",
            text="pressure and flow fall together through the decline",
            fault="unstable",
        ),
    ]


class _FixedClock:
    """A clock that says one moment, a second later at every reading.

    The repositories stamp every row from `base.utc_now`, so with this in place a re-run writes
    the same bytes, and a fixture changes in a diff only when what it serves changed.
    """

    _start = datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC)
    _ticks = 0

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        cls._ticks += 1
        return cls._start + timedelta(seconds=cls._ticks)


def _plain(value: Any) -> dict[str, Any]:
    plain: dict[str, Any] = json.loads(json.dumps(value))
    return plain


class Scene:
    """A fresh database with the constructed lever profile, a Set on it and shots filed there."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.profiles = ProfilesRepository(db)
        self.sets = SetsRepository(db)
        self.signatures = SignatureRepository(db)
        self.service = SignatureService(db)
        self.version_id = 0
        self.set_id = 0
        self.set_version_id = 0

    async def version(self, document: dict[str, Any]) -> int:
        version, _ = await self.profiles.ensure_version(Profile.model_validate(document))
        return version.id

    async def make_set(self, version_id: int, name: str, target: float) -> int:
        bean = await BeansRepository(self.db).create(
            BeanWrite(name=name, roast_level="medium", process="washed")
        )
        grinder = await GrindersRepository(self.db).create(
            GrinderWrite(name=name, step_unit="numbers")
        )
        row = await self.sets.create(
            SetWrite(name=name, bean_id=bean.id, grinder_id=grinder.id),
            SetVersionWrite(
                profile_version_id=version_id,
                grind_setting="14",
                dose_g=18.0,
                target_yield_g=target,
            ),
        )
        assert row.current_version_id is not None
        self.set_id, self.set_version_id = row.id, row.current_version_id
        return row.current_version_id

    async def add_shot(
        self,
        slog: Slog,
        *,
        profile: dict[str, Any] | None,
        version_id: int | None,
        set_version_id: int | None,
        has_pressure: bool | None = None,
    ) -> int:
        derived = derive_shot(
            slog,
            slog_to_raw(slog),
            device_id="000900",
            source="import",
            profile=profile,
            has_pressure=has_pressure,
        )
        shot = await ShotsRepository(self.db).insert(derived.shot, derived.samples)
        if version_id is not None:
            await self.db.execute(
                "UPDATE shots SET profile_version_id = ? WHERE id = ?", (version_id, shot)
            )
        if set_version_id is not None:
            assert await self.sets.assign_shot(shot, set_version_id)
        return shot

    async def fields(self, shot: int) -> dict[str, Any]:
        found = await shot_fields(self.db, shot)
        assert found is not None
        document = _plain(found.model_dump(mode="json"))
        document["shot_id"] = 1
        return document

    async def signature(self, version_id: int) -> dict[str, Any]:
        data: SignatureData = await _signature(version_id, self.signatures, self.profiles)
        return _plain(data.model_dump(mode="json"))


async def _scene(work: Any) -> Any:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "fixture.db")
        await db.connect()
        try:
            await run_migrations(db)
            return await work(Scene(db))
        finally:
            await db.close()


async def _lever(
    scene: Scene, slog: Slog, *, signed: bool, has_pressure: bool | None = None
) -> Any:
    version = await scene.version(LEVER_PROFILE)
    set_version = await scene.make_set(version, "Constructed lever", TARGET_YIELD_G)
    shot = await scene.add_shot(
        slog,
        profile=LEVER_PROFILE,
        version_id=version,
        set_version_id=set_version,
        has_pressure=has_pressure,
    )
    if signed:
        await scene.service.propose(version, lever_signature(), reason="what the lever is for")
        await scene.signatures.confirm_all(version)
    return await scene.fields(shot)


async def _turbo(scene: Scene) -> Any:
    profile = turbo_profile()
    version = await scene.version(profile)
    set_version = await scene.make_set(version, "Constructed turbo", 42.0)
    shot = await scene.add_shot(
        turbo_shot(), profile=profile, version_id=version, set_version_id=set_version
    )
    await scene.service.propose(
        version,
        [
            ExpectationInput(
                tier="important", kind="expects_warning", warning="fast flow", phase="main"
            )
        ],
        reason="a turbo runs fast on purpose",
    )
    await scene.signatures.confirm_all(version)
    return await scene.fields(shot)


async def _real(scene: Scene, export: Slog) -> Any:
    shot = await scene.add_shot(export, profile=None, version_id=None, set_version_id=None)
    return await scene.fields(shot)


async def _signatures(scene: Scene) -> dict[str, Any]:
    """The signature routes' documents, in every state the card draws."""
    version = await scene.version(LEVER_PROFILE)
    set_version = await scene.make_set(version, "Constructed lever", TARGET_YIELD_G)
    documents: dict[str, Any] = {"none": await scene.signature(version)}

    thread = await ChatRepository(scene.db).create_thread(
        ChatThreadWrite(title="Signature for the lever", set_id=scene.set_id)
    )
    assert thread.thread is not None
    await scene.service.propose(
        version, lever_signature(), reason="what the lever is for", thread_id=thread.thread.id
    )
    documents["proposed"] = await scene.signature(version)
    documents["threadId"] = thread.thread.id

    # One of each answer: the first confirmed, the second rejected with a reason, the rest wait.
    rows = await scene.signatures.for_version(version)
    await scene.signatures.answer(rows[0].id, confirm=True)
    await scene.signatures.answer(
        rows[1].id, confirm=False, reject_reason="too tight for this lever"
    )
    documents["mixed"] = await scene.signature(version)

    # Back to all proposed, then confirmed in one go.
    await scene.db.execute(
        "UPDATE signature_expectations SET status = 'proposed', reject_reason = '', "
        "answered_at = NULL"
    )
    await scene.signatures.confirm_all(version)
    documents["confirmed"] = await scene.signature(version)

    # A next version whose soak phase was renamed: what can be carried is, one cannot.
    renamed = copy.deepcopy(LEVER_PROFILE)
    renamed["label"] = "Constructed lever, renamed soak"
    renamed["phases"][1]["name"] = "bloom"
    next_version = await scene.version(renamed)
    await scene.service.carry(version, next_version)
    documents["carried"] = await scene.signature(next_version)

    ramp = next(
        r
        for r in await scene.signatures.for_version(version)
        if r.phase == "ramp" and r.kind == "measure"
    )
    waiting = await scene.service.propose_override(
        set_version_id=set_version,
        profile_version_id=version,
        expectation_id=ramp.id,
        compare={"op": "<=", "value": 0.2},
        reason="a coarser bean",
        thread_id=thread.thread.id,
    )
    documents["overrideWaiting"] = _plain(
        (await _override_out(waiting, scene.signatures)).model_dump(mode="json")
    )
    await scene.signatures.answer_override(waiting.id, confirm=True)
    confirmed = await scene.signatures.get_override(waiting.id)
    assert confirmed is not None
    documents["overrideConfirmed"] = _plain(
        (await _override_out(confirmed, scene.signatures)).model_dump(mode="json")
    )
    documents["setId"] = scene.set_id
    documents["setVersionId"] = set_version
    documents["profileVersionId"] = version
    return documents


def format_like_biome(path: Path) -> None:
    """Run the project's formatter over the file, so `npm run check` accepts it as written.

    The front end's checks include its fixtures, and Biome lays JSON out its own way
    (short arrays on a line); a plain `json.dumps` differs from it on every regeneration.
    """
    web = REPO / "web"
    try:
        subprocess.run(
            ["npx", "--no-install", "biome", "format", "--write", str(path)],
            cwd=web,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - dev tooling
        raise SystemExit(
            f"could not format {path.name} with Biome (npm ci in web/?): {exc}"
        ) from exc


def _write(target: Path, documents: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(documents, indent=1) + "\n", encoding="utf-8")
    format_like_biome(target)
    print(f"wrote {target.relative_to(REPO)}")


async def build() -> None:
    vars(repos_base)["datetime"] = _FixedClock
    export = shot_export_to_slog(ShotExport.model_validate(json.loads(EXPORT.read_text())))
    fields = {
        "lever": await _scene(lambda s: _lever(s, lever_shot(), signed=False)),
        "leverNoScale": await _scene(
            lambda s: _lever(s, without_scale(lever_shot()), signed=False)
        ),
        "leverNoPressure": await _scene(
            lambda s: _lever(s, without_pressure(lever_shot()), signed=False, has_pressure=False)
        ),
        "leverSigned": await _scene(lambda s: _lever(s, lever_shot(), signed=True)),
        "leverSignedNoScale": await _scene(
            lambda s: _lever(s, without_scale(lever_shot()), signed=True)
        ),
        "turbo": await _scene(_turbo),
        "real": await _scene(lambda s: _real(s, export)),
    }
    _write(FIELDS_TARGET, fields)
    _write(SIGNATURE_TARGET, await _scene(_signatures))


if __name__ == "__main__":
    asyncio.run(build())
