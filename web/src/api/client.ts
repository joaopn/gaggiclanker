/**
 * The one place that talks HTTP to the gaggiclanker backend.
 *
 * Every route answers in the same envelope:
 *
 *   {"ok": true,  "data": <payload>,                       "meta": {"request_id": "..."}}
 *   {"ok": false, "error": {"code", "message", "details"}, "meta": {"request_id": "..."}}
 *
 * `fetchApi<T>()` unwraps it, so callers deal in payloads and exceptions and
 * never in `{ok}`. Endpoint wrappers below are thin and typed; add one rather
 * than calling `fetch` from a component.
 */

import type {
  BackupData,
  DeviceStatusData,
  HealthData,
  ImportOptions,
  ImportSummary,
  ProfileListData,
  SettingsMap,
  SettingsPatch,
  ShotDetailData,
  ShotListData,
  ShotListParams,
  ShotSamplesData,
  SyncStatusData,
} from "@/api/types";
import { redirectToSignIn } from "@/lib/auth-navigation";

const API_BASE = "/api";

/** localStorage, not sessionStorage: the archive is a long-lived home tab. */
const AUTH_TOKEN_KEY = "gaggiclanker.token";

export class ApiClientError extends Error {
  readonly requestId: string | undefined;
  readonly status: number | undefined;
  readonly code: string | undefined;
  /** The server's `error.details`, echoed verbatim. Never carries a secret. */
  readonly details: unknown;

  constructor(
    message: string,
    options?: { requestId?: string; status?: number; code?: string; details?: unknown },
  ) {
    const requestId = options?.requestId;
    // The request id is in the message because that is what ends up pasted
    // into a bug report, and it is the only handle on the server-side log line.
    super(requestId ? `${message} (request ${requestId})` : message);
    this.name = "ApiClientError";
    this.requestId = requestId;
    this.status = options?.status;
    this.code = options?.code;
    this.details = options?.details;
  }
}

type Envelope<T> =
  | { ok: true; data: T; meta: { request_id: string } }
  | {
      ok: false;
      error: { code: string; message: string; details?: unknown };
      meta: { request_id: string };
    };

// ---------------------------------------------------------------------------
// Token handling. Auth lands later; the plumbing is here so every request
// already carries the header and nothing has to be retrofitted into call sites.
// ---------------------------------------------------------------------------

function loadStoredToken(): string | null {
  try {
    return window.localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    // Storage blocked (private mode, embedded webview): no token, no crash.
    return null;
  }
}

function storeToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(AUTH_TOKEN_KEY, token);
    else window.localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    // Ignore: the header still works for this page load via the cache below.
  }
}

let cachedAuthToken: string | null = loadStoredToken();

export function setAuthToken(token: string | null): void {
  cachedAuthToken = token;
  storeToken(token);
}

export function clearAuthSession(): void {
  setAuthToken(null);
}

export function getCachedAuthHeader(): string | undefined {
  return cachedAuthToken ? `Bearer ${cachedAuthToken}` : undefined;
}

export function hasAuthenticatedSession(): boolean {
  return Boolean(cachedAuthToken);
}

/** Called by the SSE helper on a 401: drop the token, then send them to sign in. */
export function recoverAuthHeaderAfterUnauthorized(): void {
  clearAuthSession();
  redirectToSignIn();
}

/** Test seam: module-level token state would otherwise leak between tests. */
export function __resetApiClientAuthForTests(token: string | null = null): void {
  cachedAuthToken = token;
  storeToken(token);
}

// ---------------------------------------------------------------------------
// The fetch pipeline.
// ---------------------------------------------------------------------------

function normalizeHeaders(headers?: HeadersInit): Record<string, string> {
  if (!headers) return {};
  if (headers instanceof Headers) {
    const next: Record<string, string> = {};
    headers.forEach((value, key) => {
      next[key] = value;
    });
    return next;
  }
  if (Array.isArray(headers)) return Object.fromEntries(headers);
  return { ...headers };
}

function parseEnvelope<T>(payload: unknown, status: number): Envelope<T> {
  if (!payload || typeof payload !== "object") {
    throw new ApiClientError("API request failed: malformed JSON response", { status });
  }
  if (typeof (payload as { ok?: unknown }).ok !== "boolean") {
    throw new ApiClientError("API request failed: response is not in the envelope", { status });
  }
  return payload as Envelope<T>;
}

function toApiError<T>(status: number, parsed: Envelope<T>): ApiClientError {
  if (parsed.ok) {
    return new ApiClientError("API request failed", {
      status,
      requestId: parsed.meta?.request_id,
    });
  }
  return new ApiClientError(parsed.error.message || "API request failed", {
    status,
    code: parsed.error.code,
    details: parsed.error.details,
    requestId: parsed.meta?.request_id,
  });
}

/**
 * The whole pipeline, against an absolute path on this origin.
 *
 * Exists next to `fetchApi` because `/health` is deliberately not under
 * `/api` (see gaggiclanker/api/health.py: a healthcheck that needs a token is
 * not a healthcheck) while answering in the same envelope.
 */
