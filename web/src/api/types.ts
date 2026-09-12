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

/** A PATCH body: registry key -> value, with null meaning "drop the override". */
export type SettingsPatch = Record<string, SettingValue>;
