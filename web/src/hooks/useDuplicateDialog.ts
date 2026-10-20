import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RecipeVersionDetail } from "@/api/types";

/**
 * Which version's Duplicate dialog is open on a Recipe page, if any.
 *
 * Opened by a button on the page (`open`) or on arrival, by `?duplicate=<version id>` (the
 * Profiles page's pinned row sends people here). The parameter opens the dialog only for a
 * version of **this** Recipe: an id that is not a number, unknown, or another Recipe's opens nothing.
 * Closing removes the parameter, so the person stays on the Recipe page with no dialog and a reload
 * does not open it again.
 *
 * The page keeps its state across `/recipes/:recipeId`, so a button's choice is dropped when the route
 * moves to another Recipe: coming back to a Recipe must not find its dialog still open.
 */
export function useDuplicateDialog(
  recipeId: number,
  versions: RecipeVersionDetail[] | undefined,
): {
  versionId: number | null;
  /** The button that opened the dialog, which gets focus back; null when the URL opened it. */
  openerId: string | null;
  open: (versionId: number, openerId?: string) => void;
  close: () => void;
} {
  const [params, setParams] = useSearchParams();
  const [chosen, setChosen] = useState<{
    recipeId: number;
    versionId: number;
    openerId: string | null;
  } | null>(null);
  const [seen, setSeen] = useState(recipeId);
  if (!Object.is(seen, recipeId)) {
    setSeen(recipeId);
    setChosen(null);
  }
  const raw = params.get("duplicate");
  const asked = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  const fromUrl =
    asked !== null && versions?.some((entry) => entry.version.id === asked) ? asked : null;
  const mine = chosen !== null && chosen.recipeId === recipeId ? chosen : null;
  return {
    versionId: mine?.versionId ?? fromUrl,
    openerId: mine?.openerId ?? null,
    open: (versionId, openerId) => setChosen({ recipeId, versionId, openerId: openerId ?? null }),
    close: () => {
      setChosen(null);
      if (params.has("duplicate")) {
        const next = new URLSearchParams(params);
        next.delete("duplicate");
        setParams(next, { replace: true });
      }
    },
  };
}
