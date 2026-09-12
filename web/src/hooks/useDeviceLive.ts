import { useSyncExternalStore } from "react";
import type { DeviceConnectionEvent, LiveStatus } from "@/api/types";
import { useSse } from "@/hooks/useSse";
import {
  getLiveSnapshot,
  isBrewing,
  type LiveSnapshot,
  publishLiveConnection,
  publishLiveStatus,
  subscribeLiveStatus,
} from "@/lib/liveStatus";

/**
 * The single writer into the live-status store. Mounted once, by `App`.
 *
 * Separate from `useEventInvalidation` because the two streams mean different
 * things: `device.live` carries the value itself and is read straight off the
 * wire, while `device.connection` is a nudge to re-read `/api/device/status`.
 * The invalidation hook handles the second; this handles the first, on the same
 * subscription, so the tab holds one connection rather than two.
 */
export function useDeviceLiveStream(url: string | null): { connected: boolean } {
  return useSse<unknown>(url, (message) => {
    if (message.event === "device.live") {
      publishLiveStatus(message.data as LiveStatus);
    } else if (message.event === "device.connection") {
      publishLiveConnection(message.data as DeviceConnectionEvent);
    }
  });
}

/**
 * The latest frame. Re-renders at most twice a second — see `lib/liveStatus`.
 *
 * Only for a component that draws the numbers. The snapshot is a fresh object
 * per frame, so every caller re-renders on every frame whether it reads
 * anything that changed or not.
 */
export function useLiveStatus(): LiveSnapshot {
  return useSyncExternalStore(subscribeLiveStatus, getLiveSnapshot, getLiveSnapshot);
}

/**
 * Whether a shot is running, and nothing else.
 *
 * A **primitive** snapshot, which is the whole point: React bails out of the
 * re-render when `useSyncExternalStore` returns the same value, so a page that
 * only wants to know whether to show a banner stops re-rendering at 2 Hz. On
 * the shots list that was a thousand-row table being reconciled twice a second
 * for the length of every shot.
 */
export function useIsBrewing(): boolean {
  return useSyncExternalStore(
    subscribeLiveStatus,
    () => isBrewing(getLiveSnapshot()),
    () => false,
  );
}
