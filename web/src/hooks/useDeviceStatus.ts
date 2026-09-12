import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getDeviceStatus, getDeviceWrites } from "@/api/client";
import type { DeviceStatusData, DeviceWritesData } from "@/api/types";
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

/**
 * Every write this box has asked the machine to make, and whether the switch
 * that allows them is on.
 *
 * Both in one query because they are read together: a list of refusals means
 * one thing when writes are off and something quite different when they are on,
 * and two requests would render the wrong sentence for a moment every time.
 */
export function useDeviceWrites(limit = 100): UseQueryResult<DeviceWritesData, Error> {
  return useQuery({
    queryKey: queryKeys.device.writes(),
    queryFn: () => getDeviceWrites(limit),
    retry: 0,
  });
}
