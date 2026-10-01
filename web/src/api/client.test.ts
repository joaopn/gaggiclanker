import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  __resetApiClientAuthForTests,
  ApiClientError,
  chatTranscriptUrl,
  createBackup,
  downloadFile,
  fetchApi,
  getHealth,
  getProfileBoard,
  getSettings,
  goBackOnBoard,
  hasAuthenticatedSession,
  login,
  logout,
  patchSettings,
  putOnBoard,
  setAuthToken,
  takeOntoBoard,
} from "@/api/client";

const { redirectToSignIn } = vi.hoisted(() => ({ redirectToSignIn: vi.fn() }));
vi.mock("@/lib/auth-navigation", () => ({
  redirectToSignIn,
  setAuthNavigator: vi.fn(),
  buildSignInPath: (next: string | null) => (next ? `/sign-in?next=${next}` : "/sign-in"),
  getCurrentAppPath: () => "/shots",
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function htmlResponse(status: number): Response {
  return new Response("<!doctype html><html><body>Vite</body></html>", {
    status,
    headers: { "Content-Type": "text/html" },
  });
}

function success<T>(data: T, requestId = "req-1") {
  return { ok: true, data, meta: { request_id: requestId } };
}

function failure(code: string, message: string, details?: unknown, requestId = "req-9") {
  return { ok: false, error: { code, message, details }, meta: { request_id: requestId } };
}

describe("the profile board's requests", () => {
  beforeEach(() => {
    __resetApiClientAuthForTests(null);
  });

  it("puts a draft on the board with its Set and major choice, only together", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(201, success({})));
    await putOnBoard({ draftId: 4, setId: 3, major: true });
    await putOnBoard({ draftId: 4, major: true });
    const body = (n: number) => {
      const init = spy.mock.calls[n]?.[1] as RequestInit;
      return JSON.parse(String(init.body));
    };
    expect(spy.mock.calls[0]?.[0]).toBe("/api/profile-board");
    expect(body(0)).toEqual({ draft_id: 4, set_id: 3, major: true });
    expect(body(1)).toEqual({ draft_id: 4, set_id: null });
  });

  it("sends the stop-condition acknowledgement only when it was given", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(201, success({})));
    await putOnBoard({ draftId: 4, acknowledgeStopChanges: true });
    await putOnBoard({ draftId: 4, acknowledgeStopChanges: false });
    const body = (n: number) => {
      const init = spy.mock.calls[n]?.[1] as RequestInit;
      return JSON.parse(String(init.body));
    };
    expect(body(0)).toEqual({ draft_id: 4, set_id: null, acknowledge_stop_changes: true });
    expect(body(1)).toEqual({ draft_id: 4, set_id: null });
  });

  it("goes back by the row, as a POST", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(200, success({})));
    await goBackOnBoard(7);
    expect(spy.mock.calls[0]?.[0]).toBe("/api/profile-board/7/go-back");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
  });

  it("takes a profile by the field the server reads", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(201, success({})));
    await takeOntoBoard("later");
    expect(spy.mock.calls[0]?.[0]).toBe("/api/profile-board/take");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ device_profile_id: "later" });
    expect(init.method).toBe("POST");
  });

  it("reads the machine only when asked to", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(200, success({})));
    await getProfileBoard();
    await getProfileBoard(true);
    expect(spy.mock.calls[0]?.[0]).toBe("/api/profile-board");
    expect(spy.mock.calls[1]?.[0]).toBe("/api/profile-board?live=true");
  });
});

