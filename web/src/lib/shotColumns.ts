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
  /** This column's track in the grid template. */
  width: string;
  /** Hidden below `md`, whatever the chooser says: a phone has five columns of room. */
  narrowHidden?: boolean;
  /** Right-aligned, for the numbers. */
  numeric?: boolean;
};

/**
 * Every column, in the order they are drawn. Reordering is not offered: the
 * order carries meaning (when, what, how it went, what you thought) and a
 * table whose columns move is a table nobody can read at a glance.
 */
export const SHOT_COLUMNS: ShotColumn[] = [
  { id: "time", label: "Time", width: "10.5rem" },
  { id: "profile", label: "Profile", width: "minmax(8rem,1fr)" },
  { id: "curve", label: "Curve", width: "6rem", narrowHidden: true },
  { id: "duration", label: "Duration", width: "4.5rem", numeric: true },
  { id: "yield", label: "Yield", width: "4.5rem", numeric: true, narrowHidden: true },
  { id: "score", label: "Score", width: "3.25rem", numeric: true },
  { id: "rating", label: "Rating", width: "5.5rem" },
  { id: "set", label: "Set", width: "minmax(6rem,auto)", narrowHidden: true },
  { id: "notes", label: "Notes", width: "minmax(8rem,1.2fr)", narrowHidden: true },
  { id: "flags", label: "Flags", width: "minmax(6rem,auto)", narrowHidden: true },
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
  "time",
  "duration",
  "yield",
  "score",
  "rating",
  "set",
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
 * The grid template for a set of columns, at both breakpoints.
 *
 * Two templates rather than one with `display:none` on the cells: a grid track
 * with a hidden child still takes its width, so a phone would carry six empty
 * columns' worth of gutter.
 */
export function gridTemplates(columns: ShotColumn[]): { narrow: string; wide: string } {
  const wide = columns.map((column) => column.width).join(" ");
  const narrowColumns = columns.filter((column) => !column.narrowHidden);
  const narrow = (narrowColumns.length > 0 ? narrowColumns : columns)
    .map((column) => column.width)
    .join(" ");
  return { narrow, wide };
}
