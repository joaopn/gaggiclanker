import type { ShotListRow } from "@/api/types";

/**
 * The vocabulary the shot pages share: how a number is written.
 *
 * It belongs here rather than in a component because the same shot is rendered
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

/**
 * The shot page's Review card, as a fragment, for a link that promises the
 * review: landing at the top of a long page would leave the reader to find it.
 */
export const REVIEW_ANCHOR = "review";

// ── numbers ──────────────────────────────────────────────────────────

export function formatTime(value: string | null | undefined): string {
  // A shot from a machine whose clock never synced has no timestamp at all
  // (`startEpoch < 10000`); saying so is better than rendering 1970.
  if (!value) return "no clock";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/**
 * A shot's time as the list shows it: short enough for a narrow column.
 *
 * Day, short month and time for a shot from this year; day, short month and
 * year — no time — for an older one. The list is a year deep for most people,
 * so the year is noise on nearly every row and signal on the few where it
 * appears; and once a shot is from another year the minute it was pulled is
 * the least useful thing about when. The full timestamp goes in the cell's
 * `title` (`formatTime`), so nothing is lost, only folded.
 *
 * `now` is a parameter so the current-year rule can be tested on a fixed day.
 */
export function formatListTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "no clock";
  const date = new Date(value);
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleString(undefined, {
      day: "numeric",
      month: "short",
      hour: "numeric",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
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