describe("fetchApi", () => {
  beforeEach(() => {
    __resetApiClientAuthForTests(null);
    redirectToSignIn.mockClear();
  });

  it("unwraps the success envelope and returns only data", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(200, success({ status: "ok", version: "0.1.0", database: "ok" })),
      );

    await expect(getHealth()).resolves.toEqual({
      status: "ok",
      version: "0.1.0",
      database: "ok",
    });
    // /health is outside /api and must stay that way: a healthcheck behind the
    // auth guard would be useless.
    expect(fetchSpy).toHaveBeenCalledWith("/health", expect.anything());
  });

  it("prefixes /api for everything else", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({})));
    await getSettings();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/settings");
  });

  it("maps the error envelope onto ApiClientError with code, status and request id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(400, failure("INVALID_REQUEST", "Request validation failed", [{ field: "x" }])),
    );

    const error = await patchSettings({ modelReview: 5 as never }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error.code).toBe("INVALID_REQUEST");
    expect(error.status).toBe(400);
    expect(error.requestId).toBe("req-9");
    expect(error.details).toEqual([{ field: "x" }]);
    // The request id is in the message because that is what gets pasted into
    // a bug report.
    expect(error.message).toContain("req-9");
  });

  it("explains an HTML body instead of dying inside JSON.parse", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(htmlResponse(200));
    const error = await getSettings().catch((e) => e);
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error.message).toMatch(/expected JSON but received HTML/i);
    expect(error.message).toMatch(/backend running/i);
  });

  it("rejects a JSON body that is not in the envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse(200, { status: "ok" }));
    await expect(getSettings()).rejects.toThrow(/not in the envelope/);
  });

  it("attaches the bearer token when one is stored", async () => {
    setAuthToken("tok-abc");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({})));

    await getSettings();

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc");
  });

  it("sends no Authorization header when signed out", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({})));
    await getSettings();
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("persists the token so a reload keeps the session", () => {
    setAuthToken("tok-persist");
    expect(window.localStorage.getItem("gaggiclanker.token")).toBe("tok-persist");
    setAuthToken(null);
    expect(window.localStorage.getItem("gaggiclanker.token")).toBeNull();
  });

  it("lets FormData set its own Content-Type", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({ ok: 1 })));

    const body = new FormData();
    body.append("file", new Blob(["x"]), "shot.slog");
    await fetchApi("/import", { method: "POST", body });

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init.body).toBe(body);
  });

  it("sends no Content-Type on a body-less GET", async () => {
    // A Content-Type describing a body that does not exist is noise, and with
    // auth in front it is what turns a simple GET into a CORS preflight.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({})));
    await getSettings();
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("sets JSON Content-Type for a normal body", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, success({})));
    await patchSettings({ gaggimateHost: "10.0.0.5" });
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe('{"gaggimateHost":"10.0.0.5"}');
  });

  it("clears the session and redirects on UNAUTHORIZED", async () => {
    setAuthToken("stale");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(401, failure("UNAUTHORIZED", "Not authenticated")),
    );

    await expect(getSettings()).rejects.toThrow(/Not authenticated/);
    expect(window.localStorage.getItem("gaggiclanker.token")).toBeNull();
    expect(redirectToSignIn).toHaveBeenCalledTimes(1);
  });

  it("does not redirect on an ordinary failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(503, failure("SERVICE_UNAVAILABLE", "Database is unavailable")),
    );
    await expect(createBackup()).rejects.toThrow(/Database is unavailable/);
    expect(redirectToSignIn).not.toHaveBeenCalled();
  });
});

describe("the auth endpoints", () => {
  beforeEach(() => {
    __resetApiClientAuthForTests(null);
    redirectToSignIn.mockClear();
  });

  it("keeps the token a successful login returned", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(200, success({ token: "fresh", expires_in: 2592000, user: "barista" })),
    );

    await expect(login("barista", "secret")).resolves.toMatchObject({ user: "barista" });
    expect(hasAuthenticatedSession()).toBe(true);
    expect(window.localStorage.getItem("gaggiclanker.token")).toBe("fresh");
  });

  it("keeps no token when the credentials are refused", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(401, failure("UNAUTHORIZED", "Invalid username or password")),
    );

    await expect(login("barista", "wrong")).rejects.toThrow(/Invalid username or password/);
    expect(hasAuthenticatedSession()).toBe(false);
  });

  it("sends the password in the body, never in the URL", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(200, success({ token: "t", expires_in: 60, user: "barista" })),
      );

    await login("barista", "secret");

    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/auth/login");
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBe('{"username":"barista","password":"secret"}');
  });

  it("forgets the token even when logout fails on the server", async () => {
    // The one case where this matters is also the likeliest: the token had
    // already expired, so the revoke 401s. A sign-out that left you signed in
    // there would be worse than no button at all.
    setAuthToken("stale");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(401, failure("UNAUTHORIZED", "Authentication required")),
    );

    await expect(logout()).rejects.toThrow();
    expect(hasAuthenticatedSession()).toBe(false);
  });

  it("forgets the token on a clean logout", async () => {
    setAuthToken("live");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(200, success({ revoked: true })),
    );

    await logout();
    expect(hasAuthenticatedSession()).toBe(false);
    expect(window.localStorage.getItem("gaggiclanker.token")).toBeNull();
  });
});

