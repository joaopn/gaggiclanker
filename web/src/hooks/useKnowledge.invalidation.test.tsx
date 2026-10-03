import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAnswerInsight } from "@/hooks/useKnowledge";
import { queryKeys } from "@/lib/queryKeys";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { patchKnowledgeInsight, dismissKnowledgeInsight } = vi.hoisted(() => ({
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
}));

beforeEach(() => {
  vi.clearAllMocks();
  patchKnowledgeInsight.mockResolvedValue(knowledgeInsight({ set_id: 3, general: false }));
  dismissKnowledgeInsight.mockResolvedValue(
    knowledgeInsight({ set_id: 3, general: false, dismissed: true }),
  );
});

function spyOn(queryClient: QueryClient): readonly unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

describe("answering a Set's insight", () => {
  it.each(["add", "dismiss", "take_back"] as const)(
    "%s touches the knowledge prefix and the conversation, and no Set or shot",
    async (answer) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsight());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ id: 4, answer, threadId: 9 });

      await waitFor(() => expect(keys.length).toBe(2));
      // The insight itself, the Set's list with the general ones beside it and
      // the Knowledge list are all under `knowledge`; the card is drawn from
      // the transcript of the conversation that proposed it.
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("9"));
      expect(keys.flat()).not.toContain("sets");
      expect(keys.flat()).not.toContain("shots");
    },
  );

  it("without a conversation it reaches the knowledge prefix alone", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 4, answer: "add" });

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toEqual([queryKeys.knowledge.all]);
  });

  it("sends add and take back as the confirm switch and dismiss as its own route", async () => {
    const { result } = renderHookWithQueryClient(() => useAnswerInsight());

    await result.current.mutateAsync({ id: 4, answer: "add" });
    await result.current.mutateAsync({ id: 4, answer: "take_back" });
    await result.current.mutateAsync({ id: 4, answer: "dismiss" });

    expect(patchKnowledgeInsight).toHaveBeenNthCalledWith(1, 4, { confirmed: true });
    expect(patchKnowledgeInsight).toHaveBeenNthCalledWith(2, 4, { confirmed: false });
    expect(dismissKnowledgeInsight).toHaveBeenCalledWith(4);
  });
});
