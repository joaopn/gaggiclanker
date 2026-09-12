import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import {
  applyThemeClass,
  DARK_THEME_ID,
  getThemePreference,
  LIGHT_THEME_ID,
  nextPreference,
  resolveIsDark,
  setThemePreference,
  THEME_STORAGE_KEY,
} from "@/lib/theme";
import { setSystemPrefersDark } from "@/setupTests";

describe("theme store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setSystemPrefersDark(false);
  });

  it("defaults to the system preference", () => {
    expect(getThemePreference()).toBe("system");
    expect(resolveIsDark("system")).toBe(false);
    setSystemPrefersDark(true);
    expect(resolveIsDark("system")).toBe(true);
  });

  it("stamps both hooks: a .dark class without data-theme is dark styling on a light palette", () => {
    setThemePreference("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.getAttribute("data-theme")).toBe(DARK_THEME_ID);

    setThemePreference("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.getAttribute("data-theme")).toBe(LIGHT_THEME_ID);
  });

  it("removes the key for system rather than storing the word", () => {
    setThemePreference("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    setThemePreference("system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("follows the system scheme while the preference is system", () => {
    setSystemPrefersDark(true);
    applyThemeClass();
    expect(document.documentElement.getAttribute("data-theme")).toBe(DARK_THEME_ID);
  });

  it("cycles light -> dark -> system", () => {
    expect(nextPreference("light")).toBe("dark");
    expect(nextPreference("dark")).toBe("system");
    expect(nextPreference("system")).toBe("light");
  });
});

describe("the pre-paint script in index.html", () => {
  // The FOUC script cannot import this module — it runs before any bundle — so
  // it duplicates the storage key and the palette ids. This pins the copy: a
  // rename here must fail the build rather than show up as a flash of the
  // wrong palette on the next deploy.
  const html = readFileSync(path.resolve(__dirname, "../../index.html"), "utf8");

  it("uses the same storage key", () => {
    expect(html).toContain(`localStorage.getItem("${THEME_STORAGE_KEY}")`);
  });

  it("uses the same palette ids", () => {
    expect(html).toContain(`"${DARK_THEME_ID}"`);
    expect(html).toContain(`"${LIGHT_THEME_ID}"`);
  });

  it("stamps the class and the attribute, like applyThemeClass does", () => {
    expect(html).toContain('classList.toggle("dark", dark)');
    expect(html).toContain('setAttribute("data-theme"');
  });

  it("falls back to prefers-color-scheme", () => {
    expect(html).toContain("prefers-color-scheme: dark");
  });
});

describe("index.css palettes", () => {
  const css = readFileSync(path.resolve(__dirname, "../index.css"), "utf8");

  it("defines a block for each palette id the store can stamp", () => {
    // The light palette lives in bare :root; the dark one in its data-theme block.
    expect(css).toContain(`:root[data-theme="${DARK_THEME_ID}"]`);
    expect(css).toMatch(/^:root \{/m);
  });

  it("declares color-scheme on both, so form controls and scrollbars follow", () => {
    expect(css).toContain("color-scheme: light");
    expect(css).toContain("color-scheme: dark");
  });
});
