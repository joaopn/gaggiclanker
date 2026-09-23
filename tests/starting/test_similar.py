"""The similar-Set query: what it scores, what it excludes, and in what order.

The fixture is built so every assertion here is about one term of the score at a
time — see `conftest.py` for the table and why each row is in it.
"""

from __future__ import annotations

from gaggiclanker.starting.similar import SimilarSet, similar_sets
from tests.starting.conftest import Fixture

#: What the new bag is: light, washed, Kenyan. Spelled once, because every test
#: here varies exactly one of these and the rest have to stay put for the
#: variation to mean anything.
_UNSET: str = "\0"


async def _for_the_new_bag(
    fixture: Fixture,
    *,
    roast_level: str | None = _UNSET,
    process: str | None = _UNSET,
    origin: str | None = _UNSET,
    decaf: bool = False,
    grinder_id: int | None = -1,
    exclude_set_id: int | None = None,
) -> list[SimilarSet]:
    return await similar_sets(
        fixture.db,
        roast_level="light" if roast_level is _UNSET else roast_level,
        process="washed" if process is _UNSET else process,
        origin="Kenya" if origin is _UNSET else origin,
        decaf=decaf,
        grinder_id=fixture.grinder_id if grinder_id == -1 else grinder_id,
        exclude_set_id=exclude_set_id,
        limit=5,
    )


async def test_the_best_match_is_the_one_that_matches_on_everything(fixture: Fixture) -> None:
    """Same roast, same process, same origin, five well-rated shots."""
    rows = await _for_the_new_bag(fixture)
    assert rows, "the fixture archive should offer at least one anchor"
    best = rows[0]
    assert best.set_version_id == fixture.versions["kenya"]
    assert best.roast_match == "same"
    assert best.process_match is True
    assert best.origin_match is True
    assert best.attribute_score == 6.0


async def test_the_temperature_reported_is_the_profiles(fixture: Fixture) -> None:
    """What that Set actually brewed at, not what somebody wrote on it.

    The card is read to copy numbers off, so this one has to be true: it is the
    temperature in the profile the Set names. A Set naming no profile reports
    none rather than a plausible default.
    """
    from gaggiclanker.db.repos.profiles import ProfilesRepository
    from gaggiclanker.domain.models import Profile

    version = await ProfilesRepository(fixture.db).get_version(fixture.profile_version_id)
    assert version is not None and version.profile is not None
    hotter, _ = await ProfilesRepository(fixture.db).ensure_version(
        Profile.model_validate({**version.profile, "temperature": 96.0})
    )
    await fixture.db.execute(
        "UPDATE set_versions SET profile_version_id = ? WHERE id = ?",
        (hotter.id, fixture.versions["kenya"]),
    )
    await fixture.db.execute(
        "UPDATE set_versions SET profile_version_id = NULL WHERE id = ?",
        (fixture.versions["guji"],),
    )

    # The firmware writes 0 for "not set": that is no temperature, not a 0 °C
    # shot, and a card offering 0 °C would be advice to freeze the group.
    unset, _ = await ProfilesRepository(fixture.db).ensure_version(
        Profile.model_validate({**version.profile, "temperature": 0})
    )
    await fixture.db.execute(
        "UPDATE set_versions SET profile_version_id = ? WHERE id = ?",
        (unset.id, fixture.versions["sumatra"]),
    )

    rows = {row.set_version_id: row for row in await _for_the_new_bag(fixture, grinder_id=None)}
    assert rows[fixture.versions["kenya"]].profile_temperature_c == 96.0
    assert rows[fixture.versions["guji"]].profile_temperature_c is None
    assert rows[fixture.versions["sumatra"]].profile_temperature_c is None
    # Everything still on the untouched profile reports its 93 °C.
    assert rows[fixture.versions["brazil"]].profile_temperature_c == 93.0


async def test_an_adjacent_roast_scores_one_and_two_steps_away_scores_nothing(
    fixture: Fixture,
) -> None:
    """`medium-dark` is two steps from `light`, so its roast term is zero.

    The Brazil Set still appears — it matches on process — which is the point:
    adjacency has to be an index step on the ordered scale rather than "not the
    same", or every roast level would score.
    """
    rows = await _for_the_new_bag(fixture)
    by_version = {row.set_version_id: row for row in rows}
    brazil = by_version[fixture.versions["brazil"]]
    assert brazil.roast_match == "none"
    assert brazil.process_match is True
    assert brazil.attribute_score == 2.0

    # ... and with a medium-light bag, `light` is genuinely one step away.
    adjacent = await _for_the_new_bag(
        fixture, roast_level="medium-light", origin=None, process=None
    )
    guji = {row.set_version_id: row for row in adjacent}[fixture.versions["guji"]]
    assert guji.roast_match == "adjacent"
    assert guji.attribute_score == 1.0


