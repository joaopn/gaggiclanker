"""Every copy of "what temperature does this profile brew at", pinned together.

A Set version records no temperature: it is read from the profile the version
names. `domain/profile_recipe.py` is the rule — the profile's own `temperature`
when it is a number above 0, because the firmware writes 0 for "not set" — and
Python reads it exactly once, there.

SQL cannot call Python, so four queries repeat that rule: the version select in
`db/repos/sets.py`, the similar-Sets query in `starting/similar.py`, and the
`profile_temperature_c` column of `v_set_versions` and `v_shots`. Four copies of
one sentence is four chances to drift, and the drift would be silent — a Set
page and the chat's SQL quietly disagreeing about what a shot was brewed at.

So this file is the pin. It builds one Set per document in a table of profiles
that includes every shape the rule has to answer for, and asserts that all four
queries return *exactly* what `profile_recipe` returns for the same document.
The documents go in through raw SQL on purpose: `Profile` refuses most of them,
and the point is that a copy which resolved them differently could not survive
here even though no valid profile could produce one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from gaggiclanker.db.connection import Database
from gaggiclanker.db.repos.sets import SetVersionWrite, SetWrite
from gaggiclanker.domain.profile_recipe import profile_recipe
from gaggiclanker.starting.similar import similar_sets
from tests.sets.conftest import Fixtures, make_shot

#: The phases every document below carries, so the only thing that differs is
#: the field under test.
_PHASES: list[dict[str, Any]] = [
    {
        "name": "Extraction",
        "phase": "brew",
        "valve": 1,
        "duration": 30,
        "pump": {"target": "pressure", "pressure": 9, "flow": 0},
        "targets": [{"type": "volumetric", "operator": "gte", "value": 36}],
    }
]

#: `(name, what the document says about `temperature`)`. The first six are
#: shapes a real profile reaches this code with; the rest are shapes only a
#: hand-edited row could hold, and they are here because a SQL copy that
#: answered them differently from Python would be a copy that had drifted.
_DOCUMENTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("missing", {}),
    ("null", {"temperature": None}),
    ("zero", {"temperature": 0}),
    ("zero-float", {"temperature": 0.0}),
    ("negative", {"temperature": -5}),
    ("integer", {"temperature": 93}),
    ("float", {"temperature": 93.5}),
    ("numeric-string", {"temperature": "93"}),
    ("true", {"temperature": True}),
    ("word", {"temperature": "hot"}),
    ("list", {"temperature": [93]}),
    ("object", {"temperature": {"value": 93}}),
)


async def _profile_version(db: Database, name: str, document: dict[str, Any]) -> int:
    """A `profile_versions` row holding this document, by raw insert.

    Not through `ProfilesRepository.ensure_version`: that takes a `Profile`, and
    half of this table is documents the model refuses. The row is what the four
    queries read, so the row is what this test writes.
    """
    cursor = await db.execute(
        """
        INSERT INTO profile_versions (content_hash, label, type, json, source)
        VALUES (:hash, :label, 'standard', :json, 'device')
        """,
        {
            "hash": f"hash-{name}",
            "label": f"Profile {name}",
            "json": json.dumps({"label": f"Profile {name}", "phases": _PHASES, **document}),
        },
    )
    return int(cursor.lastrowid or 0)


@pytest.mark.parametrize(("name", "document"), _DOCUMENTS, ids=[row[0] for row in _DOCUMENTS])
async def test_every_query_reads_the_temperature_the_way_python_does(
    wired: Fixtures, name: str, document: dict[str, Any]
) -> None:
    profile_version_id = await _profile_version(wired.db, name, document)
    stored = await wired.db.fetch_value(
        "SELECT json FROM profile_versions WHERE id = ?", (profile_version_id,)
    )
    expected = profile_recipe(json.loads(str(stored))).temperature_c

    row = await wired.sets.create(
        SetWrite(name=f"Set {name}", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(profile_version_id=profile_version_id, dose_g=18.0, target_yield_g=36.0),
    )
    version_id = row.current_version_id
    assert version_id is not None
    shot_id = await make_shot(wired.db, f"dev-{name}", profile_version_id=profile_version_id)
    await wired.sets.assign_shot(shot_id, version_id)

    # 1. the repository's version select, which is what a Set page reads.
    version = await wired.sets.get_version(version_id)
    assert version is not None
    assert version.profile_temperature_c == expected, "the version select drifted"

    # 2 and 3. the two curated views the chat writes SQL over.
    for view, key, value in (
        ("v_set_versions", "set_version_id", version_id),
        ("v_shots", "shot_id", shot_id),
    ):
        served = await wired.db.fetch_value(
            f"SELECT profile_temperature_c FROM {view} WHERE {key} = ?",  # noqa: S608 - literals
            (value,),
        )
        assert served == expected, f"{view} drifted"

    # 4. the similar-Sets query behind the starting point's free half.
    similar = await similar_sets(
        wired.db,
        roast_level="light",
        process="natural",
        origin=None,
        grinder_id=wired.grinder_id,
        limit=20,
    )
    anchor = next(entry for entry in similar if entry.set_version_id == version_id)
    assert anchor.profile_temperature_c == expected, "the similar-Sets query drifted"


async def test_a_profile_that_states_no_temperature_is_not_a_zero_degree_shot(
    wired: Fixtures,
) -> None:
    """The firmware's 0 means "not set", and every reader has to say so.

    Spelled out beside the table above because it is the one value a plausible
    `>= 0` would turn into a 0 °C brew temperature on the Set page, in the
    chat's views and on a starting-point card at once.
    """
    profile_version_id = await _profile_version(wired.db, "unset", {"temperature": 0})
    row = await wired.sets.create(
        SetWrite(name="Says nothing", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(profile_version_id=profile_version_id, dose_g=18.0),
    )
    version_id = row.current_version_id
    assert version_id is not None
    shot_id = await make_shot(wired.db, "dev-unset", profile_version_id=profile_version_id)
    await wired.sets.assign_shot(shot_id, version_id)

    version = await wired.sets.get_version(version_id)
    assert version is not None and version.profile_temperature_c is None
    assert (
        await wired.db.fetch_value(
            "SELECT profile_temperature_c FROM v_set_versions WHERE set_version_id = ?",
            (version_id,),
        )
        is None
    )
    assert (
        await wired.db.fetch_value(
            "SELECT profile_temperature_c FROM v_shots WHERE shot_id = ?", (shot_id,)
        )
        is None
    )
    similar = await similar_sets(
        wired.db,
        roast_level="light",
        process="natural",
        origin=None,
        grinder_id=wired.grinder_id,
        limit=20,
    )
    anchor = next(entry for entry in similar if entry.set_version_id == version_id)
    assert anchor.profile_temperature_c is None


def test_every_sql_copy_of_the_rule_is_the_same_sentence() -> None:
    """The copies a fresh database never runs still have to agree.

    `0013` and `0014` create the two views and `0016` re-creates them, so on a
    fresh archive only the last copy is ever executed and a mistake in the
    earlier two is invisible at runtime — until somebody rebuilds a view from
    the migration that defines it, or reads one to learn what a column means.
    The text is API (`describe_schema` reads these names out), so it is checked
    as text: every copy of the expression, in the migrations and in the two
    queries beside them, is one sentence.
    """
    source = Path(__file__).resolve().parents[2] / "gaggiclanker"
    roots = [
        source / "db" / "repos" / "sets.py",
        source / "starting" / "similar.py",
        *sorted((source / "db" / "migrations").glob("*.sql")),
    ]
    # Anchored on `pv.json`, which every copy of *this* rule reads: the same
    # files hold other guarded `json_extract`s now (the spread's measures), and
    # a pattern that started at any `CASE WHEN json_type(` would swallow one of
    # those and the prose between it and the temperature.
    pattern = re.compile(
        r"CASE WHEN json_type\(pv\.json.*?END AS profile_temperature_c", re.DOTALL | re.IGNORECASE
    )
    found: dict[str, list[str]] = {}
    for path in roots:
        text = path.read_text()
        if "profile_temperature_c" not in text:
            continue
        copies = [" ".join(match.split()) for match in pattern.findall(text)]
        assert copies, f"{path} names the column without the shared expression"
        # Every place that *produces* the column, not every place that mentions
        # it: `sets.py` also reads it in Python, where the rule lives already.
        assert text.count("AS profile_temperature_c") == len(copies), (
            f"{path} has a copy that does not use the shared expression"
        )
        found[path.name] = copies

    everywhere = {copy for copies in found.values() for copy in copies}
    assert len(everywhere) == 1, f"the copies disagree: {everywhere}"
    # The sentence itself, so that changing it is a deliberate edit here too.
    expression = everywhere.pop()
    assert "json_type" in expression and "'integer', 'real'" in expression
    assert "> 0" in expression and ">= 0" not in expression
    # Both views in each of the three migrations that define them, plus the
    # repository's version select and the similar-Sets query.
    assert sum(len(copies) for copies in found.values()) == 8, found
