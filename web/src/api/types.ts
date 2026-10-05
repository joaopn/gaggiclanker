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
export type ShotListRow = components["schemas"]["ShotListItem"];
export type ShotWarning = components["schemas"]["ShotWarningRow"];
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
export type ShotInformation = components["schemas"]["ShotInformation"];
export type ShotInfoGroup = components["schemas"]["ShotInfoGroup"];
export type ShotInfoItem = components["schemas"]["ShotInfoItem"];
export type ShotInfoTier = ShotInfoItem["tier"];
export type VocabTerm = components["schemas"]["Term"];
export type MeasureTerm = components["schemas"]["MeasureTerm"];
export type BeanRow = components["schemas"]["BeanRow"];
export type BeanWrite = components["schemas"]["BeanWrite"];
export type GrinderRow = components["schemas"]["GrinderRow"];
export type GrinderWrite = components["schemas"]["GrinderWrite"];
export type SetRow = components["schemas"]["SetRow"];
export type SetCreate = components["schemas"]["SetCreate"];
export type SetDesignCreate = components["schemas"]["SetDesignCreate"];
export type SetDesignCreated = components["schemas"]["SetDesignCreated"];
export type SetDesignDiscarded = components["schemas"]["SetDesignDiscarded"];
export type SetListData = components["schemas"]["SetListData"];
export type SetDetailData = components["schemas"]["SetDetailData"];
export type SetRevertRow = components["schemas"]["SetRevertRow"];
export type SetVersionRow = components["schemas"]["SetVersionRow"];
export type ProfileMatchSummary = components["schemas"]["ProfileMatchSummary"];
export type SetVersionDetail = components["schemas"]["SetVersionDetail"];
export type SetProposal = components["schemas"]["SetProposalDetail"];
export type SetProposalListData = components["schemas"]["SetProposalListData"];
export type SetProposalDecision = components["schemas"]["SetProposalDecision"];
export type ProposalDecline = components["schemas"]["ProposalDecline"];
export type OutcomeProposal = components["schemas"]["OutcomeProposalRow"];
export type OutcomeProposalListData = components["schemas"]["OutcomeProposalListData"];
export type OutcomeProposalDecision = components["schemas"]["OutcomeProposalDecision"];
export type InsightDeletion = components["schemas"]["InsightDeletionRow"];
export type InsightDeletionListData = components["schemas"]["InsightDeletionListData"];
export type InsightDeletionDecision = components["schemas"]["InsightDeletionDecision"];
export type PatternsData = components["schemas"]["PatternsData"];
export type PatternRun = components["schemas"]["PatternRunRow"];
export type PatternProposal = components["schemas"]["PatternProposalRow"];
export type PatternSource = components["schemas"]["PatternSource"];
export type PatternSkipped = components["schemas"]["PatternSkipped"];
export type PatternProposalDecision = components["schemas"]["PatternProposalDecision"];
export type MeasureSpread = components["schemas"]["MeasureSpread"];
export type SpreadMeasure = MeasureSpread["measure"];
export type VersionEvidence = components["schemas"]["VersionEvidence"];
export type MeasureEvidence = components["schemas"]["MeasureEvidence"];
export type EvidenceCounts = components["schemas"]["EvidenceCounts"];
export type SetVersionAdd = components["schemas"]["SetVersionAdd"];
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
// here; only `StartingPointOutput` below is, because the server declares it
// as a JSON column.
export type StartingPointRun = components["schemas"]["StartingPointRunRow"];
export type StartingPointRequest = components["schemas"]["StartingPointRequest"];
export type StartingPointAccepted = components["schemas"]["StartingPointAccepted"];
export type SimilarSet = components["schemas"]["SimilarSet"];
export type SimilarSetsData = components["schemas"]["SimilarSetsData"];

