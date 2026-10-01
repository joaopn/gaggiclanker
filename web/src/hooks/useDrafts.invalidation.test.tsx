import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCreateDraft, useDiscardDraft, useRefineDraft } from "@/hooks/useDrafts";
import { queryKeys } from "@/lib/queryKeys";
import { draft } from "@/test/draftFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { createProfileDraft, refineProfileDraft, discardProfileDraft } = vi.hoisted(() => ({
  createProfileDraft: vi.fn(),
  refineProfileDraft: vi.fn(),
  discardProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  createProfileDraft,
  refineProfileDraft,
  discardProfileDraft,
}));

beforeEach(() => {
  vi.clearAllMocks();
  createProfileDraft.mockResolvedValue(draft());
  refineProfileDraft.mockResolvedValue(draft({ id: 2 }));
  discardProfileDraft.mockResolvedValue(draft({ status: "discarded" }));
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

describe("a draft made, refined or discarded refreshes the board that says where a put lands", () => {
  it("making one (the editor's save, the wizard)", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useCreateDraft());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ base_version_id: 7 });

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.drafts.all]));
    await waitFor(() => expect(keys).toContainEqual([...queryKeys.board.all]));
  });

  it("refining one", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useRefineDraft());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 1, notes: "softer" });

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.board.all]));
  });

  it("discarding one", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDiscardDraft());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(1);

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.board.all]));
  });
});
