import type { BeanRow, SetDetailData, SetRow, SetVersionDetail, SetVersionRow } from "@/api/types";

/**
 * The vocabulary the Set and bean pages share: how a recipe is written down.
 *
 * Here rather than in a component for the same reason `lib/shots.ts` exists: a
 * dose that reads "18 g" on the Set card and "18.0g" on the timeline is drift
 * nobody notices until they are comparing two versions side by side.
 */

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
  // The profile's temperature, not the Set's: a version records none of its
  // own, so this line follows whichever profile the version names.
  if (version.profile_temperature_c) parts.push(`${version.profile_temperature_c} °C`);
  return parts.join(" · ") || "nothing recorded yet";
}

/**
 * How a version's shots were labelled, on one line: "2 Keep · 1 Improve".
 *
 * Only the counts that are not zero, and the unlabelled ones last and in lower
 * case: they are the work still to do, not a verdict anybody gave. Empty when
 * there are no shots, so the caller renders nothing rather than "0 Keep".
 */
export function labelSummary(labels: SetVersionDetail["labels"]): string {
  const parts: string[] = [];
  if (labels.keep) parts.push(`${labels.keep} Keep`);
  if (labels.improve) parts.push(`${labels.improve} Improve`);
  if (labels.discard) parts.push(`${labels.discard} Discard`);
  if (labels.unlabelled) parts.push(`${labels.unlabelled} not labelled`);
  return parts.join(" · ");
}

/**
 * "6 of 10 predictions held", or nothing at all.
 *
 * Nothing until something has been graded: a track record of zero out of zero
 * is not a modest score, it is an absence, and rendering it as one would make
 * every new Set look like a failure.
 */
export function trackRecordSentence(record: SetDetailData["track_record"]): string | null {
  if (record.graded === 0) return null;
  const word = record.graded === 1 ? "prediction" : "predictions";
  return `${record.held} of ${record.graded} ${word} held`;
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
 * The roasters (or origins) already recorded, for the bean form to suggest.
 *
 * Archived beans count: a coffee you stopped buying was still roasted by
 * somebody you may buy from again. Values are trimmed and compared without
 * case, so "Square Mile" and "square mile " are one suggestion, spelled the way
 * most beans spell it (ties go to the spelling first in code-point order, so
 * the answer does not depend on the order the beans arrived in). Sorted
 * alphabetically, without case.
 */
export function beanFieldSuggestions(
  beans: readonly Pick<BeanRow, "roaster" | "origin">[],
  field: "roaster" | "origin",
): string[] {
  const spellings = new Map<string, Map<string, number>>();
  for (const bean of beans) {
    const value = bean[field]?.trim();
    if (!value) continue;
    const key = value.toLowerCase();
    const counts = spellings.get(key) ?? new Map<string, number>();
    counts.set(value, (counts.get(value) ?? 0) + 1);
    spellings.set(key, counts);
  }
  const chosen = [...spellings.values()].map((counts) => {
    let best = "";
    let bestCount = 0;
    for (const [spelling, count] of counts) {
      if (count > bestCount || (count === bestCount && spelling < best)) {
        best = spelling;
        bestCount = count;
      }
    }
    return best;
  });
  return chosen.sort((a, b) => {
    const byName = a.toLowerCase().localeCompare(b.toLowerCase());
    return byName !== 0 ? byName : a < b ? -1 : a > b ? 1 : 0;
  });
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
