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
  AcceptedSuggestion,
  Analysis,
  AuthStatusData,
  BackupData,
  BatchResult,
  BeanRow,
  BeanWrite,
  ChatRun,
  ChatSendResult,
  ChatThread,
  ChatThreadDetail,
  ChatThreadWrite,
  ChatToolList,
  CleanupPlan,
  CleanupRunAccepted,
  CleanupRunsData,
  DeviceStatusData,
  DeviceWritesData,
  DraftCreateBody,
  DraftPreview,
  DraftPushResult,
  FlavorPicks,
  GrinderRow,
  GrinderWrite,
  HealthData,
  ImportOptions,
  ImportSummary,
  JudgementWrite,
  KnowledgeDocDetail,
  KnowledgeDocListData,
  KnowledgeInsight,
  KnowledgeInsightCreate,
  KnowledgeInsightListData,
  KnowledgeInsightPatch,
  KnowledgeRule,
  KnowledgeRuleListData,
  KnowledgeRulePatch,
  KnowledgeSearchData,
  LlmCallsData,
  LlmCredentialCheck,
  LlmModelsData,
  LlmPurpose,
  LlmRateLimit,
  LlmStatusData,
  LlmUsageTotals,
  LoginData,
  MachineData,
  MachinePatch,
  MachineRow,
  NotesPushAccepted,
  PasswordData,
  PendingNotesData,
  ProfileDraft,
  ProfileDraftDetail,
  ProfileDraftListData,
  ProfileListData,
  ProfileVersionListData,
  ProfileVersionParams,
  ProfileVersionRow,
  PromptData,
  PromptListData,
  RollbackWrite,
  SetCreate,
  SetDetailData,
  SetListData,
  SetRow,
  SetTrends,
  SettingsMap,
  SettingsPatch,
  SetVersionPatch,
  SetVersionRow,
  ShotDetailData,
  ShotDetailRow,
  ShotJudgement,
  ShotListData,
  ShotListParams,
  ShotSamplesData,
  SimilarSetsData,
  StartingPointAccepted,
  StartingPointRequest,
  StartingPointRun,
  Suggestion,
  SuggestionListData,
  SyncStatusData,
  VersionOutcomeWrite,
  VersionPredictionWrite,
  Vocabulary,
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
// Token handling. Every request already carries the header, so a call site
// never thinks about auth; only the sign-in page and the header's sign-out
// control touch the functions below.
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

/**
 * Whether this server wants a token, and whether ours is still good.
 *
 * Public, so it answers before we have signed in — which is what lets the app
 * show a sign-in page up front instead of bouncing off the first 401. This is
 * the one endpoint that must NOT clear the session on a 401, and it cannot:
 * the route never returns one.
 */
export async function getAuthStatus(): Promise<AuthStatusData> {
  return fetchApi<AuthStatusData>("/auth/status");
}

/** Sign in and keep the token. Throws `ApiClientError` on bad credentials. */
export async function login(username: string, password: string): Promise<LoginData> {
  const data = await fetchApi<LoginData>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setAuthToken(data.token);
  return data;
}

/**
 * Set the sign-in password. The plain value is hashed on the server.
 *
 * `authPasswordHash` is read-only through `PATCH /api/settings` precisely so
 * this is the only way: a browser never holds a hash, and nothing can store a
 * value in that field that argon2 cannot verify. Every session is revoked,
 * including this one, so the caller is signed out on success.
 */
export async function setPassword(input: {
  currentPassword?: string;
  newPassword: string;
}): Promise<PasswordData> {
  return fetchApi<PasswordData>("/auth/password", {
    method: "POST",
    body: JSON.stringify({
      ...(input.currentPassword ? { current_password: input.currentPassword } : {}),
      new_password: input.newPassword,
    }),
  });
}

/**
 * Revoke this session on the server, then forget it here.
 *
 * The local half runs whatever the server said. A logout that failed because
 * the token had already expired must still clear the token, or the sign-out
 * button does nothing on the one page where it is most obviously needed.
 */
