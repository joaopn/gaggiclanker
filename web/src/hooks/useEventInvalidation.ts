import { useQueryClient } from "@tanstack/react-query";
import { useSse } from "@/hooks/useSse";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";

/**
 * Turn server events into query invalidations.
 *
 * The event carries no payload we trust — the bus drops frames under pressure
 * — so an event only ever means "this family of queries is stale, go and
 * re-read". The event-to-key map lives in `lib/invalidate.ts`.
 *
 * The stream itself arrives with the device client; until then `url` can be null and this
 * hook does nothing, which is why it is wired in from the start.
 */
export function useEventInvalidation(url: string | null): { connected: boolean } {
  const queryClient = useQueryClient();
  return useSse(url, (message) => {
    const keys = EVENT_INVALIDATIONS[message.event];
    if (!keys) return;
    for (const queryKey of keys) {
      void queryClient.invalidateQueries({ queryKey });
    }
  });
}
