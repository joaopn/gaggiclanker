import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getDeviceStatus } from "@/api/client";
import type { DeviceStatusData } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Whether the machine is there, and what it is.
 *
 * Polled slowly on purpose. The facts here change rarely — configured,
 * connected, firmware versions — and the fast ones live on `/api/device/live`
 * instead. The `device.connection` event invalidates this key
 * (`lib/invalidate.ts`), so a socket coming up or going down refreshes it
 * immediately and the interval is only a backstop for a missed event.
 */
export function useDeviceStatus(): UseQueryResult<DeviceStatusData, Error> {
  return useQuery({
    queryKey: queryKeys.device.status(),
    queryFn: getDeviceStatus,
    refetchInterval: 30_000,
    staleTime: 10_000,
    retry: 0,
  });
}