export async function logout(): Promise<void> {
  try {
    await fetchApi<unknown>("/auth/logout", { method: "POST" });
  } finally {
    clearAuthSession();
  }
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

/**
 * The JSON a "download this shot" button hands over.
 *
 * Built in the browser rather than served, because there is no endpoint that
 * returns the row and its curve in one document and adding one would duplicate
 * two that already exist. The file is what a reader would reconstruct anyway:
 * the detail payload with its samples attached.
 */
export async function getShotExport(id: number): Promise<unknown> {
  const [detail, samples] = await Promise.all([getShot(id), getShotSamples(id)]);
  return { ...detail, samples };
}

export async function getProfileVersions(
  params: ProfileVersionParams = {},
): Promise<ProfileVersionListData> {
  return fetchApi<ProfileVersionListData>(`/profile-versions${queryString(params)}`);
}

/** Ask the sync engine to run now. 202 and returns before any device I/O. */
export async function runSync(kind = "all"): Promise<{ queued: string[] }> {
  return fetchApi<{ queued: string[] }>("/sync/run", {
    method: "POST",
    body: JSON.stringify({ kind }),
  });
}

/** One immutable version, with the document the drafts editor starts from. */
export async function getProfileVersion(id: number): Promise<ProfileVersionRow> {
  return fetchApi<ProfileVersionRow>(`/profile-versions/${id}`);
}

export async function getProfiles(includeDeleted = false): Promise<ProfileListData> {
  return fetchApi<ProfileListData>(`/profiles${queryString({ include_deleted: includeDeleted })}`);
}

// ---------------------------------------------------------------------------
// Profile drafts: the only routes in this client that can change a
// machine. `pushDraft` answers 200 for a push that *failed verification* too —
// the draft's `status` is the outcome, not the HTTP code — because the profile
// is on the display either way and the caller has to be told which.
// ---------------------------------------------------------------------------

export async function getProfileDrafts(
  params: { status?: string; open?: boolean } = {},
): Promise<ProfileDraftListData> {
  return fetchApi<ProfileDraftListData>(`/profile-drafts${queryString(params)}`);
}

export async function getProfileDraft(id: number): Promise<ProfileDraftDetail> {
  return fetchApi<ProfileDraftDetail>(`/profile-drafts/${id}`);
}

export async function createProfileDraft(body: DraftCreateBody): Promise<ProfileDraft> {
  return fetchApi<ProfileDraft>("/profile-drafts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function refineProfileDraft(
  id: number,
  body: { notes?: string; model?: string } = {},
): Promise<ProfileDraft> {
  return fetchApi<ProfileDraft>(`/profile-drafts/${id}/refine`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function approveProfileDraft(
  id: number,
  acknowledgeStopChanges = false,
): Promise<ProfileDraft> {
  return fetchApi<ProfileDraft>(`/profile-drafts/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ acknowledge_stop_changes: acknowledgeStopChanges }),
  });
}

export async function pushProfileDraft(
  id: number,
  options: { setId?: number; allowStaleBase?: boolean } = {},
): Promise<DraftPushResult> {
  return fetchApi<DraftPushResult>(`/profile-drafts/${id}/push`, {
    method: "POST",
    body: JSON.stringify({
      set_id: options.setId ?? null,
      allow_stale_base: options.allowStaleBase ?? false,
    }),
  });
}

export async function rollbackProfileDraft(id: number): Promise<ProfileDraft> {
  return fetchApi<ProfileDraft>(`/profile-drafts/${id}/rollback`, { method: "POST" });
}

export async function discardProfileDraft(id: number): Promise<ProfileDraft> {
  return fetchApi<ProfileDraft>(`/profile-drafts/${id}/discard`, { method: "POST" });
}

/** Validate a document the editor has not saved. Answers 200 whatever it finds. */
export async function previewProfileDraft(
  baseVersionId: number,
  profile: Record<string, unknown>,
): Promise<DraftPreview> {
  return fetchApi<DraftPreview>("/profile-drafts/preview", {
    method: "POST",
    body: JSON.stringify({ base_version_id: baseVersionId, profile }),
  });
}

/** Every write this box has asked the machine to make, refusals included. */
export async function getDeviceWrites(limit = 100): Promise<DeviceWritesData> {
  return fetchApi<DeviceWritesData>(`/device/writes${queryString({ limit })}`);
}

/**
 * What a cleanup would delete from the machine right now.
 *
 * A dry run: it reads the archive and the last identity frame and writes
 * nothing, which is what makes it safe to fetch on every render of the Device
 * page.
 */
export async function getCleanupPlan(): Promise<CleanupPlan> {
  return fetchApi<CleanupPlan>("/device/cleanup/plan");
}

/**
 * Start a cleanup of the plan a person confirmed. Resolves as soon as it is
 * **queued**, not when it is done.
 *
 * `shotIds` are the planned shots the preview showed. The server compares them
 * with a fresh plan and refuses with a 409 if they differ, so what runs is what
 * was approved. 202 and a background task, like running an analysis: a hundred
 * shots at two deletes a second is most of a minute. The page follows the ledger.
 */
export async function runCleanup(shotIds: number[]): Promise<CleanupRunAccepted> {
  return fetchApi<CleanupRunAccepted>("/device/cleanup/run", {
    method: "POST",
    body: JSON.stringify({ shot_ids: shotIds }),
  });
}

export async function getCleanupRuns(limit = 20): Promise<CleanupRunsData> {
  return fetchApi<CleanupRunsData>(`/device/cleanup/runs${queryString({ limit })}`);
}

/** The judgements the machine's own notes cards do not have yet. */
export async function getPendingNotes(): Promise<PendingNotesData> {
  return fetchApi<PendingNotesData>("/device/notes/pending");
}

/**
 * Send the judgements a person selected to the machine's notes cards. 202, like
 * the cleanup run.
 *
 * `shotIds` are the ticked shots, at least one; there is no "everything
 * pending" form. Every one must still be pending when the request lands, or the
 * server refuses it with a 409 and sends nothing.
 */
export async function pushPendingNotes(shotIds: number[]): Promise<NotesPushAccepted> {
  return fetchApi<NotesPushAccepted>("/device/notes/push", {
    method: "POST",
    body: JSON.stringify({ shot_ids: shotIds }),
  });
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
 * arrived or was too large.
 */
export async function importFiles(
  files: File[],
  options: ImportOptions = {},
): Promise<ImportSummary> {
  const body = new FormData();
  for (const file of files) body.append("files", file, file.name);
  if (options.replace) body.append("replace", "true");
  return fetchApi<ImportSummary>("/import", { method: "POST", body });
}

// ---------------------------------------------------------------------------
// The LLM layer. None of these makes a model call: they configure the
// provider, watch what it is doing, and edit the prompts it renders.
// ---------------------------------------------------------------------------

export async function getLlmStatus(): Promise<LlmStatusData> {
  return fetchApi<LlmStatusData>("/llm/status");
}

/** The cheapest call each provider offers. Never a completion. */
export async function validateLlm(provider?: string): Promise<LlmCredentialCheck> {
  return fetchApi<LlmCredentialCheck>("/llm/validate", {
    method: "POST",
    body: JSON.stringify({ provider: provider ?? null }),
  });
}

export async function getLlmModels(provider?: string): Promise<LlmModelsData> {
  return fetchApi<LlmModelsData>(`/llm/models${queryString({ provider })}`);
}

export async function resetLlmRateLimit(): Promise<LlmRateLimit> {
  return fetchApi<LlmRateLimit>("/llm/rate-limit/reset", { method: "POST" });
}

export async function getLlmCalls(): Promise<LlmCallsData> {
  return fetchApi<LlmCallsData>("/llm/calls");
}

export async function getLlmUsage(since?: string): Promise<LlmUsageTotals> {
  return fetchApi<LlmUsageTotals>(`/llm/usage${queryString({ since })}`);
}

export async function getPrompts(): Promise<PromptListData> {
  return fetchApi<PromptListData>("/prompts");
}

export async function getPrompt(name: string): Promise<PromptData> {
  return fetchApi<PromptData>(`/prompts/${name}`);
}

export async function putPrompt(name: string, content: string): Promise<PromptData> {
  return fetchApi<PromptData>(`/prompts/${name}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export async function resetPrompt(name: string): Promise<PromptData> {
  return fetchApi<PromptData>(`/prompts/${name}/reset`, { method: "POST" });
}

/** Which registry key holds the model for a purpose. Mirrors config.py. */
export const MODEL_KEYS: Record<LlmPurpose, string> = {
  default: "modelDefault",
  analysis: "modelAnalysis",
  draft: "modelDraft",
  chat: "modelChat",
};

// ---------------------------------------------------------------------------
// Sets, beans, grinders and the judgement. The half of a shot the machine
// knows nothing about: what was in the hopper, what you were trying, and
// whether it was any good.
// ---------------------------------------------------------------------------

/**
 * Every closed vocabulary, in one request.
 *
 * Fetched once and cached for the session (`useVocabulary`): nothing here
 * changes without a redeploy, and a UI that typed its own copy of these words
 * would drift from the CHECK constraints the moment one of them did.
 */
export async function getVocabulary(): Promise<Vocabulary> {
  return fetchApi<Vocabulary>("/vocab");
}

/**
 * The flavour-wheel notes the shot panel offers, one list for taste and one
 * for aroma, in wheel order.
 */
export async function getFlavorPicks(): Promise<FlavorPicks> {
  return fetchApi<FlavorPicks>("/flavor-picks");
}

/** Both lists, whole: the server de-duplicates and puts them in wheel order. */
export async function putFlavorPicks(body: FlavorPicks): Promise<FlavorPicks> {
  return fetchApi<FlavorPicks>("/flavor-picks", { method: "PUT", body: JSON.stringify(body) });
}

export async function getBeans(includeArchived = false): Promise<{ items: BeanRow[] }> {
  return fetchApi<{ items: BeanRow[] }>(
    `/beans${queryString({ include_archived: includeArchived })}`,
  );
}

export async function createBean(body: BeanWrite): Promise<BeanRow> {
  return fetchApi<BeanRow>("/beans", { method: "POST", body: JSON.stringify(body) });
}

export async function updateBean(id: number, body: BeanWrite): Promise<BeanRow> {
  return fetchApi<BeanRow>(`/beans/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

/**
 * Delete a bean nobody used. The server answers 409 while any Set points at it,
 * because archiving is how a coffee with history is retired.
 */
export async function deleteBean(id: number): Promise<{ deleted: boolean }> {
  return fetchApi<{ deleted: boolean }>(`/beans/${id}`, { method: "DELETE" });
}

/** Hide a finished bag. Its Sets still point at it, so it stays resolvable. */
export async function setBeanArchived(id: number, archived: boolean): Promise<BeanRow> {
  return fetchApi<BeanRow>(`/beans/${id}/${archived ? "archive" : "unarchive"}`, {
    method: "POST",
  });
}

export async function getGrinders(): Promise<{ items: GrinderRow[] }> {
  return fetchApi<{ items: GrinderRow[] }>("/grinders");
}

export async function createGrinder(body: GrinderWrite): Promise<GrinderRow> {
  return fetchApi<GrinderRow>("/grinders", { method: "POST", body: JSON.stringify(body) });
}

export async function updateGrinder(id: number, body: GrinderWrite): Promise<GrinderRow> {
  return fetchApi<GrinderRow>(`/grinders/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

/** The machine. One row, always there, with how much of the archive came off it. */
export async function getMachine(): Promise<MachineData> {
  return fetchApi<MachineData>("/machine");
}

/** Name and notes only. Everything else is the machine's own account of itself. */
export async function patchMachine(body: MachinePatch): Promise<MachineRow> {
  return fetchApi<MachineRow>("/machine", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function getSets(includeArchived = false): Promise<SetListData> {
  return fetchApi<SetListData>(`/sets${queryString({ include_archived: includeArchived })}`);
}

export async function getSet(id: number): Promise<SetDetailData> {
  return fetchApi<SetDetailData>(`/sets/${id}`);
}

export async function createSet(body: SetCreate): Promise<SetRow> {
  return fetchApi<SetRow>("/sets", { method: "POST", body: JSON.stringify(body) });
}

/**
 * A new version: the current one plus whatever is in `patch`.
 *
 * Only send what changed. Omitting a field inherits the parent's value and
 * sending `null` clears it, so building this body from a whole form would
 * record every field as changed.
 */
export async function addSetVersion(id: number, patch: SetVersionPatch): Promise<SetVersionRow> {
  return fetchApi<SetVersionRow>(`/sets/${id}/versions`, {
    method: "POST",
    body: JSON.stringify(patch),
  });
}

/**
 * What a version is expected to do differently, and against which version.
 *
 * Only accepted while the version has no shots; an empty `prediction` takes one
 * back. The server refuses the rest with a code of its own.
 */
export async function setVersionPrediction(
  id: number,
  versionId: number,
  body: VersionPredictionWrite,
): Promise<SetVersionRow> {
  return fetchApi<SetVersionRow>(`/sets/${id}/versions/${versionId}/prediction`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function setVersionOutcome(
  id: number,
  versionId: number,
  body: VersionOutcomeWrite,
): Promise<SetVersionRow> {
  return fetchApi<SetVersionRow>(`/sets/${id}/versions/${versionId}/outcome`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function clearVersionOutcome(id: number, versionId: number): Promise<SetVersionRow> {
  return fetchApi<SetVersionRow>(`/sets/${id}/versions/${versionId}/outcome`, {
    method: "DELETE",
  });
}

/** Go back to an earlier recipe. Appends a version; writes nothing to the machine. */
export async function rollbackSet(id: number, body: RollbackWrite): Promise<SetVersionRow> {
  return fetchApi<SetVersionRow>(`/sets/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function activateSet(id: number): Promise<SetRow> {
  return fetchApi<SetRow>(`/sets/${id}/activate`, { method: "POST" });
}

export async function archiveSet(id: number): Promise<SetRow> {
  return fetchApi<SetRow>(`/sets/${id}/archive`, { method: "POST" });
}

export async function getSetTrends(id: number): Promise<SetTrends> {
  return fetchApi<SetTrends>(`/sets/${id}/trends`);
}

export async function putJudgement(shotId: number, body: JudgementWrite): Promise<ShotJudgement> {
  return fetchApi<ShotJudgement>(`/shots/${shotId}/judgement`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteJudgement(shotId: number): Promise<{ deleted: boolean }> {
  return fetchApi<{ deleted: boolean }>(`/shots/${shotId}/judgement`, { method: "DELETE" });
}

/** Assign a shot to a Set version, or pass null to detach it. */
export async function putShotSetVersion(
  shotId: number,
  setVersionId: number | null,
): Promise<ShotDetailRow> {
  return fetchApi<ShotDetailRow>(`/shots/${shotId}/set-version`, {
    method: "PUT",
    body: JSON.stringify({ set_version_id: setVersionId }),
  });
}

// ---------------------------------------------------------------------------
// The analyzer.
//
// `runAnalysis` is a long request on purpose: the call behind it takes thirty
// seconds to two minutes, and the LLM stream reports `analysis.started` and
// `analysis.finished` to anyone watching in the meantime. It resolves with the
// stored row whatever happened — a provider failure is a `failed` row and a
// 201, not an exception — so a caller renders the outcome rather than a toast.
//
// There is no wrapper for `GET /shots/{id}/analyses`: the shot detail already
// carries them, and a second request for a list that is usually empty or one
// row long would be a round trip for nothing. The route exists for API users.
// ---------------------------------------------------------------------------

export async function runAnalysis(
  shotId: number,
  options: { model?: string; force?: boolean } = {},
): Promise<Analysis> {
  return fetchApi<Analysis>(`/shots/${shotId}/analyses`, {
    method: "POST",
    body: JSON.stringify({ model: options.model ?? "", force: options.force ?? false }),
  });
}

export async function getAnalysis(id: number): Promise<Analysis> {
  return fetchApi<Analysis>(`/analyses/${id}`);
}

export async function analyseSet(
  setId: number,
  options: { onlyUnanalysed?: boolean; model?: string } = {},
): Promise<BatchResult> {
  return fetchApi<BatchResult>(`/sets/${setId}/analyse`, {
    method: "POST",
    body: JSON.stringify({
      only_unanalysed: options.onlyUnanalysed ?? true,
      model: options.model ?? "",
    }),
  });
}

export async function getSetSuggestions(setId: number): Promise<SuggestionListData> {
  return fetchApi<SuggestionListData>(`/sets/${setId}/suggestions`);
}

export async function acceptSuggestion(id: number): Promise<AcceptedSuggestion> {
  return fetchApi<AcceptedSuggestion>(`/suggestions/${id}/accept`, { method: "POST" });
}

export async function rejectSuggestion(id: number): Promise<Suggestion> {
  return fetchApi<Suggestion>(`/suggestions/${id}/reject`, { method: "POST" });
}

export async function getKnowledgeRules(
  params: { category?: string; enabled?: boolean } = {},
): Promise<KnowledgeRuleListData> {
  return fetchApi<KnowledgeRuleListData>(`/knowledge/rules${queryString(params)}`);
}

export async function patchKnowledgeRule(
  id: number,
  body: KnowledgeRulePatch,
): Promise<KnowledgeRule> {
  return fetchApi<KnowledgeRule>(`/knowledge/rules/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function reloadKnowledgeRules(): Promise<{ changed: number }> {
  return fetchApi<{ changed: number }>("/knowledge/rules/reload", { method: "POST" });
}

// Tier 2: the documents, their chunks and the search over them. The list is
// deliberately body-less server-side — 200 KB of markdown between the
// twenty-five of them — so the doc view fetches the one it is showing.

export async function getKnowledgeDocs(): Promise<KnowledgeDocListData> {
  return fetchApi<KnowledgeDocListData>("/knowledge/docs");
}

export async function getKnowledgeDoc(slug: string): Promise<KnowledgeDocDetail> {
  return fetchApi<KnowledgeDocDetail>(`/knowledge/docs/${encodeURIComponent(slug)}`);
}

export async function putKnowledgeDoc(slug: string, markdown: string): Promise<KnowledgeDocDetail> {
  return fetchApi<KnowledgeDocDetail>(`/knowledge/docs/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify({ markdown }),
  });
}

export async function resetKnowledgeDoc(slug: string): Promise<KnowledgeDocDetail> {
  return fetchApi<KnowledgeDocDetail>(`/knowledge/docs/${encodeURIComponent(slug)}/reset`, {
    method: "POST",
  });
}

export async function searchKnowledge(q: string, k = 8): Promise<KnowledgeSearchData> {
  return fetchApi<KnowledgeSearchData>(`/knowledge/search${queryString({ q, k })}`);
}

// Tier 3: the learned insights. The only part of the knowledge base with a real
// DELETE — a rule or a document is shipped in a file and comes back on the next
// boot, an insight is this box's alone.

export async function getKnowledgeInsights(
  params: { confirmed?: boolean; analysis_id?: number; set_id?: number } = {},
): Promise<KnowledgeInsightListData> {
  return fetchApi<KnowledgeInsightListData>(`/knowledge/insights${queryString(params)}`);
}

export async function createKnowledgeInsight(
  body: KnowledgeInsightCreate,
): Promise<KnowledgeInsight> {
  return fetchApi<KnowledgeInsight>("/knowledge/insights", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function patchKnowledgeInsight(
  id: number,
  body: KnowledgeInsightPatch,
): Promise<KnowledgeInsight> {
  return fetchApi<KnowledgeInsight>(`/knowledge/insights/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteKnowledgeInsight(id: number): Promise<{ deleted: boolean }> {
  return fetchApi<{ deleted: boolean }>(`/knowledge/insights/${id}`, { method: "DELETE" });
}

// The chat. Sending is the only one of these that is not a plain
// read: it answers 202 with the run to follow, and the answer itself arrives on
// `/api/chat/runs/{id}/stream`.

export async function getChatThreads(): Promise<ChatThread[]> {
  return fetchApi<ChatThread[]>("/chat/threads");
}

export async function createChatThread(body: ChatThreadWrite): Promise<ChatThread> {
  return fetchApi<ChatThread>("/chat/threads", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * The conversation about one version of a Set, started if there is none.
 *
 * What Discuss presses: a second press lands in the same room rather than
 * leaving a trail of empty conversations about one change. Omitting the version
 * means the Set's current one.
 */
export async function openChatThread(
  setId: number,
  setVersionId?: number | null,
): Promise<ChatThread> {
  return fetchApi<ChatThread>("/chat/threads/open", {
    method: "POST",
    body: JSON.stringify({ set_id: setId, set_version_id: setVersionId ?? null }),
  });
}

export async function getChatThread(id: number): Promise<ChatThreadDetail> {
  return fetchApi<ChatThreadDetail>(`/chat/threads/${id}`);
}

export async function renameChatThread(id: number, title: string): Promise<ChatThread> {
  return fetchApi<ChatThread>(`/chat/threads/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteChatThread(id: number): Promise<{ deleted: boolean }> {
  return fetchApi<{ deleted: boolean }>(`/chat/threads/${id}`, { method: "DELETE" });
}

export async function sendChatMessage(id: number, message: string): Promise<ChatSendResult> {
  return fetchApi<ChatSendResult>(`/chat/threads/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export async function cancelChatRun(id: number): Promise<ChatRun> {
  return fetchApi<ChatRun>(`/chat/runs/${id}/cancel`, { method: "POST" });
}

/**
 * What the agent can do in a conversation of this kind.
 *
 * The kind is a parameter because the two surfaces differ: a Set's chat cannot
 * query the archive and a general one cannot change a Set. The page never
 * filters this itself — the scope is the server's, and a second copy of the
 * rule in the browser is a copy that can disagree.
 */
export async function getChatTools(kind: "general" | "set" = "general"): Promise<ChatToolList> {
  return fetchApi<ChatToolList>(`/chat/tools${queryString({ kind })}`);
}

/**
 * Where the browser follows one run.
 *
 * `after` is the last sequence number the tab has seen, so a reconnect replays
 * only what it missed. Not a `fetchApi` wrapper: `lib/sse.ts` reads it with
 * `fetch` and its own reader, because `EventSource` cannot send a bearer token.
 */
export function chatRunStreamUrl(runId: number, after = 0): string {
  return `${API_BASE}/chat/runs/${runId}/stream${queryString({ after })}`;
}

// ── the starting-point wizard ───────────────────────────────────────

/**
 * What this archive already knows about beans like this one.
 *
 * Free, and read before anybody presses the button that spends money — so the
 * wizard's first step has something on it either way. `grinderId` is a filter
 * rather than a hint: a grind number from another grinder is not weaker
 * evidence, it is meaningless.
 */
export async function getSimilarSets(
  beanId: number,
  params: { grinderId?: number | null } = {},
): Promise<SimilarSetsData> {
  return fetchApi<SimilarSetsData>(
    `/beans/${beanId}/similar-sets${queryString({
      grinder_id: params.grinderId ?? undefined,
    })}`,
  );
}

/**
 * Ask for three starting points. Answers 202 with a `running` row.
 *
 * "Resolved" means *queued*, exactly as it does for an analysis: the provider
 * call runs in the background and the row is the handle. `wait` blocks until it
 * is done and exists for tests.
 */
export async function createStartingPoint(
  body: StartingPointRequest,
  options: { wait?: boolean } = {},
): Promise<StartingPointRun> {
  return fetchApi<StartingPointRun>(`/starting-points${queryString({ wait: options.wait })}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getStartingPoint(runId: number): Promise<StartingPointRun> {
  return fetchApi<StartingPointRun>(`/starting-points/${runId}`);
}

/** Take one option: the Set, its first version and — if it carried one — a draft. */
export async function acceptStartingPoint(
  runId: number,
  option: "conservative" | "recommended" | "adventurous",
): Promise<StartingPointAccepted> {
  return fetchApi<StartingPointAccepted>(`/starting-points/${runId}/accept`, {
    method: "POST",
    body: JSON.stringify({ option }),
  });
}
