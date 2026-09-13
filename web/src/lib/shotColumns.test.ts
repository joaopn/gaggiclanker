import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_SHOT_COLUMNS,
  gridTemplates,
  loadShotColumns,
  SHOT_COLUMNS,
  SHOT_COLUMNS_KEY,
  type ShotColumnId,
  saveShotColumns,
  visibleColumns,
} from "@/lib/shotColumns";

/**
 * The column choice is the one piece of this page's state that lives in the
 * browser rather than in the URL or on the server, so every way storage can
 * let us down ends at the default rather than at a broken table.
 */

/** Storage that throws, the way a private window or blocked site data does. */
const hostile: Storage = {
  length: 0,
  clear: () => {
    throw new Error("nope");
  },
  getItem: () => {
    throw new Error("nope");
  },
  key: () => {
    throw new Error("nope");
  },
  removeItem: () => {
    throw new Error("nope");
  },
  setItem: () => {
    throw new Error("nope");
  },
};

beforeEach(() => {
  window.localStorage.clear();
});

describe("loadShotColumns", () => {
  it("starts with Profile, Curve and Notes off", () => {
    // The change worth pinning: a sparkline is a request and a canvas per row,
    // and the profile name is the same string on nearly every row.
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("profile");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("curve");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("notes");
  });

  it("round-trips a choice", () => {
    saveShotColumns(["time", "notes"]);
    expect(loadShotColumns()).toEqual(["time", "notes"]);
  });

  it("drops ids it does not know rather than rendering them", () => {
    // A column removed in a later release leaves its id in every browser that
    // ever ticked it.
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "espresso-vibes"]));
    expect(loadShotColumns()).toEqual(["time"]);
  });

  it.each([
    ["not JSON at all", "{{{"],
    ["JSON that is not a list", '{"time":true}'],
    ["a list of nothing we know", '["nope"]'],
    ["an empty list", "[]"],
  ])("falls back to the default for %s", (_why, stored) => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, stored);
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it("survives storage that throws on every call", () => {
    expect(loadShotColumns(hostile)).toEqual(DEFAULT_SHOT_COLUMNS);
    expect(() => saveShotColumns(["time"], hostile)).not.toThrow();
  });
});

describe("SHOT_COLUMNS", () => {
  it("puts the Set first, in the chooser and in the default table", () => {
    // A session is read Set by Set, so the Set is the column scanned first.
    expect(SHOT_COLUMNS[0].id).toBe("set");
    expect(visibleColumns(DEFAULT_SHOT_COLUMNS)[0].id).toBe("set");
  });
});

describe("visibleColumns", () => {
  it("keeps the canonical order whatever order the ids arrive in", () => {
    // The order carries meaning — when, what, how it went, what you thought —
    // so a column turned off and on again comes back where it was.
    const ids: ShotColumnId[] = ["flags", "time", "score"];
    expect(visibleColumns(ids).map((column) => column.id)).toEqual(["time", "score", "flags"]);
  });

  it("never returns an empty table", () => {
    expect(visibleColumns([]).map((column) => column.id)).toEqual(["time"]);
  });
});

describe("gridTemplates", () => {
  it("gives the narrow breakpoint one track per column that survives it", () => {
    const columns = visibleColumns(["time", "curve", "score"]);
    const { narrow, wide } = gridTemplates(columns);
    expect(wide.split(" ").length).toBe(3);
    // Curve is hidden on a phone, and a hidden grid item still takes its track:
    // the narrow template has to be short, not the cells hidden.
    expect(narrow.split(" ").length).toBe(2);
  });

  it("covers every column the chooser offers", () => {
    const all = SHOT_COLUMNS.map((column) => column.id);
    expect(gridTemplates(visibleColumns(all)).wide.split(" ")).toHaveLength(all.length);
  });
});
