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
export type ShotSamplesData = components["schemas"]["ShotSamplesData"];
export type ProfileListData = components["schemas"]["ProfileListData"];
export type DeviceProfileSummary = components["schemas"]["DeviceProfileSummary"];
export type SyncStatusData = components["schemas"]["SyncStatusData"];
export type ApiErrorBody = components["schemas"]["ApiError"];
export type SettingValue = components["schemas"]["SettingValue"];

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

/** The `device.connection` event on `/api/device/live`. */
export type DeviceConnectionEvent = {
  connected: boolean;
  configured: boolean;
  host?: string;
  reason?: string;
};
