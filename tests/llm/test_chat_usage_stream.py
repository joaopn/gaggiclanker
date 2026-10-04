"""Usage out of real Claude Code streams: per request, never per content block.

The three fixtures are captures of ``claude -p --output-format stream-json
--include-partial-messages`` (CLI 2.1.289, Haiku) with the session ids and the
init noise removed. In them a request is several ``assistant`` events (thinking,
text, tool_use) that share one ``message.id`` and one copy of the request's
usage with a stub output count, so the numbers below are what only a reader
keyed on the id can reproduce. Every figure is read off the stream by hand.
"""

from __future__ import annotations

from pathlib import Path

from gaggiclanker.llm.chat_types import ChatTurn
from gaggiclanker.llm.types import RequestUsage
from tests.llm.test_chat_claude_code import RecordedStream, provider, request

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "claude_stream"


async def replay(name: str) -> ChatTurn:
    lines = (FIXTURES / f"{name}.jsonl").read_text().splitlines(keepends=True)
    return await provider(RecordedStream(lines)).chat(request(), lambda _event: None)


async def test_four_requests_sharing_blocks_are_counted_once_each() -> None:
    turn = await replay("run1_four_requests")

    # context = fresh + cache write + cache read, per request.
    assert turn.usage.requests == (
        RequestUsage(context=21146, cache_read=13803, cache_write=7334, fresh=9, out=183),
        RequestUsage(context=21460, cache_read=21137, cache_write=313, fresh=10, out=92),
        RequestUsage(context=21603, cache_read=21450, cache_write=145, fresh=8, out=87),
        RequestUsage(context=21741, cache_read=21595, cache_write=138, fresh=8, out=69),
    )
    assert turn.usage.context_tokens == 21741  # the last request, not the sum
    assert len(turn.usage.requests) == 4


async def test_the_run_totals_are_the_envelopes_and_equal_the_per_request_sums() -> None:
    turn = await replay("run1_four_requests")
    usage = turn.usage

    assert (usage.prompt_tokens, usage.completion_tokens) == (85950, 431)
    assert (usage.fresh_tokens, usage.cache_write_tokens, usage.cache_read_tokens) == (
        35,
        7930,
        77985,
    )
    # The stop condition of the feature: the CLI's own sum agrees with ours.
    assert sum(r.context or 0 for r in usage.requests) == usage.prompt_tokens
    assert sum(r.out or 0 for r in usage.requests) == usage.completion_tokens
    assert sum(r.cache_read or 0 for r in usage.requests) == usage.cache_read_tokens
    assert sum(r.cache_write or 0 for r in usage.requests) == usage.cache_write_tokens
    assert sum(r.fresh or 0 for r in usage.requests) == usage.fresh_tokens
    assert usage.cost_usd == 0.025848499999999996
    assert usage.context_window == 200000


async def test_a_message_gets_the_requests_that_produced_it() -> None:
    turn = await replay("run1_four_requests")

    assert turn.tool_usage is not None and turn.answer_usage is not None
    assert [r.context for r in turn.tool_usage.requests] == [21146, 21460, 21603]
    assert [r.context for r in turn.answer_usage.requests] == [21741]
    assert turn.answer_usage.completion_tokens == 69
    assert (turn.tool_usage.completion_tokens or 0) + (
        turn.answer_usage.completion_tokens or 0
    ) == 431


async def test_parallel_tool_calls_are_two_requests_not_the_clis_num_turns() -> None:
    turn = await replay("run2_parallel_tools")

    # The envelope says num_turns 3; two requests were made, and the first one's
    # two tool_use blocks are one request.
    assert turn.usage.requests == (
        RequestUsage(context=1156, cache_read=0, cache_write=0, fresh=1156, out=165),
        RequestUsage(context=1443, cache_read=0, cache_write=0, fresh=1443, out=60),
    )
    assert (turn.usage.prompt_tokens, turn.usage.completion_tokens) == (2599, 225)
    assert [call.name for call in turn.executed_tool_calls] == [
        "mcp__t__get_word"
    ] * 2  # the capture used its own server name


async def test_a_run_cut_before_its_result_is_the_per_request_sum_not_an_inflated_one() -> None:
    """Cancel, timeout or crash: no envelope, and the old reader had added every block.

    Three requests started; the third never got its ``message_delta``, so its
    output is the stub the stream gave (1), a lower bound. The old reader summed
    the usage of each of the seven ``assistant`` events: 3+3+3+1+1+1+1 output
    tokens and the input of every block.
    """
    stream = RecordedStream(
        (FIXTURES / "run1_cut_before_result.jsonl").read_text().splitlines(True)
    )
    reader_turn = await provider(stream).chat(request(), lambda _event: None)

    usage = reader_turn.usage
    assert [r.context for r in usage.requests] == [21146, 21460, 21603]
    assert [r.out for r in usage.requests] == [183, 92, 1]
    assert usage.prompt_tokens == 21146 + 21460 + 21603
    assert usage.completion_tokens == 276
    assert usage.cost_usd is None and usage.context_window is None
