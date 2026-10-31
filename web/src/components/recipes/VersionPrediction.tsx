import type { RecipeVersionRow } from "@/api/types";

/**
 * What this shot's version predicted, always shown.
 *
 * The prediction is part of the record next to the shot, so it is there to read whether or not
 * the shot has been judged: nothing is hidden behind a button or a state.
 *
 * Renders nothing when the shot has no Recipe, or when its version predicted nothing — most
 * versions, and an empty row saying so would be noise on every shot in the archive.
 */
export function VersionPrediction({ version }: { version: RecipeVersionRow | null | undefined }) {
  if (!version?.prediction) return null;

  return (
    <div
      className="rounded-md border border-border bg-muted/30 px-2 py-1.5"
      data-testid="version-prediction-row"
    >
      <span className="text-muted-foreground text-xs">
        Version prediction for {version.version_label}
        {version.compares_to_version_label
          ? `, compared to ${version.compares_to_version_label}`
          : ""}
      </span>
      <p className="text-sm">{version.prediction}</p>
    </div>
  );
}
