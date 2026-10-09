import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  usePutOnBoard,
  useResolveConflict,
  useResumeBoard,
  useSetActiveVersion,
  useSetOnMachine,
  useSetStarred,
} from "@/hooks/useBoard";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  putOnBoard,
  setBoardOnMachine,
  setBoardStarred,
  setBoardActiveVersion,
  resolveBoardConflict,
  resumeBoard,
} = vi.hoisted(() => ({
  putOnBoard: vi.fn(),
  setBoardOnMachine: vi.fn(),
  setBoardStarred: vi.fn(),
  setBoardActiveVersion: vi.fn(),
  resolveBoardConflict: vi.fn(),
  resumeBoard: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  putOnBoard,
  setBoardOnMachine,
  setBoardStarred,
  setBoardActiveVersion,
  resolveBoardConflict,
  resumeBoard,
}));

const row = { id: 4, label: "9 Bar Espresso [AI]" };

beforeEach(() => {
  vi.clearAllMocks();
  putOnBoard.mockResolvedValue(row);
  setBoardOnMachine.mockResolvedValue(row);
  setBoardStarred.mockResolvedValue(row);
  setBoardActiveVersion.mockResolvedValue(row);
  resolveBoardConflict.mockResolvedValue(row);
  resumeBoard.mockResolvedValue({ resumed: true });
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

/** Everything a change to the board can change, by prefix. */
const BOARD_WRITES = [
  queryKeys.board.all,
  queryKeys.drafts.all,
  queryKeys.profiles.all,
  queryKeys.sync.all,
  queryKeys.device.all,
];

describe("every board mutation refreshes everything it changes", () => {
  it("putting a draft on the board", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePutOnBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ draftId: 1 });

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
    // No Set named, no Set record to refresh.
    expect(keys).not.toContainEqual([...queryKeys.sets.all]);
  });

  it("putting a draft on the board for a Set also refreshes the Sets", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePutOnBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ draftId: 1, setId: 3, major: true });

    for (const key of [...BOARD_WRITES, queryKeys.sets.all]) {
      await waitFor(() => expect(keys).toContainEqual([...key]));
    }
  });

  it("a refused put still refreshes, since the server is the authority on what the board holds", async () => {
    putOnBoard.mockRejectedValue(new Error("That profile version is already on the board."));
    const { result, queryClient } = renderHookWithQueryClient(() => usePutOnBoard());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ draftId: 1 })).rejects.toThrow();

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.board.all]));
  });

  it("switching a profile on or off the machine", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetOnMachine());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ rowId: 4, on: false });

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
    expect(setBoardOnMachine).toHaveBeenCalledWith(4, false);
  });

  it("starring a profile", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetStarred());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ rowId: 4, starred: false });

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
    expect(setBoardStarred).toHaveBeenCalledWith(4, false);
  });

  it("making a version active also refreshes the Sets, which brew whatever is active", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetActiveVersion());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ rowId: 4, versionId: 9 });

    for (const key of [...BOARD_WRITES, queryKeys.sets.all]) {
      await waitFor(() => expect(keys).toContainEqual([...key]));
    }
    expect(setBoardActiveVersion).toHaveBeenCalledWith(4, 9);
  });

  it("choosing a side of a conflict, and a refused choice still refreshes both sides", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useResolveConflict());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ rowId: 4, keep: "app", contentHash: "h1" });
    for (const key of [...BOARD_WRITES, queryKeys.sets.all]) {
      await waitFor(() => expect(keys).toContainEqual([...key]));
    }
    expect(resolveBoardConflict).toHaveBeenCalledWith(4, "app", "h1");

    keys.length = 0;
    resolveBoardConflict.mockRejectedValue(new Error("changed"));
    await expect(
      result.current.mutateAsync({ rowId: 4, keep: "machine", contentHash: "h1" }),
    ).rejects.toThrow();
    // The board holds the conflict query: a stale refusal re-reads what the machine has now.
    await waitFor(() => expect(keys).toContainEqual([...queryKeys.board.all]));
  });

  it("resuming a paused board", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useResumeBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync();

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
  });
});

describe("a put refreshes where each proposal stands", () => {
  it("marks a cached standing read stale, whether or not the put carried a name or a Set", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePutOnBoard());
    // The test client collects a query nobody observes at once; these are kept to be inspected.
    queryClient.setQueryDefaults(queryKeys.drafts.all, { gcTime: Number.POSITIVE_INFINITY });
    queryClient.setQueryData(queryKeys.drafts.standing(7), { state: "waiting" });
    queryClient.setQueryData(queryKeys.drafts.standing(8), { state: "waiting" });
    expect(queryClient.getQueryState(queryKeys.drafts.standing(7))?.isInvalidated).toBe(false);

    await result.current.mutateAsync({ draftId: 7, setId: 3, major: false, label: "Gentle Bloom" });

    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.drafts.standing(7))?.isInvalidated).toBe(true),
    );
    // Every proposal's card is refreshed: the put changes what another one would land on.
    expect(queryClient.getQueryState(queryKeys.drafts.standing(8))?.isInvalidated).toBe(true);
  });

  it("sends the name the person typed, and the Set only when it records one", async () => {
    const { result } = renderHookWithQueryClient(() => usePutOnBoard());

    await result.current.mutateAsync({ draftId: 7, label: "Gentle Bloom" });

    expect(putOnBoard).toHaveBeenCalledWith({ draftId: 7, label: "Gentle Bloom" });
  });
});
