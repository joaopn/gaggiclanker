import { useEffect, useRef, useState } from "react";
import { type SseHandlers, type SseMessage, subscribeToEventSource } from "@/lib/sse";

/**
 * Subscribe to an SSE endpoint for the lifetime of the component.
 *
 * The handler is held in a ref so a caller can pass an inline arrow function
 * without tearing the stream down and reconnecting on every render — which is
 * the classic way to turn a working subscription into a reconnect loop.
 */
export function useSse<T = unknown>(
  url: string | null,
  onEvent: (message: SseMessage<T>) => void,
  options: { enabled?: boolean } = {},
): { connected: boolean } {
  const { enabled = true } = options;
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!url || !enabled) return;
    const handlers: SseHandlers<T> = {
      onOpen: () => setConnected(true),
      onMessage: (message) => handlerRef.current(message),
      onError: () => setConnected(false),
    };
    const unsubscribe = subscribeToEventSource<T>(url, handlers);
    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [url, enabled]);

  return { connected };
}
