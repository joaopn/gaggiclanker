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

  it("maps every declared event to at least one key", () => {
    for (const [event, keys] of Object.entries(EVENT_INVALIDATIONS)) {
      expect(keys.length, event).toBeGreaterThan(0);
    }
  });
});
