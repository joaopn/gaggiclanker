import { beforeEach, describe, expect, it } from "vitest";
import {
  clampWidth,
  DEFAULT_SHOT_COLUMNS,
  gridTemplates,
  loadShotColumns,
  loadShotWidths,
  PREVIOUS_DEFAULT_SHOT_COLUMNS,
  SHOT_COLUMNS,
  SHOT_COLUMNS_BALANCE_KEY,
  SHOT_COLUMNS_CHECK_KEY,
  SHOT_COLUMNS_DISCARD_KEY,
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
  // Most tests store a layout as a person's own choice; the bringing-along of a saved layout to
  // the new default row has its own describe, which takes the mark away.
  window.localStorage.setItem(SHOT_COLUMNS_BALANCE_KEY, "1");
  window.localStorage.setItem(SHOT_COLUMNS_DISCARD_KEY, "1");
});

describe("loadShotColumns", () => {
  it("leaves Profile, Curve and Notes to the chooser on a first visit", () => {
    // The Recipe already names the profile, since a Recipe brews one. Curve is the
    // expensive one (a sparkline is a request and a canvas per row) and Notes the
    // long one (a note is for reading a session back, not for scanning).
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("profile");
    expect(SHOT_COLUMNS.map((column) => column.id)).toContain("profile");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("curve");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("notes");
  });

  it("shows Rating, Balance and Discard by default, and Flags only to somebody who asks", () => {
    expect(DEFAULT_SHOT_COLUMNS).toEqual(expect.arrayContaining(["rating", "balance", "discard"]));
    expect(SHOT_COLUMNS.map((column) => column.id)).not.toContain("decision");
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("flags");
    expect(SHOT_COLUMNS.map((column) => column.id)).not.toContain("analyze");
  });

  it("gives somebody who chose the Analyse button its replacement in its place", () => {
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "rating", "analyze", "flags"]),
    );
    // Analyse became Decision, and Decision is gone: the stars stand in the same place.
    expect(loadShotColumns()).toEqual(["time", "rating", "flags"]);
    // Mapped first, so a stored generation that named Analyse is still read as
    // the generation it is, and upgraded whole.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["recipe", "time", "duration", "yield", "score", "rating", "analyze"]),
    );
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it("draws the columns in one canonical order, AI Review last but for Flags", () => {
    const ids = SHOT_COLUMNS.map((column) => column.id);
    expect(ids).not.toContain("score");
    expect(ids).toEqual([
      "recipe",
      "drink",
      "time",
      "profile",
      "curve",
      "duration",
      "yield",
      "check",
      "rating",
      "notes",
      "balance",
      "discard",
      "review",
      "flags",
    ]);
    expect(SHOT_COLUMNS.find((column) => column.id === "check")?.label).toBe("Curve check");
    expect(SHOT_COLUMNS.find((column) => column.id === "review")?.label).toBe("AI Review");
  });

  it("starts on the row a reader scans: Rating, Balance and Discard in, Profile out, AI Review last", () => {
    expect(DEFAULT_SHOT_COLUMNS).toEqual([
      "recipe",
      "time",
      "duration",
      "yield",
      "check",
      "rating",
      "balance",
      "discard",
      "review",
    ]);
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("profile");
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it("reads the default of the release before as the default, in whatever order it was stored", () => {
    // An older generation: Review and Rating before Decision, no Balance.
    const before = [
      "recipe",
      "time",
      "profile",
      "duration",
      "yield",
      "check",
      "review",
      "rating",
      "decision",
    ];
    expect(PREVIOUS_DEFAULT_SHOT_COLUMNS[1]).toEqual(before);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(before));
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify([...before].reverse()));
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
  });

  it("keeps a layout somebody chose, Balance or not, as they made it", () => {
    const chosen = ["time", "rating", "discard", "check", "review"];
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(chosen));
    expect(loadShotColumns()).toEqual(chosen);
    // Balance is a column like another: a stored choice may name it, and it is drawn before
    // Discard and AI Review whatever order the list was stored in.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["discard", "balance", "review", "check", "time"]),
    );
    expect(visibleColumns(loadShotColumns()).map((column) => column.id)).toEqual([
      "time",
      "check",
      "balance",
      "discard",
      "review",
    ]);
  });

  it("reads a layout stored before the change, naming score, as review in the same place", () => {
    // The default as it was, with the Score column on.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify([
        "recipe",
        "time",
        "profile",
        "duration",
        "yield",
        "score",
        "rating",
        "decision",
      ]),
    );
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    // A chosen layout keeps its position and its other columns, and a layout
    // that had Score off still has it off.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "score", "rating", "flags"]),
    );
    expect(loadShotColumns()).toEqual(["time", "check", "review", "rating", "flags"]);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "rating", "flags"]));
    expect(loadShotColumns()).toEqual(["time", "rating", "flags"]);
  });

  it("adds Curve check in front of Review in a stored layout that shows Review, once", () => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "review", "flags"]));
    expect(loadShotColumns()).toEqual(["time", "check", "review", "flags"]);
    // Written back, with the mark that says it was done.
    expect(JSON.parse(window.localStorage.getItem(SHOT_COLUMNS_KEY) ?? "[]")).toEqual([
      "time",
      "check",
      "review",
      "flags",
    ]);
    expect(window.localStorage.getItem(SHOT_COLUMNS_CHECK_KEY)).toBe("1");
    // A person who then turns it off (saving the layout) has made a choice: it stays off.
    saveShotColumns(["time", "review", "flags"]);
    expect(loadShotColumns()).toEqual(["time", "review", "flags"]);
  });

  it("keeps every other stored choice: a layout without Review, or already with Curve check", () => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "flags"]));
    expect(loadShotColumns()).toEqual(["time", "flags"]);
    expect(window.localStorage.getItem(SHOT_COLUMNS_CHECK_KEY)).toBeNull();
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["review", "check", "time"]));
    expect(loadShotColumns()).toEqual(["review", "check", "time"]);
  });

  it("adds Curve check in memory when nothing can be stored, and never throws", () => {
    const store = new Map<string, string>([[SHOT_COLUMNS_KEY, JSON.stringify(["time", "review"])]]);
    const readOnly = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: () => {
        throw new Error("full");
      },
    } as unknown as Storage;
    expect(loadShotColumns(readOnly)).toEqual(["time", "check", "review"]);
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

  it("shows the Recipe column where a stored layout had the Set column", () => {
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "set", "rating", "flags"]),
    );
    expect(loadShotColumns()).toEqual(["time", "recipe", "rating", "flags"]);
    // Off stays off: a layout that never had the column does not gain one.
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "rating", "flags"]));
    expect(loadShotColumns()).toEqual(["time", "rating", "flags"]);
  });

  it.each(PREVIOUS_DEFAULT_SHOT_COLUMNS.map((generation, index) => [index, generation]))(
    "reads a copy of default generation %i stored with set as the current default",
    (_index, generation) => {
      const old = generation.map((id) => (id === "recipe" ? "set" : id));
      expect(old).toContain("set");
      window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(old));
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
    ["one column fewer", ["time", "duration", "rating", "recipe", "flags"]],
    ["one column more", ["time", "duration", "yield", "rating", "recipe", "notes", "flags"]],
    ["Flags without the Recipe", ["time", "duration", "yield", "rating", "flags"]],
    ["the default with a duplicate", ["time", "duration", "yield", "rating", "recipe", "recipe"]],
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
  it("puts the Recipe first, in the chooser and in the default table", () => {
    // A session is read Recipe by Recipe, so the Recipe is the column scanned first.
    expect(SHOT_COLUMNS[0].id).toBe("recipe");
    expect(visibleColumns(DEFAULT_SHOT_COLUMNS)[0].id).toBe("recipe");
  });
});