async def test_a_version_with_no_shots_is_never_offered(fixture: Fixture) -> None:
    """The Untouched Set matches on all three attributes and has never been brewed.

    On attributes alone it would tie with Kenya AA for first place. It is
    excluded outright, because the whole point of the query is to find results
    rather than intentions.
    """
    rows = await _for_the_new_bag(fixture)
    assert fixture.versions["untouched"] not in {row.set_version_id for row in rows}


async def test_the_grinder_is_a_filter_not_a_score_term(fixture: Fixture) -> None:
    """The Sumatra Set matches on everything and is on the other grinder.

    It has the best outcome in the archive, so if the grinder were merely
    weighted it would come first — and its grind setting, on a scale the user
    does not have, would be the number on the card.
    """
    rows = await _for_the_new_bag(fixture)
    assert fixture.versions["sumatra"] not in {row.set_version_id for row in rows}

    on_the_other = await _for_the_new_bag(fixture, grinder_id=fixture.other_grinder_id)
    assert [row.set_version_id for row in on_the_other] == [fixture.versions["sumatra"]]

    # No grinder at all lifts the filter rather than matching none.
    unfiltered = await _for_the_new_bag(fixture, grinder_id=None)
    assert fixture.versions["sumatra"] in {row.set_version_id for row in unfiltered}


async def test_the_outcome_term_rewards_shots_and_ratings(fixture: Fixture) -> None:
    """Five shots at 4.4 stars beat three at 3, and the numbers are on the row."""
    rows = await _for_the_new_bag(fixture)
    by_version = {row.set_version_id: row for row in rows}
    kenya = by_version[fixture.versions["kenya"]]
    guji = by_version[fixture.versions["guji"]]

    assert kenya.outcome.shots == 5
    assert kenya.outcome.mean_rating == 4.4
    assert guji.outcome.shots == 3
    assert guji.outcome.mean_rating == 3.0
    # Guji is at 3/5 shots, so its outcome term is scaled to 60 %.
    assert kenya.outcome_score > guji.outcome_score
    assert kenya.score == round(kenya.attribute_score + kenya.outcome_score, 3)


async def test_a_version_with_no_judgements_still_scores_on_execution(
    fixture: Fixture,
) -> None:
    """A rating nobody gave is ``None``, not a zero somebody would average."""
    await fixture.db.execute("DELETE FROM shot_judgements")
    rows = await _for_the_new_bag(fixture)
    kenya = {row.set_version_id: row for row in rows}[fixture.versions["kenya"]]
    assert kenya.outcome.mean_rating is None
    assert kenya.outcome.mean_execution_score is not None
    assert kenya.outcome_score > 0


async def test_the_ordering_is_stable_across_repeated_calls(fixture: Fixture) -> None:
    """Determinism, asserted directly rather than inferred.

    The wizard renders these as cards and a list that reshuffles between two
    presses of the same button is a list nobody trusts.
    """
    first = await _for_the_new_bag(fixture)
    second = await _for_the_new_bag(fixture)
    assert [row.model_dump() for row in first] == [row.model_dump() for row in second]


async def test_ties_break_on_shots_then_on_the_newest_version(fixture: Fixture) -> None:
    """Two versions identical on everything come back newest-first.

    Built by hand rather than seeded, because a genuine tie is exactly what the
    fixture's readable rows avoid — and it is the case where SQLite would
    otherwise be free to choose.
    """
    rows = await _for_the_new_bag(fixture)
    original = {row.set_version_id: row for row in rows}[fixture.versions["kenya"]]

    # A second version of the same Set, with the same shots moved onto it,
    # would change the first one's shot count — so copy the *Set* instead.
    await fixture.db.execute(
        """
        INSERT INTO sets (name, bean_id, grinder_id, archived, automatch, created_at)
        SELECT name || ' (copy)', bean_id, grinder_id, archived, 0, created_at
          FROM sets WHERE id = ?
        """,
        (fixture.sets["kenya"],),
    )
    copy_id = int(await fixture.db.fetch_value("SELECT last_insert_rowid()") or 0)
    await fixture.db.execute(
        """
        INSERT INTO set_versions (set_id, version_no, profile_version_id, grind_setting,
                                  grind_value, dose_g, target_yield_g,
                                  intent, origin, created_at)
        SELECT ?, 1, profile_version_id, grind_setting, grind_value, dose_g, target_yield_g,
               intent, origin, created_at
          FROM set_versions WHERE id = ?
        """,
        (copy_id, fixture.versions["kenya"]),
    )
    copy_version = int(await fixture.db.fetch_value("SELECT last_insert_rowid()") or 0)
    # Give the copy the same five verdicts on new shots, so the two tie exactly.
    for index in range(5):
        cursor = await fixture.db.execute(
            """
            INSERT INTO shots (device_id, raw_slog, started_at, duration_ms,
                               execution_score, set_version_id, synced_at, updated_at)
            VALUES (?, x'00', '2026-02-20T08:00:00.000Z', 28000, ?, ?,
                    '2026-02-20T08:00:00.000Z', '2026-02-20T08:00:00.000Z')
            """,
            (f"9000{index:02d}", 8.8 + index * 0.1, copy_version),
        )
        await fixture.db.execute(
            "INSERT INTO shot_judgements (shot_id, rating, dose_in_g, dose_out_g, updated_at) "
            "VALUES (?, ?, 18.0, 45.0, '2026-02-20T08:00:00.000Z')",
            (int(cursor.lastrowid or 0), 4 + (index % 2)),
        )

    tied = await _for_the_new_bag(fixture)
    ranked = [row.set_version_id for row in tied]
    assert ranked.index(copy_version) < ranked.index(original.set_version_id), (
        "an exact tie breaks on the newer version id"
    )


