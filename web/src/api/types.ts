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
export type DeviceProfileSummary = components["schemas"]["DeviceProfileSummary"];
export type SyncStatusData = components["schemas"]["SyncStatusData"];
export type ImportSummary = components["schemas"]["ImportSummary"];
export type ImportResult = components["schemas"]["ImportResult"];
export type ApiErrorBody = components["schemas"]["ApiError"];
export type MachineListData = components["schemas"]["MachineListData"];
export type MachineRow = components["schemas"]["MachineRow"];
export type MachinePatch = components["schemas"]["MachinePatch"];

// Sets, beans, grinders and the judgement. Every one of these is a real
// pydantic model on the server, so none of them is retyped here.
export type Vocabulary = components["schemas"]["Vocabulary"];
export type TasteGroup = components["schemas"]["TasteGroup"];
export type TasteTag = components["schemas"]["TasteTag"];
export type VocabTerm = components["schemas"]["Term"];
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
export type SetVersionPatch = components["schemas"]["SetVersionPatch"];
export type SetVersionWrite = components["schemas"]["SetVersionWrite"];
export type FieldChange = components["schemas"]["FieldChange"];
export type SetTrends = components["schemas"]["SetTrends"];
export type SetTrendPoint = components["schemas"]["SetTrendPoint"];
export type SetTrendVersion = components["schemas"]["SetTrendVersion"];
export type ShotJudgement = components["schemas"]["ShotJudgementRow"];
export type JudgementWrite = components["schemas"]["JudgementWrite"];
export type ShotSetBadge = components["schemas"]["ShotSetBadge"];
export type SettingValue = components["schemas"]["SettingValue"];

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
export type SettingSource = "database" | "environment" | "default";

/** A non-secret setting: value, default and override are all disclosed. */
export type PlainSetting = {
  key: string;
  type: SettingType;
  secret: false;
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
  machine_id?: number;
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
  machineId?: number;
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

/**
 * The merged `evt:status` the device stream carries, as the firmware spells it.
 * Declared server-side as
 * a plain object — it is open on purpose, so a firmware that adds a key does
 * not take the live connection down — which is why the keys we read are named
 * here rather than generated.
 */
export type LiveProcess = {
  /** 1 while a shot is running. The live view's whole trigger. */
  a?: number | null;
  s?: string | null;
  /** The phase label, `l` on the wire. */
  l?: string | null;
  /** Elapsed milliseconds. */
  e?: number | null;
  u?: number | null;
  /** What the phase target counts: "volumetric" (grams) or "time" (ms). */
  tt?: string | null;
  /** The phase target, in the unit `tt` names. */
  pt?: number | null;
  /** Progress towards `pt`, same unit. */
  pp?: number | null;
};

export type LiveWarning = { k?: string | null; l?: number | null; a?: boolean | null };

export type LiveStatus = {
  process?: LiveProcess | null;
  /** Current and target boiler temperature. */
  ct?: number | null;
  tt?: number | null;
  /** Pressure at the pump, and the profile's target. Zero on Standard boards. */
  pr?: number | null;
  pt?: number | null;
  /** Flow: modelled pump flow, target flow, puck flow. */
  fl?: number | null;
  tf?: number | null;
  pf?: number | null;
  /** Current weight from the BLE scale, and puck resistance. */
  cw?: number | null;
  pkr?: number | null;
  /** Mode: 0 standby, 1 brew, 2 steam, 3 water, 4 grind. */
  m?: number | null;
  /** Selected profile label and id. */
  p?: string | null;
  puid?: string | null;
  /** Capabilities: pressure sensor (Pro boards only), pump dimming. */
  cp?: boolean | null;
  cd?: boolean | null;
  warn?: LiveWarning[] | null;
  sys?: { s?: string | null; m?: string | null; c?: number | null } | null;
  /** Scale connected, and its battery percentage. */
  bc?: boolean | null;
  sbat?: number | null;
};

/** The `device.connection` event on `/api/device/live`. */
export type DeviceConnectionEvent = {
  connected: boolean;
  configured: boolean;
  host?: string;
  reason?: string;
};
