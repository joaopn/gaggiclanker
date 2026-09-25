"""The tool loop: what it does, what it refuses to keep doing, and what it stores.

Every test here scripts the provider and asserts on rows and events rather than
on timing. The properties being pinned are the ones a person notices when they
break: the answer arrives, the trace shows what was called, the run stops when
the bounds say so, cancel means cancel, and a reconnect does not lose the
middle of an answer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gaggiclanker.chat.runner import ChatRunner, _to_chat_message, run_task_name
from gaggiclanker.db.repos.chat import ChatEventsRepository, ChatRepository
from gaggiclanker.db.repos.sets import DesignBrief, SetsRepository, SetWrite
from gaggiclanker.infra.sse import EventBus, SseEvent
from gaggiclanker.infra.tasks import TaskRegistry
from gaggiclanker.llm.chat_types import ChatToolCall, ChatTurn
from gaggiclanker.llm.errors import LlmApiError
from gaggiclanker.llm.types import Usage
from gaggiclanker.tools.scope import DESIGN_TOOLS, GENERAL_TOOLS, SET_TOOLS
from tests.analyzer.conftest import Fixture
from tests.llm.conftest import FakeProvider


async def finish(tasks: TaskRegistry, run_id: int) -> None:
    """Wait for one run's background task, whatever it did."""
    task = tasks.get(run_task_name(run_id))
    if task is not None:
        await asyncio.wait([task], timeout=10)


async def send(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, message: str = "how is it going?"
) -> int:
    run, _ = await runner.send(thread, message, tasks=tasks)
    await finish(tasks, run.id)
    return run.id


def kinds(events: list[Any]) -> list[str]:
    return [event.kind for event in events]


# -- the happy path --------------------------------------------------------


