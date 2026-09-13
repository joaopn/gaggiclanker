import { describe, expect, it } from "vitest";
import { formatListTime, formatTime } from "@/lib/shots";

describe("formatListTime", () => {
  // Local wall-clock dates, so the assertions hold in whatever zone the suite
  // runs in. The output is the runner's locale; the tests assert what the
  // format keeps and folds, not one locale's spelling.
  const now = new Date(2026, 8, 13, 12, 0);

  it("shows this year's shot with its day, month and time, and no year", () => {
    const value = new Date(2026, 2, 4, 8, 47).toISOString();
    const text = formatListTime(value, now);
    expect(text).toContain("47");
    expect(text).not.toContain("2026");
    // Shorter than the long form it replaces, which is the point of it.
    expect(text.length).toBeLessThan(formatTime(value).length);
  });

  it("shows an older shot with its year and without the minute", () => {
    const value = new Date(2025, 11, 24, 23, 47).toISOString();
    const text = formatListTime(value, now);
    expect(text).toContain("2025");
    expect(text).not.toContain("47");
  });

  it("says so when the machine had no clock", () => {
    expect(formatListTime(null, now)).toBe("no clock");
  });
});
