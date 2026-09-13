"""What this kitchen did last time with a bag like this one, as one SQL statement.

This is the evidence half of the starting-point wizard. A starting point built only from the
rule tier is a textbook answer: correct, general, and silent about the fact
that *this* grinder runs two numbers finer than the chart says. A starting
point anchored on a Set that was actually pulled on this hardware, with a bean
of the same roast and process, carries that calibration for free.

crema does the same thing and does it in Python with a string-similarity hack
over bean names (crema's review prompt: token overlap of the
name, two points a token). That is thrown away here. Name overlap rewards
buying twice from the same roaster, which says nothing about extraction; what
predicts a dial-in is the roast level, the process and the kit. So the score is
four terms, all of them stated facts:

    same roast level        +3      adjacent roast level    +1
    same process            +2
    same origin             +1
    decaf mismatch          -2
    the version's outcome   +0..4

The attribute half therefore reaches 6 and the outcome half 4, and the balance
between those two ceilings *is* the design: a perfect outcome on an unrelated
bean must not outrank an exact match with a mediocre one.

**Decaf is a penalty rather than a filter.** Decaf is a different coffee
hydraulically — it wants a few degrees less and usually grinds differently — so
a decaf Set is weak evidence for a caffeinated bag and vice versa. But it is not
*no* evidence the way a different grinder is, and excluding it outright would
leave a kitchen that drinks decaf in the evening with nothing to anchor on. So
it costs two points, which is enough that an otherwise identical caffeinated Set
always wins.

**The outcome term is the half nobody else has.** A Set version is not just a
recipe, it is a recipe with shots attached and verdicts on them, so a version
somebody rated 4.5 out of 5 is better evidence than an identically-specified
one they abandoned at 2. It is scaled by how many shots back it up — one lucky
shot is not a dial-in — and a version with no shots at all is excluded outright
rather than scored at zero: it records an intention, not a result, and the
whole point of this query is to find results.

**Deterministic, and the tie-breaks say so.** The ordering is (score, shots,
version id) all descending, with the score rounded to three decimals before it
is compared. Two versions that genuinely tie on everything come back in a
stable order rather than whatever the query planner felt like, because the
wizard shows these to a person and a list that reshuffles between two presses
of the same button is a list nobody trusts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from gaggiclanker.db.connection import Database
from gaggiclanker.domain.vocab import ROAST_LEVELS

__all__ = [
    "DEFAULT_LIMIT",
    "OUTCOME_CONFIDENCE_SHOTS",
    "SimilarOutcome",
    "SimilarSet",
    "similar_sets",
]

#: How many similar Sets the wizard is given. Three, which is crema's number
#: and about the point where a fourth card stops being read.
DEFAULT_LIMIT = 3

#: Shots at which the outcome term counts in full. Below it the term is scaled
#: down pro rata: a version with one 5-star shot behind it has *some* evidence
#: and should outrank one with none, but not as much as a version with five.
OUTCOME_CONFIDENCE_SHOTS = 5


class SimilarOutcome(BaseModel):
    """How a candidate version actually turned out, over its own shots.

    Every field is nullable because every field can be genuinely unknown: a Set
    pulled before anybody started judging shots has no rating, a shot with no
    dose typed against it has no ratio. ``None`` is the honest answer and the
    prompt renders it as "not recorded" — a zero here would read as "rated it
    nothing", which is a different and much worse claim.
    """

    model_config = ConfigDict(extra="forbid")

    shots: int = 0
    mean_rating: float | None = None
    mean_execution_score: float | None = None
    mean_ratio: float | None = None
    mean_duration_s: float | None = None


class SimilarSet(BaseModel):
    """One past Set version offered as an anchor, with why it was offered."""

    model_config = ConfigDict(extra="forbid")

    set_id: int
    set_name: str
    set_version_id: int
    version_no: int
    created_at: str
    #: What made it similar, so the card can say "same roast, same process"
    #: rather than "score 5.3".
    score: float
    attribute_score: float
    outcome_score: float
    roast_match: str = "none"
    process_match: bool = False
    origin_match: bool = False
    #: Whether this Set is on the same side of the decaf line. False costs two
    #: points; the card shows it because "same roast, but decaf" is a sentence a
    #: reader needs before they copy the grind.
    decaf_match: bool = True

    bean_id: int | None = None
    bean_name: str = ""
    roast_level: str | None = None
    process: str | None = None
    origin: str | None = None
    decaf: bool = False

    grinder_id: int | None = None
    grinder_name: str = ""

    grind_setting: str | None = None
    grind_value: float | None = None
    dose_g: float | None = None
    target_yield_g: float | None = None
    target_temperature_c: float | None = None
    ratio: float | None = None
    profile_version_id: int | None = None
    profile_label: str | None = None

    outcome: SimilarOutcome = SimilarOutcome()


def _neighbours(roast_level: str | None) -> tuple[str | None, str | None]:
    """The roast levels either side of this one on the light → dark scale.

    ``ROAST_LEVELS`` is ordered and says so (`domain/vocab.py`), so "adjacent"
    is an index step rather than a table of pairs that could disagree with it.
    An unstated or unrecognised roast level has no neighbours, which is what
    makes the whole roast term score zero for a bag that does not say.
    """
    if roast_level is None or roast_level not in ROAST_LEVELS:
        return None, None
    index = ROAST_LEVELS.index(roast_level)
    before = ROAST_LEVELS[index - 1] if index > 0 else None
    after = ROAST_LEVELS[index + 1] if index + 1 < len(ROAST_LEVELS) else None
    return before, after


#: The whole query. One statement rather than a fetch-then-score in Python for
#: the reason the trends query is one statement: the archive is the thing that
#: knows which shots belong to which version, and pulling every version into
#: the process to average five columns would be a page of code to do what
#: `GROUP BY` does.
#:
#: The outcome CTE aggregates over *shots*, so a version with none simply has
#: no row in it and the inner join drops it — the exclusion is structural
#: rather than a `HAVING` somebody could delete.
_SQL = """
WITH outcomes AS (
    SELECT sh.set_version_id                       AS version_id,
           COUNT(*)                                AS shots,
           AVG(j.rating)                           AS mean_rating,
           AVG(sh.execution_score)                 AS mean_execution_score,
           AVG(CASE WHEN j.dose_in_g > 0
                    THEN COALESCE(j.dose_out_g, sh.final_weight_g, sh.index_volume_g)
                         / j.dose_in_g END)        AS mean_ratio,
           AVG(sh.duration_ms / 1000.0)            AS mean_duration_s
      FROM shots sh
      LEFT JOIN shot_judgements j ON j.shot_id = sh.id
     WHERE sh.set_version_id IS NOT NULL
       -- A quarantined shot never parsed, so it has no duration, no score and
       -- nothing to say about whether the recipe worked.
       AND sh.quarantined = 0
     GROUP BY sh.set_version_id
)
SELECT v.id                                         AS set_version_id,
       v.set_id,
       v.version_no,
       v.created_at,
       v.grind_setting,
       v.grind_value,
       v.dose_g,
       v.target_yield_g,
       v.target_temperature_c,
       v.profile_version_id,
       pv.label                                     AS profile_label,
       s.name                                       AS set_name,
       s.grinder_id,
       g.name                                       AS grinder_name,
       b.id                                         AS bean_id,
       b.name                                       AS bean_name,
       b.roast_level,
       b.process,
       b.origin,
       b.decaf,
       o.shots,
       o.mean_rating,
       o.mean_execution_score,
       o.mean_ratio,
       o.mean_duration_s,
       CASE WHEN b.roast_level = :roast_level THEN 'same'
            WHEN b.roast_level IN (:roast_before, :roast_after) THEN 'adjacent'
            ELSE 'none' END                         AS roast_match,
       CASE WHEN :process IS NOT NULL AND b.process = :process THEN 1
            ELSE 0 END                              AS process_match,
       CASE WHEN :origin IS NOT NULL AND b.origin = :origin THEN 1
            ELSE 0 END                              AS origin_match,
       CASE WHEN b.decaf = :decaf THEN 1 ELSE 0 END AS decaf_match,
       ROUND(
           (CASE WHEN b.roast_level = :roast_level THEN 3.0
                 WHEN b.roast_level IN (:roast_before, :roast_after) THEN 1.0
                 ELSE 0.0 END)
         + (CASE WHEN :process IS NOT NULL AND b.process = :process THEN 2.0 ELSE 0.0 END)
         + (CASE WHEN :origin IS NOT NULL AND b.origin = :origin THEN 1.0 ELSE 0.0 END)
         -- Not a filter: see the module docstring. Two points, which is enough
         -- that an otherwise identical caffeinated Set always wins.
         + (CASE WHEN b.decaf = :decaf THEN 0.0 ELSE -2.0 END),
       3)                                           AS attribute_score,
       ROUND(
           (MIN(o.shots, :confidence_shots) * 1.0 / :confidence_shots)
         * (0.4 * COALESCE(o.mean_rating, 0.0) + 0.2 * COALESCE(o.mean_execution_score, 0.0)),
       3)                                           AS outcome_score
  FROM set_versions v
  JOIN outcomes o ON o.version_id = v.id
  JOIN sets s     ON s.id = v.set_id
  JOIN beans b    ON b.id = s.bean_id
  LEFT JOIN grinders g          ON g.id = s.grinder_id
  LEFT JOIN profile_versions pv ON pv.id = v.profile_version_id
 WHERE (:grinder_id IS NULL OR s.grinder_id = :grinder_id)
   AND (:exclude_set_id IS NULL OR s.id <> :exclude_set_id)
 ORDER BY (attribute_score + outcome_score) DESC, o.shots DESC, v.id DESC
 LIMIT :limit
