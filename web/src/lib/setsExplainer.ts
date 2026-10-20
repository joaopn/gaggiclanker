/**
 * Whether the Sets page's "How a Set works" block is open, remembered in this
 * browser once somebody opens or closes it by hand.
 *
 * Until then the page decides: open while nothing is being dialled in (no Set
 * that is not archived), when the idea is new, and closed once there is one.
 * A choice made by hand wins from then on, in either direction. Every storage
 * access is guarded: a private window or blocked site data simply means the
 * page decides every visit.
 */
export const RECIPES_EXPLAINER_KEY = "sets.explainer.v1";

/** The stored choice, or `null` when nobody has made one (or it is unreadable). */
export function loadExplainerOpen(storage: Storage | undefined = safeStorage()): boolean | null {
  try {
    const value = storage?.getItem(RECIPES_EXPLAINER_KEY);
    if (value === "1") return true;
    if (value === "0") return false;
    return null;
  } catch {
    return null;
  }
}

export function saveExplainerOpen(
  open: boolean,
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(RECIPES_EXPLAINER_KEY, open ? "1" : "0");
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
