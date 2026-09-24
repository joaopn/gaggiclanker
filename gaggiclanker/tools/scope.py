"""Which tools exist in a conversation, and what each one may touch.

A conversation is one of two kinds and the kind is a **limit**, not a hint.

A **Set** conversation is about one version of one Set, and every tool it has
is bounded by that Set: it reads that Set's versions, shots, predictions and
outcomes, the knowledge tiers, and nothing else. There is no archive-wide SQL
in it, no list of other Sets, and no way to ask about a shot filed somewhere
else — not because a prompt asks nicely, but because the tool that would answer
is not offered and would be refused if it were called anyway.

A **General** conversation is the other half: the whole archive, read-only. It
answers "which beans did I like most" and "what does pre-infusion do", and it
cannot change a Set, because changing a Set is an argument that belongs in that
Set's own room where the prediction ledger is in front of the model.

**This module is the single source.** Three consumers ask it the same question:
the function-calling schemas the runner sends a provider, the dispatcher that
decides whether a call runs, and the stdio MCP server the ``claude_code``
provider spawns — which is handed the scope in its environment because its tool
loop runs inside the CLI, out of the dispatcher's reach. A second list anywhere
(a switch in the runner, a filter in the web, an "allowed tools" constant in the
MCP entry point) is how one of the three comes to offer something the other two
refuse, and the refusal then reads as a bug in the model rather than as the rule
it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "DESIGN_RULE",
    "GENERAL_TOOLS",
    "SET_TOOLS",
    "ChatKind",
    "ToolScope",
]

#: What a conversation is about. Exactly two, because there are exactly two
#: things an agent can be pointed at here: one experiment, or the archive.
type ChatKind = Literal["general", "set"]

#: The tools a Set conversation has. Everything here is bounded by the Set:
#: ``get_set`` and ``propose_set_version`` refuse another Set's id, ``get_shot``
#: and ``compare_shots`` refuse a shot filed elsewhere, and ``list_set_shots``
#: takes no Set at all — it reads the one this conversation is about.
#:
#: What is deliberately absent is as much of the design as what is here.
#: ``query_shots`` and ``describe_schema`` would be a way around the scope in
#: one SELECT; ``list_sets``, ``list_beans`` and ``list_grinders`` would name
#: other coffees; ``starting_point`` is a question about a bag with no Set yet,
#: which is a General question; ``run_analysis`` is a second adviser that owes
#: no prediction.
SET_TOOLS: frozenset[str] = frozenset(
    {
        "compare_shots",
        "draft_profile",
        "get_insights",
        "get_knowledge_chunk",
        "get_rules",
        "get_set",
        "get_shot",
        "list_profiles",
        "list_set_shots",
        "propose_set_version",
        "record_insight",
        "search_knowledge",
    }
)

#: The tools a General conversation has: every read of the archive, plus the two
#: proposals that belong to no Set — a starting point for a bag nobody has
#: brewed yet, and a profile draft, which waits on the Profiles page for a
#: person either way.
#:
#: Absent: ``propose_set_version`` and ``record_insight``, because a Set is
#: changed and learned about in its own folder, where the ledger and the
#: evidence are in front of the model; ``run_analysis``, which is the per-shot
#: adviser; and ``list_set_shots``, which has no Set to list.
GENERAL_TOOLS: frozenset[str] = frozenset(
    {
        "compare_shots",
        "describe_schema",
        "draft_profile",
        "get_insights",
        "get_knowledge_chunk",
        "get_rules",
        "get_set",
        "get_shot",
        "get_starting_point",
        "list_beans",
        "list_grinders",
        "list_profiles",
        "list_sets",
        "query_shots",
        "search_knowledge",
        "starting_point",
    }
)


#: What a conversation about a Set being designed is told when it reaches for a
#: tool that changes or reads a recipe. One sentence for every such refusal —
#: the scope's, `propose_set_version`'s and `draft_profile`'s — because they are
#: one rule, and a model that met it phrased three ways would read three.
DESIGN_RULE = (
    "This Set is being designed: its recipe does not exist yet, so there is nothing to "
    "change and there are no shots to read. propose_initial_recipe is how the recipe is "
    "proposed."
)

#: Why one particular tool is not here, where the kind's own rule would say
#: something untrue. Keyed by name because each of these is refused for the
#: same reason wherever it is called.
_REASONS: dict[str, str] = {
    "run_analysis": (
        "The per-shot analysis is not something a conversation queues: a Set's chat grades "
        "its own prediction, and a general one is not about a shot."
    ),
    "list_set_shots": (
        "It lists the shots of the one Set a conversation is about, and this one is about "
        "the archive. query_shots reads any Set's shots here."
    ),
}


@dataclass(frozen=True, slots=True)
class ToolScope:
    """One conversation's kind and, when it has one, the experiment it is about.

    Frozen: a scope is decided when a turn starts, from the thread, and nothing
    downstream may widen it. The default is General, which is the weaker of the
    two in the direction that matters — it can read everything and change no
    Set — so a caller that forgets to pass one cannot accidentally get a Set's
    proposal tools pointed at the wrong Set.
    """

    kind: ChatKind = "general"
    #: The Set a Set conversation is about, and ``None`` in a General one. Tools
    #: that take a ``set_id`` read it from here and refuse any other.
    set_id: int | None = None
    #: The version being argued. Not a limit — the whole Set's history is what a
    #: grade is made against — but it is what the opening context is built from.
    set_version_id: int | None = None

    @classmethod
    def for_thread(cls, set_id: int | None, set_version_id: int | None = None) -> ToolScope:
        """The scope of a conversation, from the two columns the thread stores."""
        if set_id is None:
            return cls()
        return cls(kind="set", set_id=set_id, set_version_id=set_version_id)

    @property
    def tools(self) -> frozenset[str]:
        """The names that exist here. The one mapping from a kind to a surface."""
        return SET_TOOLS if self.kind == "set" else GENERAL_TOOLS

    def allows(self, name: str) -> bool:
        return name in self.tools

    def refusal(self, name: str) -> str:
        """What the model is told when it calls something that is not here.

        Names the rule and then the way out, because a model that is told "not
        available" and nothing else asks for it again with different arguments.
        It says nothing about any other Set: which Sets exist is exactly what a
        Set conversation does not get to learn.

        Two tools get a reason of their own rather than the rule of the kind
        they were called in, because the rule would be the wrong sentence: the
        per-shot analysis belongs to no conversation at all, and a tool that
        lists one Set's shots is not missing from a general conversation
        because of anything to do with changing a Set.
        """
        offered = ", ".join(sorted(self.tools))
        reason = _REASONS.get(name)
        if reason is None:
            reason = (
                "This conversation can see this Set only."
                if self.kind == "set"
                else "Changing a Set happens in that Set's own conversation."
            )
        here = "a conversation about one Set" if self.kind == "set" else "a general conversation"
        return f"{name!r} is not available in {here}. {reason} Call one of: {offered}."
