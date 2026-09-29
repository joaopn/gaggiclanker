#!/usr/bin/env python
"""Reproduce: a shot review's excerpt retrieval chases healthy readings.

    uv run python scripts/repro_review_excerpts_unremarkable_bands.py

Exits non-zero while the bug exists, zero when it is fixed.

The bug
-------

`KnowledgeService.queries_for` turns every uppercase signal token into a
retrieval query unless its *label* is in a label-global list of "fine" words.
Since a real shot's review tokens are section-qualified, healthy readings such
as `channeling_risk:LOW`, `temperature_overshoot:MINIMAL` and
`resistance_stability:VERY_STABLE` became queries, and the first of them takes
the diagnostics reference's one excerpt slot (one chunk per document),
displacing whatever a band that does stand out would have fetched. The label
alone cannot say what is healthy: `LOW` is the healthy value of
`channeling_risk` and the notable one of `resistance_level`.

The check derives each real fixture shot the way ingest does, builds the
review's input, and requires that no query is built from a band that is healthy
for its own metric (the list below is written out here on purpose, apart from
the code under test), and that no excerpt was found by one.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.knowledge import RulesRepository
from gaggiclanker.db.repos.shots import ShotsRepository
from gaggiclanker.domain.slog import parse_slog
from gaggiclanker.knowledge.rules import seed_rules
from gaggiclanker.knowledge.service import KnowledgeService, RetrievalContext
from gaggiclanker.review.context import build_review_input
from gaggiclanker.sync.derive import derive_shot

ROOT = Path(__file__).resolve().parents[1]

#: Readings that say nothing is wrong, per metric.
HEALTHY = {
    "channeling_risk": {"LOW"},
    "temperature_overshoot": {"MINIMAL"},
    "temperature_undershoot": {"MINIMAL"},
    "temperature_stability": {"VERY_STABLE", "STABLE"},
    "resistance_stability": {"VERY_STABLE", "STABLE"},
    "resistance_saturation": {"GOOD_TIMING"},
    "flow_trend": {"STABLE"},
}


async def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "repro.db")
        await db.connect()
        await run_migrations(db)
        await seed_rules(RulesRepository(db))
        service = KnowledgeService(db)
        await service.seed_docs()
        try:
            for path in sorted((ROOT / "tests" / "fixtures" / "slog").glob("*.slog")):
                raw = path.read_bytes()
                derived = derive_shot(parse_slog(raw), raw, device_id=path.stem)
                shot_id = await ShotsRepository(db).insert(derived.shot, derived.samples)
                review = await build_review_input(db, shot_id)
                context = RetrievalContext(style=review.style, signals=tuple(review.signals))
                queries = service.queries_for(context)
                print(f"{path.name}: {len(queries)} queries")
                healthy = [
                    f"{metric}:{label}"
                    for token in review.signals
                    for metric, _, label in [token.partition(":")]
                    if label in HEALTHY.get(metric, ())
                    and f"{metric} {label}".replace("_", " ") in queries
                ]
                if healthy:
                    failed = True
                    print(f"  QUERIES built from healthy bands: {healthy}")
                healthy_queries = {t.replace("_", " ").replace(":", " ") for t in healthy}
                for e in review.excerpts:
                    print(f"  excerpt: {e['heading_path']}   (found by {e['query']!r})")
                    if e["query"] in healthy_queries:
                        failed = True
                        print("    ^ FOUND BY A HEALTHY BAND: it holds the document's one slot")
        finally:
            await db.close()
    print("FAIL" if failed else "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
