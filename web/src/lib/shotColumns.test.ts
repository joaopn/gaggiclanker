import { beforeEach, describe, expect, it } from "vitest";
import {
  clampWidth,
  DEFAULT_SHOT_COLUMNS,
  fixedSize,
  gridTemplates,
  loadShotColumns,
  loadShotWidths,
  SHOT_COLUMNS,
  SHOT_COLUMNS_KEY,
  SHOT_WIDTHS_KEY,
  type ShotColumnId,
  saveShotColumns,
  saveShotWidths,
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
    // Three fixed tracks and the empty one that takes the rest of the row.
    expect(wide.split(" ")).toEqual(["7.5rem", "6.5rem", "3.25rem", "minmax(0,1fr)"]);
    // Curve is hidden on a phone, and a hidden grid item still takes its track:
    // the narrow template has to be short, not the cells hidden.
    expect(narrow.split(" ")).toEqual(["7.5rem", "3.25rem", "minmax(0,1fr)"]);
  });

  it("adds no filler track when a flexible column already takes the rest", () => {
    const { wide } = gridTemplates(visibleColumns(["set", "time"]));
    expect(wide).toBe("minmax(8rem,1fr) 7.5rem");
  });

  it("covers every column the chooser offers", () => {
    const all = SHOT_COLUMNS.map((column) => column.id);
    expect(gridTemplates(visibleColumns(all)).wide.split(" ")).toHaveLength(all.length);
  });

  it("draws a fixed column at the reader's width, clamped to its bounds", () => {
    const columns = visibleColumns(["set", "time", "score"]);
    expect(gridTemplates(columns, { time: 9.25 }).wide).toBe("minmax(8rem,1fr) 9.25rem 3.25rem");
    // A stored width from a looser release is still drawn inside today's bounds.
    expect(gridTemplates(columns, { time: 1, score: 99 }).wide).toBe(
      "minmax(8rem,1fr) 4.5rem 6rem",
    );
  });

  it("sizes Time for the compact format, not the long one it used to show", () => {
    const time = SHOT_COLUMNS.find((column) => column.id === "time");
    expect(time && fixedSize(time)?.rem).toBe(7.5);
  });
});

describe("column widths", () => {
  it("round-trips the widths, and forgets the key once nothing is customised", () => {
    saveShotWidths({ time: 9, flags: 12 });
    expect(loadShotWidths()).toEqual({ time: 9, flags: 12 });
    saveShotWidths({});
    expect(window.localStorage.getItem(SHOT_WIDTHS_KEY)).toBeNull();
    expect(loadShotWidths()).toEqual({});
  });

  it("keeps each good width and drops each bad one on its own", () => {
    window.localStorage.setItem(
      SHOT_WIDTHS_KEY,
      JSON.stringify({
        time: 8,
        // A flexible column has no width to store.
        set: 12,
        // Not a column any more.
        vibes: 5,
        // Not a number.
        score: "wide",
        // Outside the bounds: clamped, not discarded.
        flags: 400,
      }),
    );
    expect(loadShotWidths()).toEqual({ time: 8, flags: 24 });
  });

  it.each([
    ["not JSON at all", "{{{"],
    ["a list", "[8]"],
    ["null", "null"],
  ])("reads %s as no widths", (_why, stored) => {
    window.localStorage.setItem(SHOT_WIDTHS_KEY, stored);
    expect(loadShotWidths()).toEqual({});
  });

  it("survives storage that throws on every call", () => {
    expect(loadShotWidths(hostile)).toEqual({});
    expect(() => saveShotWidths({ time: 8 }, hostile)).not.toThrow();
  });

  it("clamps and rounds", () => {
    const size = { rem: 5, min: 4, max: 6 };
    expect(clampWidth(size, 3)).toBe(4);
    expect(clampWidth(size, 7)).toBe(6);
    expect(clampWidth(size, 5.123456)).toBe(5.12);
  });
});