"""


async def similar_sets(
    db: Database,
    *,
    roast_level: str | None,
    process: str | None,
    origin: str | None,
    grinder_id: int | None,
    decaf: bool = False,
    exclude_set_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SimilarSet]:
    """The best few past Set versions to anchor a new bag on.

    ``grinder_id`` is a filter rather than a score term, and deliberately: a
    grind number from a different grinder is not a weaker signal, it is a
    meaningless one, and putting it on a card next to "22" would invite
    somebody to dial 22 on the wrong grinder. ``None`` lifts the filter, which
    is what a Set with no recorded grinder needs — the recipe is still worth
    seeing, and the caller renders it without a grind number.

    There is no machine term: the archive holds one, so every past Set was
    brewed on the same boiler at the same offset and a filter on it would
    always match.

    ``exclude_set_id`` keeps a Set out of its own suggestions, for the "suggest
    a new version of this Set" caller. The wizard leaves it ``None``: a
    previous bag of the same coffee is the *best* anchor there is.
    """
    before, after = _neighbours(roast_level)
    rows = await db.fetch_all(
        _SQL,
        {
            "roast_level": roast_level,
            "roast_before": before,
            "roast_after": after,
            "process": process,
            "origin": origin,
            "decaf": int(decaf),
            "grinder_id": grinder_id,
            "exclude_set_id": exclude_set_id,
            "confidence_shots": OUTCOME_CONFIDENCE_SHOTS,
            "limit": max(1, limit),
        },
    )
    return [_to_model(dict(row)) for row in rows]


def _to_model(row: dict[str, Any]) -> SimilarSet:
    attribute = float(row["attribute_score"])
    outcome = float(row["outcome_score"])
    dose = row["dose_g"]
    yield_g = row["target_yield_g"]
    return SimilarSet(
        set_id=int(row["set_id"]),
        set_name=str(row["set_name"]),
        set_version_id=int(row["set_version_id"]),
        version_no=int(row["version_no"]),
        created_at=str(row["created_at"]),
        score=round(attribute + outcome, 3),
        attribute_score=attribute,
        outcome_score=outcome,
        roast_match=str(row["roast_match"]),
        process_match=bool(row["process_match"]),
        origin_match=bool(row["origin_match"]),
        decaf_match=bool(row["decaf_match"]),
        bean_id=row["bean_id"],
        bean_name=str(row["bean_name"] or ""),
        roast_level=row["roast_level"],
        process=row["process"],
        origin=row["origin"],
        decaf=bool(row["decaf"]),
        grinder_id=row["grinder_id"],
        grinder_name=str(row["grinder_name"] or ""),
        grind_setting=row["grind_setting"],
        grind_value=row["grind_value"],
        dose_g=dose,
        target_yield_g=yield_g,
        target_temperature_c=row["target_temperature_c"],
        ratio=round(float(yield_g) / float(dose), 2) if dose and yield_g else None,
        profile_version_id=row["profile_version_id"],
        profile_label=row["profile_label"],
        outcome=SimilarOutcome(
            shots=int(row["shots"]),
            mean_rating=_round(row["mean_rating"], 2),
            mean_execution_score=_round(row["mean_execution_score"], 2),
            mean_ratio=_round(row["mean_ratio"], 2),
            mean_duration_s=_round(row["mean_duration_s"], 1),
        ),
    )


def _round(value: Any, digits: int) -> float | None:
    """``None`` stays ``None``. See :class:`SimilarOutcome` for why that matters."""
    return None if value is None else round(float(value), digits)
