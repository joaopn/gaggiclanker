import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { SetVersionDetail } from "@/api/types";

/**
 * Which version's Duplicate dialog is open on a Set page, if any.
 *
 * Opened by a button on the page (`open`) or on arrival, by `?duplicate=<version id>` (the
 * Profiles page's pinned row sends people here). The parameter opens the dialog only for a
 * version of **this** Set: an id that is not a number, unknown, or another Set's opens nothing.
 * Closing removes the parameter, so the person stays on the Set page with no dialog and a reload
 * does not open it again.
 *
 * The page keeps its state across `/sets/:setId`, so a button's choice is dropped when the route
 * moves to another Set: coming back to a Set must not find its dialog still open.
 */
export function useDuplicateDialog(
  setId: number,
  versions: SetVersionDetail[] | undefined,
): {
  versionId: number | null;
  /** The button that opened the dialog, which gets focus back; null when the URL opened it. */
  openerId: string | null;
  open: (versionId: number, openerId?: string) => void;
  close: () => void;
} {
  const [params, setParams] = useSearchParams();
  const [chosen, setChosen] = useState<{
    setId: number;
    versionId: number;
    openerId: string | null;
  } | null>(null);
  const [seen, setSeen] = useState(setId);
  if (!Object.is(seen, setId)) {
    setSeen(setId);
    setChosen(null);
  }
  const raw = params.get("duplicate");
  const asked = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  const fromUrl =
    asked !== null && versions?.some((entry) => entry.version.id === asked) ? asked : null;
  const mine = chosen !== null && chosen.setId === setId ? chosen : null;
  return {
    versionId: mine?.versionId ?? fromUrl,
    openerId: mine?.openerId ?? null,
    open: (versionId, openerId) => setChosen({ setId, versionId, openerId: openerId ?? null }),
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
