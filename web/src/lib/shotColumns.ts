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
  | "score"
  | "rating"
  | "set"
  | "notes"
  | "decision"
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
 * The Set leads. A session is read Set by Set — this bag on this grinder, then
 * the next — and an archive scanned for "which shots were the Guji" is scanned
 * down its first column; the time beside it then says where in that bag a shot
 * fell.
 *
 * **Every column is a fixed track in rem, and every column can be dragged.**
 * There are no flexible tracks: Profile and Notes used to be `fr` and were the
 * two columns whose edge could not be taken hold of, which is the one thing a
 * reader expects of a column of text. Nor is any track `auto` — every row is a
 * grid of its own (see `ShotsTable`), and an `auto` track is sized by the
 * content of *that* row, so a long Set name in one row pushed every later
 * column of that row sideways, out of line with the header.
 *
 * **A default width is what fits that column, and nothing more.** For a column
 * with one shape of content it is a measurement: the sparkline's 96 px, five
 * 16 px stars, three words side by side. For a column of free text (the Set, a
 * profile name, a note) there is no such width, so the default is the width
 * that shows a usual value and the rest truncates with the whole string on
 * hover. Either way the heading counts as content: a column narrower than its
 * own word is a column with an ellipsis for a title, so each default is at
 * least the label at `text-xs` uppercase with `tracking-wide`, plus the 1rem
 * the sort arrow takes while that column is the one being sorted by. The
 * measurements below are Helvetica's, which is wider than the UI faces a
 * browser actually uses — a default that fits there fits everywhere.
 *
 * What the fixed tracks leave over goes to an empty track after the last
 * column (see `gridTemplates`), the way a spreadsheet does it, rather than
 * being shared out among columns that did not ask for it. A reader who wants
 * the room in a particular column drags that column's edge, and the width is
 * kept.
 */
export const SHOT_COLUMNS: ShotColumn[] = [
  // A Set badge says a name and a version; both truncate, and the whole name
  // is on hover. 8rem holds a usual one ("Guji washed v2").
  { id: "set", label: "Set", size: { rem: 8, min: 4, max: 20 }, narrowHidden: true },
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
  // The badge is two characters wide; the sorted heading is 3.8rem.
  { id: "score", label: "Score", size: { rem: 4, min: 2.75, max: 6 } },
  // Five 16 px star buttons and their gaps: 5.5rem is the narrowest they fit,
  // and wider than the sorted heading.
  { id: "rating", label: "Rating", size: { rem: 5.5, min: 5.5, max: 9 } },
  // A note is a sentence and will truncate at any width; 14rem is a phrase,
  // which is what the column is for — the whole note is on hover, and reading
  // a session back is what the drag is for.
  { id: "notes", label: "Notes", size: { rem: 14, min: 6, max: 32 }, narrowHidden: true },
  // Keep, Improve and Discard side by side at text-xs: 9.75rem in DejaVu Sans,
  // which is wider than the system faces a browser actually uses, so the
  // default still shows all three words.
  {
    id: "decision",
    label: "Decision",
    size: { rem: 9.75, min: 9.75, max: 14 },
    narrowHidden: true,
  },
  // Badges that wrap: "gone from machine" is the widest single one.
  { id: "flags", label: "Flags", size: { rem: 9, min: 4, max: 24 }, narrowHidden: true },
];

/**
 * What a first visit shows.
 *
 * Set, Time and Profile say what was brewed and when; Duration, Yield and
 * Score say what the machine did; Rating and Decision say what you made of it.
 * That is the row a reader scans, and it is the row they get without touching
 * the chooser.
 *
 * Curve and Notes are the two left off. A sparkline is a request and a canvas
 * per row, wanted by somebody comparing shapes rather than by somebody
 * scanning; Notes is long, and it is there for reading a session back. Flags
 * is off because the flags are mostly absences (imported, gone from the
 * machine, incomplete) that matter on a handful of rows — Decision, "keep this
 * recipe, improve on it, or bin the shot", is the question every shot ends on,
 * and a column that answers it with a click is worth more than a badge. An
 * analysis is started from the shot page; its state is in Flags, which stays
 * in the chooser.
 */
export const DEFAULT_SHOT_COLUMNS: ShotColumnId[] = [
  "set",
  "time",
  "profile",
  "duration",
  "yield",
  "score",
  "rating",
  "decision",
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
export const PREVIOUS_DEFAULT_SHOT_COLUMNS: readonly (readonly ShotColumnId[])[] = [
  // Before Profile joined the default row.
  ["set", "time", "duration", "yield", "score", "rating", "decision"],
  // Before the column after Rating (Decision) replaced Flags.
  ["time", "duration", "yield", "score", "rating", "set", "flags"],
];

/**
 * Whether a stored choice is one of the defaults that came before. Compared as
 * a set, because the order a list was stored in carries nothing: the table
 * draws columns in the canonical order whatever the list says.
 */
function isPreviousDefault(ids: ShotColumnId[]): boolean {
  const stored = new Set(ids);
  if (stored.size !== ids.length) return false;
  return PREVIOUS_DEFAULT_SHOT_COLUMNS.some(
    (generation) => generation.length === stored.size && generation.every((id) => stored.has(id)),
  );
}

/** Bump the suffix when the meaning of a stored value changes, never the keys. */
export const SHOT_COLUMNS_KEY = "shots.columns.v1";

/**
 * Columns that were replaced, and what took their place. The Analyse button
 * gave way to Decision: somebody who chose the button gets its replacement in
 * the same place rather than losing a column without a word. Any other stored
 * choice is kept as made.
 */
const REPLACED: Record<string, ShotColumnId> = { analyze: "decision" };

const ALL_IDS = new Set<string>(SHOT_COLUMNS.map((column) => column.id));

function isColumnId(value: unknown): value is ShotColumnId {
  return typeof value === "string" && ALL_IDS.has(value);
}

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
      .filter(isColumnId);
    if (known.length === 0 || isPreviousDefault(known)) return DEFAULT_SHOT_COLUMNS;
    return known;
  } catch {
    return DEFAULT_SHOT_COLUMNS;
  }
}

export function saveShotColumns(
  visible: ShotColumnId[],
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(SHOT_COLUMNS_KEY, JSON.stringify(visible));
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
    for (const column of SHOT_COLUMNS) {
      const value = (parsed as Record<string, unknown>)[column.id];
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
 * Every column is a fixed track, so a trailing empty `1fr` track takes
 * whatever the row has left. Without it the fixed tracks would still be
 * start-aligned, but the grid would end short of the row and nothing would say
 * why; with it the space is visibly "after the last column", which is where a
 * spreadsheet puts it, and a dragged edge stays under the pointer.
 */
export function gridTemplates(
  columns: ShotColumn[],
  widths: ShotWidths = {},
): { narrow: string; wide: string } {
  const template = (list: ShotColumn[]) => {
    const tracks = list.map((column) => `${columnWidth(column.size, widths[column.id])}rem`);
    tracks.push("minmax(0,1fr)");
    return tracks.join(" ");
  };
  const narrowColumns = columns.filter((column) => !column.narrowHidden);
  return {
    narrow: template(narrowColumns.length > 0 ? narrowColumns : columns),
    wide: template(columns),
  };
}
