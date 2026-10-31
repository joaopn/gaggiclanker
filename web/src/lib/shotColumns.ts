/**
 * Which columns the shots table draws, and how wide each one is.
 *
 * The table is a CSS grid rather than a `<table>` (see `ShotsTable`), so the
 * set of visible columns and the grid template are the same fact and have to
 * be derived from one list — a hand-maintained template and a hand-maintained
 * header drift apart the first time somebody adds a column.
 *
 * The choice is per-person and per-browser, not per-archive: it is a view
 * preference, nothing on the server depends on it, and a round trip to store it
 * would make opening the page wait on a request. So `localStorage`, under a
 * versioned key — when the column list changes, the old value is read through
 * the same filter as any other input and unknown ids simply fall away.
 */

export type ShotColumnId =
  | "time"
  | "profile"
  | "curve"
  | "duration"
  | "yield"
  | "check"
  | "review"
  | "rating"
  | "recipe"
  | "drink"
  | "notes"
  | "balance"
  | "discard"
  | "flags";

export type ShotColumn = {
  id: ShotColumnId;
  /** What the header says, and what the column chooser lists. */
  label: string;
  /** How wide the column is drawn, and the bounds a drag is clamped to. */
  size: ColumnSize;
  /** Hidden below `md`, whatever the chooser says: a phone has five columns of room. */
  narrowHidden?: boolean;
};

/** A track in rem: the width it starts at, and the bounds a drag is clamped to. */
export type ColumnSize = { rem: number; min: number; max: number };

/**
 * Every column, in the order they are drawn. Reordering is not offered: the
 * order carries meaning (what you were brewing, when, what, how it went, what
 * you thought) and a table whose columns move is a table nobody can read at a
 * glance.
 *
 * The Recipe leads. A session is read Recipe by Recipe — this bean on this grinder, then
 * the next — and an archive scanned for "which shots were the Guji" is scanned
 * down its first column; the time beside it then says where in that bag a shot
 * fell.
 *
 * **Every column is a fixed track in rem, and every column can be dragged.**
 * There are no flexible tracks: Profile and Notes used to be `fr` and were the
 * two columns whose edge could not be taken hold of, which is the one thing a
 * reader expects of a column of text. Nor is any track `auto` — every row is a
 * grid of its own (see `ShotsTable`), and an `auto` track is sized by the
 * content of *that* row, so a long Recipe name in one row pushed every later
 * column of that row sideways, out of line with the header.
 *
 * **A default width is what fits that column, and nothing more.** For a column
 * with one shape of content it is a measurement: the sparkline's 96 px, five
 * 16 px stars, three words side by side. For a column of free text (the Recipe, a
 * profile name, a note) there is no such width, so the default is the width
 * that shows a usual value and the rest truncates with the whole string on
 * hover. Either way the heading counts as content: a column narrower than its
 * own word is a column with an ellipsis for a title, so each default is at
 * least the label at `text-xs` uppercase with `tracking-wide`, plus the 1rem
 * the sort arrow takes while that column is the one being sorted by. The
 * measurements below are Helvetica's, which is wider than the UI faces a
 * browser actually uses — a default that fits there fits everywhere.
 *
 * The table is as wide as its columns and no wider (see `gridTemplates`):
 * room the columns do not use is left to the page rather than shared out
 * among columns that did not ask for it, or drawn as an empty band at the end
 * of every row. A reader who wants the room in a particular column drags that
 * column's edge, the table grows with it, and the width is kept.
 */
