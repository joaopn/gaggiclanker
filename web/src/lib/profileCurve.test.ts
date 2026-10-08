import { describe, expect, it } from "vitest";
import { buildPhaseRanges, hasCurve, prepareData, profileCurve } from "@/lib/profileCurve";
import golden from "@/lib/profileCurve.golden.json";

type Point = [number, number | null, number];
type Case = {
  profile: { type: string; phases: Record<string, unknown>[] };
  pressure: Point[];
  flow: Point[];
  ranges: { start: number; end: number }[];
  xMax: number;
};
const cases = golden.cases as unknown as Record<string, Case>;

const same = (got: number, want: number | null) =>
  want === null ? Number.isNaN(got) : Object.is(got, want) || got === want;

describe("the curve against the firmware's own code", () => {
  it("has the cases that matter", () => {
    const names = Object.keys(cases);
    for (const kind of ["instant", "linear", "ease-in", "ease-out", "ease-in-out"]) {
      expect(names).toContain(`transition-${kind}`);
    }
    expect(names).toEqual(
      expect.arrayContaining([
        "carry-over-pressure",
        "carry-over-flow",
        "transition-duration-zero",
        "zero-duration-phase",
        "single-phase",
        "empty-phase-list",
      ]),
    );
    expect(names.filter((name) => name.startsWith("fixture-")).length).toBeGreaterThanOrEqual(4);
  });

  for (const [name, want] of Object.entries(cases)) {
    it(`draws ${name} to the float`, () => {
      const curve = profileCurve(want.profile);
      expect(curve).not.toBeNull();
      if (!curve) return;
      for (const series of ["pressure", "flow"] as const) {
        const got = curve[series];
        expect(got.length).toBe(want[series].length);
        got.forEach((point, index) => {
          const [x, y, target] = want[series][index];
          if (!same(point.x, x) || !same(point.y, y) || point.target !== (target === 1)) {
            throw new Error(
              `${name} ${series}[${index}]: got ${point.x},${point.y},${point.target} want ${x},${y},${target}`,
            );
          }
        });
      }
      expect(curve.ranges.map(({ start, end }) => ({ start, end }))).toEqual(want.ranges);
      expect(curve.xMax).toBe(want.xMax);
    });
  }
});

describe("profileCurve", () => {
  it("draws nothing for a profile the machine draws nothing for", () => {
    expect(profileCurve({ type: "standard", phases: [{ duration: 5 }] })).toBeNull();
    expect(profileCurve({ phases: [] })).toBeNull();
    expect(profileCurve(null)).toBeNull();
    expect(hasCurve({ type: "pro" })).toBe(true);
    expect(hasCurve({ type: "standard" })).toBe(false);
  });

  it("starts every curve from 0, every 0.1 s, and ends where the phases end", () => {
    const curve = profileCurve({
      type: "pro",
      phases: [{ duration: 2, pump: { target: "pressure", pressure: 9, flow: 0 } }],
    });
    expect(curve?.pressure[0]).toEqual({ x: 0, y: 0, target: true });
    expect(curve?.pressure.length).toBe(20);
    expect(curve?.flow[0].target).toBe(false);
    expect(curve?.xMax).toBe(2);
  });

  it("names a phase by its name, or by its place in the list", () => {
    const curve = profileCurve({
      type: "pro",
      phases: [{ name: "Fill", duration: 3 }, { duration: 4 }, { name: "", duration: 1 }],
    });
    expect(curve?.ranges).toEqual([
      { name: "Fill", start: 0, end: 3 },
      { name: "Phase 2", start: 3, end: 7 },
      { name: "Phase 3", start: 7, end: 8 },
    ]);
    expect(buildPhaseRanges([{ duration: "2.5" }])[0].end).toBe(2.5);
  });

  it("draws an empty phase list as an empty curve", () => {
    expect(profileCurve({ type: "pro", phases: [] })).toEqual({
      pressure: [],
      flow: [],
      ranges: [],
      xMax: 0,
    });
  });

  it("reads a duration the firmware's loop cannot finish on as an empty curve, not a hang", () => {
    // `parseFloat("12s")` is 12 (the firmware's axis and phase lines use it), while its loop
    // compares against the string, which is NaN and never true: it would run for ever.
    const phases = [{ name: "A", duration: "12s", pump: { target: "flow", flow: 3 } }];
    const curve = profileCurve({ type: "pro", phases });
    expect(curve?.pressure).toEqual([]);
    expect(curve?.flow).toEqual([]);
    expect(curve?.xMax).toBe(12);
    expect(curve?.ranges).toEqual([{ name: "A", start: 0, end: 12 }]);
  });

  it("gives up on a duration the firmware would loop on for ever", () => {
    expect(prepareData([{ pump: { target: "flow", flow: 3 } }], "flow")).toEqual([]);
    expect(prepareData([{ duration: "soon" }], "flow")).toEqual([]);
    expect(prepareData([{ duration: 1e9 }], "flow")).toEqual([]);
  });
});
