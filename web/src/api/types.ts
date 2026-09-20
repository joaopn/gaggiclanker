import type { components } from "@/api/schema";

/**
 * Hand-written narrowings on top of the generated schema.
 *
 * `GET /api/settings` is declared server-side as `dict[str, Any]` — the
 * registry produces the shape, not a pydantic model, so OpenAPI can only say
 * "an object". These types mirror `ResolvedSetting.to_api()` in
 * `gaggiclanker/settings.py`; when that method changes, change these. Anything
 * the schema DOES describe is imported from it rather than retyped.
 */

export type HealthData = components["schemas"]["HealthData"];
export type AuthStatusData = components["schemas"]["AuthStatusData"];
export type LoginData = components["schemas"]["LoginData"];
export type PasswordData = components["schemas"]["PasswordData"];
export type BackupData = components["schemas"]["BackupData"];
export type DeviceStatusData = components["schemas"]["DeviceStatusData"];
export type ShotListData = components["schemas"]["ShotListData"];
export type ShotListRow = components["schemas"]["ShotListRow"];
export type ShotDetailData = components["schemas"]["ShotDetailData"];
export type DeviceShotNotes = components["schemas"]["DeviceShotNotesRow"];
export type ShotSamplesData = components["schemas"]["ShotSamplesData"];
export type ShotSampleRow = components["schemas"]["ShotSampleRow"];
export type ShotDetailRow = components["schemas"]["ShotDetailRow"];
export type ProfileListData = components["schemas"]["ProfileListData"];
export type ProfileVersionListData = components["schemas"]["ProfileVersionListData"];
export type ProfileVersionSummary = components["schemas"]["ProfileVersionSummary"];
export type ProfileVersionRow = components["schemas"]["ProfileVersionRow"];
export type DeviceProfileSummary = components["schemas"]["DeviceProfileSummary"];
export type SyncStatusData = components["schemas"]["SyncStatusData"];
export type SyncRunRow = components["schemas"]["SyncRunRow"];
export type ImportSummary = components["schemas"]["ImportSummary"];
export type ImportResult = components["schemas"]["ImportResult"];
export type ApiErrorBody = components["schemas"]["ApiError"];
export type MachineData = components["schemas"]["MachineData"];
export type MachineRow = components["schemas"]["MachineRow"];
export type MachinePatch = components["schemas"]["MachinePatch"];

// Sets, beans, grinders and the judgement. Every one of these is a real
// pydantic model on the server, so none of them is retyped here.
export type Vocabulary = components["schemas"]["Vocabulary"];
export type FlavorNode = components["schemas"]["FlavorNode"];
export type FlavorPicks = components["schemas"]["FlavorPicks"];
export type VocabTerm = components["schemas"]["Term"];
export type MeasureTerm = components["schemas"]["MeasureTerm"];
export type BeanRow = components["schemas"]["BeanRow"];
export type BeanWrite = components["schemas"]["BeanWrite"];
export type GrinderRow = components["schemas"]["GrinderRow"];
export type GrinderWrite = components["schemas"]["GrinderWrite"];
export type SetRow = components["schemas"]["SetRow"];
export type SetCreate = components["schemas"]["SetCreate"];
export type SetListData = components["schemas"]["SetListData"];
export type SetDetailData = components["schemas"]["SetDetailData"];
export type SetVersionRow = components["schemas"]["SetVersionRow"];
export type SetVersionDetail = components["schemas"]["SetVersionDetail"];
export type MeasureSpread = components["schemas"]["MeasureSpread"];
export type SpreadMeasure = MeasureSpread["measure"];
export type VersionEvidence = components["schemas"]["VersionEvidence"];
export type MeasureEvidence = components["schemas"]["MeasureEvidence"];
export type EvidenceCounts = components["schemas"]["EvidenceCounts"];
export type SetVersionPatch = components["schemas"]["SetVersionPatch"];
export type VersionPredictionWrite = components["schemas"]["VersionPredictionWrite"];
export type VersionOutcomeWrite = components["schemas"]["VersionOutcomeWrite"];
export type RollbackWrite = components["schemas"]["RollbackWrite"];
export type VersionOutcome = NonNullable<SetVersionRow["outcome"]>;
export type SetVersionWrite = components["schemas"]["SetVersionWrite"];
export type FieldChange = components["schemas"]["FieldChange"];
export type SetTrends = components["schemas"]["SetTrends"];
export type SetTrendPoint = components["schemas"]["SetTrendPoint"];
export type SetTrendVersion = components["schemas"]["SetTrendVersion"];
export type ShotJudgement = components["schemas"]["ShotJudgementRow"];
export type JudgementWrite = components["schemas"]["JudgementWrite"];
export type ShotSetBadge = components["schemas"]["ShotSetBadge"];
export type SettingValue = components["schemas"]["SettingValue"];

