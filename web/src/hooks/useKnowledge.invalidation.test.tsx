import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useAnswerGeneralInsightDeletion,
  useAnswerInsight,
  useAnswerInsightDeletion,
  useDeleteKnowledgeInsight,
  usePatchKnowledgeInsight,
  useSaveMessageAsInsight,
} from "@/hooks/useKnowledge";
import { queryKeys } from "@/lib/queryKeys";
import {
  generalInsightDeletion,
  insightDeletion,
  knowledgeInsight,
} from "@/test/knowledgeFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  createKnowledgeInsight,
  deleteKnowledgeInsight,
  acceptInsightDeletion,
  keepInsightDeletion,
  acceptGeneralInsightDeletion,
  keepGeneralInsightDeletion,
} = vi.hoisted(() => ({
  createKnowledgeInsight: vi.fn(),
  deleteKnowledgeInsight: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
  acceptInsightDeletion: vi.fn(),
  keepInsightDeletion: vi.fn(),
  acceptGeneralInsightDeletion: vi.fn(),
  keepGeneralInsightDeletion: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  createKnowledgeInsight,
  deleteKnowledgeInsight,
  acceptInsightDeletion,
  keepInsightDeletion,
  acceptGeneralInsightDeletion,
  keepGeneralInsightDeletion,
}));

beforeEach(() => {
  vi.clearAllMocks();
  deleteKnowledgeInsight.mockResolvedValue({ deleted: true });
  acceptInsightDeletion.mockResolvedValue({ proposal: insightDeletion({ status: "deleted" }) });
  keepInsightDeletion.mockResolvedValue({ proposal: insightDeletion({ status: "kept" }) });
  acceptGeneralInsightDeletion.mockResolvedValue(generalInsightDeletion({ status: "deleted" }));
  keepGeneralInsightDeletion.mockResolvedValue(generalInsightDeletion({ status: "kept" }));
  createKnowledgeInsight.mockResolvedValue(knowledgeInsight({ id: 12, confirmed: true }));
  patchKnowledgeInsight.mockResolvedValue(knowledgeInsight({ recipe_id: 3, general: false }));
  dismissKnowledgeInsight.mockResolvedValue(
    knowledgeInsight({ recipe_id: 3, general: false, dismissed: true }),
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

describe("answering a Recipe's insight", () => {
  it.each(["add"] as const)(
    "%s touches the knowledge prefix and the conversation, and no Recipe or shot",
    async (answer) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsight());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ id: 4, answer, threadId: 9 });

      await waitFor(() => expect(keys.length).toBe(2));
      // The insight itself, the Recipe's list with the general ones beside it and
      // the Knowledge list are all under `knowledge`; the card is drawn from
      // the transcript of the conversation that proposed it.
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("9"));
      expect(keys.flat()).not.toContain("recipes");
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

describe("answering and saving a general insight", () => {
  it("an Add with the person's edits is one PATCH carrying the words, the scope and the confirmation", async () => {
    const { result } = renderHookWithQueryClient(() => useAnswerInsight());

    await result.current.mutateAsync({
      id: 4,
      answer: "add",
      edit: { text: "Reworded.", scope: { grinder_id: 2 } },
    });

    expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1);
    expect(patchKnowledgeInsight).toHaveBeenCalledWith(4, {
      text: "Reworded.",
      scope: { grinder_id: 2 },
      confirmed: true,
    });
  });

  it.each(["add", "dismiss"] as const)(
    "%s invalidates the knowledge lists (the Recipe's insights among them) and the thread's messages",
    async (answer) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsight());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ id: 4, answer, threadId: 9 });

      await waitFor(() => expect(keys.length).toBe(2));
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("9"));
      // `knowledge` holds ["knowledge", "insights", {}] and ["knowledge", "insights", {recipe_id}].
      expect(queryKeys.knowledge.insights({ recipe_id: 3 }).slice(0, 1)).toEqual(
        queryKeys.knowledge.all,
      );
    },
  );

  it("a save from a message writes it from that message, confirmed, and reaches the same", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSaveMessageAsInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      text: "Always rinse.",
      scope: { grinder_id: 2 },
      messageId: 31,
      threadId: 9,
    });

    expect(createKnowledgeInsight).toHaveBeenCalledWith({
      text: "Always rinse.",
      scope: { grinder_id: 2 },
      from_message_id: 31,
      confirmed: true,
    });
    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.knowledge.all);
    expect(keys).toContainEqual(queryKeys.chat.thread("9"));
  });
});

