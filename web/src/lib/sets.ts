import type {
  BeanRow,
  EvidenceCounts,
  MeasureEvidence,
  MeasureSpread,
  MeasureTerm,
  SetDetailData,
  SetRow,
  SetVersionDetail,
  SetVersionRow,
} from "@/api/types";

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

// ── the spread and the evidence ──────────────────────────────────────

/**
 * A number at the precision the server says the measure is read in, with its
 * unit.
 *
 * `toFixed` rather than the value as it arrives: the server has already
 * rounded, and printing "2" where it served 2.0 loses the one digit that says
 * how precisely the number is meant. The slug-only fallback is what shows in
 * the moment before `/api/vocab` answers.
 */
export function readingOf(value: number, term?: MeasureTerm): string {
  const text = term ? value.toFixed(term.decimals) : String(value);
  return term?.unit ? `${text} ${term.unit}` : text;
}

/**
 * A difference or a yardstick: one decimal finer than the means it came from.
 *
 * Which is the whole point of the extra digit — "+2.04 s, beyond 2.00 s" says
 * what the arithmetic found, where "+2.0 s, beyond 2.0 s" reads as a
 * contradiction of its own verdict. The floor on the spread line is a yardstick
 * too, and is written the same way.
 */
export function yardstickOf(value: number, term?: MeasureTerm): string {
  const text = term ? value.toFixed(term.difference_decimals) : String(value);
  return term?.unit ? `${text} ${term.unit}` : text;
}

/**
 * One line of the Spread block, in one of its two states.
 *
 * "Shot time ±1.8 s · from 9 repeat shots of 3 recipes" once there are enough
 * repeats to trust, and the floor-based sentence until then. The second one
 * names the floor rather than the unmeasured figure on purpose: the figure is
 * served and is real, but it is not what a difference is being held against
 * yet, and a reader shown "±0.4 s" would take it for the answer.
 *
 * The recipe count is the basis the reader can judge: nine shots of one recipe
 * and nine of eight recipes are very different evidence. It is the number of
 * repeat groups that contributed, which is the shots minus the degrees of
 * freedom — one degree is spent on each group's own average.
 */
export function spreadSentence(entry: MeasureSpread, term?: MeasureTerm): string {
  const label = term?.label ?? entry.measure;
  if (entry.measured && entry.value != null) {
    const recipes = entry.shots - entry.degrees_of_freedom;
    const shots = `${entry.shots} repeat shot${entry.shots === 1 ? "" : "s"}`;
    return `${label} ±${readingOf(entry.value, term)} · from ${shots} of ${recipes} recipe${
      recipes === 1 ? "" : "s"
    }`;
  }
  return `${label}: not measured yet · differences under ${yardstickOf(entry.floor, term)} are not counted`;
}

/** A side's mean and how many shots it rests on: "34.2 s · 3 shots". */
export function meanCell(side: MeasureEvidence["this"], term?: MeasureTerm): string {
  if (side.mean == null) return "not recorded";
  return `${readingOf(side.mean, term)} · ${side.n} shot${side.n === 1 ? "" : "s"}`;
}

/** The difference, signed, because the direction is half of what was claimed. */
export function differenceCell(row: MeasureEvidence, term?: MeasureTerm): string {
  if (row.difference == null) return "—";
  const body = yardstickOf(row.difference, term);
  return row.difference > 0 ? `+${body}` : body;
}

/**
 * The verdict in words, with what the difference was held against.
 *
 * Words rather than a colour: this is the one cell a reader acts on, and a
 * green tick is unreadable to a person who cannot tell it from the red one.
 */
export function verdictSentence(row: MeasureEvidence, term?: MeasureTerm): string {
  if (row.verdict === "no_data") return "no data";
  const held = row.yardstick == null ? "" : ` · held against ${yardstickOf(row.yardstick, term)}`;
  return `${row.verdict === "beyond" ? "beyond the spread" : "inside the spread"}${held}`;
}

/**
 * One side's plain facts, zeros left out: how the cups went and how they were
 * labelled. No Discard, because a discarded shot is not counted at all.
 */
export function evidenceSideSummary(side: EvidenceCounts): string {
  const parts = [`${side.shots} shot${side.shots === 1 ? "" : "s"}`];
  if (side.sour) parts.push(`${side.sour} sour`);
  if (side.balanced) parts.push(`${side.balanced} balanced`);
  if (side.bitter) parts.push(`${side.bitter} bitter`);
  if (side.keep) parts.push(`${side.keep} Keep`);
  if (side.improve) parts.push(`${side.improve} Improve`);
  if (side.unlabelled) parts.push(`${side.unlabelled} not labelled`);
  return parts.join(" · ");
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

/** A bean's taste scales, in the order every screen lists them. */
export const BEAN_SCALES = ["acidity", "intensity", "sweetness"] as const;

/**
 * The taste scales a bean has, as short labels ("acidity 4/5"); an unset one
 * is left out rather than shown as "not stated".
 */
export function beanScaleLabels(
  bean: Pick<BeanRow, "acidity" | "intensity" | "sweetness">,
): string[] {
  return BEAN_SCALES.flatMap((scale) => {
    const value = bean[scale];
    return value == null ? [] : [`${scale} ${value}/5`];
  });
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