// The starting-point wizard. The run row, the request body and the
// similar-Set cards are all real pydantic models, so none of them is retyped
// here; only `StartingPointOutput` below is, for the reason `AnalysisOutput`
// is — the server declares it as a JSON column.
export type StartingPointRun = components["schemas"]["StartingPointRunRow"];
export type StartingPointRequest = components["schemas"]["StartingPointRequest"];
export type StartingPointAccepted = components["schemas"]["StartingPointAccepted"];
export type SimilarSet = components["schemas"]["SimilarSet"];
export type SimilarSetsData = components["schemas"]["SimilarSetsData"];

// The analyzer and its knowledge tier. All real pydantic models on the
// server, so none of them is retyped here either.
export type Analysis = components["schemas"]["AnalysisRow"];
export type AnalysisListData = components["schemas"]["AnalysisListData"];
export type AnalysisRequest = components["schemas"]["AnalysisRequest"];
export type Suggestion = components["schemas"]["SuggestionRow"];
export type SuggestionListData = components["schemas"]["SuggestionListData"];
export type AcceptedSuggestion = components["schemas"]["AcceptedData"];
export type KnowledgeRule = components["schemas"]["RuleRow"];
export type KnowledgeRuleListData = components["schemas"]["RuleListData"];
export type KnowledgeRulePatch = components["schemas"]["RulePatch"];

// Tiers 2 and 3: the prose documents with their chunks, and the
// learned insights. Real pydantic models server-side, so none of them is
// retyped here.
export type KnowledgeDoc = components["schemas"]["DocRow"];
export type KnowledgeDocListData = components["schemas"]["DocListData"];
export type KnowledgeDocDetail = components["schemas"]["DocDetailData"];
export type KnowledgeChunk = components["schemas"]["ChunkRow"];
export type KnowledgeChunkHit = components["schemas"]["ChunkHit"];
export type KnowledgeSearchData = components["schemas"]["SearchData"];
export type KnowledgeInsight = components["schemas"]["InsightRow"];
export type KnowledgeInsightListData = components["schemas"]["InsightListData"];
export type KnowledgeInsightCreate = components["schemas"]["InsightCreate"];
export type KnowledgeInsightPatch = components["schemas"]["InsightPatch"];
export type KnowledgeInsightScope = components["schemas"]["InsightScope"];
export type SetAnalyseRequest = components["schemas"]["SetAnalyseRequest"];
export type BatchResult = components["schemas"]["BatchResult"];

/**
 * The analysis document the model returns, as stored on `Analysis.output`.
 *
 * Declared server-side as decoded JSON of whatever `AnalysisResult` produced,
 * so OpenAPI can only say "an object". This mirrors
 * `gaggiclanker/analyzer/models.py`; when that changes, change this. Everything
 * below the top level is optional because a row written by an older build, or
 * one whose provider answered a slightly different shape, still has to render.
 */
/**
 * The three options a starting-point run answers with, decoded.
 *
 * Hand-written for the reason `AnalysisOutput` is: `output` is a JSON column on
 * `starting_point_runs`, so the server declares it as `dict[str, Any]` and the
 * generated type is `unknown`. The authority is
 * `gaggiclanker/starting/models.py::StartingPointResult`; when that changes,
 * change this. Everything below the top level is optional, because a row
 * written by an older build still has to render.
 */
export type StartingPointOption = {
  option: "conservative" | "recommended" | "adventurous";
  headline?: string;
  grind_setting?: string;
  /**
   * Whether `grind_setting` is a number on this grinder's own scale.
   *
   * The load-bearing field on the card. False means the setting is words —
   * "two steps finer than your usual" — because nothing anchored a number, and
   * the card has to say so rather than letting somebody read it as a dial
   * position.
   */
  grind_is_absolute?: boolean;
  grind_note?: string;
  dose_g?: number;
  yield_g?: number;
  ratio?: number;
  temperature_c?: number;
  profile_version_id?: number | null;
  /** A whole profile document, when the option authored one. */
  profile?: Record<string, unknown> | null;
  profile_note?: string;
  rationale?: string;
  rules_used?: string[];
  excerpts_used?: string[];
  similar_set_version_ids?: number[];
};

