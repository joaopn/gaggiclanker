import { describe, expect, it } from "vitest";
import {
  daysOffRoast,
  freshness,
  grindPatch,
  setSummary,
  versionRatio,
  versionSummary,
} from "@/lib/sets";
import { setRow, version } from "@/test/setsFixtures";

const APRIL_8 = new Date("2026-04-08T10:00:00");

describe("daysOffRoast", () => {
  it("counts whole days from local midnight on both sides", () => {
    // Not an elapsed duration: a bag roasted yesterday afternoon is "1 day off
    // roast" all of today, not "0" until the afternoon.
    expect(daysOffRoast("2026-04-01", APRIL_8)).toBe(7);
    expect(daysOffRoast("2026-04-08", APRIL_8)).toBe(0);
  });

  it("says nothing rather than guessing when there is no date", () => {
    expect(daysOffRoast(null, APRIL_8)).toBeNull();
    expect(daysOffRoast("not a date", APRIL_8)).toBeNull();
  });
});

describe("freshness", () => {
  it("bands the windows a dial-in actually behaves differently in", () => {
    expect(freshness(2).label).toContain("resting");
    expect(freshness(7).tone).toBe("good");
    expect(freshness(30).label).toContain("going quiet");
    expect(freshness(90).tone).toBe("bad");
  });

  it("flags a roast date in the future as the typo it is", () => {
    expect(freshness(-1).label).toContain("future");
  });

  it("has nothing to say about a bag with no date", () => {
    expect(freshness(null)).toEqual({ label: "no roast date", tone: "neutral", meaning: "" });
  });
});

describe("recipes", () => {
  it("writes a version on one line", () => {
    expect(versionSummary(version())).toBe("18 g in · 36 g out · grind 22 · 93 °C");
    expect(versionRatio(version())).toBe("1:2.0");
  });

  it("says so when a version records nothing yet", () => {
    expect(
      versionSummary(
        version({
          dose_g: null,
          target_yield_g: null,
          grind_setting: null,
          target_temperature_c: null,
        }),
      ),
    ).toBe("nothing recorded yet");
    expect(versionRatio(version({ dose_g: null }))).toBeNull();
  });

  it("identifies a Set as bean · grinder · profile vN", () => {
    expect(setSummary(setRow())).toBe("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
    expect(setSummary(setRow({ grinder_name: null, profile_label: null }))).toBe(
      "Ethiopia Guji · v2",
    );
  });
});

describe("grindPatch", () => {
  it("sends the number with the text, so the trend line has something to plot", () => {
    expect(grindPatch("22")).toEqual({ grind_setting: "22", grind_value: 22 });
    expect(grindPatch(" 3.5 ")).toEqual({ grind_setting: "3.5", grind_value: 3.5 });
  });

  it("keeps a reading that is not a number, without inventing one", () => {
    // A Mazzer says "between 3 and 4". The text is what a person reads back;
    // there is simply no number to plot.
    expect(grindPatch("between 3 and 4")).toEqual({ grind_setting: "between 3 and 4" });
  });

  it("sends nothing at all for a blank reading", () => {
    // Not `{grind_setting: ""}`: on a version patch an omitted field inherits
    // and an empty one would record "cleared" as a change.
    expect(grindPatch("")).toEqual({});
    expect(grindPatch(null)).toEqual({});
    expect(grindPatch(undefined)).toEqual({});
  });
});
