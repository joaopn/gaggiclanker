import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { useDuplicateSet } from "@/hooks/useDuplicate";
import { queryKeys } from "@/lib/queryKeys";
import { duplicateAnswer } from "@/test/duplicateFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { duplicateVersion } = vi.hoisted(() => ({ duplicateVersion: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  duplicateVersion,
}));

beforeEach(() => {
  vi.clearAllMocks();
  duplicateVersion.mockResolvedValue(duplicateAnswer());
});

function spyOn(queryClient: QueryClient): unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

/**
 * Every prefix a duplicate writes under: the Sets (the list, and the new Set's page, one prefix),
 * the board and what the board's writes reach, which includes the profile mirror and so every
 * profile-versions query, and the insights. No shot is filed or moved by it.
 */
const TOUCHED = [
  queryKeys.sets.all,
  queryKeys.board.all,
  queryKeys.drafts.all,
  queryKeys.profiles.all,
  queryKeys.sync.all,
  queryKeys.device.all,
  queryKeys.knowledge.all,
];

async function expectTouched(keys: unknown[][]) {
  for (const key of TOUCHED) await waitFor(() => expect(keys).toContainEqual([...key]));
  expect(keys.flat()).not.toContain("shots");
}

const VARIABLES = {
  setId: 3,
  versionId: 22,
  body: {
    set_name: "Guji milk",
    profile_name: "Blooming milk",
    bean_id: 1,
    grinder_id: 1,
    insight_ids: [11],
  },
};

describe("duplicating refreshes everything it writes", () => {
  it("on success", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDuplicateSet());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(VARIABLES);

    await expectTouched(keys);
    expect(duplicateVersion).toHaveBeenCalledWith(3, 22, VARIABLES.body);
  });

  it("when the server refused it, since the server is the authority on what exists", async () => {
    duplicateVersion.mockRejectedValue(new ApiClientError("Taken.", { status: 409 }));
    const { result, queryClient } = renderHookWithQueryClient(() => useDuplicateSet());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync(VARIABLES)).rejects.toThrow();

    await expectTouched(keys);
  });

  it("when no answer came back, since the Set may exist anyway", async () => {
    duplicateVersion.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result, queryClient } = renderHookWithQueryClient(() => useDuplicateSet());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync(VARIABLES)).rejects.toThrow();

    await expectTouched(keys);
  });
});