export type StartingPointOutput = {
  summary?: string;
  questions_for_user?: string[];
  options?: StartingPointOption[];
};

export type AnalysisOutput = {
  shot_style?: string;
  execution?: {
    summary?: string;
    issues?: Array<{ signal?: string; severity?: string; evidence?: string }>;
  };
  taste_prediction?: { balance?: string; body?: string; confidence?: string };
  diagnosis?: string;
  suggestions?: Array<{
    variable?: string;
    direction?: string;
    magnitude?: number | null;
    unit?: string;
    reason?: string;
    confidence?: string;
    priority?: number;
  }>;
  profile_patch?: Array<{
    phase_index?: number;
    field?: string;
    from?: string;
    to?: string;
    reason?: string;
  }>;
  questions_for_user?: string[];
  rules_used?: string[];
  /**
   * The heading paths of the reference excerpts the model leaned on. Checked
   * server-side against the excerpts this shot was actually given, so a path
   * here always resolves to a passage in the Docs tab.
   */
  excerpts_used?: string[];
  /**
   * At most two. Already stored as unconfirmed rows linked to the analysis, so
   * the panel renders the *rows* rather than this copy — this is what the model
   * said, and the rows are what the user acts on.
   */
  proposed_insights?: Array<{
    scope?: KnowledgeInsightScope;
    text?: string;
    evidence_shot_ids?: number[];
  }>;
};

/** Where the newest analysis of a shot got to. Four states, not five. */
export type AnalysisState = "none" | "running" | "ok" | "failed";

// The LLM layer. These the schema DOES describe, so they are imported
// rather than retyped; only the live-call record below is hand-written,
// because the observer's ring is a plain dict on the server side.
export type LlmStatusData = components["schemas"]["LlmStatusData"];
export type LlmCredentialCheck = components["schemas"]["CredentialCheckData"];
export type LlmModelsData = components["schemas"]["ModelsData"];
export type LlmRateLimit = components["schemas"]["RateLimitData"];
export type LlmUsageTotals = components["schemas"]["UsageTotals"];
export type PromptSummary = components["schemas"]["PromptSummary"];
export type PromptListData = components["schemas"]["PromptListData"];
export type PromptData = components["schemas"]["PromptData"];

/** What a purpose is called on both sides. `default` is the fallback. */
export type LlmPurpose = "default" | "analysis" | "draft" | "chat";

/**
 * One entry in the live-call ring (`gaggiclanker/llm/observer.py`). Declared
 * server-side as `dict[str, Any]` - the observer is a dataclass, not a
 * response model - so OpenAPI can only say "an object".
 */
export type LlmCall = {
  id: string;
  label: string;
  subject: string;
  provider: string;
  model: string;
  purpose: string;
  status: "running" | "succeeded" | "failed";
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  mode: string | null;
  error: string | null;
};

export type LlmCallsData = { calls: LlmCall[]; running: number };

/** The two events `/api/llm/calls/stream` carries. */
export type LlmCallEvent = { call: LlmCall; running: number };
export type LlmSnapshotEvent = LlmCallsData;

export type SettingType = "string" | "int" | "float" | "bool";
export type SettingSource = "database" | "default";

/** A non-secret setting: value, default and override are all disclosed. */
export type PlainSetting = {
  key: string;
  type: SettingType;
  secret: false;
  /** A dedicated endpoint owns this key; `PATCH /api/settings` refuses it. */
  readonly: boolean;
  value: SettingValue;
  default: SettingValue;
  override: SettingValue;
  source: SettingSource;
  description: string;
};

/** A secret: never its value, only whether one is set and its four-char hint. */
export type SecretSetting = {
  key: string;
  type: SettingType;
  secret: true;
  /** A dedicated endpoint owns this key; `PATCH /api/settings` refuses it. */
  readonly: boolean;
  configured: boolean;
  hint: string | null;
  source: SettingSource;
  description: string;
};

export type ResolvedSetting = PlainSetting | SecretSetting;

export type SettingsMap = Record<string, ResolvedSetting>;

export function isSecretSetting(setting: ResolvedSetting): setting is SecretSetting {
  return setting.secret;
}

/**
 * The filters `GET /api/shots` accepts. `cursor` and `offset` are alternatives
 * and the server answers 400 if both are sent, so a caller picks one.
 */
export type ShotSort = "started_at" | "execution_score" | "duration" | "rating";

