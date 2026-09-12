import { act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useEventInvalidation } from "@/hooks/useEventInvalidation";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";
import type { SseMessage } from "@/lib/sse";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";

const { subscribeToEventSource, lastHandlers } = vi.hoisted(() => {
  const lastHandlers: { current: { onMessage?: (m: SseMessage) => void } } = { current: {} };
  return {
    lastHandlers,
    subscribeToEventSource: vi.fn((_url: string, handlers: Record<string, unknown>) => {
      lastHandlers.current = handlers;
      return () => {};
    }),
  };
});
vi.mock("@/lib/sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sse")>()),
  subscribeToEventSource,
}));

describe("useEventInvalidation", () => {
  it("does not subscribe when there is no stream yet", () => {
    renderHookWithQueryClient(() => useEventInvalidation(null));
    expect(subscribeToEventSource).not.toHaveBeenCalled();
  });

  it("invalidates the query families an event touches", () => {
    const { queryClient } = renderHookWithQueryClient(() => useEventInvalidation("/api/events"));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    act(() => lastHandlers.current.onMessage?.({ event: "shot.ingested", data: { id: 1 } }));

    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.shots.all });
  });

  it("ignores an event nothing maps", () => {
    const { queryClient } = renderHookWithQueryClient(() => useEventInvalidation("/api/events"));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    act(() => lastHandlers.current.onMessage?.({ event: "who.knows", data: null }));

    expect(invalidate).not.toHaveBeenCalled();
  });

  it("does not re-fetch an immutable curve when a shot lands", async () => {
    // A backfill publishes one `shot.ingested` per shot. While the samples key
    // lived under the `shots` prefix, each of those re-fetched every sparkline
    // on screen and any open detail curve — TanStack re-fetches *active*
    // queries on invalidation whatever their staleTime says. Fifty shots
    // landing meant thousands of pointless requests at the appliance.
    const curve = vi.fn().mockResolvedValue({ samples: [] });
    const { queryClient } = renderHookWithQueryClient(() => useEventInvalidation("/api/events"));
    await queryClient.fetchQuery({
      queryKey: queryKeys.samples.curve("7", 40),
      queryFn: curve,
    });
    expect(curve).toHaveBeenCalledTimes(1);

    await act(async () => {
      lastHandlers.current.onMessage?.({ event: "shot.ingested", data: { imported: 50 } });
      lastHandlers.current.onMessage?.({ event: "sync.progress", data: { kind: "shots" } });
    });

    expect(curve).toHaveBeenCalledTimes(1);
  });

  it("re-reads the one curve a shot update names", () => {
    // A re-derive does move the samples, and `shot.updated` is the only event
    // that says which shot. Used to narrow, never as data: missing one costs a
    // stale copy of something that almost never changes.
    const { queryClient } = renderHookWithQueryClient(() => useEventInvalidation("/api/events"));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    act(() =>
      lastHandlers.current.onMessage?.({
        event: "shot.updated",
        data: { shot_id: 7, device_id: "000007" },
      }),
    );

    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.samples.shot("7") });
  });

  it("keeps the curve keys out of the shots prefix", () => {
    // The guard for the bug above: a prefix match is what did the damage, so
    // the two families must not share a first segment.
    expect(queryKeys.samples.curve("7", 40)[0]).not.toBe(queryKeys.shots.all[0]);
    for (const keys of Object.values(EVENT_INVALIDATIONS)) {
      for (const key of keys) {
        expect(key[0]).not.toBe(queryKeys.samples.all[0]);
      }
    }
  });

  it("maps every declared event to at least one key", () => {
    for (const [event, keys] of Object.entries(EVENT_INVALIDATIONS)) {
      expect(keys.length, event).toBeGreaterThan(0);
    }
  });
});