// A shot's review and the knowledge tier. All real pydantic models on the
// server, so none of them is retyped here either.
export type ShotReview = components["schemas"]["ShotReviewRow"];
export type ShotReviewDetail = components["schemas"]["ShotReviewDetail"];
export type ReviewListData = components["schemas"]["ReviewListData"];
export type ReviewRequest = components["schemas"]["ReviewRequest"];
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
export type InsightRestsOn = components["schemas"]["RestsOnRow"];
export type KnowledgeInsightListData = components["schemas"]["InsightListData"];
export type KnowledgeInsightCreate = components["schemas"]["InsightCreate"];
export type KnowledgeInsightPatch = components["schemas"]["InsightPatch"];
export type KnowledgeInsightScope = components["schemas"]["InsightScope"];

/**
 * The three options a starting-point run answers with, decoded.
 *
 * Hand-written because `output` is a JSON column on
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

// The LLM layer. These the schema DOES describe, so they are imported
// rather than retyped; only the live-call record below is hand-written,
// because the observer's ring is a plain dict on the server side.
export type LlmStatusData = components["schemas"]["LlmStatusData"];
export type ClaudeCliStatus = components["schemas"]["ClaudeCliStatusData"];
export type LlmCredentialCheck = components["schemas"]["CredentialCheckData"];
export type LlmModelsData = components["schemas"]["ModelsData"];
export type LlmRateLimit = components["schemas"]["RateLimitData"];
export type LlmUsageTotals = components["schemas"]["UsageTotals"];
export type PromptSummary = components["schemas"]["PromptSummary"];
export type PromptListData = components["schemas"]["PromptListData"];
export type PromptData = components["schemas"]["PromptData"];

/** What a purpose is called on both sides. `default` is the fallback. */
export type LlmPurpose = "default" | "review" | "draft" | "chat";

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
  /** The cached part of `prompt_tokens`; null when the provider did not say. */
  cache_read_tokens: number | null;
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
export type ShotSort = "started_at" | "duration" | "rating" | "review";

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

/** The machine's own puck resistance (squared), or ours computed as pressure / flow². */
export type ResistanceSource = "machine" | "computed";

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
    /** Only in a phase that steers by pressure (or flow) and has samples to grade. */
    pressure_rmse_bar?: number;
    flow_rmse_ml_s?: number;
    resistance_avg?: number;
    resistance_slope?: number;
    resistance_source?: ResistanceSource;
  };
  /** What the phase did, in plain numbers (`domain/phase_metrics.py`); absent on a shot derived
      before they existed, and on a log with no phase table. A key is absent when the shot
      cannot have the value (no scale, no pressure sensor). */
  metrics?: {
    /** The firmware's exit-reason code; 0 is "Unknown". */
    ended_by?: number;
    cup_weight_end_g?: number;
    cup_weight_gained_g?: number;
    scale_flow_mean_g_s?: number;
    scale_flow_peak_g_s?: number;
    puck_flow_mean_ml_s?: number;
    puck_flow_peak_ml_s?: number;
    water_pumped_ml?: number;
    pressure_peak_bar?: number;
    pressure_end_bar?: number;
    temperature_min_c?: number;
    temperature_target_c?: number;
    first_drip_s?: number;
  };
};

/** Start, end, min, max and the time-weighted average of one firmware-analyzer stream. */
export type FirmwareStats = { start: number; end: number; min: number; max: number; avg: number };

/**
 * What the firmware's own shot analyzer shows (`domain/firmware_values.py`): the machine's
 * puck resistance `pr` (s·√bar/mL), liquid resistance `lr` (bar·s/mL), and water pumped.
 * No grade on any of it. The whole block is absent on a shot derived before it existed; each
 * stream is null when the shot has no valid reading, and the water fields unless it
 * recorded the pump's count (format v7).
 */
export type FirmwareValues = {
  pr: FirmwareStats | null;
  lr: FirmwareStats | null;
  phases: Array<{ phase_number: number; pr: FirmwareStats | null; lr: FirmwareStats | null }>;
  water_pumped_ml: number | null;
  water_minus_weight_g: number | null;
};

