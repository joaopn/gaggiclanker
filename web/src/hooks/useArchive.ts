import {
  type UseInfiniteQueryResult,
  type UseQueryResult,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query";
import {
  getProfiles,
  getProfileVersion,
  getProfileVersions,
  getShot,
  getShotFields,
  getShotSamples,
  getShots,
  getSyncStatus,
} from "@/api/client";
import type {
  ProfileListData,
  ProfileVersionListData,
  ProfileVersionParams,
  ProfileVersionRow,
  ShotDetailData,
  ShotFieldsData,
  ShotListData,
  ShotListParams,
  ShotSamplesData,
  SyncStatusData,
} from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Reads of the archive the sync engine fills.
 *
 * None of these poll. `/api/sync/events` pushes `shot.ingested`,
 * `shot.quarantined` and `profile.updated`, and `lib/invalidate.ts` maps each
 * one onto these query keys — so a shot pulled on the machine appears here a
 * moment later without a timer, and a browser tab left open overnight costs
 * nothing. The event carries no payload we trust (the bus drops events under
 * backpressure), so it only ever means "go and re-read".
 */

/**
 * How often a list with a review running re-reads itself.
 *
 * The event stream says when a review finishes, but the bus is lossy and the window does not
 * refetch on focus, so a `Reviewing…` badge whose event was lost would stay for ever. While any
 * loaded row is `running` the list polls; `refetchInterval` returning `false` is what stops it.
 */
export const READING_POLL_MS = 5000;

function anyReading(rows: ReadonlyArray<{ review?: { state?: string } }> | undefined): boolean {
  return (rows ?? []).some((row) => row.review?.state === "running");
}

export function useShots(params: ShotListParams = {}): UseQueryResult<ShotListData, Error> {
  return useQuery({
    refetchInterval: (query) => (anyReading(query.state.data?.items) ? READING_POLL_MS : false),
    // The filters are part of the key: two different filters are two different
    // cache entries, and invalidating `shots.all` still catches both.
    queryKey: queryKeys.shots.list(params as Record<string, unknown>),
    queryFn: () => getShots(params),
  });
}

/**
 * The list page's own query: keyset paging over `(started_at, id)`.
 *
 * Cursor rather than offset, because the sync engine inserts rows *above* the
 * reader while they scroll — on this appliance, continuously. With offsets,
 * every shot that lands while somebody is on page three pushes one shot they
 * have already seen onto page four, and they read it twice.
 *
 * A refetch re-runs every page that has been loaded, so the SSE invalidation
 * refreshes a long scroll in place rather than collapsing it back to fifty rows.
 */
export type ShotPageParam = { cursor?: string; offset?: number };

export function useShotsInfinite(
  params: ShotListParams = {},
): UseInfiniteQueryResult<{ pages: ShotListData[] }, Error> {
  // A cursor is only issued for `started_at` walked *backwards*: the cursor is
  // that sort key and the comparison in the SQL is a strict `<`
  // (`gaggiclanker/db/repos/shots.py`). Every other view — "worst shots first",
  // and "oldest first" too — pages by offset. Deciding on the sort alone left
  // "oldest first" asking for a cursor the server never hands back, so the list
  // stopped after one page and said nothing, which is the worst way for paging
  // to fail.
  const keyset =
    (params.sort ?? "started_at") === "started_at" && (params.order ?? "desc") === "desc";
  return useInfiniteQuery({
    queryKey: queryKeys.shots.list(params as Record<string, unknown>),
    queryFn: ({ pageParam }) => getShots({ ...params, ...pageParam }),
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => anyReading(page.items)) ? READING_POLL_MS : false,
    initialPageParam: {} as ShotPageParam,
    getNextPageParam: (last, pages): ShotPageParam | undefined => {
      if (keyset) return last.next_cursor ? { cursor: last.next_cursor } : undefined;
      const loaded = pages.reduce((count, page) => count + page.items.length, 0);
      return loaded < last.total ? { offset: loaded } : undefined;
    },
  });
}

export function useShot(id: number | undefined): UseQueryResult<ShotDetailData, Error> {
  return useQuery({
    queryKey: queryKeys.shots.detail(String(id)),
    queryFn: () => getShot(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/** A shot's fields: the warnings, the phase table and the shot-wide numbers the page is built from. */
export function useShotFields(
  id: number | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<ShotFieldsData, Error> {
  const { enabled = true } = options;
  return useQuery({
    queryKey: queryKeys.shots.fields(String(id)),
    queryFn: () => getShotFields(id as number),
    enabled: enabled && id !== undefined && Number.isFinite(id),
  });
}

/**
 * A shot's curve, whole or thinned.
 *
 * `enabled` is how the list defers a sparkline until its row scrolls into
 * view: a hundred rows must not pull a hundred curves on mount. The cache is
 * what makes scrolling back up free.
 */
export function useShotSamples(
  id: number | undefined,
  options: { downsample?: number; enabled?: boolean } = {},
): UseQueryResult<ShotSamplesData, Error> {
  const { downsample, enabled = true } = options;
  return useQuery({
    queryKey: queryKeys.samples.curve(String(id), downsample),
    queryFn: () => getShotSamples(id as number, downsample),
    enabled: enabled && id !== undefined && Number.isFinite(id),
    // Samples are immutable once a shot is ingested; re-reading them is pure
    // cost. Only a re-derive changes them, and the key sits outside the
    // `shots` prefix so that an ingest cannot sweep them all up
    // (`lib/queryKeys.ts`).
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useProfiles(): UseQueryResult<ProfileListData, Error> {
  return useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => getProfiles() });
}

export function useProfileVersions(
  params: ProfileVersionParams = {},
): UseQueryResult<ProfileVersionListData, Error> {
  return useQuery({
    queryKey: queryKeys.profiles.versions(params as Record<string, unknown>),
    queryFn: () => getProfileVersions(params),
  });
}

/** One version's full document. Fetched only when the draft editor opens it. */
export function useProfileVersion(
  id: number | undefined,
): UseQueryResult<ProfileVersionRow, Error> {
  return useQuery({
    queryKey: queryKeys.profiles.version(String(id)),
    queryFn: () => getProfileVersion(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useSyncStatus(): UseQueryResult<SyncStatusData, Error> {
  return useQuery({ queryKey: queryKeys.sync.status(), queryFn: getSyncStatus });
}