export const SHOT_COLUMNS: ShotColumn[] = [
  // A Recipe badge says a name and a version; both truncate, and the whole name
  // is on hover. 8rem holds a usual one ("Guji washed v2").
  { id: "recipe", label: "Recipe", size: { rem: 8, min: 4, max: 20 }, narrowHidden: true },
  // The drink of the version the shot is filed under, as a badge: the longest word ("Cappuccino")
  // is 5.3rem as measured in headless Chromium with the badge's padding and border, so 6rem
  // holds it whole. Off by default; a shot filed under no Recipe, or one that states no drink,
  // has an empty cell. Hidden on a phone like Recipe, whatever the chooser says.
  { id: "drink", label: "Drink", size: { rem: 6.5, min: 6, max: 12 }, narrowHidden: true },
  // The list's own compact format (`formatListTime`), measured rather than
  // guessed: the widest current-year form of the locales checked is US English
  // ("Dec 24, 11:59 PM"), 7.7rem at text-sm in DejaVu Sans. The day-first
  // locales need about 6.5rem, and an older shot's date-with-year is narrower
  // than either.
  { id: "time", label: "Time", size: { rem: 7.5, min: 4.5, max: 16 } },
  // Profile names run from "Default" to "Blooming espresso 9 bar"; 9rem shows
  // the ones this archive is built around and truncates the rest.
  { id: "profile", label: "Profile", size: { rem: 9, min: 4, max: 24 } },
  // The sparkline is drawn at a fixed 96 px; narrower than that clips it.
  { id: "curve", label: "Curve", size: { rem: 6, min: 6, max: 12 }, narrowHidden: true },
  // "28.5 s" needs 2.6rem; the heading with its sort arrow needs 5.1rem, and
  // Duration is a column people sort by.
  { id: "duration", label: "Duration", size: { rem: 5.25, min: 3.5, max: 8 } },
  // "36.0 g" is 2.5rem, the heading 2.3rem — and Yield does not sort, so no
  // arrow ever appears beside it.
  { id: "yield", label: "Yield", size: { rem: 3, min: 2.75, max: 8 }, narrowHidden: true },
  // The deterministic checks and warnings: "phase: fault" and "+N" for the rest.
  // "decline: skipped +2" is 8.6rem as measured in headless Chromium (badge padding and
  // border included), so 9rem holds it; the sorted heading is 6.2rem. A longer phase name,
  // or a "+12", truncates inside the column and the whole list is on hover. The minimum is
  // 8.75rem: with the phase gone, the longest fault word and the widest count (": high
  // pressure +12", 8.55rem in Chromium with the badge's padding and border) still fit
  // whole, so a drag can never cut the fault.
  { id: "check", label: "Curve check", size: { rem: 9, min: 8.75, max: 24 } },
  // Five 16 px star buttons and their gaps: 5.5rem is the narrowest they fit,
  // and wider than the sorted heading.
  { id: "rating", label: "Rating", size: { rem: 5.5, min: 5.5, max: 9 } },
  // A note is a sentence and will truncate at any width; 14rem is a phrase,
  // which is what the column is for — the whole note is on hover, and reading
  // a session back is what the drag is for.
  { id: "notes", label: "Notes", size: { rem: 14, min: 6, max: 32 }, narrowHidden: true },
  // Sour, Balanced and Bitter side by side at text-xs: 9.02rem as measured in headless Chromium,
  // buttons' padding and borders included, so 9.25rem holds all three whole, filled or outlined.
  // Not sortable, so no arrow ever widens the heading. The minimum is the default.
  {
    id: "balance",
    label: "Balance",
    size: { rem: 9.25, min: 9.25, max: 14 },
    narrowHidden: true,
  },
  // An outlined "Discard" until clicked, then a filled "Discarded" (text-xs with the button's padding:
  // 4.4rem as measured in headless Chromium, DejaVu Sans, the widest face). The heading and
  // its sort arrow are narrower. Not sortable. Hidden on a phone like Balance. The minimum is
  // the default: a drag can never cut the word.
  { id: "discard", label: "Discard", size: { rem: 5, min: 5, max: 10 }, narrowHidden: true },
  // What the model wrote: the same "phase: fault +N" badge, or the Review button, or a grey
  // "Failed to run" with its Retry button beside it. The minimum is what the two together
  // need: "Failed to run" and "Retry" are 9.7rem in headless Chromium, and the longest fault
  // with its count is narrower, so a drag can never cut either. A stored width below it (the
  // old Score column's, or a drag from before) is raised to it on load. It sits after the
  // person's own columns, just before Flags: the model's reading comes last.
  { id: "review", label: "AI Review", size: { rem: 10.5, min: 9.75, max: 24 } },
  // Badges that wrap: "gone from machine" is the widest single one.
  { id: "flags", label: "Flags", size: { rem: 9, min: 4, max: 24 }, narrowHidden: true },
];

/**
 * What a first visit shows.
 *
 * Recipe and Time say what was brewed and when; Duration and Yield say
 * what the machine did; Curve check says what the deterministic checks make of the curve;
 * Rating and Balance say what you made of the cup, and Discard is the one click that takes a
 * shot that went wrong out of every count. That is the row a reader scans, and it is the row
 * they get without touching the chooser. AI Review comes last of those: the model's reading,
 * with the button that starts one.
 *
 * The stars are the whole judgement: their colour is the band (Good, Neutral, Bad) the server
 * says they are in, so there is no Decision column to say it twice.
 *
 * Profile is off the row, in the chooser: the Recipe already names it, since a Recipe brews one
 * profile.
 *
 * Curve and Notes are left off. A sparkline is a request and a canvas per row, wanted by somebody comparing
 * shapes rather than by somebody scanning; Notes is long, and it is there for reading a session
 * back. Flags is off because the flags are mostly absences (imported, gone from the machine,
 * incomplete) that matter on a handful of rows.
 */
