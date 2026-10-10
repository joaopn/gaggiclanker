import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { usePinVersion, useUnpin } from "@/hooks/usePins";
import { useArchiveSet } from "@/hooks/useSets";
import { queryKeys } from "@/lib/queryKeys";
import { pinDetail } from "@/test/pinFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { setRow } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: (...args: unknown[]) => toastError(...args), info: vi.fn() },
  Toaster: () => null,
}));

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
const { pinVersion, unpinPin, archiveSet } = vi.hoisted(() => ({
  pinVersion: vi.fn(),
  unpinPin: vi.fn(),
  archiveSet: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  pinVersion,
  unpinPin,
  archiveSet,
}));

beforeEach(() => {
  vi.clearAllMocks();
  pinVersion.mockResolvedValue({ pin: pinDetail(), board_id: 7 });
  unpinPin.mockResolvedValue({
    pin: pinDetail({ unpinned_at: "2026-10-10T10:00:00Z" }),
    board_id: 7,
  });
  archiveSet.mockResolvedValue(setRow({ id: 3, archived: true }));
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
 * Every prefix a pin's writes touch: the Set (its pins, its versions' pinned marker, the list),
 * the board (a row appears or goes) and what the board's writes reach, which includes the
 * profile mirror and so every profile-versions query. No shot is refiled by any of the three.
 */
const TOUCHED = [
  queryKeys.sets.all,
  queryKeys.board.all,
  queryKeys.drafts.all,
  queryKeys.profiles.all,
  queryKeys.sync.all,
  queryKeys.device.all,
];

async function expectTouched(keys: unknown[][]) {
  for (const key of TOUCHED) await waitFor(() => expect(keys).toContainEqual([...key]));
  expect(keys.flat()).not.toContain("shots");
}

describe("pinning, unpinning and archiving refresh everything they write", () => {
  it("pinning a version", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePinVersion());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ setId: 3, versionId: 22 });

    await expectTouched(keys);
    expect(pinVersion).toHaveBeenCalledWith(3, 22, undefined);
  });

  it("a refused pin still refreshes, since the server is the authority on what is pinned", async () => {
    pinVersion.mockRejectedValue(new Error("That version is already pinned."));
    const { result, queryClient } = renderHookWithQueryClient(() => usePinVersion());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ setId: 3, versionId: 22 })).rejects.toThrow();

    await expectTouched(keys);
  });

  it("unpinning", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useUnpin());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ pinId: 5 });

    await expectTouched(keys);
    expect(unpinPin).toHaveBeenCalledWith(5);
  });

  it("archiving a Set, which unpins its pins in the same write", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useArchiveSet());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(3);

    await expectTouched(keys);
  });
});

describe("a request that got no answer names no outcome", () => {
  const offline = () => new TypeError("Failed to fetch");
  const noAnswer = (where: string) =>
    `No answer came back from the app. Check ${where} before trying again.`;

  it("pinning toasts nothing (the dialog says it once) and still refreshes the Set and the board", async () => {
    pinVersion.mockRejectedValue(offline());
    const { result, queryClient } = renderHookWithQueryClient(() => usePinVersion());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ setId: 3, versionId: 22 })).rejects.toThrow();

    // The pin may exist although its answer was lost: the card must be able to show it.
    await expectTouched(keys);
    expect(toastError).not.toHaveBeenCalled();
  });

  it("a refusal the server worded is not toasted either", async () => {
    pinVersion.mockRejectedValue(new ApiClientError("Already pinned.", { status: 409 }));
    const { result } = renderHookWithQueryClient(() => usePinVersion());

    await expect(result.current.mutateAsync({ setId: 3, versionId: 22 })).rejects.toThrow();

    expect(toastError).not.toHaveBeenCalled();
  });

  it("unpinning toasts that no answer came, not the browser's words, and refreshes", async () => {
    unpinPin.mockRejectedValue(offline());
    const { result, queryClient } = renderHookWithQueryClient(() => useUnpin());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ pinId: 5 })).rejects.toThrow();

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(noAnswer("the page")));
    await expectTouched(keys);
  });

  it("archiving toasts that no answer came, not the browser's words, and refreshes", async () => {
    archiveSet.mockRejectedValue(offline());
    const { result, queryClient } = renderHookWithQueryClient(() => useArchiveSet());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync(3)).rejects.toThrow();

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(noAnswer("the page")));
    await expectTouched(keys);
  });

  it("a server-worded refusal of an unpin keeps the server's sentence", async () => {
    unpinPin.mockRejectedValue(new ApiClientError("Not pinned.", { status: 409 }));
    const { result } = renderHookWithQueryClient(() => useUnpin());

    await expect(result.current.mutateAsync({ pinId: 5 })).rejects.toThrow();

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Not pinned."));
  });
});
