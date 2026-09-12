"""The knowledge base: what gaggiclanker knows about espresso, as data.

Three tiers, and they are three because
they answer three different questions:

1. **rules** (`rules.py`, seeded from `seed/rules.yaml`) — small machine-readable
   facts with the conditions they apply under, selected deterministically by the
   Set's attributes and the shot's signals. Authoritative, and the model is
   asked to cite them by key;
2. **chunks** (`chunker.py` + `service.py`, seeded from `seed/docs/`) — thirty
   thousand words of prose split at its headings, indexed with FTS5 and
   retrieved a handful at a time under a token budget. Supporting context, cited
   by `heading_path`;
3. **insights** (`db/repos/knowledge_insights.py`) — what this archive has
   learned about this kitchen, scoped by Set attributes, proposed by the
   analyzer and confirmed by the user. Nothing unconfirmed reaches a prompt.

:class:`~gaggiclanker.knowledge.service.KnowledgeService` is the one object the
analyzer and the chat both go through for tiers 2 and 3.
"""

from __future__ import annotations

from gaggiclanker.knowledge.chunker import ParsedChunk, chunk_markdown
from gaggiclanker.knowledge.rules import (
    DEFAULT_RULES_FILE,
    RuleSelection,
    seed_rules,
    select_rules,
)
from gaggiclanker.knowledge.service import (
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_DOCS_DIR,
    Excerpt,
    KnowledgeService,
    RetrievalContext,
    render_excerpts,
    render_insights,
)

__all__ = [
    "DEFAULT_CHUNK_TOKEN_BUDGET",
    "DEFAULT_DOCS_DIR",
    "DEFAULT_RULES_FILE",
    "Excerpt",
    "KnowledgeService",
    "ParsedChunk",
    "RetrievalContext",
    "RuleSelection",
    "chunk_markdown",
    "render_excerpts",
    "render_insights",
    "seed_rules",
    "select_rules",
]
