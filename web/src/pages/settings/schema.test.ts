import { describe, expect, it } from "vitest";
import type { PlainSetting, SettingsMap } from "@/api/types";
import {
  buildSettingsSchema,
  fieldSchema,
  groupEntries,
  groupFor,
  humanizeKey,
  SETTINGS_GROUPS,
  sectionFor,
  toFormValues,
  toPatch,
} from "@/pages/settings/schema";

/** One non-secret registry entry, for the per-key validators below. */
function plain(overrides: Partial<PlainSetting> & { key: string }): PlainSetting {
  return {
    type: "float",
    secret: false,
    readonly: false,
    value: 0,
    default: 0,
    override: null,
    source: "default",
    description: "",
    ...overrides,
  } as PlainSetting;
}

const settings: SettingsMap = {
  host: {
    key: "host",
    type: "string",
    secret: false,
    readonly: false,
    value: "10.0.0.5",
    default: "",
    override: "10.0.0.5",
    source: "database",
    description: "",
  },
  enabled: {
    key: "enabled",
    type: "bool",
    secret: false,
    readonly: false,
    value: true,
    default: true,
    override: null,
    source: "default",
    description: "",
  },
  interval: {
    key: "interval",
    type: "int",
    secret: false,
    readonly: false,
    value: 60,
    default: 60,
    override: null,
    source: "default",
    description: "",
  },
  apiKey: {
    key: "apiKey",
    type: "string",
    secret: true,
    readonly: false,
    configured: true,
    hint: "sk-p",
    source: "database",
    description: "",
  },
};

describe("toFormValues", () => {
  it("stringifies non-booleans and blanks every secret", () => {
    expect(toFormValues(settings)).toEqual({
      host: "10.0.0.5",
      enabled: true,
      interval: "60",
      apiKey: "",
    });
  });
});

describe("toPatch", () => {
  it("is empty when nothing changed", () => {
    expect(toPatch(settings, toFormValues(settings))).toEqual({});
  });

  it("sends only the changed keys, typed as the registry declares them", () => {
    const values = { ...toFormValues(settings), interval: "30", enabled: false };
    expect(toPatch(settings, values)).toEqual({ interval: 30, enabled: false });
  });

  it("treats a blank secret as 'keep' and a filled one as a write", () => {
    expect(toPatch(settings, { ...toFormValues(settings), apiKey: "" })).toEqual({});
    expect(toPatch(settings, { ...toFormValues(settings), apiKey: "sk-new" })).toEqual({
      apiKey: "sk-new",
    });
  });
});

describe("buildSettingsSchema", () => {
  const schema = buildSettingsSchema(settings);

  it("accepts the current values", () => {
    expect(schema.safeParse(toFormValues(settings)).success).toBe(true);
  });

  it("rejects a non-integer in an int field", () => {
    const result = schema.safeParse({ ...toFormValues(settings), interval: "1.5" });
    expect(result.success).toBe(false);
  });

  it("rejects a string where a bool is declared", () => {
    const result = schema.safeParse({ ...toFormValues(settings), enabled: "yes" });
    expect(result.success).toBe(false);
  });
});