export const DEFAULT_SHOT_COLUMNS: ShotColumnId[] = [
  "recipe",
  "time",
  "duration",
  "yield",
  "check",
  "rating",
  "balance",
  "discard",
  "review",
];

/**
 * The defaults of earlier releases, newest first, as a v1 value could hold
 * them.
 *
 * A stored value only exists once somebody has used the chooser, so a stored
 * copy of an old default means "I looked, and this is what I wanted" only in
 * the sense that it was what they were given — they toggled something and put
 * it back. Any of those exact values is read as the current default. Anything
 * else is a choice somebody made and is kept as they made it, Flags included:
 * an upgrade that rewrote chosen columns would be the release deciding for
 * them.
 *
 * A generation is kept, not replaced, when the default changes again: a
 * browser that has not been opened since the older one was current is exactly
 * the browser this is for.
 */
export const PREVIOUS_DEFAULT_SHOT_COLUMNS: readonly (readonly string[])[] = [
  // Before the stars came back as the judgement and Discard became its own column: the Decision
  // column (a retired id, which only a stored layout can still hold) stood where they are.
  ["recipe", "time", "duration", "yield", "check", "decision", "balance", "review"],
  // Before Balance joined the default row, and Rating and Profile left it.
  ["recipe", "time", "profile", "duration", "yield", "check", "review", "rating", "decision"],
  // Before the Curve check column was split from Review.
  ["recipe", "time", "profile", "duration", "yield", "review", "rating", "decision"],
  // Before Profile joined the default row.
  ["recipe", "time", "duration", "yield", "review", "rating", "decision"],
  // Before the column after Rating (Decision) replaced Flags.
  ["time", "duration", "yield", "review", "rating", "recipe", "flags"],
];

/**
 * Whether a stored choice is one of the defaults that came before. Compared as
 * a set, because the order a list was stored in carries nothing: the table
 * draws columns in the canonical order whatever the list says.
 */
function isPreviousDefault(ids: string[]): boolean {
  const stored = new Set(ids);
  if (stored.size !== ids.length) return false;
  return PREVIOUS_DEFAULT_SHOT_COLUMNS.some(
    (generation) => generation.length === stored.size && generation.every((id) => stored.has(id)),
  );
}

/** Bump the suffix when the meaning of a stored value changes, never the keys. */
export const SHOT_COLUMNS_KEY = "shots.columns.v1";

/**
 * Set once the Curve check column has been added to a stored layout, or once the person chose
 * their columns themselves. The column is added in front of Review in every stored layout that
 * shows Review, exactly once (the maintainer asked for it, as the Score column became Review);
 * after that a layout without it is a choice and is kept. Any other stored choice is untouched.
 */
export const SHOT_COLUMNS_CHECK_KEY = "shots.columns.check.v1";

/**
 * Set once a stored layout has been brought to the new default row, or once the person chose
 * their columns themselves. The stars gave way to Balance and Profile left the row (the
 * maintainer asked for both), so a stored layout that matches no earlier default gets, exactly
 * once: Balance in place of Rating, or just without Rating when it shows Balance already, and
 * without Profile. Every other column stays as it was. After that a layout with Rating or
 * Profile is a choice and is kept. Widths are not touched.
 */
export const SHOT_COLUMNS_BALANCE_KEY = "shots.columns.balance.v1";

/**
 * Set once a stored layout has been brought to the stars-and-Discard row, or once the person
 * chose their columns themselves. The Decision column is gone: where it was shown, the stars
 * (Rating) take its place unless they are already shown, and Discard sits right after Balance
 * (or where Decision was when Balance is off). A layout that did not show Decision gets no
 * Discard, because the person hid that column. After that, a layout without Rating or Discard is
 * a choice and is kept. Widths are not touched.
 */
export const SHOT_COLUMNS_DISCARD_KEY = "shots.columns.discard.v1";

/**
 * Columns that were replaced, and what took their place. The Analyse button
 * gave way to Decision: somebody who chose the button gets its replacement in
 * the same place rather than losing a column without a word. Any other stored
 * choice is kept as made. The Score column gave way to Review, in the same
 * place, so a layout that names `score` shows `review` at the same position,
 * on or off as it was.
 */
const REPLACED: Record<string, string> = {
  analyze: "decision",
  score: "review",
  set: "recipe",
};

const ALL_IDS = new Set<string>(SHOT_COLUMNS.map((column) => column.id));

