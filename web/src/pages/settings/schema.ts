import { z } from "zod";
import type { ResolvedSetting, SettingsMap, SettingsPatch, SettingValue } from "@/api/types";

/**
 * The settings form is generated from the registry the backend returns, so the
 * validation has to be generated too — there is no static list of keys on this
 * side, and hand-writing one would mean a new setting in
 * `gaggiclanker/settings.py` needs a matching edit here to be editable.
 *
 * Every field is a string except booleans: an `<input>` yields strings, and
 * coercing in the schema rather than in the submit handler is what lets the
 * error appear under the field that caused it.
 */

const INTEGER = /^-?\d+$/;
const NUMBER = /^-?(?:\d+|\d*\.\d+)$/;

export type SettingsFormValues = Record<string, string | boolean>;

export function fieldSchema(setting: ResolvedSetting): z.ZodTypeAny {
  // A secret is write-only: the form never holds its value, and blank means
  // "leave whatever is stored alone".
  if (setting.secret) return z.string();
  switch (setting.type) {
    case "bool":
      return z.boolean();
    case "int":
      return z.string().regex(INTEGER, "expected an integer");
    case "float":
      return z.string().regex(NUMBER, "expected a number");
    default:
      return z.string();
  }
}

export function buildSettingsSchema(settings: SettingsMap): z.ZodType<SettingsFormValues> {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const setting of Object.values(settings)) {
    shape[setting.key] = fieldSchema(setting);
  }
  return z.object(shape) as unknown as z.ZodType<SettingsFormValues>;
}

/** The form's starting state: current values, and blanks for every secret. */
export function toFormValues(settings: SettingsMap): SettingsFormValues {
  const values: SettingsFormValues = {};
  for (const setting of Object.values(settings)) {
    if (setting.secret) {
      values[setting.key] = "";
    } else if (setting.type === "bool") {
      values[setting.key] = Boolean(setting.value);
    } else {
      values[setting.key] = setting.value === null ? "" : String(setting.value);
    }
  }
  return values;
}

function parseField(setting: ResolvedSetting, raw: string | boolean): SettingValue {
  if (setting.type === "bool") return Boolean(raw);
  if (setting.type === "int") return Number.parseInt(String(raw), 10);
  if (setting.type === "float") return Number.parseFloat(String(raw));
  return String(raw);
}

/**
 * Only what actually changed.
 *
 * Sending the whole form back would rewrite every key as a database override,
 * which is precisely what makes an environment variable stop working: after
 * one save the operator's compose file is shadowed by a row that happens to
 * hold the same value. See `settings_service.py` for the precedence this
 * protects.
 */
export function toPatch(settings: SettingsMap, values: SettingsFormValues): SettingsPatch {
  const patch: SettingsPatch = {};
  for (const setting of Object.values(settings)) {
    const raw = values[setting.key];
    if (raw === undefined) continue;
    if (setting.secret) {
      // Blank means "keep". There is no way to tell a blank from an unchanged
      // secret, which is the point: the value never reaches the browser.
      if (typeof raw === "string" && raw.length > 0) patch[setting.key] = raw;
      continue;
    }
    const next = parseField(setting, raw);
    if (next !== setting.value) patch[setting.key] = next;
  }
  return patch;
}

/** A human label for a registry key: `devicePollIntervalSeconds` -> `Device poll interval seconds`. */
export function humanizeKey(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Which section a key belongs to. Prefix-based, so a new registry entry lands
 * somewhere sensible without an edit here; anything unrecognised falls into
 * "General" rather than disappearing.
 */
export const SETTINGS_SECTIONS = [
  { id: "device", title: "Machine", description: "How gaggiclanker reaches the GaggiMate." },
  { id: "llm", title: "Analysis", description: "The provider behind per-shot LLM analysis." },
  { id: "general", title: "General", description: "Everything else in the registry." },
] as const;

export function sectionFor(key: string): string {
  if (key.startsWith("device") || key.startsWith("gaggimate")) return "device";
  if (key.startsWith("llm")) return "llm";
  return "general";
}
