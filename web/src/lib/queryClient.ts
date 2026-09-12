import { QueryClient } from "@tanstack/react-query";

/**
 * One client per app. The defaults are tuned for a single-user tool on a LAN:
 *
 * - `staleTime` 30 s: the interesting data changes when the machine pulls a
 *   shot, and that arrives as an SSE event which invalidates explicitly. Polling
 *   harder buys nothing.
 * - `retry: 1`: the backend is one process one hop away. A real failure is real;
 *   retrying three times only delays the error toast.
 * - `refetchOnWindowFocus: false`: this tab lives open on a kitchen tablet for
 *   days; refetching everything each time it is touched is noise.
 */
export const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 10 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: 0 },
    },
  });

export const queryClient = createQueryClient();
