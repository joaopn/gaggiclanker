import type { ShotListParams, ShotSort } from "@/api/types";
import { SCORE_BANDS, type ScoreBandValue } from "@/lib/shots";

/**
 * The filter bar's state, and how it becomes a query string.
 *
 * Kept apart from the component because it is the half with rules in it — a
 * date turning into an instant, a band turning into two numbers — and because
 * the list page needs the same conversion to build its query key.
 *
 * Everything is a string, including the numbers. That is what an `<input>` and
 * a `<select>` deal in, and keeping one representation means there is no
 * "empty string or undefined or zero" question at every call site; `toParams`
 * is the single place where empty means "no filter".
 */
export type ShotFilterState = {
  /** `yyyy-mm-dd`, as a date input spells it. */
  from: string;
  to: string;
  profileVersionId: string;
  /**
   * "" any Set, "needs" the inbox, otherwise a Set id.
   *
   * One control for two different server parameters (`needs_set` and `set_id`)
   * because to a reader they are one question — "which Set?" — and "not in one
   * yet" is one of its answers.
   */
  set: string;
  scoreBand: ScoreBandValue;
  minRating: string;
  source: "" | "device" | "import";
  /** "" any, "yes" only quarantined, "no" only readable. */
  quarantined: "" | "yes" | "no";
  sort: ShotSort;
  order: "asc" | "desc";
};

export const DEFAULT_FILTERS: ShotFilterState = {
  from: "",
  to: "",
  profileVersionId: "",
  set: "",
  scoreBand: "any",
  minRating: "",
  source: "",
  quarantined: "",
  sort: "started_at",
  order: "desc",
};

export function isDefaultFilters(state: ShotFilterState): boolean {
  return (Object.keys(DEFAULT_FILTERS) as Array<keyof ShotFilterState>).every(
    (key) => state[key] === DEFAULT_FILTERS[key],
  );
}

/**
 * The keys that narrow the list. Sorting is not one of them: it changes the
 * order of an answer, not which shots are in it, and a "3 filters" badge that
 * counted "newest first" would be lying about why rows are missing.
 */
const FILTER_KEYS: Array<keyof ShotFilterState> = [
  "from",
  "to",
  "profileVersionId",
  "set",
  "scoreBand",
  "minRating",
  "source",
  "quarantined",
];

/** How many filters are on — the number on the Filters button's badge. */
export function activeFilterCount(state: ShotFilterState): number {
  return FILTER_KEYS.filter((key) => state[key] !== DEFAULT_FILTERS[key]).length;
}

/**
 * A date input gives a day; `started_at` is an instant.
 *
 * The day is read in the browser's own zone and converted, so "shots from the
 * 4th" means the 4th where the machine is, not the 4th in UTC. `to` takes the
 * last millisecond of its day, because a `to` that meant midnight would
 * silently exclude every shot on the day the user picked.
 */
function dayStart(day: string): string | undefined {
  if (!day) return undefined;
  const date = new Date(`${day}T00:00:00`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function dayEnd(day: string): string | undefined {
  if (!day) return undefined;
  const date = new Date(`${day}T23:59:59.999`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/**
 * The filter bar lives in the query string.
 *
 * Two things fall out of that and both matter. A link can carry a filter — the
 * profiles page links a version's shot count straight at the filtered list, and
 * without this that link would land on an unfiltered page. And the back button
 * works: clearing a filter is a navigation, not a state change nobody can undo.
 *
 * Unknown values fall back to the default rather than throwing: a query string
 * is user input, and a typed-in `?sort=nonsense` should show the archive, not
 * an error page.
 */
const SORTS: ShotSort[] = ["started_at", "execution_score", "duration", "rating"];

export function fromSearchParams(params: URLSearchParams): ShotFilterState {
  const scoreBand = params.get("score") ?? "";
  const source = params.get("source") ?? "";
  const quarantined = params.get("quarantined") ?? "";
  const sort = params.get("sort") ?? "";
  const order = params.get("order") ?? "";
  return {
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    profileVersionId: params.get("profile_version_id") ?? "",
    set: params.get("set") ?? "",
    scoreBand: SCORE_BANDS.some((band) => band.value === scoreBand)
      ? (scoreBand as ScoreBandValue)
      : "any",
    minRating: /^[1-5]$/.test(params.get("min_rating") ?? "")
      ? (params.get("min_rating") as string)
      : "",
    source: source === "device" || source === "import" ? source : "",
    quarantined: quarantined === "yes" || quarantined === "no" ? quarantined : "",
    sort: SORTS.includes(sort as ShotSort) ? (sort as ShotSort) : "started_at",
    order: order === "asc" ? "asc" : "desc",
  };
}

export function toSearchParams(state: ShotFilterState): URLSearchParams {
  const params = new URLSearchParams();
  // Only what differs from the default, so an unfiltered list has a clean URL
  // and "is anything filtered" is visible in the address bar.
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.profileVersionId) params.set("profile_version_id", state.profileVersionId);
  if (state.set) params.set("set", state.set);
  if (state.scoreBand !== "any") params.set("score", state.scoreBand);
  if (state.minRating) params.set("min_rating", state.minRating);
  if (state.source) params.set("source", state.source);
  if (state.quarantined) params.set("quarantined", state.quarantined);
  if (state.sort !== "started_at") params.set("sort", state.sort);
  if (state.order !== "desc") params.set("order", state.order);
  return params;
}

export function toParams(state: ShotFilterState, limit: number): ShotListParams {
  const band = SCORE_BANDS.find((entry) => entry.value === state.scoreBand);
  const rating = Number.parseInt(state.minRating, 10);
  const profileVersionId = Number.parseInt(state.profileVersionId, 10);
  const setId = Number.parseInt(state.set, 10);
  return {
    limit,
    from: dayStart(state.from),
    to: dayEnd(state.to),
    profile_version_id: Number.isFinite(profileVersionId) ? profileVersionId : undefined,
    // "needs" is not a Set id, so the two never travel together.
    needs_set: state.set === "needs" ? true : undefined,
    set_id: Number.isFinite(setId) ? setId : undefined,
    min_score: band && "min" in band ? band.min : undefined,
    max_score: band && "max" in band ? band.max : undefined,
    min_rating: Number.isFinite(rating) ? rating : undefined,
    source: state.source || undefined,
    quarantined: state.quarantined === "" ? undefined : state.quarantined === "yes",
    sort: state.sort,
    order: state.order,
  };
}