async def test_an_unstated_roast_level_scores_nothing_rather_than_everything(
    fixture: Fixture,
) -> None:
    """A bag that does not say gets no roast term — and no neighbours either."""
    rows = await _for_the_new_bag(fixture, roast_level=None, process=None, origin=None)
    assert rows, "outcome alone still ranks them"
    assert all(row.attribute_score == 0.0 for row in rows)
    assert all(row.roast_match == "none" for row in rows)


async def test_a_set_can_be_excluded_from_its_own_suggestions(fixture: Fixture) -> None:
    rows = await _for_the_new_bag(fixture, exclude_set_id=fixture.sets["kenya"])
    assert fixture.versions["kenya"] not in {row.set_version_id for row in rows}


async def test_a_quarantined_shot_does_not_count_as_evidence(fixture: Fixture) -> None:
    """Its bytes never parsed, so it has nothing to say about the recipe."""
    await fixture.db.execute(
        "UPDATE shots SET quarantined = 1 WHERE set_version_id = ?",
        (fixture.versions["guji"],),
    )
    rows = await _for_the_new_bag(fixture)
    assert fixture.versions["guji"] not in {row.set_version_id for row in rows}


async def test_a_decaf_set_never_outranks_a_caffeinated_exact_match(fixture: Fixture) -> None:
    """Decaf is a penalty, not a filter — but a big enough one to settle ties.

    Decaf is a different coffee hydraulically. Excluding it outright would leave
    a kitchen that drinks decaf in the evening with nothing to anchor on, so it
    costs two points: enough that an otherwise identical caffeinated Set always
    wins, not so much that a decaf Set with real shots behind it disappears.
    """
    # Make the Guji Set decaf. On attributes it was second (roast only, 3);
    # against a caffeinated bag it now scores 1.
    await fixture.db.execute(
        "UPDATE beans SET decaf = 1 WHERE id = (SELECT bean_id FROM sets WHERE id = ?)",
        (fixture.sets["guji"],),
    )
    rows = await _for_the_new_bag(fixture)
    by_version = {row.set_version_id: row for row in rows}
    guji = by_version[fixture.versions["guji"]]
    assert guji.decaf_match is False
    assert guji.attribute_score == 1.0
    # Still offered — it has shots behind it and is the only other light roast.
    assert rows[0].set_version_id == fixture.versions["kenya"]

    # ... and for a decaf bag the sign flips: the decaf Set is now the match and
    # every caffeinated one takes the penalty.
    for_decaf = await _for_the_new_bag(fixture, decaf=True)
    decaf_rows = {row.set_version_id: row for row in for_decaf}
    assert decaf_rows[fixture.versions["guji"]].decaf_match is True
    assert decaf_rows[fixture.versions["kenya"]].decaf_match is False
    assert decaf_rows[fixture.versions["kenya"]].attribute_score == 4.0


async def test_a_decaf_mismatch_is_spelled_out_in_the_prompt(fixture: Fixture) -> None:
    """A card that looks like a match and is not has to say so.

    A model that copies a decaf Set's grind for a caffeinated bag has been
    misled by the card, not by its own reasoning.
    """
    from gaggiclanker.starting.context import build_context
    from tests.starting.conftest import AS_OF

    await fixture.db.execute(
        "UPDATE beans SET decaf = 1 WHERE id = (SELECT bean_id FROM sets WHERE id = ?)",
        (fixture.sets["kenya"],),
    )
    context = await build_context(
        fixture.db,
        bean_id=fixture.new_bean_id,
        grinder_id=fixture.grinder_id,
        as_of=AS_OF,
    )
    assert "DECAF MISMATCH" in context.render()["similar_sets"]
