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
  | "flags";

export type ShotColumn = {
  id: ShotColumnId;
  /** What the header says, and what the column chooser lists. */
  label: string;
  /**
   * How wide the column is: either a fixed width in rem, which the reader can
   * drag between `min` and `max`, or a flexible track that shares whatever
   * the fixed columns leave.
   */
  size: FixedSize | { track: string };
  /** Hidden below `md`, whatever the chooser says: a phone has five columns of room. */
  narrowHidden?: boolean;
};

/** A fixed track, in rem, with the bounds a drag is clamped to. */
export type FixedSize = { rem: number; min: number; max: number };

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
 * Only Set, Profile and Notes are flexible, and the rest are fixed rather than
 * `auto`. Every row is a grid of its own (see `ShotsTable`), and an `auto`
 * track is sized by the content of *that* row: a long Set name in one row
 * pushed every later column of that row sideways, out of line with the header.
 * A `fr` track resolves identically in every row, because the fixed tracks
 * beside it are the same everywhere.
 *
 * Time is sized for the list's own compact format (`formatListTime`), measured
 * rather than guessed: the widest current-year form of the locales checked is
 * US English ("Dec 24, 11:59 PM"), 7.7rem at text-sm in DejaVu Sans, which is
 * wider than the system UI faces a browser actually uses. The day-first
 * locales need about 6.5rem. An older shot's date-with-year is narrower than
 * either.
 */
export const SHOT_COLUMNS: ShotColumn[] = [
  { id: "set", label: "Set", size: { track: "minmax(8rem,1fr)" }, narrowHidden: true },
  { id: "time", label: "Time", size: { rem: 7.5, min: 4.5, max: 16 } },
  { id: "profile", label: "Profile", size: { track: "minmax(8rem,1fr)" } },
  // The sparkline is drawn at a fixed 96 px; narrower than that clips it.
  { id: "curve", label: "Curve", size: { rem: 6.5, min: 6, max: 12 }, narrowHidden: true },
  { id: "duration", label: "Duration", size: { rem: 4.5, min: 3.5, max: 8 } },
  { id: "yield", label: "Yield", size: { rem: 4.5, min: 3.5, max: 8 }, narrowHidden: true },
  { id: "score", label: "Score", size: { rem: 3.25, min: 2.75, max: 6 } },
  // Five 16 px star buttons and their gaps: 5.5rem is the narrowest they fit.
  { id: "rating", label: "Rating", size: { rem: 5.5, min: 5.5, max: 9 } },
  { id: "notes", label: "Notes", size: { track: "minmax(8rem,1.2fr)" }, narrowHidden: true },
  { id: "flags", label: "Flags", size: { rem: 9, min: 4, max: 24 }, narrowHidden: true },
];

/**
 * What a first visit shows.
 *
 * Profile and Curve are off, which is the change worth explaining. A sparkline
 * per row is a request per row and a canvas per row, and the profile name is
 * the same string on almost every row of an archive built around a handful of
 * profiles — so both cost a lot and say little, while Set and Rating (what you
 * were brewing, and whether it worked) say everything and were the two hardest
 * things to see. Notes is off because it is long: it is there for somebody who
 * wants to read a session back, not for scanning.
 */
export const DEFAULT_SHOT_COLUMNS: ShotColumnId[] = [
  "set",
  "time",
  "duration",
  "yield",
  "score",
  "rating",
  "flags",
];

/** Bump the suffix when the meaning of a stored value changes, never the keys. */
export const SHOT_COLUMNS_KEY = "shots.columns.v1";

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
    const known = parsed.filter(isColumnId);
    return known.length > 0 ? known : DEFAULT_SHOT_COLUMNS;
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

/** A fixed column's bounds, or `null` for a flexible one. */
export function fixedSize(column: ShotColumn): FixedSize | null {
  return "rem" in column.size ? column.size : null;
}

/** A width inside the column's bounds, to the nearest hundredth of a rem. */
export function clampWidth(size: FixedSize, rem: number): number {
  const bounded = Math.min(size.max, Math.max(size.min, rem));
  return Math.round(bounded * 100) / 100;
}

/** The width a fixed column is drawn at: the reader's, clamped, or its default. */
export function columnWidth(size: FixedSize, stored: number | undefined): number {
  return stored === undefined ? size.rem : clampWidth(size, stored);
}

/**
 * The stored widths, keeping only what still makes sense.
 *
 * The same failure-tolerant reading as the column choice: storage that throws,
 * a value that is not an object, an id that is no longer a column or no longer
 * a fixed one, a width that is not a number — each of those falls away on its
 * own and leaves the rest of the reader's widths standing.
 */
export function loadShotWidths(storage: Storage | undefined = safeStorage()): ShotWidths {
  try {
    const raw = storage?.getItem(SHOT_WIDTHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const widths: ShotWidths = {};
    for (const column of SHOT_COLUMNS) {
      const size = fixedSize(column);
      const value = (parsed as Record<string, unknown>)[column.id];
      if (size === null || typeof value !== "number" || !Number.isFinite(value)) continue;
      widths[column.id] = clampWidth(size, value);
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
 * When no flexible column is visible, a trailing empty `1fr` track takes the
 * rest of the row. Without it the fixed tracks would still be start-aligned,
 * but the grid would end short of the row and nothing would say why; with it
 * the space is visibly "after the last column", which is where a spreadsheet
 * puts it, and a dragged edge stays under the pointer.
 */
export function gridTemplates(
  columns: ShotColumn[],
  widths: ShotWidths = {},
): { narrow: string; wide: string } {
  const template = (list: ShotColumn[]) => {
    const tracks = list.map((column) => {
      const size = fixedSize(column);
      return size === null
        ? (column.size as { track: string }).track
        : `${columnWidth(size, widths[column.id])}rem`;
    });
    if (list.every((column) => fixedSize(column) !== null)) tracks.push("minmax(0,1fr)");
    return tracks.join(" ");
  };
  const narrowColumns = columns.filter((column) => !column.narrowHidden);
  return {
    narrow: template(narrowColumns.length > 0 ? narrowColumns : columns),
    wide: template(columns),
  };
}
