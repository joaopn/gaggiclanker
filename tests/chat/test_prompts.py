"""The two chat prompts, as they render.

One prompt per kind of conversation, and the goldens are what stop a wording
change from being invisible: these are the instructions a model follows, so a
diff here is a behaviour change however small the edit looked.

The Set prompt's `{{scope}}` is rendered with a marker rather than with a real
opening context. The context has a golden of its own
(`golden/set-chat-context.txt`) and pinning it twice would mean every change to
the ledger's wording failing two files; what this golden is for is the
*instructions* around it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gaggiclanker.chat.runner import (
    DESIGN_CHAT_PROMPT,
    GENERAL_CHAT_PROMPT,
    SET_CHAT_PROMPT,
    prompt_for,
)
from gaggiclanker.db.connection import Database
from gaggiclanker.db.migrations import run_migrations
from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.prompts import DEFAULT_PROMPTS_DIR, PromptService, seed_prompts
from gaggiclanker.tools.registry import registry
from gaggiclanker.tools.scope import ToolScope

GOLDEN = Path(__file__).resolve().parent / "golden"

#: What `{{scope}}` is rendered with here. Not a context: the context is pinned
#: by its own golden, and a prompt golden carrying a copy would fail twice for
#: one change.
SCOPE_MARKER = "<<the opening context goes here>>"


@pytest.fixture
async def prompts(tmp_path: Path) -> AsyncIterator[PromptService]:
    """The shipped prompt files, seeded into a real table and rendered from it."""
    db = Database(tmp_path / "prompts.db")
    await db.connect()
    await run_migrations(db)
    repo = PromptsRepository(db)
    await seed_prompts(repo, DEFAULT_PROMPTS_DIR)
    try:
        yield PromptService(repo)
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("name", "variables", "golden"),
    [
        (SET_CHAT_PROMPT, {"scope": SCOPE_MARKER}, "chat-set-prompt.txt"),
        (DESIGN_CHAT_PROMPT, {"scope": SCOPE_MARKER}, "chat-design-prompt.txt"),
        (GENERAL_CHAT_PROMPT, {}, "chat-general-prompt.txt"),
    ],
)
async def test_the_rendered_prompt_matches_the_golden_file(
    prompts: PromptService,
    update_golden: bool,
    name: str,
    variables: dict[str, str],
    golden: str,
) -> None:
    rendered = (await prompts.load(name, variables)).system

    path = GOLDEN / golden
    if update_golden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        pytest.skip("golden file rewritten")
    assert path.exists(), "run with --update-golden to create it"
    assert rendered == path.read_text(encoding="utf-8")


async def test_every_variable_the_set_prompt_uses_is_declared_and_provided(
    prompts: PromptService,
) -> None:
    """An undefined variable raises, so this is the only way to find a typo."""
    rendered = await prompts.load(SET_CHAT_PROMPT, {"scope": SCOPE_MARKER})

    assert rendered.declared == ["scope"]
    assert SCOPE_MARKER in rendered.system


async def test_the_general_prompt_takes_no_variables(prompts: PromptService) -> None:
    rendered = await prompts.load(GENERAL_CHAT_PROMPT, {})

    assert rendered.declared == []
    assert rendered.system.strip()


def test_the_prompt_follows_the_conversation_s_kind() -> None:
    assert prompt_for(ToolScope.for_thread(3, 9)) == SET_CHAT_PROMPT
    assert prompt_for(ToolScope.for_thread(3, 9, designing=True)) == DESIGN_CHAT_PROMPT
    assert prompt_for(ToolScope()) == GENERAL_CHAT_PROMPT


async def test_the_design_prompt_takes_the_design_context(prompts: PromptService) -> None:
    rendered = await prompts.load(DESIGN_CHAT_PROMPT, {"scope": SCOPE_MARKER})

    assert rendered.declared == ["scope"]
    assert SCOPE_MARKER in rendered.system


async def test_the_design_prompt_says_what_designing_is_and_is_not(
    prompts: PromptService,
) -> None:
    # Read as prose: where the lines wrap is not what is being tested.
    system = " ".join((await prompts.load(DESIGN_CHAT_PROMPT, {"scope": ""})).system.split())

    # Nothing exists until the person accepts, and one card replaces the last.
    assert "nothing exists yet" in system
    assert "a newer card replaces the one waiting" in system
    # Ask first, but briefly.
    assert "Never a questionnaire" in system
    # The Sets are evidence and outrank the rules; the profile is new.
    assert "THEY OUTRANK THE RULES" in system
    assert "THE PROFILE IS NEW, SO NAME IT" in system
    # And it is the design and nothing else: version 1's shots are analysed in
    # a new conversation, and the person is told so before they accept.
    assert "becomes the Set's ordinary one" not in system
    assert "one conversation is one version's work" in system
    assert "this design conversation is done" in system
    # A decline arrives as a turn of its own, and is answered, not re-sent.
    assert '"Declined:"' in system
    assert "never the same card" in system
    # None of the experiment's rules, which do not apply to a baseline.
    assert "GRADE FIRST" not in system


async def test_the_grind_number_rule_is_one_text_in_both_first_recipe_prompts(
    prompts: PromptService,
) -> None:
    """The starting point and the design conversation state it identically."""
    rule = await prompts.render_fragment("fragments/grind-number")
    design = (await prompts.load(DESIGN_CHAT_PROMPT, {"scope": ""})).system
    starting = (await prompts.load("starting_point", {})).system

    assert "NEVER INVENT A GRIND NUMBER" in rule
    assert rule.strip() in design
    assert rule.strip() in starting


async def test_the_set_prompt_says_what_a_grade_and_a_proposal_have_to_be(
    prompts: PromptService,
) -> None:
    """The behaviour the whole piece exists for, in the words it is given in."""
    system = (await prompts.load(SET_CHAT_PROMPT, {"scope": ""})).system

    assert "GRADE FIRST" in system
    assert "inconclusive" in system
    assert "new hypothesis" in system
    assert "One change at a time" in system
    assert "Keep shots are the target" in system
    # It never decides the experiment is over.
    assert "never tell them to stop or to carry on" in system
    # And it knows it can see one Set.
    assert "this Set and nothing else" in system


async def test_the_set_prompt_sends_an_accepted_version_to_a_new_conversation(
    prompts: PromptService,
) -> None:
    """One conversation is one version, and the person is told so when they accept.

    The Accept button on a card in the chat sends a message that starts with
    "Accepted:" (the web's `acceptedMessage`); this is what the agent is told to
    do with it, for a change and for a first recipe alike, since the turn after
    a design is accepted is answered by this prompt.
    """
    system = " ".join((await prompts.load(SET_CHAT_PROMPT, {"scope": ""})).system.split())

    assert "WHEN THEY ACCEPT, THIS CONVERSATION IS DONE" in system
    assert '"Accepted:"' in system
    assert "they must start a new conversation" in system
    assert "Discuss in chat on the Set page" in system
    assert "first recipe was designed" in system


async def test_the_set_prompt_answers_a_declined_card_in_the_same_conversation(
    prompts: PromptService,
) -> None:
    """The Decline button's turn starts with "Declined:"; nothing changed, so it stays here."""
    system = " ".join((await prompts.load(SET_CHAT_PROMPT, {"scope": ""})).system.split())

    assert '"Declined:"' in system
    assert "this conversation carries on about the same version" in system
    assert "never the same change again" in system


async def test_the_general_prompt_sends_a_set_change_to_that_set_s_folder(
    prompts: PromptService,
) -> None:
    system = (await prompts.load(GENERAL_CHAT_PROMPT, {})).system

    assert "YOU CANNOT CHANGE A SET HERE" in system
    assert "propose_set_version" not in system
    assert "query_shots" in system


@pytest.mark.parametrize(
    ("name", "variables", "scope"),
    [
        (SET_CHAT_PROMPT, {"scope": SCOPE_MARKER}, ToolScope.for_thread(3, 9)),
        (
            DESIGN_CHAT_PROMPT,
            {"scope": SCOPE_MARKER},
            ToolScope.for_thread(3, 9, designing=True),
        ),
        (GENERAL_CHAT_PROMPT, {}, ToolScope()),
    ],
)
async def test_a_prompt_names_no_tool_this_conversation_does_not_have(
    prompts: PromptService, name: str, variables: dict[str, str], scope: ToolScope
) -> None:
    """Prose drifts from a list that moved; this is what notices.

    Every backticked token that happens to be a registered tool is checked —
    the prompts also backtick words like `inconclusive`, which are not tools and
    are none of this test's business.
    """
    system = (await prompts.load(name, variables)).system
    registered = set(registry.names())

    named = {token for token in re.findall(r"`([a-z_]+)`", system) if token in registered}

    assert named, "a prompt that names no tool at all is probably a rendering bug"
    assert named <= scope.tools, sorted(named - scope.tools)


async def test_both_prompts_carry_the_shared_rules(prompts: PromptService) -> None:
    """The fragment is expanded, not merely referenced."""
    for name, variables in ((SET_CHAT_PROMPT, {"scope": ""}), (GENERAL_CHAT_PROMPT, {})):
        system = (await prompts.load(name, variables)).system

        assert "NEVER INVENT DATA" in system, name
        assert "PROPOSE, NEVER ACT" in system, name
        assert "{{>" not in system, name