export type ShotListParams = {
  limit?: number;
  offset?: number;
  cursor?: string;
  from?: string;
  to?: string;
  profile_version_id?: number;
  quarantined?: boolean;
  include_deleted?: boolean;
  set_id?: number;
  set_version_id?: number;
  /** The inbox: shots the archive could not attach to a Set on its own. */
  needs_set?: boolean;
  source?: "device" | "import";
  min_score?: number;
  max_score?: number;
  min_rating?: number;
  sort?: ShotSort;
  order?: "asc" | "desc";
};

/** `GET /api/profile-versions`. Offset paging; versions are inserted rarely. */
export type ProfileVersionParams = {
  limit?: number;
  offset?: number;
  source?: "device" | "import";
};

/**
 * The derived blobs on a shot detail row.
 *
 * `phases` and `diagnostics` are declared server-side as decoded JSON of
 * whatever `gaggiclanker/domain/diagnostics.py` produced, so OpenAPI can only
 * say "anything". These mirror the TypedDicts in that module at
 * `detail_level: "per_phase"`; when those change, change these. Everything is
 * optional because a shot stored before a diagnostics fix — or one whose
 * diagnostics pass failed, which does not quarantine it — has none of it.
 */
export type BandAnnotations = Record<string, string>;

export type ShotPhase = {
  name: string;
  phase_number: number;
  start_time_seconds: number;
  duration_seconds: number;
  sample_count: number;
  avg_temperature_c: number;
  avg_pressure_bar: number;
  total_flow_ml: number;
  diagnostics?: {
    phase_type?: string;
    avg_pressure_bar?: number;
    avg_flow_ml_s?: number;
    pressure_rmse_bar?: number;
    flow_rmse_ml_s?: number;
    ramp_rate_bar_s?: number;
    saturation_time_s?: number;
    resistance_avg?: number;
    resistance_slope?: number;
    channeling_risk?: string;
    flow_jitter_ml_s?: number;
    pressure_jitter_bar?: number;
    taper_rate_bar_s?: number;
    taper_smoothness?: number;
    annotations?: BandAnnotations;
  };
};

export type ShotDiagnosticsBlob = {
  summary?: {
    temperature?: { min_c: number; max_c: number; avg_c: number; target_avg_c: number };
    pressure?: {
      min_bar: number;
      max_bar: number;
      avg_bar: number;
      peak_time_s: number;
    } | null;
    flow?: {
      total_volume_ml: number;
      avg_flow_ml_s: number;
      peak_flow_ml_s: number;
      time_to_first_drip_s: number | null;
    };
    extraction?: {
      preinfusion_time_s: number;
      main_extraction_time_s: number;
      total_time_s: number;
    };
  };
  diagnostics?: {
    has_pressure?: boolean;
    resistance?: {
      avg: number;
      std: number;
      slope: number;
      peak: number;
      peak_timing_pct: number;
      annotations: BandAnnotations;
    } | null;
    channeling?: {
      flow_jitter_ml_s: number;
      flow_vs_target_residual_ml_s: number | null;
      pressure_max_drop_rate_bar_s: number;
      flow_acceleration_late_ml_s2: number;
      flow_spread_ml_s: number;
      pressure_jitter_bar: number;
      channeling_risk: string;
      annotations: BandAnnotations;
    } | null;
    temperature?: {
      overshoot_c: number;
      undershoot_c: number;
      stability_std_c: number;
      annotations: BandAnnotations;
    };
    extraction?: {
      pressure_auc_bar_s: number;
      pressure_slope_brew_bar_s: number;
      flow_slope_brew_ml_s2: number;
      flow_avg_brew_ml_s: number;
      annotations: BandAnnotations;
    };
    weight?: {
      rate_avg_g_s: number | null;
      rate_std_g_s: number | null;
      scale_connected: boolean;
      annotations: BandAnnotations;
    };
    profile_compliance?: {
      pressure_rmse_bar: number;
      flow_rmse_ml_s: number | null;
      max_pressure_overshoot_bar: number;
      max_pressure_undershoot_bar: number;
      max_flow_overshoot_ml_s: number | null;
      max_flow_undershoot_ml_s: number | null;
      annotations: BandAnnotations;
    } | null;
  } | null;
  detail_level?: string;
  has_pressure?: boolean;
  /** Added with the shots UI. Absent on shots derived before it; the columns still carry
      the score and its one-line reason. */
  score?: {
    score: number;
    confidence: string;
    reason: string;
    components: Record<string, number>;
  };
};

/** The form fields `POST /api/import` accepts beside the files themselves. */
export type ImportOptions = {
  /** Overwrite shots already in the archive instead of skipping them. */
  replace?: boolean;
};

