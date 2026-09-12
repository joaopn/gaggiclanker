import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

/**
 * jsdom is missing three things every component here touches.
 *
 * `matchMedia` is used by the theme store; without it the store's try/catch
 * swallows the failure and every test silently runs in light mode, which hides
 * exactly the bug the theme tests exist to catch.
 *
 * `ResizeObserver` is what radix's popper uses to position a Select or a
 * Tooltip. Missing, it throws inside a portal and the failure surfaces three
 * components away from the cause.
 *
 * `localStorage` exists in modern jsdom but not in every environment this may
 * run in (a `--environment node` file, a worker), so it is polyfilled when the
 * shape is wrong rather than assumed.
 */

class MockResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = MockResizeObserver;
}

// radix's Select calls these on its trigger; jsdom implements neither.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

type MatchMediaListener = (event: MediaQueryListEvent) => void;

/** Per-test control over `prefers-color-scheme`, used by the theme tests. */
export const matchMediaState = { matches: false };
const matchMediaListeners = new Set<MatchMediaListener>();

export function setSystemPrefersDark(matches: boolean): void {
  matchMediaState.matches = matches;
  for (const listener of matchMediaListeners) {
    listener({ matches } as MediaQueryListEvent);
  }
}

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn((query: string) => ({
      media: query,
      get matches() {
        return matchMediaState.matches;
      },
      onchange: null,
      addEventListener: (_event: string, listener: MatchMediaListener) => {
        matchMediaListeners.add(listener);
      },
      removeEventListener: (_event: string, listener: MatchMediaListener) => {
        matchMediaListeners.delete(listener);
      },
      addListener: (listener: MatchMediaListener) => matchMediaListeners.add(listener),
      removeListener: (listener: MatchMediaListener) => matchMediaListeners.delete(listener),
      dispatchEvent: () => false,
    })),
  });
}

function hasStorageShape(value: unknown): value is Storage {
  if (!value || typeof value !== "object") return false;
  const storage = value as Partial<Storage>;
  return (
    typeof storage.getItem === "function" &&
    typeof storage.setItem === "function" &&
    typeof storage.removeItem === "function" &&
    typeof storage.clear === "function"
  );
}

if (!hasStorageShape(globalThis.localStorage)) {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => void store.delete(key),
    setItem: (key: string, value: string) => void store.set(key, value),
  };
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    writable: true,
    value: storage,
  });
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  matchMediaState.matches = false;
  document.documentElement.className = "";
  document.documentElement.removeAttribute("data-theme");
});
