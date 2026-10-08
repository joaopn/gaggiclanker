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
  /** Which of the chart palette's six slots this takes. */
  color: number;
  /** Only meaningful on a machine with a pressure sensor (Pro boards). */
  needsPressure?: boolean;
  /** Only meaningful with a BLE scale attached. */
  needsScale?: boolean;
};

/**
 * The ten series a shot page can draw, in the order the toggles list them.
 *
 * Four pairs of actual-against-target plus two flows with no target. Cup flow is
 * what reached the cup, measured by the scale. Puck flow is *estimated* from the
 * pump model, runs at about the pump flow and starts seconds after coffee is in
 * the cup; reading it against the commanded flow is how a channel shows up.
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
    key: "cupFlow",
    label: "Cup flow",
    field: "vf",
    axis: "y",
    unit: "g/s",
    color: 5,
    needsScale: true,
  },
  {
    key: "flow",
    label: "Pump flow",
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

/** What a fresh page shows for a shot without a scale: enough to read it, not every line. */
export const DEFAULT_SERIES: string[] = [
  "pressure",
  "targetPressure",
  "flow",
  "puckFlow",
  "weight",
  "temperature",
];

/** What a fresh page shows for a shot with a scale: the cup is the flow that matters. */
export const SCALE_DEFAULT_SERIES: string[] = [
  "pressure",
  "targetPressure",
  "cupFlow",
  "weight",
  "temperature",
];

/**
 * Which flow an overlay of several shots draws: the cup's only where every shot had a scale,
 * since mixing the two in one picture would compare different things.
 */
export function compareFlow(hasScale: boolean[]): "cup" | "puck" {
  return hasScale.length > 0 && hasScale.every(Boolean) ? "cup" : "puck";
}

/**
 * The series a fresh page draws for a shot: the scale default when it had a scale, else the
 * other, and never a series the shot has no data for (a disabled toggle is never on).
 *
 * Whether the shot had a scale is the server's answer (`has_scale` on the shot); it is not
 * worked out here from the samples, which for a board without a scale are zeros.
 */
export function defaultSeries(hasScale: boolean, present: Set<string>): string[] {
  return (hasScale ? SCALE_DEFAULT_SERIES : DEFAULT_SERIES).filter((key) => present.has(key));
}

/**
 * A sample's reading of a field. Cup flow is read at zero where the log has it
 * below: a scale cannot lose coffee into the machine, and the old tare glitch
 * logged -20 g/s on the first samples of some shots.
 */
export function reading(sample: ShotSampleRow, field: keyof ShotSampleRow): number | null {
  const value = sample[field];
  if (typeof value !== "number") return null;
  return field === "vf" ? Math.max(value, 0) : value;
}

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
      const value = reading(sample, series.spec.field);
      if (value !== null) series.points.push({ x, y: value });
    }
  }
  return built;
}

/** Whether a signal is present at all, so a toggle for it can be disabled. */
export function availableSeries(samples: ShotSampleRow[], hasScale: boolean): Set<string> {
  const present = new Set<string>();
  for (const spec of SHOT_SERIES) {
    // A board with no scale records weight and cup flow as zeros: they are not signals there.
    if (spec.needsScale && !hasScale) continue;
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

type TimedPoint = [t: number, value: number];

/** The numeric readings of one field with their times; `null` is "never recorded". */
function fieldPoints(samples: ShotSampleRow[], field: keyof ShotSampleRow): TimedPoint[] {
  const points: TimedPoint[] = [];
  for (const sample of samples) {
    const value = reading(sample, field);
    if (value !== null) points.push([sample.t_ms, value]);
  }
  return points;
}

/**
 * One polyline over `points`, with the vertical scale handed in: `low` is the
 * value at the bottom of the box and `high` the value 92% of the way up. The
 * 0.92 leaves the peak just inside the box rather than on its edge, and the
 * y axis is inverted because SVG's grows downwards and a curve should not read
 * upside down.
 */
function polyline(
  points: TimedPoint[],
  low: number,
  high: number,
  width: number,
  height: number,
): string {
  const span = points[points.length - 1][0] - points[0][0] || 1;
  const first = points[0][0];
  const range = high - low;
  return points
    .map(([t, value], index) => {
      const x = ((t - first) / span) * width;
      const y = range > 0 ? height - ((value - low) / range) * height * 0.92 : height * 0.5;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * A sparkline path for a list row: one field, normalised on its own so its
 * shape is readable at 20 pixels tall.
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
  const points = fieldPoints(samples, field);
  const max = points.reduce((most, [, value]) => Math.max(most, value), 0);
  if (points.length < 2 || max <= 0) return "";
  return polyline(points, 0, max, width, height);
}

export type SparklineCurve = { spec: SeriesSpec; d: string };

/**
 * The chosen series of one row, drawn on shared scales.
 *
 * Series that share a unit share one vertical scale, so an actual drawn
 * against its target can be compared by eye: normalised on their own, a
 * pressure that never reached its target would still touch the top of the box.
 * Each group runs from 0 to its maximum, except °C, which runs from its
 * minimum: a temperature held within a degree of 93 drawn from 0 is a flat
 * line. Different units are not comparable, so each group has its own scale.
 *
 * A series the shot has no usable data for is left out rather than drawn flat:
 * fewer than two readings, or none above zero. The firmware logs every field
 * on every board, so a board with no scale or no pressure sensor records zeros,
 * not nulls, and a zero line drawn on its group's scale would read as a
 * measurement. That series must not take part in the group's scale either.
 */
export function sparklineCurves(
  samples: ShotSampleRow[],
  keys: readonly string[],
  width: number,
  height: number,
  hasScale = true,
): SparklineCurve[] {
  const drawn = SHOT_SERIES.filter(
    (spec) => keys.includes(spec.key) && (hasScale || !spec.needsScale),
  ).flatMap((spec) => {
    const points = fieldPoints(samples, spec.field);
    const peak = points.reduce((most, [, value]) => Math.max(most, value), 0);
    return points.length >= 2 && peak > 0 ? [{ spec, points }] : [];
  });

  // Cup flow (g/s) shares the flows' scale (ml/s): espresso is close enough to 1 g/ml that
  // drawing them together reads honestly, and the unit stays in the tooltip.
  const scaleOf = (unit: string) => (unit === "g/s" ? "ml/s" : unit);
  const scales = new Map<string, { low: number; high: number }>();
  for (const { spec, points } of drawn) {
    const celsius = spec.unit === "°C";
    const scale = scales.get(scaleOf(spec.unit)) ?? {
      low: celsius ? Number.POSITIVE_INFINITY : 0,
      high: Number.NEGATIVE_INFINITY,
    };
    for (const [, value] of points) {
      scale.high = Math.max(scale.high, value);
      if (celsius) scale.low = Math.min(scale.low, value);
    }
    scales.set(scaleOf(spec.unit), scale);
  }

  return drawn.flatMap(({ spec, points }) => {
    const scale = scales.get(scaleOf(spec.unit));
    if (!scale) return [];
    return [{ spec, d: polyline(points, scale.low, scale.high, width, height) }];
  });
}
