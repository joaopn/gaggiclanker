/**
 * The curve a profile aims for, drawn the way the machine's own web page draws it.
 *
 * A port of `prepareData`, `applyEasing` and `buildPhaseRanges` from GaggiMate firmware
 * v1.9.0 (`web/src/components/ExtendedProfileChart.jsx`). It is held to the firmware's own
 * code by `profileCurve.golden.json`, which `tests/fixtures/profile_curve/generate.py` makes by
 * running the firmware's functions under node. The firmware's quirks are kept on purpose:
 *
 * - the first phase starts from 0 whatever the machine was doing before;
 * - a phase's ramp runs over `transition.duration`, or the phase's duration when that is 0 or
 *   absent, and a ramp longer than the phase is cut off where the phase ends;
 * - a phase of zero duration divides 0 by 0, so its target quantity is NaN and that NaN is the
 *   next phase's starting point (later ramps of that quantity start from NaN until a phase
 *   that does not target it draws it at its own value again). The firmware's chart
 *   sets `spanGaps: true`, as this one does, so Chart.js draws one continuous segment across
 *   the NaN points rather than a gap;
 * - a point is a "target" point when the phase's `pump.target` names the series, and the
 *   other series is then only a limit.
 *
 * Where this differs from the firmware: a duration that is not a number (the firmware's loop
 * never ends on it, and `Number("12s")` is NaN though `parseFloat("12s")` is 12, which the axis
 * and phase lines still use) gives an empty curve here; and a profile of more than
 * `MAX_POINTS` points (over 20,000 s) is also empty, although the firmware would draw it. Both
 * are out of reach of a stored profile: the app's profile model allows 0.5 to 300 s per
 * phase and numbers only (`domain/models.py`), so neither the cap nor a zero-duration NaN can
 * come from one.
 */

const POINT_INTERVAL = 0.1; // s

/**
 * A guard against a billion points from a typo like 1e9 s. It does cut profiles the firmware
 * would draw (a 20,003 s profile); see the note at the top.
 */
const MAX_POINTS = 200_000;

export type CurvePoint = {
  x: number;
  y: number;
  /** Whether the phase aims for this quantity (solid) or only limits it (dashed). */
  target: boolean;
};

export type PhaseRange = { name: string; start: number; end: number };

export type ProfileCurve = {
  pressure: CurvePoint[];
  flow: CurvePoint[];
  ranges: PhaseRange[];
  /** The sum of the phase durations: the x axis' end. */
  xMax: number;
};

type Json = Record<string, unknown>;

type Phase = {
  name?: string;
  duration?: number | string;
  transition?: { type?: string; duration?: number | string };
  pump?: { target?: string; pressure?: number; flow?: number };
};

function applyEasing(t: number, type: string): number {
  if (t <= 0.0) return 0.0;
  if (t >= 1.0) return 1.0;
  switch (type) {
    case "linear":
      return t;
    case "ease-in":
      return t * t;
    case "ease-out":
      return 1.0 - (1.0 - t) * (1.0 - t);
    case "ease-in-out":
      return t < 0.5 ? 2.0 * t * t : 1.0 - 2.0 * (1.0 - t) * (1.0 - t);
    default:
      return 1.0;
  }
}

/** `a || b` as the firmware writes it: a missing, zero or NaN value falls through. */
const orElse = <T>(a: T | undefined | null, b: T): T => (a ? a : b);

/** The firmware's `prepareData`, for one series. */
export function prepareData(phases: Phase[], target: "pressure" | "flow"): CurvePoint[] {
  if (!Array.isArray(phases) || phases.length === 0) return [];
  // The firmware loops until the clock passes every duration; a duration that is not a number
  // never does, and a profile of days would not fit in memory.
  let total = 0;
  for (const phase of phases) {
    const duration = Number(phase.duration);
    if (Number.isNaN(duration)) return [];
    total += Math.max(duration, 0);
  }
  if (total / POINT_INTERVAL > MAX_POINTS) return [];

  const data: CurvePoint[] = [];
  let time = 0;
  let phaseTime = 0;
  let phaseIndex = 0;
  let currentPhase = phases[phaseIndex];
  let currentPressure = 0;
  let currentFlow = 0;
  let phaseStartFlow = 0;
  let phaseStartPressure = 0;
  let effectiveFlow = currentPhase.pump?.flow || 0;
  let effectivePressure = currentPhase.pump?.pressure || 0;

  do {
    currentPhase = phases[phaseIndex];
    const alpha = applyEasing(
      phaseTime / Number(orElse(currentPhase.transition?.duration, currentPhase.duration)),
      currentPhase?.transition?.type || "linear",
    );
    currentFlow =
      currentPhase.pump?.target === "flow"
        ? phaseStartFlow + (effectiveFlow - phaseStartFlow) * alpha
        : currentPhase.pump?.flow || 0;
    currentPressure =
      currentPhase.pump?.target === "pressure"
        ? phaseStartPressure + (effectivePressure - phaseStartPressure) * alpha
        : currentPhase.pump?.pressure || 0;
    data.push({
      x: time,
      y: target === "pressure" ? currentPressure : currentFlow,
      target: currentPhase.pump?.target === target,
    });
    time += POINT_INTERVAL;
    phaseTime += POINT_INTERVAL;
    if (phaseTime >= Number(currentPhase.duration)) {
      phaseTime = 0;
      phaseIndex++;
      if (phaseIndex < phases.length) {
        phaseStartFlow = currentFlow;
        phaseStartPressure = currentPressure;
        const nextPhase = phases[phaseIndex];
        effectiveFlow = nextPhase.pump?.flow === -1 ? currentFlow : nextPhase.pump?.flow || 0;
        effectivePressure =
          nextPhase.pump?.pressure === -1 ? currentPressure : nextPhase.pump?.pressure || 0;
      }
    }
  } while (phaseIndex < phases.length);

  return data;
}

/** The firmware's `buildPhaseRanges`, with the name it labels each line with. */
export function buildPhaseRanges(phases: Phase[]): PhaseRange[] {
  const ranges: PhaseRange[] = [];
  let start = 0;
  phases.forEach((phase, index) => {
    const duration = Number.parseFloat(String(phase.duration));
    ranges.push({ name: phase.name || `Phase ${index + 1}`, start, end: start + duration });
    start += duration;
  });
  return ranges;
}

/** Whether the machine draws this profile's curve: only "pro" profiles have one. */
export function hasCurve(profile: Json | null | undefined): boolean {
  return profile?.type === "pro";
}

/**
 * The two series, the phase lines and the x axis' end for a profile document, or null for a
 * profile the machine draws no curve for.
 */
export function profileCurve(profile: Json | null | undefined): ProfileCurve | null {
  if (!profile || !hasCurve(profile)) return null;
  const phases = (Array.isArray(profile.phases) ? profile.phases : []) as Phase[];
  const ranges = buildPhaseRanges(phases);
  let xMax = 0;
  for (const phase of phases) xMax += Number.parseFloat(String(phase.duration));
  return {
    pressure: prepareData(phases, "pressure"),
    flow: prepareData(phases, "flow"),
    ranges,
    xMax,
  };
}
