"""A Set version's name: v<major>.<minor>, numbered by the repository, one rule for every path.

What is pinned here is the repository's numbering on a real database — the
sequence a person sees, what a roll back and a filled design are called, that
the unique index refuses a second copy of a name — and the default rule as
each path that appends a version applies it: the Add a version form, an
accepted proposal, a pushed draft (in `tests/drafts/`), a roll back. The pure
rule is in `tests/domain/test_version_names.py`.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
from gaggiclanker.db.repos.sets import (
    RollbackWrite,
    SetsRepository,
    SetVersionPatch,
    SetVersionWrite,
    SetWrite,
)
from gaggiclanker.db.repos.version_names import label_sql, next_names
from gaggiclanker.domain.sets import version_label
from tests.sets.conftest import Fixtures, make_profile_version

PREDICTION = "Two seconds longer and a touch sweeter than the version before it."


async def _set(wired: Fixtures, profile: int | None = None) -> int:
    row = await wired.sets.create(
        SetWrite(name="Guji on the Niche", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        SetVersionWrite(profile_version_id=profile, grind_setting="22", dose_g=18),
    )
    return row.id


async def _add(
    sets: SetsRepository, set_id: int, major: bool | None = None, **fields: object
) -> str:
    version = await sets.add_version(set_id, SetVersionPatch.model_validate(fields), major=major)
    assert version is not None
    return version.version_label


# -- numbering ----------------------------------------------------------------


async def test_minor_and_major_versions_number_as_the_person_reads_them(wired: Fixtures) -> None:
    set_id = await _set(wired)
    first = await wired.sets.current_version(set_id)
    assert first is not None
    assert (first.version_major, first.version_minor) == (1, 0)
    assert first.version_label == "v1"

    labels = [
        await _add(wired.sets, set_id, major=False, grind_setting="21"),
        await _add(wired.sets, set_id, major=False, grind_setting="20"),
        await _add(wired.sets, set_id, major=True, dose_g=19),
        await _add(wired.sets, set_id, major=False, grind_setting="19"),
    ]

    assert labels == ["v1.1", "v1.2", "v2", "v2.1"]
    versions = await wired.sets.versions(set_id)
    # Newest made first, and the Set is on the last one made.
    assert [(v.version_major, v.version_minor) for v in versions] == [
        (2, 1),
        (2, 0),
        (1, 2),
        (1, 1),
        (1, 0),
    ]
    row = await wired.sets.get(set_id)
    assert row is not None
    assert row.current_version_label == "v2.1"


async def test_the_minor_counts_within_the_current_major_not_across_the_set(
    wired: Fixtures,
) -> None:
    """v1.1, v1.2, then v2: the next minor is v2.1, not v2.3."""
    set_id = await _set(wired)
    await _add(wired.sets, set_id, major=False, grind_setting="21")
    await _add(wired.sets, set_id, major=False, grind_setting="20")
    await _add(wired.sets, set_id, major=True, dose_g=19)

    assert await _add(wired.sets, set_id, major=False, dose_g=18.5) == "v2.1"


async def test_the_next_names_are_the_names_the_insert_then_writes(wired: Fixtures) -> None:
    set_id = await _set(wired)
    await _add(wired.sets, set_id, major=False, grind_setting="21")

    names = await next_names(wired.db, set_id)
    assert (names.minor, names.major) == ("v1.2", "v2")
    # The Set row serves the same two names from its own query.
    row = await wired.sets.get(set_id)
    assert row is not None
    assert (row.next_minor_label, row.next_major_label) == (names.minor, names.major)
    assert await _add(wired.sets, set_id, major=True, dose_g=19) == names.major
    names = await next_names(wired.db, set_id)
    assert (names.minor, names.major) == ("v2.1", "v3")
    assert await _add(wired.sets, set_id, major=False, dose_g=18) == names.minor


async def test_a_design_filled_in_place_stays_v1(wired: Fixtures) -> None:
    from gaggiclanker.db.repos.sets import DesignBrief

    row = await wired.sets.create_design(
        SetWrite(name="Guji, designed", bean_id=wired.bean_id, grinder_id=wired.grinder_id),
        DesignBrief(),
    )
    names = await next_names(wired.db, row.id)
    assert (names.minor, names.major) == ("v1", "v1")
    assert (row.next_minor_label, row.next_major_label) == ("v1", "v1")
    profile = await make_profile_version(wired.db, "Designed")

    # Even marked major, and even though it names a profile the empty v1 did
    # not: filling the design is not a change to anything.
    filled = await wired.sets.add_version(
        row.id, SetVersionPatch(profile_version_id=profile, dose_g=18), major=True
    )

    assert filled is not None
    assert (filled.version_major, filled.version_minor) == (1, 0)
    assert await _add(wired.sets, row.id, dose_g=19) == "v1.1"


async def test_the_unique_index_refuses_a_second_copy_of_a_name(wired: Fixtures) -> None:
    set_id = await _set(wired)
    await _add(wired.sets, set_id, major=False, grind_setting="21")

    with pytest.raises(sqlite3.IntegrityError):
        await wired.db.execute(
            "INSERT INTO set_versions (set_id, version_major, version_minor, "
            "created_at) VALUES (?, 1, 1, 'x')",
            (set_id,),
        )
    # Another Set may of course have its own v1.1.
    other = await _set(wired)
    assert await _add(wired.sets, other, major=False, grind_setting="21") == "v1.1"


# -- the default rule, path by path --------------------------------------------


async def test_the_form_defaults_a_grind_dose_or_yield_change_to_minor(wired: Fixtures) -> None:
    profile = await make_profile_version(wired.db, "House")
    set_id = await _set(wired, profile)

    assert await _add(wired.sets, set_id, grind_setting="21") == "v1.1"
    assert await _add(wired.sets, set_id, dose_g=18.5) == "v1.2"
    assert await _add(wired.sets, set_id, target_yield_g=40) == "v1.3"
    # Sending the same profile is not a change of profile.
    assert await _add(wired.sets, set_id, profile_version_id=profile) == "v1.4"


async def test_the_form_defaults_a_different_profile_to_major(wired: Fixtures) -> None:
    house = await make_profile_version(wired.db, "House")
    other = await make_profile_version(wired.db, "Turbo")
    set_id = await _set(wired, house)

    assert await _add(wired.sets, set_id, profile_version_id=other) == "v2"
    # "Any profile" is a different profile too.
    assert await _add(wired.sets, set_id, profile_version_id=None) == "v3"


async def test_an_explicit_answer_wins_in_both_directions(wired: Fixtures) -> None:
    house = await make_profile_version(wired.db, "House")
    other = await make_profile_version(wired.db, "Turbo")
    set_id = await _set(wired, house)

    assert await _add(wired.sets, set_id, major=True, grind_setting="21") == "v2"
    assert await _add(wired.sets, set_id, major=False, profile_version_id=other) == "v2.1"


async def _proposal(
    wired: Fixtures, set_id: int, **patch: object
) -> tuple[SetProposalsRepository, int]:
    proposals = SetProposalsRepository(wired.db)
    result = await proposals.create(
        set_id,
        ProposalWrite(
            patch=SetVersionPatch.model_validate(patch),
            reason="Walking the rule.",
            prediction=PREDICTION,
        ),
    )
    assert result.proposal is not None, result.refused
    return proposals, result.proposal.id


@pytest.mark.parametrize(
    ("change", "major", "label"),
    [
        ({"grind_setting": "21"}, None, "v1.1"),
        ("profile", None, "v2"),
        ({"grind_setting": "21"}, True, "v2"),
        ("profile", False, "v1.1"),
    ],
)
async def test_an_accepted_proposal_follows_the_rule_unless_the_person_says(
    wired: Fixtures, change: object, major: bool | None, label: str
) -> None:
    house = await make_profile_version(wired.db, "House")
    other = await make_profile_version(wired.db, "Turbo")
    set_id = await _set(wired, house)
    patch = {"profile_version_id": other} if change == "profile" else change
    assert isinstance(patch, dict)
    proposals, proposal_id = await _proposal(wired, set_id, **patch)
    stored = await proposals.get(set_id, proposal_id)
    assert stored is not None
    assert await proposals.default_major(stored) is (change == "profile")

    accepted = await proposals.accept(set_id, proposal_id, major=major)

    assert accepted.version is not None
    assert accepted.version.version_label == label
    assert accepted.proposal is not None
    assert accepted.proposal.resulting_version_label == label


async def test_the_agent_s_suggestion_is_not_the_default(wired: Fixtures) -> None:
    """The agent suggests; only the person's answer makes a grind change major."""
    set_id = await _set(wired)
    proposals = SetProposalsRepository(wired.db)
    result = await proposals.create(
        set_id,
        ProposalWrite(
            patch=SetVersionPatch(grind_setting="21"),
            reason="Walking the rule.",
            prediction=PREDICTION,
            suggest_major=True,
            major_reason="The agent thinks this grind is a new direction for the Set.",
        ),
    )
    assert result.proposal is not None
    assert (result.proposal.suggest_major, result.proposal.major_reason) == (
        True,
        "The agent thinks this grind is a new direction for the Set.",
    )
    assert await proposals.default_major(result.proposal) is False

    accepted = await proposals.accept(set_id, result.proposal.id)

    assert accepted.version is not None
    assert accepted.version.version_label == "v1.1"


