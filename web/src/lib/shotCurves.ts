import { SHOT_SERIES } from "@/lib/shotChart";

/**
 * Which curves the shots table's Curve column draws, and in what colour.
 *
 * A view preference, per browser, like the column choice (`shotColumns.ts`,
 * whose failure handling this copies): storage can throw, the value can be
 * anything, and every failure ends at the default rather than at an error.
 *
 * Colours are theme tokens, never hex: a stored `#1f77b4` cannot follow the
 * light and dark themes, a `var(--chart-1)` does.
 */

export type CurveColour = { token: string; label: string };

/** What a swatch can be: the chart slots and the two text colours. */
export const CURVE_COLOURS: readonly CurveColour[] = [
  { token: "--chart-1", label: "Chart 1" },
  { token: "--chart-2", label: "Chart 2" },
  { token: "--chart-3", label: "Chart 3" },
  { token: "--chart-4", label: "Chart 4" },
  { token: "--chart-5", label: "Chart 5" },
  { token: "--chart-6", label: "Chart 6" },
  { token: "--foreground", label: "Foreground" },
  { token: "--muted-foreground", label: "Muted" },
];

/**
 * Which series are drawn, and the colour chosen for each series.
 *
 * Colours are kept for hidden series too: a colour picked, then hidden and
 * shown again, comes back as it was. A series with no entry in `colors` has
 * its own default.
 */
export type CurveChoice = { shown: string[]; colors: Record<string, string> };

/**
 * Pressure and cup flow. A row whose shot had no scale has no cup flow to draw and shows its
 * pressure alone, as it shows no weight.
 */
export const DEFAULT_CURVES: CurveChoice = {
  shown: ["pressure", "cupFlow"],
  colors: {},
};

export const SHOT_CURVES_KEY = "shots.curves.v2";
/** Where the choice was kept before the cup flow could be chosen: read once, see below. */
export const LEGACY_SHOT_CURVES_KEY = "shots.curves.v1";

const KNOWN_SERIES = new Set(SHOT_SERIES.map((spec) => spec.key));
const KNOWN_TOKENS = new Set(CURVE_COLOURS.map((colour) => colour.token));

// Flow and target flow take chart-5, the slot puck flow has on the shot page,
// rather than the chart-2 they have there: here puck flow is chart-2 (what the
// column always drew), and a flow curve ticked next to it in the same colour
// could not be told apart from it. The pressure pair and puck flow keep theirs.
const DEFAULT_COLOURS: Record<string, string> = {
  pressure: "--chart-1",
  puckFlow: "--chart-2",
  flow: "--chart-5",
  targetFlow: "--chart-5",
};

/** A series' own colour, for one that has not been coloured. */
export function defaultCurveColour(key: string): string {
  const fixed = DEFAULT_COLOURS[key];
  if (fixed) return fixed;
  const spec = SHOT_SERIES.find((candidate) => candidate.key === key);
  return `--chart-${(spec?.color ?? 0) + 1}`;
}

/** The token a series is drawn in: the chosen one, else its own. */
export function curveColour(choice: CurveChoice, key: string): string {
  return choice.colors[key] ?? defaultCurveColour(key);
}

/**
 * The stored choice, or the default.
 *
 * A choice kept under the old key that names puck flow and not cup flow gets the cup flow
 * added, once, and is written under the current key: the person then owns the choice, and a
 * cup flow they remove stays removed. Puck flow is never taken away.
 */
export function loadShotCurves(storage: Storage | undefined = safeStorage()): CurveChoice {
  try {
    const current = storage?.getItem(SHOT_CURVES_KEY);
    if (current) return parseChoice(current);
    const legacy = storage?.getItem(LEGACY_SHOT_CURVES_KEY);
    if (!legacy) return DEFAULT_CURVES;
    const choice = parseChoice(legacy);
    if (choice === DEFAULT_CURVES) return DEFAULT_CURVES;
    const migrated =
      choice.shown.includes("puckFlow") && !choice.shown.includes("cupFlow")
        ? { ...choice, shown: [...choice.shown, "cupFlow"] }
        : choice;
    saveShotCurves(migrated, storage);
    return migrated;
  } catch {
    return DEFAULT_CURVES;
  }
}

function parseChoice(raw: string): CurveChoice {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return DEFAULT_CURVES;
    }
    const { shown, colors } = parsed as { shown?: unknown; colors?: unknown };
    const known = Array.isArray(shown)
      ? [...new Set(shown)].filter(
          (key): key is string => typeof key === "string" && KNOWN_SERIES.has(key),
        )
      : [];
    // A curve column with no curves is not a preference anybody expressed.
    if (known.length === 0) return DEFAULT_CURVES;
    const kept: Record<string, string> = {};
    if (typeof colors === "object" && colors !== null && !Array.isArray(colors)) {
      for (const [key, token] of Object.entries(colors)) {
        // Dropped rather than repaired: a series with no entry is drawn in its own colour.
        if (KNOWN_SERIES.has(key) && typeof token === "string" && KNOWN_TOKENS.has(token)) {
          kept[key] = token;
        }
      }
    }
    return { shown: known, colors: kept };
  } catch {
    return DEFAULT_CURVES;
  }
}

export function saveShotCurves(
  choice: CurveChoice,
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(SHOT_CURVES_KEY, JSON.stringify(choice));
  } catch {
    // Not worth a toast: the column already shows what was asked for.
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
