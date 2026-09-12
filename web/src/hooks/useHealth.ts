import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getHealth } from "@/api/client";
import type { HealthData } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The liveness probe behind the header status pill.
 *
 * Polled rather than pushed: `/health` is the one endpoint that must answer
 * when everything else is broken, so it must not depend on the event stream
 * being up. 30 s is frequent enough for a pill and cheap enough to ignore.
 */
export function useHealth(): UseQueryResult<HealthData, Error> {
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: getHealth,
    refetchInterval: 30_000,
    staleTime: 10_000,
    retry: 0,
  });
}
