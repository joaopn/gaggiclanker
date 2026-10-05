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

/**
 * One decimal, rounding an exact tie to the even digit as the server does
 * (Python's `format`): 33.25 is 33.2, where `toFixed` says 33.3. The page says the
 * shot's time from the served field and the list says it from the row's
 * milliseconds, and the two must not disagree about the same shot.
 *
 * `toFixed` rounds the number's real binary value, as Python does, so it differs
 * only on an exact tie, and the only one-decimal ties a double can hold exactly
 * end in .25 or .75 (0.35 is not a tie: it is a hair above). Those are found with
 * exact arithmetic (a multiple of 4 is exact) and rounded to even by hand. `Intl`'s
 * `halfEven` is not the same thing: it rounds the shortest decimal text, so it
 * calls 36.45 a tie where Python, and this, round it up.
 */
function oneDecimal(value: number): string {
  if (Number.isInteger(value * 4) && !Number.isInteger(value * 2)) {
    const down = Math.floor(value * 10);
    return ((down % 2 === 0 ? down : down + 1) / 10).toFixed(1);
  }
  return value.toFixed(1);
}

export function formatSeconds(ms: number | null | undefined): string {
  return ms == null ? "—" : `${oneDecimal(ms / 1000)} s`;
}

export function formatGrams(value: number | null | undefined): string {
  return value == null ? "—" : `${oneDecimal(value)} g`;
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