async def test_a_plain_answer_is_stored_streamed_and_completed(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [
        ChatTurn(text="Pull two more and we will know.", usage=Usage(11, 7))
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    messages = await ChatRepository(runner.db).messages(thread)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "Pull two more and we will know."

    events = await ChatEventsRepository(runner.db).since(run_id)
    assert "delta" in kinds(events)
    assert kinds(events)[-1] == "completed"


async def test_the_thread_is_named_from_its_first_question(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    """Nobody titles a conversation; an untitled list of twenty is unusable."""
    await send(runner, tasks, thread, "Why is this shot sour? I tried two clicks finer.")

    row = await ChatRepository(runner.db).get_thread(thread)
    assert row is not None
    assert row.title.startswith("Why is this shot sour?")


async def test_a_tool_round_executes_dispatches_and_feeds_the_result_back(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [
        ChatTurn(
            text="",
            tool_calls=[
                ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": archive.shots[0]})
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Shot looks fine."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert (run.status, run.tool_rounds, run.tool_calls) == ("ok", 1, 1)

    roles = [message.role for message in await ChatRepository(runner.db).messages(thread)]
    assert roles == ["user", "assistant", "tool", "assistant"]

    events = kinds(await ChatEventsRepository(runner.db).since(run_id))
    assert "tool_call" in events and "tool_result" in events

    # The second turn was shown the result, which is the point of the loop.
    second = chat_provider.chat_calls[1]
    assert second.messages[-1].role == "tool"
    assert second.messages[-1].tool_results[0].name == "get_shot"


async def test_a_failing_tool_is_shown_to_the_model_rather_than_ending_the_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[ChatToolCall(id="c1", name="get_shot", arguments={"shot_id": 999_999})],
            stop_reason="tool_use",
        ),
        ChatTurn(text="There is no shot 999999 in the archive."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.status == "ok"
    result = chat_provider.chat_calls[1].messages[-1].tool_results[0]
    assert result.ok is False
    # A Set conversation refuses a shot that is not its own in the same words
    # whether or not it exists, which is what the model is shown here.
    assert "not a shot of this Set" in result.content


# -- the bounds ------------------------------------------------------------


async def test_the_round_budget_ends_the_loop_with_an_answer(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """A model that keeps calling tools still has to produce something to read."""
    await runner.llm.settings.store("chatMaxToolRounds", 2)
    looping = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    chat_provider.chat_script = [looping, looping, ChatTurn(text="I ran out of budget.")]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.tool_rounds == 2
    # The final turn is asked without tools, so the model cannot keep going.
    assert chat_provider.chat_calls[-1].tools == []
    assert (await ChatRepository(runner.db).messages(thread))[-1].content == "I ran out of budget."


async def test_the_call_budget_caps_the_tools_actually_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    await runner.llm.settings.store("chatMaxToolCalls", 2)
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(id=f"c{index}", name="list_sets", arguments={}) for index in range(5)
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Enough."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.tool_calls == 2


async def test_an_exhausted_call_budget_leaves_a_history_a_provider_will_accept(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """The rounds after the budget is spent must not be written down.

    They used to be: an assistant message with empty content and a `tool`
    message with no results. Both are 400s on the Anthropic provider on every
    *later* turn of the thread, so one exhausted budget poisoned the
    conversation for good. The check is the provider's own message builder,
    against the stored history.
    """
    from gaggiclanker.llm.providers.anthropic import _chat_messages

    await runner.llm.settings.store("chatMaxToolCalls", 2)
    await runner.llm.settings.store("chatMaxToolRounds", 5)
    asking = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    # Two rounds spend the budget; the three rounds left are the ones that used
    # to be written down empty.
    chat_provider.chat_script = [asking, asking, ChatTurn(text="Done.")]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.tool_calls == 2

    stored = await ChatRepository(runner.db).messages(thread)
    assert [message.role for message in stored] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    # No empty rounds: every assistant row either says something or asked for
    # something, and every tool row carries a result.
    for message in stored:
        if message.role == "assistant":
            assert message.content or message.tool_calls
        if message.role == "tool":
            assert message.tool_results

    rendered = _chat_messages([_to_chat_message(message) for message in stored])
    assert all(message["content"] for message in rendered)


async def test_the_budget_nudge_is_appended_once(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """Repeating it every round would crowd the question out of the history."""
    await runner.llm.settings.store("chatMaxToolCalls", 2)
    asking = ChatTurn(
        tool_calls=[ChatToolCall(id="c", name="list_sets", arguments={})], stop_reason="tool_use"
    )
    chat_provider.chat_script = [asking, asking, ChatTurn(text="Done.")]

    await send(runner, tasks, thread)

    sent = chat_provider.chat_calls[-1].messages
    nudges = [
        message for message in sent if message.role == "user" and "tool budget" in message.content
    ]
    assert len(nudges) == 1
    assert chat_provider.chat_calls[-1].tools == []


async def test_a_slow_tool_does_not_hang_the_run(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    """The per-tool timeout is inside the dispatcher; the run carries on."""
    spec = runner.tools.get("query_shots")
    assert spec is not None
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(
                    id="c1",
                    name="query_shots",
                    arguments={
                        "sql": (
                            "WITH RECURSIVE spin(n) AS (SELECT 1 UNION ALL "
                            "SELECT n + 1 FROM spin WHERE n < 100000000) SELECT COUNT(*) FROM spin"
                        )
                    },
                )
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="That query was too slow; here is a narrower one."),
    ]

    run_id = await send(runner, tasks, thread)

    run = await ChatRepository(runner.db).get_run(run_id)
    assert run is not None and run.status == "ok"
    assert chat_provider.chat_calls[1].messages[-1].tool_results[0].ok is False


# -- cancellation ----------------------------------------------------------


async def test_cancel_mid_run_stores_a_cancelled_row_and_a_cancelled_event(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_delay = 0.2
    chat_provider.chat_script = [ChatTurn(text="never finished")]

    run, _ = await runner.send(thread, "take your time", tasks=tasks)
    await asyncio.sleep(0.02)
    await runner.cancel(run.id)
    await finish(tasks, run.id)

    stored = await ChatRepository(runner.db).get_run(run.id)
    assert stored is not None
    assert stored.status == "cancelled"
    assert kinds(await ChatEventsRepository(runner.db).since(run.id))[-1] == "cancelled"


async def test_cancelling_a_finished_run_is_not_an_error(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    run_id = await send(runner, tasks, thread)

    row = await runner.cancel(run_id)

    assert row.status == "ok", "a cancel after the fact must not rewrite the outcome"


async def test_a_run_left_running_by_a_restart_is_reconciled(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    """A `running` row is only true while a process holds it."""
    run = await ChatRepository(runner.db).start_run(thread)

    assert await runner.reconcile() == 1

    stored = await ChatRepository(runner.db).get_run(run.id)
    assert stored is not None
    assert stored.status == "interrupted"
    assert stored.error


# -- failure ---------------------------------------------------------------


async def test_a_provider_failure_is_a_stored_row_and_an_error_event(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [LlmApiError("no credit left", status=429)]

    run_id = await send(runner, tasks, thread)

    stored = await ChatRepository(runner.db).get_run(run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error is not None and stored.error.startswith("rate_limited")
    events = await ChatEventsRepository(runner.db).since(run_id)
    assert events[-1].kind == "error"
    assert events[-1].data["code"] == "rate_limited"


# -- the stream ------------------------------------------------------------


async def test_a_reconnect_replays_everything_after_the_last_sequence(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [ChatTurn(text="one two three")]

    run_id = await send(runner, tasks, thread)

    whole = await runner.replay(run_id)
    assert len(whole) > 2
    resumed = await runner.replay(run_id, after_seq=int(whole[0].data["seq"]))
    assert len(resumed) == len(whole) - 1
    assert [event.data["seq"] for event in resumed] == [event.data["seq"] for event in whole[1:]]


async def test_events_are_published_on_the_bus_as_well_as_stored(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    bus: EventBus[SseEvent],
) -> None:
    chat_provider.chat_script = [ChatTurn(text="live")]
    seen: list[str] = []

    with bus.subscribe() as queue:
        run_id = await send(runner, tasks, thread)
        while not queue.empty():
            seen.append(queue.get_nowait().data["kind"])

    assert "completed" in seen
    assert await ChatEventsRepository(runner.db).since(run_id)


# -- the history budget ----------------------------------------------------


async def test_the_oldest_messages_are_dropped_to_fit_the_budget(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    await runner.llm.settings.store("chatHistoryTokenBudget", 1000)
    chat_provider.chat_script = [ChatTurn(text="ok")]
    for index in range(6):
        await send(runner, tasks, thread, f"question {index} " + "x" * 1500)

    sent = chat_provider.chat_calls[-1].messages

    assert sent, "the newest question is always kept"
    assert len(sent) < len(await ChatRepository(runner.db).messages(thread))
    assert sent[0].role != "tool", "an orphaned tool result is a 400 on both APIs"


async def test_the_system_prompt_carries_the_set_scope(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    system = chat_provider.chat_calls[0].system
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in system
    assert f"Set {archive.set_id}" in system
    # Not only the Set: the experiment, which is what a grade is made against.
    assert "THE EXPERIMENT SO FAR" in system


async def test_an_unscoped_thread_gets_no_scope_block(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    plain = created.thread
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, plain.id, "what is a 1:2 ratio?")

    assert "THIS CONVERSATION IS ABOUT" not in chat_provider.chat_calls[0].system


# -- the tool surface handed to the provider -------------------------------


async def test_a_general_thread_is_given_the_archive_s_tools(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, created.thread.id, "what is a 1:2 ratio?")

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    assert names == GENERAL_TOOLS


async def test_the_provider_is_given_the_chat_tool_set(
    runner: ChatRunner, tasks: TaskRegistry, thread: int, chat_provider: FakeProvider
) -> None:
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    # The Set conversation's surface, because that is what this thread is: the
    # archive-wide tools are not merely refused, they are never described.
    assert names == SET_TOOLS
    assert not any(name.startswith("push_") for name in names)


async def test_a_set_being_designed_is_given_the_design_tools_and_a_dispatcher_to_match(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """The schemas sent, and the calls allowed, are the design scope — from the Set's flag."""
    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(),
    )
    opened = await ChatRepository(archive.db).open_thread(designed.id)
    assert opened.thread is not None
    chat_provider.chat_script = [
        ChatTurn(
            tool_calls=[
                ChatToolCall(
                    id="c1",
                    name="propose_set_version",
                    arguments={"reason": "Finer.", "grind_setting": "18"},
                )
            ],
            stop_reason="tool_use",
        ),
        ChatTurn(text="Let us talk about what you want first."),
    ]

    await send(runner, tasks, opened.thread.id, "Help me design this Set")

    names = {schema["function"]["name"] for schema in chat_provider.chat_calls[0].tools}
    assert names == DESIGN_TOOLS
    refused = chat_provider.chat_calls[1].messages[-1].tool_results[0]
    assert refused.ok is False
    assert "propose_initial_recipe" in refused.content


async def test_a_design_is_answered_by_the_design_prompt_until_its_recipe_is_accepted(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """Read from the Set on every turn: the turn after Accept is an ordinary Set chat."""
    from gaggiclanker.db.repos.llm import LlmCallsRepository
    from gaggiclanker.db.repos.profile_drafts import ProfileDraftsRepository, ProfileDraftWrite
    from gaggiclanker.db.repos.set_proposals import ProposalWrite, SetProposalsRepository
    from gaggiclanker.db.repos.sets import SetVersionPatch

    calls = LlmCallsRepository(archive.db)
    runner.llm.calls_repo = calls
    designed = await SetsRepository(archive.db).create_design(
        SetWrite(name="Designed", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        DesignBrief(goal="More body."),
    )
    opened = await ChatRepository(archive.db).open_thread(designed.id)
    assert opened.thread is not None
    thread_id = opened.thread.id
    chat_provider.chat_script = [ChatTurn(text="What basket?"), ChatTurn(text="Grade it.")]

    first = await send(runner, tasks, thread_id, "Help me design this Set")

    draft = await ProfileDraftsRepository(archive.db).create(
        ProfileDraftWrite(
            base_version_id=archive.profile_version_id,
            draft_version_id=archive.profile_version_id,
        )
    )
    proposals = SetProposalsRepository(archive.db)
    card = await proposals.create(
        designed.id,
        ProposalWrite(
            kind="design",
            draft_id=draft.id,
            thread_id=thread_id,
            reason="The recipe we agreed.",
            patch=SetVersionPatch(profile_version_id=archive.profile_version_id, dose_g=18),
        ),
    )
    assert card.proposal is not None, card.refused
    assert (await proposals.accept(designed.id, card.proposal.id)).refused is None

    second = await send(runner, tasks, thread_id, "It came out fast.")

    design_turn, set_turn = chat_provider.chat_calls
    assert "THIS CONVERSATION IS DESIGNING A NEW SET" in design_turn.system
    assert "GRADE FIRST" not in design_turn.system
    assert {schema["function"]["name"] for schema in design_turn.tools} == DESIGN_TOOLS
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in set_turn.system
    assert "GRADE FIRST" in set_turn.system
    # The card it came from is told as the recipe it set, not as an empty change.
    assert "its initial recipe was accepted as v1" in set_turn.system
    assert {schema["function"]["name"] for schema in set_turn.tools} == SET_TOOLS
    rows = {row.call_id: row.prompt_name for row in await calls.recent(limit=10)}
    assert (rows[f"chat-{first}"], rows[f"chat-{second}"]) == ("chat-design", "chat-set")


async def test_a_hand_made_set_that_names_no_profile_is_an_ordinary_set_chat(
    runner: ChatRunner,
    tasks: TaskRegistry,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """Its version 1 looks like an empty design, and nothing about it changes."""
    from gaggiclanker.db.repos.sets import SetVersionWrite

    any_profile = await SetsRepository(archive.db).create(
        SetWrite(name="Any profile", bean_id=archive.bean_id, grinder_id=archive.grinder_id),
        SetVersionWrite(),
    )
    opened = await ChatRepository(archive.db).open_thread(any_profile.id)
    assert opened.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, opened.thread.id)

    request = chat_provider.chat_calls[0]
    assert request.system.rstrip().endswith(
        "Those facts are the record, not the whole archive: use the tools for anything else, "
        "and for anything you are about to quote a number from."
    )
    assert "THIS CONVERSATION IS ABOUT ONE VERSION OF ONE SET" in request.system
    assert "DESIGNING" not in request.system
    assert {schema["function"]["name"] for schema in request.tools} == SET_TOOLS


async def test_the_usage_row_records_which_prompt_answered_the_turn(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    archive: Fixture,
    chat_provider: FakeProvider,
) -> None:
    """A turn has to be traceable to the instructions that produced it.

    The two kinds of conversation are answered by two different prompts, and
    the ledger row is where that is written down — an analysis of what the
    agent said six weeks ago is explainable only if the row names the prompt.
    """
    from gaggiclanker.db.repos.chat import ChatThreadWrite
    from gaggiclanker.db.repos.llm import LlmCallsRepository

    repo = LlmCallsRepository(archive.db)
    runner.llm.calls_repo = repo
    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    chat_provider.chat_script = [ChatTurn(text="ok"), ChatTurn(text="ok")]

    scoped_run = await send(runner, tasks, thread)
    general_run = await send(runner, tasks, created.thread.id, "what is a 1:2 ratio?")

    rows = {row.call_id: row.prompt_name for row in await repo.recent(limit=10)}
    assert rows[f"chat-{scoped_run}"] == "chat-set"
    assert rows[f"chat-{general_run}"] == "chat-general"


async def test_an_empty_message_is_refused_before_a_run_is_opened(
    runner: ChatRunner, tasks: TaskRegistry, thread: int
) -> None:
    from gaggiclanker.infra.errors import BadRequest

    with pytest.raises(BadRequest):
        await runner.send(thread, "   ", tasks=tasks)

    assert await ChatRepository(runner.db).messages(thread) == []


# -- the Set scope reaches the provider ------------------------------------


async def test_the_scope_is_on_the_request_so_the_cli_can_carry_it(
    runner: ChatRunner,
    tasks: TaskRegistry,
    thread: int,
    chat_provider: FakeProvider,
    archive: Fixture,
) -> None:
    """The other providers get the scope through the tool context; the CLI cannot.

    `claude_code` runs the tool loop itself against our MCP server, so the only
    way a scoped thread's `get_set` with no argument answers there is if the
    scope is on the request and ends up in the generated --mcp-config.
    """
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, thread)

    assert chat_provider.chat_calls[0].set_id == archive.set_id
    assert chat_provider.chat_calls[0].set_version_id == archive.version_id


async def test_an_unscoped_thread_sends_no_scope(
    runner: ChatRunner, tasks: TaskRegistry, archive: Fixture, chat_provider: FakeProvider
) -> None:
    from gaggiclanker.db.repos.chat import ChatThreadWrite

    created = await ChatRepository(archive.db).create_thread(ChatThreadWrite())
    assert created.thread is not None
    plain = created.thread
    chat_provider.chat_script = [ChatTurn(text="ok")]

    await send(runner, tasks, plain.id, "what is a 1:2 ratio?")

    assert chat_provider.chat_calls[0].set_id is None
