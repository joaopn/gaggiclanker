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
 * A Set, its versions, its shots and its chart.
 *
 * Deliberately coarse. Saving a judgement changes the shot row, the Set
 * detail's own copy of that row and the trend chart's averages; three named
 * invalidations that a call site has to remember all three of is how one of
 * them gets forgotten. Everything under `sets` is at most a handful of
 * queries.
 */
export function invalidateSets(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.sets.all }).then(() => undefined);
}

/**
 * Analyses and the suggestions hanging off them.
 *
 * Accepting a suggestion writes a Set version, so the caller invalidates `sets`
 * as well — the same "name what changed, not which keys" rule the Set
 * mutations follow.
 */
export function invalidateAnalyses(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.analyses.all }).then(() => undefined);
}

export function invalidateKnowledge(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.knowledge.all }).then(() => undefined);
}

/**
 * The profile mirror. Needed for the first time by profile push: a push changes
 * what the machine holds, and the Profiles page has to stop showing the state
 * before it.
 */
export function invalidateProfiles(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.profiles.all }).then(() => undefined);
}

/** The draft queue, and the device-write audit that every push appends to. */
export function invalidateDrafts(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.drafts.all }).then(() => undefined);
}

export function invalidateDeviceWrites(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.device.all }).then(() => undefined);
}

export function invalidateBeans(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.beans.all }).then(() => undefined);
}

export function invalidateHardware(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.hardware.all }).then(() => undefined);
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
  // `sets` is in here because an ingested shot is auto-assigned to the active
  // Set: its page gains a row and its chart gains a point, with nothing on the
  // client having asked for either.
  "shot.ingested": [queryKeys.shots.all, queryKeys.sync.all, queryKeys.sets.all],
  "shot.updated": [queryKeys.shots.all, queryKeys.sync.all, queryKeys.sets.all],
  // A shot we could not parse is still a shot: it appears in the list with a
  // flag, so the same queries are stale.
  "shot.quarantined": [queryKeys.shots.all, queryKeys.sync.all],
  "sync.progress": [queryKeys.shots.all, queryKeys.sync.all, queryKeys.device.all],
  // Storage cleanup: a cleanup starting or finishing moves the Storage card's plan,
  // its run history and its free-space figures, and it marks shots as gone from
  // the machine — so the shots list moves too. A run started in another tab
  // shows up here because of this line.
  "cleanup.progress": [queryKeys.device.all, queryKeys.shots.all, queryKeys.sync.all],
  // Notes write-back: one judgement reached the machine. The shot row's sync state and
  // the pending-notes count both change, and so does the write audit.
  "notes.writeback": [queryKeys.device.all, queryKeys.shots.all],
  // Nothing on the server publishes these two any more — the stream that
  // carried them was the device's own telemetry, and that is the machine's web
  // UI's job. They stay mapped because the key costs nothing and the header
  // pill would otherwise be the one thing in the app with no path from an
  // event to a refresh; what keeps it current today is its own 15 s poll.
  "device.connection": [queryKeys.device.all],
  "device.status": [queryKeys.device.all],
  "settings.changed": [queryKeys.settings.all],
  "profile.updated": [queryKeys.profiles.all, queryKeys.sync.all],
  // The analyzer's own events, carried on the LLM stream. A batch
  // started from the Set page moves rows on the shots list and the shot pages
  // of every shot it touches, none of which asked for anything.
  "analysis.started": [queryKeys.analyses.all, queryKeys.shots.all],
  // `knowledge` is on the finished event and on no other: a completed analysis
  // may have written proposed insights, and the shot panel and the Knowledge
  // page both list them. Without this an analysis started in another tab leaves
  // the proposals invisible until something else happens to refetch.
  "analysis.finished": [
    queryKeys.analyses.all,
    queryKeys.shots.all,
    queryKeys.sets.all,
    queryKeys.knowledge.all,
  ],
  "analysis.failed": [queryKeys.analyses.all, queryKeys.shots.all],
  // The starting-point wizard follows its own run by polling the row, so `started`
  // buys nothing there — but a run started from the chat, or in another tab,
  // has to reach the wizard too, and the key is what does it.
  "starting_point.started": [queryKeys.startingPoints.all],
  "starting_point.finished": [queryKeys.startingPoints.all],
  "starting_point.failed": [queryKeys.startingPoints.all],
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
