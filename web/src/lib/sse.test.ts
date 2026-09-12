import { beforeEach, describe, expect, it, vi } from "vitest";
import { __resetApiClientAuthForTests, setAuthToken } from "@/api/client";
import {
  isFatalStatus,
  nextBackoff,
  parseSseFrame,
  RECONNECT_BASE_DELAY_MS,
  RECONNECT_MAX_DELAY_MS,
  subscribeToEventSource,
  takeFrame,
} from "@/lib/sse";

const { redirectToSignIn } = vi.hoisted(() => ({ redirectToSignIn: vi.fn() }));
vi.mock("@/lib/auth-navigation", () => ({
  redirectToSignIn,
  setAuthNavigator: vi.fn(),
  buildSignInPath: () => "/sign-in",
  getCurrentAppPath: () => "/shots",
}));

/** A ReadableStream that emits `chunks` and then ends, like a closed stream. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

function streamResponse(chunks: string[], status = 200): Response {
  return new Response(streamOf(chunks), {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** Resolve once the microtask/timer queue has drained enough for the loop to run. */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("parseSseFrame", () => {
  it("joins multi-line data and reads the event name and id", () => {
    expect(parseSseFrame('event: shot.ingested\nid: 7\ndata: {"a":\ndata: 1}')).toEqual({
      event: "shot.ingested",
      id: "7",
      data: '{"a":\n1}',
    });
  });

  it("defaults the event name to message", () => {
    expect(parseSseFrame("data: 1")).toEqual({ event: "message", data: "1" });
  });

  it("ignores a keep-alive comment frame", () => {
    // sse-starlette sends `: ping` every 15 s; it carries no data and must not
    // reach a handler as an event.
    expect(parseSseFrame(": ping")).toBeNull();
  });
});

describe("takeFrame", () => {
  // sse-starlette separates lines with CRLF by default, so the bytes the real
  // backend sends end "\r\n\r\n". Splitting on "\n\n" alone matched none of
  // them: every LF-based test passed and onMessage never fired in production.
  it("accepts CRLF, LF and CR separators", () => {
    expect(takeFrame("data: 1\r\n\r\ndata: 2")).toEqual({
      frame: "data: 1",
      rest: "data: 2",
    });
    expect(takeFrame("data: 1\n\ndata: 2")).toEqual({ frame: "data: 1", rest: "data: 2" });
    expect(takeFrame("data: 1\r\rdata: 2")).toEqual({ frame: "data: 1", rest: "data: 2" });
  });

  it("holds back an incomplete frame", () => {
    expect(takeFrame("data: 1")).toBeNull();
    // The chunk boundary fell between the CR and the LF of the separator.
    // Treating this as complete would emit a half-frame; normalising each chunk
    // in isolation would leave a lone CR that never completes.
    expect(takeFrame("data: 1\r\n\r")).toBeNull();
  });
});

describe("backoff classification", () => {
  it("doubles up to the ceiling", () => {
    expect(nextBackoff(RECONNECT_BASE_DELAY_MS)).toBe(2000);
    expect(nextBackoff(8000)).toBe(RECONNECT_MAX_DELAY_MS);
    expect(nextBackoff(RECONNECT_MAX_DELAY_MS)).toBe(RECONNECT_MAX_DELAY_MS);
  });

  it("treats only 401 as fatal", () => {
    expect(isFatalStatus(401)).toBe(true);
    for (const status of [500, 503, 404, 200]) {
      expect(isFatalStatus(status)).toBe(false);
    }
  });
});

describe("subscribeToEventSource", () => {
  beforeEach(() => {
    __resetApiClientAuthForTests(null);
    redirectToSignIn.mockClear();
  });

  it("delivers parsed events and reports the stream opening", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse(['event: shot.ingested\ndata: {"id":129}\n\n', 'data: {"id":130}\n\n']),
    );

    const onMessage = vi.fn();
    const onOpen = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage, onOpen });
    await flush();
    unsubscribe();

    expect(onOpen).toHaveBeenCalled();
    expect(onMessage).toHaveBeenNthCalledWith(1, {
      event: "shot.ingested",
      data: { id: 129 },
    });
    expect(onMessage).toHaveBeenNthCalledWith(2, { event: "message", data: { id: 130 } });
  });

  it("reads the CRLF frames sse-starlette actually sends", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse([
        'event: device.status\r\ndata: {"t":93}\r\n\r\n',
        ": ping\r\n\r\n",
        'event: shot.ingested\r\ndata: {"id":129}\r\n\r\n',
      ]),
    );

    const onMessage = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage });
    await flush();
    unsubscribe();

    expect(onMessage).toHaveBeenCalledTimes(2);
    expect(onMessage).toHaveBeenNthCalledWith(1, {
      event: "device.status",
      data: { t: 93 },
    });
    expect(onMessage).toHaveBeenNthCalledWith(2, {
      event: "shot.ingested",
      data: { id: 129 },
    });
  });

  it("reassembles a frame split across chunks, separator included", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      // The split falls inside the JSON payload and then between the CR and the
      // LF of the separator - both are ordinary TCP behaviour, not edge cases.
      streamResponse(['event: shot.ingested\r\ndata: {"id":', "129}\r\n\r", "\n"]),
    );

    const onMessage = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage });
    await flush();
    unsubscribe();

    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith({ event: "shot.ingested", data: { id: 129 } });
  });

  it("survives a malformed frame and keeps reading", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse(["data: not json\n\n", 'data: {"ok":true}\n\n']),
    );

    const onMessage = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage });
    await flush();
    unsubscribe();

    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith({ event: "message", data: { ok: true } });
  });

  it("sends the bearer token, which is the whole reason this is not an EventSource", async () => {
    setAuthToken("tok-sse");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(streamResponse([]));

    const unsubscribe = subscribeToEventSource("/api/events", { onMessage: vi.fn() });
    await flush();
    unsubscribe();

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-sse");
  });

  it("treats 401 as fatal: clears the session, redirects, and stops", async () => {
    setAuthToken("stale");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("", { status: 401 }));

    const onError = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage: vi.fn(), onError });
    await flush();
    unsubscribe();

    expect(onError).toHaveBeenCalledWith({ fatal: true, status: 401 });
    expect(redirectToSignIn).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem("gaggiclanker.token")).toBeNull();
    // No reconnect: a retry with the same dead token is a request storm.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("treats a 503 as transient and backs off rather than giving up", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("", { status: 503 }));

    const onError = vi.fn();
    const unsubscribe = subscribeToEventSource("/api/events", { onMessage: vi.fn(), onError });

    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledWith({ fatal: false, status: 503 });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // The firmware answers 503 for the whole of an OTA update, so this is the
    // normal path, not the exceptional one.
    await vi.advanceTimersByTimeAsync(RECONNECT_BASE_DELAY_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2 * RECONNECT_BASE_DELAY_MS);
    expect(fetchSpy).toHaveBeenCalledTimes(3);

    unsubscribe();
    vi.useRealTimers();
  });

  it("stops fetching once unsubscribed", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("", { status: 500 }));

    const unsubscribe = subscribeToEventSource("/api/events", { onMessage: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    unsubscribe();

    const callsAtUnsubscribe = fetchSpy.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchSpy).toHaveBeenCalledTimes(callsAtUnsubscribe);
    vi.useRealTimers();
  });
});
