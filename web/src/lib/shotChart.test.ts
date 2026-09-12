import { describe, expect, it } from "vitest";
import type { ShotPhase } from "@/api/types";
import {
  availableSeries,
  buildShotSeries,
  DEFAULT_SERIES,
  phaseBands,
  SHOT_SERIES,
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
    const present = availableSeries(syntheticSamples(10, { hasPressure: false, hasScale: false }));

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
