import { useCallback, useRef } from "react";

/**
 * One request per click, however fast the second click comes.
 *
 * A button disabled by `mutation.isPending` is still clickable until React has re-rendered, so a
 * double-click sends two requests. This holds a ref that is set synchronously: `start` runs the
 * task only when none is in flight and hands it `release`, to be called when the request
 * settles (`mutate(vars, { onSettled: release })`).
 */
export function useSingleFlight(): (task: (release: () => void) => void) => void {
  const busy = useRef(false);
  return useCallback((task) => {
    if (busy.current) return;
    busy.current = true;
    task(() => {
      busy.current = false;
    });
  }, []);
}
