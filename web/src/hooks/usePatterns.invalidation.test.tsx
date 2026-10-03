import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAnswerPatternProposal, useStartPatternRun } from "@/hooks/usePatterns";
import { queryKeys } from "@/lib/queryKeys";
import { patternProposal, patternRun } from "@/test/knowledgeFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { startPatternRun, approvePatternProposal, dismissPatternProposal } = vi.hoisted(() => ({
  startPatternRun: vi.fn(),
  approvePatternProposal: vi.fn(),
  dismissPatternProposal: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  startPatternRun,
  approvePatternProposal,
  dismissPatternProposal,
}));

beforeEach(() => {
  vi.clearAllMocks();
  startPatternRun.mockResolvedValue(patternRun({ status: "running", finished_at: null }));
  approvePatternProposal.mockResolvedValue({
    proposal: patternProposal({ status: "approved" }),
    skipped: [],
    deleted_insight_ids: [21, 22],
  });
  dismissPatternProposal.mockResolvedValue({
    proposal: patternProposal({ status: "dismissed" }),
    skipped: [],
    deleted_insight_ids: [],
  });
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

describe("starting a run", () => {
  it("re-reads the pattern section and nothing else: a run writes no insight", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useStartPatternRun());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync();

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toContainEqual(queryKeys.knowledge.patterns());
    expect(keys).not.toContainEqual(queryKeys.knowledge.all);
    expect(keys.flat()).not.toContain("sets");
    expect(keys.flat()).not.toContain("shots");
  });
});

describe("answering a proposal", () => {
  it.each(["approve", "dismiss"] as const)(
    "%s touches the whole knowledge prefix: the list, every Set's insights and the run",
    async (answer) => {
      const { result, queryClient } = renderHookWithQueryClient(() => useAnswerPatternProposal());
      const keys = spyOn(queryClient);

      await result.current.mutateAsync({ id: 11, answer });

      await waitFor(() => expect(keys.length).toBe(1));
      // The Knowledge list, each source Set's insights (the Set pages read them under
      // `knowledge`), the chat's insight cards and the pattern run share one prefix.
      expect(keys).toContainEqual(queryKeys.knowledge.all);
      expect(queryKeys.knowledge.patterns().slice(0, 1)).toEqual(queryKeys.knowledge.all);
      expect(queryKeys.knowledge.insights({ set_id: 3 }).slice(0, 1)).toEqual(
        queryKeys.knowledge.all,
      );
      expect(keys.flat()).not.toContain("shots");
    },
  );

  it("sends the right call for each answer", async () => {
    const { result } = renderHookWithQueryClient(() => useAnswerPatternProposal());

    await result.current.mutateAsync({ id: 11, answer: "approve" });
    await result.current.mutateAsync({ id: 12, answer: "dismiss" });

    expect(approvePatternProposal).toHaveBeenCalledWith(11);
    expect(dismissPatternProposal).toHaveBeenCalledWith(12);
  });
});
