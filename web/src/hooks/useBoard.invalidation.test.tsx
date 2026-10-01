import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useDeleteBoardRow,
  useGoBackOnBoard,
  usePutOnBoard,
  useResumeBoard,
  useSetHomeScreen,
  useTakeOntoBoard,
} from "@/hooks/useBoard";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  putOnBoard,
  setBoardHomeScreen,
  deleteBoardRow,
  resumeBoard,
  takeOntoBoard,
  goBackOnBoard,
} = vi.hoisted(() => ({
  goBackOnBoard: vi.fn(),
  takeOntoBoard: vi.fn(),
  putOnBoard: vi.fn(),
  setBoardHomeScreen: vi.fn(),
  deleteBoardRow: vi.fn(),
  resumeBoard: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  putOnBoard,
  setBoardHomeScreen,
  deleteBoardRow,
  resumeBoard,
  takeOntoBoard,
  goBackOnBoard,
}));

const row = { id: 4, label: "9 Bar Espresso [AI]" };

beforeEach(() => {
  vi.clearAllMocks();
  putOnBoard.mockResolvedValue(row);
  setBoardHomeScreen.mockResolvedValue(row);
  deleteBoardRow.mockResolvedValue(row);
  resumeBoard.mockResolvedValue({ resumed: true });
  takeOntoBoard.mockResolvedValue(row);
  goBackOnBoard.mockResolvedValue(row);
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

  it("the home-screen toggle", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetHomeScreen());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ rowId: 4, on: false });

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
  });

  it("deleting a profile", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDeleteBoardRow());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(4);

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
  });

  it("taking a machine profile onto the board", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useTakeOntoBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync("later");

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
  });

  it("going back a version also refreshes the Sets, whose versions the sync then touches", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useGoBackOnBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(4);

    for (const key of [...BOARD_WRITES, queryKeys.sets.all]) {
      await waitFor(() => expect(keys).toContainEqual([...key]));
    }
    expect(goBackOnBoard).toHaveBeenCalledWith(4);
  });

  it("resuming a paused board", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useResumeBoard());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync();

    for (const key of BOARD_WRITES) await waitFor(() => expect(keys).toContainEqual([...key]));
  });
});
