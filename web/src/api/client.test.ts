import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  __resetApiClientAuthForTests,
  ApiClientError,
  createBackup,
  fetchApi,
  getHealth,
  getSettings,
  hasAuthenticatedSession,
  login,
  logout,
  patchSettings,
  setAuthToken,
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

    const error = await patchSettings({ modelAnalysis: 5 as never }).catch((e) => e);
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
