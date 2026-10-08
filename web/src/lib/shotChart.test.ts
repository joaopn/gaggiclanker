import { describe, expect, it } from "vitest";
import type { ShotPhase } from "@/api/types";
import {
  availableSeries,
  buildShotSeries,
  compareFlow,
  DEFAULT_SERIES,
  defaultSeries,
  phaseBands,
  SCALE_DEFAULT_SERIES,
  SHOT_SERIES,
  sparklineCurves,
  sparklinePath,
} from "@/lib/shotChart";
import {
  SHOT_129_SAMPLE_COUNT,
  shot129,
  shot129Samples,
  syntheticSamples,
} from "@/test/shotFixture";

const samples = shot129Samples.samples;
const phases = (shot129.shot.phases ?? []) as unknown as ShotPhase[];

describe("buildShotSeries", () => {
  it("draws every requested signal from the real shot", () => {
    const built = buildShotSeries(samples, DEFAULT_SERIES);

    expect(built.map((series) => series.spec.key)).toEqual(DEFAULT_SERIES);
    for (const series of built) {
      expect(series.points.length).toBe(SHOT_129_SAMPLE_COUNT);
    }
  });

  it("puts time in seconds on the x axis, from the sample's own t", () => {
    const [pressure] = buildShotSeries(samples, ["pressure"]);

    expect(pressure.points[0].x).toBe(0);
    // 213 samples at 250 ms: the last one is at 53 s, and the shot's 54.6 s
    // duration includes the final sample's own interval.
    expect(pressure.points[pressure.points.length - 1].x).toBeCloseTo(53, 1);
  });

  it("drops a signal the firmware never recorded rather than plotting zero", () => {
    // A Standard board writes no pressure at all; "not recorded" and "recorded
    // zero" are different facts and the second would draw a flat line at the
    // bottom of the chart that looks like a measurement.
    const standard = syntheticSamples(20, { hasPressure: false });
    const [pressure] = buildShotSeries(standard, ["pressure"]);

    expect(pressure.points).toHaveLength(0);
  });

  it("reads the samples once however many series are asked for", () => {
    // Not a timing assertion — the shape is the guarantee: every series gets
    // the same number of points from the same pass, so toggling one on cannot
    // shift the others.
    const built = buildShotSeries(
      samples,
      SHOT_SERIES.map((spec) => spec.key),
    );
    const lengths = new Set(built.filter((s) => s.points.length > 0).map((s) => s.points.length));

    expect(lengths.size).toBe(1);
  });
});

describe("availableSeries", () => {
  it("names only the signals present in the file", () => {
    const present = availableSeries(
      syntheticSamples(10, { hasPressure: false, hasScale: false }),
      false,
    );

    expect(present.has("pressure")).toBe(false);
    expect(present.has("weight")).toBe(false);
    // Estimated weight is integrated from pump flow, so it survives a machine
    // with no scale.
    expect(present.has("estimatedWeight")).toBe(true);
    expect(present.has("temperature")).toBe(true);
  });
});

describe("phaseBands", () => {
  it("turns the header's transition table into bands", () => {
    const bands = phaseBands(phases);

    expect(bands.map((band) => band.name)).toEqual(["fill", "soak", "ramp", "decline 9-4"]);
    expect(bands[0].start).toBe(0);
    // Each band ends where the next begins, which is what makes them read as
    // one shot rather than four separate ones.
    for (let index = 1; index < bands.length; index += 1) {
      expect(bands[index].start).toBeCloseTo(bands[index - 1].end, 1);
    }
  });

  it("is empty rather than wrong when there are no phases", () => {
    expect(phaseBands(null)).toEqual([]);
    expect(phaseBands([])).toEqual([]);
  });
});