describe("editing or deleting a Recipe's insight", () => {
  it("an edit reaches the knowledge prefix, which holds the Recipe's list and the Knowledge list", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePatchKnowledgeInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 4, patch: { text: "Reworded." } });

    // And the open conversations, whose messages say "Saved as general insight #N" from it.
    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.knowledge.all);
    expect(keys).toContainEqual(["chat", "thread"]);
  });

  it("a delete reaches the same, and no Recipe or shot", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDeleteKnowledgeInsight());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(4);

    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.knowledge.all);
    expect(keys).toContainEqual(["chat", "thread"]);
    expect(keys.flat()).not.toContain("recipes");
  });
});

describe("answering an agent's proposed deletion", () => {
  it.each([
    ["delete", acceptInsightDeletion],
    ["keep", keepInsightDeletion],
  ] as const)(
    "%s touches the knowledge prefix and the conversation, and no Recipe or shot",
    async (answer, call) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerInsightDeletion());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ recipeId: 3, proposalId: 9, answer, threadId: 14 });

      await waitFor(() => expect(keys.length).toBe(2));
      // The insight, the Recipe's list (with its waiting deletions), the Knowledge list and
      // the deletion cards are all under `knowledge`; the card is drawn from the
      // transcript of the conversation that proposed it.
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("14"));
      expect(keys.flat()).not.toContain("recipes");
      expect(keys.flat()).not.toContain("shots");
      expect(call).toHaveBeenCalledWith(3, 9);
      // Each answer goes to its own route and never the other.
      const other = call === acceptInsightDeletion ? keepInsightDeletion : acceptInsightDeletion;
      expect(other).not.toHaveBeenCalled();
    },
  );

  it("keeps its cards under the knowledge prefix, so an Add that replaces one reaches them", () => {
    expect(queryKeys.knowledge.insightDeletions("3", 14)).toEqual([
      ...queryKeys.knowledge.all,
      "insight-deletions",
      "3",
      14,
    ]);
  });
});

describe("answering a proposal to delete a general insight", () => {
  it.each([
    ["delete", acceptGeneralInsightDeletion, keepGeneralInsightDeletion],
    ["keep", keepGeneralInsightDeletion, acceptGeneralInsightDeletion],
  ] as const)(
    "%s invalidates the knowledge prefix and the conversation's messages, and no Recipe or shot",
    async (answer, call, other) => {
      const { result, queryClient } = renderHookWithQueryClient(() =>
        useAnswerGeneralInsightDeletion(),
      );
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ proposalId: 12, answer, threadId: 14 });

      await waitFor(() => expect(keys.length).toBe(2));
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(keys).toContainEqual(queryKeys.chat.thread("14"));
      expect(keys.flat()).not.toContain("recipes");
      expect(keys.flat()).not.toContain("shots");
      expect(call).toHaveBeenCalledWith(12);
      expect(other).not.toHaveBeenCalled();
    },
  );

  it("keeps everything it must refresh under the knowledge prefix", () => {
    const prefix = queryKeys.knowledge.all;
    // The knowledge insights list, every Recipe's insights, one insight (a request card), and the
    // general proposal itself: one invalidation of the prefix reaches all four.
    for (const key of [
      queryKeys.knowledge.insights({}),
      queryKeys.knowledge.insights({ recipe_id: 3 }),
      queryKeys.knowledge.insight("7"),
      queryKeys.knowledge.generalInsightDeletion(12),
    ]) {
      expect(key.slice(0, prefix.length)).toEqual(prefix);
    }
  });
});

describe("the Knowledge page's own edit or delete of a general insight", () => {
  it.each([
    ["edit", () => usePatchKnowledgeInsight(), { id: 7, patch: { text: "Reworded." } }],
    ["delete", () => useDeleteKnowledgeInsight(), 7],
  ] as const)(
    "%s refreshes the general proposals and request cards on screen, and the open conversations",
    async (_name, hook, variables) => {
      const { result, queryClient } = renderHookWithQueryClient(hook as never);
      queryClient.setQueryData(
        queryKeys.knowledge.generalInsightDeletion(12),
        generalInsightDeletion(),
      );
      queryClient.setQueryData(queryKeys.knowledge.insight("31"), knowledgeInsight({ id: 31 }));
      queryClient.setQueryData(queryKeys.chat.thread("14"), { thread: {} });

      await (result.current as { mutateAsync: (v: unknown) => Promise<unknown> }).mutateAsync(
        variables,
      );

      await waitFor(() => {
        expect(
          queryClient.getQueryState(queryKeys.knowledge.generalInsightDeletion(12))?.isInvalidated,
        ).toBe(true);
        expect(queryClient.getQueryState(queryKeys.knowledge.insight("31"))?.isInvalidated).toBe(
          true,
        );
        expect(queryClient.getQueryState(queryKeys.chat.thread("14"))?.isInvalidated).toBe(true);
      });
    },
  );
});