describe("visibleColumns", () => {
  it("keeps the canonical order whatever order the ids arrive in", () => {
    // The order carries meaning — when, what, how it went, what you thought —
    // so a column turned off and on again comes back where it was.
    const ids: ShotColumnId[] = ["flags", "time", "rating"];
    expect(visibleColumns(ids).map((column) => column.id)).toEqual(["time", "rating", "flags"]);
  });

  it("never returns an empty table", () => {
    expect(visibleColumns([]).map((column) => column.id)).toEqual(["time"]);
  });
});

describe("gridTemplates", () => {
  it("gives the narrow breakpoint one track per column that survives it", () => {
    const columns = visibleColumns(["time", "curve", "rating"]);
    const { narrow, wide } = gridTemplates(columns);
    // Three tracks and nothing after them: the table is as wide as its columns.
    expect(wide.split(" ")).toEqual(["7.5rem", "6rem", "5.5rem"]);
    // Curve is hidden on a phone, and a hidden grid item still takes its track:
    // the narrow template has to be short, not the cells hidden.
    expect(narrow.split(" ")).toEqual(["7.5rem", "5.5rem"]);
  });

  it("sizes the text columns too, and leaves no spare track after the last one", () => {
    // Profile used to be `minmax(8rem,1fr)` and swallowed whatever the row had
    // left, which is also why it had no edge to drag. Then an empty `1fr` track
    // at the end took the leftover and drew it as a band of blank row. Every
    // column is a width now, and the room they do not use belongs to the page.
    const { wide } = gridTemplates(visibleColumns(["time", "profile"]));
    expect(wide).toBe("7.5rem 9rem");
    expect(wide).not.toContain("fr");
  });

  it("makes the Recipe a column that can be dragged narrower than it was", () => {
    const recipe = SHOT_COLUMNS.find((column) => column.id === "recipe");
    expect(recipe?.size).toEqual({ rem: 8, min: 4, max: 20 });
    expect(gridTemplates(visibleColumns(["recipe", "time"]), { recipe: 2 }).wide).toBe(
      "4rem 7.5rem",
    );
  });

  it("sizes Discard for its word and no wider, and hides it on a phone", () => {
    const discard = SHOT_COLUMNS.find((column) => column.id === "discard");
    expect(discard?.size).toEqual({ rem: 5, min: 5, max: 10 });
    expect(discard?.narrowHidden).toBe(true);
  });

  it("sizes Balance for its three words and no wider, and hides it on a phone like Discard", () => {
    const balance = SHOT_COLUMNS.find((column) => column.id === "balance");
    expect(balance?.size).toEqual({ rem: 9.25, min: 9.25, max: 14 });
    expect(balance?.narrowHidden).toBe(true);
  });

  it("covers every column the chooser offers, and nothing else", () => {
    const all = SHOT_COLUMNS.map((column) => column.id);
    expect(gridTemplates(visibleColumns(all)).wide.split(" ")).toHaveLength(all.length);
  });

  it("draws a column at the reader's width, clamped to its bounds", () => {
    const columns = visibleColumns(["profile", "time", "rating"]);
    expect(gridTemplates(columns, { time: 9.25 }).wide).toBe("9.25rem 9rem 5.5rem");
    // A stored width from a looser release is still drawn inside today's bounds.
    expect(gridTemplates(columns, { time: 1, rating: 99 }).wide).toBe("4.5rem 9rem 9rem");
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
      recipe: 8,
      drink: 6.5,
      time: 7.5,
      profile: 9,
      curve: 6,
      duration: 5.25,
      yield: 3,
      check: 9,
      review: 10.5,
      rating: 5.5,
      notes: 14,
      balance: 9.25,
      discard: 5,
      flags: 9,
    });
  });
});

