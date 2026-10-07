import { useCallback, useSyncExternalStore } from "react";

/**
 * Which of a shot's boxes are open, remembered per browser.
 *
 * The shot page and the shots list's open row show the same boxes in the same order
 * (`ShotBoxes`): **Your judgement** (open by default), **Curves**, **Review** and **Curve check**
 * (closed by default; the version's prediction sits between Your judgement and Curves and is not
 * a box that folds). One choice serves both places and every shot: a person who keeps Curves
 * open reads a shot the same way wherever they open it.
 *
 * A view preference, like the shots table's columns: nothing on the server depends on it, so it
 * lives in `localStorage` under one versioned key, every read and write wrapped in try/catch, and
 * the page renders with the defaults when storage cannot be used (a private window, blocked site
 * data) — then the choice lasts as long as the tab. It is read from storage whenever it is asked
 * for rather than copied once, so a second tab's choice and a cleared storage are both seen.
 */

export type BoxId = "judgement" | "curves" | "review" | "check";

export const BOXES_KEY = "shots.boxes.v1";

export const DEFAULT_BOXES: Readonly<Record<BoxId, boolean>> = {
  judgement: true,
  curves: false,
  review: false,
  check: false,
};

const IDS = Object.keys(DEFAULT_BOXES) as BoxId[];

/** The stored choice over the defaults: only a boolean for a box that exists is believed. */
export function parseBoxes(raw: string | null): Readonly<Record<BoxId, boolean>> {
  if (!raw) return DEFAULT_BOXES;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return DEFAULT_BOXES;
    }
    const stored = parsed as Record<string, unknown>;
    const next = { ...DEFAULT_BOXES };
    for (const id of IDS) {
      if (typeof stored[id] === "boolean") next[id] = stored[id] as boolean;
    }
    return next;
  } catch {
    return DEFAULT_BOXES;
  }
}

// What storage said last, and what that came to: `useSyncExternalStore` needs one value per state.
let cachedRaw: string | null | undefined;
let cachedBoxes: Readonly<Record<BoxId, boolean>> = DEFAULT_BOXES;
// Set once a write has failed: the choice then lives here, for the life of the tab.
let memory: string | null | undefined;

const listeners = new Set<() => void>();

function readRaw(): string | null {
  if (memory !== undefined) return memory;
  try {
    return window.localStorage.getItem(BOXES_KEY);
  } catch {
    return null;
  }
}

function snapshot(): Readonly<Record<BoxId, boolean>> {
  const raw = readRaw();
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedBoxes = parseBoxes(raw);
  }
  return cachedBoxes;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab's choice.
  const onStorage = (event: StorageEvent) => {
    if (event.key === BOXES_KEY || event.key === null) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Opens or folds one box, for every place that shows it. */
export function setBox(id: BoxId, open: boolean): void {
  const current = snapshot();
  if (current[id] === open) return;
  const next = JSON.stringify({ ...current, [id]: open });
  try {
    window.localStorage.setItem(BOXES_KEY, next);
    memory = undefined;
  } catch {
    // Not remembered across a reload, but the boxes still obey for this tab.
    memory = next;
  }
  for (const listener of listeners) listener();
}

/** Forgets what this module knows about storage: for a test that clears it between cases. */
export function forgetBoxes(): void {
  cachedRaw = undefined;
  cachedBoxes = DEFAULT_BOXES;
  memory = undefined;
}

/** Whether one box is open, and the way to change that; every box that shows it follows. */
export function useBox(id: BoxId): [boolean, (open: boolean) => void] {
  const open = useSyncExternalStore(
    subscribe,
    () => snapshot()[id],
    () => DEFAULT_BOXES[id],
  );
  const change = useCallback((next: boolean) => setBox(id, next), [id]);
  return [open, change];
}
