import { beforeEach, describe, expect, it } from "vitest";
import {
  clampWidth,
  DEFAULT_SHOT_COLUMNS,
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
  it("shows Profile from the first visit, and leaves Curve and Notes to the chooser", () => {
    // What was brewed belongs on the row that is scanned. The two left off are
    // the expensive one (a sparkline is a request and a canvas per row) and
    // the long one (a note is for reading a session back, not for scanning).
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    expect(DEFAULT_SHOT_COLUMNS).toContain("profile");
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
    // Mapped first, so a stored generation that named Analyse is still read as
    // the generation it is, and upgraded whole.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["set", "time", "duration", "yield", "score", "rating", "analyze"]),
    );
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it.each(PREVIOUS_DEFAULT_SHOT_COLUMNS.map((generation, index) => [index, generation]))(
    "reads a stored copy of default generation %i as the current default",
    (_index, generation) => {
      // Stored by somebody who toggled a column and put it back: what they had
      // was what they were given, so they get what is given now. Every
      // generation is kept, because a browser that has not been opened since
      // an older one was current is exactly the browser this is for.
      window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(generation));
      expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
      // In whatever order it was stored: the order of a stored list means nothing.
      window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify([...generation].reverse()));
      expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    },
  );

  it("does not read a generation as the default once it is the default again", () => {
    // The current default is not in the list of previous ones, so a reader who
    // ticks their way back to exactly today's columns has made a choice and it
    // is kept as a choice — there is nothing to upgrade it to.
    expect(PREVIOUS_DEFAULT_SHOT_COLUMNS).not.toContainEqual(DEFAULT_SHOT_COLUMNS);
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
    // Three tracks and the empty one that takes the rest of the row.
    expect(wide.split(" ")).toEqual(["7.5rem", "6rem", "4rem", "minmax(0,1fr)"]);
    // Curve is hidden on a phone, and a hidden grid item still takes its track:
    // the narrow template has to be short, not the cells hidden.
    expect(narrow.split(" ")).toEqual(["7.5rem", "4rem", "minmax(0,1fr)"]);
  });

  it("sizes the text columns too, and leaves the spare room after the last one", () => {
    // Profile used to be `minmax(8rem,1fr)` and swallowed whatever the row had
    // left, which is also why it had no edge to drag. Every column is a width
    // now, and the leftover is a track of its own at the end.
    const { wide } = gridTemplates(visibleColumns(["time", "profile"]));
    expect(wide).toBe("7.5rem 9rem minmax(0,1fr)");
  });

  it("makes the Set a column that can be dragged narrower than it was", () => {
    const set = SHOT_COLUMNS.find((column) => column.id === "set");
    expect(set?.size).toEqual({ rem: 8, min: 4, max: 20 });
    expect(gridTemplates(visibleColumns(["set", "time"]), { set: 2 }).wide).toBe(
      "4rem 7.5rem minmax(0,1fr)",
    );
  });

  it("sizes Decision for its three words and no wider", () => {
    const decision = SHOT_COLUMNS.find((column) => column.id === "decision");
    expect(decision?.size).toEqual({ rem: 9.75, min: 9.75, max: 14 });
  });

  it("covers every column the chooser offers, plus the leftover track", () => {
    const all = SHOT_COLUMNS.map((column) => column.id);
    expect(gridTemplates(visibleColumns(all)).wide.split(" ")).toHaveLength(all.length + 1);
  });

  it("draws a column at the reader's width, clamped to its bounds", () => {
    const columns = visibleColumns(["profile", "time", "score"]);
    expect(gridTemplates(columns, { time: 9.25 }).wide).toBe("9.25rem 9rem 4rem minmax(0,1fr)");
    // A stored width from a looser release is still drawn inside today's bounds.
    expect(gridTemplates(columns, { time: 1, score: 99 }).wide).toBe(
      "4.5rem 9rem 6rem minmax(0,1fr)",
    );
  });

  it("sizes Time for the compact format, not the long one it used to show", () => {
    const time = SHOT_COLUMNS.find((column) => column.id === "time");
    expect(time?.size.rem).toBe(7.5);
  });
});

describe("column sizes", () => {
  it("gives every column a width, so every column has an edge to drag", () => {
    // Profile and Notes were flexible tracks, and were the only two columns a
    // reader could not resize — the two columns of text, where a width is
    // wanted most.
    for (const column of SHOT_COLUMNS) {
      expect(column.size.rem, column.id).toBeGreaterThan(0);
      expect(column.size.min, column.id).toBeLessThanOrEqual(column.size.rem);
      expect(column.size.max, column.id).toBeGreaterThan(column.size.rem);
    }
  });

  it("starts each column at what fits it, not at a share of the row", () => {
    // The defaults are measurements, and the reasoning for each is beside it
    // in `shotColumns.ts`: the sparkline's 96 px, five 16 px stars, three
    // words side by side, a heading plus the sort arrow it grows when it is
    // the column being sorted by.
    const widths = Object.fromEntries(SHOT_COLUMNS.map((column) => [column.id, column.size.rem]));
    expect(widths).toEqual({
      set: 8,
      time: 7.5,
      profile: 9,
      curve: 6,
      duration: 5.25,
      yield: 3,
      score: 4,
      rating: 5.5,
      notes: 14,
      decision: 9.75,
      flags: 9,
    });
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
        // Profile is a column with a width like any other now.
        profile: 12,
        // Not a column any more.
        vibes: 5,
        // Not a number.
        score: "wide",
        // Outside the bounds: clamped, not discarded.
        flags: 400,
      }),
    );
    expect(loadShotWidths()).toEqual({ time: 8, profile: 12, flags: 24 });
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