describe("sparklinePath", () => {
  it("spans the full width and stays inside the box", () => {
    const path = sparklinePath(samples, "cp", 96, 20);
    const coordinates = path
      .slice(1)
      .split(/[ML]/)
      .filter(Boolean)
      .map((pair) => pair.split(",").map(Number));

    expect(coordinates[0][0]).toBe(0);
    expect(coordinates[coordinates.length - 1][0]).toBeCloseTo(96, 0);
    for (const [, y] of coordinates) {
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(20);
    }
  });

  it("draws nothing rather than a flat line when the signal is absent", () => {
    expect(sparklinePath(syntheticSamples(10, { hasPressure: false }), "cp", 96, 20)).toBe("");
    expect(sparklinePath(samples.slice(0, 1), "cp", 96, 20)).toBe("");
  });
});

describe("sparklineCurves", () => {
  const ys = (d: string) =>
    d
      .slice(1)
      .split(/[ML]/)
      .filter(Boolean)
      .map((pair) => Number(pair.split(",")[1]));

  it("draws a series on the same scale as its target when their values are equal", () => {
    const rows = syntheticSamples(10).map((row) => ({ ...row, tp: row.cp }));
    const curves = sparklineCurves(rows, ["pressure", "targetPressure"], 96, 20);

    expect(curves.map((curve) => curve.spec.key)).toEqual(["pressure", "targetPressure"]);
    expect(ys(curves[0].d)).toEqual(ys(curves[1].d));
  });

  it("scales a unit group together, so an actual below its target stays below it", () => {
    const rows = syntheticSamples(10).map((row) => ({ ...row, tp: 9, cp: 4.5 }));
    const [actual, target] = sparklineCurves(rows, ["pressure", "targetPressure"], 96, 20);

    // Higher on the screen is a smaller y: half the target's value sits lower.
    expect(Math.min(...ys(actual.d))).toBeGreaterThan(Math.min(...ys(target.d)));
  });

  it("keeps different units on scales of their own", () => {
    const rows = syntheticSamples(10).map((row) => ({ ...row, cp: 1, tf: 100 }));
    const [pressure, flow] = sparklineCurves(rows, ["pressure", "targetFlow"], 96, 20);

    expect(Math.min(...ys(pressure.d))).toBeCloseTo(Math.min(...ys(flow.d)), 5);
  });

  it("scales temperature from its group minimum to its maximum, not from zero", () => {
    const rows = syntheticSamples(10).map((row, index) => ({
      ...row,
      ct: 92 + (index % 2),
      tt: 93,
    }));
    const [actual] = sparklineCurves(rows, ["temperature", "targetTemperature"], 96, 20);

    // From zero, 92 and 93 would be within a pixel of each other.
    expect(Math.max(...ys(actual.d)) - Math.min(...ys(actual.d))).toBeGreaterThan(10);
  });

  it("gives the same shape as the single-field path for one series", () => {
    expect(sparklineCurves(samples, ["pressure"], 96, 20)[0].d).toBe(
      sparklinePath(samples, "cp", 96, 20),
    );
  });

  it("leaves out a scale that recorded zeros, though its target has data", () => {
    // The firmware logs every field on every board: no scale attached is a
    // column of zeros, not of nulls.
    const noScale = samples.map((row) => ({ ...row, v: 0 }));
    expect(Math.max(...samples.map((row) => row.v ?? 0))).toBeGreaterThan(0);
    expect(Math.max(...samples.map((row) => row.ev ?? 0))).toBeGreaterThan(0);

    const curves = sparklineCurves(noScale, ["weight", "estimatedWeight"], 96, 20);
    expect(curves.map((curve) => curve.spec.key)).toEqual(["estimatedWeight"]);
  });

  it("leaves out a pressure that recorded zeros, though its target has data", () => {
    const noSensor = samples.map((row) => ({ ...row, cp: 0 }));
    expect(Math.max(...samples.map((row) => row.cp ?? 0))).toBeGreaterThan(0);
    expect(Math.max(...samples.map((row) => row.tp ?? 0))).toBeGreaterThan(0);

    const curves = sparklineCurves(noSensor, ["pressure", "targetPressure"], 96, 20);
    expect(curves.map((curve) => curve.spec.key)).toEqual(["targetPressure"]);
  });

  it("leaves out a series the shot has no data for, in the series' own order", () => {
    const standard = syntheticSamples(10, { hasPressure: false, hasScale: false });
    const curves = sparklineCurves(standard, ["weight", "temperature", "pressure"], 96, 20);

    expect(curves.map((curve) => curve.spec.key)).toEqual(["temperature"]);
  });
});