describe("labels and sections", () => {
  it("turns a camelCase registry key into a sentence", () => {
    expect(humanizeKey("deviceCleanupKeepNewest")).toBe("Device cleanup keep newest");
    expect(humanizeKey("gaggimateHost")).toBe("Gaggimate host");
  });

  it("routes a key to a page by prefix, defaulting to the LLM page", () => {
    expect(sectionFor("gaggimateHost")).toBe("machine");
    expect(sectionFor("deviceSyncEnabled")).toBe("machine");
    // What a pull does with a shot as it lands is decided beside the pull.
    expect(sectionFor("shotsProfileAutomatch")).toBe("machine");
    expect(sectionFor("llmProvider")).toBe("llm");
    // The LLM keys are named after what they configure rather than sharing one
    // prefix, so the section covers three of them.
    expect(sectionFor("anthropicApiKey")).toBe("llm");
    expect(sectionFor("claudeCodeBin")).toBe("llm");
    expect(sectionFor("modelAnalysis")).toBe("llm");
    // The writes switch is about the machine; the seven bounds are about what
    // may be written, which is a different question and its own section.
    expect(sectionFor("deviceWritesEnabled")).toBe("machine");
    expect(sectionFor("profilePolicyTemperatureMaxC")).toBe("safety");
    expect(sectionFor("profilePolicyMaxPhases")).toBe("safety");
    // The analysis and chat budgets bound what a call may consume.
    expect(sectionFor("analysisChunkTokenBudget")).toBe("llm");
    expect(sectionFor("chatMaxToolRounds")).toBe("llm");
    // No catch-all page: an unrecognised key is visible under LLM's "Other".
    expect(sectionFor("somethingNew")).toBe("llm");
    expect(groupFor("somethingNew")).toBe("other");
  });

  it("names every grouped key on the page that key's prefix sends it to", () => {
    // A key listed under a heading on the wrong page would be claimed by a
    // group that never receives it, and would silently fall into "Other".
    for (const [page, groups] of Object.entries(SETTINGS_GROUPS)) {
      for (const group of groups) {
        for (const key of group.keys) expect(sectionFor(key), key).toBe(page);
      }
    }
  });

  it("sorts a page's keys into its groups, in the groups' order, with the rest under Other", () => {
    expect(groupFor("gaggimateHost")).toBe("connection");
    expect(groupFor("notesWritebackFields")).toBe("writes");
    expect(groupFor("modelStartingPoint")).toBe("models");
    expect(groupFor("deviceSomethingNew")).toBe("other");

    const entries = [
      "deviceCleanupMode",
      "deviceSomethingNew",
      "gaggimateHost",
      "deviceWritesEnabled",
    ].map((key) => plain({ key, type: "string", value: "" }));
    const grouped = groupEntries("machine", entries);
    expect(grouped.map(({ group }) => group.id)).toEqual([
      "connection",
      "writes",
      "cleanup",
      "other",
    ]);
    expect(grouped.at(-1)?.entries.map((entry) => entry.key)).toEqual(["deviceSomethingNew"]);
    // An empty group is left out rather than drawn as a card with nothing in it.
    expect(groupEntries("safety", [])).toEqual([]);
  });

  it("refuses a policy bound wider than the firmware's own limit", () => {
    // The whole point of layer 2 is that it is narrower. A bound a text box can
    // widen to the firmware's own limit is a layer that does nothing — and the
    // server refuses these too; this is only so the message lands before save.
    const cases: Array<[string, string]> = [
      ["profilePolicyTemperatureMaxC", "200"],
      ["profilePolicyTemperatureMinC", "-10"],
      ["profilePolicyPhaseDurationMaxS", "900"],
      ["profilePolicyPhaseDurationMinS", "0"],
      ["profilePolicyPressureMaxBar", "50"],
      ["profilePolicyFlowMaxMlS", "40"],
    ];
    for (const [key, value] of cases) {
      const schema = fieldSchema(plain({ key, type: "float", value: 1 }));
      expect(schema.safeParse(value).success, key).toBe(false);
    }
  });

  it("accepts a policy bound inside the firmware's limits", () => {
    const schema = fieldSchema(
      plain({ key: "profilePolicyTemperatureMaxC", type: "float", value: 100 }),
    );
    expect(schema.safeParse("96").success).toBe(true);
  });

  it("refuses a profile with no phases at all", () => {
    const schema = fieldSchema(plain({ key: "profilePolicyMaxPhases", type: "int", value: 10 }));
    expect(schema.safeParse("0").success).toBe(false);
    expect(schema.safeParse("-3").success).toBe(false);
    expect(schema.safeParse("4").success).toBe(true);
  });
});
