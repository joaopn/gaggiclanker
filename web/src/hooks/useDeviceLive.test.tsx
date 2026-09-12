import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useIsBrewing, useLiveStatus } from "@/hooks/useDeviceLive";
import { publishLiveStatus, resetLiveStatus } from "@/lib/liveStatus";

beforeEach(() => {
  resetLiveStatus();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  resetLiveStatus();
});

/** The store rate-limits to 500 ms, so frames are spaced past that window. */
function pushFrames(temperatures: number[]) {
  for (const ct of temperatures) {
    act(() => {
      publishLiveStatus({ ct, process: { a: 1, e: 1000 } });
      vi.advanceTimersByTime(600);
    });
  }
}

describe("useIsBrewing", () => {
  it("does not re-render while only the telemetry moves", () => {
    // The snapshot is a fresh object every frame, so a component that reads it
    // re-renders twice a second for the length of every shot — on the shots
    // list, a thousand-row table reconciled 2 Hz to decide whether to show a
    // banner. A primitive snapshot lets React bail out instead.
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useIsBrewing();
    });
    // The shot starting is a real change and re-renders once; what must not
    // re-render is the rest of the shot.
    pushFrames([92.0]);
    const settled = renders;

    pushFrames([92.1, 92.4, 92.9, 93.2]);

    expect(renders).toBe(settled);
  });

  it("re-renders on the transition that matters", () => {
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useIsBrewing();
    });
    expect(result.current).toBe(false);
    const initial = renders;

    act(() => publishLiveStatus({ process: { a: 1, e: 0 } }));

    expect(result.current).toBe(true);
    expect(renders).toBeGreaterThan(initial);
  });

  it("still gives the whole frame to whatever draws the numbers", () => {
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLiveStatus();
    });
    const initial = renders;

    pushFrames([92.1, 92.4]);

    expect(result.current.status?.ct).toBe(92.4);
    expect(renders).toBeGreaterThan(initial);
  });
});