describe("the Drink column", () => {
  it("is off by default, so a first visit and an upgrade keep the table as it was", () => {
    expect(DEFAULT_SHOT_COLUMNS).not.toContain("drink");
    for (const generation of PREVIOUS_DEFAULT_SHOT_COLUMNS)
      expect(generation).not.toContain("drink");
    expect(loadShotColumns()).not.toContain("drink");
  });

  it("is drawn after Recipe, hidden on a phone like Recipe, and a stored choice may switch it on", () => {
    const ids = SHOT_COLUMNS.map((column) => column.id);
    expect(ids.indexOf("drink")).toBe(ids.indexOf("recipe") + 1);
    expect(SHOT_COLUMNS.find((column) => column.id === "drink")?.narrowHidden).toBe(true);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["drink", "time", "recipe"]));
    expect(visibleColumns(loadShotColumns()).map((column) => column.id)).toEqual([
      "recipe",
      "drink",
      "time",
    ]);
  });

  it("is wide enough for the longest word, with the badge's padding", () => {
    // "Cappuccino" at text-xs medium plus px-2 and the border is about 5.3rem in Chromium.
    const size = SHOT_COLUMNS.find((column) => column.id === "drink")?.size;
    expect(size?.min).toBeGreaterThanOrEqual(5.75);
    expect(size?.rem).toBeGreaterThanOrEqual(size?.min ?? 0);
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

  it("raises a width dragged on the old Score column below Review's minimum to it", () => {
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ score: 5.5 }));
    expect(loadShotWidths()).toEqual({ review: 9.75 });
  });

  it("keeps the width dragged on the old Score column for Review", () => {
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ score: 11, time: 8 }));
    expect(loadShotWidths()).toEqual({ review: 11, time: 8 });
    // A stored Review width under the minimum is raised: the fault word is never cut by a drag.
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ review: 7 }));
    expect(loadShotWidths()).toEqual({ review: 9.75 });
    // Review's own width wins once it has been dragged.
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ score: 5.5, review: 12 }));
    expect(loadShotWidths()).toEqual({ review: 12 });
  });

  it("reads a width dragged on the old Set column as the Recipe column's", () => {
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ set: 12, time: 8 }));
    expect(loadShotWidths()).toEqual({ recipe: 12, time: 8 });
    // Clamped to the column's bounds like any other width.
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ set: 99 }));
    expect(loadShotWidths()).toEqual({ recipe: 20 });
    // The Recipe column's own width wins once it has been dragged.
    window.localStorage.setItem(SHOT_WIDTHS_KEY, JSON.stringify({ set: 12, recipe: 6 }));
    expect(loadShotWidths()).toEqual({ recipe: 6 });
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
        decision: "wide",
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