function isColumnId(value: unknown): value is ShotColumnId {
  return typeof value === "string" && ALL_IDS.has(value);
}

/** The retired Decision column's id, which only a stored layout can still name. */
const RETIRED_DECISION = "decision";

/**
 * The stored choice, or the default.
 *
 * Every failure mode ends at the default rather than at an error: storage can
 * throw outright (a private window, blocked site data), the value can be
 * something else entirely (a hand-edited key), and an empty list would be a
 * table with no columns, which is not a preference anybody expressed.
 */
export function loadShotColumns(storage: Storage | undefined = safeStorage()): ShotColumnId[] {
  try {
    const raw = storage?.getItem(SHOT_COLUMNS_KEY);
    if (!raw) return DEFAULT_SHOT_COLUMNS;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_SHOT_COLUMNS;
    const known = parsed
      .map((value) => (typeof value === "string" ? (REPLACED[value] ?? value) : value))
      .filter((value): value is string => isColumnId(value) || value === RETIRED_DECISION);
    if (known.length === 0 || isPreviousDefault(known)) return DEFAULT_SHOT_COLUMNS;
    const shown = withDiscard(withBalance(withCurveCheck(known, storage), storage), storage);
    const columns = shown.filter(isColumnId);
    return columns.length > 0 ? columns : DEFAULT_SHOT_COLUMNS;
  } catch {
    return DEFAULT_SHOT_COLUMNS;
  }
}

/** The stored layout with Curve check added in front of Review, once; see the key above. */
function withCurveCheck(known: string[], storage: Storage | undefined): string[] {
  if (!known.includes("review") || known.includes("check")) return known;
  let done = false;
  try {
    done = (storage?.getItem(SHOT_COLUMNS_CHECK_KEY) ?? null) !== null;
  } catch {
    done = false;
  }
  if (done) return known;
  const added = known.flatMap((id): string[] => (id === "review" ? ["check", id] : [id]));
  try {
    storage?.setItem(SHOT_COLUMNS_KEY, JSON.stringify(added));
    storage?.setItem(SHOT_COLUMNS_CHECK_KEY, "1");
  } catch {
    // Not remembered: the next load adds it again, which is what a person who cannot save
    // anything would want.
  }
  return added;
}

/** The stored layout with Rating swapped for Balance and Profile hidden, once; see the key above. */
function withBalance(known: string[], storage: Storage | undefined): string[] {
  let done = false;
  try {
    done = (storage?.getItem(SHOT_COLUMNS_BALANCE_KEY) ?? null) !== null;
  } catch {
    done = false;
  }
  if (done) return known;
  const hasBalance = known.includes("balance");
  const moved = known.flatMap((id): string[] =>
    id === "rating" ? (hasBalance ? [] : ["balance"]) : id === "profile" ? [] : [id],
  );
  // Never a table with no columns: a layout that would end up empty is kept as it was.
  const next = moved.length > 0 ? moved : known;
  try {
    if (next.length !== known.length || next.some((id, index) => id !== known[index])) {
      storage?.setItem(SHOT_COLUMNS_KEY, JSON.stringify(next));
    }
    storage?.setItem(SHOT_COLUMNS_BALANCE_KEY, "1");
  } catch {
    // Not remembered: the next load does it again, which is what a person who cannot save
    // anything would want.
  }
  return next;
}

/**
 * The stored layout with Decision replaced by Rating and Discard, once; see the key above.
 *
 * Rating takes Decision's place unless the stars are shown already. Discard goes right after
 * Balance, or where Decision was when Balance is off. A layout that never showed Decision is
 * left as it is. The retired id never survives this function, flag or no flag.
 */
function withDiscard(known: string[], storage: Storage | undefined): string[] {
  const hadDecision = known.includes(RETIRED_DECISION);
  let done = false;
  try {
    done = (storage?.getItem(SHOT_COLUMNS_DISCARD_KEY) ?? null) !== null;
  } catch {
    done = false;
  }
  if (done || !hadDecision) {
    return known.filter((id) => id !== RETIRED_DECISION);
  }
  const hasRating = known.includes("rating");
  const hasBalance = known.includes("balance");
  const next = known.flatMap((id): string[] => {
    if (id === RETIRED_DECISION) {
      return [...(hasRating ? [] : ["rating"]), ...(hasBalance ? [] : ["discard"])];
    }
    return id === "balance" ? [id, "discard"] : [id];
  });
  try {
    storage?.setItem(SHOT_COLUMNS_KEY, JSON.stringify(next));
    storage?.setItem(SHOT_COLUMNS_DISCARD_KEY, "1");
  } catch {
    // Not remembered: the next load does it again, which is what a person who cannot save
    // anything would want.
  }
  return next;
}

