"""Prompts: the seeding rules, rendering, validation, edit and reset."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaggiclanker.db.repos.llm import PromptsRepository
from gaggiclanker.llm.prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptError,
    PromptService,
    seed_prompts,
)

GREETING = """
name: greeting
description: a tiny prompt
variables:
  - name: who
    description: who to greet
system: |
  You are terse.
  {{> manners}}
user: |
  Say hello to {{who}} on behalf of {{app_version}}.
"""

MANNERS = """
name: fragments/manners
template: |
  Be polite about it.
"""


@pytest.fixture
def seeds(tmp_path: Path) -> Path:
    root = tmp_path / "prompts"
    (root / "fragments").mkdir(parents=True)
    (root / "greeting.yaml").write_text(GREETING)
    (root / "fragments" / "manners.yaml").write_text(MANNERS)
    return root


@pytest.fixture
def prompts(prompts_repo: PromptsRepository) -> PromptService:
    return PromptService(prompts_repo)


# -- seeding --------------------------------------------------------------


async def test_seeding_inserts_every_file_with_the_default_matching(
    prompts_repo: PromptsRepository, seeds: Path
) -> None:
    assert await seed_prompts(prompts_repo, seeds) == 2

    row = await prompts_repo.get("greeting")
    assert row is not None
    assert row.content == row.default_content
    assert row.edited is False
    # The path under the directory is the name, so fragments are namespaced.
    assert await prompts_repo.get("fragments/manners") is not None


async def test_a_steady_state_boot_changes_nothing(
    prompts_repo: PromptsRepository, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    assert await seed_prompts(prompts_repo, seeds) == 0


async def test_an_upgrade_reaches_an_untouched_prompt(
    prompts_repo: PromptsRepository, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)
    (seeds / "greeting.yaml").write_text(GREETING.replace("You are terse.", "You are brief."))

    await seed_prompts(prompts_repo, seeds)

    row = await prompts_repo.get("greeting")
    assert row is not None
    assert "You are brief." in row.content
    assert row.edited is False


async def test_an_upgrade_leaves_an_edited_prompt_alone_but_moves_its_default(
    prompts_repo: PromptsRepository, seeds: Path
) -> None:
    """Reset then converges on the new wording, not the version it was forked from."""
    await seed_prompts(prompts_repo, seeds)
    await prompts_repo.set_content("greeting", GREETING.replace("terse", "chatty"))
    (seeds / "greeting.yaml").write_text(GREETING.replace("You are terse.", "You are brief."))

    await seed_prompts(prompts_repo, seeds)

    row = await prompts_repo.get("greeting")
    assert row is not None
    assert "chatty" in row.content
    assert "You are brief." in row.default_content
    assert row.edited is True


async def test_a_broken_seed_is_skipped_not_fatal(
    prompts_repo: PromptsRepository, seeds: Path
) -> None:
    (seeds / "broken.yaml").write_text("name: broken\nsystem: [unclosed")

    await seed_prompts(prompts_repo, seeds)

    assert await prompts_repo.get("broken") is None
    assert await prompts_repo.get("greeting") is not None


async def test_a_missing_directory_is_not_an_error(
    prompts_repo: PromptsRepository, tmp_path: Path
) -> None:
    assert await seed_prompts(prompts_repo, tmp_path / "nowhere") == 0


# -- rendering ------------------------------------------------------------


async def test_rendering_expands_fragments_then_variables(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    rendered = await prompts.load("greeting", {"who": "the barista"})

    assert "Be polite about it." in rendered.system
    assert "Say hello to the barista" in rendered.user
    assert "{{" not in rendered.user


async def test_app_version_is_available_without_being_passed(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    from gaggiclanker import __version__

    await seed_prompts(prompts_repo, seeds)

    rendered = await prompts.load("greeting", {"who": "you"})

    assert __version__ in rendered.user


async def test_an_undefined_variable_raises_naming_both_lists(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    """Rendering it empty costs money and answers confidently about nothing."""
    await seed_prompts(prompts_repo, seeds)

    with pytest.raises(PromptError) as caught:
        await prompts.load("greeting", {})

    message = str(caught.value)
    assert "missing variables: who" in message
    assert "Declared: who" in message
    assert "Provided: app_version" in message


async def test_an_empty_string_is_a_real_value(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    rendered = await prompts.load("greeting", {"who": ""})

    assert "Say hello to  on behalf of" in rendered.user


async def test_extra_variables_are_ignored(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    rendered = await prompts.load("greeting", {"who": "you", "unused": "whatever"})

    assert "whatever" not in rendered.user


async def test_messages_drop_an_empty_system_prompt(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    (seeds / "bare.yaml").write_text("name: bare\nuser: just this\n")
    await seed_prompts(prompts_repo, seeds)

    rendered = await prompts.load("bare", {})

    assert [m.role for m in rendered.messages()] == ["user"]
    assert rendered.as_dicts() == [{"role": "user", "content": "just this"}]


async def test_a_nested_fragment_is_refused(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    (seeds / "fragments" / "outer.yaml").write_text(
        "name: fragments/outer\ntemplate: |\n  {{> manners}}\n"
    )
    (seeds / "nested.yaml").write_text("name: nested\nuser: |\n  {{> outer}}\n")
    await seed_prompts(prompts_repo, seeds)

    with pytest.raises(PromptError, match="one level deep"):
        await prompts.load("nested", {})


# -- editing --------------------------------------------------------------


async def test_an_edit_changes_the_next_render_with_no_restart(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    """The acceptance criterion, on the service rather than over HTTP."""
    await seed_prompts(prompts_repo, seeds)
    before = await prompts.load("greeting", {"who": "you"})

    await prompts.save("greeting", GREETING.replace("Say hello", "Say good morning"))
    after = await prompts.load("greeting", {"who": "you"})

    assert "Say hello" in before.user
    assert "Say good morning" in after.user


async def test_saving_invalid_yaml_is_refused(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    with pytest.raises(PromptError, match="not valid YAML"):
        await prompts.save("greeting", "name: greeting\nsystem: [unclosed")


async def test_an_unknown_key_is_a_typo_not_an_extension(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    with pytest.raises(PromptError, match="prompt schema"):
        await prompts.save("greeting", "name: greeting\nsytsem: oops\n")


async def test_referencing_a_fragment_that_does_not_exist_is_refused(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)

    with pytest.raises(PromptError, match="does not exist"):
        await prompts.save("greeting", "name: greeting\nuser: |\n  {{> nope}}\n")


async def test_reset_restores_the_shipped_text(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    await seed_prompts(prompts_repo, seeds)
    await prompts.save("greeting", GREETING.replace("terse", "chatty"))

    restored = await prompts.reset("greeting")

    assert "terse" in restored["content"]
    assert restored["edited"] is False


async def test_listing_survives_a_prompt_someone_broke(
    prompts_repo: PromptsRepository, prompts: PromptService, seeds: Path
) -> None:
    """The list is where you would go to fix it, so it must not be the casualty."""
    await seed_prompts(prompts_repo, seeds)
    await prompts_repo.set_content("greeting", "name: greeting\nsystem: [unclosed")

    listing = await prompts.list_prompts()

    entry = next(item for item in listing if item["name"] == "greeting")
    assert entry["valid"] is False
    assert len(listing) == 2


# -- the shipped prompts --------------------------------------------------


async def test_the_shipped_prompts_parse_and_render(
    prompts_repo: PromptsRepository, prompts: PromptService
) -> None:
    """ping.yaml is what the Validate button sends, so it has to work."""
    # Counted from the directory rather than written down, so shipping a new
    # prompt is one file and not two edits.
    shipped = len(list(DEFAULT_PROMPTS_DIR.rglob("*.yaml")))
    assert await seed_prompts(prompts_repo, DEFAULT_PROMPTS_DIR) == shipped

    rendered = await prompts.load("ping", {"topic": "puck preparation"})

    assert "puck preparation" in rendered.user
    assert "careful barista" in rendered.system
