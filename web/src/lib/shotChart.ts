import type { ShotPhase, ShotSampleRow } from "@/api/types";

/**
 * Turning a shot's samples into chart data, without any chart library in sight.
 *
 * Kept separate from the component for two reasons. It is the part worth
 * testing — "does the pressure series carry 213 points and start at zero" is a
 * question about data, not about pixels, and jsdom cannot answer questions
 * about pixels. And it is the part that runs again on every series toggle, so
 * it has to stay a single pass over the samples.
 */

export type AxisId = "y" | "yTemp" | "yWeight";

export type SeriesSpec = {
  key: string;
  label: string;
  /** The `.slog` sample field this draws (firmware report §2.2). */
  field: keyof ShotSampleRow;
  axis: AxisId;
  unit: string;
  /** Targets are drawn dashed: what was commanded, against what happened. */
  dashed?: boolean;
  /** Which of the chart palette's five slots this takes. */
  color: number;
  /** Only meaningful on a machine with a pressure sensor (Pro boards). */
  needsPressure?: boolean;
  /** Only meaningful with a BLE scale attached. */
  needsScale?: boolean;
};

/**
 * The nine series a shot page can draw, in the order the toggles list them.
 *
 * Four pairs of actual-against-target plus puck flow, which is the one signal
 * with no target: it is *estimated* from the pump, not measured, and reading it
 * against the commanded flow is how a channel shows up.
 */
export const SHOT_SERIES: SeriesSpec[] = [
  {
    key: "pressure",
    label: "Pressure",
    field: "cp",
    axis: "y",
    unit: "bar",
    color: 0,
    needsPressure: true,
  },
  {
    key: "targetPressure",
    label: "Target pressure",
    field: "tp",
    axis: "y",
    unit: "bar",
    dashed: true,
    color: 0,
    needsPressure: true,
  },
  {
    key: "flow",
    label: "Flow",
    field: "fl",
    axis: "y",
    unit: "ml/s",
    color: 1,
    needsPressure: true,
  },
  {
    key: "targetFlow",
    label: "Target flow",
    field: "tf",
    axis: "y",
    unit: "ml/s",
    dashed: true,
    color: 1,
    needsPressure: true,
  },
  {
    key: "puckFlow",
    label: "Puck flow",
    field: "pf",
    axis: "y",
    unit: "ml/s",
    color: 4,
    needsPressure: true,
  },
  {
    key: "weight",
    label: "Weight",
    field: "v",
    axis: "yWeight",
    unit: "g",
    color: 3,
    needsScale: true,
  },
  {
    key: "estimatedWeight",
    label: "Estimated weight",
    field: "ev",
    axis: "yWeight",
    unit: "g",
    dashed: true,
    color: 3,
  },
  { key: "temperature", label: "Temperature", field: "ct", axis: "yTemp", unit: "°C", color: 2 },
  {
    key: "targetTemperature",
    label: "Target temperature",
    field: "tt",
    axis: "yTemp",
    unit: "°C",
    dashed: true,
    color: 2,
  },
];

/** What a fresh shot page shows: enough to read the shot, not all nine lines. */
export const DEFAULT_SERIES: string[] = [
  "pressure",
  "targetPressure",
  "flow",
  "puckFlow",
  "weight",
  "temperature",
];

export type SeriesPoint = { x: number; y: number };

export type BuiltSeries = {
  spec: SeriesSpec;
  points: SeriesPoint[];
};

/**
 * One pass over the samples, producing a point list per requested series.
 *
 * `null` is dropped rather than plotted as zero: the `.slog` fieldsMask says
 * which signals the firmware recorded at all, and "never recorded" is a
 * different fact from "recorded zero" — the difference between a machine with
 * no scale and a shot that yielded nothing.
 */
export function buildShotSeries(
  samples: ShotSampleRow[],
  keys: string[] = DEFAULT_SERIES,
): BuiltSeries[] {
  const specs = SHOT_SERIES.filter((spec) => keys.includes(spec.key));
  const built: BuiltSeries[] = specs.map((spec) => ({ spec, points: [] }));
  for (const sample of samples) {
    const x = sample.t_ms / 1000;
    for (const series of built) {
      const value = sample[series.spec.field];
      if (typeof value === "number") series.points.push({ x, y: value });
    }
  }
  return built;
}

/** Whether a signal is present at all, so a toggle for it can be disabled. */
export function availableSeries(samples: ShotSampleRow[]): Set<string> {
  const present = new Set<string>();
  for (const spec of SHOT_SERIES) {
    if (samples.some((sample) => typeof sample[spec.field] === "number")) present.add(spec.key);
  }
  return present;
}

export type PhaseBand = {
  name: string;
  phaseNumber: number;
  start: number;
  end: number;
};

/**
 * The phase table as bands on the time axis.
 *
 * `phases` comes from the header's own transition table, so the boundaries are
 * the machine's and not something re-derived from the curve. A v4 or earlier
 * shot has no transition table and arrives as a single "extraction" phase,
 * which draws as one band across the whole shot — correct, and visibly
 * different from four.
 */
export function phaseBands(phases: ShotPhase[] | null | undefined): PhaseBand[] {
  if (!phases || phases.length === 0) return [];
  return phases.map((phase) => ({
    name: phase.name,
    phaseNumber: phase.phase_number,
    start: phase.start_time_seconds,
    end: phase.start_time_seconds + phase.duration_seconds,
  }));
}

/**
 * A sparkline path for a list row: pressure and puck flow, normalised
 * separately so both shapes are readable at 20 pixels tall.
 *
 * SVG rather than a canvas per row: a hundred canvases is a hundred GPU
 * surfaces, and the shape of a 40-point curve needs no more than a polyline.
 */
export function sparklinePath(
  samples: ShotSampleRow[],
  field: keyof ShotSampleRow,
  width: number,
  height: number,
): string {
  const points: Array<[number, number]> = [];
  let max = 0;
  for (const sample of samples) {
    const value = sample[field];
    if (typeof value !== "number") continue;
    points.push([sample.t_ms, value]);
    if (value > max) max = value;
  }
  if (points.length < 2 || max <= 0) return "";
  const span = points[points.length - 1][0] - points[0][0] || 1;
  const first = points[0][0];
  return points
    .map(([t, value], index) => {
      const x = ((t - first) / span) * width;
      // Inverted: SVG's y grows downwards and a curve should not read upside
      // down. 0.92 leaves the peak just inside the box rather than on its edge.
      const y = height - (value / max) * height * 0.92;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
