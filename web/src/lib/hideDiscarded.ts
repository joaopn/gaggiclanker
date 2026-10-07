/**
 * The Shots page's Hide discarded tickbox, remembered in this browser.
 *
 * In `localStorage` like the columns, not in the query string like the
 * filters: it is how this reader likes the list, every visit, and a fresh visit
 * that forgot an untick would be the list changing under them (the maintainer's
 * call, 2026-10-07: "always remember in the browser"). Ticked until somebody
 * unticks it. Every storage access is guarded: a private window or blocked
 * site data falls back to ticked, and a failed save only means tomorrow starts
 * ticked again.
 */
export const HIDE_DISCARDED_KEY = "shots.hideDiscarded.v1";

export function loadHideDiscarded(storage: Storage | undefined = safeStorage()): boolean {
  try {
    return storage?.getItem(HIDE_DISCARDED_KEY) !== "0";
  } catch {
    return true;
  }
}

export function saveHideDiscarded(
  hide: boolean,
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(HIDE_DISCARDED_KEY, hide ? "1" : "0");
  } catch {
    // A view preference is not worth a toast.
  }
}

/** `localStorage`, or nothing — reading the property itself can throw. */
function safeStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}
