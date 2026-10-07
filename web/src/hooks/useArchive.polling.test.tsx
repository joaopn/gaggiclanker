import { act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { READING_POLL_MS, useShots, useShotsInfinite } from "@/hooks/useArchive";
import { reviewBlock } from "@/test/claimFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

const { getShots } = vi.hoisted(() => ({ getShots: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShots,
}));

const page = (state: "running" | "reviewed" | "unreviewed") => ({
  items: [{ id: 1, review: reviewBlock({ state }) }],
  total: 1,
  limit: 50,
  offset: null,
  next_cursor: null,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => vi.useRealTimers());

/**
 * The event stream says when a reading finishes, but it is lossy and the window does not refetch
 * on focus: a list with a reading running polls, and stops when none is.
 */
describe.each([
  ["the shots list", () => useShots()],
  ["the shots page's list", () => useShotsInfinite()],
])("%s while a reading runs", (_name, hook) => {
  it("re-reads every few seconds, and stops once nothing is running", async () => {
    getShots.mockResolvedValueOnce(page("running")).mockResolvedValue(page("reviewed"));
    renderHookWithQueryClient(hook as () => any);
    await waitFor(() => expect(getShots).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(READING_POLL_MS + 100);
    });
    await waitFor(() => expect(getShots).toHaveBeenCalledTimes(2));

    // The second answer has nothing running: no third request, however long it waits.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(READING_POLL_MS * 4);
    });
    expect(getShots).toHaveBeenCalledTimes(2);
  });

  it("does not poll a list with nothing running", async () => {
    getShots.mockResolvedValue(page("unreviewed"));
    renderHookWithQueryClient(hook as () => any);
    await waitFor(() => expect(getShots).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(READING_POLL_MS * 4);
    });
    expect(getShots).toHaveBeenCalledTimes(1);
  });
});
