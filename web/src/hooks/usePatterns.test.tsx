import { act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePatterns } from "@/hooks/usePatterns";
import { patternRun, patternsData } from "@/test/knowledgeFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

const { getPatterns } = vi.hoisted(() => ({ getPatterns: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getPatterns,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: false });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("polling while a run is going", () => {
  it("re-reads every three seconds while a run is running, and stops when it is done", async () => {
    const running = patternsData({ run: patternRun({ status: "running", finished_at: null }) });
    const done = patternsData();
    getPatterns.mockResolvedValue(running);
    renderHookWithQueryClient(() => usePatterns());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getPatterns).toHaveBeenCalledTimes(1);

    // Not before three seconds…
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(getPatterns).toHaveBeenCalledTimes(1);
    // …then once per interval while it still says running.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(getPatterns).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(getPatterns).toHaveBeenCalledTimes(3);

    // The run ends: the next read says so, and nothing is read after it.
    getPatterns.mockResolvedValue(done);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    const afterDone = getPatterns.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(getPatterns).toHaveBeenCalledTimes(afterDone);
  });

  it("does not poll at all when no run is going", async () => {
    getPatterns.mockResolvedValue(patternsData());
    const { result } = renderHookWithQueryClient(() => usePatterns());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await waitFor(() => expect(result.current.data).toBeDefined());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });

    expect(getPatterns).toHaveBeenCalledTimes(1);
  });
});
