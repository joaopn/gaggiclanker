import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUpdateSettings } from "@/hooks/useSettings";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

const { patchSettings } = vi.hoisted(() => ({
  patchSettings: vi.fn(async () => ({})),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  patchSettings,
}));

type InvalidateSpy = { mock: { calls: ReadonlyArray<ReadonlyArray<unknown>> } };

function calledWith(spy: InvalidateSpy, key: readonly unknown[]): number {
  return spy.mock.calls.filter(
    ([filters]) =>
      JSON.stringify((filters as { queryKey?: unknown } | undefined)?.queryKey) ===
      JSON.stringify(key),
  ).length;
}

describe("useUpdateSettings", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("re-reads the machine connection after a machine setting is saved, and again a moment later", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result, queryClient } = renderHookWithQueryClient(() => useUpdateSettings());
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.mutateAsync({ gaggimateHost: "10.0.0.9" });
    });

    await waitFor(() => expect(calledWith(invalidate, queryKeys.device.status())).toBe(1));
    expect(calledWith(invalidate, queryKeys.sync.all)).toBe(1);

    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
    await waitFor(() => expect(calledWith(invalidate, queryKeys.device.status())).toBe(2));
  });

  it("leaves the machine connection alone when only other settings are saved", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useUpdateSettings());
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      await result.current.mutateAsync({ modelDefault: "sonnet" });
    });

    await waitFor(() => expect(calledWith(invalidate, queryKeys.settings.all)).toBe(1));
    expect(calledWith(invalidate, queryKeys.device.status())).toBe(0);
  });
});
