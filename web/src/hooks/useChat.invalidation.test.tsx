import type { QueryClient } from "@tanstack/react-query";
import { act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type ChatStreamEvent, useChatRun } from "@/hooks/useChat";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

let emit: (message: { data: ChatStreamEvent }) => void = () => undefined;
vi.mock("@/hooks/useSse", () => ({
  useSse: (_url: string | null, onEvent: (message: { data: ChatStreamEvent }) => void) => {
    emit = onEvent;
    return { connected: true };
  },
}));

function spyOn(queryClient: QueryClient): unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

describe("a finished chat run refreshes what an agent may have written", () => {
  it.each(["completed", "cancelled", "error"] as const)(
    "%s reaches the Sets, the knowledge, the drafts, the signatures and the shots' checks",
    (kind) => {
      const { queryClient } = renderHookWithQueryClient(() => useChatRun(5, 9));
      const keys = spyOn(queryClient);

      act(() => emit({ data: { seq: 1, kind } as ChatStreamEvent }));

      // A proposed signature is in force at once: the Profiles card, the Set page's line and
      // every shot's checks change with it.
      expect(keys).toContainEqual(queryKeys.signatures.all);
      expect(keys).toContainEqual(queryKeys.shots.all);
      expect(keys).toContainEqual(queryKeys.sets.all);
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.drafts.all);
    },
  );
});

describe("a drafted profile version appears mid-answer with its buttons", () => {
  it("refreshes the board and that draft's standing when its tool result arrives", () => {
    const { queryClient } = renderHookWithQueryClient(() => useChatRun(5, 9));
    const keys = spyOn(queryClient);

    act(() =>
      emit({
        data: {
          seq: 1,
          kind: "tool_call",
          id: "c1",
          name: "draft_profile",
          arguments: {},
        } as ChatStreamEvent,
      }),
    );
    act(() =>
      emit({
        data: {
          seq: 2,
          kind: "tool_result",
          id: "c1",
          ok: true,
          content: JSON.stringify({ draft_id: 12, status: "draft" }),
        } as ChatStreamEvent,
      }),
    );

    expect(keys).toContainEqual(queryKeys.drafts.standing(12));
    expect(keys).toContainEqual(queryKeys.board.all);
  });

  it("refreshes nothing for a failed call, or a result that made no draft", () => {
    const { queryClient } = renderHookWithQueryClient(() => useChatRun(5, 9));
    const keys = spyOn(queryClient);

    act(() =>
      emit({
        data: {
          seq: 1,
          kind: "tool_result",
          id: "c1",
          ok: false,
          content: JSON.stringify({ draft_id: 12 }),
        } as ChatStreamEvent,
      }),
    );
    act(() =>
      emit({
        data: {
          seq: 2,
          kind: "tool_result",
          id: "c2",
          ok: true,
          content: JSON.stringify({ shot: { shot_id: 1 } }),
        } as ChatStreamEvent,
      }),
    );

    expect(keys).toEqual([]);
  });
});
