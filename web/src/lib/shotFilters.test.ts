import { describe, expect, it } from "vitest";
import {
  activeFilterCount,
  DEFAULT_FILTERS,
  fromSearchParams,
  isDefaultFilters,
  type ShotFilterState,
  toParams,
  toSearchParams,
} from "@/lib/shotFilters";

describe("activeFilterCount", () => {
  it("counts nothing when nothing is filtered", () => {
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0);
  });

  it("counts one per narrowed field", () => {
    expect(
      activeFilterCount({ ...DEFAULT_FILTERS, set: "needs", minRating: "4", source: "import" }),
    ).toBe(3);
  });

  it("does not count the sort", () => {
    // The badge answers "why am I seeing so few rows". An order is not a
    // reason, and counting it would send somebody looking for a filter that
    // does not exist.
    expect(activeFilterCount({ ...DEFAULT_FILTERS, sort: "execution_score", order: "asc" })).toBe(
      0,
    );
  });
});

describe("toParams", () => {
  it("sends nothing but the page size when nothing is filtered", () => {
    expect(toParams(DEFAULT_FILTERS, 50)).toEqual({
      limit: 50,
      from: undefined,
      to: undefined,
      profile_version_id: undefined,
      min_score: undefined,
      max_score: undefined,
      min_rating: undefined,
      source: undefined,
      quarantined: undefined,
      sort: "started_at",
      order: "desc",
    });
  });

  it("turns a score band into the two bounds the API takes", () => {
    expect(toParams({ ...DEFAULT_FILTERS, scoreBand: "clean" }, 50)).toMatchObject({
      min_score: 8,
      max_score: undefined,
    });
    expect(toParams({ ...DEFAULT_FILTERS, scoreBand: "minor" }, 50)).toMatchObject({
      min_score: 6.5,
      max_score: 8,
    });
    expect(toParams({ ...DEFAULT_FILTERS, scoreBand: "poor" }, 50)).toMatchObject({
      min_score: undefined,
      max_score: 5,
    });
  });

  it("gives `to` the whole of its day", () => {
    // A `to` that meant midnight would silently exclude every shot on the day
    // the user picked, which is the one day they definitely meant to include.
    const params = toParams({ ...DEFAULT_FILTERS, from: "2026-03-04", to: "2026-03-04" }, 50);

    expect(params.from).toBeDefined();
    expect(params.to).toBeDefined();
    const span =
      new Date(params.to as string).getTime() - new Date(params.from as string).getTime();
    expect(span).toBeCloseTo(24 * 60 * 60 * 1000 - 1, -2);
  });

  it("distinguishes `any` from `only readable`", () => {
    expect(toParams(DEFAULT_FILTERS, 50).quarantined).toBeUndefined();
    expect(toParams({ ...DEFAULT_FILTERS, quarantined: "no" }, 50).quarantined).toBe(false);
    expect(toParams({ ...DEFAULT_FILTERS, quarantined: "yes" }, 50).quarantined).toBe(true);
  });

  it("knows when it is back to the default", () => {
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, minRating: "4" })).toBe(false);
  });
});

describe("the query string", () => {
  it("keeps an unfiltered list's URL clean", () => {
    expect(toSearchParams(DEFAULT_FILTERS).toString()).toBe("");
  });

  it("round-trips every filter", () => {
    const state: ShotFilterState = {
      from: "2026-03-01",
      to: "2026-03-04",
      profileVersionId: "7",
      set: "3",
      version: "22",
      scoreBand: "faulted",
      minRating: "3",
      source: "import",
      quarantined: "yes",
      sort: "execution_score",
      order: "asc",
    };

    expect(fromSearchParams(toSearchParams(state))).toEqual(state);
  });

  it("reads the link the profiles page writes", () => {
    // `/shots?profile_version_id=7` is a real link from a version's shot count,
    // and landing on an unfiltered list would make it a lie.
    const state = fromSearchParams(new URLSearchParams("profile_version_id=7"));

    expect(state.profileVersionId).toBe("7");
    expect(toParams(state, 50).profile_version_id).toBe(7);
  });

  it("reads the link a version's shot count writes", () => {
    // `/shots?set=3&version=22` is the Set page's log linking at *this*
    // version's shots, and the count beside the link is that version's.
    const state = fromSearchParams(new URLSearchParams("set=3&version=22"));

    expect(state.set).toBe("3");
    expect(state.version).toBe("22");
    const params = toParams(state, 50);
    expect(params.set_id).toBe(3);
    expect(params.set_version_id).toBe(22);
  });

  it("ignores a version with no Set beside it", () => {
    // A version id alone would narrow the list to a Set the filter bar is not
    // showing: an empty archive with no visible reason.
    const state = fromSearchParams(new URLSearchParams("version=22"));

    expect(state.version).toBe("");
    expect(toParams(state, 50).set_version_id).toBeUndefined();
    expect(toSearchParams(state).toString()).toBe("");
  });

  it("ignores a version on the inbox, which has no versions", () => {
    const state = fromSearchParams(new URLSearchParams("set=needs&version=22"));

    expect(state.version).toBe("");
    expect(toParams(state, 50).set_version_id).toBeUndefined();
  });

  it("never sends a version on its own, even from state built by hand", () => {
    // `toParams` is called with state the filter bar did not build, so the
    // rule lives there as well as in the parser.
    expect(toParams({ ...DEFAULT_FILTERS, version: "22" }, 50).set_version_id).toBeUndefined();
  });

  it("counts the version as a reason there are few rows", () => {
    expect(activeFilterCount({ ...DEFAULT_FILTERS, set: "3", version: "22" })).toBe(2);
  });

  it("falls back to the default for nonsense rather than failing", () => {
    // A query string is user input; a typed-in `?sort=nonsense` should show the
    // archive, not an error page.
    const state = fromSearchParams(
      new URLSearchParams("sort=nonsense&score=brilliant&min_rating=9&source=telepathy"),
    );

    expect(state).toEqual(DEFAULT_FILTERS);
  });
});