describe("chatTranscriptUrl", () => {
  it("names the conversation, with no /log segment and no query string", () => {
    // A blocker rule (EasyPrivacy `/log?format=`) drops such URLs before they
    // leave the browser; this is the path the server registers.
    expect(chatTranscriptUrl(12)).toBe("/api/chat/threads/12/transcript");
  });
});

describe("downloadFile", () => {
  let click: ReturnType<typeof vi.spyOn>;
  let saved: { name: string; href: string }[];

  beforeEach(() => {
    __resetApiClientAuthForTests(null);
    redirectToSignIn.mockClear();
    saved = [];
    // jsdom has neither object URLs nor navigation: what is saved is what the
    // anchor was told to save, at the moment it is clicked.
    URL.createObjectURL = vi.fn(() => "blob:log");
    URL.revokeObjectURL = vi.fn();
    click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push({ name: this.download, href: this.href });
    });
  });

  function file(disposition?: string): Response {
    return new Response("# log", {
      status: 200,
      headers: {
        "Content-Type": "text/markdown",
        ...(disposition ? { "Content-Disposition": disposition } : {}),
      },
    });
  }

  it("sends the bearer token, which a plain link cannot", async () => {
    setAuthToken("tok");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(file());

    await downloadFile("/api/chat/threads/3/transcript", "chat-3.md");

    const [url, init] = fetchSpy.mock.calls[0] ?? [];
    expect(url).toBe("/api/chat/threads/3/transcript");
    expect((init as RequestInit).headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("sends no Authorization header when nobody is signed in", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(file());

    await downloadFile("/api/x", "x.md");

    const init = (fetchSpy.mock.calls[0]?.[1] ?? {}) as RequestInit;
    expect(init.headers).toBeUndefined();
  });

  it("saves the blob under the name the server gave it", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      file('attachment; filename="chat-3-dialling-in.md"'),
    );

    await expect(downloadFile("/api/x", "chat-3.md")).resolves.toBe("chat-3-dialling-in.md");

    expect(saved).toEqual([{ name: "chat-3-dialling-in.md", href: "blob:log" }]);
    expect(click).toHaveBeenCalledTimes(1);
    // Not on the same tick as the click; a moment later.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:log"));
  });

  it("never lets the server's name be a path", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      file('attachment; filename="../../etc/passwd"'),
    );

    await downloadFile("/api/x", "fallback.md");

    expect(saved[0]?.name).toBe("passwd");
  });

  it("falls back to the given name when there is no Content-Disposition", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(file());

    await downloadFile("/api/x", "chat-3.md");

    expect(saved[0]?.name).toBe("chat-3.md");
  });

  it("saves nothing and throws the envelope's error on a 404", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(404, failure("NOT_FOUND", "No chat thread 3")),
    );

    await expect(downloadFile("/api/x", "x.md")).rejects.toMatchObject({
      message: "No chat thread 3 (request req-9)",
      code: "NOT_FOUND",
      status: 404,
    });

    expect(click).not.toHaveBeenCalled();
    expect(saved).toEqual([]);
  });

  it("saves nothing, drops the session and goes to sign-in on a 401", async () => {
    setAuthToken("stale");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(401, failure("UNAUTHORIZED", "Sign in first")),
    );

    await expect(downloadFile("/api/x", "x.md")).rejects.toMatchObject({ status: 401 });

    expect(click).not.toHaveBeenCalled();
    expect(hasAuthenticatedSession()).toBe(false);
    expect(redirectToSignIn).toHaveBeenCalled();
  });

  it("says the download was blocked when fetch itself throws, and saves nothing", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const error = await downloadFile("/api/x", "x.json").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect((error as Error).message).toMatch(/blocked before it reached the app/);
    expect((error as Error).message).toMatch(/content-blocking extension/);
    expect((error as Error).message).not.toMatch(/Failed to fetch/);
    expect(click).not.toHaveBeenCalled();
  });

  it("lets any other failure of fetch through untouched", async () => {
    const boom = new Error("aborted");
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(boom);

    await expect(downloadFile("/api/x", "x.json")).rejects.toBe(boom);
  });

  it("saves nothing when the error is not an envelope either", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(htmlResponse(502));

    await expect(downloadFile("/api/x", "x.md")).rejects.toThrow("Download failed (502)");

    expect(click).not.toHaveBeenCalled();
  });
});
