"""The knowledge base: what gaggiclanker knows about espresso, as data.

Tier 1 only in the prototype — structured rules (`rules.py`, seeded from
`seed/rules.yaml`). Tiers 2 and 3 (prose chunks with FTS5, and learned insights)
are not built yet.
"""

from __future__ import annotations

from gaggiclanker.knowledge.rules import (
    DEFAULT_RULES_FILE,
    RuleSelection,
    seed_rules,
    select_rules,
)

__all__ = ["DEFAULT_RULES_FILE", "RuleSelection", "seed_rules", "select_rules"]