async def test_a_revert_continues_the_line_it_went_back_to(wired: Fixtures) -> None:
    """v2.1 back to v1.2 is v1.2 again, and the next minor is v1.3.

    The names are identifiers, not a sequence, so the whole walk is one test on a
    real database: a revert writes no row, the next minor is the next free minor of the
    major the Set is on, and the next major is the highest major + 1.
    """
    set_id = await _set(wired)
    ids: dict[str, int] = {}

    async def current_label() -> str:
        row = await wired.sets.get(set_id)
        assert row is not None
        return row.current_version_label

    async def add(expected: str, *, major: bool, **fields: object) -> None:
        version = await wired.sets.add_version(
            set_id, SetVersionPatch.model_validate(fields), major=major
        )
        assert version is not None and version.version_label == expected
        ids[expected] = version.id

    async def revert(label: str) -> None:
        result = await wired.sets.rollback(set_id, RollbackWrite(to_version_id=ids[label]))
        assert result.refused is None
        assert await current_label() == label

    first = await wired.sets.current_version(set_id)
    assert first is not None
    ids["v1"] = first.id
    await add("v1.1", major=False, grind_setting="21")
    await add("v1.2", major=False, grind_setting="20")
    await add("v2", major=True, dose_g=19)
    await add("v2.1", major=False, grind_setting="19")
    rows = await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions")

    await revert("v1.2")
    assert await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions") == rows
    row = await wired.sets.get(set_id)
    assert row is not None and (row.next_minor_label, row.next_major_label) == ("v1.3", "v3")
    await add("v1.3", major=False, grind_setting="18")
    await revert("v2.1")
    await add("v2.2", major=False, grind_setting="17")
    await revert("v1.2")
    # v1.3 is taken, so the next free minor of major 1 is v1.4.
    await add("v1.4", major=False, grind_setting="16")
    await add("v3", major=True, dose_g=20)

    # Row count: one per write that made a version, nothing for the three reverts.
    assert await wired.db.fetch_value("SELECT COUNT(*) FROM set_versions") == 9
    assert len(await wired.sets.reverts(set_id)) == 3
    # Ancestry is the fork history: v1.3 and v1.4 both came off v1.2.
    for label in ("v1.3", "v1.4"):
        version = await wired.sets.get_version(ids[label])
        assert version is not None and version.parent_version_id == ids["v1.2"]


