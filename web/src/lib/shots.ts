import type { ShotListRow } from "@/api/types";

/**
 * The vocabulary the shot pages share: how a number is written, and what a band
 * label means.
 *
 * Both belong here rather than in a component because the same shot is rendered
 * in three places — a list row, a detail card, a compare overlay — and a
 * duration that reads "28.4 s" in one and "28s" in another is the kind of drift
 * nobody notices until they are comparing two shots side by side.
 */

// ── places ───────────────────────────────────────────────────────────

/**
 * The fragment that lands the shot page on its Assign panel. Links to it are
 * made from the shots list, which offers only a few Sets and sends the rest
 * there; the page reads it and scrolls. One constant, so the two cannot drift.
 */
export const ASSIGN_ANCHOR = "set";

// ── numbers ──────────────────────────────────────────────────────────

export function formatTime(value: string | null | undefined): string {
  // A shot from a machine whose clock never synced has no timestamp at all
  // (`startEpoch < 10000`); saying so is better than rendering 1970.
  if (!value) return "no clock";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "no clock";
  return new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function formatSeconds(ms: number | null | undefined): string {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`;
}

export function formatGrams(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(1)} g`;
}

export function formatScore(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(1);
}

export function formatNumber(value: number | null | undefined, digits = 2, unit = ""): string {
  return value == null ? "—" : `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

/** The dose is not recorded anywhere yet (beans land in a later chunk), so a
    ratio can only be shown when somebody's device notes carry a `doseIn`. */
export function formatRatio(doseIn: number | null | undefined, out: number | null): string | null {
  if (doseIn == null || doseIn <= 0 || out == null) return null;
  return `1:${(out / doseIn).toFixed(1)}`;
}

export function profileName(shot: Pick<ShotListRow, "profile_label" | "profile_name_on_device">) {
  return shot.profile_label || shot.profile_name_on_device || "unknown profile";
}

// ── the execution score ──────────────────────────────────────────────

export type Tone = "good" | "warn" | "bad" | "neutral";

/**
 * Four bands over the 1-10 execution score.
 *
 * The score is a penalty subtraction from ten (`domain/scoring.py`), so the
 * interesting ground is the top half: 8+ is a shot with no material fault,
 * and below 5 something went properly wrong. Colouring it linearly would make
 * every shot look green.
 */
export function scoreBand(score: number | null | undefined): { label: string; tone: Tone } {
  if (score == null) return { label: "not scored", tone: "neutral" };
  if (score >= 8) return { label: "clean", tone: "good" };
  if (score >= 6.5) return { label: "minor faults", tone: "warn" };
  if (score >= 5) return { label: "faulted", tone: "warn" };
  return { label: "poor", tone: "bad" };
}

/** The score filter's four ranges, spelled once for the filter bar and tests. */
export const SCORE_BANDS = [
  { value: "any", label: "Any score" },
  { value: "clean", label: "Clean (8+)", min: 8 },
  { value: "minor", label: "Minor faults (6.5-8)", min: 6.5, max: 8 },
  { value: "faulted", label: "Faulted (5-6.5)", min: 5, max: 6.5 },
  { value: "poor", label: "Poor (under 5)", max: 5 },
] as const;

export type ScoreBandValue = (typeof SCORE_BANDS)[number]["value"];

export const TONE_TEXT: Record<Tone, string> = {
  good: "text-status-good-text",
  warn: "text-status-warn-text",
  bad: "text-status-bad-text",
  neutral: "text-muted-foreground",
};

// ── the device's own codes ───────────────────────────────────────────

/**
 * Why the shot ended. Mirrors `PHASE_EXIT_REASONS` in
 * `gaggiclanker/domain/models.py`; 8 is emitted by BrewProcess for
 * hold-to-flush and never made it into the header's own enum.
 */
export const EXIT_REASONS: Record<number, string> = {
  0: "Unknown",
  1: "Volumetric target",
  2: "Pressure target",
  3: "Flow target",
  4: "Pumped target",
  5: "Duration",
  6: "Safety timeout",
  7: "Aborted",
  8: "Hold released",
};

export function exitReasonLabel(code: number | null | undefined): string {
  if (code == null) return "Unknown";
  return EXIT_REASONS[code] ?? `Code ${code}`;
}

// ── band labels ──────────────────────────────────────────────────────

/**
 * One line per band label, so a card can say what "MODERATE_DECLINE" means
 * without the reader going to crema's own tables.
 *
 * The labels come from the threshold tables in
 * `gaggiclanker/domain/diagnostics.py` and they are *upstream's calibration* —
 * the meanings here describe what the number is measuring, never a
 * recommendation to grind finer. That judgement is the maintainer's, and from
 * the analyser's; a deterministic band has no business making it.
 *
 * Several labels appear in more than one table (`MODERATE` is a resistance
 * level, a temperature stability and a taper smoothness), so lookup is by
 * `metric:LABEL` first and bare label second.
 */
const BAND_MEANINGS: Record<string, string> = {
  // channeling risk
  "channeling:LOW": "No indicator crossed its threshold — the puck held.",
  "channeling:MODERATE": "Two or three indicators fired; read them with the flow shape.",
  "channeling:HIGH": "Four or five aligned indicators — a channel almost certainly opened.",
  "channeling:VERY_HIGH": "Six or more indicators: the water found a path around the bed.",
  "channeling:INSUFFICIENT_DATA": "Fewer than five steady-state samples; nothing was assessed.",
  // confidence in the channeling window
  "window_confidence:INSUFFICIENT": "Under five steady samples — no verdict was formed.",
  "window_confidence:LOW": "Five to seven steady samples; one indicator could be noise.",
  "window_confidence:MEDIUM": "Eight to fourteen steady samples.",
  "window_confidence:HIGH": "Fifteen or more steady samples — the window is worth trusting.",
  // resistance
  "level:VERY_LOW": "Under 0.5 — water is passing almost unimpeded.",
  "level:LOW": "0.5-1.5 — a loose bed for espresso.",
  "level:MODERATE": "1.5-3.0 — the usual range for a well-packed basket.",
  "level:HIGH": "3.0-5.0 — a tight bed; the pump is working against it.",
  "level:VERY_HIGH": "Over 5.0 — very tight, and flow will be hard to hold.",
  "erosion:INCREASING": "Resistance rose through the shot — the bed compacted rather than eroded.",
  "erosion:FLAT": "Resistance held steady: the puck kept its shape.",
  "erosion:GRADUAL_DECLINE": "A slow fall in resistance, normal as the bed saturates.",
  "erosion:MODERATE_DECLINE": "Resistance fell noticeably — part of the bed gave way.",
  "erosion:STEEP_DECLINE": "Resistance collapsed: the puck lost structure under pressure.",
  "saturation:EARLY": "Peak resistance in the first 15 % — the bed set almost at once.",
  "saturation:GOOD_TIMING": "Peak resistance between 15 % and 35 % of the shot.",
  "saturation:MID_SHOT": "Peak resistance around the middle; saturation took its time.",
  "saturation:LATE": "Peak resistance past 60 % — the bed was still settling near the end.",
  // generic stability / volatility families
  VERY_STABLE: "Below the first threshold: as steady as this signal gets.",
  STABLE: "Within normal sample-to-sample variation.",
  MODERATE_JITTER: "More movement than usual, below the level that flags a fault.",
  JITTERY: "Enough instability to register as a channeling indicator.",
  VOLATILE: "The signal is not holding at all.",
  MODERATE: "Middle of the range: neither clean nor a fault.",
  UNSTABLE: "Past the last threshold — the signal never settled.",
  // temperature
  MINIMAL: "Under 0.5 °C away from target — not worth acting on.",
  SLIGHT: "0.5-1.0 °C from target.",
  SIGNIFICANT: "Over 2 °C from target; the boiler did not hold.",
  // adherence / compliance
  EXCELLENT: "RMSE under 0.3 — the machine tracked the profile closely.",
  GOOD: "RMSE 0.3-0.8; small, steady deviation from what was commanded.",
  FAIR: "RMSE 0.8-1.5 — visibly off the commanded curve.",
  POOR: "RMSE over 1.5: the shot did not follow the profile.",
  WITHIN_TOLERANCE: "Inside the band where deviation means nothing.",
  MINOR_DEVIATION: "A small, measurable departure from target.",
  NOTABLE_DEVIATION: "Large enough to look at alongside the curve.",
  SEVERE_DEVIATION: "The actual signal and the target parted company.",
  MINOR_OVERSHOOT: "0.25-0.5 bar over target.",
  NOTABLE_OVERSHOOT: "0.5-1.0 bar over target.",
  SEVERE_OVERSHOOT: "Over a bar above target — the controller ran out of room.",
  // pressure drop rate
  NORMAL: "No abrupt pressure drop.",
  MODERATE_DROP: "A drop of 1-2.5 bar/s somewhere in the shot.",
  STEEP_DROP: "2.5-5 bar/s: pressure fell faster than a profile would command.",
  CLIFF: "Over 5 bar/s — the kind of drop a channel opening makes.",
  // late flow
  SLIGHT_ACCELERATION: "Flow picked up slightly towards the end, beyond the overall trend.",
  MODERATE_ACCELERATION: "Flow accelerated late — the bed may be opening up.",
  RAPID_ACCELERATION: "Flow ran away in the last part of the shot.",
  // flow shape (descriptive, not scored)
  FLAT: "Flow held one level.",
  RISING: "Flow climbed through the window.",
  FALLING: "Flow fell through the window.",
  // taper
  VERY_SMOOTH: "The decline was almost a straight line.",
  SMOOTH: "A clean taper with small deviations.",
  ROUGH: "The decline was stepped or noisy.",
  // ramp rate
  GENTLE: "Under 0.5 bar/s into the phase.",
  BRISK: "1.5-3 bar/s — a fast ramp.",
  AGGRESSIVE: "3-5 bar/s.",
  VERY_AGGRESSIVE: "Over 5 bar/s: as hard as the pump will push.",
  "N/A": "Not assessed for this shot.",
};

/**
 * Metrics that read the same band table under a different name.
 *
 * `extraction.annotations.pressure_trend` is banded by the resistance-slope
 * table, so "GRADUAL_DECLINE" there means what it means under `erosion` — but
 * the two need distinct keys, because a card showing both must not give them
 * the same handle.
 */
const METRIC_ALIASES: Record<string, string> = {
  pressure_trend: "erosion",
  flow_trend: "late_flow_trend",
  rate_stability: "",
};

export function bandMeaning(metric: string, label: string | undefined): string | undefined {
  if (!label) return undefined;
  const alias = METRIC_ALIASES[metric];
  return (
    BAND_MEANINGS[`${metric}:${label}`] ??
    (alias ? BAND_MEANINGS[`${alias}:${label}`] : undefined) ??
    BAND_MEANINGS[label]
  );
}

/** Whether a band label reads as good news, a warning or a fault. */
export function bandTone(label: string | undefined): Tone {
  if (!label) return "neutral";
  if (
    [
      "VERY_STABLE",
      "STABLE",
      "LOW",
      "MINIMAL",
      "EXCELLENT",
      "GOOD",
      "WITHIN_TOLERANCE",
      "NORMAL",
      "FLAT",
      "GOOD_TIMING",
      "VERY_SMOOTH",
      "SMOOTH",
      "GENTLE",
      "HIGH_CONFIDENCE",
    ].includes(label)
  ) {
    return "good";
  }
  if (
    [
      "VOLATILE",
      "UNSTABLE",
      "POOR",
      "SEVERE_DEVIATION",
      "SEVERE_OVERSHOOT",
      "CLIFF",
      "RAPID_ACCELERATION",
      "STEEP_DECLINE",
      "STEEP_DROP",
      "VERY_HIGH",
      "SIGNIFICANT",
      "ROUGH",
      "VERY_AGGRESSIVE",
    ].includes(label)
  ) {
    return "bad";
  }
  if (label === "INSUFFICIENT_DATA" || label === "N/A" || label === "INSUFFICIENT") {
    return "neutral";
  }
  return "warn";
}

/** "MODERATE_DECLINE" as "moderate decline". Labels are shouted on the wire. */
export function humanizeBand(label: string | undefined): string {
  if (!label) return "—";
  return label.toLowerCase().replace(/_/g, " ");
}

/** "flow_adherence" as "flow adherence", for score components and annotations. */
export function humanizeKey(key: string): string {
  return key.replace(/_/g, " ");
}
