import type { QueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Named invalidations, so a mutation says what it changed rather than
 * enumerating keys. Each is a prefix match, which is why the key factory is
 * hierarchical.
 */

export function invalidateSettings(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.settings.all }).then(() => undefined);
}

export function invalidateHealth(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.health() }).then(() => undefined);
}

export function invalidateShots(queryClient: QueryClient, shotId?: string): Promise<void> {
  const queryKey = shotId ? queryKeys.shots.detail(shotId) : queryKeys.shots.all;
  return queryClient.invalidateQueries({ queryKey }).then(() => undefined);
}

/**
 * Which query families each server event touches.
 *
 * The bus is lossy (gaggiclanker/infra/sse.py), so an event means "go and
 * re-read", never "here is the new value". Keeping the map here rather than in
 * a component means a new event type is one line, and `useEventInvalidation`
 * needs no changes.
 */
export const EVENT_INVALIDATIONS: Record<string, ReadonlyArray<readonly unknown[]>> = {
  "shot.ingested": [queryKeys.shots.all],
  "shot.updated": [queryKeys.shots.all],
  "sync.progress": [queryKeys.shots.all, queryKeys.device.all],
  "device.status": [queryKeys.device.all],
  "settings.changed": [queryKeys.settings.all],
  "profile.updated": [queryKeys.profiles.all],
};