export async function fetchPath<T>(path: string, options?: RequestInit): Promise<T> {
  // Content-Type describes a body, so it is set only when there is one.
  //
  // FormData is excluded because the multipart boundary is the browser's to
  // generate and overriding the header makes the server parse nothing; a
  // body-less GET is excluded because a Content-Type on a request with no
  // entity is meaningless and, with auth in front, is the sort of header
  // that turns a simple GET into a CORS preflight for no reason.
  const isFormData = typeof FormData !== "undefined" && options?.body instanceof FormData;
  const hasBody = options?.body !== undefined && options?.body !== null;
  const headers: Record<string, string> = {
    ...(hasBody && !isFormData ? { "Content-Type": "application/json" } : {}),
    ...normalizeHeaders(options?.headers),
  };
  const authHeader = getCachedAuthHeader();
  if (authHeader) headers.Authorization = authHeader;

  const response = await fetch(path, { ...options, headers });
  const text = await response.text();

  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    // Almost always one of two things: the dev proxy is not pointed at a
    // running backend and Vite served index.html, or a reverse proxy returned
    // its own error page. Both look like "undefined is not an object" three
    // frames later unless we say so here.
    throw new ApiClientError(
      `Server error (${response.status}): expected JSON but received ${describeBody(text)}. Is the backend running?`,
      { status: response.status },
    );
  }

  const parsed = parseEnvelope<T>(payload, response.status);
  if (!parsed.ok) {
    if (parsed.error.code === "UNAUTHORIZED") {
      clearAuthSession();
      redirectToSignIn();
    }
    throw toApiError(response.status, parsed);
  }
  return parsed.data;
}

/** Every `/api/*` call. `endpoint` is relative to `/api`. */
export async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  return fetchPath<T>(`${API_BASE}${endpoint}`, options);
}

function describeBody(text: string): string {
  const head = text.trimStart().slice(0, 14).toLowerCase();
  if (head.startsWith("<!doctype") || head.startsWith("<html")) return "HTML";
  return "a non-JSON body";
}

// ---------------------------------------------------------------------------
// Endpoints. One thin wrapper each.
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthData> {
  return fetchPath<HealthData>("/health");
}

export async function getSettings(): Promise<SettingsMap> {
  return fetchApi<SettingsMap>("/settings");
}

export async function patchSettings(patch: SettingsPatch): Promise<SettingsMap> {
  return fetchApi<SettingsMap>("/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function createBackup(): Promise<BackupData> {
  return fetchApi<BackupData>("/backup", { method: "POST" });
}

export async function getDeviceStatus(): Promise<DeviceStatusData> {
  return fetchApi<DeviceStatusData>("/device/status");
}

/**
 * Only the parameters that were actually set reach the query string.
 *
 * A `?quarantined=` with no value is not "no filter" to FastAPI, it is a
 * validation error, and an `undefined` stringifies to the literal "undefined" —
 * which for `profile_version_id` would 400 every request the moment a filter is
 * cleared.
 */
function queryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

export async function getShots(params: ShotListParams = {}): Promise<ShotListData> {
  return fetchApi<ShotListData>(`/shots${queryString(params)}`);
}

export async function getShot(id: number): Promise<ShotDetailData> {
  return fetchApi<ShotDetailData>(`/shots/${id}`);
}

export async function getShotSamples(id: number, downsample?: number): Promise<ShotSamplesData> {
  return fetchApi<ShotSamplesData>(`/shots/${id}/samples${queryString({ downsample })}`);
}

/** The stored `.slog` bytes. A download, not JSON — hence the raw path. */
export function shotRawUrl(id: number): string {
  return `${API_BASE}/shots/${id}/raw`;
}

export async function getProfiles(includeDeleted = false): Promise<ProfileListData> {
  return fetchApi<ProfileListData>(`/profiles${queryString({ include_deleted: includeDeleted })}`);
}

export async function getSyncStatus(): Promise<SyncStatusData> {
  return fetchApi<SyncStatusData>("/sync/status");
}

/**
 * Upload shot and profile exports. One request, many files, one result each.
 *
 * `FormData` and no `Content-Type`: the multipart boundary is the browser's to
 * generate, and setting the header by hand leaves the server parsing nothing
 * (see `fetchPath`, which excludes FormData for exactly this reason).
 *
 * A failed *file* is not a failed request — it comes back as an item with
 * `status: "failed"` — so the only rejections here are a request that never
 * arrived, was too large, or named a machine that does not exist.
 */
export async function importFiles(
  files: File[],
  options: ImportOptions = {},
): Promise<ImportSummary> {
  const body = new FormData();
  for (const file of files) body.append("files", file, file.name);
  if (options.machineId !== undefined) body.append("machine_id", String(options.machineId));
  if (options.replace) body.append("replace", "true");
  return fetchApi<ImportSummary>("/import", { method: "POST", body });
}
