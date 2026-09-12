import { useQueryClient } from "@tanstack/react-query";
import { useSse } from "@/hooks/useSse";
import {
  EVENT_INVALIDATIONS,
  invalidateShotSamples,
  SAMPLE_INVALIDATING_EVENTS,
  shotIdFromEvent,
} from "@/lib/invalidate";

/**
 * Turn server events into query invalidations.
 *
 * The event carries no payload we trust — the bus drops frames under pressure
 * — so an event only ever means "this family of queries is stale, go and
 * re-read". The event-to-key map lives in `lib/invalidate.ts`.
 *
 * The one exception is a payload used to *narrow*: `shot.updated` names the
 * shot it changed, and that id keeps one curve out of the cache instead of
 * every curve. Missing it costs a stale copy of something immutable, which is
 * the right side of that trade.
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
    if (SAMPLE_INVALIDATING_EVENTS.has(message.event)) {
      const shotId = shotIdFromEvent(message.data);
      if (shotId) void invalidateShotSamples(queryClient, shotId);
    }
  });
}
