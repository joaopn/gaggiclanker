import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

/**
 * Which collapsible cards on a settings page are open.
 *
 * Every card starts closed. A link's `#<card>` opens that one, on arrival and
 * when the hash changes on a page already showing, which is how another page
 * sends somebody to one switch (`/settings/machine#writes`). `openGroups` is for
 * the page itself: a save that fails validation opens the cards holding the bad
 * fields, since a message in a closed card reads as a save that did nothing.
 */
export function useOpenGroups() {
  const { hash } = useLocation();
  const linked = decodeHash(hash);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set(linked ? [linked] : []));

  useEffect(() => {
    if (linked)
      setOpen((previous) => (previous.has(linked) ? previous : new Set(previous).add(linked)));
  }, [linked]);

  const isOpen = useCallback((id: string) => open.has(id), [open]);

  const setGroupOpen = useCallback((id: string, next: boolean) => {
    setOpen((previous) => {
      const updated = new Set(previous);
      if (next) updated.add(id);
      else updated.delete(id);
      return updated;
    });
  }, []);

  const openGroups = useCallback((ids: Iterable<string>) => {
    setOpen((previous) => new Set([...previous, ...ids]));
  }, []);

  return { isOpen, setGroupOpen, openGroups };
}

/** `#fragments%2Fstyle` names the card `fragments/style`; a malformed escape names none. */
function decodeHash(hash: string): string {
  try {
    return decodeURIComponent(hash.slice(1));
  } catch {
    return "";
  }
}
