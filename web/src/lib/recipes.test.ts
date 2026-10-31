import { describe, expect, it } from "vitest";
import {
  beanFieldSuggestions,
  beanScaleLabels,
  evidenceSideSummary,
  grindPatch,
  labelSummary,
  recipeSummary,
  versionRatio,
  versionSummary,
} from "@/lib/recipes";
import { bean, labelCounts, recipeRow, version } from "@/test/recipesFixtures";

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
          profile_temperature_c: null,
        }),
      ),
    ).toBe("nothing recorded yet");
    expect(versionRatio(version({ dose_g: null }))).toBeNull();
  });

  it("identifies a Recipe as bean · grinder · profile vN", () => {
    expect(recipeSummary(recipeRow())).toBe("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
    expect(recipeSummary(recipeRow({ grinder_name: null, profile_label: null }))).toBe(
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

describe("beanScaleLabels", () => {
  it("labels the scales that are set, in order, and leaves out the rest", () => {
    expect(beanScaleLabels(bean({ sweetness: 2, acidity: 5 }))).toEqual([
      "acidity 5/5",
      "sweetness 2/5",
    ]);
    expect(beanScaleLabels(bean())).toEqual([]);
  });
});

describe("beanFieldSuggestions", () => {
  it("offers each roaster once, sorted, archived beans included", () => {
    const beans = [
      bean({ roaster: "Square Mile" }),
      bean({ roaster: "hasbean" }),
      bean({ roaster: "  Square Mile " }),
      bean({ roaster: "square mile", archived: true }),
      bean({ roaster: "Assembly", archived: true }),
      bean({ roaster: null }),
      bean({ roaster: "   " }),
    ];
    expect(beanFieldSuggestions(beans, "roaster")).toEqual(["Assembly", "hasbean", "Square Mile"]);
  });

  it("keeps the most common spelling, and the first one alphabetically on a tie", () => {
    expect(
      beanFieldSuggestions(
        [bean({ origin: "kenya" }), bean({ origin: "Kenya" }), bean({ origin: "kenya" })],
        "origin",
      ),
    ).toEqual(["kenya"]);
    expect(
      beanFieldSuggestions([bean({ origin: "kenya" }), bean({ origin: "Kenya" })], "origin"),
    ).toEqual(["Kenya"]);
    expect(
      beanFieldSuggestions([bean({ origin: "Kenya" }), bean({ origin: "kenya" })], "origin"),
    ).toEqual(["Kenya"]);
  });

  it("reads the field it is asked for", () => {
    const beans = [bean({ roaster: "Hasbean", origin: "Ethiopia" })];
    expect(beanFieldSuggestions(beans, "origin")).toEqual(["Ethiopia"]);
    expect(beanFieldSuggestions([], "origin")).toEqual([]);
  });
});

describe("labelSummary", () => {
  it("reads every part, in order, with a Discarded count and the unrated last", () => {
    expect(
      labelSummary(labelCounts({ good: 2, neutral: 1, bad: 1, discarded: 1, unrated: 3 })),
    ).toBe("2 Good · 1 Neutral · 1 Bad · 1 Discarded · 3 not rated");
  });

  it("leaves out each part that is zero, one at a time", () => {
    const full = { good: 1, neutral: 1, bad: 1, discarded: 1, unrated: 1 };
    expect(labelSummary(labelCounts({ ...full, good: 0 }))).toBe(
      "1 Neutral · 1 Bad · 1 Discarded · 1 not rated",
    );
    expect(labelSummary(labelCounts({ ...full, neutral: 0 }))).toBe(
      "1 Good · 1 Bad · 1 Discarded · 1 not rated",
    );
    expect(labelSummary(labelCounts({ ...full, bad: 0 }))).toBe(
      "1 Good · 1 Neutral · 1 Discarded · 1 not rated",
    );
    expect(labelSummary(labelCounts({ ...full, discarded: 0 }))).toBe(
      "1 Good · 1 Neutral · 1 Bad · 1 not rated",
    );
    expect(labelSummary(labelCounts())).toBe("");
  });
});

describe("evidenceSideSummary", () => {
  const side = {
    version_id: 1,
    version_label: "v1",
    shots: 4,
    sour: 0,
    balanced: 0,
    bitter: 0,
    good: 0,
    neutral: 0,
    bad: 0,
    unrated: 0,
  };

  it("says Neutral and not rated, and has no Discarded part (a discarded shot is not counted)", () => {
    expect(evidenceSideSummary({ ...side, good: 1, neutral: 1, bad: 1, unrated: 1 })).toBe(
      "4 shots · 1 Good · 1 Neutral · 1 Bad · 1 not rated",
    );
    expect(evidenceSideSummary({ ...side, shots: 1, neutral: 1 })).toBe("1 shot · 1 Neutral");
    expect(evidenceSideSummary(side)).not.toContain("Discarded");
  });
});
