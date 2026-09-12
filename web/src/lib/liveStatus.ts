import type { DeviceConnectionEvent, LiveStatus } from "@/api/types";

/**
 * The machine's live state, held once for the whole tab.
 *
 * `/api/device/live` is the one stream that carries a payload worth reading
 * rather than a "go and re-read" nudge, and it arrives twice a second. Two
 * things follow, and this module exists for both:
 *
 * 1. **One subscription.** The header pill, the list banner and the live page
 *    all want the same frames. A hook that opened its own `EventSource` per
 *    consumer would hold three sockets against a device whose own limit is
 *    three *WebSocket* clients — and would re-merge the same frame three times.
 * 2. **A rate limit that is ours, not the firmware's.** The device pushes at
 *    500 ms today; a future one may push faster, and nothing about a React tree
 *    wants to re-render at 10 Hz. Listeners are notified at most every
 *    `MIN_NOTIFY_MS`, with the newest frame always kept — so a consumer never
 *    sees a stale value, only fewer of them.
 *
 * Read it with `useLiveStatus()`; `App` is the only writer.
 */

export type LiveSnapshot = {
  status: LiveStatus | null;
  connected: boolean;
  configured: boolean;
  /** `Date.now()` of the frame in `status`, so a consumer can age it out. */
  receivedAt: number;
};

/** 2 Hz. The device's own telemetry rate, and fast enough for a brew clock. */
export const MIN_NOTIFY_MS = 500;

const EMPTY: LiveSnapshot = { status: null, connected: false, configured: true, receivedAt: 0 };

let snapshot: LiveSnapshot = EMPTY;
let pending: LiveSnapshot | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let lastNotifiedAt = 0;
const listeners = new Set<() => void>();

function notify(): void {
  lastNotifiedAt = Date.now();
  for (const listener of listeners) listener();
}

function commit(next: LiveSnapshot, immediate: boolean): void {
  const elapsed = Date.now() - lastNotifiedAt;
  if (immediate || elapsed >= MIN_NOTIFY_MS) {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    pending = null;
    snapshot = next;
    notify();
    return;
  }
  // Too soon: keep the frame, publish it when the window opens. The *newest*
  // frame wins, so a burst collapses to its last value rather than replaying.
  pending = next;
  if (!timer) {
    timer = setTimeout(() => {
      timer = null;
      if (pending) {
        snapshot = pending;
        pending = null;
        notify();
      }
    }, MIN_NOTIFY_MS - elapsed);
  }
}

/** A `device.live` frame. Rate-limited; the newest one always wins. */
export function publishLiveStatus(status: LiveStatus): void {
  const wasActive = snapshot.status?.process?.a === 1;
  const isActive = status.process?.a === 1;
  commit(
    { status, connected: true, configured: true, receivedAt: Date.now() },
    // A shot starting or ending is the one transition nobody should wait half
    // a second to see: it swaps a banner in and a live page on.
    wasActive !== isActive,
  );
}

/** A `device.connection` frame. Rare, so never delayed. */
export function publishLiveConnection(event: DeviceConnectionEvent): void {
  commit(
    {
      // A disconnected machine's last frame is history, not telemetry. Keeping
      // it would leave a dead boiler reading 93 °C on screen for ever.
      status: event.connected ? snapshot.status : null,
      connected: Boolean(event.connected),
      configured: event.configured !== false,
      receivedAt: event.connected ? snapshot.receivedAt : 0,
    },
    true,
  );
}

export function getLiveSnapshot(): LiveSnapshot {
  return snapshot;
}

export function subscribeLiveStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test seam: module-level state would otherwise leak between tests. */
export function resetLiveStatus(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  pending = null;
  lastNotifiedAt = 0;
  snapshot = EMPTY;
  listeners.clear();
}

/** Whether a shot is running right now. `process.a` is the firmware's flag. */
export function isBrewing(snapshotToCheck: LiveSnapshot): boolean {
  return snapshotToCheck.connected && snapshotToCheck.status?.process?.a === 1;
}
