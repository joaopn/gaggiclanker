import { describe, expect, it } from "vitest";
import {
  curveColour,
  DEFAULT_CURVES,
  loadShotCurves,
  SHOT_CURVES_KEY,
  saveShotCurves,
} from "@/lib/shotCurves";

function storageWith(value: string | null): Storage {
  const store = new Map<string, string>();
  if (value !== null) store.set(SHOT_CURVES_KEY, value);
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, v: string) => void store.set(key, v),
  } as unknown as Storage;
}

const throwing = {
  getItem() {
    throw new Error("blocked");
  },
  setItem() {
    throw new Error("blocked");
  },
} as unknown as Storage;

const stored = (value: unknown) => storageWith(JSON.stringify(value));

describe("loadShotCurves", () => {
  it("is pressure and puck flow in chart-1 and chart-2 when nothing is stored", () => {
    const choice = loadShotCurves(storageWith(null));
    expect(choice.shown).toEqual(["pressure", "puckFlow"]);
    expect(curveColour(choice, "pressure")).toBe("--chart-1");
    expect(curveColour(choice, "puckFlow")).toBe("--chart-2");
  });

  it("falls back to the default when storage throws", () => {
    expect(loadShotCurves(throwing)).toEqual(DEFAULT_CURVES);
  });

  it("falls back to the default for garbage and for a non-object", () => {
    for (const raw of ["{nope", "[]", '"pressure"', "42", "null"]) {
      expect(loadShotCurves(storageWith(raw))).toEqual(DEFAULT_CURVES);
    }
  });

  it("gives the default shown set when shown is not an array, empty or all unknown", () => {
    for (const shown of ["pressure", 3, null, [], ["bogus"], [1, null]]) {
      expect(loadShotCurves(stored({ shown, colors: { weight: "--chart-4" } })).shown).toEqual(
        DEFAULT_CURVES.shown,
      );
    }
    expect(loadShotCurves(stored({ colors: {} })).shown).toEqual(DEFAULT_CURVES.shown);
  });

  it("drops series keys it does not know, and repeats", () => {
    const choice = loadShotCurves(stored({ shown: ["weight", "bogus", "weight"], colors: {} }));
    expect(choice.shown).toEqual(["weight"]);
  });

  it("falls back per series for colours that are not an object, unknown keys and unknown tokens", () => {
    const choice = loadShotCurves(
      stored({
        shown: ["pressure", "targetFlow", "weight"],
        colors: { pressure: "#ff0000", targetFlow: "red", weight: 3, bogus: "--chart-1" },
      }),
    );
    expect(choice.colors).toEqual({});
    expect(curveColour(choice, "pressure")).toBe("--chart-1");
    expect(curveColour(choice, "targetFlow")).toBe("--chart-5");
    expect(curveColour(choice, "weight")).toBe("--chart-4");

    for (const colors of ["--chart-1", ["--chart-1"], null, 7]) {
      const bad = loadShotCurves(stored({ shown: ["weight"], colors }));
      expect(bad.shown).toEqual(["weight"]);
      expect(curveColour(bad, "weight")).toBe("--chart-4");
    }
  });

  it("never defaults flow to the colour puck flow has", () => {
    const choice = loadShotCurves(storageWith(null));
    expect(curveColour(choice, "puckFlow")).toBe("--chart-2");
    expect(curveColour(choice, "flow")).toBe("--chart-5");
    expect(curveColour(choice, "targetFlow")).toBe("--chart-5");
    expect(curveColour(choice, "targetPressure")).toBe("--chart-1");
  });

  it("keeps a colour chosen for a series that is not shown", () => {
    const choice = loadShotCurves(
      stored({ shown: ["pressure"], colors: { weight: "--foreground" } }),
    );
    expect(curveColour(choice, "weight")).toBe("--foreground");
  });

  it("round-trips a saved choice", () => {
    const storage = storageWith(null);
    const choice = {
      shown: ["temperature", "pressure"],
      colors: { pressure: "--chart-5", weight: "--foreground" },
    };
    saveShotCurves(choice, storage);
    expect(loadShotCurves(storage)).toEqual(choice);
  });
});

describe("saveShotCurves", () => {
  it("never throws, whatever storage does", () => {
    expect(() => saveShotCurves(DEFAULT_CURVES, throwing)).not.toThrow();
    expect(() => saveShotCurves(DEFAULT_CURVES, undefined)).not.toThrow();
  });
});
