import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getLiveSnapshot,
  isBrewing,
  MIN_NOTIFY_MS,
  publishLiveConnection,
  publishLiveStatus,
  resetLiveStatus,
  subscribeLiveStatus,
} from "@/lib/liveStatus";

beforeEach(() => {
  resetLiveStatus();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  resetLiveStatus();
});

describe("the live status store", () => {
  it("holds the newest frame", () => {
    publishLiveStatus({ ct: 92.5 });

    expect(getLiveSnapshot().status?.ct).toBe(92.5);
    expect(getLiveSnapshot().connected).toBe(true);
  });

  it("notifies at most twice a second, and never with a stale frame", () => {
    // The device pushes every 500 ms today and may push faster tomorrow;
    // nothing in a React tree wants to re-render at 10 Hz. A burst has to
    // collapse to its *last* value, not its first.
    const listener = vi.fn();
    subscribeLiveStatus(listener);

    publishLiveStatus({ ct: 90 });
    expect(listener).toHaveBeenCalledTimes(1);

    publishLiveStatus({ ct: 91 });
    publishLiveStatus({ ct: 92 });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getLiveSnapshot().status?.ct).toBe(90);

    vi.advanceTimersByTime(MIN_NOTIFY_MS);
    expect(listener).toHaveBeenCalledTimes(2);
    expect(getLiveSnapshot().status?.ct).toBe(92);
  });

  it("publishes a shot starting immediately, rate limit or not", () => {
    // Half a second is a long time to wait to be told a shot has begun: the
    // banner and the live page both hang off this transition.
    const listener = vi.fn();
    publishLiveStatus({ ct: 90 });
    subscribeLiveStatus(listener);

    publishLiveStatus({ process: { a: 1, l: "Infusion", e: 0 } });

    expect(listener).toHaveBeenCalledTimes(1);
    expect(isBrewing(getLiveSnapshot())).toBe(true);
  });

  it("drops the last frame when the machine goes away", () => {
    // A disconnected machine's last reading is history, and leaving it on
    // screen means a dead boiler showing 93 °C for ever.
    publishLiveStatus({ ct: 93, process: { a: 1 } });
    publishLiveConnection({ connected: false, configured: true, reason: "socket closed" });

    expect(getLiveSnapshot().status).toBeNull();
    expect(isBrewing(getLiveSnapshot())).toBe(false);
  });

  it("separates 'no machine configured' from 'not connected'", () => {
    publishLiveConnection({ connected: false, configured: false });

    expect(getLiveSnapshot().configured).toBe(false);
  });
});
