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
 * One shot's stored curve.
 *
 * Never swept up with the list: samples are immutable once a shot is ingested,
 * and the only things that change them are a re-derive and an import with
 * `replace`. Both know which shot they touched, which is why this takes an id.
 */
export function invalidateShotSamples(queryClient: QueryClient, shotId: string): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.samples.shot(shotId) })
    .then(() => undefined);
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
  // Note what is *not* here: `queryKeys.samples`. A backfill publishes one of
  // these per shot, and a prefix that reached the curves would re-fetch every
  // sparkline on screen fifty times over — TanStack refetches active queries
  // on invalidation whatever their staleTime says.
  "shot.ingested": [queryKeys.shots.all, queryKeys.sync.all],
  "shot.updated": [queryKeys.shots.all, queryKeys.sync.all],
  // A shot we could not parse is still a shot: it appears in the list with a
  // flag, so the same queries are stale.
  "shot.quarantined": [queryKeys.shots.all, queryKeys.sync.all],
  "sync.progress": [queryKeys.shots.all, queryKeys.sync.all, queryKeys.device.all],
  // The rare one: the socket came up or went down, so re-read
  // /api/device/status. `device.live` is deliberately absent — it arrives
  // twice a second and is read straight off the stream.
  "device.connection": [queryKeys.device.all],
  "device.status": [queryKeys.device.all],
  "settings.changed": [queryKeys.settings.all],
  "profile.updated": [queryKeys.profiles.all, queryKeys.sync.all],
};

/**
 * The events whose payload names a shot whose stored curve may have moved.
 *
 * The only event that carries a `shot_id` at all. It is used as a *narrowing*
 * hint rather than as data: the bus is lossy, so the worst case of missing one
 * is a stale copy of something that almost never changes.
 */
export const SAMPLE_INVALIDATING_EVENTS = new Set(["shot.updated"]);

/** The `shot_id` out of an event payload, when it has one we can use. */
export function shotIdFromEvent(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const id = (data as { shot_id?: unknown }).shot_id;
  return typeof id === "number" || typeof id === "string" ? String(id) : null;
}
