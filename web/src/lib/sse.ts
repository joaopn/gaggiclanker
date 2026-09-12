/**
 * Server-sent events over `fetch` + a stream reader, not `EventSource`.
 *
 * `EventSource` cannot send an `Authorization` header, and auth puts a bearer
 * token in front of every `/api/*` route. Reading the body ourselves also lets
 * us classify failures: a 401 is fatal (the token is gone; reconnecting just
 * hammers the server), everything else is transient and gets exponential
 * backoff up to 15 s and retries forever.
 *
 * The backend's bus (`gaggiclanker/infra/sse.py`) is lossy by design — a tab
 * that stops reading drops frames rather than stalling the device loop — so a
 * consumer must treat an event as "something changed, go and re-read", never
 * as the only copy of a fact. That is exactly what `useEventInvalidation`
 * below does.
 */

import { getCachedAuthHeader, recoverAuthHeaderAfterUnauthorized } from "@/api/client";

export const RECONNECT_BASE_DELAY_MS = 1000;
export const RECONNECT_MAX_DELAY_MS = 15000;

export type SseErrorInfo = {
  /**
   * `true` means the subscription is over and will not retry. Only a 401 is
   * fatal; a caller that tears down and re-subscribes on a transient error
   * aborts the pending backoff and turns a blip into a retry storm.
   */
  fatal: boolean;
  status?: number;
};

export type SseMessage<T = unknown> = {
  /** The SSE `event:` field, or "message" when the server sent none. */
  event: string;
  data: T;
  id?: string;
};

export type SseHandlers<T> = {
  onOpen?: () => void;
  onMessage: (message: SseMessage<T>) => void;
  onError?: (info: SseErrorInfo) => void;
};

/** Next backoff delay. Exported so a test can pin the curve, not re-derive it. */
export function nextBackoff(current: number): number {
  return Math.min(current * 2, RECONNECT_MAX_DELAY_MS);
}

/**
 * Where one event ends and the next begins.
 *
 * NOT just "\n\n". `sse-starlette` — what the backend uses — separates lines
 * with CRLF by default, so a real frame ends `\r\n\r\n`. Splitting on "\n\n"
 * alone never matched it: `onMessage` fired zero times against the actual
 * server while every jsdom test with LF fixtures passed, and the buffer grew for
 * as long as the tab stayed open. The spec allows CRLF, LF and CR, so all three
 * are accepted here and the server keeps its default.
 *
 * The alternation is also why the buffer is kept raw rather than normalised on
 * arrival: a chunk boundary can fall between the `\r` and the `\n` of the
 * separator, and rewriting each chunk in isolation would turn that into a lone
 * `\r` that never completes.
 */
const FRAME_SEPARATOR = /\r\n\r\n|\n\n|\r\r/;

/** Split off the first complete frame, or null while one is still arriving. */
export function takeFrame(buffer: string): { frame: string; rest: string } | null {
  const match = FRAME_SEPARATOR.exec(buffer);
  if (!match) return null;
  return {
    frame: buffer.slice(0, match.index),
    rest: buffer.slice(match.index + match[0].length),
  };
}

/** A 401 is the only failure worth giving up on. */
export function isFatalStatus(status: number): boolean {
  return status === 401;
}

/** One `event:`/`data:`/`id:` block. Returns null when it carried no data. */
export function parseSseFrame(frame: string): { event: string; data: string; id?: string } | null {
  const dataLines: string[] = [];
  let event = "message";
  let id: string | undefined;
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // a comment / keep-alive ping
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("id:")) id = line.slice(3).trim();
  }
  if (dataLines.length === 0) return null;
  return id === undefined
    ? { event, data: dataLines.join("\n") }
    : { event, data: dataLines.join("\n"), id };
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Subscribe to `url` until the returned function is called.
 *
 * Every option is deliberate; see the module docstring for why this is not an
 * `EventSource`.
 */
export function subscribeToEventSource<T = unknown>(
  url: string,
  handlers: SseHandlers<T>,
): () => void {
  const controller = new AbortController();
  let isClosed = false;

  void (async () => {
    let reconnectDelay = RECONNECT_BASE_DELAY_MS;

    while (!isClosed) {
      try {
        const authHeader = getCachedAuthHeader();
        const response = await fetch(url, {
          headers: authHeader
            ? { Authorization: authHeader, Accept: "text/event-stream" }
            : { Accept: "text/event-stream" },
          signal: controller.signal,
        });

        if (isFatalStatus(response.status)) {
          // Release the socket: an un-consumed body keeps the connection in the
          // pool until the browser gives up on it.
          void response.body?.cancel().catch(() => {});
          recoverAuthHeaderAfterUnauthorized();
          handlers.onError?.({ fatal: true, status: response.status });
          return;
        }

        if (!response.ok || !response.body) {
          void response.body?.cancel().catch(() => {});
          handlers.onError?.({ fatal: false, status: response.status });
          await delay(reconnectDelay, controller.signal);
          reconnectDelay = nextBackoff(reconnectDelay);
          continue;
        }

        // Live again: reset the curve so the next unrelated blip starts at 1 s.
        reconnectDelay = RECONNECT_BASE_DELAY_MS;
        handlers.onOpen?.();

        const decoder = new TextDecoder();
        const reader = response.body.getReader();
        let buffer = "";

        try {
          while (!isClosed) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // `decode({stream: true})` already holds back a partial multi-byte
            // character; `takeFrame` holds back a partial frame the same way, so
            // an event split across two chunks is reassembled rather than lost.
            let next = takeFrame(buffer);
            while (next) {
              buffer = next.rest;
              const parsed = parseSseFrame(next.frame);
              if (parsed) {
                try {
                  handlers.onMessage({
                    event: parsed.event,
                    data: JSON.parse(parsed.data) as T,
                    ...(parsed.id === undefined ? {} : { id: parsed.id }),
                  });
                } catch {
                  // A malformed frame must not kill the stream: the next event
                  // is probably fine, and the bus is lossy anyway.
                }
              }
              next = takeFrame(buffer);
            }
          }
        } finally {
          try {
            await reader.cancel();
          } catch {
            // Already closed; nothing to do.
          }
        }

        if (!isClosed) {
          handlers.onError?.({ fatal: false });
          await delay(reconnectDelay, controller.signal);
          reconnectDelay = nextBackoff(reconnectDelay);
        }
      } catch {
        if (isClosed || controller.signal.aborted) return;
        handlers.onError?.({ fatal: false });
        await delay(reconnectDelay, controller.signal);
        reconnectDelay = nextBackoff(reconnectDelay);
      }
    }
  })();

  return () => {
    isClosed = true;
    controller.abort();
  };
}
