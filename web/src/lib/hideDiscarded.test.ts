import { describe, expect, it } from "vitest";
import { HIDE_DISCARDED_KEY, loadHideDiscarded, saveHideDiscarded } from "@/lib/hideDiscarded";

function memory(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, value),
  };
}

describe("Hide discarded in this browser", () => {
  it("is ticked until somebody unticks it, and remembers either answer", () => {
    const storage = memory();
    expect(loadHideDiscarded(storage)).toBe(true);
    saveHideDiscarded(false, storage);
    expect(storage.getItem(HIDE_DISCARDED_KEY)).toBe("0");
    expect(loadHideDiscarded(storage)).toBe(false);
    saveHideDiscarded(true, storage);
    expect(loadHideDiscarded(storage)).toBe(true);
  });

  it("falls back to ticked when storage throws or is missing", () => {
    const broken = {
      ...memory(),
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    } as Storage;
    expect(loadHideDiscarded(broken)).toBe(true);
    expect(() => saveHideDiscarded(false, broken)).not.toThrow();
    expect(loadHideDiscarded(undefined)).toBe(true);
  });
});
