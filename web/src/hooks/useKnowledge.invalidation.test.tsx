import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useAnswerInsight,
  useAnswerInsightDeletion,
  useDeleteKnowledgeInsight,
  usePatchKnowledgeInsight,
} from "@/hooks/useKnowledge";
import { queryKeys } from "@/lib/queryKeys";
import { insightDeletion, knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  deleteKnowledgeInsight,
  acceptInsightDeletion,
  keepInsightDeletion,
} = vi.hoisted(() => ({
  deleteKnowledgeInsight: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
  acceptInsightDeletion: vi.fn(),
  keepInsightDeletion: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  deleteKnowledgeInsight,
  acceptInsightDeletion,
  keepInsightDeletion,
}));

beforeEach(() => {
  vi.clearAllMocks();
  deleteKnowledgeInsight.mockResolvedValue({ deleted: true });
  acceptInsightDeletion.mockResolvedValue({ proposal: insightDeletion({ status: "deleted" }) });
  keepInsightDeletion.mockResolvedValue({ proposal: insightDeletion({ status: "kept" }) });
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

describe("editing or deleting a Set's insight", () => {
  it("an edit reaches the knowledge prefix, which holds the Set's list and the Knowledge list", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePatchKnowledgeInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 4, patch: { text: "Reworded." } });

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toEqual([queryKeys.knowledge.all]);
  });

  it("a delete reaches the same, and no Set or shot", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDeleteKnowledgeInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(4);

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toEqual([queryKeys.knowledge.all]);
    expect(keys.flat()).not.toContain("sets");
  });
});

describe("answering an agent's proposed deletion", () => {
  it.each([
    ["delete", acceptInsightDeletion],
    ["keep", keepInsightDeletion],
  ] as const)(
    "%s touches the knowledge prefix and the conversation, and no Set or shot",
    async (answer, call) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsightDeletion());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ setId: 3, proposalId: 9, answer, threadId: 14 });

      await waitFor(() => expect(keys.length).toBe(2));
      // The insight, the Set's list (with its waiting deletions), the Knowledge list and
      // the deletion cards are all under `knowledge`; the card is drawn from the
      // transcript of the conversation that proposed it.
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("14"));
      expect(keys.flat()).not.toContain("sets");
      expect(keys.flat()).not.toContain("shots");
      expect(call).toHaveBeenCalledWith(3, 9);
    },
  );

  it("sends delete and keep to their own routes and never the other", async () => {
    const { result } = renderHookWithQueryClient(() => useAnswerInsightDeletion());

    await result.current.mutateAsync({ setId: 3, proposalId: 9, answer: "keep" });
    expect(keepInsightDeletion).toHaveBeenCalledTimes(1);
    expect(acceptInsightDeletion).not.toHaveBeenCalled();
  });

  it("keeps its cards under the knowledge prefix, so an Add that replaces one reaches them", () => {
    expect(queryKeys.knowledge.insightDeletions("3", 14)).toEqual([
      ...queryKeys.knowledge.all,
      "insight-deletions",
      "3",
      14,
    ]);
  });
});

describe("a replacing Add and the Set page's Delete", () => {
  it("an Add that deletes the old insight reaches the insight lists and the deletion cards by the one prefix", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 4, answer: "add", threadId: 14 });

    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.knowledge.all);
    // The deletion cards sit under that prefix, so the Add that removed an insight
    // (and made its waiting deletions stale) refreshes them too.
    expect(queryKeys.knowledge.insightDeletions("3").slice(0, 1)).toEqual(queryKeys.knowledge.all);
  });

  it("the Set page's Delete touches the knowledge prefix", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDeleteKnowledgeInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(4);

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toEqual([queryKeys.knowledge.all]);
  });
});