/** A PATCH body: registry key -> value, with null meaning "drop the override". */
export type SettingsPatch = Record<string, SettingValue>;

/**
 * The device identity, `res:ota-settings`. Declared server-side as a plain
 * object (it is whatever the firmware sent, carried through), so OpenAPI can
 * only say "an object" and the fields we read are named here.
 */
export type DeviceIdentity = {
  hardware?: string | null;
  displayVersion?: string | null;
  controllerVersion?: string | null;
  latestVersion?: string | null;
  channel?: string | null;
  updating?: boolean | null;
};

// Profile drafts and the device-write audit. The rows are real
// pydantic models, so they come from the schema; the two JSON columns on a
// draft do not, because the server declares them as decoded JSON and OpenAPI
// can only say "a list". Their element types are the same models the preview
// endpoint returns, which is how the two halves stay one definition.
export type ProfileDraft = components["schemas"]["ProfileDraftRow"];
export type ProfileDraftDetail = components["schemas"]["ProfileDraftDetail"];
export type ProfileDraftListData = components["schemas"]["DraftListData"];
export type DraftPreview = components["schemas"]["DraftPreview"];
export type DraftPushResult = components["schemas"]["PushedData"];
export type PolicyChange = components["schemas"]["PolicyChange"];
export type StopConditionChange = components["schemas"]["StopConditionChange"];
export type PolicyViolation = components["schemas"]["Violation"];
export type DeviceWrite = components["schemas"]["DeviceWriteRow"];
export type DeviceWritesData = components["schemas"]["DeviceWritesData"];

/** Storage cleanup: the dry run, the ledger of past runs, and one row of each. */
export type CleanupPlan = components["schemas"]["CleanupPlan"];
export type CleanupPolicy = components["schemas"]["CleanupPolicy"];
export type PlannedShot = components["schemas"]["PlannedShot"];
export type SkippedShot = components["schemas"]["SkippedShot"];
export type CleanupRun = components["schemas"]["CleanupRunRow"];
export type CleanupRunsData = components["schemas"]["CleanupRunsData"];
export type CleanupRunAccepted = components["schemas"]["CleanupRunAccepted"];

/** Notes write-back: what the machine's notes cards are missing, and the send. */
export type PendingNotesData = components["schemas"]["PendingNotesData"];
export type NotesPushAccepted = components["schemas"]["NotesPushAccepted"];

/** Every state a draft can be in, as the `status` CHECK spells them. */
export type DraftStatus = "draft" | "approved" | "pushed" | "failed" | "discarded" | "superseded";

/** `POST /api/profile-drafts`, in the two shapes the route accepts. */
export type DraftCreateBody = {
  base_version_id: number;
  /** A complete profile document: the manual editor's path, no model involved. */
  profile?: Record<string, unknown>;
  analysis_id?: number;
  suggestion_id?: number;
  notes?: string;
  change_summary?: string;
  model?: string;
};

/**
 * A draft's two JSON columns, decoded.
 *
 * `gaggiclanker/db/repos/profile_drafts.py` owns the shape; when that changes,
 * change this. They are cast rather than generated because the columns are
 * `JsonList` on the server, which OpenAPI renders as `unknown[]`.
 */
export function clampChangesOf(draft: ProfileDraft): PolicyChange[] {
  return (draft.clamp_changes ?? []) as PolicyChange[];
}

export function stopConditionChangesOf(draft: ProfileDraft): StopConditionChange[] {
  return (draft.stop_condition_changes ?? []) as StopConditionChange[];
}

/**
 * The two documents a failed push recorded: what we sent, and what the machine
 * served back. `null` on a draft that has not been pushed, and on a push that
 * verified the `loaded` half is simply confirmation.
 */
export type PushVerification = {
  sent?: Record<string, unknown> | null;
  loaded?: Record<string, unknown> | null;
  sent_canonical?: unknown;
  loaded_canonical?: unknown;
};

// The chat and its tool surface. Every one of these is a pydantic
// model on the server, so nothing here is retyped.
export type ChatThread = components["schemas"]["ChatThreadRow"];
export type ChatThreadWrite = components["schemas"]["ChatThreadWrite"];
export type ChatMessage = components["schemas"]["ChatMessageRow"];
export type ChatRun = components["schemas"]["ChatRunRow"];
export type ChatThreadDetail = components["schemas"]["ThreadDetail"];
export type ChatSendResult = components["schemas"]["SendResult"];
export type ChatToolInfo = components["schemas"]["ToolInfo"];
export type ChatToolList = components["schemas"]["ToolList"];