export type ShotDiagnosticsBlob = {
  /** The firmware analyzer's values; absent on a shot derived before they were kept. */
  firmware?: FirmwareValues;
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
      /** Where R came from; absent on a shot stored before the source was recorded. */
      source?: ResistanceSource;
      avg: number;
      slope: number;
    } | null;
    extraction?: {
      flow_avg_brew_ml_s: number;
    };
    weight?: {
      rate_avg_g_s: number | null;
      scale_connected: boolean;
    };
    /** Absent when the shot has no known profile to grade against. Each adherence is graded
        only over the phases that steer by it: "not_applicable" when the profile has none,
        "not_graded" when it has and the number could not be worked out. */
    profile_compliance?: {
      pressure_rmse_bar: number | null;
      flow_rmse_ml_s: number | null;
      max_pressure_overshoot_bar: number | null;
      max_pressure_undershoot_bar: number | null;
      max_flow_overshoot_ml_s: number | null;
      max_flow_undershoot_ml_s: number | null;
      /** Absent on a block stored before the grading was recorded. */
      pressure_grading?: "graded" | "not_applicable" | "not_graded";
      flow_grading?: "graded" | "not_applicable" | "not_graded";
    } | null;
  } | null;
  detail_level?: string;
  has_pressure?: boolean;
  /** The shot's own facts (`domain/phase_metrics.py`): whether the log has a phase table and
      why each phase ended, the profile's phases it never began, the first fast-flow window. */
  metrics?: {
    per_phase: boolean;
    exit_reasons: boolean;
    profile_phases: string[] | null;
    phases_not_reached: Array<{ phase_number: number; name: string }>;
    fast_flow: {
      phase_number: number | null;
      start_s: number;
      end_s: number;
      mean_g_s: number;
      pressure_min_bar: number;
      peak_pressure_bar: number;
    } | null;
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
export type PolicyChange = components["schemas"]["PolicyChange"];
export type StopConditionChange = components["schemas"]["StopConditionChange"];
export type PolicyViolation = components["schemas"]["Violation"];
export type DeviceWrite = components["schemas"]["DeviceWriteRow"];
export type DeviceWritesData = components["schemas"]["DeviceWritesData"];

// The profile board: what the app means the machine to hold. The rows and the plan are
// pydantic models; a run's `summary` is decoded JSON, so its shape is `BoardRunSummary` in
// `lib/board.ts`.
export type BoardView = components["schemas"]["BoardView"];
export type BoardRow = components["schemas"]["BoardRow"];
export type BoardRowView = components["schemas"]["BoardRowView"];
export type DraftLanding = components["schemas"]["DraftLanding"];
export type BoardAction = components["schemas"]["BoardAction"];
export type BoardProposal = components["schemas"]["BoardProposal"];
export type ResumePreview = components["schemas"]["ResumePreview"];
export type ActiveVersion = components["schemas"]["ActiveVersion"];
export type SetBrewing = components["schemas"]["SetBrewing"];
export type ConflictSummary = components["schemas"]["ConflictSummary"];
export type ConflictView = components["schemas"]["ConflictView"];
export type ListedVersion = components["schemas"]["ListedVersion"];
export type ProposedVersion = components["schemas"]["ProposedVersion"];
export type ProfileVersionsView = components["schemas"]["ProfileVersionsView"];

/** Every state a draft can be in, as the `status` CHECK spells them. */
export type DraftStatus = "draft" | "approved" | "pushed" | "failed" | "discarded" | "superseded";

/** `POST /api/profile-drafts`, in the two shapes the route accepts. */
export type DraftCreateBody = {
  base_version_id: number;
  /** A complete profile document: the manual editor's path, no model involved. */
  profile?: Record<string, unknown>;
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
