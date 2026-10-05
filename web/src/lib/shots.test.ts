import { describe, expect, it } from "vitest";
import { formatGrams, formatListTime, formatSeconds, formatTime } from "@/lib/shots";

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

describe("formatSeconds and formatGrams", () => {
  it("round an exact tie to the even digit, as the server does", () => {
    // 33.25 s is the shot page's 33.2 s (Python), not `toFixed`'s 33.3.
    expect(formatSeconds(33_250)).toBe("33.2 s");
    expect(formatSeconds(33_750)).toBe("33.8 s");
    expect(formatGrams(0.25)).toBe("0.2 g");
    expect(formatGrams(1.25)).toBe("1.2 g");
    expect(formatGrams(0.75)).toBe("0.8 g");
    expect(formatGrams(32.75)).toBe("32.8 g");
    expect(formatGrams(32.25)).toBe("32.2 g");
  });

  it("round a number that is not an exact tie by its real value, like everything else", () => {
    // 33.35 is a hair above the tie in binary, 36.45 too: both round up in Python as well.
    expect(formatSeconds(33_350)).toBe("33.4 s");
    expect(formatGrams(36.45)).toBe("36.5 g");
    expect(formatSeconds(28_400)).toBe("28.4 s");
    expect(formatGrams(36.4)).toBe("36.4 g");
    expect(formatGrams(0.05)).toBe("0.1 g");
    expect(formatGrams(32.15)).toBe("32.1 g");
  });

  it("keep a trailing zero, a dash for nothing and no thousands separator", () => {
    expect(formatSeconds(30_000)).toBe("30.0 s");
    expect(formatGrams(1234.5)).toBe("1234.5 g");
    expect(formatSeconds(null)).toBe("—");
    expect(formatGrams(undefined)).toBe("—");
  });
});
