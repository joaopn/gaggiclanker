import { z } from "zod";
import type { ResolvedSetting, SettingsMap, SettingsPatch, SettingValue } from "@/api/types";
import type { SettingsPageId } from "@/lib/settingsPages";

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

/** The settings pages whose fields come from the registry. */
export type RegistryPageId = Extract<SettingsPageId, "machine" | "safety" | "llm" | "auth">;

/**
 * Which key belongs to which registry page. Prefix-based, so a new registry
 * entry lands somewhere sensible without an edit here.
 *
 * Three pages are recognised by prefix, and everything else is the LLM page's.
 * That is not a catch-all by accident: the LLM keys are named after the things
 * they configure - `anthropicApiKey`, `claudeCodeBin`, `modelAnalysis`,
 * `chatMaxToolRounds`, `analysisChunkTokenBudget` - rather than sharing one
 * prefix, and they are most of the registry. A key that is not an LLM setting
 * and has no prefix here still shows up, in the LLM page's trailing "Other"
 * card, plainly unsorted until somebody gives it a prefix.
 */
export function sectionFor(key: string): RegistryPageId {
  // Before the `device` prefix check: `profilePolicy*` is about what may be
  // written, not about how the machine is reached, and burying seven bounds in
  // the connection settings would hide them.
  if (key.startsWith("profilePolicy")) return "safety";
  // `notesWriteback*` is named after what it writes rather than after the
  // machine, but it is a device write behind the same master switch — so it
  // belongs beside `deviceWritesEnabled`, where a person turning writes on
  // will find it.
  if (key.startsWith("device") || key.startsWith("gaggimate") || key.startsWith("notesWriteback"))
    return "machine";
  if (key.startsWith("auth")) return "auth";
  return "llm";
}

export type SettingsGroup = {
  id: string;
  title: string;
  description: string;
  /** Membership, and the order the fields render in. */
  keys: readonly string[];
};

/**
 * The collapsible cards inside each registry page.
 *
 * Unlike the pages, these name their keys: a heading is a claim about what
 * belongs together, and a prefix cannot make it. A key a page receives that no
 * group names lands in a trailing "Other" card instead of vanishing, so a new
 * setting is editable the day it ships and gets a home when somebody decides
 * where it goes.
 */
export const SETTINGS_GROUPS: Record<RegistryPageId, readonly SettingsGroup[]> = {
  machine: [
    {
      id: "connection",
      title: "Connection",
      description: "Where the display board is, and whether gaggiclanker keeps a connection open.",
      keys: ["gaggimateHost", "gaggimateProtocol", "gaggimateTimeoutSeconds", "deviceSyncEnabled"],
    },
    {
      id: "writes",
      title: "Writes",
      description: "What this box may change on the machine, and what a notes send writes.",
      keys: ["deviceWritesEnabled", "notesWritebackFields"],
    },
    {
      id: "cleanup",
      title: "Storage cleanup",
      description:
        "What the Sync page proposes when the machine's storage runs short. Nothing is deleted until a person confirms it there.",
      keys: ["deviceCleanupMode", "deviceCleanupKeepNewest", "deviceCleanupMinFreeKb"],
    },
  ],
  safety: [
    {
      id: "temperature",
      title: "Temperature",
      description: "The coldest and hottest a profile or a phase override may ask for.",
      keys: ["profilePolicyTemperatureMinC", "profilePolicyTemperatureMaxC"],
    },
    {
      id: "pump",
      title: "Pressure and flow",
      description: "The most the pump may be asked for, as a target or a stop condition.",
      keys: ["profilePolicyPressureMaxBar", "profilePolicyFlowMaxMlS"],
    },
    {
      id: "phases",
      title: "Phases",
      description: "How long a phase may run, and how many a profile may have.",
      keys: [
        "profilePolicyPhaseDurationMinS",
        "profilePolicyPhaseDurationMaxS",
        "profilePolicyMaxPhases",
      ],
    },
  ],
  llm: [
    {
      id: "provider",
      title: "Provider",
      description: "Who answers a call, and the credential it needs.",
      keys: [
        "llmProvider",
        "llmBaseUrl",
        "llmApiKey",
        "anthropicApiKey",
        "claudeCodeOauthToken",
        "claudeCodeBin",
        "claudeCodeEffort",
      ],
    },
    {
      id: "models",
      title: "Models",
      description:
        "Each purpose falls back to the default, and the default falls back to whatever the provider picks.",
      keys: ["modelDefault", "modelAnalysis", "modelDraft", "modelChat", "modelStartingPoint"],
    },
    {
      id: "limits",
      title: "Limits and the call ledger",
      description: "How long a call may take, when throttling stops everything, and what is kept.",
      keys: ["llmTimeoutSeconds", "llmRateLimitRetries", "llmStoreCallText"],
    },
    {
      id: "analysis",
      title: "Analysis",
      description: "How much of the knowledge base one analysis may read.",
      keys: ["analysisChunkTokenBudget"],
    },
    {
      id: "chat",
      title: "Chat",
      description: "How far one chat answer may go, and how much history it carries.",
      keys: ["chatMaxToolRounds", "chatMaxToolCalls", "chatHistoryTokenBudget"],
    },
  ],
  auth: [
    {
      id: "sign-in",
      title: "Sign-in",
      description: "The username and the password. Authentication is on once both are set.",
      keys: ["authUser", "authPasswordHash"],
    },
    {
      id: "sessions",
      title: "Sessions",
      description: "How long a signed-in browser stays signed in.",
      keys: ["authTokenTtlSeconds"],
    },
  ],
};

export const OTHER_GROUP_ID = "other";

/** The group a key renders in on its page: a named one, or "Other". */
export function groupFor(key: string): string {
  const group = SETTINGS_GROUPS[sectionFor(key)].find((candidate) => candidate.keys.includes(key));
  return group?.id ?? OTHER_GROUP_ID;
}

/**
 * A page's settings sorted into its groups, in the groups' own order. Groups
 * with nothing in them are left out; keys no group names go last, under
 * "Other".
 */
export function groupEntries(
  page: RegistryPageId,
  entries: readonly ResolvedSetting[],
): { group: SettingsGroup; entries: ResolvedSetting[] }[] {
  const byKey = new Map(entries.map((entry) => [entry.key, entry]));
  const named = new Set<string>();
  const result: { group: SettingsGroup; entries: ResolvedSetting[] }[] = [];
  for (const group of SETTINGS_GROUPS[page]) {
    const members = group.keys
      .map((key) => byKey.get(key))
      .filter((entry): entry is ResolvedSetting => entry !== undefined);
    for (const key of group.keys) named.add(key);
    if (members.length > 0) result.push({ group, entries: members });
  }
  const rest = entries.filter((entry) => !named.has(entry.key));
  if (rest.length > 0) {
    result.push({
      group: {
        id: OTHER_GROUP_ID,
        title: "Other",
        description: "Settings no heading claims yet.",
        keys: rest.map((entry) => entry.key),
      },
      entries: rest,
    });
  }
  return result;
}
