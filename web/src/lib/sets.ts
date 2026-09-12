import type { BeanRow, SetRow, SetVersionRow } from "@/api/types";
import type { Tone } from "@/lib/shots";

/**
 * The vocabulary the Set and bean pages share: freshness, and how a recipe is
 * written down.
 *
 * Here rather than in a component for the same reason `lib/shots.ts` exists: a
 * dose that reads "18 g" on the Set card and "18.0g" on the timeline is drift
 * nobody notices until they are comparing two versions side by side.
 */

// ── freshness ────────────────────────────────────────────────────────

/**
 * Whole days since the roast, or null when the bag does not say.
 *
 * Computed against local midnight on both sides rather than as an elapsed
 * duration: a bag roasted yesterday afternoon is "1 day off roast" all of
 * today, not "0" until the afternoon. Coffee is talked about in days, and a
 * counter that flipped over at 15:42 would look broken.
 */
export function daysOffRoast(
  roastDate: string | null | undefined,
  now = new Date(),
): number | null {
  if (!roastDate) return null;
  const roasted = new Date(`${roastDate}T00:00:00`);
  if (Number.isNaN(roasted.getTime())) return null;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((today.getTime() - roasted.getTime()) / 86_400_000);
  return days;
}

/**
 * What those days mean, in one word plus a sentence.
 *
 * The windows are the ordinary espresso ones (the knowledge tier is seeded with
 * the same numbers): a few days of rest before
 * the CO2 has gone, a broad useful plateau, and a slow decline after a month.
 * They are advice, not a rule — a light roast wants longer and this pill does
 * not know the roast level — so the wording hedges where the knowledge tier
 * will later be specific.
 */
export function freshness(days: number | null): { label: string; tone: Tone; meaning: string } {
  if (days === null) return { label: "no roast date", tone: "neutral", meaning: "" };
  if (days < 0) {
    return {
      label: "roasted in the future",
      tone: "warn",
      meaning: "The roast date is ahead of today — probably a typo.",
    };
  }
  if (days <= 3) {
    return {
      label: `${days}d — resting`,
      tone: "warn",
      meaning: "Still degassing. Expect gushing and a thin, sharp cup for a few more days.",
    };
  }
  if (days <= 21) {
    return {
      label: `${days}d — ready`,
      tone: "good",
      meaning: "In the window where a dial-in holds still from one day to the next.",
    };
  }
  if (days <= 45) {
    return {
      label: `${days}d — going quiet`,
      tone: "warn",
      meaning: "Past its best: the aromatics fade first, so the cup flattens before it turns.",
    };
  }
  return {
    label: `${days}d — stale`,
    tone: "bad",
    meaning: "Old enough that the beans, not the recipe, are what the cup tastes of.",
  };
}

// ── recipes ──────────────────────────────────────────────────────────

/** A dose and a target yield as a ratio, when both are known. */
export function versionRatio(version: Pick<SetVersionRow, "dose_g" | "target_yield_g">) {
  if (!version.dose_g || !version.target_yield_g) return null;
  return `1:${(version.target_yield_g / version.dose_g).toFixed(1)}`;
}

/** The recipe on one line, for a card that has no room for a table. */
export function versionSummary(version: SetVersionRow): string {
  const parts: string[] = [];
  if (version.dose_g) parts.push(`${version.dose_g} g in`);
  if (version.target_yield_g) parts.push(`${version.target_yield_g} g out`);
  if (version.grind_setting) parts.push(`grind ${version.grind_setting}`);
  if (version.target_temperature_c) parts.push(`${version.target_temperature_c} °C`);
  return parts.join(" · ") || "nothing recorded yet";
}

/** The Set's identity on one line: bean · grinder · profile vN. */
export function setSummary(row: SetRow): string {
  const parts = [row.bean_name ?? `bean #${row.bean_id}`];
  if (row.grinder_name) parts.push(row.grinder_name);
  parts.push(
    row.profile_label
      ? `${row.profile_label} v${row.current_version_no}`
      : `v${row.current_version_no}`,
  );
  return parts.join(" · ");
}

/** How a bean reads in a picker: the name, and the roaster when there is one. */
export function beanLabel(bean: Pick<BeanRow, "name" | "roaster">): string {
  return bean.roaster ? `${bean.name} — ${bean.roaster}` : bean.name;
}

/**
 * A grind reading as the two fields a version stores.
 *
 * `grind_setting` is the text a person reads back ("22", "between 3 and 4") and
 * `grind_value` is the same thing as a number when there is one, which is what
 * the chart can plot. They are written together or not at all: a version
 * carrying the text and not the number is a version the trend line skips, and
 * that is the sort of gap nobody attributes to a missing field.
 *
 * Both callers that create a version go through this — the Set page's form and
 * the shot page's "new version from this shot" — so they cannot drift.
 */
export function grindPatch(reading: string | null | undefined): {
  grind_setting?: string;
  grind_value?: number;
} {
  const text = (reading ?? "").trim();
  if (!text) return {};
  const value = Number.parseFloat(text);
  return {
    grind_setting: text,
    ...(Number.isFinite(value) && value >= 0 ? { grind_value: value } : {}),
  };
}