describe("a saved layout is brought to the new default row, once", () => {
  const saved = ["recipe", "time", "profile", "duration", "yield", "check", "rating", "review"];

  beforeEach(() => {
    window.localStorage.removeItem(SHOT_COLUMNS_BALANCE_KEY);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(saved));
  });

  it("puts Balance where Rating was and hides Profile, and leaves every other column", () => {
    // The precondition: the layout shows Rating and Profile, matches no earlier default, and has
    // not been brought along.
    expect(saved).toContain("rating");
    expect(saved).toContain("profile");
    expect(window.localStorage.getItem(SHOT_COLUMNS_BALANCE_KEY)).toBeNull();
    expect(PREVIOUS_DEFAULT_SHOT_COLUMNS.map((g) => [...g].sort())).not.toContainEqual(
      [...saved].sort(),
    );

    const after = loadShotColumns();

    expect(after).toEqual(["recipe", "time", "duration", "yield", "check", "balance", "review"]);
    expect(JSON.parse(window.localStorage.getItem(SHOT_COLUMNS_KEY) ?? "[]")).toEqual(after);
    expect(window.localStorage.getItem(SHOT_COLUMNS_BALANCE_KEY)).toBe("1");
  });

  it("does nothing once it has been done, so a Rating or Profile chosen later is kept", () => {
    window.localStorage.setItem(SHOT_COLUMNS_BALANCE_KEY, "1");
    expect(loadShotColumns()).toEqual(saved);
    // And a layout brought along once is not brought along again after the person adds them back.
    window.localStorage.removeItem(SHOT_COLUMNS_BALANCE_KEY);
    loadShotColumns();
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(saved));
    expect(loadShotColumns()).toEqual(saved);
  });

  it("only drops Rating from a layout that already shows Balance", () => {
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "rating", "balance", "review"]),
    );
    expect(loadShotColumns()).toEqual(["time", "balance", "check", "review"]);
  });

  it("marks a layout that needed nothing, and keeps a layout that would be left empty", () => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["time", "flags"]));
    expect(loadShotColumns()).toEqual(["time", "flags"]);
    expect(window.localStorage.getItem(SHOT_COLUMNS_BALANCE_KEY)).toBe("1");
    window.localStorage.removeItem(SHOT_COLUMNS_BALANCE_KEY);
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(["profile"]));
    expect(loadShotColumns()).toEqual(["profile"]);
  });

  it("still reads an earlier default as the current default, and a very old layout gets all three", () => {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(PREVIOUS_DEFAULT_SHOT_COLUMNS[0]));
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    // Names score (-> review), no Curve check, shows Rating and Profile.
    window.localStorage.setItem(
      SHOT_COLUMNS_KEY,
      JSON.stringify(["time", "profile", "score", "rating", "flags"]),
    );
    expect(loadShotColumns()).toEqual(["time", "check", "review", "balance", "flags"]);
  });

  it("is done by choosing columns, so a chosen Rating stays", () => {
    saveShotColumns(["time", "rating", "profile"]);
    expect(window.localStorage.getItem(SHOT_COLUMNS_BALANCE_KEY)).toBe("1");
    expect(loadShotColumns()).toEqual(["time", "rating", "profile"]);
  });
});