# -- the label in SQL is the label in Python -------------------------------------


async def test_the_sql_label_is_the_python_label(wired: Fixtures) -> None:
    for major, minor in [(1, 0), (1, 1), (2, 0), (2, 13), (10, 0), (10, 2)]:
        found = await wired.db.fetch_value(
            f"SELECT {label_sql('v')} FROM (SELECT ? AS version_major, ? AS version_minor) v",  # noqa: S608 - a constant expression
            (major, minor),
        )
        assert found == version_label(major, minor)
    # A LEFT JOIN that found nothing is no label, not "v".
    nothing = await wired.db.fetch_value(
        f"SELECT {label_sql('v')} FROM (SELECT NULL AS version_major, NULL AS version_minor) v"  # noqa: S608 - a constant expression
    )
    assert nothing is None


def test_every_view_spells_the_label_as_the_repository_does() -> None:
    """The views repeat `label_sql` because a view cannot call Python; same text."""
    schema = (
        Path(__file__).resolve().parents[2] / "gaggiclanker" / "db" / "schema.sql"
    ).read_text()
    pattern = re.compile(r"'v' \|\| (\w+)\.version_major.*?ELSE '' END", re.DOTALL)
    copies = [(m.group(1), " ".join(m.group(0).split())) for m in pattern.finditer(schema)]
    assert len(copies) == 6, copies
    for alias, text in copies:
        assert f"({text})" == " ".join(label_sql(alias).split())
