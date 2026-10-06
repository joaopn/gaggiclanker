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
 * One Set's page: its detail, and nothing else under the `sets` prefix.
 *
 * For the writes that change what one Set page renders and nothing a list or a
 * chart shows — recording a prediction, grading one. The Sets list carries a
 * name, a recipe and a version count; the trend chart carries ratings,
 * ratio and duration. Neither shows a prediction or an outcome, so sweeping the whole
 * prefix would refetch two queries to redraw a badge.
 */
export function invalidateSetDetail(queryClient: QueryClient, setId: string): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.sets.detail(setId) })
    .then(() => undefined);
}

/**
 * The Sets list, both with and without the archived ones, and nothing else.
 *
 * For the writes that change what a Set's card says without touching its
 * versions' shots or its chart: a Set that starts or stops being designed
 * gains or loses its badge here, and the Chat page reads the same list to know
 * which tools a design conversation has.
 */
export function invalidateSetList(queryClient: QueryClient): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: [...queryKeys.sets.all, "list"] })
    .then(() => undefined);
}

/** Every change an agent has proposed for one Set, waiting and answered. */
export function invalidateSetProposals(queryClient: QueryClient, setId: string): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.sets.proposals(setId) })
    .then(() => undefined);
}

/** Every grade an agent has proposed for one Set's versions, waiting and answered. */
export function invalidateOutcomeProposals(queryClient: QueryClient, setId: string): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.sets.outcomeProposals(setId) })
    .then(() => undefined);
}

/** The conversation list: what the Chat page's folders are drawn from. */
export function invalidateChatThreads(queryClient: QueryClient): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.chat.threads() })
    .then(() => undefined);
}

/**
 * Every open conversation's transcript, and not the list: what the Chat page's
 * heading reads (a version's label, whether it is a dead end) is on the thread
 * detail, which a Set-side change to the current version can move.
 */
export function invalidateChatTranscripts(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: ["chat", "thread"] }).then(() => undefined);
}

/** One conversation's transcript and runs. */
export function invalidateChatThread(queryClient: QueryClient, threadId: string): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: queryKeys.chat.thread(threadId) })
    .then(() => undefined);
}

/**
 * Every open shot detail, and no other shots query.
 *
 * A version's prediction reaches a shot through that shot's own detail
 * (`set_version`), and it is the only thing about a shot that a Set-side write
 * can change. The shots *list* has no prediction column and the sync counts
 * have nothing to do with it, so `invalidateShots` — which sweeps `["shots"]`
 * and therefore every open list and its filters — would be a page-wide refetch
 * to update a strip in one panel.
 */
export function invalidateShotDetails(queryClient: QueryClient): Promise<void> {
  return queryClient
    .invalidateQueries({ queryKey: [...queryKeys.shots.all, "detail"] })
    .then(() => undefined);
}

/**
 * Everything a person's answer about a signature changes.
 *
 * A signature is read at read time, so one answer moves what every shot of the profile says:
 * the shot page's checks (`shots` detail and its fields), every list row's badge and warnings
 * (`shots` lists), and the Set pages that brew the profile (their versions carry shots with
 * badges, and the page's own signature state and overrides). The signature itself is the card
 * that was pressed. Refetching all of it is the point: a stale red badge after a confirm is
 * the bug this exists to prevent. Runs on success and on failure alike: a 409 means another tab
 * answered first, and the card must show that answer.
 */
export async function invalidateSignatureAnswers(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.signatures.all }),
    queryClient.invalidateQueries({ queryKey: queryKeys.shots.all }),
    queryClient.invalidateQueries({ queryKey: queryKeys.sets.all }),
  ]);
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

/** The board, both the mirror read and the live preview. */
export function invalidateBoard(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.board.all }).then(() => undefined);
}

/**
 * Everything a change to the board, or to whether syncs may write it, can change: the
 * board and its preview, the drafts that say whether they are on it, the profile mirror
 * the next sync rewrites, the sync status whose last run carries the summary and the pause,
 * and the write audit.
 */
export async function invalidateBoardWrites(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    invalidateBoard(queryClient),
    invalidateDrafts(queryClient),
    invalidateProfiles(queryClient),
    queryClient.invalidateQueries({ queryKey: queryKeys.sync.all }),
    invalidateDeviceWrites(queryClient),
  ]);
}

export function invalidateDeviceWrites(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.device.all }).then(() => undefined);
}

/**
 * Everything that reports the machine connection: the device status the header
 * pill reads, and the sync status whose `configured`/`connected` the Sync page
 * reads. A settings change rebuilds that connection on the server.
 */
export function invalidateDeviceConnection(queryClient: QueryClient): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.device.status() }),
    queryClient.invalidateQueries({ queryKey: queryKeys.sync.all }),
  ]).then(() => undefined);
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
  "settings.changed": [queryKeys.settings.all],
  // A sync's write phase (`{"board": true}`) changes the board's machine state, marks the
  // drafts it pushed, records the Set versions they were put on the board for, and appends to
  // the write audit: every reader of those is stale, not only the mirror.
  "profile.updated": [
    queryKeys.profiles.all,
    queryKeys.sync.all,
    queryKeys.board.all,
    queryKeys.drafts.all,
    queryKeys.sets.all,
    queryKeys.device.all,
  ],
  // A shot's review, carried on the LLM stream. Only the shot page shows one
  // (its detail carries the reviews), so only shot details are re-read: the
  // shots list carries nothing about a review.
  "review.started": [[...queryKeys.shots.all, "detail"]],
  "review.finished": [[...queryKeys.shots.all, "detail"]],
  "review.failed": [[...queryKeys.shots.all, "detail"]],
  // Find patterns across Sets, carried on the same stream: the Knowledge page's section
  // follows the run row and its proposals, and nothing else shows either.
  "patterns.started": [queryKeys.knowledge.patterns()],
  "patterns.finished": [queryKeys.knowledge.patterns()],
  "patterns.failed": [queryKeys.knowledge.patterns()],
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