describe("a saved layout that showed Decision is brought to Rating and Discard, once", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SHOT_COLUMNS_DISCARD_KEY);
  });

  function store(layout: string[]) {
    window.localStorage.setItem(SHOT_COLUMNS_KEY, JSON.stringify(layout));
  }

  it("puts the stars where Decision was and Discard right after Balance", () => {
    // The precondition: Decision is on, Rating is off, and the layout is a choice (it matches
    // no default), and nothing has been brought along yet.
    const saved = ["recipe", "time", "check", "decision", "balance", "notes", "review"];
    store(saved);
    expect(window.localStorage.getItem(SHOT_COLUMNS_DISCARD_KEY)).toBeNull();
    expect(PREVIOUS_DEFAULT_SHOT_COLUMNS.map((g) => [...g].sort())).not.toContainEqual(
      [...saved].sort(),
    );

    const after = loadShotColumns();

    expect(after).toEqual([
      "recipe",
      "time",
      "check",
      "rating",
      "balance",
      "discard",
      "notes",
      "review",
    ]);
    expect(JSON.parse(window.localStorage.getItem(SHOT_COLUMNS_KEY) ?? "[]")).toEqual(after);
    expect(window.localStorage.getItem(SHOT_COLUMNS_DISCARD_KEY)).toBe("1");
  });

  it("leaves the stars alone when they are on already, and puts Discard where Decision was without Balance", () => {
    store(["time", "rating", "decision", "flags"]);
    expect(loadShotColumns()).toEqual(["time", "rating", "discard", "flags"]);
  });

  it("gives Rating and Discard in Decision's place when both Balance and the stars are off", () => {
    store(["time", "decision", "flags"]);
    expect(loadShotColumns()).toEqual(["time", "rating", "discard", "flags"]);
  });

  it("adds nothing to a layout that never showed Decision", () => {
    store(["time", "rating", "balance", "flags"]);
    expect(loadShotColumns()).toEqual(["time", "rating", "balance", "flags"]);
  });

  it("does nothing once it has been done: Decision just falls away", () => {
    window.localStorage.setItem(SHOT_COLUMNS_DISCARD_KEY, "1");
    store(["time", "decision", "balance", "flags"]);
    expect(loadShotColumns()).toEqual(["time", "balance", "flags"]);
  });

  it("is marked by saving the chooser, so a layout the person made is theirs", () => {
    saveShotColumns(["time", "balance"]);
    expect(window.localStorage.getItem(SHOT_COLUMNS_DISCARD_KEY)).toBe("1");
  });

  it("reads the previous default row as the new default", () => {
    store(["recipe", "time", "duration", "yield", "check", "decision", "balance", "review"]);
    expect(loadShotColumns()).toEqual(DEFAULT_SHOT_COLUMNS);
    expect(window.localStorage.getItem(SHOT_COLUMNS_DISCARD_KEY)).toBeNull();
  });
});
