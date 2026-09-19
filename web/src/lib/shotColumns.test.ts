import { beforeEach, describe, expect, it } from "vitest";
import {
  clampWidth,
  DEFAULT_SHOT_COLUMNS,
  fixedSize,
  gridTemplates,
  loadShotColumns,
  loadShotWidths,
  PREVIOUS_DEFAULT_SHOT_COLUMNS,
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

  it("shows Decision by default, and Flags only to somebody who asks", () => {
    expect(DEFAULT_SHOT_COLUMNS).toContain("decision");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("flags");
    expect(SHOT_COLUMNS.map((column) => column.id)).not.toContain("analyze");
  });

  it("gives somebody who chose the Analyse button its replacement in its place", () => {
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "rating", "analyze", "flags"]),
    );
    expect(loadShotColumns()).toEqual(["time", "rating", "decision", "flags"]);
    // The old default, Analyse included, is the new default.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["set", "time", "duration", "yield", "score", "rating", "analyze"]),
    );
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it("reads a stored copy of the previous default as the new default", () => {
    // Stored by somebody who toggled a column and put it back: what they had
    // was what they were given, so they get what is given now.
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(PREVIOUS_DEFAULT_SHOT_COLUMNS));
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    // In whatever order it was stored: the order of a stored list means nothing.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify([...PREVIOUS_DEFAULT_SHOT_COLUMNS].reverse()),
    );
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it.each([
    ["one column fewer", ["time", "duration", "score", "rating", "set", "flags"]],
    ["one column more", ["time", "duration", "yield", "score", "rating", "set", "notes", "flags"]],
    ["Flags without the Set", ["time", "duration", "yield", "score", "rating", "flags"]],
    [
      "the default with a duplicate",
      ["time", "duration", "yield", "score", "rating", "set", "set"],
    ],
  ])("keeps any other stored choice as it was made: %s", (_why, stored) => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(stored));
    expect(loadShotColumns()).toEqual(stored);
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
    const { wide } = gridTemplates(visibleColumns(["time", "profile"]));
    expect(wide).toBe("7.5rem minmax(8rem,1fr)");
  });

  it("makes the Set a fixed column that can be dragged narrower than it was", () => {
    const set = SHOT_COLUMNS.find((column) => column.id === "set");
    const size = set ? fixedSize(set) : null;
    expect(size).toEqual({ rem: 8, min: 4, max: 20 });
    expect(gridTemplates(visibleColumns(["set", "time"]), { set: 2 }).wide).toBe(
      "4rem 7.5rem minmax(0,1fr)",
    );
  });

  it("sizes Decision for its three words", () => {
    const decision = SHOT_COLUMNS.find((column) => column.id === "decision");
    expect(decision && fixedSize(decision)).toEqual({ rem: 10.5, min: 9.75, max: 14 });
  });

  it("covers every column the chooser offers", () => {
    const all = SHOT_COLUMNS.map((column) => column.id);
    expect(gridTemplates(visibleColumns(all)).wide.split(" ")).toHaveLength(all.length);
  });

  it("draws a fixed column at the reader's width, clamped to its bounds", () => {
    const columns = visibleColumns(["profile", "time", "score"]);
    expect(gridTemplates(columns, { time: 9.25 }).wide).toBe("9.25rem minmax(8rem,1fr) 3.25rem");
    // A stored width from a looser release is still drawn inside today's bounds.
    expect(gridTemplates(columns, { time: 1, score: 99 }).wide).toBe(
      "4.5rem minmax(8rem,1fr) 6rem",
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
        profile: 12,
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
