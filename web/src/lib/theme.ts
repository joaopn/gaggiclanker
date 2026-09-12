import { useSyncExternalStore } from "react";

// Mirrored by the inline FOUC script in index.html — the storage key and the
// two class/attribute stamps must stay in lockstep. theme.test.ts pins both
// sides so a change to one fails the build rather than showing up as a flash
// of the wrong palette on the next deploy.
export const THEME_STORAGE_KEY = "gaggiclanker.theme";

export type ThemePreference = "system" | "light" | "dark";

/** The palette ids that have a block in index.css. One light, one dark. */
export const LIGHT_THEME_ID = "crema";
export const DARK_THEME_ID = "roast";

const listeners = new Set<() => void>();
let attachedSystemQuery: MediaQueryList | null = null;

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    // jsdom without a matchMedia polyfill, or a very old browser.
    return false;
  }
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Private mode or blocked storage: the choice simply does not persist.
    return null;
  }
}

export function getThemePreference(): ThemePreference {
  const stored = readStored();
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function resolveIsDark(preference: ThemePreference): boolean {
  if (preference === "dark") return true;
  if (preference === "light") return false;
  return systemPrefersDark();
}

export function resolveActiveThemeId(): string {
  return resolveIsDark(getThemePreference()) ? DARK_THEME_ID : LIGHT_THEME_ID;
}

// Stamps BOTH hooks on every branch: the .dark class (which drives
// @custom-variant dark and every dark: utility) and data-theme (which selects
// the palette). Stamping one without the other leaves dark-variant styling
// sitting on top of the light palette, which looks like a rendering bug and is
// really a one-line omission.
export function applyThemeClass(): void {
  const root = document.documentElement;
  const dark = resolveIsDark(getThemePreference());
  root.classList.toggle("dark", dark);
  root.setAttribute("data-theme", dark ? DARK_THEME_ID : LIGHT_THEME_ID);
}

export function setThemePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // Storage unavailable — still restamp below so the click does something.
  }
  applyThemeClass();
  notifyListeners();
}

/** Cycle light -> dark -> system, which is what the header button does. */
export function nextPreference(current: ThemePreference): ThemePreference {
  if (current === "light") return "dark";
  if (current === "dark") return "system";
  return "light";
}

function notifyListeners(): void {
  for (const listener of listeners) listener();
}

function handleSystemSchemeChange(): void {
  if (getThemePreference() === "system") applyThemeClass();
  notifyListeners();
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    try {
      attachedSystemQuery = window.matchMedia("(prefers-color-scheme: dark)");
      attachedSystemQuery.addEventListener("change", handleSystemSchemeChange);
    } catch {
      attachedSystemQuery = null;
    }
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      attachedSystemQuery?.removeEventListener("change", handleSystemSchemeChange);
      attachedSystemQuery = null;
    }
  };
}

export function useTheme(): {
  preference: ThemePreference;
  isDark: boolean;
  setPreference: (preference: ThemePreference) => void;
  toggle: () => void;
} {
  // Every subscribed snapshot must be a PRIMITIVE: useSyncExternalStore
  // re-renders forever if getSnapshot returns a fresh object each call.
  const preference = useSyncExternalStore(subscribe, getThemePreference, () => "system" as const);
  const isDark = useSyncExternalStore(
    subscribe,
    () => resolveIsDark(getThemePreference()),
    () => false,
  );
  return {
    preference,
    isDark,
    setPreference: setThemePreference,
    toggle: () => setThemePreference(nextPreference(getThemePreference())),
  };
}
