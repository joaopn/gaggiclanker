import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getProfiles, getShots, getSyncStatus } from "@/api/client";
import type { ProfileListData, ShotListData, ShotListParams, SyncStatusData } from "@/api/types";
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

export function useShots(params: ShotListParams = {}): UseQueryResult<ShotListData, Error> {
  return useQuery({
    // The filters are part of the key: two different filters are two different
    // cache entries, and invalidating `shots.all` still catches both.
    queryKey: queryKeys.shots.list(params as Record<string, unknown>),
    queryFn: () => getShots(params),
  });
}

export function useProfiles(): UseQueryResult<ProfileListData, Error> {
  return useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => getProfiles() });
}

export function useSyncStatus(): UseQueryResult<SyncStatusData, Error> {
  return useQuery({ queryKey: queryKeys.sync.status(), queryFn: getSyncStatus });
}