export function saveShotColumns(
  visible: ShotColumnId[],
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(SHOT_COLUMNS_KEY, JSON.stringify(visible));
    // A layout the person chose is theirs: Curve check is not added to it behind their back.
    storage?.setItem(SHOT_COLUMNS_CHECK_KEY, "1");
    storage?.setItem(SHOT_COLUMNS_BALANCE_KEY, "1");
    storage?.setItem(SHOT_COLUMNS_DISCARD_KEY, "1");
  } catch {
    // A view preference is not worth a toast. The table is already showing
    // what was asked for; it just will not still be doing so tomorrow.
  }
}

/** `localStorage`, or nothing — reading the property itself can throw. */
function safeStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

/** The visible columns in their canonical order, never empty. */
export function visibleColumns(ids: ShotColumnId[]): ShotColumn[] {
  const wanted = new Set(ids);
  const columns = SHOT_COLUMNS.filter((column) => wanted.has(column.id));
  return columns.length > 0 ? columns : SHOT_COLUMNS.filter((c) => c.id === "time");
}

/**
 * How wide the reader dragged each fixed column, in rem.
 *
 * Stored apart from the column choice, under its own key: the two change
 * independently, and a width stored inside the column list would turn every
 * resize into a rewrite of which columns are shown. Rem rather than pixels, so
 * a browser zoomed or a larger default font keeps the proportions the reader
 * chose instead of squeezing the text.
 */
export type ShotWidths = Partial<Record<ShotColumnId, number>>;

/** Bump the suffix when the meaning of a stored value changes. */
export const SHOT_WIDTHS_KEY = "shots.widths.v1";

/** A width inside the column's bounds, to the nearest hundredth of a rem. */
export function clampWidth(size: ColumnSize, rem: number): number {
  const bounded = Math.min(size.max, Math.max(size.min, rem));
  return Math.round(bounded * 100) / 100;
}

/** The width a column is drawn at: the reader's, clamped, or its default. */
export function columnWidth(size: ColumnSize, stored: number | undefined): number {
  return stored === undefined ? size.rem : clampWidth(size, stored);
}

/**
 * The stored widths, keeping only what still makes sense.
 *
 * The same failure-tolerant reading as the column choice: storage that throws,
 * a value that is not an object, an id that is no longer a column, a width
 * that is not a number — each of those falls away on its own and leaves the
 * rest of the reader's widths standing.
 */
export function loadShotWidths(storage: Storage | undefined = safeStorage()): ShotWidths {
  try {
    const raw = storage?.getItem(SHOT_WIDTHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const widths: ShotWidths = {};
    const stored = parsed as Record<string, unknown>;
    for (const column of SHOT_COLUMNS) {
      // A width dragged on the old Score column is the Review column's now, and one dragged on
      // the Set column is the Recipe column's, unless the new column has been dragged itself.
      const own = stored[column.id];
      const old =
        column.id === "review" ? stored.score : column.id === "recipe" ? stored.set : undefined;
      const value = own === undefined ? old : own;
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      widths[column.id] = clampWidth(column.size, value);
    }
    return widths;
  } catch {
    return {};
  }
}

export function saveShotWidths(
  widths: ShotWidths,
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    if (Object.keys(widths).length === 0) storage?.removeItem(SHOT_WIDTHS_KEY);
    else storage?.setItem(SHOT_WIDTHS_KEY, JSON.stringify(widths));
  } catch {
    // As with the columns: a width that does not survive a reload is not
    // worth a toast.
  }
}

/**
 * The grid template for a set of columns, at both breakpoints.
 *
 * Two templates rather than one with `display:none` on the cells: a grid track
 * with a hidden child still takes its width, so a phone would carry six empty
 * columns' worth of gutter.
 *
 * Every column is a fixed track and there is nothing after the last one: the
 * grid is exactly as wide as its tracks, and the table around it shrinks to
 * that width (`w-fit` on the shots page). A trailing `1fr` track used to take
 * whatever the row had left, which on a wide window was a band of empty
 * bordered row wider than the columns themselves.
 */
export function gridTemplates(
  columns: ShotColumn[],
  widths: ShotWidths = {},
): { narrow: string; wide: string } {
  const template = (list: ShotColumn[]) =>
    list.map((column) => `${columnWidth(column.size, widths[column.id])}rem`).join(" ");
  const narrowColumns = columns.filter((column) => !column.narrowHidden);
  return {
    narrow: template(narrowColumns.length > 0 ? narrowColumns : columns),
    wide: template(columns),
  };
}
