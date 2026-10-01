import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getDeviceStatus, getDeviceWrites } from "@/api/client";
import type { DeviceStatusData, DeviceWritesData } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Whether the machine is there, and what it is.
 *
 * The only thing that answers that question now. There used to be a 2 Hz
 * telemetry stream beside it whose connection events refreshed this key the
 * instant a socket came up or went down; without it the poll is the whole
 * mechanism, so it runs every fifteen seconds rather than every thirty. That
 * is the delay between plugging the machine in and the sync button going live,
 * and it costs one small request a minute against the box's own database.
 */
export function useDeviceStatus(): UseQueryResult<DeviceStatusData, Error> {
  return useQuery({
    queryKey: queryKeys.device.status(),
    queryFn: getDeviceStatus,
    refetchInterval: 15_000,
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