// The real shot with its scale columns zeroed, the way a board with no scale logs them.
const noScale = samples.map((row) => ({ ...row, v: 0, vf: 0 }));
// The old tare glitch: the first sample of some shots logs the clamp, -20 g/s.
const glitched = samples.map((row, index) => (index === 0 ? { ...row, vf: -20 } : row));

describe("cup flow", () => {
  it("is a series of its own on the flows' axis, with a colour no other series has", () => {
    const spec = SHOT_SERIES.find((candidate) => candidate.key === "cupFlow");

    expect(spec).toMatchObject({ label: "Cup flow", field: "vf", axis: "y", unit: "g/s" });
    expect(spec?.needsScale).toBe(true);
    expect(spec?.dashed).toBeUndefined();
    const colours = SHOT_SERIES.filter((other) => other.key !== "cupFlow").map((o) => o.color);
    expect(colours).not.toContain(spec?.color);
    expect(SHOT_SERIES.find((candidate) => candidate.key === "flow")?.label).toBe("Pump flow");
  });

  it("is read at zero where the log has it below", () => {
    const [built] = buildShotSeries(glitched, ["cupFlow"]);

    expect(built.points[0].y).toBe(0);
    expect(Math.min(...built.points.map((point) => point.y))).toBeGreaterThanOrEqual(0);
  });

  it("is a toggle that exists only for a shot that had a scale", () => {
    expect(availableSeries(samples, true).has("cupFlow")).toBe(true);
    // The zeros a board without a scale logs are numbers, but they are not a signal: the
    // server says the shot had no scale, and the web does not decide that from the zeros.
    const bare = availableSeries(noScale, false);
    expect(bare.has("cupFlow")).toBe(false);
    expect(bare.has("weight")).toBe(false);
    expect(bare.has("puckFlow")).toBe(true);
  });

  it("leads the default series of a shot with a scale, and not those of one without", () => {
    expect(defaultSeries(true, availableSeries(samples, true))).toEqual(SCALE_DEFAULT_SERIES);
    expect(SCALE_DEFAULT_SERIES).toEqual([
      "pressure",
      "targetPressure",
      "cupFlow",
      "weight",
      "temperature",
    ]);
    // Without a scale, today's default minus what the shot does not have: no weight.
    const bare = defaultSeries(false, availableSeries(noScale, false));
    expect(bare).toEqual(DEFAULT_SERIES.filter((key) => key !== "weight"));
    expect(DEFAULT_SERIES).not.toContain("cupFlow");
  });

  it("shares the sparkline scale of the other flows", () => {
    const rows = samples.map((row) => ({ ...row, vf: 0.5, pf: 4 }));
    const curves = sparklineCurves(rows, ["puckFlow", "cupFlow"], 96, 20);
    // A shot the server says had no scale draws no cup flow, whatever its columns hold.
    expect(
      sparklineCurves(rows, ["puckFlow", "cupFlow"], 96, 20, false).map((c) => c.spec.key),
    ).toEqual(["puckFlow"]);

    expect(curves.map((curve) => curve.spec.key)).toEqual(["cupFlow", "puckFlow"]);
    // Both are flat lines on one scale from zero to 4: the cup flow sits at an eighth.
    const y = (d: string) => Number(d.split(" ")[0].split(",")[1]);
    expect(y(curves[0].d)).toBeGreaterThan(y(curves[1].d));
  });
});

describe("compareFlow", () => {
  it("is the cup flow only when every shot drawn had a scale", () => {
    expect(compareFlow([true, true])).toBe("cup");
    expect(compareFlow([true, false])).toBe("puck");
    expect(compareFlow([false])).toBe("puck");
    expect(compareFlow([])).toBe("puck");
  });
});
