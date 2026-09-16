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

/**
 * The firmware's own hard limits, mirrored from `gaggiclanker/settings.py`.
 *
 * A deliberate second copy, and the only one in this file: the server refuses
 * these too, and it is the refusal that matters. What this buys is the error
 * appearing under the field as somebody types rather than after a round trip —
 * "the safety policy is narrower than the firmware, not wider" is a sentence
 * worth reading *before* pressing save.
 *
 * When the Python changes, change this. The pair rules are deliberately NOT
 * mirrored: comparing two fields needs the form's whole state, and a check that
 * only half works would be worse than sending it to the server.
 */
const POLICY_LIMITS: Record<string, { min: number; max: number; described: string }> = {
  profilePolicyTemperatureMinC: { min: 0, max: 150, described: "0-150 °C" },
  profilePolicyTemperatureMaxC: { min: 0, max: 150, described: "0-150 °C" },
  profilePolicyPressureMaxBar: { min: 0, max: 12, described: "0-12 bar" },
  profilePolicyFlowMaxMlS: { min: 0, max: 15, described: "0-15 ml/s" },
  profilePolicyPhaseDurationMinS: { min: 0.5, max: 300, described: "0.5-300 s" },
  profilePolicyPhaseDurationMaxS: { min: 0.5, max: 300, described: "0.5-300 s" },
};

export function fieldSchema(setting: ResolvedSetting): z.ZodTypeAny {
  const limits = POLICY_LIMITS[setting.key];
  if (limits) {
    return z
      .string()
      .regex(NUMBER, "expected a number")
      .refine((raw) => {
        const value = Number.parseFloat(raw);
        return value >= limits.min && value <= limits.max;
      }, `the safety policy is narrower than the firmware, not wider: ${limits.described}`);
  }
  if (setting.key === "profilePolicyMaxPhases") {
    return z
      .string()
      .regex(INTEGER, "expected an integer")
      .refine(
        (raw) => Number.parseInt(raw, 10) >= 1,
        "a profile has at least one phase, so this must be 1 or more",
      );
  }
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

/**
 * Keys the generated form leaves alone.
 *
 * `PATCH /api/settings` refuses them, because a dedicated endpoint owns the
 * write. `authPasswordHash` is the one: rendered as an ordinary masked box it
 * read "Auth password hash", which invited typing the *password* into it — and
 * a stored value argon2 cannot verify is a credential that authenticates
 * nobody. `AuthSection` shows its state and offers "Set password" instead.
 */
export function isEditable(setting: ResolvedSetting): boolean {
  return !setting.readonly;
}

export function buildSettingsSchema(settings: SettingsMap): z.ZodType<SettingsFormValues> {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const setting of Object.values(settings)) {
    if (!isEditable(setting)) continue;
    shape[setting.key] = fieldSchema(setting);
  }
  return z.object(shape) as unknown as z.ZodType<SettingsFormValues>;
}

/** The form's starting state: current values, and blanks for every secret. */
export function toFormValues(settings: SettingsMap): SettingsFormValues {
  const values: SettingsFormValues = {};
  for (const setting of Object.values(settings)) {
    if (!isEditable(setting)) continue;
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
 * Sending the whole form back would rewrite every key as a stored override,
 * so a default the maintainer never touched would be frozen at whatever it was
 * the day somebody pressed Save — and a later release changing that default
 * would silently not reach this box. A key nobody edited keeps no row.
 */
export function toPatch(settings: SettingsMap, values: SettingsFormValues): SettingsPatch {
  const patch: SettingsPatch = {};
  for (const setting of Object.values(settings)) {
    if (!isEditable(setting)) continue;
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

/** A human label for a registry key: `deviceCleanupKeepNewest` -> `Device cleanup keep newest`. */
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
  {
    id: "device",
    title: "Machine",
    description:
      "How gaggiclanker reaches the GaggiMate, and what it is allowed to change on it. Writes are off by default. Profiles are pushed from the Profiles page; sending notes and cleaning up storage happen only on the Sync page, when you confirm them. The cleanup policy here shapes what that page proposes, and the notes fields are what a send writes.",
  },
  {
    id: "llm",
    title: "LLM",
    description: "Which provider answers a call, what it costs, and which model does what.",
  },
  {
    id: "auth",
    title: "Authentication",
    description:
      "Off unless a username and a password are both set. Turn it on if anything you do not trust can reach this box.",
  },
  {
    id: "safety",
    title: "Profile safety policy",
    description:
      "Bounds narrower than the firmware's own parser. A profile drafted for the machine is clamped to these and then re-validated; anything a clamp cannot fix is refused rather than quietly rewritten. The firmware itself accepts 150 °C and 300 s phases.",
  },
  { id: "general", title: "General", description: "Everything else in the registry." },
] as const;

/**
 * Which key belongs to which registry key. Prefix-based, so a new setting
 * lands somewhere sensible without an edit here; anything unrecognised falls
 * into "General" rather than disappearing.
 *
 * The LLM prefixes are three rather than one because the keys are named after
 * the things they configure - `anthropicApiKey`, `claudeCodeBin`,
 * `modelAnalysis` - which reads better in the API than an `llm` prefix glued
 * onto everything would.
 */
const LLM_PREFIXES = ["llm", "anthropic", "claudeCode", "model"];

export function sectionFor(key: string): string {
  // Before the `device` prefix check: `profilePolicy*` is about what may be
  // written, not about how the machine is reached, and burying seven bounds in
  // the connection section would hide them.
  if (key.startsWith("profilePolicy")) return "safety";
  // `notesWriteback*` is named after what it writes rather than after the
  // machine, but it is a device write behind the same master switch — so it
  // belongs beside `deviceWritesEnabled` rather than in "General", where a
  // person turning writes on would never find it.
  if (key.startsWith("device") || key.startsWith("gaggimate") || key.startsWith("notesWriteback"))
    return "device";
  if (LLM_PREFIXES.some((prefix) => key.startsWith(prefix))) return "llm";
  if (key.startsWith("auth")) return "auth";
  return "general";
}
