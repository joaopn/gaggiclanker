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
